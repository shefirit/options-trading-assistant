"""LEAPS Finder tests - scoring, contract economics and the base rate.

All synthetic: a rising stock, a falling one, and hand-built option chains, so
nothing here touches the network.
"""

from __future__ import annotations

import math

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType
from src.research import leaps


def _rising(n: int = 1300, start: float = 50.0, step: float = 0.08) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling(n: int = 1300, start: float = 200.0, step: float = 0.08) -> list[float]:
    return [max(1.0, start - i * step) for i in range(n)]


def _flat(n: int = 1300, price: float = 100.0) -> list[float]:
    # A gentle zigzag so volatility is not exactly zero.
    return [price + (1.0 if i % 2 else -1.0) for i in range(n)]


def _call(strike: float, dte: int, premium: float, delta: float = 0.75,
          iv: float = 0.30, oi: int = 800) -> OptionContract:
    return OptionContract(
        option_type=OptionType.CALL, strike=strike, expiration="2027-01-15", dte=dte,
        delta=delta, iv=iv, bid=round(premium * 0.98, 2), ask=round(premium * 1.02, 2),
        open_interest=oi)


STRONG_INFO = {
    "shortName": "Solid Co", "sector": "Technology", "marketCap": 400e9,
    "profitMargins": 0.25, "revenueGrowth": 0.18, "returnOnEquity": 0.28,
    "debtToEquity": 45.0,
}


# ---------- indicators ----------
def test_weekly_closes_takes_the_last_day_of_each_week():
    assert leaps.weekly_closes([1, 2, 3, 4, 5, 6, 7]) == [5, 7]
    assert leaps.weekly_closes([]) == []


def test_stochastic_pins_high_at_the_top_of_the_range():
    k, _d = leaps.stochastic(list(range(1, 60)))
    assert k is not None and k > 90


def test_stochastic_pins_low_at_the_bottom_of_the_range():
    k, _d = leaps.stochastic(list(range(60, 1, -1)))
    assert k is not None and k < 10


def test_stochastic_needs_enough_history():
    assert leaps.stochastic([1, 2, 3]) == (None, None)


def test_realized_vol_is_higher_for_a_jumpier_stock():
    calm = [100 + (0.1 if i % 2 else -0.1) for i in range(400)]
    wild = [100 + (5.0 if i % 2 else -5.0) for i in range(400)]
    assert leaps.realized_vol(wild) > leaps.realized_vol(calm)


def test_dividend_yield_handles_both_yahoo_formats():
    assert leaps.dividend_yield_pct({"dividendYield": 0.0125}) == pytest.approx(1.25)
    assert leaps.dividend_yield_pct({"dividendYield": 3.4}) == pytest.approx(3.4)
    assert leaps.dividend_yield_pct({}) == 0.0


# ---------- base rate ----------
def test_base_rate_of_a_steady_riser_is_high():
    base = leaps.historical_base_rate(_rising(), 365, required_pct=5.0)
    assert base.hit_rate == 100.0
    assert base.median_pct > 5


def test_base_rate_of_a_faller_is_zero():
    base = leaps.historical_base_rate(_falling(), 365, required_pct=5.0)
    assert base.hit_rate == 0.0


def test_base_rate_reports_how_often_it_would_have_expired_worthless():
    base = leaps.historical_base_rate(_falling(), 365, required_pct=5.0,
                                      strike_drop_pct=-10.0)
    assert base.loss_rate is not None and base.loss_rate > 50


def test_base_rate_needs_enough_history():
    base = leaps.historical_base_rate([100.0] * 50, 365, 5.0)
    assert base.hit_rate is None
    assert "not enough" in base.read.lower()


def test_probability_above_moves_the_right_way():
    near = leaps.probability_above(100, 105, 365, 0.30)
    far = leaps.probability_above(100, 200, 365, 0.30)
    assert near > far
    assert leaps.probability_above(100, 105, 0, 0.30) is None


# ---------- contract economics ----------
def test_economics_splits_intrinsic_from_time_premium():
    econ = leaps.economics(_call(strike=90, dte=365, premium=25.0), spot=100.0)
    assert econ.intrinsic == pytest.approx(10.0)
    assert econ.extrinsic == pytest.approx(15.0)
    assert econ.extrinsic_ann_pct == pytest.approx(15.0, abs=0.2)   # 15% of spot in a year
    assert econ.breakeven == pytest.approx(115.0)
    assert econ.required_move_pct == pytest.approx(15.0)
    assert econ.total_loss_price == 90
    assert econ.total_loss_drop_pct == pytest.approx(-10.0)


def test_economics_computes_leverage_against_the_shares():
    econ = leaps.economics(_call(strike=90, dte=365, premium=25.0, delta=0.75), spot=100.0)
    # 0.75 delta for $25 on a $100 stock = $75 of exposure per $25 = 3x
    assert econ.leverage == pytest.approx(3.0, abs=0.01)


def test_dividends_given_up_are_counted_in_the_all_in_cost():
    contract = _call(strike=90, dte=365, premium=25.0)
    plain = leaps.economics(contract, spot=100.0, info={})
    payer = leaps.economics(contract, spot=100.0, info={"dividendYield": 0.04})
    assert payer.dividend_give_up_pct == pytest.approx(4.0, abs=0.1)
    assert payer.all_in_cost_ann_pct > plain.all_in_cost_ann_pct


def test_thin_option_is_marked_thin():
    contract = OptionContract(option_type=OptionType.CALL, strike=90, expiration="2027-01-15",
                              dte=365, delta=0.75, iv=0.3, bid=20.0, ask=30.0,
                              open_interest=5)
    assert leaps.economics(contract, spot=100.0).liquidity == "Thin"


# ---------- pillar scoring ----------
def test_uptrend_scores_far_above_a_downtrend():
    assert leaps.score_trend(_rising()).score > leaps.score_trend(_falling()).score


def test_downtrend_is_flagged_as_a_caution():
    pillar = leaps.score_trend(_falling())
    assert pillar.status == "watch"
    assert any("below the 200-day" in f for f in pillar.factors)


def test_entry_prefers_a_shallow_pullback_to_a_broken_chart():
    rising = _rising()
    shallow = rising + [rising[-1] * 0.94]
    broken = rising + [rising[-1] * 0.55]
    assert leaps.score_entry(shallow).score > leaps.score_entry(broken).score


def test_quality_rewards_a_big_profitable_grower():
    strong = leaps.score_quality(STRONG_INFO)
    weak = leaps.score_quality({"marketCap": 1e9, "profitMargins": -0.1,
                                "revenueGrowth": -0.2, "returnOnEquity": -0.05,
                                "debtToEquity": 400.0})
    assert strong.score > 70 and strong.status == "good"
    assert weak.score < 30 and weak.status == "watch"


def test_quality_says_so_when_it_has_no_data():
    pillar = leaps.score_quality({})
    assert pillar.measured is False


def test_cost_pillar_punishes_expensive_time_premium():
    cheap = leaps.economics(_call(strike=80, dte=365, premium=22.0), spot=100.0)
    dear = leaps.economics(_call(strike=80, dte=365, premium=40.0), spot=100.0)
    assert leaps.score_cost(cheap).score > leaps.score_cost(dear).score


def test_cost_pillar_punishes_buying_at_peak_implied_vol():
    econ = leaps.economics(_call(strike=80, dte=365, premium=25.0, iv=0.30), spot=100.0)
    at_lows = leaps.score_cost(econ, realized_vol_pct=30.0, iv_percentile=10)
    at_highs = leaps.score_cost(econ, realized_vol_pct=30.0, iv_percentile=100)
    assert at_lows.score > at_highs.score
    assert any("peak premium" in f for f in at_highs.factors)


def test_cost_pillar_punishes_implied_vol_above_realized():
    econ = leaps.economics(_call(strike=80, dte=365, premium=25.0, iv=0.50), spot=100.0)
    overpriced = leaps.score_cost(econ, realized_vol_pct=20.0)
    fair = leaps.score_cost(econ, realized_vol_pct=52.0)
    assert fair.score > overpriced.score


def test_odds_pillar_prefers_a_move_the_stock_actually_makes():
    econ = leaps.economics(_call(strike=90, dte=365, premium=15.0), spot=100.0)
    likely = leaps.historical_base_rate(_rising(), 365, econ.required_move_pct,
                                        econ.total_loss_drop_pct)
    unlikely = leaps.historical_base_rate(_falling(), 365, econ.required_move_pct,
                                          econ.total_loss_drop_pct)
    assert leaps.score_odds(econ, likely).score > leaps.score_odds(econ, unlikely).score


def test_odds_pillar_calls_out_the_total_loss_asymmetry():
    econ = leaps.economics(_call(strike=90, dte=365, premium=15.0), spot=100.0)
    pillar = leaps.score_odds(econ, None)
    assert any("wipes this contract out" in f for f in pillar.factors)


# ---------- blending and ranking ----------
def test_blend_ignores_pillars_it_could_not_measure():
    measured = leaps.Pillar(key="a", label="A", weight=0.5, score=80.0)
    missing = leaps.Pillar(key="b", label="B", weight=0.5, score=0.0, measured=False)
    assert leaps.blend([measured, missing]) == pytest.approx(80.0)


def test_default_weights_sum_to_one():
    assert sum(leaps.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_cost_and_odds_together_outweigh_any_other_pair():
    weights = leaps.DEFAULT_WEIGHTS
    assert weights["cost"] + weights["odds"] >= 0.45


def test_setup_score_prefers_the_riser():
    good = leaps.score_setup("UP", _rising(), market_cap=400e9, info=STRONG_INFO)
    bad = leaps.score_setup("DOWN", _falling(), market_cap=400e9, info=STRONG_INFO)
    assert good.score > bad.score
    assert good.stage == "setup"
    assert good.pct_off_52w_high is not None


def test_setup_handles_no_history():
    candidate = leaps.score_setup("NADA", [])
    assert candidate.score == 0.0
    assert "no price history" in candidate.summary.lower()


def test_pick_contract_targets_the_requested_delta():
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(80, 400, 28.0, delta=0.85), _call(90, 400, 20.0, delta=0.70),
        _call(100, 400, 13.0, delta=0.55),
    ])
    assert leaps.pick_contract(chain, target_delta=0.70).strike == 90
    assert leaps.pick_contract(chain, target_delta=0.85).strike == 80


def test_pick_contract_prefers_the_longest_dated_expiration():
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 60, 8.0, delta=0.70), _call(90, 400, 20.0, delta=0.70),
    ])
    assert leaps.pick_contract(chain).dte == 400


def test_pick_contract_returns_none_without_calls():
    assert leaps.pick_contract(OptionChain(underlying="T", underlying_price=100.0,
                                           contracts=[])) is None


def test_full_score_adds_the_cost_and_odds_pillars():
    closes = _rising()
    spot = closes[-1]
    chain = OptionChain(underlying="UP", underlying_price=spot, contracts=[
        _call(round(spot * 0.9, 1), 400, spot * 0.18, delta=0.75),
    ])
    setup = leaps.score_setup("UP", closes, market_cap=400e9, info=STRONG_INFO)
    full = leaps.score_full(setup, chain, closes, STRONG_INFO)
    assert full.stage == "full"
    assert {p.key for p in full.pillars} == {"trend", "entry", "quality", "cost", "odds"}
    assert full.econ is not None and full.base_rate is not None
    assert full.comparison is not None and full.strike_ladder


def test_full_score_without_a_chain_keeps_the_chart_score():
    closes = _rising()
    setup = leaps.score_setup("UP", closes, market_cap=400e9, info=STRONG_INFO)
    out = leaps.score_full(setup, None, closes, STRONG_INFO)
    assert out.stage == "setup"
    assert any("no option chain" in f.lower() for f in out.flags)


def test_expensive_time_premium_raises_a_flag():
    closes = _rising()
    spot = closes[-1]
    chain = OptionChain(underlying="UP", underlying_price=spot, contracts=[
        _call(round(spot * 0.9, 1), 365, spot * 0.30, delta=0.75),
    ])
    setup = leaps.score_setup("UP", closes, market_cap=400e9, info=STRONG_INFO)
    full = leaps.score_full(setup, chain, closes, STRONG_INFO)
    assert any("time premium is running" in f.lower() for f in full.flags)


def test_strike_ladder_covers_several_strikes_and_stays_near_the_money():
    spot = 100.0
    chain = OptionChain(underlying="T", underlying_price=spot, contracts=[
        _call(40, 400, 61.0, delta=0.97),     # too deep, should be dropped
        _call(80, 400, 28.0, delta=0.85),
        _call(90, 400, 20.0, delta=0.70),
        _call(100, 400, 13.0, delta=0.55),
    ])
    ladder = leaps.strike_ladder(chain, spot, 400)
    assert [row["strike"] for row in ladder] == [80, 90, 100]
    # A deeper strike always needs a smaller move than a shallower one.
    moves = [row["required_move_pct"] for row in ladder]
    assert moves == sorted(moves)


# ---------- filtering ----------
def _candidate(**kwargs) -> leaps.LeapsCandidate:
    base = dict(symbol="X", price=100.0, score=60.0, market_cap=50e9,
                avg_volume=5e6, sma200=90.0, sma50=95.0, pct_off_52w_high=-8.0,
                weekly_k=55.0, weekly_d=50.0)
    base.update(kwargs)
    return leaps.LeapsCandidate(**base)


def test_filter_excludes_a_stock_below_its_200_day_average():
    below = _candidate(price=80.0)
    assert leaps.passes(below, leaps.Filters(require_above_200dma=True)) is False
    assert leaps.passes(below, leaps.Filters(require_above_200dma=False)) is True


def test_filter_excludes_small_and_illiquid_names():
    assert leaps.passes(_candidate(market_cap=1e9), leaps.Filters()) is False
    assert leaps.passes(_candidate(avg_volume=100_000), leaps.Filters()) is False


def test_filter_excludes_a_broken_chart():
    assert leaps.passes(_candidate(pct_off_52w_high=-60.0), leaps.Filters()) is False


def test_filter_can_exclude_peak_priced_options():
    rules = leaps.Filters(max_iv_percentile=80)
    assert leaps.passes(_candidate(iv_percentile=100.0), rules) is False
    assert leaps.passes(_candidate(iv_percentile=20.0), rules) is True


def test_missing_data_does_not_silently_exclude():
    bare = leaps.LeapsCandidate(symbol="X", score=60.0)
    assert leaps.passes(bare, leaps.Filters()) is True


def test_rank_sorts_best_first_and_numbers_them():
    ranked = leaps.rank([_candidate(symbol="LOW", score=40.0),
                         _candidate(symbol="HIGH", score=90.0)], leaps.Filters())
    assert [c.symbol for c in ranked] == ["HIGH", "LOW"]
    assert [c.rank for c in ranked] == [1, 2]


def test_vol_percentile_places_a_high_reading_near_the_top():
    closes = _flat()
    calm = leaps.realized_vol(closes[-31:], lookback=30) * 100
    assert leaps.vol_percentile(closes, calm * 3) > 80
