"""Turns the trade log (Google Sheet or local Excel) into positions the
"My trades" tab can track.

The log is an event log, keyed by Trade ID:
  - an "open" row when a trade is logged
  - zero or more "roll" rows when the income leg (the short call) is rolled
  - a "close" row when it is closed in the app
  - a "reopen" row when a close is taken back - recorded on the wrong trade, or
    on one she had not actually closed yet

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
    # Where this roll sat in the log when it was WRITTEN. Corrections address a
    # roll by this number, while `Position.rolls` is ordered by DATE - the two
    # can disagree, so the UI has to hand back the write index rather than the
    # position in the list it is showing, or it corrects the wrong fill.
    seq: int = 0
    new_strike: Optional[float] = None
    new_expiration: Optional[date] = None
    # What the NEW short call sold for on its own. This - not the net cash - is
    # what the 50%-of-credit profit target measures against from here on.
    new_credit: float = 0.0
    note: str = ""


class LegCloseEvent(BaseModel):
    """One leg taken off on its own, while the rest of the trade stays open.

    Her case, and the reason this exists: a credit spread she decides not to
    close and not to roll. She SELLS THE LONG PUT - banking whatever the
    protection is worth now that price has come down to it - and leaves the
    short put exactly where it is, so it can be assigned and hand her the
    shares she is happy to own.

    That is a different trade from the moment the fill goes through, and the
    app has to say so: the loss is no longer capped at the width of the spread,
    the cash the broker wants is the full strike, and the follow-up from here
    is a wheel (take the shares, then sell calls against them) rather than a
    50%-of-credit exit. Recording it as a CLOSE would have thrown the still-open
    short put away; recording it as a roll would have pretended the short leg
    moved. It is its own event.
    """

    closed_on: Optional[date] = None
    # Signed: positive when taking the leg off PAID her, which is the usual
    # case here - selling a long put back. Negative when she bought a short
    # leg back and left the rest of the position open.
    cash: float = 0.0
    strike: Optional[float] = None
    option_type: str = "put"      # the leg she took off
    side: str = "buy"             # "buy" = a long leg sold, "sell" = a short bought back
    # True when the point of the exercise is to let the remaining short put be
    # assigned. This is her INTENTION, and nothing else in the log records it -
    # the same fill could equally be someone taking the protection off to
    # squeeze the last dollar out of it, which wants the opposite advice.
    for_assignment: bool = False
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
    # The credit as it was on the day she opened, kept even after a roll
    # replaces `credit` with the new call's. The month report's "premium sold"
    # counts what she actually sold on each date, so it needs the original -
    # `credit` alone would report a July roll's credit as June's opening income.
    open_credit: float = 0.0
    # Signed net cash at open: positive when the position paid her to open
    # (credit spreads), negative when it cost her (PMCC, covered calls).
    # Legacy rows logged before the ledger existed default to credit.
    open_cash: float = 0.0
    # What the 100 real shares per contract cost (covered call models only).
    # Held separately because shares are not in the option chain but still have
    # to be valued at today's price when pricing the position.
    shares_cost: float = 0.0
    # Set when a short put was ASSIGNED and became shares. This is what keeps a
    # wheel one position: the put, the shares, and every call written against
    # them afterwards all live on the same trade, so the premium already
    # collected keeps counting towards what those shares really cost.
    assigned_on: Optional[date] = None
    assigned_strike: Optional[float] = None
    max_loss: float = 0.0
    buying_power: float = 0.0
    # The BP Effect copied straight off thinkorswim, when she typed it in.
    # None means "nobody told us", and bp_effect falls back to a derived value.
    bp_override: Optional[float] = None
    # "real" or "paper", stamped at log time. "" on rows written before the
    # two accounts were split, which fall back to the go-live date.
    account: str = ""
    short_delta: float = 0.0
    passed_sop: str = ""
    note: str = ""
    legs: list[Leg] = Field(default_factory=list)
    # The legs exactly as she opened them. `legs` tracks what she holds TODAY -
    # a roll rewrites the short call's strike in place - so day-one strikes
    # would otherwise be gone by the second roll.
    open_legs: list[Leg] = Field(default_factory=list)
    underlying_price_at_entry: Optional[float] = None
    rolls: list[RollEvent] = Field(default_factory=list)
    # Legs taken off one at a time while the trade stayed open - selling the
    # long put of a credit spread and leaving the short put to be assigned.
    leg_closes: list[LegCloseEvent] = Field(default_factory=list)

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

    def days_held(self, today: Optional[date] = None) -> Optional[int]:
        """Calendar days since it was opened. None on a row with no open date.

        The LEAPS long call's "took 20% in under two weeks" rule needs this;
        every other strategy here counts down to expiration instead.
        """
        if self.opened is None:
            return None
        return max((today or date.today()) - self.opened, timedelta(0)).days

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
    def short_put_collateral(self) -> float:
        """Cash that has to sit behind put(s) she SOLD: strike x 100 each.

        Not netted against the premium they paid - those are two different
        questions (what the trade cost, and what must be in the account), and
        netting them once at the wrong moment counts the credit twice.
        """
        per_unit = sum(leg.strike * leg.quantity for leg in self.legs
                       if leg.action == Action.SELL
                       and leg.option_type == OptionType.PUT)
        return per_unit * 100 * self.contracts

    @property
    def short_put_credit(self) -> float:
        """What the put(s) she SOLD paid her, from the fill prices on the legs.

        Only meaningful where those prices came from her own fill rather than a
        chain mid - the LEAPS long call's financing put, which the log records
        with a zero credit precisely because the money is a discount on the call
        and not income. The correction panel needs it to work back to what the
        call itself cost.
        """
        per_unit = sum(leg.premium * leg.quantity for leg in self.legs
                       if leg.action == Action.SELL
                       and leg.option_type == OptionType.PUT)
        return round(per_unit * 100 * self.contracts, 2)

    @property
    def capital_at_risk(self) -> float:
        """The dollars actually tied up - what a return % should divide by.

        On a LEAPS long call financed by a sold put, the net cash that left the
        account is a small number - her WFC call cost $2,115 and the puts paid
        $1,875 back, so $240 - while $22,500 has to stand behind those puts
        until they expire. Dividing a result by the $240 turns any ordinary move
        into a triple-digit percentage, so the collateral counts here too. On
        every other shape this is exactly what it always was.
        """
        if not self.is_debit:
            return self.buying_power
        if self.is_long_premium:
            return abs(self.open_cash) + self.short_put_collateral
        return abs(self.open_cash)

    @property
    def bp_effect(self) -> float:
        """What the BROKER holds against this position - thinkorswim's "BP
        Effect" column, which is what her monthly limit is measured in.

        Rita's ruling (2026-07-25): where the app and TOS disagree, TOS is
        right. Checked against her real account, the two cases split cleanly on
        whether real SHARES are involved:

        - A long option bought outright (the PMCC's LEAPS) is paid for in cash
          and the broker holds nothing against it. TOS reads 0.00 for her DIA
          and QQQ PMCCs, where the app was logging the full LEAPS cost.
        - Shares bought for a covered call ARE margined. TOS reads 18,312.75
          against her IWM shares, not zero, so "she paid for it" is not the
          test - "is it an option or is it stock" is.
        - A PUT SHE SOLD is held against her whatever else is in the position.
          A LEAPS financed by three sold puts is a debit trade with no shares,
          which the first rule alone would report as zero while TOS holds the
          whole strike. The stored buying power (strike x 100 less the credit,
          her cash-secured-put convention) stands in until she types the real
          figure, and errs LOW by that credit rather than reading nothing.

        Broker house margin on stock is not the Reg-T textbook (TOS held 60% of
        her share cost, not 50%), so the app does not invent a rate: it keeps
        the full share cost, which errs high on a guardrail, until she types the
        real BP Effect. Derived rather than trusted from the stored column, so
        rows logged under the old meaning correct themselves.
        """
        if self.bp_override is not None:
            return self.bp_override        # a real BP Effect she read off TOS
        if (self.is_debit and self.shares_cost <= 0
                and self.short_put_collateral <= 0):
            return 0.0
        return self.buying_power

    @property
    def roll_income(self) -> float:
        """Premium banked from every roll so far, counted the day it landed."""
        return round(sum(r.cash for r in self.rolls), 2)

    @property
    def leg_close_cash(self) -> float:
        """Cash banked by taking single legs off, counted on the day each one
        landed - same rule as a roll, because it is the same kind of money."""
        return round(sum(e.cash for e in self.leg_closes), 2)

    @property
    def banked_income(self) -> float:
        """Every dollar this position has already banked while still open:
        roll credits and legs sold off. This - not roll_income alone - is what
        the open ledger adds to open_cash, so a spread whose long put has been
        sold does not report that money as missing."""
        return round(self.roll_income + self.leg_close_cash, 2)

    @property
    def awaiting_assignment(self) -> bool:
        """She took the long leg off and is WAITING to be assigned on the short.

        Not guessed from the shape: she said so when she recorded the fill.
        The same leftover short put would otherwise be indistinguishable from a
        naked put she wants out of, and the two need opposite advice - "let it
        come to you" against "close it".

        False again the moment the shares actually arrive (assigned_strike is
        set) or the trade is closed: from there the wheel takes over.
        """
        if self.status != "open" or self.assigned_strike:
            return False
        if not any(e.for_assignment for e in self.leg_closes):
            return False
        return bool(self.short_puts)

    @property
    def short_puts(self) -> list[Leg]:
        """Every put she is short right now - what can be assigned to her."""
        return [leg for leg in self.legs
                if leg.action == Action.SELL and leg.option_type == OptionType.PUT]

    @property
    def has_long_put(self) -> bool:
        """True while something bought is still underneath the short put, i.e.
        the loss stops somewhere. Sell that leg and it does not."""
        return any(leg.action == Action.BUY and leg.option_type == OptionType.PUT
                   for leg in self.legs)

    @property
    def assignment_strike(self) -> Optional[float]:
        """The strike the shares would arrive at: the HIGHEST short put, which
        is the one that goes in the money first and the one she is really
        short once the protection under it is gone."""
        strikes = [leg.strike for leg in self.short_puts if leg.strike > 0]
        return max(strikes) if strikes else None

    @property
    def assignment_cash_needed(self) -> float:
        """What buying the shares would take: strike x 100 x contracts. The
        number that has to be sitting in the account, and the reason a spread
        that becomes a naked put is not a small change."""
        strike = self.assignment_strike
        if not strike:
            return 0.0
        return round(float(strike) * 100 * max(int(self.contracts or 1), 1), 2)

    @property
    def assignment_basis(self) -> Optional[float]:
        """What the shares would really cost her, per share, if assigned today:
        the strike less every dollar this trade has already banked - the credit
        she opened for, the long put she sold off, and any roll."""
        strike = self.assignment_strike
        if not strike:
            return None
        shares = 100 * max(int(self.contracts or 1), 1)
        collected = float(self.open_credit or 0.0) + self.banked_income
        return round(float(strike) - collected / shares, 2)

    @property
    def realized_total(self) -> Optional[float]:
        """Every dollar this position has actually banked, start to finish.

        On a closed position that is the whole story. On an open one it is the
        roll income collected so far, which is already hers.
        """
        if self.realized_pl is None:
            banked = self.banked_income
            return banked if (self.rolls or self.leg_closes) else None
        return round(self.realized_pl + self.banked_income, 2)

    @property
    def is_long_premium(self) -> bool:
        """A trade that is nothing but bought options - the LEAPS long call.

        Distinct from `is_uncovered`, which it would otherwise look exactly like.
        A PMCC between short calls is TEMPORARILY not earning and wants its next
        call written; this one has no short leg by design and never will. They
        need opposite advice, so the strategy key decides rather than the shape.
        """
        return self.strategy_key == "long_call_leaps"

    @property
    def is_uncovered(self) -> bool:
        """A PMCC or covered call with no short call written against it today.

        A normal part of the rhythm, not a mistake: her SOP takes the win at
        50% of the credit, and she may sit on the long side for a while before
        writing the next call. Nothing is earning while that is true, and the
        long side is exposed both ways, so the card says so instead of running
        exit rules against a call that isn't there.
        """
        return (self.is_debit and not self.is_long_premium and not any(
            leg.action == Action.SELL and leg.option_type == OptionType.CALL
            for leg in self.legs))

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


def _account_of(cell: Any, details: dict[str, Any]) -> str:
    """Which book a row belongs to: "real", "paper", or "" when nobody said.

    Anything else is treated as "nobody said" rather than trusted - a typo in
    that cell must not quietly move a trade between the two books.
    """
    for value in (cell, details.get("account")):
        text = str(value or "").strip().lower()
        if text in ("real", "paper"):
            return text
    return ""


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


def _apply_assignment(pos: Position, event: dict[str, Any]) -> None:
    """A short put became shares. Turn the position into what she now holds.

    Deliberately NOT counted as income or loss: buying the shares moves money
    from cash into stock, so it belongs in open_cash (capital deployed) and not
    in roll_income (premium banked). Getting that wrong would show a wheel as a
    catastrophic losing month every time she is assigned, on a strategy where
    assignment is the plan rather than the accident.
    """
    pos.assigned_on = event.get("assigned_on")
    pos.assigned_strike = event.get("strike")
    cash = float(event.get("cash") or 0.0)
    pos.open_cash = round(pos.open_cash + cash, 2)
    pos.shares_cost = round(abs(cash), 2)
    # The put is gone - it was exercised, not bought back. Until she writes a
    # call against the shares the position holds no option at all, which is
    # exactly the "uncovered" state the sell-a-call form already handles.
    pos.legs = [leg for leg in pos.legs
                if not (leg.action == Action.SELL
                        and leg.option_type == OptionType.PUT)]
    if not pos.legs:
        pos.expiration = None


def _match_leg(pos: Position, event: LegCloseEvent) -> Optional[Leg]:
    """The leg this event took off, out of the ones she still holds.

    Matched on side and type first, then on the nearest strike, so a fill typed
    a dollar out (or a leg whose strike was corrected later) still finds its
    leg instead of silently leaving the position with a leg it no longer has.
    """
    want_action = Action.SELL if event.side == "sell" else Action.BUY
    try:
        want_type = OptionType(event.option_type)
    except ValueError:
        want_type = OptionType.PUT
    legs = [leg for leg in pos.legs
            if leg.action == want_action and leg.option_type == want_type]
    if not legs:
        return None
    if event.strike is None:
        return legs[0]
    return min(legs, key=lambda leg: abs(leg.strike - float(event.strike)))


def _apply_leg_close(pos: Position, event: LegCloseEvent) -> None:
    """One leg comes off; the rest of the trade carries on.

    Three things change, and all three matter more than the leg itself:

      the ledger   the fill banked cash on its own date, exactly like a roll,
                   so the month it landed in is the month it counts
      the credit   she has now collected the opening credit AND what the long
                   put sold for, so "what have I kept" measures against both.
                   Without this a spread whose long put paid $1,400 reads as
                   deeply underwater the moment the short leg is priced.
      the risk     with nothing bought underneath it, a short put's loss no
                   longer stops at the width of the spread. Max loss becomes
                   the whole strike less what she has collected, and the broker
                   wants the cash-secured amount, not the width.
    """
    pos.leg_closes.append(event)

    leg = _match_leg(pos, event)
    if leg is not None:
        pos.legs.remove(leg)

    if event.cash > 0:
        pos.credit = round(pos.credit + event.cash, 2)

    if pos.short_puts and not pos.has_long_put:
        cash_needed = pos.assignment_cash_needed
        if cash_needed:
            # Errs high on a position that still has a call side open (the put
            # side is now the bigger number by far), which is the right way for
            # a guardrail to be wrong. Her typed-in TOS BP Effect still wins:
            # bp_effect reads bp_override before this.
            pos.buying_power = cash_needed
            pos.max_loss = round(
                cash_needed - float(pos.open_credit or 0.0) - pos.banked_income, 2)

    if not pos.legs:
        pos.expiration = None


def _legs_from_json(raw: Any) -> list[Leg]:
    """Legs out of an edit's changes block - same shape the open row stores."""
    return _parse_details(json.dumps({"legs": raw}))[1] if raw else []


def _apply_edit(pos: Optional[Position],
                rolls: list[tuple[str, int, RollEvent]],
                trade_id: str, edit: dict[str, Any]) -> None:
    """Apply one correction to the trade it names.

    Only keys actually present are touched. Absent means "she did not change
    this", which is not the same as "set it to blank" - a form that submitted
    every field would otherwise wipe whatever it had no input for.

    A roll is addressed by its WRITE order (`roll_index`), not by date. Rolls
    are applied in date order further down, but correcting a roll's date would
    then renumber the very list the index is pointing into.
    """
    changes = edit.get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        return

    if edit.get("target") == "roll":
        index = edit.get("roll_index")
        if index is None:
            return
        for tid, seq, roll in rolls:
            if tid != trade_id or seq != int(index):
                continue
            if "credit" in changes:
                roll.new_credit = _to_float(changes["credit"]) or 0.0
            if "open_cash" in changes:          # the cash the roll banked
                roll.cash = _to_float(changes["open_cash"]) or 0.0
            if "strikes" in changes:
                roll.new_strike = _to_float(changes["strikes"])
            if "expiration" in changes:
                roll.new_expiration = _to_date(changes["expiration"])
            if "opened_on" in changes:
                roll.rolled_on = _to_date(changes["opened_on"])
            if "note" in changes:
                roll.note = str(changes["note"])
            return
        return

    if pos is None:
        return
    if "opened_on" in changes:
        pos.opened = _to_date(changes["opened_on"]) or pos.opened
    if "expiration" in changes:
        pos.expiration = _to_date(changes["expiration"]) or pos.expiration
    if "contracts" in changes:
        pos.contracts = int(_to_float(changes["contracts"]) or pos.contracts)
    if "credit" in changes:
        credit = _to_float(changes["credit"])
        if credit is not None:
            pos.credit = pos.open_credit = credit
    if "open_cash" in changes:
        cash = _to_float(changes["open_cash"])
        if cash is not None:
            pos.open_cash = cash
    if "max_loss" in changes:
        pos.max_loss = _to_float(changes["max_loss"]) or 0.0
    if "buying_power" in changes:
        pos.buying_power = _to_float(changes["buying_power"]) or 0.0
    if "account" in changes:
        pos.account = _account_of(changes["account"], {})
    if "note" in changes:
        pos.note = str(changes["note"])
    if "legs" in changes:
        legs = _legs_from_json(changes["legs"])
        if legs:
            # open_legs moves with it: it is the day-one snapshot the story
            # panel reads, and a corrected day one is still day one.
            pos.legs = legs
            pos.open_legs = [leg.model_copy(deep=True) for leg in legs]


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
    # (trade_id, sequence-within-that-trade, event). The sequence is the order
    # the rolls were WRITTEN, which is what edits address - see _apply_edit.
    rolls: list[tuple[str, int, RollEvent]] = []
    roll_seq: dict[str, int] = {}
    assigns: list[tuple[str, dict[str, Any]]] = []
    leg_closes: list[tuple[str, LegCloseEvent]] = []
    # Closes AND reopens, in the order they were written. A close ends the
    # trade, a reopen after it says that close never happened, and a close
    # after that ends it again - the last word wins, exactly as a corrected
    # close already supersedes the figure before it.
    endings: list[dict[str, Any]] = []
    edits: list[tuple[str, dict[str, Any]]] = []

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
            endings.append({
                "kind": "close",
                "trade_id": trade_id,
                "closed_on": _to_date(_get(row, idx, "Date", 0)),
                "exit_cost": exit_cost,
                "close_cash": close_cash,
                "realized_pl": _to_float(_get(row, idx, "Realized P&L $", 16)),
                "reason": str(_get(row, idx, "Notes", 11) or ""),
            })
            continue

        if event == "reopen" and trade_id:
            endings.append({"kind": "reopen", "trade_id": trade_id})
            continue

        if event == "assign" and trade_id:
            assigns.append((trade_id, {
                "assigned_on": _to_date(_get(row, idx, "Date", 0)),
                "strike": _to_float(_get(row, idx, "Legs (strikes)", 3)),
                "cash": _to_float(_get(row, idx, "Realized P&L $", 16)) or 0.0,
            }))
            continue

        if event == "legclose" and trade_id:
            data, _ = _parse_details(_get(row, idx, "Details JSON", 17))
            leg_closes.append((trade_id, LegCloseEvent(
                closed_on=_to_date(_get(row, idx, "Date", 0)),
                cash=_to_float(_get(row, idx, "Realized P&L $", 16)) or 0.0,
                strike=_to_float(_get(row, idx, "Legs (strikes)", 3)),
                option_type=str(data.get("type") or "put"),
                side=str(data.get("side") or "buy"),
                for_assignment=bool(data.get("for_assignment")),
                note=str(_get(row, idx, "Notes", 11) or ""),
            )))
            continue

        if event == "edit" and trade_id:
            data, _ = _parse_details(_get(row, idx, "Details JSON", 17))
            edits.append((trade_id, data))
            continue

        if event == "roll" and trade_id:
            roll_seq[trade_id] = roll_seq.get(trade_id, -1) + 1
            rolls.append((trade_id, roll_seq[trade_id], RollEvent(
                rolled_on=_to_date(_get(row, idx, "Date", 0)),
                cash=_to_float(_get(row, idx, "Realized P&L $", 16)) or 0.0,
                seq=roll_seq[trade_id],
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
            open_credit=credit,
            open_cash=open_cash,
            shares_cost=_to_float(data.get("shares_cost")) or 0.0,
            max_loss=_to_float(_get(row, idx, "Max Loss $", 8)) or 0.0,
            buying_power=_to_float(_get(row, idx, "Buying Power $", 9)) or 0.0,
            # Stored in Details JSON so honouring TOS needed no new sheet column
            # (a schema change means she has to redeploy the Apps Script).
            bp_override=_to_float(data.get("bp_effect")),
            # The visible Account column is the source of truth. Details JSON
            # is read as a fallback only for rows written in the short window
            # when the stamp lived there instead.
            account=_account_of(_get(row, idx, "Account", 18), data),
            short_delta=_to_float(_get(row, idx, "Short Delta", 4)) or 0.0,
            passed_sop=str(_get(row, idx, "Passed SOP", 10) or ""),
            note=str(_get(row, idx, "Notes", 11) or ""),
            legs=legs,
            # A snapshot, because `legs` is mutated in place by every roll: the
            # short call's strike on a rolled PMCC is wherever it ended up, not
            # what she sold on day one. The story of the trade needs the day-one
            # strikes, so they are kept before anything moves them.
            open_legs=[leg.model_copy(deep=True) for leg in legs],
            underlying_price_at_entry=_to_float(data.get("underlying_price")),
            status="open" if trade_id else "legacy",
        )
        ordered.append(pos)
        if trade_id:
            opens[trade_id] = pos

    # Corrections come FIRST, before anything is folded in, because they fix
    # what was typed on the day - the opening details, or a roll's own numbers.
    # Applying them after the rolls would mean correcting a strike that a later
    # roll had already overwritten in place.
    for trade_id, edit in edits:
        _apply_edit(opens.get(trade_id), rolls, trade_id, edit)

    # Legs taken off on their own come before assignment: selling the long put
    # is what LEAVES the short one able to assign her, so the position has to
    # be down to that single short put before the shares arrive.
    for trade_id, lc in sorted(leg_closes,
                               key=lambda r: r[1].closed_on or date.min):
        pos = opens.get(trade_id)
        if pos is not None:
            _apply_leg_close(pos, lc)

    # Assignment next: it turns the put into shares, and every roll after it
    # is a CALL written against those shares.
    for trade_id, a in sorted(assigns,
                              key=lambda r: r[1]["assigned_on"] or date.min):
        pos = opens.get(trade_id)
        if pos is not None:
            _apply_assignment(pos, a)

    # Rolls in the order they happened, so the last one wins on strike/date.
    for trade_id, _seq, roll in sorted(
            rolls, key=lambda r: r[2].rolled_on or date.min):
        pos = opens.get(trade_id)
        if pos is not None:
            _apply_roll(pos, roll)

    for ev in endings:
        pos = opens.get(ev["trade_id"])
        if pos is None:
            continue
        if ev["kind"] == "reopen":
            # She never closed it. Back to exactly how it stood - same legs,
            # same rolls, same Trade ID - with the recorded result cleared, so
            # money she never banked stops counting in the month.
            pos.status = "open"
            pos.closed_on = None
            pos.exit_cost = None
            pos.close_cash = None
            pos.realized_pl = None
            pos.exit_reason = ""
            continue
        pos.status = "closed"
        pos.closed_on = ev["closed_on"]
        pos.exit_cost = ev["exit_cost"]
        pos.close_cash = ev["close_cash"]
        pos.realized_pl = ev["realized_pl"]
        pos.exit_reason = ev["reason"]

    return ordered


def open_positions(positions: list[Position]) -> list[Position]:
    return [p for p in positions if p.status == "open"]


def closed_positions(positions: list[Position]) -> list[Position]:
    return [p for p in positions if p.status == "closed"]


def bp_in_use(positions: list[Position]) -> float:
    """Buying power tied up by every open position, together."""
    return sum(p.buying_power for p in open_positions(positions))


def bp_committed_this_month(positions: list[Position], today: date | None = None) -> float:
    """Buying power committed by EVERY trade opened this calendar month, whether
    it is still open or already closed. The figure the monthly limit is measured
    against.

    Rita's ruling (2026-07-25): her SOP's "under $50,000 per month" is
    CUMULATIVE deployment through the month, not how much happens to be tied up
    at one moment. So closing a trade does not hand the buying power back for
    the rest of the month - opening it spent that part of the budget.

    Two consequences worth knowing. Trades opened in an EARLIER month never
    count here even if they are still open: they were that month's deployment.
    And this is a gross sum of each trade's max loss / collateral, not real
    broker buying power - your broker nets positions against each other, so this
    reads higher than the number your platform shows. It is a budget guardrail,
    not a margin figure.
    """
    ref = today or date.today()
    return sum(
        p.bp_effect for p in positions
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
        "open_pl": round(position.open_cash + position.banked_income + value, 2),
    }
    if shares_pl is not None:
        # Taking the shares' cost back out of open_cash leaves what the OPTIONS
        # alone cost to put on, so options_pl + shares_pl == open_pl.
        options_cash = position.open_cash + position.shares_cost
        out["options_pl"] = round(options_cash + position.banked_income
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
    return round(position.open_cash + position.banked_income
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

    Three kinds: a "close" banks the position's closing result, a "roll" banks
    the credit collected that day, and a "legclose" banks what one leg sold for
    when she took it off and left the rest of the trade running. The last two
    count on their own date and not at the close, so income from a covered call
    rolled monthly for a year lands in each of those twelve months - which is
    how her monthly goal is measured.
    """
    events: list[dict[str, Any]] = []
    for p in positions:
        for lc in p.leg_closes:
            if lc.closed_on is not None and lc.cash:
                events.append({"date": lc.closed_on, "amount": lc.cash,
                               "kind": "legclose", "position": p})
        for r in p.rolls:
            if r.rolled_on is not None and r.cash:
                events.append({"date": r.rolled_on, "amount": r.cash,
                               "kind": "roll", "position": p})
        if (p.status == "closed" and p.realized_pl is not None
                and p.closed_on is not None):
            events.append({"date": p.closed_on, "amount": p.realized_pl,
                           "kind": "close", "position": p})
    return sorted(events, key=lambda e: e["date"])


def story(position: Position) -> list[dict[str, Any]]:
    """One position told as the sequence it actually was, oldest first.

    A closed trade in the log is a single number, and on anything she rolled
    that number hides the entire trade. A PMCC rolled weekly for two months is
    a dozen fills reported as one figure, and nothing on the screen explained
    where that figure came from - so nothing on the screen could show a fill
    that had never been logged either.

    Every row is one cash movement. `running` is the sum of every `cash` up to
    and including that row, which means the LAST row of a closed trade equals
    realized_total by construction - the story and the headline can never
    disagree, because the headline is the end of the story.

    An open position simply stops at its last roll; `running` there is money in
    minus money out so far, not a result.
    """
    steps: list[dict[str, Any]] = []

    steps.append({
        "on": position.opened,
        "what": "You opened the trade",
        "detail": _open_detail(position),
        "cash": round(position.open_cash, 2),
        "kind": "open",
    })

    for lc in position.leg_closes:
        steps.append({
            "on": lc.closed_on,
            "what": ("You sold the long leg" if lc.side == "buy"
                     else "You bought one short leg back"),
            "detail": lc.note or _leg_close_detail(lc),
            "cash": round(lc.cash, 2),
            "kind": "legclose",
        })

    if position.assigned_on is not None:
        # No cash of its own: the shares were paid for at the strike, which the
        # close already accounts for. It is here because a wheel makes no sense
        # without the day the put turned into stock.
        steps.append({
            "on": position.assigned_on,
            "what": "Assigned",
            "detail": (f"The {position.assigned_strike:g} put was assigned - "
                       f"you own the shares from here"
                       if position.assigned_strike else "Assigned into shares"),
            "cash": 0.0,
            "kind": "assign",
        })

    for r in position.rolls:
        steps.append({
            "on": r.rolled_on,
            "what": ("You sold a call" if r.new_strike is not None and r.cash > 0
                     else "You bought the call back" if r.new_strike is None
                     else "You rolled the call"),
            "detail": r.note or _roll_detail(r),
            "cash": round(r.cash, 2),
            "kind": "roll",
        })

    if position.status == "closed":
        cash = position.close_cash
        if cash is None:
            cash = -(position.exit_cost or 0.0)
        steps.append({
            "on": position.closed_on,
            "what": "You closed the trade",
            "detail": position.exit_reason or "Closed",
            "cash": round(float(cash), 2),
            "kind": "close",
        })

    running = 0.0
    for s in steps:
        running += s["cash"]
        s["running"] = round(running, 2)
    return steps


def _open_detail(position: Position) -> str:
    """What she actually bought and sold on day one, in strikes."""
    legs = position.open_legs or position.legs
    bought = [leg for leg in legs if leg.action == Action.BUY]
    sold = [leg for leg in legs if leg.action == Action.SELL]

    def names(group) -> str:
        return "the " + _and(f"{leg.strike:g} {leg.option_type.value}"
                             for leg in group)

    parts = []
    if position.shares_cost > 0:
        parts.append(f"Bought {position.contracts * 100} shares")
    if bought:
        parts.append(("bought " if parts else "Bought ") + names(bought))
    if sold:
        parts.append(("sold " if parts else "Sold ") + names(sold))
    # Comma between the buying and the selling, "and" only inside each side -
    # "bought the 705 put and the 800 call and sold the 715 put" reads as one
    # runaway sentence with no seam where the trade actually splits.
    return ", ".join(parts) if parts else (position.note or "Opened the trade")


def _and(items) -> str:
    """"a, b and c" - the way she would say it, not "a, b, c"."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _leg_close_detail(event: LegCloseEvent) -> str:
    """What came off, and what she was left holding."""
    what = (f"the {event.strike:g} {event.option_type}"
            if event.strike else f"the long {event.option_type}")
    if event.side == "buy":
        text = f"Sold {what} back"
    else:
        text = f"Bought {what} back"
    if event.for_assignment:
        text += " - the short put is on its own now, left to be assigned"
    return text


def _roll_detail(roll: RollEvent) -> str:
    if roll.new_strike is None:
        return "Bought the short call back - nothing written in its place"
    when = f" expiring {roll.new_expiration:%d/%m/%Y}" if roll.new_expiration else ""
    return f"Short call is now the {roll.new_strike:g}{when}"


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


def quality(positions: list[Position],
            today: Optional[date] = None) -> dict[str, Any]:
    """The measures that judge the PROCESS rather than the month.

    performance() answers "how much". This answers "how well, and how
    repeatably" - the numbers that say whether a good month was skill or luck,
    and the ones a trading dashboard is expected to carry.

    Two deliberate refusals to make a number up:

    * profit_factor is None when nothing has lost yet. A beginner's first
      months are routinely all winners, and dividing by zero losses gives
      infinity, which is not a number to put on a card.
    * confidence says out loud how much of this is noise. Under five closed
      trades a profit factor is an anecdote, and the dashboard says so rather
      than printing 3.4 in bold.

    Drawdown is measured on the same running total the equity chart draws -
    cash_events() - so the dip on the card and the dip in the picture are the
    same event. The peak starts at zero, so the first losing trade of an
    account's life is a real drawdown rather than a divide-by-nothing.

    One book only: the caller passes the scoped list, exactly as
    performance() does.
    """
    today = today or date.today()
    closed = [p for p in closed_positions(positions) if p.realized_pl is not None]
    events = cash_events(positions)

    results = [p.realized_total for p in closed if p.realized_total is not None]
    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    banked = sum(e["amount"] for e in events)

    # The equity curve, and how far under its own best day it has been.
    peak = 0.0
    running = 0.0
    max_dd = 0.0
    max_dd_peak = 0.0
    for e in events:
        running += e["amount"]
        if running > peak:
            peak = running
        dip = running - peak
        if dip < max_dd:
            max_dd, max_dd_peak = dip, peak

    # Wins and losses in the order they settled, so "three in a row" means the
    # last three, not three somewhere in the history.
    ordered = sorted((p for p in closed if p.closed_on is not None),
                     key=lambda p: p.closed_on)
    streak = 0
    for p in reversed(ordered):
        won = (p.realized_total or 0.0) > 0
        if streak == 0:
            streak = 1 if won else -1
        elif won and streak > 0:
            streak += 1
        elif not won and streak < 0:
            streak -= 1
        else:
            break

    n = len(closed)
    avg_win = (gross_win / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None

    return {
        "closed_count": n,
        "trade_count": len(positions),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": (banked / n) if n else None,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss))
                        if avg_win is not None and avg_loss else None,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": (abs(max_dd) / max_dd_peak) if max_dd_peak > 0 else None,
        "current_drawdown": round(min(running - peak, 0.0), 2),
        "streak": streak,
        "confidence": "thin" if n < 5 else "building" if n < 20 else "ok",
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
            e["bp_opened"] += p.bp_effect
            if p.status == "open":
                e["still_open"] += 1
            tag = "both" if (closed_key is not None and closed_key == opened_key) \
                else "opened"
            e["rows"].append({"position": p, "tag": tag})

        for lc in p.leg_closes:
            if lc.closed_on is None or not lc.cash:
                continue
            e = entry(lc.closed_on)
            # Banked in realized_pl but NOT in roll_income: this is not a roll,
            # and the month report's "income from rolls" line would be wrong to
            # claim it. cash_events counts it under its own kind for the same
            # reason.
            e["realized_pl"] += lc.cash
            e["rows"].append({"position": p, "tag": "legclose", "leg_close": lc})

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
