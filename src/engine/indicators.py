"""The technical indicators drawn on the Analyze tab's chart.

Pure arithmetic over a list of closing prices - no pandas, no network, no
Streamlit - so every one of them is unit-tested against hand-worked numbers.

These describe what price has already done. None of them predicts anything, and
none of them overrides the SOP's entry checks or exits.
"""

from __future__ import annotations

from typing import Optional

Series = list[Optional[float]]


def sma(values: list[float], length: int) -> Series:
    """Simple moving average: the plain average of the last `length` closes.

    Returns a list the same length as `values`, with None until there are
    enough closes to average - so it lines up with the bars one for one.
    """
    if length <= 0:
        raise ValueError("length must be positive")
    out: Series = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= length:
            running -= values[i - length]
        if i >= length - 1:
            out[i] = running / length
    return out


def ema(values: list[float], length: int) -> Series:
    """Exponential moving average: like the SMA but weighted towards the most
    recent closes, so it turns sooner. Seeded with the first SMA, the usual
    convention (and the one TradingView uses)."""
    if length <= 0:
        raise ValueError("length must be positive")
    out: Series = [None] * len(values)
    if len(values) < length:
        return out
    k = 2.0 / (length + 1)
    prev = sum(values[:length]) / length
    out[length - 1] = prev
    for i in range(length, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def stdev(values: list[float], length: int) -> Series:
    """Population standard deviation over a rolling window - the "how far does
    this typically wander" number the Bollinger Bands are built from."""
    out: Series = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1:i + 1]
        mean = sum(window) / length
        var = sum((v - mean) ** 2 for v in window) / length
        out[i] = var ** 0.5
    return out


def bollinger(values: list[float], length: int = 20,
              mult: float = 2.0) -> tuple[Series, Series, Series]:
    """Bollinger Bands -> (upper, middle, lower).

    The middle band is the 20-day average. The outer bands sit `mult` standard
    deviations either side of it, so they widen when the market swings and
    squeeze when it goes quiet. Price spends roughly 95% of its time inside.
    """
    mid = sma(values, length)
    sd = stdev(values, length)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i, (m, s) in enumerate(zip(mid, sd)):
        if m is not None and s is not None:
            upper[i] = m + mult * s
            lower[i] = m - mult * s
    return upper, mid, lower


def rsi(values: list[float], period: int = 14) -> Series:
    """Wilder's RSI on a 0-100 scale: above 70 is called overbought, below 30
    oversold. Computed across the whole series so the visible window matches
    what any charting package would show for the same days."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[Series, Series, Series]:
    """MACD -> (macd line, signal line, histogram).

    The MACD line is the fast average minus the slow one, so it measures how
    far momentum has pulled away from the trend. The signal line is an average
    of the MACD line; the histogram is the gap between them.
    """
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    line: Series = [None] * len(values)
    for i, (f, s) in enumerate(zip(fast_line, slow_line)):
        if f is not None and s is not None:
            line[i] = f - s

    # The signal line is an EMA of the MACD line, which only starts once the
    # slow average exists - so run it over the defined part and put it back.
    start = next((i for i, v in enumerate(line) if v is not None), None)
    sig: Series = [None] * len(values)
    hist: Series = [None] * len(values)
    if start is None:
        return line, sig, hist
    defined = [v for v in line[start:] if v is not None]
    for offset, v in enumerate(ema(defined, signal)):
        if v is not None:
            sig[start + offset] = v
    for i, (m, s) in enumerate(zip(line, sig)):
        if m is not None and s is not None:
            hist[i] = m - s
    return line, sig, hist
