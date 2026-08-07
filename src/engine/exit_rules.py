"""Turns an open position + today's prices into ONE clear instruction, using
the exit rules from your own SOP (config/strategies.yaml):

  🛑 stop      - loss reached your stop (2x the credit): close immediately
  ⏰ time      - 21 days to expiration: decide today - close, or roll for a credit
  ✅ profit    - you kept your profit target (50% of the credit): take the win
  ⚠️ watch     - price is near/past a short strike, or delta crossed the red flag
  ➕ uncovered - a PMCC / covered call with no call written against it
  ✋ hold      - nothing triggered: let time decay keep working

Pure math, no network, fully unit-tested. Priority when several trigger:
stop > time > profit > watch > hold (safety first). "uncovered" short-circuits
the lot: with no short call sold there is simply nothing for the rules to
measure.
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


def _strike_notes(position: Position, underlying_price: float,
                  accepts_assignment: bool = False) -> list[str]:
    """Warnings when price is close to - or past - an option you sold.

    accepts_assignment flips the tone on the put side. On a Wheel, price
    reaching your short put IS the plan - it hands you the shares you wanted -
    so calling it "trouble" and telling her to roll would be advising against
    the strategy she chose.
    """
    notes = []
    for leg in position.legs:
        if leg.action != Action.SELL or leg.strike <= 0:
            continue
        k = leg.strike
        if leg.option_type == OptionType.PUT:
            if underlying_price <= k:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is BELOW your short put strike "
                    f"({k:g}). Assignment is likely - that is the plan on a Wheel. Be "
                    "ready to take the 100 shares, write down your cost basis (strike "
                    "minus the premium you kept), and start selling covered calls at "
                    "or above it."
                    if accepts_assignment else
                    f"Price ({underlying_price:,.0f}) is BELOW your short put strike "
                    f"({k:g}). The trade is in trouble - your SOP says roll down and "
                    "out for a credit, or close.")
            elif (underlying_price - k) / k <= STRIKE_PROXIMITY:
                notes.append(
                    f"Price ({underlying_price:,.0f}) is within 1.5% of your short put "
                    f"strike ({k:g}). Assignment is getting close - fine on a Wheel, "
                    "just make sure the cash is still set aside."
                    if accepts_assignment else
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


def _long_premium_signal(position: Position, exit_cfg: dict[str, Any],
                         current_value: Optional[float],
                         underlying_price: Optional[float],
                         dte_left: Optional[int],
                         today: Optional[date]) -> ExitSignal:
    """The exit reading for a bought call, where the arithmetic runs backwards.

    On every other strategy she collects a credit and profits by buying it back
    for less, so "cost to close" falling is good news. Here she PAID, and
    profits by selling for more - the same number rising is the good news. Wire
    this into the credit path and a winning trade reads as a loser.

    Priority: a fast gain is taken (it is the one this strategy is most likely
    to give back), then the profit target, then the stop, then the theta clock.
    """
    paid = abs(position.open_cash)
    notes: list[str] = []

    if dte_left is not None and dte_left <= int(exit_cfg.get("roll_forward_dte", 180)):
        notes.append(
            f"Only {dte_left} days left. Under six months a long call starts losing "
            "its time value quickly, and that loss accelerates all the way down. "
            "Roll it further out or close it - do not ride it into expiration.")

    if current_value is None or paid <= 0:
        return ExitSignal(
            action="hold", tone="neutral",
            headline="Holding - no live price",
            reason=(f"You paid ${paid:,.0f} for this call. Today's value could not be "
                    "fetched, so there is no profit or loss to judge yet. The rules "
                    "still stand: take 10-20% if it comes inside a week, or 20-40% "
                    "inside four weeks."),
            notes=notes)

    pl = current_value - paid
    pct = pl / paid * 100
    held = position.days_held(today)
    money = (f"It is worth about ${current_value:,.0f} against the ${paid:,.0f} you "
             f"paid, so you are {'up' if pl >= 0 else 'down'} ${abs(pl):,.0f} "
             f"({pct:+.0f}%).")

    fast_pct = float(exit_cfg.get("fast_profit_pct", 10))
    fast_days = int(exit_cfg.get("fast_profit_days", 7))
    quick_pct = float(exit_cfg.get("quick_profit_pct", 20))
    quick_days = int(exit_cfg.get("quick_profit_days", 28))

    # Both windows say "take it". The lesson behind them is that a LEAPS up big
    # and fast is pressed against the upper Bollinger band, and that reverts:
    # the cautionary trade was +30% in two weeks, held for more, and spent the
    # next four months at -50% before scraping out at +20%.
    if held is not None and held <= fast_days and pct >= fast_pct:
        return ExitSignal(
            action="close", tone="green",
            headline=f"Take it - up {pct:.0f}% in {held} days",
            reason=(f"{money} Your SOP closes a bought call that makes {fast_pct:g}% or "
                    f"more inside {fast_days} days. A jump this fast usually means the "
                    "stock is stretched against the top of its range, and it tends to "
                    "come back. Take it and wait for the next pullback."),
            notes=notes)

    if held is not None and held <= quick_days and pct >= quick_pct:
        return ExitSignal(
            action="close", tone="green",
            headline=f"Take it - up {pct:.0f}% in {held} days",
            reason=(f"{money} Your SOP closes a bought call that makes {quick_pct:g}% or "
                    f"more inside {quick_days} days. Holding out for more is exactly how "
                    "a winner turns into months of waiting to get back to even."),
            notes=notes)

    # No stop, deliberately. Size and time are the risk control, so a loss here
    # is a hold - but say plainly how bad it is rather than a bare "hold".
    if pct <= -25:
        return ExitSignal(
            action="watch", tone="amber",
            headline=f"Down {abs(pct):.0f}% - holding by design",
            reason=(f"{money} This strategy has no stop, on purpose: you bought a year "
                    "or more of time so a pullback can recover, and cutting here locks "
                    "in the loss right before the bounce you paid for. That only works "
                    "if the position was small enough to sit through - which is what the "
                    "10% cap is for. If the reason you bought it has actually broken "
                    "(bad earnings, bad guidance), that is a different decision."),
            notes=notes)

    return ExitSignal(
        action="hold", tone="neutral",
        headline=f"Hold - {pct:+.0f}%",
        reason=(f"{money} Not at a take-it level ({fast_pct:g}% inside {fast_days} days, "
                f"or {quick_pct:g}% inside {quick_days}), so there is nothing to do."),
        notes=notes)


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

    # ---- 0a. Bought premium: every rule below is written around a CREDIT she
    # collected and buys back cheaper. This one is the mirror image - she paid,
    # and she wants to sell it for more - so it gets its own reading entirely.
    if position.is_long_premium:
        # Sign flip, deliberately in one place. `current_cost` is what it costs
        # to CLOSE, and closing a position you only ever bought PAYS you - so
        # the chain math returns it negative. Negating turns it back into what
        # the call is worth today. Get this backwards and a call that doubled
        # reports as a total loss.
        value = None if current_cost is None else -current_cost
        return _long_premium_signal(position, exit_cfg, value,
                                    underlying_price, dte_left, today)

    # ---- 0. Nothing sold: none of the exit rules have anything to measure.
    if position.is_uncovered:
        return ExitSignal(
            action="uncovered", tone="amber",
            headline="No call sold - nothing is earning",
            reason=("You bought the short call back and have not written a new "
                    "one, so this position is not collecting premium right now "
                    "and the long leg is riding the stock in both directions. "
                    "Your 50% target and the 21-day clock only apply to a call "
                    "you have actually sold. Write the next one when you like "
                    "the level - your SOP's PMCC sells about 30 days out at "
                    "delta 0.30 - then record it here."),
            notes=[])

    pl = profit_pct = None
    if current_cost is not None and credit > 0:
        pl = credit - current_cost
        profit_pct = pl / credit * 100

    # ---- collect watch-level warnings first (they ride along on any signal)
    notes: list[str] = []
    accepts_assignment = bool(exit_cfg.get("accepts_assignment"))
    if underlying_price is not None and underlying_price > 0:
        notes.extend(_strike_notes(position, underlying_price, accepts_assignment))
    if short_delta is not None and short_delta >= DELTA_RED_FLAG:
        notes.append(
            f"The short leg's delta is now {short_delta:.2f} - past your ~0.30 red "
            "flag, so assignment is getting more likely. On a Wheel that is an "
            "outcome you accepted, not a problem to fix."
            if accepts_assignment else
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
