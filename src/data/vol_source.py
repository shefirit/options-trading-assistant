"""Where the IV Rank comes from, best source first.

IV Rank is the one input the candidate check cannot fake its way around, and no
free feed carries a year of implied volatility per symbol. So there is an order,
and the screen always names which rung answered:

  1. A Barchart export she downloaded    - real IV Rank, any symbol
  2. A rank she typed in herself         - real, one symbol, thirty seconds
  3. A CBOE volatility index             - real, free, but only for the handful
                                           of underlyings that have one
  4. Realized volatility, ranked         - a PROXY, and labelled as one

Rung four deserves its warning. Ranking realized volatility tells you how much
the underlying has been moving against its own year. That is a genuinely useful
number, and it is not IV Rank: it measures what the stock DID, where IV Rank
measures what options are CHARGING. The two part company exactly when it matters
most - before an earnings report, implied volatility climbs while realized
volatility has not moved yet.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from src.data.barchart import VolSnapshot
from src.engine.candidate import rank_in_range, realized_vol_series

# Underlyings with a CBOE volatility index of their own. These give a REAL
# IV Rank for free, because the index IS the market's implied volatility and
# ranking a year of it is the same arithmetic Barchart runs.
VOL_INDEX: dict[str, str] = {
    "SPX": "^VIX", "SPY": "^VIX", "XSP": "^VIX", "ES": "^VIX",
    "NDX": "^VXN", "QQQ": "^VXN",
    "RUT": "^RVX", "IWM": "^RVX",
    "DJX": "^VXD", "DIA": "^VXD",
}

_INDEX_NAME = {"^VIX": "VIX", "^VXN": "VXN", "^RVX": "RVX", "^VXD": "VXD"}


class VolRead(BaseModel):
    iv_rank: Optional[float] = None
    iv: Optional[float] = None           # implied volatility now, percent
    hv: Optional[float] = None           # realized volatility, percent
    source: str = ""                     # short label for the screen
    is_proxy: bool = False
    note: str = ""                       # the caveat, when there is one

    @property
    def known(self) -> bool:
        return self.iv_rank is not None


def vol_index_for(symbol: str) -> Optional[str]:
    """The CBOE volatility index that tracks this underlying, if one exists."""
    return VOL_INDEX.get((symbol or "").upper().lstrip("$^"))


def resolve(
    symbol: str,
    *,
    barchart_row: Optional[VolSnapshot] = None,
    manual_rank: Optional[float] = None,
    vol_index_closes: Optional[list[float]] = None,
    own_closes: Optional[list[float]] = None,
) -> VolRead:
    """Walk the rungs in order and return the first real answer."""
    if barchart_row is not None and barchart_row.iv_rank is not None:
        return VolRead(iv_rank=barchart_row.iv_rank, iv=barchart_row.iv,
                       hv=barchart_row.hv30, source="Barchart export")

    if manual_rank is not None:
        return VolRead(iv_rank=float(manual_rank), source="typed in by hand",
                       note="Read off Barchart by hand, so it is only as fresh as "
                            "when you typed it.")

    index = vol_index_for(symbol)
    if index and vol_index_closes:
        rank = rank_in_range(vol_index_closes)
        if rank is not None:
            name = _INDEX_NAME.get(index, index)
            return VolRead(
                iv_rank=rank, iv=round(vol_index_closes[-1], 1), source=name,
                note=f"{name} is the market's own implied volatility for "
                     f"{symbol.upper()}, ranked against its last year. A real IV "
                     "Rank, not a stand-in.")

    if own_closes:
        series = realized_vol_series(own_closes)
        rank = rank_in_range(series)
        if rank is not None:
            return VolRead(
                iv_rank=rank, hv=round(series[-1], 1),
                source="realized volatility, a proxy", is_proxy=True,
                note="No implied-volatility history for this name, so this ranks how "
                     "much it has ACTUALLY moved against its own year. Useful, but "
                     "not the same thing: implied volatility runs ahead of realized "
                     "before earnings, and this proxy will not see that coming. "
                     "Import a Barchart IV Rank export to replace it.")

    return VolRead(note="Nothing available to grade volatility with. Import a "
                        "Barchart IV Rank export, or type the rank in by hand.")
