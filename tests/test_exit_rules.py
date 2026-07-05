"""The exit-signal engine: her SOP's exits applied to live numbers.

Priority when several trigger: stop > time > profit > watch > hold.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.engine.exit_rules import evaluate
from src.engine.models import Action, Leg, OptionType
from src.engine.positions import Position

TODAY = date(2026, 7, 5)
EXIT_CFG = {"profit_target_pct": 50, "stop_loss_multiple": 2.0, "time_exit_dte": 21}


def _position(credit=300.0, dte_left=40, dte_at_entry=45, short_strike=5000.0):
    return Position(
        trade_id="T1", underlying="SPX", strategy_key="put_credit_spread",
        strategy_name="Put Credit Spread", opened=TODAY - timedelta(days=5),
        expiration=TODAY + timedelta(days=dte_left), dte_at_entry=dte_at_entry,
        contracts=1, credit=credit,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=short_strike, premium=8.0, dte=dte_at_entry),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=short_strike - 25, premium=5.0, dte=dte_at_entry),
        ])


def test_profit_target_reached():
    sig = evaluate(_position(), EXIT_CFG, current_cost=150.0,
                   underlying_price=5200.0, today=TODAY)
    assert sig.action == "profit"
    assert sig.pl_dollars == 150.0
    assert sig.profit_pct == 50.0


def test_stop_loss_hit():
    # collected 300, costs 900 to close -> loss 600 = 2x the credit
    sig = evaluate(_position(), EXIT_CFG, current_cost=900.0,
                   underlying_price=5200.0, today=TODAY)
    assert sig.action == "stop"
    assert sig.tone == "red"
    assert sig.pl_dollars == -600.0


def test_stop_beats_time_exit():
    sig = evaluate(_position(dte_left=10), EXIT_CFG, current_cost=900.0,
                   underlying_price=5200.0, today=TODAY)
    assert sig.action == "stop"


def test_time_exit_at_21_dte():
    sig = evaluate(_position(dte_left=21), EXIT_CFG, current_cost=250.0,
                   underlying_price=5200.0, today=TODAY)
    assert sig.action == "time"
    assert "21" in sig.headline or "21" in sig.reason


def test_entered_inside_21_dte_downgrades_to_watch():
    """Indexes may enter at 21 DTE - then the time rule can't mean 'close on
    day one'; it becomes an active-management warning instead."""
    sig = evaluate(_position(dte_left=20, dte_at_entry=21), EXIT_CFG,
                   current_cost=250.0, underlying_price=5200.0, today=TODAY)
    assert sig.action == "watch"
    assert "entered inside" in sig.reason


def test_watch_when_price_near_short_strike():
    # 5040 is within 1.5% of the 5000 short put
    sig = evaluate(_position(), EXIT_CFG, current_cost=250.0,
                   underlying_price=5040.0, today=TODAY)
    assert sig.action == "watch"
    assert "1.5%" in sig.reason


def test_watch_when_price_crossed_short_strike():
    sig = evaluate(_position(), EXIT_CFG, current_cost=250.0,
                   underlying_price=4980.0, today=TODAY)
    assert sig.action == "watch"
    assert "BELOW" in sig.reason


def test_watch_on_short_call_side():
    p = Position(
        trade_id="T2", underlying="SPX", strategy_key="call_credit_spread",
        strategy_name="Call Credit Spread", opened=TODAY - timedelta(days=5),
        expiration=TODAY + timedelta(days=40), dte_at_entry=45, contracts=1,
        credit=300.0,
        legs=[Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                  strike=5300, premium=6.0, dte=45)])
    sig = evaluate(p, EXIT_CFG, current_cost=250.0,
                   underlying_price=5320.0, today=TODAY)
    assert sig.action == "watch"
    assert "ABOVE" in sig.reason


def test_watch_on_delta_red_flag():
    sig = evaluate(_position(), EXIT_CFG, current_cost=250.0,
                   underlying_price=5200.0, short_delta=0.35, today=TODAY)
    assert sig.action == "watch"
    assert "0.35" in sig.reason


def test_hold_when_nothing_triggered():
    sig = evaluate(_position(), EXIT_CFG, current_cost=260.0,
                   underlying_price=5200.0, today=TODAY)
    assert sig.action == "hold"
    assert sig.profit_pct is not None and 13 <= sig.profit_pct <= 14


def test_unpriced_when_no_live_cost():
    sig = evaluate(_position(), EXIT_CFG, current_cost=None,
                   underlying_price=None, today=TODAY)
    assert sig.action == "unpriced"


def test_time_and_strike_checks_still_work_without_pricing():
    sig = evaluate(_position(dte_left=15), EXIT_CFG, current_cost=None,
                   underlying_price=None, today=TODAY)
    assert sig.action == "time"    # the day count needs no option prices
