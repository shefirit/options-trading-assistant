"""Turns the trade log (Google Sheet or local Excel) into positions the
"My trades" tab can track.

The log is an event log, keyed by Trade ID:
  - an "open" row when a trade is logged
  - zero or more "roll" rows when the income leg (the short call) is rolled
  - a "close" row when it is closed in the app

Money is tracked as a signed CASH LEDGER, because the eight strategies come in
two opposite shapes and the old credit-in/cost-out model only fitted one:

  credit shapes (credit spreads, iron condor, CSP)
      open_cash = + the credit collected;  close_cash = - what it cost to buy back

  debit shapes (PMCC, the three covered call models)
      open_cash = - (what the LEAPS / shares / protective put cost) + the call credit
      close_cash = + what she RECEIVED when she sold the long side back

Either way the arithmetic is the same: every dollar in, minus every dollar out.
Rolls bank their credit on the day they happen, so a covered call rolled monthly
for a year shows income in each of those months instead of one lump at the end.

This module is pure (no network, no Streamlit) so it is fully unit-tested.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.engine.models import Action, Leg, OptionType


class RollEvent(BaseModel):
    """One roll of the income leg: buying back the short call you sold and
    selling a further-out one in its place, usually for a net credit."""

    rolled_on: Optional[date] = None
    cash: float = 0.0                        # signed: + collected, - paid
    new_strike: Optional[float] = None
    new_expiration: Optional[date] = None
    # What the NEW short call sold for on its own. This - not the net cash - is
    # what the 50%-of-credit profit target measures against from here on.
    new_credit: float = 0.0
    note: str = ""


class Position(BaseModel):
    """One logged trade and everything known about it."""

    trade_id: str = ""
    underlying: str = ""
    strategy_name: str = ""
    strategy_key: str = ""            # from Details JSON; "" on legacy rows
    opened: Optional[date] = None
    expiration: Optional[date] = None    # of the NEAR leg; a roll moves it out
    dte_at_entry: Optional[int] = None
    contracts: int = 1
    # Premium collected for the short leg(s) - the basis for the 50% profit
    # target and the 2x stop. On a credit spread that IS the whole position; on
    # a PMCC it is only the short call, and a roll replaces it with the new one.
    credit: float = 0.0
    # Signed net cash at open: positive when the position paid her to open
    # (credit spreads), negative when it cost her (PMCC, covered calls).
    # Legacy rows logged before the ledger existed default to credit.
    open_cash: float = 0.0
    # What the 100 real shares per contract cost (covered call models only).
    # Held separately because shares are not in the option chain but still have
    # to be valued at today's price when pricing the position.
    shares_cost: float = 0.0
    max_loss: float = 0.0
    buying_power: float = 0.0
    short_delta: float = 0.0
    passed_sop: str = ""
    note: str = ""
    legs: list[Leg] = Field(default_factory=list)
    underlying_price_at_entry: Optional[float] = None
    rolls: list[RollEvent] = Field(default_factory=list)

    # "open" = being tracked, "closed" = has a close row,
    # "legacy" = logged before the tracker existed (history only).
    status: str = "open"
    closed_on: Optional[date] = None
    exit_cost: Optional[float] = None    # dollars paid to close (credit shapes)
    # Signed net cash at close: negative when buying the position back cost her,
    # positive when closing PAID her (selling the LEAPS back on a PMCC).
    close_cash: Optional[float] = None
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

    @property
    def is_debit(self) -> bool:
        """True when OPENING this position cost money instead of paying her.

        The PMCC and the covered call models buy a long-dated leg up front, so
        closing them pays her back - the opposite of a credit spread.
        """
        return self.open_cash < 0

    @property
    def capital_at_risk(self) -> float:
        """The dollars actually tied up - what a return % should divide by."""
        return abs(self.open_cash) if self.is_debit else self.buying_power

    @property
    def roll_income(self) -> float:
        """Premium banked from every roll so far, counted the day it landed."""
        return round(sum(r.cash for r in self.rolls), 2)

    @property
    def realized_total(self) -> Optional[float]:
        """Every dollar this position has actually banked, start to finish.

        On a closed position that is the whole story. On an open one it is the
        roll income collected so far, which is already hers.
        """
        if self.realized_pl is None:
            return self.roll_income if self.rolls else None
        return round(self.realized_pl + self.roll_income, 2)

    @property
    def is_uncovered(self) -> bool:
        """A PMCC or covered call with no short call written against it today.

        A normal part of the rhythm, not a mistake: her SOP takes the win at
        50% of the credit, and she may sit on the long side for a while before
        writing the next call. Nothing is earning while that is true, and the
        long side is exposed both ways, so the card says so instead of running
        exit rules against a call that isn't there.
        """
        return self.is_debit and not any(
            leg.action == Action.SELL and leg.option_type == OptionType.CALL
            for leg in self.legs)

    @property
    def far_legs(self) -> list[Leg]:
        """The long-dated legs: the LEAPS on a PMCC, the protective put on a
        covered call. Empty on single-expiration positions."""
        dtes = [leg.dte for leg in self.legs if leg.dte is not None]
        if not dtes:
            return []
        near = min(dtes)
        return [leg for leg in self.legs if leg.dte is not None and leg.dte != near]

    def leg_expiration(self, leg: Leg) -> Optional[date]:
        """When this leg expires.

        Quick Log stores each leg's DTE as measured from `opened` on the day it
        was written (dte = (expiration - opened).days), so adding it back to
        `opened` reproduces the exact date with no extra column to store. A
        roll updates the short leg's dte the same way, keeping this true.
        """
        if leg.dte is None or self.opened is None:
            return None
        return self.opened + timedelta(days=int(leg.dte))


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


def _parse_details(details: Any) -> tuple[dict[str, Any], list[Leg]]:
    """(the Details JSON as a dict, its legs) from the Details JSON cell."""
    if not details:
        return {}, []
    try:
        data = json.loads(str(details))
    except (json.JSONDecodeError, TypeError):
        return {}, []
    if not isinstance(data, dict):
        return {}, []
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
            return data, []
    return data, legs


def _apply_roll(pos: Position, roll: RollEvent) -> None:
    """Move the position's short call to wherever this event put it.

    Three shapes, all one event because all three are just "cash moved and the
    short call changed":
      rolled      bought one back and sold a later one - the usual case
      bought back new_strike is None: she is now uncovered, holding the long
                  side while she waits for a level to write the next call
      written     she was uncovered and has now sold one, so the leg comes back

    After this the tracker prices the contract she actually holds, counts down
    to its expiration, and measures the 50% target against its own credit.
    """
    pos.rolls.append(roll)

    # The income leg is the nearest-dated short CALL. Model 2 and 3 also carry
    # short PUTs, which a call roll must never touch.
    short_calls = [leg for leg in pos.legs
                   if leg.action == Action.SELL and leg.option_type == OptionType.CALL]
    leg = (min(short_calls, key=lambda l: (l.dte if l.dte is not None else 10**6))
           if short_calls else None)

    if roll.new_strike is None:
        # Bought back with nothing written in its place. Drop the leg: she does
        # not hold that contract any more, and leaving it would keep counting
        # down to an expiration that no longer applies to her.
        if leg is not None:
            pos.legs.remove(leg)
        pos.credit = 0.0
        pos.expiration = _long_side_expiration(pos) or pos.expiration
        return

    if roll.new_expiration is not None:
        pos.expiration = roll.new_expiration
    if roll.new_credit > 0:
        pos.credit = roll.new_credit

    new_dte = (max((roll.new_expiration - pos.opened).days, 0)
               if roll.new_expiration is not None and pos.opened is not None
               else None)
    if leg is None:
        # She was uncovered and has written a fresh call against the long side.
        pos.legs.append(Leg(
            role="short_call", action=Action.SELL, option_type=OptionType.CALL,
            strike=roll.new_strike, quantity=1, dte=new_dte))
        return

    leg.strike = roll.new_strike
    if new_dte is not None:
        # Keep dte measured from `opened`, the invariant leg_expiration() and
        # the near/far split both rely on.
        leg.dte = new_dte
    # The old contract's delta and premium describe an option she no longer
    # holds; leaving them would quietly feed a stale delta to the red-flag check.
    leg.delta = 0.0
    leg.premium = 0.0


def _long_side_expiration(pos: Position) -> Optional[date]:
    """The last date anything she still holds expires - the LEAPS on a PMCC,
    the protective put on a covered call. What the countdown means once there
    is no short call left to count down to."""
    dates = [d for d in (pos.leg_expiration(leg) for leg in pos.legs)
             if d is not None]
    return max(dates) if dates else None


def parse_rows(header: list[str], rows: list[list[Any]]) -> list[Position]:
    """All positions from the log, oldest first. Roll and close rows are folded
    into their open row by Trade ID. Rows from before the tracker are "legacy"."""
    idx = _column_index(header)
    opens: dict[str, Position] = {}
    ordered: list[Position] = []
    rolls: list[tuple[str, RollEvent]] = []
    closes: list[dict[str, Any]] = []

    for row in rows:
        row = list(row)
        first = str(row[0] if row else "")
        if first.startswith("TEST"):     # the sidebar "Test it" rows
            continue
        event = str(_get(row, idx, "Event", 13) or "").strip().lower()
        trade_id = str(_get(row, idx, "Trade ID", 12) or "").strip()

        if event == "close" and trade_id:
            exit_cost = _to_float(_get(row, idx, "Exit Cost $", 15))
            data, _ = _parse_details(_get(row, idx, "Details JSON", 17))
            close_cash = _to_float(data.get("close_cash"))
            if close_cash is None and exit_cost is not None:
                # Rows written before the ledger: closing only ever cost money.
                close_cash = -exit_cost
            closes.append({
                "trade_id": trade_id,
                "closed_on": _to_date(_get(row, idx, "Date", 0)),
                "exit_cost": exit_cost,
                "close_cash": close_cash,
                "realized_pl": _to_float(_get(row, idx, "Realized P&L $", 16)),
                "reason": str(_get(row, idx, "Notes", 11) or ""),
            })
            continue

        if event == "roll" and trade_id:
            rolls.append((trade_id, RollEvent(
                rolled_on=_to_date(_get(row, idx, "Date", 0)),
                cash=_to_float(_get(row, idx, "Realized P&L $", 16)) or 0.0,
                new_strike=_to_float(_get(row, idx, "Legs (strikes)", 3)),
                new_expiration=_to_date(_get(row, idx, "Expiration", 14)),
                new_credit=_to_float(_get(row, idx, "Credit $", 7)) or 0.0,
                note=str(_get(row, idx, "Notes", 11) or ""),
            )))
            continue

        data, legs = _parse_details(_get(row, idx, "Details JSON", 17))
        credit = _to_float(_get(row, idx, "Credit $", 7)) or 0.0
        open_cash = _to_float(data.get("open_cash"))
        if open_cash is None:
            # Rows written before the ledger existed were all treated as pure
            # credit, which is right for the spreads and wrong for the debit
            # shapes - but their money was never recorded, so credit is the
            # most honest reading available for them.
            open_cash = credit
        pos = Position(
            trade_id=trade_id,
            underlying=str(_get(row, idx, "Underlying", 1) or ""),
            strategy_name=str(_get(row, idx, "Strategy", 2) or ""),
            strategy_key=str(data.get("key", "")),
            opened=_to_date(_get(row, idx, "Date", 0)),
            expiration=_to_date(_get(row, idx, "Expiration", 14)),
            dte_at_entry=(lambda v: int(v) if v is not None else None)(
                _to_float(_get(row, idx, "DTE", 5))),
            contracts=int(_to_float(_get(row, idx, "Contracts", 6)) or 1),
            credit=credit,
            open_cash=open_cash,
            shares_cost=_to_float(data.get("shares_cost")) or 0.0,
            max_loss=_to_float(_get(row, idx, "Max Loss $", 8)) or 0.0,
            buying_power=_to_float(_get(row, idx, "Buying Power $", 9)) or 0.0,
            short_delta=_to_float(_get(row, idx, "Short Delta", 4)) or 0.0,
            passed_sop=str(_get(row, idx, "Passed SOP", 10) or ""),
            note=str(_get(row, idx, "Notes", 11) or ""),
            legs=legs,
            underlying_price_at_entry=_to_float(data.get("underlying_price")),
            status="open" if trade_id else "legacy",
        )
        ordered.append(pos)
        if trade_id:
            opens[trade_id] = pos

    # Rolls in the order they happened, so the last one wins on strike/date.
    for trade_id, roll in sorted(
            rolls, key=lambda r: r[1].rolled_on or date.min):
        pos = opens.get(trade_id)
        if pos is not None:
            _apply_roll(pos, roll)

    for c in closes:
        pos = opens.get(c["trade_id"])
        if pos is None:
            continue
        pos.status = "closed"
        pos.closed_on = c["closed_on"]
        pos.exit_cost = c["exit_cost"]
        pos.close_cash = c["close_cash"]
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


def bp_in_use_this_month(positions: list[Position], today: date | None = None) -> float:
    """Worst-case capital tied up by trades OPENED this calendar month that are
    still open - the figure that counts against the monthly buying-power budget.

    This is a gross sum of each trade's max loss / collateral, not real broker
    buying power: your broker nets and offsets positions against each other, so
    this will read higher than the buying power your platform shows used. It is
    a worst-case guardrail, not a margin figure. Trades opened in earlier months
    (even if still open) do not count against THIS month's deployment."""
    ref = today or date.today()
    return sum(
        p.buying_power for p in open_positions(positions)
        if p.opened is not None
        and p.opened.year == ref.year and p.opened.month == ref.month
    )


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
    if position.is_uncovered:
        # Nothing has been sold, so there is nothing to buy back. Without this
        # the near leg would be the LEAPS itself and "costs to close" would
        # come back negative - the chain quoting what selling it would PAY her.
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


def position_value_from_chain(position: Position, chain,
                              underlying_price: Optional[float] = None,
                              ) -> Optional[dict[str, float]]:
    """What the WHOLE position is worth right now - far-dated legs included.

    cost_to_close_from_chain() answers a deliberately narrower question: what
    the NEAR legs cost to buy back, which is what her 50%-of-credit rule
    measures. On a PMCC that leaves out the LEAPS, i.e. nearly all the money -
    a position can sit at "you've kept 40% of the credit" while the LEAPS alone
    is up ten times that. This prices every leg so the card shows the real one.

    Returns
      value        what unwinding every leg today would pay her (signed)
      open_pl      value + the ledger so far = profit if she closed now
      options_pl   the same for the OPTIONS alone (covered calls only)
      shares_pl    what the 100 real shares per contract have done (ditto)

    On a covered call the shares are hers and the options are the trade run
    against them, so those two are worth seeing apart: the calls can be earning
    nicely while the stock drifts down, and one number hides that. They always
    add back up to open_pl. None when the chain doesn't carry every contract.
    """
    if not position.legs or position.opened is None:
        return None

    value = 0.0
    for leg in position.legs:
        exp = position.leg_expiration(leg)
        if exp is None:
            return None
        contract = next(
            (c for c in chain.contracts
             if c.expiration == exp.isoformat() and c.option_type == leg.option_type
             and abs(c.strike - leg.strike) < 1e-6),
            None)
        if contract is None or contract.mid <= 0:
            return None
        # Unwinding sells what she is long and buys back what she is short.
        sign = 1.0 if leg.action == Action.BUY else -1.0
        value += sign * contract.mid * leg.quantity

    options_value = value * 100 * position.contracts
    value = options_value
    shares_pl = None

    if position.shares_cost > 0:
        # The covered call models hold 100 real shares per contract. They are
        # not in the chain, and their cost is already inside open_cash, so the
        # position is only worth what it is with them counted at today's price.
        if not underlying_price or underlying_price <= 0:
            return None
        shares_value = underlying_price * 100 * position.contracts
        value += shares_value
        shares_pl = shares_value - position.shares_cost

    out = {
        "value": round(value, 2),
        "open_pl": round(position.open_cash + position.roll_income + value, 2),
    }
    if shares_pl is not None:
        # Taking the shares' cost back out of open_cash leaves what the OPTIONS
        # alone cost to put on, so options_pl + shares_pl == open_pl.
        options_cash = position.open_cash + position.shares_cost
        out["options_pl"] = round(options_cash + position.roll_income
                                  + options_value, 2)
        out["shares_pl"] = round(shares_pl, 2)
    return out


# ------------------------------------------------------------------ downside read
def expiry_value_at(position: Position, price: float) -> float:
    """What the position would be worth if the underlying finished at `price`.

    Intrinsic value only, plus the 100 real shares per contract on the covered
    call models. Deliberately built from the LEGS and today's ledger rather than
    the entry premiums, so it stays right after a roll has replaced the short
    call.
    """
    value = price * 100 * position.contracts if position.shares_cost > 0 else 0.0
    for leg in position.legs:
        if leg.option_type == OptionType.PUT:
            intrinsic = max(leg.strike - price, 0.0)
        else:
            intrinsic = max(price - leg.strike, 0.0)
        sign = 1.0 if leg.action == Action.BUY else -1.0
        value += sign * intrinsic * 100 * leg.quantity * position.contracts
    return value


def pl_at(position: Position, price: float) -> float:
    """Her profit or loss if the underlying finished at `price`: every dollar
    already banked, plus what the position would be worth there."""
    return round(position.open_cash + position.roll_income
                 + expiry_value_at(position, price), 2)


def downside_zones(position: Position,
                   underlying_price: Optional[float]) -> list[dict[str, Any]]:
    """Walking DOWN from today's price, what each stretch of the fall costs.

    The payoff of a protected covered call is a set of straight lines that kink
    at the put strikes, so the honest answer to "how bad can this get" is not
    one number - it is where the protection holds and where it stops. Each zone
    is {from, to, slope, pl_from, pl_to} with slope in dollars lost per $1 the
    underlying falls: 0 means that stretch is fully protected.

    Model 1's collar goes flat below its long put (the loss is capped). Model
    3's ratio goes flat down to the SHORT puts and then falls twice as fast
    below them, which is exactly the SOP's "losses accelerate" warning.
    """
    if not underlying_price or underlying_price <= 0 or not position.legs:
        return []
    bounds = sorted({leg.strike for leg in position.legs
                     if leg.option_type == OptionType.PUT
                     and 0 < leg.strike < underlying_price}, reverse=True)
    bounds.append(0.0)

    zones: list[dict[str, Any]] = []
    top = float(underlying_price)
    for bottom in bounds:
        drop = top - bottom
        if drop <= 0:
            continue
        pl_top, pl_bottom = pl_at(position, top), pl_at(position, bottom)
        zones.append({
            "from": round(top, 2),
            "to": round(bottom, 2),
            "slope": round((pl_top - pl_bottom) / drop, 2),
            "pl_from": pl_top,
            "pl_to": pl_bottom,
        })
        top = bottom
    return zones


# A stretch counts as flat when a $1 fall costs under a dollar - i.e. the
# protection is carrying it, give or take rounding on the strike grid.
_FLAT = 1.0


def protection_read(position: Position,
                    underlying_price: Optional[float]) -> Optional[dict[str, Any]]:
    """Where the downside protection holds and what it costs when it stops.

    Replaces the old "max loss = what you paid" reading on the covered call
    models, which was wrong in both directions: it ignored the protective put
    entirely (Model 1's whole point) and understated Model 3's tail, where the
    short puts make losses accelerate well past the cash she put in.
    """
    zones = downside_zones(position, underlying_price)
    if not zones:
        return None
    first = zones[0]
    return {
        "zones": zones,
        "pl_now": first["pl_from"],
        # The price where the story changes - the first kink below today.
        "break_price": first["to"],
        # Set only when she is protected from TODAY down to that price.
        "flat_to": first["to"] if abs(first["slope"]) < _FLAT else None,
        "slope_below": zones[1]["slope"] if len(zones) > 1 else 0.0,
        "worst_case": zones[-1]["pl_to"],
        # True when the loss stops growing at the bottom (a collar's put cap).
        "capped": abs(zones[-1]["slope"]) < _FLAT,
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
def cash_events(positions: list[Position]) -> list[dict[str, Any]]:
    """Every dollar actually banked, as dated events, oldest first.

    Two kinds: a "close" banks the position's closing result, and a "roll" banks
    the credit collected that day. Rolls count on their own date and not at the
    close, so income from a covered call rolled monthly for a year lands in each
    of those twelve months - which is how her monthly goal is measured.
    """
    events: list[dict[str, Any]] = []
    for p in positions:
        for r in p.rolls:
            if r.rolled_on is not None and r.cash:
                events.append({"date": r.rolled_on, "amount": r.cash,
                               "kind": "roll", "position": p})
        if (p.status == "closed" and p.realized_pl is not None
                and p.closed_on is not None):
            events.append({"date": p.closed_on, "amount": p.realized_pl,
                           "kind": "close", "position": p})
    return sorted(events, key=lambda e: e["date"])


def performance(positions: list[Position], today: Optional[date] = None) -> dict[str, Any]:
    """Realized results - what the dashboard shows."""
    today = today or date.today()
    closed = [p for p in closed_positions(positions) if p.realized_pl is not None]
    events = cash_events(positions)

    week_start = date.fromordinal(today.toordinal() - today.weekday())  # Monday
    month_start = today.replace(day=1)

    def total(since: date) -> float:
        return sum(e["amount"] for e in events if e["date"] >= since)

    # A trade "won" on its whole-life result, roll income included - that is the
    # number she would call the trade's profit.
    results = [p.realized_total for p in closed if p.realized_total is not None]
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]

    by_strategy: dict[str, dict[str, float]] = {}
    for p in closed:
        result = p.realized_total or 0.0
        s = by_strategy.setdefault(p.strategy_name or "(unknown)",
                                   {"trades": 0, "pl": 0.0, "wins": 0})
        s["trades"] += 1
        s["pl"] += result
        s["wins"] += 1 if result > 0 else 0

    cumulative, running = [], 0.0
    for e in events:
        running += e["amount"]
        cumulative.append({"date": e["date"], "total": round(running, 2)})

    return {
        "closed_count": len(closed),
        "total_pl": round(sum(e["amount"] for e in events), 2),
        "week_pl": round(total(week_start), 2),
        "month_pl": round(total(month_start), 2),
        "win_rate": (len(wins) / len(results)) if results else None,
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

    Profit lands in the month the money was banked, so the current month's
    number always equals performance()["month_pl"]. That means a close counts in
    its close month and a ROLL counts in the month it was rolled - a PMCC opened
    in June, rolled in July and closed in July puts the roll credit and the
    closing result both in July. A trade opened in June and closed in July
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
                "roll_income": 0.0,
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

        for r in p.rolls:
            if r.rolled_on is None:
                continue
            e = entry(r.rolled_on)
            e["realized_pl"] += r.cash
            e["roll_income"] += r.cash
            e["rows"].append({"position": p, "tag": "rolled", "roll": r})

        if closed_on is not None:
            e = entry(closed_on)
            if closed_key != opened_key:
                e["rows"].append({"position": p, "tag": "closed"})
            e["closed_count"] += 1
            if p.realized_pl is not None:
                e["realized_pl"] += p.realized_pl
            # The win/loss verdict is on the trade's whole-life result, so it
            # matches what the trade's row says - even when some of that result
            # was banked as roll income in an earlier month.
            if p.realized_total is not None and p.realized_total > 0:
                e["wins"] += 1
            reason, lesson = _split_exit_reason(p.exit_reason or "")
            if reason.lower().startswith(_SOP_EXIT_PREFIXES):
                e["rules_followed"] += 1
            if lesson:
                e["lessons"].append(lesson)

    for e in months.values():
        e["realized_pl"] = round(e["realized_pl"], 2)
        e["roll_income"] = round(e["roll_income"], 2)
        e["bp_opened"] = round(e["bp_opened"], 2)
        if e["closed_count"]:
            e["win_rate"] = e["wins"] / e["closed_count"]
        e["lessons"].reverse()   # newest lesson first

    return sorted(months.values(), key=lambda e: e["month"], reverse=True)
