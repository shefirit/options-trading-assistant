"""Tests for the analyst view, Instant Analyzer, price calculator and options
view. All synthetic - no network."""

from __future__ import annotations

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType
from src.research import analyst, criteria, fair_value, options_view


def _rising(n: int = 1300, start: float = 50.0, step: float = 0.08) -> list[float]:
    return [start + i * step for i in range(n)]


def _falling(n: int = 1300, start: float = 200.0, step: float = 0.08) -> list[float]:
    return [max(1.0, start - i * step) for i in range(n)]


# ============================================================ analyst view
BULLISH = {"strong_buy": 12, "buy": 8, "hold": 3, "sell": 0, "strong_sell": 0}
BEARISH = {"strong_buy": 0, "buy": 1, "hold": 4, "sell": 6, "strong_sell": 3}


def test_consensus_reads_bullish_counts_as_a_buy():
    view = analyst.build("TEST", 100.0, BULLISH, {})
    assert view.consensus in ("Strong buy", "Buy")
    assert view.total_analysts == 23
    assert view.bullish_pct > 80
    assert view.status == "good"


def test_consensus_reads_bearish_counts_as_a_sell():
    view = analyst.build("TEST", 100.0, BEARISH, {})
    assert view.consensus in ("Sell", "Hold", "Strong sell")
    assert view.status in ("watch", "ok")


def test_upside_is_measured_from_the_current_price():
    view = analyst.build("TEST", 100.0, BULLISH, {"targetMeanPrice": 130.0})
    assert view.upside_pct == pytest.approx(30.0)


def test_wide_target_range_is_called_disagreement():
    view = analyst.build("TEST", 100.0, BULLISH,
                         {"targetMeanPrice": 130.0, "targetLowPrice": 60.0,
                          "targetHighPrice": 220.0})
    assert view.dispersion_pct == pytest.approx(160.0)
    assert "disagree" in view.agreement.lower()


def test_tight_target_range_is_called_agreement():
    view = analyst.build("TEST", 100.0, BULLISH,
                         {"targetMeanPrice": 110.0, "targetLowPrice": 100.0,
                          "targetHighPrice": 120.0})
    assert "agree" in view.agreement.lower()


def test_reality_check_backs_a_target_a_riser_actually_hits():
    view = analyst.build("UP", 100.0, BULLISH, {"targetMeanPrice": 105.0}, _rising())
    assert view.base_rate_pct is not None and view.base_rate_pct > 50
    assert "not a stretch" in view.reality_check


def test_reality_check_calls_out_a_target_the_stock_never_reaches():
    closes = _falling()
    view = analyst.build("DOWN", closes[-1], BULLISH,
                         {"targetMeanPrice": closes[-1] * 2}, closes)
    assert view.base_rate_pct == 0.0
    assert "almost never" in view.reality_check


def test_no_coverage_is_stated_plainly():
    view = analyst.build("TEST", 100.0, {}, {})
    assert view.consensus == "No coverage"
    assert "no analyst coverage" in view.summary.lower()


def test_summary_always_warns_that_targets_are_opinions():
    view = analyst.build("TEST", 100.0, BULLISH, {"targetMeanPrice": 130.0})
    assert "sentiment" in view.summary.lower()


# ========================================================= instant analyzer
GOOD_CO = {
    "shortName": "Good Co", "marketCap": 250e9, "trailingPE": 19.0,
    "profitMargins": 0.22, "revenueGrowth": 0.14, "returnOnEquity": 0.25,
    "debtToEquity": 60.0, "averageVolume": 9_000_000, "dividendYield": 0.012,
    "beta": 1.05,
}
WEAK_CO = {
    "shortName": "Weak Co", "marketCap": 900e6, "trailingPE": 90.0,
    "profitMargins": -0.15, "revenueGrowth": -0.08, "returnOnEquity": -0.10,
    "debtToEquity": 380.0, "averageVolume": 120_000,
}


def test_extract_converts_to_display_units():
    values = criteria.extract(GOOD_CO)
    assert values["market_cap_b"] == pytest.approx(250.0)
    assert values["profit_margin"] == pytest.approx(22.0)
    assert values["debt_to_equity"] == pytest.approx(0.6)


def test_extract_handles_dividend_yield_in_either_format():
    assert criteria.extract({"dividendYield": 0.031})["dividend_yield"] == pytest.approx(3.1)
    assert criteria.extract({"dividendYield": 3.1})["dividend_yield"] == pytest.approx(3.1)


def test_a_strong_company_passes_the_quality_preset():
    result = criteria.evaluate("GOOD", criteria.preset("Quality compounder"), GOOD_CO)
    assert result.verdict == "pass"
    assert result.score == 100.0


def test_a_weak_company_fails_clearly():
    result = criteria.evaluate("WEAK", criteria.preset("Quality compounder"), WEAK_CO)
    assert result.verdict == "fail"
    assert result.passed_count == 0


def test_a_near_miss_is_distinguished_from_a_real_failure():
    rules = [criteria.Criterion(field="profit_margin", op=">=", value=23.0)]
    near = criteria.evaluate("GOOD", rules, GOOD_CO)          # has 22%, wants 23%
    assert near.verdict == "near"
    assert near.rules[0].near_miss is True
    assert "only by" in near.rules[0].read

    strict = [criteria.Criterion(field="profit_margin", op=">=", value=60.0)]
    far = criteria.evaluate("GOOD", strict, GOOD_CO)
    assert far.verdict == "fail"
    assert far.rules[0].near_miss is False


def test_a_rule_with_no_data_is_skipped_not_failed():
    rules = [criteria.Criterion(field="peg", op="<=", value=2.0)]
    result = criteria.evaluate("GOOD", rules, GOOD_CO)   # no pegRatio in the dict
    assert result.measured_count == 0
    assert result.rules[0].measured is False
    assert "could not be checked" in result.rules[0].read


def test_less_than_rules_work_too():
    rules = [criteria.Criterion(field="pe", op="<=", value=20.0)]
    assert criteria.evaluate("GOOD", rules, GOOD_CO).verdict == "pass"
    assert criteria.evaluate("WEAK", rules, WEAK_CO).verdict == "fail"


def test_computed_fields_come_from_extras():
    rules = [criteria.Criterion(field="above_200dma", op=">=", value=1.0)]
    up = criteria.evaluate("X", rules, GOOD_CO, {"above_200dma": 1.0})
    down = criteria.evaluate("X", rules, GOOD_CO, {"above_200dma": 0.0})
    assert up.verdict == "pass" and down.verdict == "fail"


def test_every_preset_builds_valid_criteria():
    for name in criteria.PRESETS:
        rules = criteria.preset(name)
        assert rules
        for rule in rules:
            assert rule.field in criteria.FIELDS
            assert rule.op in criteria.OPS


def test_screen_sorts_the_best_first():
    results = criteria.screen({"WEAK": WEAK_CO, "GOOD": GOOD_CO},
                              criteria.preset("Quality compounder"))
    assert [r.symbol for r in results] == ["GOOD", "WEAK"]


# ========================================================= price calculator
def _inputs(**kwargs) -> fair_value.ValuationInputs:
    base = dict(symbol="TEST", eps=5.0, growth_pct=10.0, years=5, exit_pe=18.0,
                required_return_pct=12.0, current_price=100.0)
    base.update(kwargs)
    return fair_value.ValuationInputs(**base)


def test_future_earnings_compound_at_the_growth_rate():
    result = fair_value.project(_inputs())
    assert result.future_eps == pytest.approx(5.0 * 1.10 ** 5)
    assert result.future_price == pytest.approx(result.future_eps * 18.0)


def test_buy_below_discounts_the_future_price_back():
    result = fair_value.project(_inputs())
    assert result.buy_below == pytest.approx(result.future_price / 1.12 ** 5)


def test_demanding_a_higher_return_lowers_what_you_can_pay():
    patient = fair_value.project(_inputs(required_return_pct=8.0))
    greedy = fair_value.project(_inputs(required_return_pct=20.0))
    assert greedy.buy_below < patient.buy_below


def test_a_cheap_stock_is_marked_buy_and_an_expensive_one_is_not():
    cheap = fair_value.project(_inputs(current_price=40.0))
    dear = fair_value.project(_inputs(current_price=250.0))
    assert cheap.verdict == "buy" and cheap.margin_of_safety_pct > 0
    assert dear.verdict == "expensive"
    assert "overpaying" in dear.summary


def test_implied_growth_says_what_the_price_already_assumes():
    result = fair_value.project(_inputs(current_price=100.0))
    assert result.implied_growth_pct is not None
    # A dearer price has to assume faster growth to deliver the same return.
    dearer = fair_value.project(_inputs(current_price=160.0))
    assert dearer.implied_growth_pct > result.implied_growth_pct


def test_dividends_raise_what_you_can_afford_to_pay():
    plain = fair_value.project(_inputs())
    payer = fair_value.project(_inputs(dividend_yield_pct=3.0))
    assert payer.buy_below > plain.buy_below


def test_a_loss_making_company_is_refused_politely():
    result = fair_value.project(_inputs(eps=-2.0))
    assert "cannot say anything useful" in result.summary


def test_sensitivity_grid_spans_growth_and_multiple():
    grid = fair_value.sensitivity(_inputs())
    assert len(grid["rows"]) == 5
    assert len(grid["rows"][0]["cells"]) == 5
    # Higher growth and a higher exit multiple both mean you can pay more.
    low = grid["rows"][0]["cells"][0]["buy_below"]
    high = grid["rows"][-1]["cells"][-1]["buy_below"]
    assert high > low


def test_summary_always_flags_that_it_rests_on_two_guesses():
    assert "two guesses" in fair_value.project(_inputs()).summary


# ============================================================ options view
def _contract(kind: OptionType, strike: float, dte: int, iv: float = 0.30,
              premium: float = 5.0, oi: int = 500, volume: int = 100) -> OptionContract:
    return OptionContract(
        option_type=kind, strike=strike, expiration=f"2026-{(dte // 30) % 12 + 1:02d}-15",
        dte=dte, delta=0.5 if kind == OptionType.CALL else -0.5, iv=iv,
        bid=premium * 0.98, ask=premium * 1.02, open_interest=oi, volume=volume)


def _chain(spot: float = 100.0, iv: float = 0.30, dtes=(30, 180),
           call_volume: int = 100, put_volume: int = 100) -> OptionChain:
    contracts = []
    for dte in dtes:
        for strike in (90, 95, 100, 105, 110):
            contracts.append(_contract(OptionType.CALL, strike, dte, iv,
                                       volume=call_volume))
            contracts.append(_contract(OptionType.PUT, strike, dte, iv,
                                       volume=put_volume))
    return OptionChain(underlying="TEST", underlying_price=spot, contracts=contracts)


def test_expected_move_grows_with_time():
    view = options_view.build(_chain())
    near = next(e for e in view.expirations if e.dte == 30)
    far = next(e for e in view.expirations if e.dte == 180)
    assert far.expected_move_pct > near.expected_move_pct


def test_expected_move_matches_the_textbook_formula():
    view = options_view.build(_chain(spot=100.0, iv=0.30, dtes=(365,)))
    exp = view.expirations[0]
    assert exp.expected_move_pct == pytest.approx(30.0, abs=0.2)
    assert exp.upper == pytest.approx(130.0, abs=0.5)
    assert exp.lower == pytest.approx(70.0, abs=0.5)


def test_options_priced_far_above_realized_are_called_rich():
    calm = [100 + (0.05 if i % 2 else -0.05) for i in range(400)]
    view = options_view.build(_chain(iv=0.60), calm)
    assert view.richness == "Rich"
    assert "expensive" in view.richness_read


def test_options_priced_below_realized_are_called_cheap():
    wild = [100 + (6.0 if i % 2 else -6.0) for i in range(400)]
    view = options_view.build(_chain(iv=0.10), wild)
    assert view.richness == "Cheap"


def test_put_heavy_volume_reads_defensive():
    view = options_view.build(_chain(call_volume=50, put_volume=200))
    assert view.put_call_volume == pytest.approx(4.0)
    assert view.sentiment == "Defensive"


def test_call_heavy_volume_reads_bullish():
    view = options_view.build(_chain(call_volume=300, put_volume=60))
    assert view.sentiment in ("Bullish", "Very bullish")


def test_historical_move_beat_is_high_for_a_jumpy_stock():
    wild = [100 * (1.5 if i % 2 else 0.7) for i in range(400)]
    beat = options_view.historical_move_beat(wild, 30, 2.0)
    assert beat is not None and beat > 50


def test_historical_move_beat_needs_enough_history():
    assert options_view.historical_move_beat([100.0] * 20, 30, 5.0) is None


def test_chain_rows_stay_near_the_money():
    chain = _chain(spot=100.0)
    chain.contracts.append(_contract(OptionType.CALL, 500, 30))
    view = options_view.build(chain, target_dte=30)
    assert 500 not in [r.strike for r in view.rows]
    assert any(r.moneyness == "ATM" for r in view.rows)


def test_chain_rows_pair_calls_with_puts():
    view = options_view.build(_chain(), target_dte=30)
    row = next(r for r in view.rows if r.strike == 100)
    assert row.call_mid is not None and row.put_mid is not None


def test_empty_chain_is_handled():
    view = options_view.build(OptionChain(underlying="X", underlying_price=10.0,
                                          contracts=[]))
    assert view.expirations == []
    assert "no option data" in view.summary.lower()
