"""The cash ledger: PMCC and covered calls, where opening COSTS money.

The credit strategies (spreads, condor, CSP) collect money up front and pay to
close. The debit ones are the mirror image: a PMCC buys a LEAPS, sells short
calls against it, rolls them, and gets paid when the position is finally
unwound. The old credit-in/cost-out model could not express that at all - the
long leg never entered the math, a roll had nowhere to be recorded, and closing
could only ever be a cost.

The worked example below runs through every test in this file:

    open    buy the LEAPS -4,000, sell the short call +150   ->  open_cash -3,850
    roll    buy back the near call, sell a later one, net    ->        cash   +80
    close   sell the LEAPS back, buy in the short call, net  ->  close_cash +5,000
                                                                 -----------------
                                                                 result     +1,230

All numbers here are invented. This repo is PUBLIC: never use real positions,
strikes, or fills as fixtures.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from src.data.chain import OptionChain, OptionContract
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import (
    cost_to_close_from_chain,
    monthly_summary,
    parse_rows,
    performance,
    position_value_from_chain,
)
from src.logging_tools.row import (COLUMNS, build_close_row, build_roll_row,
                                   build_row)

OPENED = date(2026, 3, 2)
LEAPS_EXP = date(2027, 6, 18)
SHORT_EXP = date(2026, 4, 1)
ROLL_EXP = date(2026, 5, 1)

LEAPS_COST = 4000.0      # 40.00 fill on 1 contract
CALL_CREDIT = 150.0      # 1.50 fill
OPEN_CASH = CALL_CREDIT - LEAPS_COST     # -3,850
ROLL_CASH = 80.0         # net credit on the roll
ROLL_NEW_CREDIT = 210.0  # what the new call sold for on its own
CLOSE_CASH = 5000.0      # net received unwinding the whole thing
RESULT = OPEN_CASH + ROLL_CASH + CLOSE_CASH               # 1,230
CLOSE_REALIZED = OPEN_CASH + CLOSE_CASH                   # 1,150


def _pmcc_trade() -> Trade:
    return Trade(
        strategy_key="poor_mans_covered_call", underlying="MSFT", contracts=1,
        underlying_price=125.0,
        legs=[
            Leg(role="long_call_leaps", action=Action.BUY,
                option_type=OptionType.CALL, strike=100, delta=0.85,
                premium=40.0, dte=(LEAPS_EXP - OPENED).days),
            Leg(role="short_call", action=Action.SELL,
                option_type=OptionType.CALL, strike=130, delta=0.30,
                premium=1.50, dte=(SHORT_EXP - OPENED).days),
        ],
    )


SIZE = {"credit": CALL_CREDIT, "max_loss": 3850.0, "buying_power": 3850.0,
        "open_cash": OPEN_CASH, "shares_cost": 0.0}


def _open_row() -> list:
    return build_row(_pmcc_trade(), "Poor Man's Covered Call (PMCC)", SIZE,
                     True, "", trade_id="P1", opened_on=OPENED,
                     expiration_on=SHORT_EXP)


def _roll_row() -> list:
    return build_roll_row("P1", "MSFT", "Poor Man's Covered Call (PMCC)",
                          cash=ROLL_CASH, new_strike=135.0,
                          new_expiration=ROLL_EXP, new_credit=ROLL_NEW_CREDIT,
                          rolled_on=date(2026, 4, 6))


def _close_row() -> list:
    # The close banks the capital result only; the roll's credit was banked on
    # the day it rolled and must not be counted a second time.
    return build_close_row("P1", "MSFT", "Poor Man's Covered Call (PMCC)",
                           exit_cost=0.0, realized_pl=CLOSE_REALIZED,
                           reason="Profit target (50%) hit",
                           closed_on=date(2026, 4, 20), close_cash=CLOSE_CASH)


# ---------------------------------------------------------------- the open
def test_open_row_records_the_leaps_as_money_out():
    p = parse_rows(COLUMNS, [_open_row()])[0]
    assert p.is_debit
    assert p.open_cash == OPEN_CASH
    assert p.credit == CALL_CREDIT      # the short call - for the 50% rule only
    assert p.capital_at_risk == 3850.0


# ---------------------------------------------------------------- the whole life
def test_full_life_totals_every_dollar_in_and_out():
    positions = parse_rows(COLUMNS, [_open_row(), _roll_row(), _close_row()])
    assert len(positions) == 1          # a roll is an event, not a new position
    p = positions[0]
    assert p.status == "closed"
    assert p.roll_income == ROLL_CASH
    assert p.realized_total == RESULT


def test_roll_moves_the_tracker_to_the_new_call():
    p = parse_rows(COLUMNS, [_open_row(), _roll_row()])[0]
    assert p.expiration == ROLL_EXP
    short = next(l for l in p.legs if l.action == Action.SELL)
    assert short.strike == 135.0        # 130 before the roll
    assert p.leg_expiration(short) == ROLL_EXP
    assert p.credit == ROLL_NEW_CREDIT  # the 50% target follows the new call
    leaps = next(l for l in p.legs if l.action == Action.BUY)
    assert leaps.strike == 100.0        # untouched, still a year out
    assert p.leg_expiration(leaps) == LEAPS_EXP


def test_only_the_nearest_short_call_is_rolled():
    """Model 2 and 3 also carry short PUTs. A call roll must not touch them."""
    trade = Trade(
        strategy_key="covered_call_model_2", underlying="MSFT", contracts=1,
        underlying_price=100.0,
        legs=[
            Leg(role="long_put_protection", action=Action.BUY,
                option_type=OptionType.PUT, strike=95, premium=4.0, dte=365),
            Leg(role="short_put", action=Action.SELL,
                option_type=OptionType.PUT, strike=85, premium=1.5, dte=365),
            Leg(role="short_call", action=Action.SELL,
                option_type=OptionType.CALL, strike=110, premium=1.2, dte=30),
        ])
    size = {"credit": 120.0, "max_loss": 10250.0, "buying_power": 10250.0,
            "open_cash": -10250.0, "shares_cost": 10000.0}
    row = build_row(trade, "Covered Call - Model 2", size, True, "",
                    trade_id="CC2", opened_on=OPENED,
                    expiration_on=OPENED + timedelta(days=30))
    roll = build_roll_row("CC2", "MSFT", "Covered Call - Model 2", cash=90.0,
                          new_strike=115.0,
                          new_expiration=OPENED + timedelta(days=60),
                          new_credit=140.0, rolled_on=date(2026, 4, 6))
    p = parse_rows(COLUMNS, [row, roll])[0]
    short_put = next(l for l in p.legs if l.role == "short_put")
    assert short_put.strike == 85 and short_put.dte == 365    # untouched
    short_call = next(l for l in p.legs if l.role == "short_call")
    assert short_call.strike == 115.0


# ---------------------------------------------------------------- the months
def test_roll_credit_banks_in_the_month_it_happened():
    """Her monthly goal counts money banked that month. A covered call rolled
    monthly for a year must show income in all twelve months, not one lump."""
    positions = parse_rows(COLUMNS, [_open_row(), _roll_row()])
    april = monthly_summary(positions, today=date(2026, 4, 25))[0]
    assert april["month"] == "2026-04"
    assert april["roll_income"] == ROLL_CASH
    assert april["realized_pl"] == ROLL_CASH   # banked, though the trade is open
    assert april["closed_count"] == 0
    # performance() and the month view must never disagree.
    assert performance(positions,
                       today=date(2026, 4, 25))["month_pl"] == ROLL_CASH


def test_month_totals_do_not_double_count_the_roll():
    positions = parse_rows(COLUMNS, [_open_row(), _roll_row(), _close_row()])
    months = {m["month"]: m
              for m in monthly_summary(positions, today=date(2026, 4, 25))}
    # 80 rolled on 4/6 + 1,150 of capital result at the 4/20 close = 1,230 once.
    assert months["2026-04"]["realized_pl"] == RESULT
    assert months["2026-03"]["realized_pl"] == 0.0   # opening banks nothing
    assert months["2026-03"]["opened_count"] == 1
    perf = performance(positions, today=date(2026, 4, 25))
    assert perf["month_pl"] == RESULT
    assert perf["total_pl"] == RESULT
    assert perf["win_rate"] == 1.0


def test_a_roll_in_an_earlier_month_stays_in_that_month():
    positions = parse_rows(COLUMNS, [
        _open_row(),
        build_roll_row("P1", "MSFT", "Poor Man's Covered Call (PMCC)",
                       cash=ROLL_CASH, new_strike=135.0,
                       new_expiration=ROLL_EXP, new_credit=ROLL_NEW_CREDIT,
                       rolled_on=date(2026, 3, 30)),
        _close_row(),
    ])
    months = {m["month"]: m
              for m in monthly_summary(positions, today=date(2026, 4, 25))}
    assert months["2026-03"]["realized_pl"] == ROLL_CASH      # rolled in March
    assert months["2026-04"]["realized_pl"] == CLOSE_REALIZED  # closed in April
    assert positions[0].realized_total == RESULT               # the whole trade


def test_month_table_rows_add_up_to_the_month_total():
    """The month view's headline says '$X of your $3,500 goal'. If the rows
    below it summed to anything else she would rightly stop trusting the page."""
    from ui import components

    positions = parse_rows(COLUMNS, [_open_row(), _roll_row(), _close_row()])
    for month in monthly_summary(positions, today=date(2026, 4, 25)):
        frame = components.month_trades_dataframe(month["rows"])
        banked = frame["Result $"].dropna().sum()
        assert banked == month["realized_pl"], month["month"]


# ---------------------------------------------------------------- live pricing
def test_priced_whole_shows_the_leaps_gain():
    """The old card tracked '% of the credit kept' on the short call while the
    LEAPS quietly gained. position_value_from_chain is what fixes that."""
    p = parse_rows(COLUMNS, [_open_row()])[0]
    chain = OptionChain(underlying="MSFT", underlying_price=140.0, contracts=[
        OptionContract(option_type=OptionType.CALL, strike=100,
                       expiration=LEAPS_EXP.isoformat(), dte=449,
                       delta=0.90, bid=44.90, ask=45.10),
        OptionContract(option_type=OptionType.CALL, strike=130,
                       expiration=SHORT_EXP.isoformat(), dte=30,
                       delta=0.45, bid=1.90, ask=2.10),
    ])
    v = position_value_from_chain(p, chain)
    # Unwinding: sell the LEAPS at 45.00, buy the call back at 2.00 = 43.00.
    assert v["value"] == 4300.0
    assert v["open_pl"] == 4300.0 + OPEN_CASH       # 450
    # The near-leg view, which the 50% rule uses, sees only the short call.
    assert cost_to_close_from_chain(p, chain)["cost_to_close"] == 200.0


def test_open_pl_counts_roll_income_already_banked():
    p = parse_rows(COLUMNS, [_open_row(), _roll_row()])[0]
    chain = OptionChain(underlying="MSFT", underlying_price=140.0, contracts=[
        OptionContract(option_type=OptionType.CALL, strike=100,
                       expiration=LEAPS_EXP.isoformat(), dte=449,
                       delta=0.90, bid=44.90, ask=45.10),
        OptionContract(option_type=OptionType.CALL, strike=135,
                       expiration=ROLL_EXP.isoformat(), dte=58,
                       delta=0.45, bid=4.90, ask=5.10),
    ])
    v = position_value_from_chain(p, chain)
    assert v["value"] == 4000.0                          # 45.00 - 5.00
    assert v["open_pl"] == OPEN_CASH + ROLL_CASH + 4000.0   # 230


def test_value_is_none_when_the_leaps_is_missing_from_the_chain():
    """Better no total than a total with the biggest leg silently left out."""
    p = parse_rows(COLUMNS, [_open_row()])[0]
    chain = OptionChain(underlying="MSFT", underlying_price=140.0, contracts=[
        OptionContract(option_type=OptionType.CALL, strike=130,
                       expiration=SHORT_EXP.isoformat(), dte=30,
                       delta=0.45, bid=1.90, ask=2.10),
    ])
    assert position_value_from_chain(p, chain) is None


def test_covered_call_values_the_shares_at_todays_price():
    trade = Trade(
        strategy_key="covered_call_model_1", underlying="MSFT", contracts=1,
        underlying_price=100.0,
        legs=[
            Leg(role="long_put_protection", action=Action.BUY,
                option_type=OptionType.PUT, strike=95, premium=3.0, dte=365),
            Leg(role="short_call", action=Action.SELL,
                option_type=OptionType.CALL, strike=110, premium=1.2, dte=30),
        ])
    opened = date(2026, 3, 1)
    size = {"credit": 120.0, "max_loss": 10180.0, "buying_power": 10180.0,
            "open_cash": -10180.0, "shares_cost": 10000.0}
    row = build_row(trade, "Covered Call - Model 1", size, True, "",
                    trade_id="CC1", opened_on=opened,
                    expiration_on=opened + timedelta(days=30))
    p = parse_rows(COLUMNS, [row])[0]
    assert p.shares_cost == 10000.0
    chain = OptionChain(underlying="MSFT", underlying_price=105.0, contracts=[
        OptionContract(option_type=OptionType.PUT, strike=95,
                       expiration=(opened + timedelta(days=365)).isoformat(),
                       dte=365, bid=1.90, ask=2.10),
        OptionContract(option_type=OptionType.CALL, strike=110,
                       expiration=(opened + timedelta(days=30)).isoformat(),
                       dte=30, bid=0.55, ask=0.65),
    ])
    v = position_value_from_chain(p, chain, underlying_price=105.0)
    # 10,500 of shares + 200 for the put - 60 to buy the call back = 10,640.
    assert v["value"] == 10640.0
    assert v["open_pl"] == 10640.0 - 10180.0    # 460 on a 5-point share move
    # Without a share price the shares cannot be valued, so there is no answer.
    assert position_value_from_chain(p, chain, underlying_price=None) is None


# ---------------------------------------------------------------- no regressions
def _spread_trade() -> Trade:
    return Trade(
        strategy_key="put_credit_spread", underlying="SPX", contracts=1,
        underlying_price=5100.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=5000, delta=-0.20, premium=8.0, dte=30),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=4975, delta=-0.15, premium=5.0, dte=30),
        ])


SPREAD_SIZE = {"credit": 300.0, "max_loss": 2200.0, "buying_power": 2200.0,
               "open_cash": 300.0, "shares_cost": 0.0}


def test_credit_spread_close_still_means_money_out():
    """The ledger must not disturb the strategies that already added up."""
    open_row = build_row(_spread_trade(), "Put Credit Spread", SPREAD_SIZE,
                         True, "", trade_id="T9")
    close_row = build_close_row("T9", "SPX", "Put Credit Spread",
                                exit_cost=150.0, realized_pl=150.0,
                                reason="Profit target (50%) hit")
    p = parse_rows(COLUMNS, [open_row, close_row])[0]
    assert not p.is_debit
    assert p.open_cash == 300.0          # the credit, as before
    assert p.close_cash == -150.0        # buying it back cost money
    assert p.realized_total == 150.0
    assert p.roll_income == 0.0


def test_rows_written_before_the_ledger_still_parse():
    """Rows already sitting in the log have no open_cash field."""
    row = build_row(_spread_trade(), "Put Credit Spread", SPREAD_SIZE, True, "",
                    trade_id="OLD")
    details = json.loads(row[COLUMNS.index("Details JSON")])
    del details["open_cash"]
    row[COLUMNS.index("Details JSON")] = json.dumps(details)
    p = parse_rows(COLUMNS, [row])[0]
    assert p.open_cash == 300.0          # falls back to the Credit $ column
    assert not p.is_debit


def test_close_row_written_before_the_ledger_still_parses():
    open_row = build_row(_spread_trade(), "Put Credit Spread", SPREAD_SIZE,
                         True, "", trade_id="OLD2")
    close_row = build_close_row("OLD2", "SPX", "Put Credit Spread",
                                exit_cost=120.0, realized_pl=180.0,
                                reason="Profit target (50%) hit")
    close_row[COLUMNS.index("Details JSON")] = ""    # the old writer left it blank
    p = parse_rows(COLUMNS, [open_row, close_row])[0]
    assert p.close_cash == -120.0        # derived from the Exit Cost $ column
    assert p.realized_total == 180.0


def test_a_roll_row_for_an_unknown_trade_is_ignored():
    """A stray roll must never conjure a phantom position into her results."""
    assert parse_rows(COLUMNS, [_roll_row()]) == []
