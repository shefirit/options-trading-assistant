"""Turns an open position + today's prices into ONE clear instruction, using
the exit rules from your own SOP (config/strategies.yaml):

  🛑 stop    - loss reached your stop (2x the credit): close immediately
  ⏰ time    - 21 days to expiration: decide today - close, or roll for a credit
  ✅ profit  - you kept your profit target (50% of the credit): take the win
  ⚠️ watch   - price is near/past a short strike, or delta crossed the red flag
  ✋ hold    - nothing triggered: let time decay keep working

Pure math, no network, fully unit-tested. Priority when several trigger:
stop > time > profit > watch > hold (safety first).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.engine.models import Action, OptionType
from src.engine.positions import Position

# SOP: "consider rolling when price comes within 1-1.5% of your short strike".
STRIKE_PROXIMITY = 0.015
# SOP: per-share short delta crossing ~0.30-0.40 means the trade has gone wrong.
DELTA_RED_FLAG = 0.30


class ExitSignal(BaseModel):
    action: str                     # stop | time | profit | watch | hold | unpriced
    headline: str                   # the short instruction shown big
    reason: str                     # one plain-English sentence of why
    tone: str                       # red | amber | green | neutral
    pl_dollars: Optional[float] = None      # profit (+) / loss (-) right now
    profit_pct: Optional[float] = None      # % of the credit kept so far
    notes: list[str] = Field(default_factory=list)  # extra warnings worth seeing


def _strike_notes(position: Position, underlying_price: float) -> list[str]:
    """Warnings when price is close to - or past - an option you sold."""
    notes = []
    for leg in position.legs:
        if leg.action != Action.SELL or leg.strike <= 0:
            continue
        k = leg.strike
        if leg.option_type == OptionType.PUT:
            if underlying_price <= k:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is BELOW your short put strike "
                    f"({k:g}). The trade is in trouble - your SOP says roll down and "
                    "out for a credit, or close.")
            elif (underlying_price - k) / k <= STRIKE_PROXIMITY:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is within 1.5% of your short put "
                    f"strike ({k:g}). Your SOP says consider rolling before it crosses.")
        else:
            if underlying_price >= k:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is ABOVE your short call strike "
                    f"({k:g}). The trade is in trouble - your SOP says roll up and "
                    "out for a credit, or close.")
            elif (k - underlying_price) / k <= STRIKE_PROXIMITY:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is within 1.5% of your short call "
                    f"strike ({k:g}). Your SOP says consider rolling before it crosses.")
    return notes


def evaluate(
    position: Position,
    exit_cfg: dict[str, Any],
    current_cost: Optional[float] = None,
    underlying_price: Optional[float] = None,
    short_delta: Optional[float] = None,
    today: Optional[date] = None,
) -> ExitSignal:
    """One instruction for one open position.

    current_cost     dollars it takes to close the whole position right now
                     (None when live pricing was unavailable)
    underlying_price today's price of the underlying (None if unavailable)
    short_delta      the live per-share delta of the short leg, if known
    """
    credit = position.credit
    dte_left = position.dte_left(today)

    pl = profit_pct = None
    if current_cost is not None and credit > 0:
        pl = credit - current_cost
        profit_pct = pl / credit * 100

    # ---- collect watch-level warnings first (they ride along on any signal)
    notes: list[str] = []
    if underlying_price is not None and underlying_price > 0:
        notes.extend(_strike_notes(position, underlying_price))
    if short_delta is not None and short_delta >= DELTA_RED_FLAG:
        notes.append(
            f"The short leg's delta is now {short_delta:.2f} - past your ~0.30 red "
            "flag. The odds have moved against this trade; consider rolling or closing.")

    # ---- 1. Stop loss - the one rule that protects your account.
    sl = exit_cfg.get("stop_loss_multiple")
    if sl and pl is not None and -pl >= float(sl) * credit - 1e-9:
        return ExitSignal(
            action="stop", tone="red",
            headline="Close now - stop loss hit",
            reason=(f"You collected ${credit:,.0f} and it now costs ${current_cost:,.0f} "
                    f"to close - a loss of ${-pl:,.0f}, which reached your stop of "
                    f"{float(sl):g}x the credit (${float(sl) * credit:,.0f}). Your SOP: "
                    "close immediately, no rolling at this point."),
            pl_dollars=pl, profit_pct=profit_pct, notes=notes)

    # ---- 2. Time exit - never drift into the fast-risk zone without deciding.
    te = exit_cfg.get("time_exit_dte")
    if te is not None and dte_left is not None and dte_left <= int(te):
        entered_inside = (position.dte_at_entry is not None
                          and position.dte_at_entry <= int(te))
        if not entered_inside:
            return ExitSignal(
                action="time", tone="red",
                headline=f"Decide today - {dte_left} days to expiration",
                reason=(f"Your SOP says never hold past {int(te)} days to expiration "
                        "without a decision: from here, price swings hit the position "
                        "much harder (gamma risk) and things go wrong fast. Close it - "
                        "or roll to a fresh ~45-day spread back at your delta target, "
                        "but ONLY if the roll fills for a net credit. If you cannot get "
                        "a credit, close instead of forcing it."),
                pl_dollars=pl, profit_pct=profit_pct, notes=notes)
        notes.insert(0, (
            f"Only {dte_left} days to expiration, and you entered inside the "
            f"{int(te)}-day window (allowed for cash-settled indexes). Manage this "
            "actively - check it every day and don't hold to the end."))

    # ---- 3. Profit target - take the win.
    pt = exit_cfg.get("profit_target_pct")
    if pt and profit_pct is not None and profit_pct >= float(pt) - 1e-9:
        return ExitSignal(
            action="profit", tone="green",
            headline="Take the win - profit target reached",
            reason=(f"You have kept {profit_pct:.0f}% of the ${credit:,.0f} credit "
                    f"(${pl:,.0f} profit). Your SOP says close at {float(pt):g}% - "
                    "don't wait for 100%. Lock it in and move on."),
            pl_dollars=pl, profit_pct=profit_pct, notes=notes)

    # ---- 4. Watch - nothing forces an exit, but something needs eyes on it.
    if notes:
        return ExitSignal(
            action="watch", tone="amber",
            headline="Watch closely - see why below",
            reason=notes[0],
            pl_dollars=pl, profit_pct=profit_pct, notes=notes[1:])

    # ---- 5. Hold (or unpriced, if we couldn't get live prices).
    if pl is None:
        return ExitSignal(
            action="unpriced", tone="neutral",
            headline="Could not price this right now",
            reason=("Live option prices were unavailable, so the profit/stop checks "
                    "could not run. The day-count and strike checks above still work. "
                    "Try again in a moment, or check the position in thinkorswim."),
            notes=notes)
    days = f" with {dte_left} days left" if dte_left is not None else ""
    return ExitSignal(
        action="hold", tone="neutral",
        headline="Hold - nothing triggered",
        reason=(f"You have kept {profit_pct:.0f}% of the credit so far{days}. "
                "No exit rule has triggered - let time decay keep working for you."),
        pl_dollars=pl, profit_pct=profit_pct, notes=notes)
