"""Payoff-at-expiration math, checked against the worked example in her SOP:
sell the $90 put for $2.50, buy the $85 put for $1.00 ->
max profit $150, max loss -$350, breakeven $88.50.
"""

from __future__ import annotations

from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.payoff import profile, value_at


def _pcs() -> Trade:
    return Trade(
        strategy_key="put_credit_spread", underlying="XYZ", contracts=1,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=90, premium=2.50, dte=45),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=85, premium=1.00, dte=45),
        ])


STRAT = {"family": "credit_spread"}


def test_sop_worked_example():
    trade = _pcs()
    assert value_at(trade, 95.0) == 150.0     # both puts expire worthless
    assert value_at(trade, 80.0) == -350.0    # full width lost, minus credit
    assert value_at(trade, 88.50) == 0.0      # breakeven

    p = profile(trade, STRAT)
    assert p.max_profit == 150.0
    assert p.max_loss == -350.0
    assert any(abs(b - 88.50) < 0.05 for b in p.breakevens)
    assert p.loss_grows_below is False        # defined risk - flat below 85
    assert p.loss_grows_above is False


def test_cash_secured_put_loss_grows_below():
    trade = Trade(
        strategy_key="cash_secured_put", underlying="XYZ", contracts=1,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=100, premium=2.0, dte=30)])
    p = profile(trade, {"family": "single_leg"})
    assert p.loss_grows_below is True
    assert any(abs(b - 98.0) < 0.05 for b in p.breakevens)
    assert p.max_profit == 200.0


def test_iron_condor_two_breakevens():
    trade = Trade(
        strategy_key="iron_condor", underlying="XYZ", contracts=1,
        legs=[
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=85, premium=1.0, dte=45),
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=90, premium=2.5, dte=45),
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=110, premium=2.5, dte=45),
            Leg(role="long_call", action=Action.BUY, option_type=OptionType.CALL,
                strike=115, premium=1.0, dte=45),
        ])
    p = profile(trade, STRAT)
    assert len(p.breakevens) == 2
    assert p.max_profit == 300.0              # both credits kept in the middle
    assert p.max_loss == -200.0               # one $5 side minus $3 credit


def test_covered_call_needs_share_price():
    legs = [Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=110, premium=2.0, dte=21)]
    strat = {"family": "covered_call", "requires_shares": True}
    without = Trade(strategy_key="covered_call_model_2", underlying="XYZ",
                    contracts=1, legs=legs)
    assert profile(without, strat) is None

    with_price = Trade(strategy_key="covered_call_model_2", underlying="XYZ",
                       contracts=1, legs=legs, underlying_price=100.0)
    p = profile(with_price, strat)
    assert p.includes_shares
    # above the strike the shares' gain is capped: (110-100) + 2 credit = $1,200
    assert value_at(with_price, 120.0, include_shares=True) == 1200.0
    assert value_at(with_price, 130.0, include_shares=True) == 1200.0


def test_contracts_scale_the_payoff():
    trade = _pcs()
    trade.contracts = 3
    assert value_at(trade, 95.0) == 450.0
    assert value_at(trade, 80.0) == -1050.0


def test_profile_none_without_strikes():
    empty = Trade(strategy_key="put_credit_spread", underlying="XYZ", legs=[])
    assert profile(empty, STRAT) is None
