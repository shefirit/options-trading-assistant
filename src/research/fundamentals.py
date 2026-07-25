"""Unit normalizers for Yahoo's fundamentals fields.

Yahoo ships several numbers in units that have changed across yfinance
versions, or that are not what they look like. Every one of these has already
caused a wrong number in this app, so they live in one place and are used
everywhere rather than being re-guessed per module.

The rule that keeps coming out of it: when a field is ambiguous, prefer a
different field that is not, and only fall back to a threshold guess when
there is nothing better. A threshold can never separate a 0.20% yield from a
20% one - only the dollar rate can.
"""

from __future__ import annotations

from typing import Optional


def _float(value) -> Optional[float]:
    """A real, finite number or None. Yahoo's fields come off pandas frames and
    arrive as NaN often enough to matter, and NaN is worse than missing: it
    passes an `is not None` check, then fails every comparison after it, so a
    stock silently scores as if it had failed rather than as unknown."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _pos_float(value) -> Optional[float]:
    number = _float(value)
    return number if number is not None and number > 0 else None


def dividend_yield_pct(info: dict, price: Optional[float] = None) -> float:
    """The annual dividend yield as a percent (2.58 means 2.58%).

    Prefers the dollar rate divided by the share price, because dollars per
    share carry no unit ambiguity. Yahoo's own yield field has shipped both as
    a fraction (0.0053) and as an already-percent number (0.53) depending on
    the yfinance version, and no threshold separates the two cleanly: a
    genuine 0.2%-yielder in percent form is indistinguishable from a 20%
    yielder in fraction form.
    """
    info = info or {}
    rate = _pos_float(info.get("trailingAnnualDividendRate")) or _pos_float(
        info.get("dividendRate"))
    if rate is not None and price and price > 0:
        return rate / price * 100.0

    raw = _pos_float(info.get("dividendYield")) or _pos_float(
        info.get("trailingAnnualDividendYield"))
    if raw is None:
        return 0.0
    # Nothing better to go on, so fall back to the long-standing convention:
    # under 0.12 reads as a fraction, 0.12-25 as a percent, above that is junk.
    # The band below 0.12 is genuinely undecidable - 0.11 is either an 11%
    # yield written as a fraction or a 0.11% one written as a percent.
    if raw < 0.12:
        return raw * 100.0
    return raw if raw <= 25 else 0.0


def debt_to_equity_ratio(info: dict) -> Optional[float]:
    """Debt to equity as a plain multiple (0.45 means 45 cents of debt per
    dollar of equity).

    Yahoo reports this field as a percent - NVDA 6.6, GOOGL 18.9, KO 124.9 -
    so it always wants dividing by 100. It is tempting to "undo the scale only
    if the number looks large", but that misreads the least indebted companies
    of all: Monster Beverage reports 1.08, meaning 0.011x, and a guard like
    `value if value <= 5` turns that into 1.08x and calls one of the most
    debt-free large caps in the market heavily leveraged.
    """
    info = info or {}
    value = _float((info or {}).get("debtToEquity"))
    if value is None or value < 0:
        return None
    return value / 100.0
