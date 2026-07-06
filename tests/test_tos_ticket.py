"""The thinkorswim order line must match TOS's Order Entry format for every
strategy shape the scanner can produce - it's a double-check tool, so a wrong
line is worse than no line."""

import datetime as dt

from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.tos_ticket import ticket_line

TODAY = dt.date(2026, 7, 6)


def leg(role, action, otype, strike, premium=1.0, dte=45):
    return Leg(role=role, action=action, option_type=otype, strike=strike,
               delta=0.1, premium=premium, dte=dte)


def test_put_credit_spread_vertical():
    trade = Trade(strategy_key="put_credit_spread", underlying="SPX", contracts=2, legs=[
        leg("short_put", Action.SELL, OptionType.PUT, 6300, premium=3.00),
        leg("long_put", Action.BUY, OptionType.PUT, 6250, premium=0.50),
    ])
    # 45 days after Jul 6 2026 = Aug 20 2026
    assert ticket_line(trade, today=TODAY) == \
        "SELL -2 VERTICAL SPX 20 AUG 26 6300/6250 PUT @2.50 LMT"


def test_call_credit_spread_vertical():
    trade = Trade(strategy_key="call_credit_spread", underlying="NDX", contracts=1, legs=[
        leg("short_call", Action.SELL, OptionType.CALL, 23500, premium=4.20),
        leg("long_call", Action.BUY, OptionType.CALL, 23550, premium=1.10),
    ])
    assert ticket_line(trade, today=TODAY) == \
        "SELL -1 VERTICAL NDX 20 AUG 26 23500/23550 CALL @3.10 LMT"


def test_iron_condor_orders_strikes_call_side_then_put_side():
    trade = Trade(strategy_key="iron_condor", underlying="SPX", contracts=1, legs=[
        leg("short_call", Action.SELL, OptionType.CALL, 6500, premium=2.00),
        leg("long_call", Action.BUY, OptionType.CALL, 6525, premium=0.80),
        leg("short_put", Action.SELL, OptionType.PUT, 6100, premium=2.50),
        leg("long_put", Action.BUY, OptionType.PUT, 6075, premium=0.50),
    ])
    assert ticket_line(trade, today=TODAY) == \
        "SELL -1 IRON CONDOR SPX 20 AUG 26 6500/6525/6100/6075 CALL/PUT @3.20 LMT"


def test_cash_secured_put_single_leg():
    trade = Trade(strategy_key="cash_secured_put", underlying="AAPL", contracts=1, legs=[
        leg("short_put", Action.SELL, OptionType.PUT, 200, premium=2.50, dte=30),
    ])
    # 30 days after Jul 6 2026 = Aug 5 2026
    assert ticket_line(trade, today=TODAY) == \
        "SELL -1 AAPL 5 AUG 26 200 PUT @2.50 LMT"


def test_half_point_strike_keeps_decimal():
    trade = Trade(strategy_key="cash_secured_put", underlying="XSP", contracts=1, legs=[
        leg("short_put", Action.SELL, OptionType.PUT, 622.5, premium=1.25, dte=30),
    ])
    assert "622.5 PUT" in ticket_line(trade, today=TODAY)


def test_pmcc_diagonal_far_date_first_at_net_debit():
    trade = Trade(strategy_key="pmcc", underlying="AAPL", contracts=1, legs=[
        leg("long_call_leaps", Action.BUY, OptionType.CALL, 150, premium=60.0, dte=210),
        leg("short_call", Action.SELL, OptionType.CALL, 210, premium=2.50, dte=30),
    ])
    line = ticket_line(trade, today=TODAY)
    # 210 days -> Feb 1 2027; 30 days -> Aug 5 2026; net debit 57.50
    assert line == "BUY +1 DIAGONAL AAPL 1 FEB 27/5 AUG 26 150/210 CALL @57.50 LMT"


def test_unrecognized_shape_returns_none():
    trade = Trade(strategy_key="weird", underlying="SPX", contracts=1, legs=[
        leg("a", Action.SELL, OptionType.PUT, 6300),
        leg("b", Action.SELL, OptionType.PUT, 6250),
        leg("c", Action.BUY, OptionType.CALL, 6400),
    ])
    assert ticket_line(trade, today=TODAY) is None
