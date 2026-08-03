"""Tests for the market-context read (works offline from the fixture)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data.chain import OptionChain
from src.data.market_context import (
    build_context,
    context_from_chain,
    daily_sentiment,
    trend_from_prices,
)

FIXTURE = Path(__file__).parent / "fixtures" / "spx_chain.json"


@pytest.fixture(scope="module")
def chain() -> OptionChain:
    return OptionChain.from_json(FIXTURE)


def test_context_reads_atm_iv_and_summarizes(chain):
    ctx = context_from_chain(chain, vix=12.0, trend="sideways")
    assert ctx.price == 5100.0
    assert ctx.atm_iv is not None
    assert "SPX" in ctx.summary
    assert ctx.suggestions  # low VIX + sideways should suggest something


def test_low_vix_sideways_suggests_iron_condor(chain):
    ctx = context_from_chain(chain, vix=12.0, trend="sideways")
    assert any(s.strategy_key == "iron_condor" for s in ctx.suggestions)


def test_best_strategy_matches_trend():
    # Sideways -> Iron Condor leads; bullish -> Put Credit Spread; bearish -> Call Credit Spread.
    assert build_context("SPX", 5100, vix=12.0, trend="sideways").best_strategy_key == "iron_condor"
    assert build_context("SPX", 5100, vix=18.0, trend="up").best_strategy_key == "put_credit_spread"
    assert build_context("SPX", 5100, vix=18.0, trend="down").best_strategy_key == "call_credit_spread"


def test_high_vol_adds_size_caution():
    ctx = build_context("SPX", 5100, vix=32.0, trend="sideways")
    assert "keep size small" in ctx.recommendation_reason.lower()


def test_high_vix_note_mentions_elevated():
    ctx = build_context("SPX", 5100, vix=30.0, trend="up")
    assert "elevated" in ctx.volatility_read.lower()


def test_daily_sentiment_positive_calm():
    label, note = daily_sentiment([0.8, 0.9, 0.7], vix=12.0)
    assert "positive" in label.lower()
    assert "calm" in label.lower()
    assert "+0.80%" in note


def test_daily_sentiment_negative_nervous():
    label, _ = daily_sentiment([-1.2, -0.9, -1.5], vix=30.0)
    assert "negative" in label.lower()
    assert "nervous" in label.lower()


def test_daily_sentiment_flat_and_missing_data():
    label, _ = daily_sentiment([0.05, -0.03], vix=18.0)
    assert "flat" in label.lower() or "mixed" in label.lower()
    label2, _ = daily_sentiment([None, None], vix=None)
    assert "no read" in label2.lower()


def test_trend_from_prices():
    rising = [100 + i for i in range(60)]          # steadily climbing
    falling = [200 - i for i in range(60)]         # steadily dropping
    flat = [100.0] * 60
    assert trend_from_prices(rising) == "up"
    assert trend_from_prices(falling) == "down"
    assert trend_from_prices(flat) == "sideways"
    assert trend_from_prices([1, 2, 3]) == "unknown"  # not enough data


# ---- the 200-day veto -------------------------------------------------
# The 20/50 crossover only sees about six weeks. SOFI drifted sideways on it
# while sitting 23% below its 200-day and down 23% on the year, so it landed in
# the "puts you'd sell for income" list - the falling knife the SOP avoids.

def _slid_then_flat() -> list[float]:
    """A long slide, then a flat stretch: the shape that fooled the crossover."""
    return [200.0 - i * 0.6 for i in range(200)] + [80.0] * 40


def test_a_long_slide_that_went_flat_is_still_a_downtrend():
    prices = _slid_then_flat()
    # The last six weeks alone look calm...
    assert trend_from_prices(prices[-60:]) == "sideways"
    # ...but with the full year in view it is a downtrend.
    assert trend_from_prices(prices) == "down"


def test_a_recovering_stock_is_not_condemned_to_downtrend():
    """A name that crashed and is genuinely climbing back sits under its
    200-day for months. Calling that a downtrend would throw away exactly the
    recoveries the wheel wants, so a rising short read is capped at sideways."""
    prices = [200.0 - i * 0.8 for i in range(200)] + [40.0 + i * 1.2 for i in range(40)]
    assert trend_from_prices(prices) == "sideways"


def test_a_name_above_its_200_day_is_judged_on_the_crossover_alone():
    rising = [100 + i * 0.5 for i in range(240)]
    assert trend_from_prices(rising) == "up"


def test_the_veto_needs_a_full_200_days_before_it_applies():
    """With under 200 closes there is no long average to judge against, so the
    old short-term answer stands rather than a guess."""
    short_history = [100.0] * 60
    assert trend_from_prices(short_history) == "sideways"
