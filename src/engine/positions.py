"""Turns the trade log (Google Sheet or local Excel) into positions the
"My trades" tab can track.

The log is an event log: an "open" row when a trade is logged, and a "close"
row with the same Trade ID when it is closed in the app. This module is pure
(no network, no Streamlit) so it is fully unit-tested.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.engine.models import Action, Leg, OptionType


class Position(BaseModel):
    """One logged trade and everything known about it."""

    trade_id: str = ""
    underlying: str = ""
    strategy_name: str = ""
    strategy_key: str = ""            # from Details JSON; "" on legacy rows
    opened: Optional[date] = None
    expiration: Optional[date] = None
    dte_at_entry: Optional[int] = None
    contracts: int = 1
    credit: float = 0.0               # dollars collected for the whole position
    max_loss: float = 0.0
    buying_power: float = 0.0
    short_delta: float = 0.0
    passed_sop: str = ""
    note: str = ""
    legs: list[Leg] = Field(default_factory=list)
    underlying_price_at_entry: Optional[float] = None

    # "open" = being tracked, "closed" = has a close row,
    # "legacy" = logged before the tracker existed (history only).
    status: str = "open"
    closed_on: Optional[date] = None
    exit_cost: Optional[float] = None    # dollars paid to close
    realized_pl: Optional[float] = None
    exit_reason: str = ""

    def dte_left(self, today: Optional[date] = None) -> Optional[int]:
        if self.expiration is None:
            return None
        return (self.expiration - (today or date.today())).days

    @property
    def short_strikes(self) -> list[float]:
        return [leg.strike for leg in self.legs if leg.action == Action.SELL]

    @property
    def can_track(self) -> bool:
        """True if there is enough saved detail to re-price and check exits."""
        return bool(self.trade_id) and bool(self.legs) and self.expiration is not None


# ------------------------------------------------------------------ parsing
def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _to_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)
    if "T" in s:
        # The sheet stores our plain ISO dates in Date cells and hands them
        # back as UTC instants: a July 5 trade logged in Israel (UTC+3) comes
        # back as "2026-07-04T21:00:00.000Z". Convert to the local calendar
        # day instead of truncating, or month math shifts at the boundary.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _column_index(header: list[str]) -> dict[str, int]:
    return {str(name).strip().lower(): i for i, name in enumerate(header)}


def _get(row: list[Any], idx: dict[str, int], name: str, fallback: int) -> Any:
    """Value by column name; falls back to the classic position for old logs."""
    i = idx.get(name.lower(), fallback)
    if i is None or i < 0 or i >= len(row):
        return None
    return row[i]


def _parse_legs(details: Any) -> tuple[str, Optional[float], list[Leg]]:
    """(strategy_key, underlying_price, legs) from the Details JSON cell."""
    if not details:
        return "", None, []
    try:
        data = json.loads(str(details))
    except (json.JSONDecodeError, TypeError):
        return "", None, []
    legs = []
    for d in data.get("legs", []):
        try:
            legs.append(Leg(
                role=d.get("role", ""),
                action=Action(d.get("action", "sell")),
                option_type=OptionType(d.get("type", "put")),
                strike=float(d.get("strike", 0.0)),
                delta=float(d.get("delta", 0.0) or 0.0),
                premium=float(d.get("premium", 0.0) or 0.0),
                quantity=int(d.get("qty", 1) or 1),
                dte=d.get("dte"),
            ))
        except (TypeError, ValueError):
            return data.get("key", ""), _to_float(data.get("underlying_price")), []
    return data.get("key", ""), _to_float(data.get("underlying_price")), legs


def parse_rows(header: list[str], rows: list[list[Any]]) -> list[Position]:
    """All positions from the log, oldest first. Close rows are folded into
    their open row by Trade ID. Rows from before the tracker are "legacy"."""
    idx = _column_index(header)
    opens: dict[str, Position] = {}
    ordered: list[Position] = []
    closes: list[dict[str, Any]] = []

    for row in rows:
        row = list(row)
        first = str(row[0] if row else "")
        if first.startswith("TEST"):     # the sidebar "Test it" rows
            continue
        event = str(_get(row, idx, "Event", 13) or "").strip().lower()
        trade_id = str(_get(row, idx, "Trade ID", 12) or "").strip()

        if event == "close" and trade_id:
            closes.append({
                "trade_id": trade_id,
                "closed_on": _to_date(_get(row, idx, "Date", 0)),
                "exit_cost": _to_float(_get(row, idx, "Exit Cost $", 15)),
                "realized_pl": _to_float(_get(row, idx, "Realized P&L $", 16)),
                "reason": str(_get(row, idx, "Notes", 11) or ""),
            })
            continue

        key, entry_px, legs = _parse_legs(_get(row, idx, "Details JSON", 17))
        pos = Position(
            trade_id=trade_id,
            underlying=str(_get(row, idx, "Underlying", 1) or ""),
            strategy_name=str(_get(row, idx, "Strategy", 2) or ""),
            strategy_key=key,
            opened=_to_date(_get(row, idx, "Date", 0)),
            expiration=_to_date(_get(row, idx, "Expiration", 14)),
            dte_at_entry=(lambda v: int(v) if v is not None else None)(
                _to_float(_get(row, idx, "DTE", 5))),
            contracts=int(_to_float(_get(row, idx, "Contracts", 6)) or 1),
            credit=_to_float(_get(row, idx, "Credit $", 7)) or 0.0,
            max_loss=_to_float(_get(row, idx, "Max Loss $", 8)) or 0.0,
            buying_power=_to_float(_get(row, idx, "Buying Power $", 9)) or 0.0,
            short_delta=_to_float(_get(row, idx, "Short Delta", 4)) or 0.0,
            passed_sop=str(_get(row, idx, "Passed SOP", 10) or ""),
            note=str(_get(row, idx, "Notes", 11) or ""),
            legs=legs,
            underlying_price_at_entry=entry_px,
            status="open" if trade_id else "legacy",
        )
        ordered.append(pos)
        if trade_id:
            opens[trade_id] = pos

    for c in closes:
        pos = opens.get(c["trade_id"])
        if pos is None:
            continue
        pos.status = "closed"
        pos.closed_on = c["closed_on"]
        pos.exit_cost = c["exit_cost"]
        pos.realized_pl = c["realized_pl"]
        pos.exit_reason = c["reason"]

    return ordered


def open_positions(positions: list[Position]) -> list[Position]:
    return [p for p in positions if p.status == "open"]


def closed_positions(positions: list[Position]) -> list[Position]:
    return [p for p in positions if p.status == "closed"]


def bp_in_use(positions: list[Position]) -> float:
    """Buying power tied up by every open position, together."""
    return sum(p.buying_power for p in open_positions(positions))


# ------------------------------------------------------------------ live pricing math
def cost_to_close_from_chain(position: Position, chain) -> Optional[dict[str, float]]:
    """What it costs to close the position's near-dated legs at today's mids.

    Only prices legs at the position's NEAR expiration (for a PMCC or covered
    call that is exactly the short call - the leg your 50% rule applies to;
    for spreads and iron condors it is every leg). Returns
    {"cost_to_close": dollars, "short_delta": per-share} or None when the
    chain doesn't carry the needed contracts.
    """
    if not position.can_track or position.expiration is None:
        return None
    exp = position.expiration.isoformat()
    entry_dtes = [leg.dte for leg in position.legs if leg.dte is not None]
    near_dte = min(entry_dtes) if entry_dtes else None

    per_share = 0.0
    short_delta = 0.0
    priced_any = False
    for leg in position.legs:
        if near_dte is not None and leg.dte is not None and leg.dte != near_dte:
            continue   # far-dated leg (LEAPS / long-term protective put)
        contract = next(
            (c for c in chain.contracts
             if c.expiration == exp and c.option_type == leg.option_type
             and abs(c.strike - leg.strike) < 1e-6),
            None)
        if contract is None or contract.mid <= 0:
            return None
        priced_any = True
        if leg.action == Action.SELL:
            per_share += contract.mid * leg.quantity   # you buy it back
            short_delta = max(short_delta, abs(contract.delta))
        else:
            per_share -= contract.mid * leg.quantity   # you sell it back
    if not priced_any:
        return None
    return {
        "cost_to_close": round(per_share * 100 * position.contracts, 2),
        "short_delta": round(short_delta, 3),
    }


def strike_cushion(position: Position,
                   underlying_price: Optional[float]) -> Optional[dict[str, Any]]:
    """How much room is left before price reaches an option you SOLD.

    Looks at every short leg and reports the one closest to trouble (for an
    iron condor that is whichever side price is nearer; for a covered call or
    PMCC it is the short call). room_pct is how far price still has to move
    to reach that strike, as a fraction of today's price - negative once the
    strike is breached. None when there is no short leg or no live price.
    """
    if underlying_price is None or underlying_price <= 0:
        return None
    nearest: Optional[dict[str, Any]] = None
    for leg in position.legs:
        if leg.action != Action.SELL or leg.strike <= 0:
            continue
        if leg.option_type == OptionType.PUT:
            room = (underlying_price - leg.strike) / underlying_price
        else:
            room = (leg.strike - underlying_price) / underlying_price
        if nearest is None or room < nearest["room_pct"]:
            nearest = {
                "strike": leg.strike,
                "option_type": leg.option_type.value,
                "room_pct": room,
                "breached": room < 0,
            }
    return nearest


# ------------------------------------------------------------------ results
def performance(positions: list[Position], today: Optional[date] = None) -> dict[str, Any]:
    """Realized results from closed trades - what the dashboard shows."""
    today = today or date.today()
    closed = [p for p in closed_positions(positions) if p.realized_pl is not None]

    week_start = date.fromordinal(today.toordinal() - today.weekday())  # Monday
    month_start = today.replace(day=1)

    def total(since: date) -> float:
        return sum(p.realized_pl for p in closed
                   if p.closed_on is not None and p.closed_on >= since)

    wins = [p.realized_pl for p in closed if p.realized_pl > 0]
    losses = [p.realized_pl for p in closed if p.realized_pl <= 0]

    by_strategy: dict[str, dict[str, float]] = {}
    for p in closed:
        s = by_strategy.setdefault(p.strategy_name or "(unknown)",
                                   {"trades": 0, "pl": 0.0, "wins": 0})
        s["trades"] += 1
        s["pl"] += p.realized_pl
        s["wins"] += 1 if p.realized_pl > 0 else 0

    dated = sorted((p for p in closed if p.closed_on is not None),
                   key=lambda p: p.closed_on)
    cumulative, running = [], 0.0
    for p in dated:
        running += p.realized_pl
        cumulative.append({"date": p.closed_on, "total": round(running, 2)})

    return {
        "closed_count": len(closed),
        "total_pl": round(sum(p.realized_pl for p in closed), 2),
        "week_pl": round(total(week_start), 2),
        "month_pl": round(total(month_start), 2),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "by_strategy": by_strategy,
        "cumulative": cumulative,
    }


# ------------------------------------------------------------------ month view
# Close reasons that count as "followed your exit rules". The "21 dte" prefix
# covers both SOP outcomes at that point - "21 DTE time exit" (closed) and
# "21 DTE credit roll" (rolled for a net credit) - because since the 2026-07-14
# rule change either one is compliant; what breaks the rule is drifting past 21
# DTE with no decision. A bare "Rolled" (a roll at any other moment) and "Other"
# deliberately do not count.
_SOP_EXIT_PREFIXES = ("profit target", "21 dte", "stop loss", "expired")


def _split_exit_reason(exit_reason: str) -> tuple[str, str]:
    """The close flow stores "reason - lesson text" in one cell; split it back."""
    parts = exit_reason.split(" - ", 1)
    reason = parts[0].strip()
    lesson = parts[1].strip() if len(parts) > 1 else ""
    return reason, lesson


def monthly_summary(positions: list[Position],
                    today: Optional[date] = None) -> list[dict[str, Any]]:
    """One entry per calendar month with activity, newest first - the data
    behind the month-by-month view. The current month is always present.

    Profit lands in the month a trade was CLOSED (the month the money was
    banked), so the current month's number always equals
    performance()["month_pl"]. A trade opened in June and closed in July
    appears in both months' lists, tagged so the table can say which.
    """
    today = today or date.today()
    months: dict[str, dict[str, Any]] = {}

    def entry(d: date) -> dict[str, Any]:
        key = f"{d.year:04d}-{d.month:02d}"
        if key not in months:
            months[key] = {
                "month": key,
                "label": d.strftime("%B %Y"),
                "realized_pl": 0.0,
                "closed_count": 0,
                "wins": 0,
                "win_rate": None,
                "opened_count": 0,
                "bp_opened": 0.0,
                "still_open": 0,
                "rules_followed": 0,
                "lessons": [],
                "rows": [],
            }
        return months[key]

    entry(today)   # the current month exists even before any trade

    for p in positions:
        opened_key = f"{p.opened.year:04d}-{p.opened.month:02d}" if p.opened else None
        closed_on = p.closed_on if p.status == "closed" else None
        closed_key = (f"{closed_on.year:04d}-{closed_on.month:02d}"
                      if closed_on else None)

        if p.opened is not None:
            e = entry(p.opened)
            e["opened_count"] += 1
            e["bp_opened"] += p.buying_power
            if p.status == "open":
                e["still_open"] += 1
            tag = "both" if (closed_key is not None and closed_key == opened_key) \
                else "opened"
            e["rows"].append({"position": p, "tag": tag})

        if closed_on is not None:
            e = entry(closed_on)
            if closed_key != opened_key:
                e["rows"].append({"position": p, "tag": "closed"})
            e["closed_count"] += 1
            if p.realized_pl is not None:
                e["realized_pl"] += p.realized_pl
                if p.realized_pl > 0:
                    e["wins"] += 1
            reason, lesson = _split_exit_reason(p.exit_reason or "")
            if reason.lower().startswith(_SOP_EXIT_PREFIXES):
                e["rules_followed"] += 1
            if lesson:
                e["lessons"].append(lesson)

    for e in months.values():
        e["realized_pl"] = round(e["realized_pl"], 2)
        e["bp_opened"] = round(e["bp_opened"], 2)
        if e["closed_count"]:
            e["win_rate"] = e["wins"] / e["closed_count"]
        e["lessons"].reverse()   # newest lesson first

    return sorted(months.values(), key=lambda e: e["month"], reverse=True)
