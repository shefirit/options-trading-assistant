"""The volatility rank: how expensive a name's options are versus its own year.

This is NOT textbook IV Rank. No free source publishes a year of implied
volatility, so we rank today's implied vol against how much the stock has
actually moved. These tests pin the maths and, just as importantly, pin that it
returns None instead of a guess when it cannot rank honestly.
"""

from __future__ import annotations

import math
import random

from src.data.premium_finder import rank_read, vol_history, vol_rank


def _steady(n: int = 300, start: float = 100.0, step: float = 0.001) -> list[float]:
    """A price series that moves the same small amount every day."""
    out, p = [], start
    for i in range(n):
        p *= (1 + step) if i % 2 == 0 else (1 - step)
        out.append(p)
    return out


def _calm_then_wild(calm: int = 200, wild: int = 60) -> list[float]:
    """Quiet for most of the year, then a volatile stretch at the end."""
    rng = random.Random(7)
    out, p = [], 100.0
    for _ in range(calm):
        p *= 1 + rng.gauss(0, 0.004)
        out.append(p)
    for _ in range(wild):
        p *= 1 + rng.gauss(0, 0.045)
        out.append(p)
    return out


# ---------------- the rolling series ----------------

def test_vol_history_returns_one_reading_per_eligible_day():
    closes = _steady(200)
    hist = vol_history(closes, lookback=30)
    # 200 closes -> 199 returns -> 199 - 30 + 1 windows
    assert len(hist) == 199 - 30 + 1
    assert all(math.isfinite(v) and v >= 0 for v in hist)


def test_vol_history_needs_enough_data():
    assert vol_history([100.0] * 10, lookback=30) == []
    assert vol_history([], lookback=30) == []


def test_vol_history_ignores_junk_prices():
    """The price feed sends the odd NaN and zero - one used to poison the lot."""
    closes = _steady(120)
    dirty = closes[:50] + [float("nan"), 0.0, -5.0] + closes[50:]
    assert len(vol_history(dirty, lookback=30)) == len(vol_history(closes, lookback=30))


# ---------------- the rank ----------------

def test_a_high_reading_ranks_near_100_and_a_low_one_near_zero():
    closes = _calm_then_wild()
    hist = vol_history(closes)
    lo, hi = min(hist), max(hist)
    assert vol_rank(closes, hi) == 100.0
    assert vol_rank(closes, lo) == 0.0
    mid = vol_rank(closes, (lo + hi) / 2)
    assert 45 <= mid <= 55


def test_todays_vol_after_a_volatile_stretch_ranks_high():
    """The practical case: the stock just got wild, so its options should read
    as expensive versus the rest of the year."""
    closes = _calm_then_wild()
    hist = vol_history(closes)
    assert vol_rank(closes, hist[-1]) > 70


def test_a_value_outside_the_year_is_clamped_not_extrapolated():
    closes = _calm_then_wild()
    hi = max(vol_history(closes))
    assert vol_rank(closes, hi * 5) == 100.0
    assert vol_rank(closes, 0.0) == 0.0


def test_it_refuses_to_rank_without_enough_history():
    """Better no number than a number built on three weeks of data."""
    assert vol_rank(_steady(50), 0.3) is None
    assert vol_rank([], 0.3) is None


def test_it_refuses_when_there_is_no_current_reading():
    closes = _calm_then_wild()
    assert vol_rank(closes, None) is None
    assert vol_rank(closes, float("nan")) is None


def test_a_flat_line_has_no_range_to_rank_inside():
    """A series that never moves gives lo == hi; dividing by that range would
    be a zero-divide, and any answer would be meaningless anyway."""
    assert vol_rank([100.0] * 300, 0.2) is None


# ---------------- the plain-English read ----------------

def test_the_read_matches_the_number():
    assert rank_read(85) == "Expensive - good time to sell"
    assert rank_read(70) == "Expensive - good time to sell"
    assert rank_read(55) == "Above average - decent for selling"
    assert rank_read(40) == "Middling"
    assert rank_read(10) == "Cheap - poor time to sell"
    assert rank_read(None) == "n/a"
