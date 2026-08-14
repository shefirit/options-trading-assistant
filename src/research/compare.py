"""Several names side by side, on the numbers that decide between them.

The Analyze tab goes deep on ONE ticker - seven tools, each answering a
different question about it. That is the right shape for studying a name and
the wrong one for choosing between three, where the question is not "what is
NVDA doing" but "which of these do I want".

So this is deliberately shallow and wide: one row per ticker, the handful of
figures that actually separate candidates, and nothing that needs a second
click to read.

It stops at the STOCK picture on purpose. Judging the options - implied
volatility, how rich the premium is, what a spread would pay - needs an option
chain per name, which is slow and is already covered by the Premium tab's own
comparison. Two tools, two questions: this one picks the name, that one prices
the trade.

Pure: takes numbers in, returns rows out. No Streamlit, no network.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel

TRADING_DAYS_YEAR = 252


class CompareRow(BaseModel):
    """One ticker's line in the comparison."""

    symbol: str
    name: str = ""
    kind: str = ""                       # "index" | "etf" | "stock"
    price: Optional[float] = None
    change_pct: Optional[float] = None   # today
    year_pct: Optional[float] = None     # past 12 months
    off_high_pct: Optional[float] = None # negative: how far under the 52w high
    trend: str = ""
    rsi: Optional[float] = None
    grade: str = ""
    pe: Optional[float] = None
    rev_growth_pct: Optional[float] = None
    days_to_earnings: Optional[int] = None
    note: str = ""                       # why a row is thin, in her words


def _pct_change(closes: list[float], back: int) -> Optional[float]:
    if not closes or len(closes) <= back:
        return None
    then = closes[-1 - back]
    return ((closes[-1] / then) - 1.0) * 100.0 if then else None


def _off_high(closes: list[float]) -> Optional[float]:
    window = closes[-TRADING_DAYS_YEAR:] if len(closes) >= TRADING_DAYS_YEAR else closes
    if not window:
        return None
    high = max(window)
    return ((closes[-1] / high) - 1.0) * 100.0 if high else None


def _float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def build_row(symbol: str, *, kind: str = "", price: Optional[float] = None,
              change_pct: Optional[float] = None, closes: Optional[list] = None,
              analysis: Any = None, info: Optional[dict] = None,
              earnings_date: Optional[dt.date] = None, trend: str = "",
              today: Optional[dt.date] = None) -> CompareRow:
    """One row from whatever the caller managed to fetch.

    Everything is optional because the sources fail independently: Yahoo
    throttles fundamentals from cloud IPs far more readily than price history,
    so a row with a price and no P/E is normal and must still be shown. A blank
    cell means "not available", never zero - a zero P/E would read as a finding.
    """
    closes = [c for c in (closes or []) if isinstance(c, (int, float))]
    info = info or {}
    today = today or dt.date.today()

    row = CompareRow(symbol=symbol.upper(), kind=kind, trend=trend or "")
    row.price = _float(price) or (closes[-1] if closes else None)
    row.change_pct = _float(change_pct)
    row.year_pct = _pct_change(closes, TRADING_DAYS_YEAR)
    row.off_high_pct = _off_high(closes)

    if analysis is not None:
        row.name = getattr(analysis, "name", "") or ""
        if getattr(analysis, "is_fund", False):
            # A fund has no company to grade - SPY is 500 of them. Saying "ETF"
            # is honest where a letter would invent a judgement.
            row.grade = "ETF"
        else:
            row.grade = getattr(analysis, "grade", None) or ""
        if row.price is None:
            row.price = _float(getattr(analysis, "price", None))

    closes_for_rsi = closes
    if len(closes_for_rsi) >= 15:
        from src.data.stock_analysis import rsi as _rsi
        row.rsi = _rsi(closes_for_rsi)

    row.pe = _float(info.get("trailingPE"))
    growth = _float(info.get("revenueGrowth"))
    row.rev_growth_pct = growth * 100.0 if growth is not None else None

    if earnings_date:
        row.days_to_earnings = (earnings_date - today).days

    if row.price is None:
        row.note = "No price came back for this one."
    elif not closes:
        row.note = "Price only - no history, so the trend columns are blank."
    return row


def sort_rows(rows: list[CompareRow], order: Optional[list[str]] = None
              ) -> list[CompareRow]:
    """Keep the order she put them in, not an order we invented.

    The switcher row above the table is in her own most-recent-first order, and
    a table that reshuffles underneath it is a table she has to re-read.
    """
    if not order:
        return list(rows)
    rank = {s.upper(): i for i, s in enumerate(order)}
    return sorted(rows, key=lambda r: rank.get(r.symbol, len(rank)))
