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
