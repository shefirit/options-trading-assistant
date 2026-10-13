"""The TWA tracker copy: one strike per row, real money only, from October.

Built 2026-10-13 for her TWA Discord Tracker spreadsheet. Every number here is
INVENTED - this repo is public, so real positions never go in source control.
"""

from datetime import date

import pytest

from src.engine.models import Action, Leg, OptionType
from src.engine.positions import Position, RollEvent, WingEvent
from src.logging_tools import trade_logger, twa_tracker
from src.logging_tools.row import COLUMNS

START = date(2026, 10, 1)


def _leg(action, kind, strike, premium=0.0, qty=1, dte=None, exp=None):
    return Leg(role="x", action=action, option_type=kind, strike=strike,
               premium=premium, quantity=qty, dte=dte, expiration=exp)


def _pos(**kw) -> Position:
    base = dict(trade_id="T1", underlying="abc", account="real", status="open",
                opened=date(2026, 10, 2), contracts=1)
    base.update(kw)
    if "legs" in base and "open_legs" not in base:
        base["open_legs"] = base["legs"]
    return Position(**base)


def _cells(p, start=START):
    rows, why = twa_tracker.rows_for(p, start)
    assert why == ""
    return [r["cells"] for r in rows], [r["key"] for r in rows]


def _pl(c):
    """The template's P/L formula, column O."""
    if c["Close $"] == "":
        return None
    k, l, j = c["Premium $"], c["Close $"], c["Quantity"]
    return round((k - l) * j * 100 if c["Type"] == "Sell" else (l - k) * j * 100, 2)


def test_cash_secured_put_is_one_sell_put_row():
    p = _pos(strategy_key="cash_secured_put", open_credit=120.0, open_cash=120.0,
             legs=[_leg(Action.SELL, OptionType.PUT, 50, 1.2,
                        exp=date(2026, 11, 20))])
    (c,), (key,) = _cells(p)
    assert key == "T1#1"
    assert c["Type"] == "Sell" and c["Strategy"] == "PUT"
    assert c["Ticker"] == "ABC" and c["Strike $"] == 50 and c["Quantity"] == 1
    assert c["Premium $"] == 1.2 and c["Close $"] == "" and c["Closed Date"] == ""
    assert c["Open Date"] == "2026-10-02" and c["Exp Date"] == "2026-11-20"
    assert c["Collateral"] == 5000


def test_a_roll_closes_the_old_row_and_opens_a_new_one():
    """Sold the 60 put for $200, rolled to the 55 for $650 banking $150, so
    the 60 was bought back for $500."""
    p = _pos(strategy_key="cash_secured_put", opened=date(2026, 10, 4),
             open_credit=200.0, open_cash=200.0,
             legs=[_leg(Action.SELL, OptionType.PUT, 60, 2.0, dte=30)],
             rolls=[RollEvent(rolled_on=date(2026, 10, 26), cash=150.0,
                              new_strike=55.0, new_expiration=date(2026, 11, 20),
                              new_credit=650.0, option_type="put")])
    (old, new), keys = _cells(p)
    assert keys == ["T1#1", "T1#2"]
    assert old["Closed Date"] == "2026-10-26" and old["Close $"] == 5.0
    assert _pl(old) == -300.0
    assert new["Open Date"] == "2026-10-26" and new["Strike $"] == 55
    assert new["Premium $"] == 6.5 and new["Collateral"] == 5500
    assert new["Notes"] == "Rolled from the 60 put"
    # The two rows together are exactly what the trade banked so far.
    assert _pl(old) + 650 == 200 + 150


def test_rows_closed_before_the_start_are_left_out_but_keep_their_numbers():
    p = _pos(strategy_key="cash_secured_put", opened=date(2026, 8, 4),
             open_credit=200.0,
             legs=[_leg(Action.SELL, OptionType.PUT, 60, 2.0)],
             rolls=[RollEvent(rolled_on=date(2026, 8, 26), cash=150.0,
                              new_strike=55.0, new_credit=650.0,
                              option_type="put")])
    cells, keys = _cells(p)
    assert keys == ["T1#2"]          # still #2 - keys never shift
    assert cells[0]["Strike $"] == 55


def test_a_wheel_assignment_becomes_an_assigned_stock_row_with_calls_on_it():
    """The 22 put assigned, then a 22 call sold and bought back for $3."""
    p = _pos(strategy_key="cash_secured_put", opened=date(2026, 10, 1),
             open_credit=80.0, shares_cost=2200.0,
             assigned_on=date(2026, 10, 2), assigned_strike=22.0,
             legs=[_leg(Action.SELL, OptionType.PUT, 22, 0.8)],
             rolls=[RollEvent(rolled_on=date(2026, 10, 2), cash=25.0,
                              new_strike=22.0, new_expiration=date(2026, 10, 16),
                              new_credit=25.0, option_type="call"),
                    RollEvent(rolled_on=date(2026, 10, 13), cash=-3.0,
                              option_type="call")])
    (put, stock, call), _ = _cells(p)
    assert put["Close $"] == 0.0 and put["Closed Date"] == "2026-10-02"
    assert _pl(put) == 80.0
    assert (stock["Type"], stock["Strategy"]) == ("ASSIGNED", "STOCK")
    assert stock["Strike $"] == 22 and stock["Premium $"] == 22
    assert stock["Collateral"] == 2200 and stock["Close $"] == ""
    assert call["Strategy"] == "CC" and call["Covered Call Collateral"] == 2200
    assert call["Premium $"] == 0.25 and call["Close $"] == 0.03
    assert _pl(call) == 22.0


def test_put_credit_spread_roll_moves_both_strikes():
    p = _pos(strategy_key="put_credit_spread", open_credit=300.0,
             legs=[_leg(Action.SELL, OptionType.PUT, 180, 5.0),
                   _leg(Action.BUY, OptionType.PUT, 160, 2.0)],
             rolls=[RollEvent(rolled_on=date(2026, 10, 9), cash=120.0,
                              new_strike=200.0, new_long_strike=180.0,
                              new_credit=330.0, option_type="put")],
             status="closed", closed_on=date(2026, 10, 20), close_cash=-100.0)
    (first, second), _ = _cells(p)
    assert first["Strategy"] == "PCS" and first["Collateral"] == 2000
    assert first["Notes"] == "PCS $180/$160"
    assert first["Close $"] == 2.1                   # 330 - 120 = 210 buy-back
    assert second["Strike $"] == 200 and second["Collateral"] == 2000
    assert second["Notes"] == "PCS $200/$180 - rolled from $180"
    assert second["Close $"] == 1.0 and second["Closed Date"] == "2026-10-20"
    assert _pl(first) + _pl(second) == 300 + 120 - 100


def test_leaps_cost_squares_with_the_net_cash_of_the_open():
    """Bought the 40 call and sold three 45 puts for a net $300 debit."""
    p = _pos(strategy_key="long_call_leaps", open_cash=-300.0,
             legs=[_leg(Action.BUY, OptionType.CALL, 40, 14.9, exp=date(2028, 1, 21)),
                   _leg(Action.SELL, OptionType.PUT, 45, 4.0, qty=3,
                        exp=date(2028, 1, 21))],
             rolls=[RollEvent(rolled_on=date(2026, 10, 3), cash=50.0,
                              new_strike=60.0, new_expiration=date(2026, 10, 16),
                              new_credit=50.0, option_type="call")])
    (leaps, puts, call), _ = _cells(p)
    assert (leaps["Type"], leaps["Strategy"]) == ("Buy", "LEAP")
    assert leaps["Premium $"] == 15.0 and leaps["Collateral"] == 1500
    assert puts["Quantity"] == 3 and puts["Premium $"] == 4.0
    assert puts["Collateral"] == 13500
    # Net of the two opening rows is the -$300 the app logged.
    assert round(puts["Premium $"] * 300 - leaps["Premium $"] * 100, 2) == -300
    assert call["Strategy"] == "CC" and call["Covered Call Collateral"] == 1500


def test_buy_write_shares_then_called_away():
    p = _pos(strategy_key="weekly_covered_call", shares_cost=5000.0,
             open_cash=-5000.0, legs=[],
             rolls=[RollEvent(rolled_on=date(2026, 10, 14), cash=80.0,
                              new_strike=52.0, new_expiration=date(2026, 10, 16),
                              new_credit=80.0, option_type="call")],
             status="closed", closed_on=date(2026, 10, 16), close_cash=5200.0)
    (shares, call), _ = _cells(p)
    assert (shares["Type"], shares["Strategy"]) == ("Buy", "STOCK")
    assert shares["Premium $"] == 50.0 and shares["Collateral"] == 5000
    assert shares["Close $"] == 52.0 and _pl(shares) == 200.0
    assert shares["Notes"] == "Bought 100 shares - closed with the whole trade"
    assert call["Close $"] == 0.0 and _pl(call) == 80.0
    assert call["Covered Call Collateral"] == 5000


@pytest.mark.parametrize("kw, why", [
    (dict(account="paper"), "not a real-money trade"),
    (dict(strategy_key="call_credit_spread"), "the tracker has no type"),
    (dict(strategy_key="iron_condor"), "the tracker has no type"),
    (dict(strategy_key="covered_call_model_2"), "the tracker has no type"),
    (dict(wings=[WingEvent(option_type="call", short_strike=1, long_strike=2)]),
     "became an iron condor"),
    (dict(merged_into="T0", status="merged"), "merged"),
])
def test_what_the_tracker_has_no_row_for_is_skipped(kw, why):
    base = dict(strategy_key="put_credit_spread", open_credit=100.0,
                legs=[_leg(Action.SELL, OptionType.PUT, 100, 2),
                      _leg(Action.BUY, OptionType.PUT, 95, 1)])
    base.update(kw)
    rows, reason = twa_tracker.rows_for(_pos(**base), START)
    assert rows == [] and why in reason


def test_build_clears_a_deleted_trade_and_reports_only_recent_skips():
    keep = _pos(trade_id="A", strategy_key="cash_secured_put", open_credit=50.0,
                legs=[_leg(Action.SELL, OptionType.PUT, 10, 0.5)])
    old_skip = _pos(trade_id="B", strategy_key="call_credit_spread",
                    status="closed", closed_on=date(2026, 9, 10))
    new_skip = _pos(trade_id="C", strategy_key="iron_condor")
    paper = _pos(trade_id="D", account="paper", strategy_key="cash_secured_put")

    trades, skipped = twa_tracker.build([keep, old_skip, new_skip, paper], start=START)
    assert [t["trade_id"] for t in trades] == ["A"]
    assert [s["trade_id"] for s in skipped] == ["C"]

    trades, _ = twa_tracker.build([keep], ["A", "GONE"], start=START)
    assert trades[1] == {"trade_id": "GONE", "rows": []}


def test_a_merge_row_updates_both_trades():
    row = [""] * len(COLUMNS)
    row[COLUMNS.index("Trade ID")] = "X"
    row[COLUMNS.index("Event")] = "merge"
    row[COLUMNS.index("Details JSON")] = '{"into":"Y"}'
    assert twa_tracker.trade_ids_in_row(row, COLUMNS) == ["X", "Y"]


def test_copy_to_tracker_skips_practice_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(twa_tracker, "is_enabled", lambda: True)
    monkeypatch.setattr(twa_tracker, "sync", lambda ids: calls.append(ids) or {"written": 1})

    trade_logger.copy_to_tracker(["P"], account="paper")
    assert calls == []

    trade_logger.copy_to_tracker(["R"], account="real")
    assert calls == [["R"]] and trade_logger.last_tracker_copy["ok"]

    def boom(ids):
        raise RuntimeError("script too old")
    monkeypatch.setattr(twa_tracker, "sync", boom)
    trade_logger.copy_to_tracker(["R"], account="real")
    assert trade_logger.last_tracker_copy["ok"] is False
    assert trade_logger.last_tracker_copy["message"] == "script too old"
