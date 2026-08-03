"""Which tickers a strategy may run on, including ones typed by hand.

The universe files only cover the S&P 500 and Nasdaq-100, so a liquid name
outside them (SOFI) never appeared in any picker. Once the pickers accept a
typed ticker, the strategy-switch cleanup has to judge it by option style
instead of by "is it in our list", or it silently throws the name away.
"""

from __future__ import annotations

from src.engine.config_loader import underlying_fits_style
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.validator import validate_trade


def _underlying_check(report):
    return next(r for r in report.results
                if r.name == "Right underlying for this strategy")


def test_a_stock_outside_the_index_lists_is_allowed():
    """The case that started this: SOFI is in neither index universe file."""
    assert underlying_fits_style("cash_secured_put", "SOFI") is True
    assert underlying_fits_style("put_credit_spread", "SOFI") is True
    assert underlying_fits_style("covered_call_model_2", "SOFI") is True


def test_us_style_strategies_still_reject_a_cash_settled_index():
    """You cannot be assigned shares of SPX, so a covered call or CSP on it is
    not a real trade - switching to one must still drop the index."""
    assert underlying_fits_style("cash_secured_put", "SPX") is False
    assert underlying_fits_style("poor_mans_covered_call", "NDX") is False
    assert underlying_fits_style("covered_call_model_1", "RUT") is False


def test_credit_spreads_take_either_side():
    """Her SOP: any liquid stock, ETF, or index."""
    assert underlying_fits_style("put_credit_spread", "SPX") is True
    assert underlying_fits_style("iron_condor", "SPY") is True
    assert underlying_fits_style("iron_condor", "XSP") is True


def test_case_does_not_matter():
    assert underlying_fits_style("cash_secured_put", "spx") is False
    assert underlying_fits_style("cash_secured_put", "sofi") is True


def test_the_sop_checklist_passes_a_stock_outside_the_index_lists():
    """The checklist judged by "is it in our universe file", so a cash secured
    put on SOFI came back red even though the SOP allows any liquid stock."""
    trade = Trade(
        strategy_key="cash_secured_put", underlying="SOFI", contracts=1,
        underlying_price=16.31,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=15.0, delta=-0.28, premium=0.55, dte=30)])
    check = _underlying_check(validate_trade(trade))
    assert check.status.value == "pass"
    assert "not allowed" not in check.message


def test_the_sop_checklist_still_rejects_an_index_you_cannot_own():
    trade = Trade(
        strategy_key="cash_secured_put", underlying="SPX", contracts=1,
        underlying_price=7490.0,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=7000.0, delta=-0.28, premium=30.0, dte=30)])
    check = _underlying_check(validate_trade(trade))
    assert check.status.value == "fail"
