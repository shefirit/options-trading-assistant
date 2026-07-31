"""The month income report - the numbers behind the report page.

The one rule this whole module has to get right: practice money and real money
never mix. Every other assertion here is arithmetic; that one is the difference
between a report and a lie.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import month_report as mr
from src.engine.positions import Position, RollEvent

LIVE = date(2026, 7, 31)


def _pos(trade_id="t1", underlying="SPX", strategy="Put Credit Spread",
         opened=date(2026, 8, 3), credit=300.0, closed_on=None,
         realized_pl=None, exit_cost=None, exit_reason="", rolls=None,
         open_cash=None, contracts=1) -> Position:
    return Position(
        trade_id=trade_id, underlying=underlying, strategy_name=strategy,
        opened=opened, credit=credit, open_credit=credit,
        open_cash=credit if open_cash is None else open_cash,
        contracts=contracts,
        status="closed" if closed_on else "open",
        closed_on=closed_on, realized_pl=realized_pl, exit_cost=exit_cost,
        exit_reason=exit_reason, rolls=rolls or [])


# ------------------------------------------------------- practice vs real
def test_a_trade_opened_before_going_live_is_practice():
    paper = _pos(opened=date(2026, 7, 30))
    live = _pos(opened=date(2026, 7, 31))
    assert not mr.is_real(paper, LIVE)
    assert mr.is_real(live, LIVE)


def test_a_paper_trade_closed_after_going_live_is_still_paper():
    """The account a trade belongs to is the one it was OPENED in. A June paper
    spread closed in August did not put a single real dollar at risk."""
    p = _pos(opened=date(2026, 6, 10), closed_on=date(2026, 8, 5),
             realized_pl=500.0, exit_reason="Profit target (50%) hit")
    assert not mr.is_real(p, LIVE)
    report = mr.build([p], month="2026-08", live_from=LIVE, mode="real")
    assert report["banked"] == 0.0
    assert report["trades_closed"] == 0


def test_with_no_live_date_the_whole_log_reads_as_practice():
    """The safe default: counting a paper trade as income is the worse error."""
    p = _pos(opened=date(2026, 8, 3))
    assert not mr.is_real(p, None)
    assert mr.split_by_mode([p], None)["practice"] == [p]


def test_practice_mode_reports_only_the_paper_side():
    paper = _pos(trade_id="p", opened=date(2026, 7, 1),
                 closed_on=date(2026, 7, 20), realized_pl=400.0,
                 exit_reason="Profit target (50%) hit")
    real = _pos(trade_id="r", opened=date(2026, 7, 31),
                closed_on=date(2026, 7, 31), realized_pl=100.0,
                exit_reason="Profit target (50%) hit")
    practice = mr.build([paper, real], month="2026-07", live_from=LIVE,
                        mode="practice")
    live = mr.build([paper, real], month="2026-07", live_from=LIVE, mode="real")
    assert practice["banked"] == 400.0
    assert live["banked"] == 100.0


# --------------------------------------------------------------- the money
def test_premium_sold_and_banked_are_different_numbers():
    """Premium sold is what she sold; banked is what survived closing it. A
    report that shows only the first one flatters the trader."""
    p = _pos(credit=300.0, opened=date(2026, 8, 3), closed_on=date(2026, 8, 20),
             realized_pl=150.0, exit_cost=150.0,
             exit_reason="Profit target (50%) hit")
    r = mr.build([p], month="2026-08", live_from=LIVE)
    assert r["premium_sold"] == 300.0
    assert r["cost_to_close"] == 150.0
    assert r["banked"] == 150.0
    assert r["capture_pct"] == pytest.approx(0.5)


def test_premium_counts_in_the_month_it_was_sold_not_the_month_it_settled():
    p = _pos(opened=date(2026, 8, 25), credit=300.0,
             closed_on=date(2026, 9, 10), realized_pl=180.0, exit_cost=120.0,
             exit_reason="Profit target (50%) hit")
    aug = mr.build([p], month="2026-08", live_from=LIVE)
    sep = mr.build([p], month="2026-09", live_from=LIVE)
    assert (aug["premium_sold"], aug["banked"]) == (300.0, 0.0)
    assert (sep["premium_sold"], sep["banked"]) == (0.0, 180.0)


def test_a_roll_sells_premium_on_its_own_date():
    """Rolling sells a new call. That premium belongs to the day it was sold,
    which is how a covered call rolled monthly shows income every month."""
    p = _pos(strategy="Poor Man's Covered Call", opened=date(2026, 8, 3),
             credit=200.0, open_cash=-1500.0,
             rolls=[RollEvent(rolled_on=date(2026, 9, 4), cash=90.0,
                              new_strike=560.0, new_credit=140.0)])
    aug = mr.build([p], month="2026-08", live_from=LIVE)
    sep = mr.build([p], month="2026-09", live_from=LIVE)
    assert aug["premium_sold"] == 200.0
    assert sep["premium_sold"] == 140.0     # the NEW call, not the net cash
    assert sep["banked"] == 90.0            # the net cash is what she banked
    assert sep["roll_income"] == 90.0


def test_capture_ignores_the_debit_shapes():
    """Closing a PMCC includes selling the LEAPS back, so "what share of the
    premium did I keep" has no meaning there and must not pollute the tile."""
    pmcc = _pos(trade_id="d", strategy="Poor Man's Covered Call",
                opened=date(2026, 8, 1), credit=200.0, open_cash=-1500.0,
                closed_on=date(2026, 8, 20), realized_pl=1700.0,
                exit_reason="Profit target (50%) hit")
    spread = _pos(trade_id="c", opened=date(2026, 8, 1), credit=400.0,
                  closed_on=date(2026, 8, 22), realized_pl=200.0,
                  exit_reason="Profit target (50%) hit")
    r = mr.build([pmcc, spread], month="2026-08", live_from=LIVE)
    assert r["capture_trades"] == 1
    assert r["capture_pct"] == pytest.approx(0.5)


def test_a_losing_month_reports_a_negative_banked_total():
    p = _pos(opened=date(2026, 8, 3), credit=300.0, closed_on=date(2026, 8, 14),
             realized_pl=-600.0, exit_cost=900.0, exit_reason="Stop loss hit")
    r = mr.build([p], month="2026-08", live_from=LIVE)
    assert r["banked"] == -600.0
    assert r["win_rate"] == 0.0
    assert r["losses"] == 1


# --------------------------------------------------------- the breakdowns
def test_weeks_run_monday_to_sunday_and_carry_both_numbers():
    # 2026-08-03 is a Monday; 2026-08-12 is the Wednesday of the next week.
    a = _pos(trade_id="a", opened=date(2026, 8, 3), credit=300.0,
             closed_on=date(2026, 8, 6), realized_pl=150.0,
             exit_reason="Profit target (50%) hit")
    b = _pos(trade_id="b", opened=date(2026, 8, 12), credit=500.0,
             closed_on=date(2026, 8, 13), realized_pl=250.0,
             exit_reason="Profit target (50%) hit")
    r = mr.build([a, b], month="2026-08", live_from=LIVE)
    assert [w["start"] for w in r["weeks"]] == [date(2026, 8, 3), date(2026, 8, 10)]
    assert [w["banked"] for w in r["weeks"]] == [150.0, 250.0]
    assert [w["premium"] for w in r["weeks"]] == [300.0, 500.0]
    assert r["best_week"]["start"] == date(2026, 8, 10)


def test_breakdowns_group_by_strategy_and_by_name():
    a = _pos(trade_id="a", underlying="SPX", strategy="Iron Condor",
             opened=date(2026, 8, 3), credit=600.0)
    b = _pos(trade_id="b", underlying="QQQ", strategy="Put Credit Spread",
             opened=date(2026, 8, 4), credit=200.0)
    c = _pos(trade_id="c", underlying="SPX", strategy="Put Credit Spread",
             opened=date(2026, 8, 5), credit=200.0)
    r = mr.build([a, b, c], month="2026-08", live_from=LIVE)

    by_strat = {x["name"]: x for x in r["by_strategy"]}
    assert by_strat["Iron Condor"]["premium"] == 600.0
    assert by_strat["Put Credit Spread"]["premium"] == 400.0
    assert by_strat["Iron Condor"]["share"] == pytest.approx(0.6)
    # Biggest first, so the pie and the table beside it read in the same order.
    assert r["by_strategy"][0]["name"] == "Iron Condor"

    by_name = {x["name"]: x["premium"] for x in r["by_underlying"]}
    assert by_name == {"SPX": 800.0, "QQQ": 200.0}


# ---------------------------------------------------------- the discipline
def test_the_four_sop_exits_count_as_by_the_rules():
    reasons = ["Profit target (50%) hit", "21 DTE time exit",
               "21 DTE credit roll (opened a new spread)", "Stop loss hit",
               "Expired worthless"]
    trades = [_pos(trade_id=f"t{i}", opened=date(2026, 8, 3),
                   closed_on=date(2026, 8, 20), realized_pl=100.0,
                   exit_reason=reason)
              for i, reason in enumerate(reasons)]
    r = mr.build(trades, month="2026-08", live_from=LIVE)
    assert r["rules_followed"] == 5
    assert r["rules_total"] == 5


def test_an_exit_outside_the_rules_is_counted_and_named():
    ok = _pos(trade_id="a", opened=date(2026, 8, 3), closed_on=date(2026, 8, 20),
              realized_pl=100.0, exit_reason="Profit target (50%) hit")
    off = _pos(trade_id="b", opened=date(2026, 8, 3), closed_on=date(2026, 8, 21),
               realized_pl=-50.0, exit_reason="Other - got nervous before the Fed")
    r = mr.build([ok, off], month="2026-08", live_from=LIVE)
    assert r["rules_followed"] == 1
    assert r["rules_total"] == 2
    labels = {row["label"]: row["count"] for row in r["exits"]}
    assert labels["Closed for another reason"] == 1
    assert r["lessons"] == ["got nervous before the Fed"]


# ----------------------------------------------------------------- the pace
def test_pace_measures_against_the_days_gone_not_the_calendar():
    p = _pos(opened=date(2026, 8, 3), closed_on=date(2026, 8, 5),
             realized_pl=1000.0, exit_reason="Profit target (50%) hit")
    today = date(2026, 8, 16)   # 16 of 31 days, so ~51.6% of the month
    r = mr.build([p], month="2026-08", live_from=LIVE, today=today)
    pace = mr.pace(r, 3500.0, today=today)
    assert pace["expected_by_now"] == pytest.approx(3500 * 16 / 31, abs=0.01)
    assert pace["on_track"] is False
    assert pace["still_needed"] == 2500.0
    assert pace["days_left"] == 15


def test_pace_is_silent_for_a_month_that_is_already_over():
    p = _pos(opened=date(2026, 7, 31), closed_on=date(2026, 7, 31),
             realized_pl=10.0, exit_reason="Profit target (50%) hit")
    r = mr.build([p], month="2026-07", live_from=LIVE, today=date(2026, 8, 16))
    assert mr.pace(r, 3500.0, today=date(2026, 8, 16)) is None


# ----------------------------------------------------------------- the shell
def test_an_empty_month_reports_zeros_rather_than_blowing_up():
    r = mr.build([], month="2026-08", live_from=LIVE)
    assert r["has_activity"] is False
    assert r["banked"] == 0.0
    assert r["capture_pct"] is None
    assert r["avg_per_active_day"] is None
    assert r["weeks"] == []


def test_available_months_always_includes_the_current_one():
    p = _pos(opened=date(2026, 6, 10))
    months = mr.available_months([p], today=date(2026, 8, 16))
    keys = [m["key"] for m in months]
    assert keys[0] == "2026-08"       # newest first
    assert "2026-06" in keys


def test_buying_power_counts_every_trade_opened_including_closed_ones():
    """Her monthly limit is a cumulative budget - closing a trade early does
    not hand its room back, so a closed trade still spends against it."""
    closed = _pos(trade_id="a", opened=date(2026, 8, 3),
                  closed_on=date(2026, 8, 10), realized_pl=100.0,
                  exit_reason="Profit target (50%) hit")
    closed.buying_power = 2200.0
    still_open = _pos(trade_id="b", opened=date(2026, 8, 12))
    still_open.buying_power = 4400.0
    r = mr.build([closed, still_open], month="2026-08", live_from=LIVE)
    assert r["bp_opened"] == 6600.0


# ------------------------------------------------- the stamp on the trade
def test_the_stamp_on_the_trade_beats_the_date_rule():
    """A practice trade placed AFTER going live is still practice. No date rule
    can know that, which is why the account is stamped when it is logged."""
    paper_after_funding = _pos(opened=date(2026, 8, 10))
    paper_after_funding.account = "paper"
    assert not mr.is_real(paper_after_funding, LIVE)

    real_before_funding = _pos(opened=date(2026, 6, 1))
    real_before_funding.account = "real"
    assert mr.is_real(real_before_funding, LIVE)


def test_a_stamped_practice_trade_stays_out_of_the_real_report():
    paper = _pos(trade_id="p", opened=date(2026, 8, 3), credit=300.0,
                 closed_on=date(2026, 8, 10), realized_pl=150.0,
                 exit_reason="Profit target (50%) hit")
    paper.account = "paper"
    real = _pos(trade_id="r", opened=date(2026, 8, 4), credit=400.0,
                closed_on=date(2026, 8, 11), realized_pl=200.0,
                exit_reason="Profit target (50%) hit")
    real.account = "real"
    live = mr.build([paper, real], month="2026-08", live_from=LIVE, mode="real")
    practice = mr.build([paper, real], month="2026-08", live_from=LIVE,
                        mode="practice")
    assert (live["banked"], live["premium_sold"]) == (200.0, 400.0)
    assert (practice["banked"], practice["premium_sold"]) == (150.0, 300.0)


def test_unstamped_rows_still_fall_back_to_the_date():
    """Everything logged before the two books existed carries no stamp, and
    must keep sorting itself correctly."""
    old_paper = _pos(opened=date(2026, 6, 10))
    old_real = _pos(opened=date(2026, 8, 1))
    assert old_paper.account == ""
    assert not mr.is_real(old_paper, LIVE)
    assert mr.is_real(old_real, LIVE)
