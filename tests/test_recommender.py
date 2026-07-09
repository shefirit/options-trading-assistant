"""Recommender tests - pure logic against the saved fixtures (no live connection)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from src.data.chain import OptionChain
from src.data.market_context import build_context
from src.data.premium_finder import PremiumSnapshot
from src.engine import recommender as rec
from src.engine.models import Candidate, Trade
from src.engine.recommender import DividendView, IncomePick, MonthlyTarget

FIXTURE = Path(__file__).parent / "fixtures" / "spx_chain.json"


@pytest.fixture(scope="module")
def chain() -> OptionChain:
    return OptionChain.from_json(FIXTURE)


# ------------------------------------------------------------------ monthly target
def test_monthly_target_rolls_past_a_too_close_monthly():
    """July 17 is only 9 days out on July 8 - the SOP never enters that close,
    so the target rolls to the August monthly."""
    mt = rec.monthly_target(dt.date(2026, 7, 8))
    assert mt.expiration == dt.date(2026, 8, 21)
    assert mt.dte == 44
    assert mt.within_sop


def test_monthly_target_keeps_a_monthly_exactly_at_the_floor():
    mt = rec.monthly_target(dt.date(2026, 8, 28))   # Sep 18 = exactly 21 days out
    assert mt.expiration == dt.date(2026, 9, 18)
    assert mt.dte == 21


def test_monthly_target_shifts_off_a_holiday_friday():
    """April 2019's third Friday was Good Friday - expiration moves to Thursday."""
    mt = rec.monthly_target(dt.date(2019, 4, 1), dte_min=10)
    assert mt.expiration == dt.date(2019, 4, 18)


def test_monthly_target_flags_a_monthly_past_the_preferred_window():
    """A 5-week gap between monthlies can push the rolled target past dte_max."""
    mt = rec.monthly_target(dt.date(2026, 9, 26))   # Oct 16 = 20d -> rolls to Nov 20 = 55d
    assert mt.expiration == dt.date(2026, 11, 20)
    assert mt.dte == 55
    assert not mt.within_sop
    assert "past your preferred" in mt.label


# ------------------------------------------------------------------ exact expiration filter
def test_chain_for_expiration_keeps_only_that_date(chain):
    monthly = rec.chain_for_expiration(chain, dt.date(2026, 8, 16))
    assert monthly.contracts, "fixture should contain the 2026-08-16 expiration"
    assert {c.expiration for c in monthly.contracts} == {"2026-08-16"}
    assert {c.dte for c in monthly.contracts} == {45}
    assert monthly.underlying == chain.underlying
    assert monthly.underlying_price == chain.underlying_price


def test_chain_for_expiration_missing_date_is_empty(chain):
    assert rec.chain_for_expiration(chain, dt.date(2030, 1, 1)).contracts == []


# ------------------------------------------------------------------ dividends
def test_dividend_from_dollar_rate_is_preferred():
    dv = rec.dividend_view({"trailingAnnualDividendRate": 6.08, "dividendYield": 45.0}, 460.0)
    assert dv.pays
    assert dv.yield_pct == pytest.approx(1.32, abs=0.01)
    assert "6.08" in dv.note


def test_dividend_yield_fraction_and_percent_forms_both_normalize():
    old_form = rec.dividend_view({"dividendYield": 0.0132}, 460.0)   # older yfinance: fraction
    new_form = rec.dividend_view({"dividendYield": 1.32}, 460.0)    # newer yfinance: percent
    assert old_form.yield_pct == pytest.approx(1.32, abs=0.01)
    assert new_form.yield_pct == pytest.approx(1.32, abs=0.01)


def test_dividend_junk_and_missing_values():
    assert not rec.dividend_view({"dividendYield": 45.0}, 100.0).pays   # implausible -> junk
    none = rec.dividend_view({}, 100.0)
    assert not none.pays
    assert "No dividend" in none.note


def test_dividend_ex_date_parses_epoch_seconds():
    epoch = dt.datetime(2026, 8, 7, tzinfo=dt.timezone.utc).timestamp()
    dv = rec.dividend_view({"trailingAnnualDividendRate": 2.0, "exDividendDate": epoch}, 100.0)
    assert dv.ex_div_date == dt.date(2026, 8, 7)


# ------------------------------------------------------------------ strategy fit
def _snap(**kw) -> PremiumSnapshot:
    base = dict(symbol="SPY", price=510.0, dte=45, short_strike=490.0, short_delta=0.30,
                credit=5.0, credit_dollars=500.0, monthly_yield_pct=1.02, trend="up",
                verdict="sell", verdict_reason="Fair premium on a strong name.",
                liquidity="Good", richness="Fair")
    base.update(kw)
    return PremiumSnapshot(**base)


def test_fitting_strategy_downtrend_picks_a_covered_call_model():
    assert rec.fitting_strategy_key(_snap(trend="down"), 50_000, vix=25) == "covered_call_model_1"
    assert rec.fitting_strategy_key(_snap(trend="down"), 50_000, vix=15) == "covered_call_model_2"
    assert rec.fitting_strategy_key(_snap(trend="down"), 50_000) == "covered_call_model_2"


def test_fitting_strategy_expensive_name_points_to_pmcc():
    assert rec.fitting_strategy_key(_snap(short_strike=600.0), 50_000) == "poor_mans_covered_call"


def test_fitting_strategy_default_is_the_cash_secured_put():
    assert rec.fitting_strategy_key(_snap(), 50_000) == "cash_secured_put"


# ------------------------------------------------------------------ SOP wording
def test_sop_summary_reads_the_put_credit_spread_rules_from_config():
    from src.engine.config_loader import get_strategy
    joined = " ".join(rec.sop_summary(get_strategy("put_credit_spread")))
    assert "0.25" in joined
    assert "21-45" in joined
    assert "30-49" in joined            # the US-style early-assignment adjustment
    assert "2x the credit" in joined
    assert "21 days left" in joined
    assert "50% of the credit" in joined


# ------------------------------------------------------------------ index picks
TODAY = dt.date(2026, 7, 8)
MONTHLY_45 = MonthlyTarget(expiration=dt.date(2026, 8, 16), dte=45, label="test monthly")


def test_build_index_pick_scans_a_real_monthly_setup(chain):
    ctx = build_context("SPX", 5100.0, vix=18.0, trend="up")
    monthly_chain = rec.chain_for_expiration(chain, dt.date(2026, 8, 16))
    pick = rec.build_index_pick("SPX", ctx, monthly_chain, hv=0.15,
                                monthly=MONTHLY_45, today=TODAY)
    assert pick.strategy_key == "put_credit_spread"      # uptrend -> the SOP's bullish spread
    assert pick.candidate is not None
    assert pick.candidate.dte == 45                       # the monthly, not a nearby weekly
    assert pick.candidate.credit > 0
    assert pick.candidate.max_loss > 0
    assert pick.candidate.buying_power > 0
    assert pick.error == ""
    assert pick.richness in ("Rich", "Fair", "Thin", "n/a")
    assert pick.why and pick.sop_notes


def test_build_index_pick_falls_back_when_monthly_is_outside_the_sop_window(chain):
    """The 60-day expiration breaks the 21-45 rule, so with a fallback chain the
    pick uses a window-fitting expiration and says so."""
    ctx = build_context("SPX", 5100.0, vix=18.0, trend="up")
    monthly_60 = rec.chain_for_expiration(chain, dt.date(2026, 8, 31))
    monthly = MonthlyTarget(expiration=dt.date(2026, 8, 31), dte=60, label="too far")

    without = rec.build_index_pick("SPX", ctx, monthly_60, hv=0.15,
                                   monthly=monthly, today=TODAY)
    assert without.candidate is None
    assert without.error

    with_fb = rec.build_index_pick("SPX", ctx, monthly_60, hv=0.15,
                                   monthly=monthly, today=TODAY, fallback_chain=chain)
    assert with_fb.candidate is not None
    assert 21 <= with_fb.candidate.dte <= 45
    assert "instead" in with_fb.expiry_note


# ------------------------------------------------------------------ income picks
def test_build_income_pick_csp_math_and_dividend():
    monthly = MonthlyTarget(expiration=TODAY + dt.timedelta(days=45), dte=45, label="t")
    pick = rec.build_income_pick(_snap(), "etf", {"trailingAnnualDividendRate": 6.5},
                                 monthly, monthly_bp=50_000, bp_limit=50_000,
                                 vix=18.0, today=TODAY)
    assert pick.strategy_key == "cash_secured_put"
    assert pick.bp_required == 48_500                     # 490*100 - 500 credit
    assert pick.bp_pct_of_limit == pytest.approx(97.0)
    assert pick.dividend.pays
    assert pick.dividend.yield_pct == pytest.approx(1.27, abs=0.01)
    assert not any("earnings date" in w for w in pick.warnings)   # ETFs have no earnings
    assert pick.why and pick.sop_notes


def test_build_income_pick_stock_without_earnings_date_warns_honestly():
    monthly = MonthlyTarget(expiration=TODAY + dt.timedelta(days=45), dte=45, label="t")
    pick = rec.build_income_pick(_snap(symbol="AAPL"), "stock", {}, monthly,
                                 monthly_bp=50_000, bp_limit=50_000, today=TODAY)
    assert any("earnings date" in w for w in pick.warnings)


def test_build_income_pick_notes_expiry_drift_off_the_monthly():
    monthly = MonthlyTarget(expiration=dt.date(2026, 8, 22), dte=45, label="t")
    pick = rec.build_income_pick(_snap(dte=30), "etf", {}, monthly,
                                 monthly_bp=50_000, bp_limit=50_000, today=TODAY)
    assert any("not the" in w and "monthly" in w for w in pick.warnings)


def test_build_income_pick_warns_on_ex_dividend_for_short_call_strategies():
    monthly = MonthlyTarget(expiration=TODAY + dt.timedelta(days=45), dte=45, label="t")
    epoch = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc).timestamp()
    info = {"trailingAnnualDividendRate": 3.0, "exDividendDate": epoch}

    cc = rec.build_income_pick(_snap(trend="down"), "etf", info, monthly,
                               monthly_bp=50_000, bp_limit=50_000, vix=15.0, today=TODAY)
    assert cc.strategy_key == "covered_call_model_2"
    assert cc.bp_required == 51_000                       # 100 shares at $510
    assert any("assigned early" in w for w in cc.warnings)

    csp = rec.build_income_pick(_snap(), "etf", info, monthly,
                                monthly_bp=50_000, bp_limit=50_000, today=TODAY)
    assert not any("assigned early" in w for w in csp.warnings)


def test_build_income_pick_survives_an_error_snapshot():
    monthly = MonthlyTarget(expiration=TODAY + dt.timedelta(days=45), dte=45, label="t")
    pick = rec.build_income_pick(PremiumSnapshot(symbol="ZZZZ", error="No option data."),
                                 "stock", {}, monthly, monthly_bp=50_000, bp_limit=50_000,
                                 today=TODAY)
    assert pick.snapshot.error
    assert pick.why == []


# ------------------------------------------------------------------ ranking
def _income(verdict: str, yield_pct: float, div: float | None, error: str = "") -> IncomePick:
    return IncomePick(
        snapshot=PremiumSnapshot(symbol="X", verdict=verdict,
                                 monthly_yield_pct=yield_pct, error=error),
        kind="etf", strategy_key="cash_secured_put",
        dividend=DividendView(pays=div is not None, yield_pct=div),
    )


def test_rank_income_verdict_dominates_and_dividend_breaks_near_ties():
    a = _income("sell", 1.0, None)
    b = _income("sell", 1.2, 3.0)     # same 0.5% bucket as a -> dividend wins
    c = _income("okay", 5.0, 5.0)     # huge yield cannot beat a better verdict
    d = _income("sell", 2.0, None)    # clearly higher yield bucket -> beats the dividend payer
    e = _income("sell", 9.9, 9.9, error="boom")   # errors sink to the bottom

    ranked = rec.rank_income_picks([a, b, c, d, e])
    assert [p.snapshot.monthly_yield_pct for p in ranked] == [2.0, 1.2, 1.0, 5.0, 9.9]


def test_keep_best_drops_skips_and_thin_indexes_with_reasons():
    def cand(fits=True) -> Candidate:
        return Candidate(trade=Trade(strategy_key="put_credit_spread", underlying="SPX"),
                         credit=100, max_loss=1000, buying_power=1000,
                         return_on_risk=0.1, short_delta=0.2, fits_sop=fits)

    good_ix = rec.IndexPick(symbol="SPX", strategy_key="put_credit_spread",
                            strategy_name="PCS", candidate=cand(), liquidity="Good")
    thin_ix = rec.IndexPick(symbol="NDX", strategy_key="put_credit_spread",
                            strategy_name="PCS", candidate=cand(), liquidity="Thin")
    nosetup_ix = rec.IndexPick(symbol="RUT", strategy_key="iron_condor",
                               strategy_name="IC", candidate=None,
                               error="No setup at your SOP delta.")

    sell = _income("sell", 1.5, None)
    okay = _income("okay", 1.0, None)
    skip = IncomePick(
        snapshot=PremiumSnapshot(symbol="ZZ", verdict="skip",
                                 verdict_reason="Hard to trade - wide bid-ask spread."),
        kind="etf", strategy_key="cash_secured_put", dividend=DividendView())

    kept_ix, kept_inc, kept_bear, left_out = rec.keep_best(
        [good_ix, thin_ix, nosetup_ix], [sell, okay, skip])
    assert [p.symbol for p in kept_ix] == ["SPX"]
    assert [p.snapshot.verdict for p in kept_inc] == ["sell", "okay"]
    assert kept_bear == []
    joined = " ".join(left_out)
    assert "NDX" in joined and "hard to trade" in joined.lower()
    assert "RUT" in joined and "No setup" in joined
    assert "ZZ" in joined and "wide bid-ask" in joined


def test_keep_best_filters_bearish_picks_like_indexes():
    def cand() -> Candidate:
        return Candidate(trade=Trade(strategy_key="call_credit_spread", underlying="NVDA"),
                         credit=80, max_loss=420, buying_power=420,
                         return_on_risk=0.19, short_delta=0.1, fits_sop=True)
    good = rec.IndexPick(symbol="NVDA", american=True, strategy_key="call_credit_spread",
                         strategy_name="CCS", candidate=cand(), liquidity="Good")
    thin = rec.IndexPick(symbol="AVGO", american=True, strategy_key="call_credit_spread",
                         strategy_name="CCS", candidate=cand(), liquidity="Thin")
    _, _, kept_bear, left_out = rec.keep_best([], [], [good, thin])
    assert [p.symbol for p in kept_bear] == ["NVDA"]
    assert any("AVGO (bearish)" in s for s in left_out)


def test_is_strong_bearish_stock_gate():
    big = {"NVDA", "MSFT"}
    # A big, downtrending stock qualifies (gated on market cap, not the grade -
    # a downtrend docks the grade and the hosted app throttles fundamentals).
    assert rec.is_strong_bearish_stock("stock", "NVDA", "down", big)
    assert rec.is_strong_bearish_stock("stock", "msft", "down", big)   # case-insensitive
    # Disqualifiers, one each:
    assert not rec.is_strong_bearish_stock("stock", "NVDA", "up", big)     # not down
    assert not rec.is_strong_bearish_stock("etf", "NVDA", "down", big)     # not a stock
    assert not rec.is_strong_bearish_stock("stock", "F", "down", big)      # not biggest


def test_build_bearish_stock_pick_is_a_call_credit_spread(chain):
    """A downtrend context on a (stock) chain yields a bear Call Credit Spread,
    flagged american, with earnings-in-window warned per the SOP."""
    down_ctx = build_context("NVDA", 5100.0, vix=18.0, trend="down")
    assert down_ctx.best_strategy_key == "call_credit_spread"
    monthly_chain = rec.chain_for_expiration(chain, dt.date(2026, 8, 16))
    earnings = TODAY + dt.timedelta(days=20)   # inside the ~45-day trade
    pick = rec.build_index_pick("NVDA", down_ctx, monthly_chain, hv=0.4,
                                monthly=MONTHLY_45, today=TODAY,
                                earnings_date=earnings, american=True)
    assert pick.american
    assert pick.strategy_key == "call_credit_spread"
    assert pick.candidate is not None
    short = pick.candidate.trade.short_legs[0]
    assert short.option_type.value == "call"       # a bear CALL spread
    assert pick.candidate.credit > 0 and pick.candidate.max_loss > 0
    assert any("no credit spreads through earnings" in w for w in pick.warnings)


def test_rank_index_fitting_setups_first_errors_last():
    def cand(fits: bool, ror: float) -> Candidate:
        return Candidate(trade=Trade(strategy_key="put_credit_spread", underlying="SPX"),
                         credit=100, max_loss=1000, buying_power=1000,
                         return_on_risk=ror, short_delta=0.2, fits_sop=fits)

    def pick(c, err="") -> rec.IndexPick:
        return rec.IndexPick(symbol="SPX", strategy_key="put_credit_spread",
                             strategy_name="PCS", candidate=c, error=err)

    fit = pick(cand(True, 0.10))
    richer_fit = pick(cand(True, 0.20))
    near_miss = pick(cand(False, 0.50))
    broken = pick(None, err="no data")

    ranked = rec.rank_index_picks([broken, near_miss, fit, richer_fit])
    assert ranked[0] is richer_fit
    assert ranked[1] is fit
    assert ranked[2] is near_miss
    assert ranked[3] is broken
