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
    # SOP put-spread limit is 0.25; a 0.35-delta short put is too close to the money.
    report = validate_trade(put_credit_spread(short_delta=-0.35))
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


def test_credit_spread_us_style_allowed_but_avoids_21_dte():
    # SOP: credit spreads may use any liquid stock/ETF/index, but US-style names
    # must enter nearer 45 DTE (no 21-DTE early-assignment zone); indices may use 21.
    ok = validate_trade(put_credit_spread(underlying="SPY", dte=40))
    assert ok.passed, f"SPY at 40 DTE should pass: {_fail_names(ok)}"
    early = validate_trade(put_credit_spread(underlying="SPY", dte=21))
    assert not early.passed
    assert any("days to expiration" in n.lower() for n in _fail_names(early))
    # A European index is fine entering at 21 DTE.
    idx = validate_trade(put_credit_spread(underlying="SPX", dte=21))
    assert idx.passed, f"SPX at 21 DTE should pass: {_fail_names(idx)}"


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
    # A 0.20-delta short call breaks the SOP's 0.15 per-leg limit even if the put is fine.
    report = validate_trade(iron_condor(short_call_delta=-0.20))
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


# ---------- room before the time exit ----------
def _runway_check(report):
    # "Room before your 21-day time exit" - not the plain "Time exit at 21 DTE"
    # reminder, which every one of these strategies also carries.
    return next((r for r in report.results if r.name.startswith("Room before")), None)


def test_entry_near_the_time_exit_is_flagged():
    # The trap: 23 DTE passes the 21-45 range check, and the 21-DTE time exit
    # then says close it in two days. The range rule alone never says that.
    report = validate_trade(put_credit_spread(underlying="SPX", dte=23))
    check = _runway_check(report)
    assert check is not None
    assert check.status.value == "warn"
    assert "2 days" in check.message
    assert any("days to expiration" in r.name.lower() and r.status.value == "pass"
               for r in report.results), "the range check should still pass"


def test_entry_at_the_time_exit_is_flagged_but_never_blocks():
    # Her SOP explicitly allows a European index at 21 DTE, so the app warns
    # loudly and still lets the trade through - it does not overrule her rule.
    report = validate_trade(put_credit_spread(underlying="SPX", dte=21))
    check = _runway_check(report)
    assert check is not None
    assert check.status.value == "warn"
    assert report.passed


def test_a_45_day_entry_has_room_and_passes():
    report = validate_trade(put_credit_spread(underlying="SPX", dte=45))
    check = _runway_check(report)
    assert check is not None
    assert check.status.value == "pass"
    assert "24 days" in check.message


def test_covered_call_has_no_runway_check():
    # A covered call's 21-day short call sits against shares you keep and roll,
    # so "you would close it the day you open it" would be nonsense there.
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
    assert _runway_check(report) is None
    assert report.passed


def test_runway_warning_wording_scales_with_the_squeeze():
    # 2 days really is "closing it as you open it"; 9 days is merely tight.
    # Wording both the same way is how a warning stops meaning anything.
    tight = _runway_check(validate_trade(put_credit_spread(underlying="SPX", dte=23)))
    short = _runway_check(validate_trade(put_credit_spread(underlying="SPX", dte=30)))
    assert tight.status.value == short.status.value == "warn"
    assert "almost as soon as you open it" in tight.message
    assert "almost as soon as you open it" not in short.message
    assert "short runway" in short.message


# ---------- one profit-target number, not two ----------
def test_profit_target_is_the_same_number_in_both_places():
    """The checklist and the TOS-alert card each used to work this out on their
    own, and could land on different dollars for the same trade. She types the
    number into an alert, so they have to agree."""
    from src.engine.rules import profit_target_keep

    for credit in (415.0, 415.00000000000006, 414.999999, 207.5, 833.33, 1000.0, 5.0):
        keep = profit_target_keep(credit, 50)
        cost = round(credit, 2) - keep            # what the card shows
        assert f"{keep:,.0f}" == f"{cost:,.0f}", f"disagreed on a {credit} credit"
        assert abs(keep + cost - round(credit, 2)) < 0.005


def test_profit_target_keep_handles_other_percentages():
    from src.engine.rules import profit_target_keep

    assert profit_target_keep(400.0, 50) == 200.0
    assert profit_target_keep(400.0, 25) == 100.0
    assert profit_target_keep(333.0, 75) == 249.75
