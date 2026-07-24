"""The chart's technical indicators, checked against hand-worked numbers."""

import math

import pytest

from src.engine import indicators as ind


# ---------------------------------------------------------------- moving averages
def test_sma_averages_the_last_n_closes():
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_sma_is_none_until_there_are_enough_closes():
    out = ind.sma([10, 20], 5)
    assert out == [None, None]


def test_sma_lines_up_one_for_one_with_the_bars():
    closes = [float(i) for i in range(50)]
    assert len(ind.sma(closes, 20)) == len(closes)


def test_sma_rejects_a_nonsense_length():
    with pytest.raises(ValueError):
        ind.sma([1, 2, 3], 0)


def test_ema_seeds_on_the_first_sma_then_weights_recent_closes():
    """Seed = mean(1,2,3) = 2; then k = 2/4 = 0.5, so 2 + (4-2)*0.5 = 3."""
    out = ind.ema([1, 2, 3, 4], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)


def test_ema_turns_faster_than_the_sma_after_a_jump():
    closes = [10.0] * 20 + [20.0] * 5
    assert ind.ema(closes, 10)[-1] > ind.sma(closes, 10)[-1]


# ---------------------------------------------------------------- bollinger bands
def test_bollinger_middle_band_is_the_moving_average():
    closes = [float(i) for i in range(1, 41)]
    upper, mid, lower = ind.bollinger(closes, 20)
    assert mid == ind.sma(closes, 20)


def test_bollinger_bands_sit_symmetrically_around_the_middle():
    closes = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]     # textbook sd = 2
    upper, mid, lower = ind.bollinger(closes, 8, mult=2.0)
    assert mid[-1] == pytest.approx(5.0)
    assert upper[-1] == pytest.approx(9.0)
    assert lower[-1] == pytest.approx(1.0)


def test_bollinger_bands_widen_when_the_market_swings():
    calm = [100.0, 100.5, 100.0, 100.5] * 10
    wild = [100.0, 110.0, 90.0, 105.0] * 10
    calm_width = ind.bollinger(calm, 20)[0][-1] - ind.bollinger(calm, 20)[2][-1]
    wild_width = ind.bollinger(wild, 20)[0][-1] - ind.bollinger(wild, 20)[2][-1]
    assert wild_width > calm_width * 5


def test_bollinger_bands_are_undefined_before_the_window_fills():
    upper, mid, lower = ind.bollinger([1.0, 2.0, 3.0], 20)
    assert upper == mid == lower == [None, None, None]


# ---------------------------------------------------------------- rsi
def test_rsi_is_100_when_every_day_is_an_up_day():
    assert ind.rsi([float(i) for i in range(1, 30)])[-1] == pytest.approx(100.0)


def test_rsi_is_0_when_every_day_is_a_down_day():
    assert ind.rsi([float(i) for i in range(30, 1, -1)])[-1] == pytest.approx(0.0)


def test_rsi_sits_near_the_middle_when_gains_and_losses_balance():
    closes, price = [100.0], 100.0
    for i in range(60):
        price += 1.0 if i % 2 == 0 else -1.0
        closes.append(price)
    assert 40 < ind.rsi(closes)[-1] < 60


def test_rsi_stays_inside_its_scale():
    import random
    random.seed(7)
    closes, price = [], 100.0
    for _ in range(300):
        price = max(1.0, price + random.uniform(-3, 3))
        closes.append(price)
    for v in ind.rsi(closes):
        assert v is None or 0.0 <= v <= 100.0


def test_rsi_needs_more_closes_than_its_period():
    assert ind.rsi([1.0, 2.0, 3.0], 14) == [None, None, None]


# ---------------------------------------------------------------- macd
def test_macd_histogram_is_the_gap_between_the_two_lines():
    closes = [100.0 + i * 0.5 for i in range(80)]
    line, sig, hist = ind.macd(closes)
    for m, s, h in zip(line, sig, hist):
        if h is not None:
            assert h == pytest.approx(m - s)


def test_macd_line_is_positive_while_price_climbs():
    closes = [100.0 + i for i in range(80)]
    line, _, _ = ind.macd(closes)
    assert line[-1] > 0


def test_macd_line_is_negative_while_price_falls():
    closes = [200.0 - i for i in range(80)]
    line, _, _ = ind.macd(closes)
    assert line[-1] < 0


def test_macd_survives_a_series_too_short_to_compute():
    line, sig, hist = ind.macd([1.0, 2.0, 3.0])
    assert line == sig == hist == [None, None, None]


def test_every_indicator_returns_one_value_per_bar():
    closes = [100.0 + math.sin(i / 5) * 3 for i in range(250)]
    n = len(closes)
    assert len(ind.sma(closes, 50)) == n
    assert len(ind.ema(closes, 20)) == n
    assert len(ind.rsi(closes)) == n
    assert all(len(s) == n for s in ind.bollinger(closes, 20))
    assert all(len(s) == n for s in ind.macd(closes))
