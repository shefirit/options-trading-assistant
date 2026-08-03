"""The Wheel: a cash secured put that is ALLOWED to be assigned.

Rita's ruling (2026-08-03): a Wheel put has no stop loss and no 21-day time
exit, because both close the put before assignment can happen - and assignment
is the doorway to the covered call phase, not a failure. These tests pin that
down, because a stop quietly reappearing in config would break the strategy
without breaking anything else.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.engine import exit_rules
from src.engine.config_loader import (
    allowed_underlyings_for,
    get_strategy,
    underlying_fits_style,
)
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import Position
from src.engine.validator import validate_trade


def _wheel_position(strike: float = 50.0, credit: float = 120.0,
                    dte_left: int = 12) -> Position:
    """An open Wheel put with `dte_left` days to run."""
    today = date(2026, 8, 3)
    return Position(
        trade_id="W1", underlying="TESTCO", strategy_key="wheel",
        strategy_name="The Wheel (CSP into Covered Call)",
        credit=credit, contracts=1, buying_power=strike * 100 - credit,
        opened=today - timedelta(days=23),
        expiration=today + timedelta(days=dte_left),
        dte_at_entry=35,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=strike, delta=-0.28, premium=1.20, dte=35)])


def _cfg():
    return get_strategy("wheel")["exit"]


# ---------------- the config itself ----------------

def test_the_wheel_has_no_stop_loss_and_no_time_exit():
    exit_cfg = _cfg()
    assert "stop_loss_multiple" not in exit_cfg
    assert "time_exit_dte" not in exit_cfg
    assert exit_cfg["profit_target_pct"] == 50
    assert exit_cfg["accepts_assignment"] is True


def test_the_wheel_is_a_us_style_strategy():
    """You cannot own shares of an index, so a Wheel needs a stock or ETF."""
    assert underlying_fits_style("wheel", "SOFI") is True
    assert underlying_fits_style("wheel", "SPY") is True
    assert underlying_fits_style("wheel", "SPX") is False


# ---------------- exits ----------------

def test_a_big_loss_does_not_trigger_a_stop():
    """Same position on a regular CSP would be a red 'stop loss hit'. On a
    Wheel it must not be - there is no stop to hit."""
    pos = _wheel_position(credit=120.0)
    # Costs $400 to close against a $120 credit: a 2.3x loss, well past 2x.
    signal = exit_rules.evaluate(pos, _cfg(), current_cost=400.0,
                                 today=date(2026, 8, 3))
    assert signal.action != "stop"


def test_reaching_21_dte_does_not_force_an_exit():
    pos = _wheel_position(dte_left=21)
    signal = exit_rules.evaluate(pos, _cfg(), current_cost=60.0,
                                 today=date(2026, 8, 3))
    assert signal.action != "time"


def test_the_50_percent_profit_target_still_works():
    pos = _wheel_position(credit=120.0)
    signal = exit_rules.evaluate(pos, _cfg(), current_cost=55.0,
                                 today=date(2026, 8, 3))
    assert signal.action == "profit"


# ---------------- wording: assignment is the plan ----------------

def test_price_below_the_strike_reads_as_the_plan_not_as_trouble():
    pos = _wheel_position(strike=50.0)
    signal = exit_rules.evaluate(pos, _cfg(), current_cost=200.0,
                                 underlying_price=47.0, today=date(2026, 8, 3))
    text = " ".join([signal.reason] + list(signal.notes))
    assert "in trouble" not in text
    assert "cost basis" in text


def test_a_regular_csp_still_calls_it_trouble():
    """The old wording must survive for the strategy that still avoids
    assignment - this is a per-strategy flag, not a global change."""
    notes = exit_rules._strike_notes(
        _wheel_position(strike=50.0), 47.0, accepts_assignment=False)
    assert any("in trouble" in n for n in notes)


# ---------------- the SOP checklist ----------------

def test_the_checklist_passes_a_wheel_on_a_normal_stock():
    trade = Trade(
        strategy_key="wheel", underlying="SOFI", contracts=1,
        underlying_price=16.31,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=15.0, delta=-0.28, premium=0.55, dte=35)])
    report = validate_trade(trade)
    underlying = next(r for r in report.results
                      if r.name == "Right underlying for this strategy")
    assert underlying.status.value == "pass"


def test_the_wheel_is_offered_on_stocks_and_etfs():
    allowed = allowed_underlyings_for("wheel")
    assert "SPY" in allowed
    assert "AAPL" in allowed
    assert "SPX" not in allowed
