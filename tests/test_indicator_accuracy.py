"""One number, one answer - wherever the app shows it.

Rita: "looks like data not accurate. rsi not accurate. there is also diference
of data between tabs inside analyze tab. i need acuuracy."

She was right on both counts, and these tests pin the fixes.

RSI: the app had THREE implementations. The chart used Wilder's method (the
standard one, which thinkorswim and TradingView use); the Overview, LEAPS and
Screener tabs each averaged the last 14 bars, which is not RSI at all. On AAPL
they read 47.6 and 40.8 - either side of the line between "healthy" and
"getting oversold". Now there is one implementation.

Weekly bars: weekly_closes grouped forward from the OLDEST bar, so the week
boundaries moved whenever the history length changed and the same latest price
gave different weekly readings per tab.

Nothing here touches the network - all synthetic series with invented prices.
"""

from __future__ import annotations

import pytest

from src.data import stock_analysis
from src.engine import indicators
from src.research import leaps


def _series(moves: list[float], start: float = 100.0) -> list[float]:
    out = [start]
    for m in moves:
        out.append(out[-1] * (1 + m / 100))
    return out


@pytest.fixture(scope="module")
def closes() -> list[float]:
    """A long, wiggly series - enough bars for Wilder's to settle."""
    import math

    return [100 + 12 * math.sin(i / 9) + 5 * math.sin(i / 2.3) + i * 0.03
            for i in range(400)]


# ------------------------------------------------------------------ one RSI
def test_every_rsi_in_the_app_agrees(closes):
    """The Overview tab, the LEAPS tab, the Screener and the chart must not
    disagree about the same stock's momentum."""
    chart = round(indicators.rsi(closes)[-1], 1)
    overview = stock_analysis.rsi(closes)
    finder = leaps.rsi(closes)
    assert chart == overview == finder


def test_rsi_is_wilders_not_a_plain_14_bar_average(closes):
    """The specific bug. A plain average of the last 14 gains and losses ignores
    everything before them; real RSI seeds at bar 14 and smooths forward, so
    every earlier bar still counts. They are different numbers."""
    period = 14
    gains = sum(max(closes[i] - closes[i - 1], 0.0) for i in range(-period, 0)) / period
    losses = sum(max(closes[i - 1] - closes[i], 0.0) for i in range(-period, 0)) / period
    naive = round(100 - 100 / (1 + gains / losses), 1)

    assert stock_analysis.rsi(closes) != naive, (
        "RSI is back to the naive 14-bar average - it disagrees with thinkorswim")


def test_rsi_matches_a_hand_worked_wilder_example():
    """Independent check against the arithmetic done longhand, so "they all
    agree" cannot pass by all three being wrong together."""
    period = 14
    prices = _series([1, -0.5, 0.8, -0.3, 1.2, 0.4, -0.9, 0.6, 1.1, -0.2,
                      0.7, -0.6, 0.9, 0.3, -0.4, 1.5, -0.8, 0.2])

    # Seed: simple average of the first `period` changes.
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    avg_gain = sum(max(c, 0.0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0.0) for c in changes[:period]) / period
    # Then Wilder's smoothing over the rest.
    for c in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(c, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-c, 0.0)) / period
    expected = round(100 - 100 / (1 + avg_gain / avg_loss), 1)

    assert stock_analysis.rsi(prices) == expected


def test_rsi_is_bounded_and_reads_the_right_direction():
    straight_up = _series([0.8] * 40)
    straight_down = _series([-0.8] * 40)
    assert stock_analysis.rsi(straight_up) == 100.0
    assert stock_analysis.rsi(straight_down) == 0.0
    assert stock_analysis.rsi([100.0] * 5) is None      # not enough bars


def test_rsi_does_not_drift_with_the_history_window(closes):
    """Overview reads 1 year, the chart 2, the LEAPS Finder up to 20. Wilder's
    depends on every earlier bar, so this confirms it has converged and the
    tabs cannot disagree just because they asked for different amounts."""
    full = stock_analysis.rsi(closes)
    for window in (120, 200, 300):
        assert stock_analysis.rsi(closes[-window:]) == full


# ------------------------------------------------------- stable weekly bars
def test_weekly_closes_end_on_the_latest_bar(closes):
    assert leaps.weekly_closes(closes)[-1] == closes[-1]


def test_weekly_closes_ignore_where_the_history_starts(closes):
    """The bug: grouping forward from the oldest bar moved every week boundary
    when the history length changed, so the same latest price produced a
    different weekly stochastic depending on which tab asked."""
    readings = {leaps.stochastic(leaps.weekly_closes(closes[drop:]))
                for drop in range(5)}
    assert len(readings) == 1, f"weekly reading shifted with the start date: {readings}"


def test_weekly_closes_step_five_trading_days(closes):
    weekly = leaps.weekly_closes(closes)
    assert weekly[-2] == closes[-6]
    assert weekly[-3] == closes[-11]


def test_weekly_closes_handles_short_and_empty_input():
    assert leaps.weekly_closes([]) == []
    assert leaps.weekly_closes([101.0]) == [101.0]
    assert leaps.weekly_closes([1.0, 2.0, 3.0]) == [3.0]


# ------------------------------------------------- one bad row cannot poison it
def test_a_single_nan_close_does_not_poison_realized_vol(closes):
    """This exact bug already cost her once, in premium_finder: Yahoo returns the
    odd NaN close, the standard deviation came out NaN, every name graded "Thin"
    premium and the whole Picks scan silently emptied. leaps.realized_vol had
    the same hole, and the LEAPS cost pillar is scored off it."""
    import math

    poisoned = closes[:150] + [float("nan")] + closes[151:]
    value = leaps.realized_vol(poisoned)
    assert value is not None and math.isfinite(value)
    # And it lands near the clean answer rather than being skewed by the gap.
    assert abs(value - leaps.realized_vol(closes)) < 0.02


def test_the_two_volatility_windows_are_different_on_purpose(closes):
    """premium_finder reads 30 days (is the option premium rich right now);
    leaps reads a year (how much does this move over a LEAPS holding period).
    Same maths, different question - so they SHOULD disagree, and neither is
    the other's bug."""
    from src.data import premium_finder

    near = premium_finder.annualized_vol(closes)
    long = leaps.realized_vol(closes)
    assert near is not None and long is not None
    # Same estimator, so a 30-day window on the same series is in the same ballpark.
    assert 0 < near < 2 and 0 < long < 2
