"""Copies her real trades into the TWA tracker spreadsheet.

The TWA tracker (a copy of the "TWA Discord Tracker" template) is a separate
Google Sheet from the app's own log. It tracks ONE STRIKE PER ROW, the way the
course does it: a sold put is a row, a covered call is a row, the shares are a
row. A roll stays on the same row - new strike and expiration, the roll's net
credit added to Premium $, and a line in Notes - which is how Ashley's
reference tab records it ("Rolled to 11/07, down to $135 from $140 for
$2.58cr").

So this is a view, not a log. Every time anything is logged on a real trade,
the app replays that trade from its event rows and sends the rows it should
have in the tracker. The Apps Script finds them by the "App ID" column and
writes only the cells she would type herself - never a formula cell, so the
template's P/L, days, return and breach columns keep working.

Her rulings, 2026-10-13:
  - real money only; practice trades never reach the tracker
  - only the types the tracker has (PUT, PCS, CC, STOCK, LEAP). Call credit
    spreads, iron condors and Covered Call Models 1-3 are left out, and so is
    a put spread that later grew a call side
  - backfill from October: a row closed before `start` is not sent
  - the app's own log keeps working exactly as before - this is a second copy
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from src.engine.config_loader import load_settings
from src.engine.models import Action, OptionType
from src.engine.positions import Position, parse_rows

# Strategies the tracker has a type for. Everything else is skipped, with the
# reason shown on the Settings sync panel.
INCLUDED = {
    "cash_secured_put", "wheel", "leaps_put",
    "put_credit_spread",
    "poor_mans_covered_call", "long_call_leaps",
    "weekly_covered_call",
}
_SINGLE_PUT = {"cash_secured_put", "wheel", "leaps_put"}
_LONG_CALL = {"poor_mans_covered_call", "long_call_leaps"}

# Between the lines of a row's Notes (Ashley's own tab uses " / " too). The
# script splits on it to tell which roll lines a cell already has.
NOTE_SEP = " / "

# Her backfill ruling: rows closed before this are not sent.
DEFAULT_START = date(2026, 10, 1)


def settings() -> dict[str, Any]:
    return ((load_settings().get("google_sheet") or {}).get("twa_tracker") or {})


def is_enabled() -> bool:
    return bool(settings().get("enabled"))


def start_date() -> date:
    raw = settings().get("start")
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_START


def google_symbol(underlying: str) -> str:
    """The Ticker cell. The tracker prices it with GOOGLEFINANCE, which picks
    the wrong security for a few symbols, so those carry their exchange
    (`google_symbols` in settings.yaml)."""
    sym = str(underlying or "").upper()
    overrides = {str(k).upper(): str(v) for k, v in
                 (settings().get("google_symbols") or {}).items()}
    return overrides.get(sym, sym)


@dataclass
class _Row:
    """One tracker row while the trade is being replayed."""

    kind: str                      # "put", "pcs", "cc", "stock", "leap"
    side: str                      # "Sell", "Buy" or "ASSIGNED"
    strategy: str                  # the tracker's Strategy dropdown value
    opened: Optional[date]
    expires: Optional[date]
    strike: float
    qty: int
    premium: float                 # per share
    long_strike: Optional[float] = None
    cc_collateral: Optional[float] = None
    collateral: Optional[float] = None
    note: str = ""
    closed: Optional[date] = None
    close: Optional[float] = None  # per share
    cells: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.closed is None

    @property
    def buys(self) -> bool:
        return self.side != "Sell"

    def add_note(self, line: str) -> None:
        """Notes are lines joined by NOTE_SEP. The script appends only the
        lines a cell does not already hold, so her own words stay put."""
        self.note = f"{self.note}{NOTE_SEP}{line}" if self.note else line

    def close_at(self, on: Optional[date], dollars: float) -> None:
        """Close with `dollars` of signed cash for the whole row (+ = paid her)."""
        self.closed = on
        per = dollars / (100 * self.qty) if self.qty else 0.0
        self.close = round(per if self.buys else -per, 4) or 0.0


def _per_share(dollars: float, qty: int) -> float:
    return round(dollars / (100 * qty), 4) if qty else 0.0


def _iso(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def _money(x: Optional[float]) -> Any:
    return "" if x is None else round(float(x), 2)


def _leg_expiry(position: Position, leg) -> Optional[date]:
    if getattr(leg, "expiration", None) is not None:
        return leg.expiration
    if leg.dte is not None and position.opened is not None:
        return position.opened + timedelta(days=int(leg.dte))
    return position.expiration


def _pcs_note(short: float, long: Optional[float]) -> str:
    return f"PCS ${short:g}/${long:g}" if long is not None else "PCS"


def _opening_rows(p: Position) -> tuple[list[_Row], str]:
    """Day one as tracker rows, or ([], why) when the tracker cannot show it."""
    legs = p.open_legs or p.legs
    c = max(int(p.contracts or 1), 1)
    sold = [l for l in legs if l.action == Action.SELL]
    bought = [l for l in legs if l.action == Action.BUY]
    key = p.strategy_key

    if key in _SINGLE_PUT:
        if len(legs) != 1 or not sold or sold[0].option_type != OptionType.PUT:
            return [], "not a single sold put"
        leg = sold[0]
        q = c * max(int(leg.quantity or 1), 1)
        return [_Row("put", "Sell", "PUT", p.opened, _leg_expiry(p, leg),
                     leg.strike, q, _per_share(p.open_premium, q),
                     collateral=leg.strike * 100 * q)], ""

    if key == "put_credit_spread":
        puts = [l for l in legs if l.option_type == OptionType.PUT]
        if len(legs) != 2 or len(puts) != 2 or len(sold) != 1:
            return [], "not a two-leg put spread"
        short, long_ = sold[0], bought[0]
        q = c * max(int(short.quantity or 1), 1)
        width = abs(short.strike - long_.strike)
        return [_Row("pcs", "Sell", "PCS", p.opened, _leg_expiry(p, short),
                     short.strike, q, _per_share(p.open_premium, q),
                     long_strike=long_.strike, collateral=width * 100 * q,
                     note=_pcs_note(short.strike, long_.strike))], ""

    if key == "weekly_covered_call" or key in _LONG_CALL:
        rows: list[_Row] = []
        if key == "weekly_covered_call":
            if bought or not p.shares_cost:
                return [], "no shares on the trade"
            cost = float(p.shares_cost)
            per = _per_share(cost, c)
            rows.append(_Row("stock", "Buy", "STOCK", p.opened, None, per, c,
                             per, collateral=cost,
                             note=f"Bought {c * 100} shares"))
        else:
            if len(bought) != 1 or bought[0].option_type != OptionType.CALL:
                return [], "not one long call"
        if key == "weekly_covered_call" and (
                len(sold) > 1 or any(l.option_type != OptionType.CALL for l in sold)):
            return [], "more than one call sold on day one"

        # Sold legs: one of them is exactly open_premium; several are priced
        # off their own legs.
        sold_rows = []
        for leg in sold:
            q = c * max(int(leg.quantity or 1), 1)
            dollars = (p.open_premium if len(sold) == 1
                       else leg.premium * 100 * q)
            sold_rows.append((leg, q, dollars))

        if key in _LONG_CALL:
            leaps = bought[0]
            q = c * max(int(leaps.quantity or 1), 1)
            # What the LEAPS cost is whatever squares the sold legs with the
            # net cash of the open, so the tracker's totals match the app's.
            cost = round(sum(d for _, _, d in sold_rows) - float(p.open_cash or 0.0), 2)
            rows.append(_Row("leap", "Buy", "LEAP", p.opened,
                             _leg_expiry(p, leaps), leaps.strike, q,
                             _per_share(cost, q), collateral=cost))

        cover = rows[0].collateral
        for leg, q, dollars in sold_rows:
            if leg.option_type == OptionType.CALL:
                rows.append(_Row("cc", "Sell", "CC", p.opened, _leg_expiry(p, leg),
                                 leg.strike, q, _per_share(dollars, q),
                                 cc_collateral=cover))
            else:
                rows.append(_Row("put", "Sell", "PUT", p.opened, _leg_expiry(p, leg),
                                 leg.strike, q, _per_share(dollars, q),
                                 collateral=leg.strike * 100 * q))
        return rows, ""

    return [], "the tracker has no type for this strategy"


def _cover_cost(rows: list[_Row]) -> Optional[float]:
    """What a covered call row is written against: the shares or the LEAPS."""
    for r in reversed(rows):
        if r.kind in ("stock", "leap") and r.is_open:
            return r.collateral
    return None


def _apply_roll(rows: list[_Row], p: Position, roll) -> None:
    put = str(roll.option_type).lower() == "put"
    kinds = ("put", "pcs") if put else ("cc",)
    current = next((r for r in reversed(rows) if r.is_open and r.kind in kinds), None)

    if current is not None:
        if roll.new_strike is None:
            # Bought back (or expired worthless at 0) with nothing in its place.
            current.close_at(roll.rolled_on, roll.cash)
            return
        # Her ruling 2026-10-13: a roll stays on the SAME row, the way Ashley's
        # reference tab keeps it. Strike and expiration move to the new ones,
        # Premium $ becomes the running net credit (original + every roll's
        # net), and Notes gain one line per roll - so P/L at the close is the
        # whole trade's result, from one row.
        q = current.qty
        per = _per_share(float(roll.cash), q)
        old = (f"${current.strike:g}/${current.long_strike:g}"
               if current.kind == "pcs" and current.long_strike is not None
               else f"${current.strike:g}")
        current.premium = round(current.premium + per, 4)
        current.strike = float(roll.new_strike)
        if roll.new_expiration is not None:
            current.expires = roll.new_expiration
        if current.kind == "pcs":
            if roll.new_long_strike is not None:
                current.long_strike = roll.new_long_strike
            long_ = current.long_strike
            width = abs(current.strike - long_) if long_ is not None else 0.0
            current.collateral = width * 100 * q
            new = (f"${current.strike:g}/${long_:g}" if long_ is not None
                   else f"${current.strike:g}")
        else:
            if current.kind == "put":
                current.collateral = current.strike * 100 * q
            new = f"${current.strike:g}"
        when = f"{roll.rolled_on:%m/%d}" if roll.rolled_on else ""
        exp = (f" (exp {roll.new_expiration:%m/%d})"
               if roll.new_expiration is not None else "")
        kind = "credit" if per >= 0 else "debit"
        current.add_note(f"Rolled {when} from {old} to {new}{exp} "
                         f"for ${abs(per):.2f} {kind}")
        return

    if roll.new_strike is None:
        return
    q = max(int(p.contracts or 1), 1)
    dollars = float(roll.new_credit or roll.cash)
    if put:
        rows.append(_Row("put", "Sell", "PUT", roll.rolled_on, roll.new_expiration,
                         roll.new_strike, q, _per_share(dollars, q),
                         collateral=roll.new_strike * 100 * q))
    else:
        rows.append(_Row("cc", "Sell", "CC", roll.rolled_on, roll.new_expiration,
                         roll.new_strike, q, _per_share(dollars, q),
                         cc_collateral=_cover_cost(rows)))


def _apply_assignment(rows: list[_Row], p: Position) -> None:
    for r in rows:
        if r.is_open and r.kind == "put":
            r.close_at(p.assigned_on, 0.0)    # the put was exercised, not bought
    c = max(int(p.contracts or 1), 1)
    cost = float(p.shares_cost or 0.0)
    strike = float(p.assigned_strike or _per_share(cost, c))
    rows.append(_Row("stock", "ASSIGNED", "STOCK", p.assigned_on, None, strike, c,
                     _per_share(cost, c), collateral=cost,
                     note=f"Assigned at ${strike:g}"))


def _apply_close(rows: list[_Row], p: Position) -> None:
    still = [r for r in rows if r.is_open]
    if not still:
        return
    cash = p.close_cash if p.close_cash is not None else -(p.exit_cost or 0.0)
    # The close is one number for the whole trade. It goes on the shares or
    # the LEAPS when there is one (that is what being called away or selling
    # the LEAPS is), and every option still open closes at 0 beside it - so
    # the tracker's Total Profit still equals the app's result.
    anchor = next((r for r in still if r.kind in ("stock", "leap")), still[0])
    for r in still:
        r.close_at(p.closed_on, float(cash) if r is anchor else 0.0)
    if len(still) > 1:
        anchor.add_note("Closed with the whole trade")


def _skip_reason(p: Position) -> str:
    if (p.account or "") != "real":
        return "not a real-money trade"
    if p.status in ("merged", "legacy") or p.merged_into:
        return "merged into another trade" if p.merged_into else "logged before tracking"
    if p.strategy_key not in INCLUDED:
        return "the tracker has no type for this strategy"
    if p.wings:
        return "became an iron condor"
    if p.leg_closes:
        return "a single leg was taken off - the tracker cannot show that"
    return ""


def rows_for(p: Position, start: Optional[date] = None) -> tuple[list[dict[str, Any]], str]:
    """The tracker rows for one trade: ([{"key", "cells"}], skip_reason).

    Keys are numbered before the date filter so a row keeps its key for good.
    """
    start = start or start_date()
    why = _skip_reason(p)
    if why:
        return [], why
    rows, why = _opening_rows(p)
    if why:
        return [], why

    events: list[tuple[date, int, int, str, Any]] = []
    for r in p.rolls:
        put = str(r.option_type).lower() == "put"
        events.append((r.rolled_on or date.min, 0 if put else 2, r.seq, "roll", r))
    if p.assigned_on is not None:
        events.append((p.assigned_on, 1, 0, "assign", None))
    for _, _, _, kind, ev in sorted(events, key=lambda e: e[:3]):
        if kind == "roll":
            _apply_roll(rows, p, ev)
        else:
            _apply_assignment(rows, p)
    if p.status == "closed":
        _apply_close(rows, p)

    out = []
    for n, r in enumerate(rows, start=1):
        if r.closed is not None and r.closed < start:
            continue
        out.append({"key": f"{p.trade_id}#{n}", "cells": {
            "Type": r.side,
            "Strategy": r.strategy,
            "Open Date": _iso(r.opened),
            "Exp Date": _iso(r.expires),
            "Closed Date": _iso(r.closed),
            "Ticker": google_symbol(p.underlying),
            "Strike $": round(float(r.strike), 4),
            "Quantity": r.qty,
            "Premium $": r.premium,
            "Close $": "" if r.close is None else r.close,
            "Covered Call Collateral": _money(r.cc_collateral),
            "Collateral": _money(r.collateral),
            "Notes": r.note,
        }})
    return out, ""


def build(positions: list[Position], trade_ids: Optional[list[str]] = None,
          start: Optional[date] = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Payload for the script: ([{"trade_id", "rows"}], skipped).

    With trade_ids, only those trades - and an id with no position at all
    (deleted) is sent with no rows, which clears it from the tracker. Without,
    every real trade that has a row on or after `start`.
    """
    by_id = {p.trade_id: p for p in positions if p.trade_id}
    wanted = trade_ids if trade_ids is not None else list(by_id)
    trades, skipped = [], []
    for tid in wanted:
        p = by_id.get(tid)
        if p is None:
            trades.append({"trade_id": tid, "rows": []})
            continue
        rows, why = rows_for(p, start)
        if why:
            recent = (p.status != "closed"
                      or (p.closed_on or date.max) >= (start or start_date()))
            if ((p.account or "") == "real" and trade_ids is None and recent
                    and not p.merged_into):
                skipped.append({"trade_id": tid, "underlying": p.underlying,
                                "strategy": p.strategy_name, "why": why})
            if trade_ids is None:
                continue
        if trade_ids is None and not rows:
            continue
        trades.append({"trade_id": tid, "rows": rows})
    return trades, skipped


def sync(trade_ids: Optional[list[str]] = None) -> dict[str, Any]:
    """Read the log, rebuild the tracker rows and send them. Raises on failure."""
    from src.logging_tools import trade_logger, webhook_logger

    header, rows, source = trade_logger.fetch_all_rows()
    if source != "sheet":
        raise RuntimeError("Your Google Sheet is not connected.")
    trades, skipped = build(parse_rows(header, rows), trade_ids)
    result = webhook_logger.send_tracker(trades) if trades else {}
    return {"trades": len(trades), "skipped": skipped, **result}


def trade_ids_in_row(row: list[Any], columns: list[str]) -> list[str]:
    """Which trades an event row touches: its own, and the one a merge joins."""
    def cell(name: str) -> Any:
        i = columns.index(name)
        return row[i] if i < len(row) else ""

    ids = [str(cell("Trade ID") or "")]
    if str(cell("Event") or "") == "merge":
        try:
            into = json.loads(str(cell("Details JSON") or "{}")).get("into")
        except (ValueError, AttributeError):
            into = None
        if into:
            ids.append(str(into))
    return [i for i in ids if i]
