"""Mapping a trade into Rita's App Trades (M(1)) columns."""

from __future__ import annotations

from src.engine.models import Action, Leg, OptionType, Trade
from src.logging_tools import app_trades


def _call_spread():
    return Trade(
        strategy_key="call_credit_spread", underlying="SPX", contracts=3,
        legs=[
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=7950, delta=0.10, premium=6.0, dte=30),
            Leg(role="long_call", action=Action.BUY, option_type=OptionType.CALL,
                strike=8000, delta=0.06, premium=4.6, dte=30),
        ])


def test_strategy_codes_match_her_dropdown():
    assert app_trades.strategy_code("put_credit_spread") == "CS"
    assert app_trades.strategy_code("call_credit_spread") == "CS"
    assert app_trades.strategy_code("iron_condor") == "IC"
    assert app_trades.strategy_code("cash_secured_put") == "SP"
    assert app_trades.strategy_code("poor_mans_covered_call") == "PMCC"
    assert app_trades.strategy_code("covered_call_model_1") == "CC"
    assert app_trades.strategy_code("covered_call_model_3") == "CC"


def test_mirror_fields_map_to_her_columns():
    trade = _call_spread()
    # credit per share = 6.0 - 4.6 = 1.4; total = 1.4 * 100 * 3 = 420
    sizing = {"credit": 420.0, "max_loss": 14580.0, "buying_power": 15000.0}
    f = app_trades.mirror_fields(trade, sizing, "20260705-1-SPX", "2026-08-04")

    assert f["ticker"] == "SPX"
    assert f["code"] == "CS"
    assert f["call_strike"] == 7950      # the short call
    assert f["put_strike"] == ""         # a call spread has no short put
    assert f["premium"] == 140.0         # credit per contract: 420 / 3
    assert f["contracts"] == 3
    assert f["bp"] == 15000.0
    assert f["profit_pct"] == 1.0        # 100% target at entry
    # her Profit$ formula (Profit% x Contracts x Premium) then gives the full credit
    assert f["profit_pct"] * f["contracts"] * f["premium"] == 420.0
    assert f["trade_id"] == "20260705-1-SPX"
    assert f["expiration"] == "2026-08-04"


def test_secured_put_uses_put_strike():
    trade = Trade(
        strategy_key="cash_secured_put", underlying="AAPL", contracts=1,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=180, delta=-0.30, premium=3.0, dte=30)])
    f = app_trades.mirror_fields(trade, {"credit": 300.0, "buying_power": 18000.0},
                                 "T1", "2026-08-04")
    assert f["code"] == "SP"
    assert f["put_strike"] == 180
    assert f["call_strike"] == ""
    assert f["premium"] == 300.0
