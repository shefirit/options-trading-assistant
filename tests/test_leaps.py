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


def _wide(strike: float, dte: int, premium: float, delta: float = 0.75,
          oi: int = 800, spread: float = 30.0) -> OptionContract:
    """Same, with the bid-ask gap set to `spread` percent of the mid."""
    half = premium * spread / 200
    return OptionContract(
        option_type=OptionType.CALL, strike=strike, expiration="2028-01-21", dte=dte,
        delta=delta, iv=0.30, bid=round(premium - half, 2), ask=round(premium + half, 2),
        open_interest=oi)


STRONG_INFO = {
    "shortName": "Solid Co", "sector": "Technology", "marketCap": 400e9,
    "profitMargins": 0.25, "revenueGrowth": 0.18, "returnOnEquity": 0.28,
    "debtToEquity": 45.0,
}


# ---------- indicators ----------
def test_weekly_closes_takes_the_last_day_of_each_week():
    """Counted back from the NEWEST bar, so any partial week is the oldest one.

    This deliberately reverses what the function used to do. Grouping forward
    from the oldest bar (which gave [5, 7] here) left the partial week at the
    RECENT end and moved every boundary whenever the history length changed -
    so the same latest price produced a different weekly reading depending on
    how much history a tab had asked for. See test_indicator_accuracy.py.
    """
    assert leaps.weekly_closes([1, 2, 3, 4, 5, 6, 7]) == [2, 7]
    assert leaps.weekly_closes([1, 2, 3, 4, 5]) == [5]
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


def test_dividend_yield_prefers_the_dollar_rate_over_the_ambiguous_field():
    # AAPL as Yahoo actually reports it: rate in dollars, yield already a percent.
    info = {"trailingAnnualDividendRate": 1.04, "dividendYield": 0.34}
    assert leaps.dividend_yield_pct(info, price=333.02) == pytest.approx(0.312, abs=0.01)


def test_a_small_percent_yield_is_not_read_as_a_huge_fraction():
    """0.20 in percent form means 0.20%, not 20%. The old threshold sat at
    0.25 and turned every low-yielding mega-cap - AAPL, QQQ - into one that
    appeared to pay a fifth of its price a year, which then swamped the
    all-in cost of the contract."""
    assert leaps.dividend_yield_pct({"dividendYield": 0.20}) == pytest.approx(0.20)
    assert leaps.dividend_yield_pct({"dividendYield": 0.34}) == pytest.approx(0.34)


def test_the_fallback_heuristic_still_has_an_undecidable_band():
    """Below 0.12 the two conventions genuinely overlap: 0.11 is either an
    11% yield written as a fraction or a 0.11% one written as a percent, and
    no threshold separates them. We keep the app-wide convention (fraction)
    and rely on the dollar rate, which every real payer reports, to settle it."""
    assert leaps.dividend_yield_pct({"dividendYield": 0.11}) == pytest.approx(11.0)
    # ...but the rate wins whenever it is there, which is the point of the fix.
    assert leaps.dividend_yield_pct(
        {"dividendYield": 0.11, "trailingAnnualDividendRate": 0.11},
        price=100.0) == pytest.approx(0.11)


def test_dividend_yield_ignores_junk_values():
    assert leaps.dividend_yield_pct({"dividendYield": 900}) == 0.0
    assert leaps.dividend_yield_pct({"dividendYield": -2}) == 0.0


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


def test_pick_contract_skips_a_thin_strike_for_a_liquid_one():
    """Open interest is a hard rule, not a tiebreak.

    The picker used to ignore it entirely and hand back strikes with an open
    interest of 2 or 3 - contracts you cannot get out of.
    """
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(88, 400, 21.0, delta=0.72, oi=3),      # closest to the floor, untradable
        _call(85, 400, 23.0, delta=0.76, oi=900),    # the one she can actually trade
    ])
    assert leaps.pick_contract(chain, target_delta=0.70).strike == 85


def test_pick_contract_reaches_a_further_expiration_to_stay_compliant():
    """A compliant contract one expiration out beats a broken nearer one.

    Live case this comes from: UPS listed a 400-day call with an open interest
    of 22, and a 526-day call at delta 0.78 with 849. The old picker locked onto
    a single expiration and took the untradable one.
    """
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 400, 20.0, delta=0.72, oi=22),
        _call(90, 526, 24.0, delta=0.78, oi=849),
    ])
    picked = leaps.pick_contract(chain, target_delta=0.70)
    assert (picked.dte, picked.open_interest) == (526, 849)


def test_pick_contract_will_not_run_past_the_dte_ceiling():
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 400, 20.0, delta=0.72),
        _call(90, 900, 34.0, delta=0.72),            # beyond dte_max
    ])
    assert leaps.pick_contract(chain).dte == 400


def test_pick_contract_aims_at_the_target_delta_not_the_floor():
    """0.70 is a floor to clear; 0.72 is what her SOP actually aims at."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(70, 400, 33.0, delta=0.90),
        _call(85, 400, 23.0, delta=0.74),
        _call(95, 400, 15.0, delta=0.62),            # under the floor
    ])
    assert leaps.pick_contract(chain, target_delta=0.70).strike == 85


def test_pick_contract_accepts_a_delta_that_rounds_to_the_floor():
    """AEP listed 0.6999 against a 0.70 floor - rejected by a ten-thousandth."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(115, 525, 21.0, delta=0.6999, oi=354),
    ])
    picked = leaps.pick_contract(chain, target_delta=0.70)
    assert picked.strike == 115
    assert leaps.breaches(picked) == []


def test_a_wide_spread_is_never_a_breach():
    """Her ruling, 2026-08-14: the spread gets noticed, never enforced. It was
    briefly a hard rule earlier the same day - do not put it back."""
    wide = _wide(88, 525, 26.0, delta=0.73, oi=490, spread=54.0)
    assert leaps.breaches(wide) == []
    assert leaps.meets_sop(wide, 0.70, 365, 800, 250) is True


def test_a_wide_spread_still_gets_said_out_loud():
    note = leaps.spread_note(_wide(88, 525, 26.0, delta=0.73, oi=490, spread=54.0))
    assert note is not None and "54%" in note
    assert leaps.spread_note(_call(85, 525, 28.0, delta=0.76, oi=300)) is None


def test_a_wide_spread_cannot_push_a_contract_out_of_the_running():
    """Only the OI floor and the delta/DTE window can do that."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _wide(88, 525, 26.0, delta=0.73, oi=490, spread=54.0),
    ])
    assert leaps.pick_contract(chain, target_delta=0.70).strike == 88


def test_the_spread_only_breaks_a_tie():
    """Two contracts equal on every rule - the cheaper one to trade out of wins.
    This is the whole of "pay attention to it" with no limit attached."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _wide(88, 525, 26.0, delta=0.72, oi=500, spread=40.0),
        _wide(88, 525, 26.0, delta=0.72, oi=500, spread=3.0),
    ])
    picked = leaps.pick_contract(chain, target_delta=0.70)
    assert leaps.spread_pct(picked) == 3.0


def test_an_unquotable_spread_is_not_held_against_a_contract():
    contract = OptionContract(
        option_type=OptionType.CALL, strike=85, expiration="2028-01-21", dte=525,
        delta=0.73, bid=0.0, ask=0.0, open_interest=900)
    assert leaps.spread_pct(contract) is None
    assert leaps.spread_note(contract) is None
    assert leaps.meets_sop(contract, 0.70, 365, 800, 250) is True


def test_a_near_stock_substitute_delta_is_flagged():
    """EBAY's only liquid 525-day strike was delta 0.94 - compliant on the
    letter, but that is a PMCC leg, not a bet on a move."""
    broken = leaps.breaches(_call(50, 525, 57.25, delta=0.9426, oi=974))
    assert len(broken) == 1
    assert "stock substitute" in broken[0]


def test_the_target_delta_is_preferred_over_a_deeper_liquid_strike():
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(50, 525, 57.0, delta=0.94, oi=974),
        _call(88, 525, 26.0, delta=0.73, oi=900),
    ])
    assert leaps.pick_contract(chain, target_delta=0.70).strike == 88


def test_pick_contract_still_returns_something_when_nothing_qualifies():
    """Showing her nothing would be worse - but see the flag test below."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 308, 18.0, delta=0.72, oi=7),
    ])
    assert leaps.pick_contract(chain).dte == 308


def test_the_fallback_prefers_the_least_bad_contract():
    """WM had a 308-day strike with an open interest of 7 and a 672-day one with
    3. Neither is tradable, but reaching for the longer date made it worse."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 672, 26.0, delta=0.72, oi=3),      # breaks OI only, but thinner
        _call(90, 400, 20.0, delta=0.72, oi=180),    # breaks OI only, far more of it
    ])
    picked = leaps.pick_contract(chain)
    assert (picked.dte, picked.open_interest) == (400, 180)


def test_the_fallback_prefers_fewer_broken_rules_over_open_interest():
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(90, 200, 14.0, delta=0.72, oi=900),    # too short AND thin on rules
        _call(90, 400, 20.0, delta=0.72, oi=180),    # only the OI floor missed
    ])
    assert leaps.pick_contract(chain).dte == 400


def test_the_fallback_will_not_trade_delta_away_for_open_interest():
    """MAR's real chain: a 0.24 delta with 266 open interest against a 0.70 with
    56. Both break one rule, but the 0.24 is a different trade entirely."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(140, 525, 4.0, delta=0.24, oi=266),
        _call(90, 525, 24.0, delta=0.70, oi=56),
    ])
    assert leaps.pick_contract(chain).strike == 90


def test_the_fallback_will_not_drift_deep_for_open_interest_either():
    """Open interest piles up deep in the money. LIN came back at delta 0.94 for
    $19,325 - the same mistake as the 0.24, off the other end."""
    chain = OptionChain(underlying="T", underlying_price=100.0, contracts=[
        _call(40, 525, 61.0, delta=0.94, oi=130),
        _call(88, 525, 26.0, delta=0.74, oi=22),
    ])
    assert leaps.pick_contract(chain).strike == 88


# ---------- the rules a contract breaks ----------
def test_breaches_is_empty_for_a_compliant_contract():
    assert leaps.breaches(_call(85, 400, 23.0, delta=0.74, oi=900)) == []


def test_breaches_names_the_short_expiration_and_the_thin_strike():
    broken = leaps.breaches(_call(90, 308, 18.0, delta=0.72, oi=7))
    assert len(broken) == 2
    assert any("308 days" in b for b in broken)
    assert any("Open interest is 7" in b for b in broken)


def test_breaches_ignores_delta_when_the_feed_sent_no_greeks():
    """A delta of 0 means the feed is missing greeks, not a shallow option."""
    broken = leaps.breaches(_call(90, 400, 18.0, delta=0.0, oi=900))
    assert broken == []


def test_full_score_flags_a_contract_that_breaks_the_rules():
    """The whole point: the Analyze tab cannot show a bad pick in silence."""
    closes = _rising()
    spot = closes[-1]
    chain = OptionChain(underlying="UP", underlying_price=spot, contracts=[
        _call(round(spot * 0.9, 1), 308, spot * 0.15, delta=0.72, oi=7),
    ])
    setup = leaps.score_setup("UP", closes, market_cap=400e9, info=STRONG_INFO)
    full = leaps.score_full(setup, chain, closes, STRONG_INFO)
    assert any("does not meet your SOP" in f for f in full.flags)
    assert any("308 days" in f for f in full.flags)


def test_full_score_stays_quiet_when_the_contract_is_compliant():
    closes = _rising()
    spot = closes[-1]
    chain = OptionChain(underlying="UP", underlying_price=spot, contracts=[
        _call(round(spot * 0.9, 1), 400, spot * 0.18, delta=0.75, oi=900),
    ])
    setup = leaps.score_setup("UP", closes, market_cap=400e9, info=STRONG_INFO)
    full = leaps.score_full(setup, chain, closes, STRONG_INFO)
    assert not any("does not meet your SOP" in f for f in full.flags)


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


# ---------- the critical-pillar gate ----------
def _pillar(key: str, score: float) -> leaps.Pillar:
    return leaps.Pillar(key=key, label=key.title(),
                        weight=leaps.DEFAULT_WEIGHTS[key], score=score)


def test_a_failing_cost_pillar_caps_an_otherwise_glowing_score():
    """The whole point of weighting cost at 25% was to stop a great company
    with terrible option pricing looking like a buy. A plain average does not
    manage it - AAPL scored 74 on the average with a cost pillar of 22."""
    pillars = [_pillar("trend", 100), _pillar("entry", 89), _pillar("quality", 94),
               _pillar("cost", 22), _pillar("odds", 81)]
    raw = leaps.blend(pillars)
    assert raw > 70                                  # the average alone reads green
    assert leaps.apply_gate(raw, pillars) == leaps.CAPPED_SCORE


def test_a_failing_odds_pillar_also_caps():
    pillars = [_pillar("trend", 95), _pillar("entry", 90), _pillar("quality", 95),
               _pillar("cost", 80), _pillar("odds", 20)]
    assert leaps.apply_gate(leaps.blend(pillars), pillars) == leaps.CAPPED_SCORE


def test_a_weak_but_not_failing_pillar_does_not_cap():
    pillars = [_pillar("trend", 90), _pillar("entry", 80), _pillar("quality", 85),
               _pillar("cost", 50), _pillar("odds", 70)]
    raw = leaps.blend(pillars)
    assert leaps.apply_gate(raw, pillars) == raw


def test_a_weak_non_critical_pillar_does_not_cap():
    """Trend and quality are not make-or-break the way cost and odds are - a
    cheap option on a dull company is still a legitimate thing to consider."""
    pillars = [_pillar("trend", 20), _pillar("entry", 30), _pillar("quality", 25),
               _pillar("cost", 90), _pillar("odds", 85)]
    raw = leaps.blend(pillars)
    assert leaps.apply_gate(raw, pillars) == raw


def test_the_gate_never_raises_a_low_score():
    pillars = [_pillar("trend", 10), _pillar("entry", 10), _pillar("quality", 10),
               _pillar("cost", 10), _pillar("odds", 10)]
    raw = leaps.blend(pillars)
    assert leaps.apply_gate(raw, pillars) == raw < leaps.CAPPED_SCORE


def test_failing_pillars_come_back_worst_first():
    pillars = [_pillar("cost", 30), _pillar("odds", 12)]
    assert [p.key for p in leaps.failing_pillars(pillars)] == ["odds", "cost"]


def test_an_unmeasured_pillar_cannot_trigger_the_gate():
    pillars = [_pillar("trend", 90), _pillar("entry", 90), _pillar("quality", 90),
               _pillar("cost", 0), _pillar("odds", 90)]
    pillars[3].measured = False
    raw = leaps.blend(pillars)
    assert leaps.apply_gate(raw, pillars) == raw


# ---------- the base-rate distribution that feeds the chart ----------
def test_the_distribution_buckets_cover_every_window():
    base = leaps.historical_base_rate(_rising(), 365, required_pct=5.0)
    assert base.distribution
    assert sum(b["pct"] for b in base.distribution) == pytest.approx(100.0, abs=0.5)


def test_buckets_above_the_required_move_are_marked_as_clearing():
    base = leaps.historical_base_rate(_rising(), 365, required_pct=5.0)
    for bucket in base.distribution:
        assert bucket["clears"] == (bucket["mid"] >= 5.0)


def test_distribution_is_empty_when_history_is_too_short():
    assert leaps.historical_base_rate([100.0] * 10, 365, 5.0).distribution == []
