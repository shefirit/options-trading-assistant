"""Return on investment for her trades, and the same return stretched to a year.

Rita's ask (2026-10-12): ROI and annualized ROI in the tracking, especially the
Journal. Her rulings on the two choices that change every number:

  BASE - "money at risk", not the broker's buying power. What the trade could
         actually lose, the same yardstick on every trade:
           credit trades  max_loss - the width or the strike, less everything
                          collected (rolls included; positions._reprice_risk
                          keeps it current).
           debit trades   the cash laid out - the shares or the LEAPS, less the
                          call sold against them (Position.capital_at_risk). Not
                          max_loss: on a collar that is the small capped loss,
                          and dividing by it would turn an ordinary month into
                          a four-digit percentage.
  YEAR - simple, not compounded: ROI x 365 / days held. The way brokers quote
         it, and checkable by hand. A 2% weekly call is ~104% a year this way;
         compounded it would read ~181%, a number she could never bank.

Pure arithmetic on Positions - no network, no Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional


@dataclass(frozen=True)
class Roi:
    """One trade's return."""

    result: float            # dollars made (+) or lost (-), whole trade
    base: float              # money at risk it was measured on
    days: int                # days held, at least 1
    pct: float               # result / base, as a percentage
    annual_pct: float        # pct x 365 / days


def money_at_risk(position) -> Optional[float]:
    """What this trade put at risk, in dollars - the ROI denominator.

    None when the log never recorded it (rows from before the tracker), rather
    than a guess: a made-up base is a made-up return.
    """
    if position.is_debit:
        base = float(position.capital_at_risk or 0.0)
    else:
        base = float(position.max_loss or 0.0)
    return round(base, 2) if base > 0 else None


def days_held(position, today: Optional[date] = None) -> Optional[int]:
    """Open to close - or to today, while it is still running. A trade opened
    and closed on the same day counts as one day, so the year figure stays a
    real number instead of a division by zero."""
    if position.opened is None:
        return None
    end = position.closed_on or today or date.today()
    return max((end - position.opened).days, 1)


def annualize(pct: float, days: int) -> float:
    """Simple annualizing - her ruling. ROI x 365 / days."""
    return pct * 365.0 / max(int(days), 1)


def trade_roi(position) -> Optional[Roi]:
    """ROI of a CLOSED trade. None while it is open (its result is not known
    yet) or when there is no base to divide by."""
    if position.status != "closed":
        return None
    result = position.realized_total
    base = money_at_risk(position)
    days = days_held(position)
    if result is None or base is None or days is None:
        return None
    pct = result / base * 100.0
    return Roi(result=round(result, 2), base=base, days=days,
               pct=round(pct, 2), annual_pct=round(annualize(pct, days), 1))


def open_roi(position, pl_now: Optional[float],
             today: Optional[date] = None) -> Optional[Roi]:
    """ROI SO FAR on an open trade: what it would make if closed right now,
    over the same money at risk the Journal uses once it closes.

    pl_now is the open-trades P&L - the whole trade, banked rolls included and,
    on a debit trade, the shares or LEAPS at today's price. None when the
    trade could not be priced, and then there is no ROI to show either.
    """
    if position.status != "open" or pl_now is None:
        return None
    base = money_at_risk(position)
    days = days_held(position, today)
    if base is None or days is None:
        return None
    pct = pl_now / base * 100.0
    return Roi(result=round(pl_now, 2), base=base, days=days,
               pct=round(pct, 2), annual_pct=round(annualize(pct, days), 1))


@dataclass(frozen=True)
class CoveredCallRoi:
    """The three returns a covered call is judged on, each with its own clock.

    All on the Journal's base - the cash she has in the trade - so they line
    up with the ROI it gets once it closes.
    """

    base: float
    premium: float                     # every call's premium, net of buy-backs
    premium_days: int                  # since the shares were bought
    premium_pct: float
    premium_annual_pct: float
    call_credit: Optional[float] = None    # the call written right now
    call_days: Optional[int] = None        # from its sale to its expiration
    call_pct: Optional[float] = None
    call_annual_pct: Optional[float] = None
    away_result: Optional[float] = None    # whole trade, if called at the strike
    away_days: Optional[int] = None        # from buying the shares to that date
    away_pct: Optional[float] = None
    away_annual_pct: Optional[float] = None
    # Where the third card is measured on something other than `base` - the
    # covered call models' "if called away", which leaves the put side out.
    away_base: Optional[float] = None


def _call_sold_on(position) -> Optional[date]:
    """When the call she is short now was written: the last roll that wrote
    one, or the open itself if the first call went on with the shares."""
    for r in reversed(position.rolls):
        if r.new_strike is not None and r.kind.value == "call":
            return r.rolled_on
    return position.opened


def covered_call_roi(position, state,
                     today: Optional[date] = None) -> Optional[CoveredCallRoi]:
    """This week's call, the premium so far, and being called away - as ROI.

    `state` is the wheel.WheelState for the position: it already holds the
    premium collected and what being called away would pay, so the dollars
    here are the same ones the card prints above them.
    """
    base = money_at_risk(position)
    held = days_held(position, today)
    if state is None or base is None or held is None:
        return None
    prem_pct = state.premium_collected / base * 100.0
    out = dict(base=base, premium=state.premium_collected, premium_days=held,
               premium_pct=round(prem_pct, 2),
               premium_annual_pct=round(annualize(prem_pct, held), 1))

    exp = position.expiration
    if state.call_strike and exp is not None:
        credit = float(position.credit or 0.0)
        sold = _call_sold_on(position)
        if credit > 0 and sold is not None:
            days = max((exp - sold).days, 1)
            pct = credit / base * 100.0
            out.update(call_credit=round(credit, 2), call_days=days,
                       call_pct=round(pct, 2),
                       call_annual_pct=round(annualize(pct, days), 1))
        if state.called_away_profit is not None and position.opened is not None:
            days = max((exp - position.opened).days, 1)
            pct = state.called_away_profit / base * 100.0
            out.update(away_result=state.called_away_profit, away_days=days,
                       away_pct=round(pct, 2),
                       away_annual_pct=round(annualize(pct, days), 1))
    return CoveredCallRoi(**out)


def _strike_floor(position) -> Optional[float]:
    """What a PMCC is worth AT LEAST, in dollars, if the stock sits at or above
    the short strike when that call expires: the gap between the two strikes.

    Above the short strike every dollar the LEAPS gains the short call loses,
    and the LEAPS is always worth at least its intrinsic value - so the pair is
    worth (short - long) x 100 or more. More, because the LEAPS still has
    months of time value left; this deliberately leaves that out rather than
    guess at it.

    None where that floor does not hold: more calls sold than LEAPS owned (the
    extra call is naked and its loss has no ceiling), or puts sold against the
    LEAPS (their loss is not in the sum).
    """
    from src.engine.models import Action, OptionType

    longs = [l for l in position.legs
             if l.action == Action.BUY and l.option_type == OptionType.CALL]
    shorts = [l for l in position.legs
              if l.action == Action.SELL and l.option_type == OptionType.CALL]
    if not longs or not shorts or position.short_puts:
        return None
    long_qty = sum(max(int(l.quantity or 1), 1) for l in longs)
    short_qty = sum(max(int(l.quantity or 1), 1) for l in shorts)
    if short_qty > long_qty:
        return None
    long_k = max(l.strike for l in longs)       # the worst one, if several
    short_k = min(l.strike for l in shorts)
    contracts = max(int(position.contracts or 1), 1)
    return (short_k - long_k) * 100 * short_qty * contracts


def _call_premium(position) -> float:
    """Premium from the CALLS alone: the one sold at the open plus every call
    roll's net cash since.

    The income side of every call-writing trade that is not a share-assigned
    wheel. What moves on the put side is something else: a covered call
    model's protection being bought, rolled or sold off, or a LEAPS call's
    financing puts - a discount on the call, not income (her ruling,
    2026-08-13, which is why that trade logs a zero credit at the open).
    """
    from src.engine.models import OptionType
    opened_with = float(position.open_credit or 0.0)
    rolls = sum(r.cash for r in position.rolls if r.kind == OptionType.CALL)
    return round(opened_with + rolls, 2)


def _call_cards(position, base: float, premium: float, held: int) -> dict:
    """The first two cards - this call, and the premium so far - which every
    call-writing trade shares."""
    prem_pct = premium / base * 100.0
    out = dict(base=base, premium=round(premium, 2), premium_days=held,
               premium_pct=round(prem_pct, 2),
               premium_annual_pct=round(annualize(prem_pct, held), 1))
    exp = position.expiration
    credit = float(position.credit or 0.0)
    sold = _call_sold_on(position)
    if position.has_short_call and exp is not None and credit > 0 and sold:
        days = max((exp - sold).days, 1)
        pct = credit / base * 100.0
        out.update(call_credit=round(credit, 2), call_days=days,
                   call_pct=round(pct, 2),
                   call_annual_pct=round(annualize(pct, days), 1))
    return out


def _with_floor(out: dict, position, base: float, value_floor) -> dict:
    """Add the third card: the whole trade if closed at this call's
    expiration, given the least the remaining legs are worth then."""
    exp = position.expiration
    if value_floor is None or exp is None or position.opened is None:
        return out
    result = value_floor + float(position.open_cash or 0.0) + position.banked_income
    days = max((exp - position.opened).days, 1)
    pct = result / base * 100.0
    out.update(away_result=round(result, 2), away_days=days,
               away_pct=round(pct, 2),
               away_annual_pct=round(annualize(pct, days), 1))
    return out


def covered_model_roi(position,
                      today: Optional[date] = None) -> Optional[CoveredCallRoi]:
    """The three cards on a covered call model (1, 2 or 3): shares, a put
    side for protection, and a monthly call.

    Premium is the calls only - see _call_premium.

    "If called away" is the classic covered-call figure: the shares sold at
    the strike plus the call premium, over what the SHARES cost, with the put
    side left out. Counting the puts at zero instead (a true floor) read -$500
    on an ordinary Model 2 a month in, while its two-year put spread was still
    worth most of the $1,200 paid - accurate, and no use to her. The puts are
    still hers after the shares go; what they sell for then is the rest of it.
    """
    from src.engine.models import Action, OptionType

    base = money_at_risk(position)
    held = days_held(position, today)
    if base is None or held is None:
        return None
    premium = _call_premium(position)
    out = _call_cards(position, base, premium, held)

    shorts = [l for l in position.legs
              if l.action == Action.SELL and l.option_type == OptionType.CALL]
    shares_cost = float(position.shares_cost or 0.0)
    contracts = max(int(position.contracts or 1), 1)
    exp = position.expiration
    one_per_100 = sum(max(int(l.quantity or 1), 1) for l in shorts) <= 1
    if shorts and one_per_100 and shares_cost > 0 and exp and position.opened:
        strike = min(l.strike for l in shorts)
        result = strike * 100 * contracts - shares_cost + premium
        days = max((exp - position.opened).days, 1)
        pct = result / shares_cost * 100.0
        out.update(away_result=round(result, 2), away_days=days,
                   away_pct=round(pct, 2), away_base=round(shares_cost, 2),
                   away_annual_pct=round(annualize(pct, days), 1))
    return CoveredCallRoi(**out)


def pmcc_roi(position, today: Optional[date] = None) -> Optional[CoveredCallRoi]:
    """The PMCC's version of the covered-call cards: this call, the premium so
    far, and - in place of being called away, which a PMCC cannot be - what the
    trade makes at LEAST if the stock finishes at or above the short strike.

    Same base as everything else here: the cash in the trade (the LEAPS less
    the call sold with it). Premium is the calls only, so a LEAPS call she has
    started writing calls against does not count its financing puts.
    """
    base = money_at_risk(position)
    held = days_held(position, today)
    if base is None or held is None:
        return None
    out = _call_cards(position, base, _call_premium(position), held)
    out = _with_floor(out, position, base, _strike_floor(position))
    return CoveredCallRoi(**out)


@dataclass(frozen=True)
class BookRoi:
    """Several closed trades taken together."""

    trades: int
    result: float
    base: float              # sum of each trade's money at risk
    pct: float               # result / base
    annual_pct: float        # weighted by how long each base was tied up
    avg_days: float


def book_roi(positions: Iterable) -> Optional[BookRoi]:
    """The return on a set of closed trades, as one number.

    The year figure is NOT an average of the per-trade ones. Averaging would let
    a $40 two-day trade at 900% a year outvote a $900 sixty-day trade at 30%.
    It is total result over total "dollar-days" at risk, times 365 - every
    dollar counts for as long as it was actually tied up.
    """
    rois = [r for r in (trade_roi(p) for p in positions) if r is not None]
    if not rois:
        return None
    result = sum(r.result for r in rois)
    base = sum(r.base for r in rois)
    dollar_days = sum(r.base * r.days for r in rois)
    return BookRoi(
        trades=len(rois),
        result=round(result, 2),
        base=round(base, 2),
        pct=round(result / base * 100.0, 2),
        annual_pct=round(result / dollar_days * 365.0 * 100.0, 1),
        avg_days=round(sum(r.days for r in rois) / len(rois), 1),
    )
