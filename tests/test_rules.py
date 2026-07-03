"""Unit tests for the SOP rules engine.

These prove the "make sure I do it correctly" safety net works, with no live
market data - just hand-built trades. A valid trade passes; deliberately broken
trades each fail the correct rule.
"""

from __future__ import annotations

import pytest

from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.validator import validate_trade


# ---------- small helpers to build trades ----------

def put_credit_spread(
    underlying="SPX",
    short_strike=5000.0, short_delta=-0.08, short_premium=8.0,
    long_strike=4980.0, long_delta=-0.05, long_premium=5.0,
    dte=30, contracts=1,
) -> Trade:
    return Trade(
        strategy_key="put_credit_spread",
        underlying=underlying,
        contracts=contracts,
        underlying_price=5100.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=short_strike, delta=short_delta, premium=short_premium, dte=dte),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=long_strike, delta=long_delta, premium=long_premium, dte=dte),
        ],
    )


def iron_condor(short_call_delta=-0.08) -> Trade:
    return Trade(
        strategy_key="iron_condor",
        underlying="SPX",
        contracts=1,
        underlying_price=5100.0,
        legs=[
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=4980, delta=-0.05, premium=4.0, dte=30),
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=5000, delta=-0.08, premium=7.0, dte=30),
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=5200, delta=short_call_delta, premium=7.0, dte=30),
            Leg(role="long_call", action=Action.BUY, option_type=OptionType.CALL,
                strike=5220, delta=0.05, premium=4.0, dte=30),
        ],
    )


def _fail_names(report):
    return [r.name for r in report.results if r.status.value == "fail"]


# ---------- the tests ----------

def test_valid_put_credit_spread_passes():
    report = validate_trade(put_credit_spread())
    assert report.passed, f"expected pass, failed: {_fail_names(report)}"
    assert report.n_failed == 0


def test_short_leg_delta_too_high_fails():
    report = validate_trade(put_credit_spread(short_delta=-0.15))
    assert not report.passed
    assert any("delta under" in n.lower() for n in _fail_names(report))


def test_dte_out_of_window_fails():
    report = validate_trade(put_credit_spread(dte=10))
    assert not report.passed
    assert any("days to expiration" in n.lower() for n in _fail_names(report))


def test_debit_instead_of_credit_fails():
    # Buy leg more expensive than sell leg -> a debit, which is wrong for a credit spread.
    report = validate_trade(put_credit_spread(short_premium=5.0, long_premium=8.0))
    assert not report.passed
    assert any("credit" in n.lower() for n in _fail_names(report))


def test_over_monthly_bp_limit_fails():
    # 40 contracts x ~$1,700 risk each = ~$68k, over the $50k monthly limit.
    report = validate_trade(put_credit_spread(contracts=40))
    assert not report.passed
    assert any("buying power" in n.lower() for n in _fail_names(report))


def test_credit_spread_on_us_style_fails():
    # Credit spreads must use European-style, cash-settled names (no assignment risk).
    # A put credit spread on SPY (US-style) should be rejected.
    report = validate_trade(put_credit_spread(underlying="SPY"))
    assert not report.passed
    assert any("underlying" in n.lower() for n in _fail_names(report))


def test_credit_spread_allows_european_names():
    # SPX, NDX, RUT, XSP are all fine for credit spreads.
    for name in ("SPX", "NDX", "RUT", "XSP"):
        report = validate_trade(put_credit_spread(underlying=name))
        assert report.passed, f"{name} should be allowed: {_fail_names(report)}"


def test_wrong_underlying_for_covered_call_fails():
    # Covered calls need US-style shares you can own; SPX is cash-settled.
    trade = Trade(
        strategy_key="covered_call_model_1",
        underlying="SPX",
        contracts=1,
        legs=[
            Leg(role="long_put_protection", action=Action.BUY, option_type=OptionType.PUT,
                strike=500, delta=-0.5, premium=30.0, dte=500),
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=520, delta=0.30, premium=6.0, dte=21),
        ],
    )
    report = validate_trade(trade)
    assert not report.passed
    assert any("underlying" in n.lower() for n in _fail_names(report))


def test_iron_condor_checks_both_short_legs():
    # A 0.12-delta short call breaks the 0.10 limit even if the short put is fine.
    report = validate_trade(iron_condor(short_call_delta=-0.12))
    assert not report.passed
    assert any("short call delta under" in n.lower() for n in _fail_names(report))


def test_covered_call_reports_share_reminder_and_no_crash():
    trade = Trade(
        strategy_key="covered_call_model_1",
        underlying="SPY",
        contracts=1,
        legs=[
            Leg(role="long_put_protection", action=Action.BUY, option_type=OptionType.PUT,
                strike=500, delta=-0.5, premium=30.0, dte=500),
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=520, delta=0.30, premium=6.0, dte=21),
        ],
    )
    report = validate_trade(trade)
    assert report.passed  # correct underlying, on-target delta, share reminder is INFO
    assert any("100 shares" in r.message for r in report.results)


def test_all_eight_strategies_validate_without_error():
    from src.engine.config_loader import load_strategies
    for key in load_strategies():
        # Minimal one-leg trade just to confirm no rule crashes on any strategy shape.
        trade = Trade(
            strategy_key=key,
            underlying="SPY",
            contracts=1,
            legs=[Leg(role="probe", action=Action.SELL, option_type=OptionType.PUT,
                      strike=500, delta=-0.09, premium=5.0, dte=45)],
        )
        report = validate_trade(trade)
        assert report.strategy_key == key
        assert len(report.results) > 0
