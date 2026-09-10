"""Money bucketed by day, week and month - the Profit page's arithmetic.

Three things this module has to get right, in order of how much damage getting
them wrong would do:

  1. The two books never sum. `banked` is one account and `back` is the other,
     and no field holds their total.
  2. A roll banks on the day it was rolled, not on the day the trade closed.
     Her monthly goal is measured in months, so a covered call rolled every
     month has to pay into each of those months.
  3. A period's target prorates the same way every other target in the app
     does - through goals.elapsed_target, never a fresh day-count.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import pnl
from src.engine.positions import Position, RollEvent

LIVE = date(2026, 7, 1)
SETTINGS = {"targets": {"monthly": 3500.0, "weekly": 808.0},
            "capital": {"starting": 100000.0},
            "risk_limits": {"monthly_bp_limit": 50000.0}}


def _pos(trade_id="t1", underlying="SPX", strategy="Put Credit Spread",
         opened=date(2026, 7, 6), credit=300.0, closed_on=None,
         realized_pl=None, rolls=None, account="real") -> Position:
    return Position(
        trade_id=trade_id, underlying=underlying, strategy_name=strategy,
        opened=opened, credit=credit, open_credit=credit, open_cash=credit,
        contracts=1, account=account,
        status="closed" if closed_on else "open",
        closed_on=closed_on, realized_pl=realized_pl,
        exit_reason="Profit target (50%) hit" if closed_on else "",
        rolls=rolls or [])


def _at(rows, label):
    return next(r for r in rows if r["label"] == label)


# ------------------------------------------------------------- the grains
@pytest.mark.parametrize("grain", pnl.GRAINS)
def test_every_grain_returns_an_unbroken_run_of_periods(grain):
    """Empty periods are in the list, not missing from it. A month drawn only
    on the days that earned is a scatter with no shape, and the shape is the
    question."""
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=150.0)
    rows = pnl.buckets([p], grain, SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    assert rows
    for a, b in zip(rows, rows[1:]):
        assert a["start"] < b["start"], "periods must be ordered and unique"
        assert b["start"] > a["end"] or grain == "day"


def test_an_unknown_grain_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        pnl.buckets([], "quarter", SETTINGS, LIVE)


def test_an_empty_log_is_an_empty_list_not_a_row_of_zeroes():
    assert pnl.buckets([], "month", SETTINGS, LIVE) == []
    assert pnl.summary([])["banked"] == 0.0
    assert pnl.current([]) is None


# ------------------------------------------------------ where money lands
def test_a_close_banks_on_the_day_it_closed():
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=150.0)
    rows = pnl.buckets([p], "day", SETTINGS, LIVE, today=date(2026, 7, 10),
                       limit=None)
    assert _at(rows, "Wed 8/7")["banked"] == 150.0
    assert _at(rows, "Mon 6/7")["banked"] == 0.0


def test_a_roll_banks_in_its_own_month_not_the_closing_one():
    """The rule the whole monthly goal rests on. A covered call rolled in July
    and closed in September paid her in July, and July's report has to say so.
    """
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 9, 4),
             realized_pl=100.0,
             rolls=[RollEvent(rolled_on=date(2026, 7, 20), cash=250.0, seq=1)])
    rows = pnl.buckets([p], "month", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    assert _at(rows, "July 2026")["banked"] == 250.0
    assert _at(rows, "September 2026")["banked"] == 100.0


def test_a_week_bucket_runs_monday_to_sunday():
    """Monday-start is how her weekly target is counted, so it is how weeks are
    cut here too."""
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 12),
             realized_pl=400.0)
    rows = pnl.buckets([p], "week", SETTINGS, LIVE, today=date(2026, 7, 13),
                       limit=None)
    week = _at(rows, "6/7 - 12/7")
    assert week["start"].weekday() == 0 and week["end"].weekday() == 6
    assert week["banked"] == 400.0


def test_the_running_total_accumulates_across_periods():
    a = _pos("a", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=100.0)
    b = _pos("b", opened=date(2026, 8, 3), closed_on=date(2026, 8, 5),
             realized_pl=250.0)
    rows = pnl.buckets([a, b], "month", SETTINGS, LIVE,
                       today=date(2026, 9, 10), limit=None)
    assert _at(rows, "July 2026")["cumulative"] == 100.0
    assert _at(rows, "August 2026")["cumulative"] == 350.0
    assert _at(rows, "September 2026")["cumulative"] == 350.0


# --------------------------------------------------------- the two books
def test_the_other_book_rides_along_but_never_joins_the_total():
    """The invariant the charts are built on: no field holds real + practice."""
    real = _pos("r", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
                realized_pl=100.0, account="real")
    paper = _pos("p", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
                 realized_pl=900.0, account="paper")
    rows = pnl.buckets([real, paper], "month", SETTINGS, LIVE,
                       today=date(2026, 7, 31), limit=None)
    july = _at(rows, "July 2026")
    assert july["banked"] == 100.0, "the real book is the foreground"
    assert july["back"] == 900.0, "the practice book is the backdrop"
    assert 1000.0 not in july.values(), "nothing may hold their sum"
    assert pnl.summary(rows)["banked"] == 100.0


def test_switching_the_book_swaps_which_is_the_backdrop():
    real = _pos("r", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
                realized_pl=100.0, account="real")
    paper = _pos("p", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
                 realized_pl=900.0, account="paper")
    rows = pnl.buckets([real, paper], "month", SETTINGS, LIVE,
                       today=date(2026, 7, 31), mode="practice", limit=None)
    july = _at(rows, "July 2026")
    assert (july["banked"], july["back"]) == (900.0, 100.0)


def test_both_books_share_one_axis():
    """A backdrop drawn on a shorter axis reads as the other book having
    stopped, so the span covers both."""
    paper = _pos("p", opened=date(2026, 6, 1), closed_on=date(2026, 6, 5),
                 realized_pl=50.0, account="paper")
    real = _pos("r", opened=date(2026, 8, 3), closed_on=date(2026, 8, 5),
                realized_pl=100.0, account="real")
    rows = pnl.buckets([paper, real], "month", SETTINGS, LIVE,
                       today=date(2026, 8, 31), limit=None)
    assert [r["label"] for r in rows] == ["June 2026", "July 2026",
                                          "August 2026"]


# ------------------------------------------------------------- the target
def test_a_finished_month_is_worth_the_whole_monthly_goal():
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=100.0)
    rows = pnl.buckets([p], "month", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    assert _at(rows, "August 2026")["target"] == 3500.0


def test_a_day_is_worth_one_days_share_of_the_month():
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=100.0)
    rows = pnl.buckets([p], "day", SETTINGS, LIVE, today=date(2026, 7, 31),
                       limit=None)
    # July has 31 days, so one day is 3500/31.
    assert _at(rows, "Wed 8/7")["target"] == pytest.approx(3500 / 31, abs=0.02)


def test_the_month_in_progress_is_paced_to_today_not_to_month_end():
    """The gap between the two targets is the whole reason there are two. A
    month five days old measured against $3,500 always looks like a failure."""
    p = _pos(opened=date(2026, 9, 1), closed_on=date(2026, 9, 3),
             realized_pl=600.0)
    rows = pnl.buckets([p], "month", SETTINGS, LIVE, today=date(2026, 9, 5),
                       limit=None)
    sept = _at(rows, "September 2026")
    assert sept["target"] == 3500.0
    assert sept["pace_target"] == pytest.approx(3500 * 5 / 30, abs=0.02)
    assert sept["pace_target"] < sept["target"]


def test_nothing_is_targeted_before_she_funded_the_account():
    """A steady plan cannot have produced anything in a month she had no real
    account in."""
    p = _pos(opened=date(2026, 6, 1), closed_on=date(2026, 6, 5),
             realized_pl=100.0, account="paper")
    rows = pnl.buckets([p], "month", SETTINGS, live_from=date(2026, 7, 1),
                       today=date(2026, 7, 31), limit=None)
    assert _at(rows, "June 2026")["target"] == 0.0
    assert _at(rows, "July 2026")["target"] == 3500.0


def test_a_week_straddling_two_months_prorates_across_both():
    """The reason targets go through goals.elapsed_target instead of a fresh
    day-count: 31-day July and 31-day August share this week at different daily
    rates in general, and the one definition has to be the one that applies."""
    p = _pos(opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=100.0)
    rows = pnl.buckets([p], "week", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    straddle = _at(rows, "31/8 - 6/9")
    assert straddle["target"] == pytest.approx(3500 / 31 + 6 * 3500 / 30,
                                               abs=0.05)


# ------------------------------------------------------------ the summary
def test_the_average_is_per_active_period_not_per_calendar_period():
    """"You average $260 on the days money settled" says something about her
    trading. The same total spread over every weekend and quiet Tuesday does
    not."""
    a = _pos("a", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=200.0)
    b = _pos("b", opened=date(2026, 7, 6), closed_on=date(2026, 7, 9),
             realized_pl=400.0)
    rows = pnl.buckets([a, b], "day", SETTINGS, LIVE, today=date(2026, 7, 31),
                       limit=None)
    s = pnl.summary(rows)
    assert s["active"] == 2
    assert s["average"] == 300.0
    assert s["banked"] == 600.0


def test_the_best_and_worst_periods_are_the_rows_themselves():
    a = _pos("a", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
             realized_pl=200.0)
    b = _pos("b", opened=date(2026, 7, 6), closed_on=date(2026, 7, 9),
             realized_pl=-50.0)
    s = pnl.summary(pnl.buckets([a, b], "day", SETTINGS, LIVE,
                                today=date(2026, 7, 31), limit=None))
    assert s["best"]["banked"] == 200.0 and s["best"]["label"] == "Wed 8/7"
    assert s["worst"]["banked"] == -50.0


def test_a_losing_period_ends_the_streak():
    win = _pos("a", opened=date(2026, 7, 6), closed_on=date(2026, 7, 8),
               realized_pl=200.0)
    loss = _pos("b", opened=date(2026, 7, 6), closed_on=date(2026, 7, 9),
                realized_pl=-50.0)
    later = _pos("c", opened=date(2026, 7, 6), closed_on=date(2026, 7, 10),
                 realized_pl=75.0)
    s = pnl.summary(pnl.buckets([win, loss, later], "day", SETTINGS, LIVE,
                                today=date(2026, 7, 31), limit=None))
    assert s["win_streak"] == 1, "the loss on the 9th ends the run"


def test_the_series_never_runs_past_today():
    """A chart that trails off into empty future days reads as a run of losing
    days. The span stops at the period she is in."""
    p = _pos(opened=date(2026, 9, 1), closed_on=date(2026, 9, 2),
             realized_pl=100.0)
    rows = pnl.buckets([p], "day", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    assert rows[-1]["start"] == date(2026, 9, 10)
    assert not any(r["is_future"] for r in rows)
    assert rows[-1]["is_current"]


def test_the_period_in_progress_is_current_and_not_a_shortfall():
    """September is not a failed month on the 10th - it is an unfinished one,
    which is why it carries a paced target as well as a full one."""
    p = _pos(opened=date(2026, 9, 1), closed_on=date(2026, 9, 2),
             realized_pl=100.0)
    rows = pnl.buckets([p], "month", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    sept = _at(rows, "September 2026")
    assert sept["is_current"] and not sept["is_future"]
    s = pnl.summary(rows)
    assert s["banked"] == 100.0
    assert s["buckets"] == len([r for r in rows if not r["is_future"]])


# ------------------------------------------------------------- the trims
def test_each_grain_trims_to_something_a_phone_can_read():
    p = _pos(opened=date(2026, 1, 5), closed_on=date(2026, 1, 6),
             realized_pl=100.0)
    days = pnl.buckets([p], "day", SETTINGS, date(2026, 1, 1),
                       today=date(2026, 9, 10))
    assert len(days) == pnl.DEFAULT_LIMIT["day"]


def test_a_trimmed_view_still_carries_a_true_running_total():
    """The cumulative is computed over the whole span before trimming - a
    running total that restarts at the left edge is a different number wearing
    the same label."""
    old = _pos("a", opened=date(2026, 1, 5), closed_on=date(2026, 1, 6),
               realized_pl=1000.0)
    new = _pos("b", opened=date(2026, 9, 1), closed_on=date(2026, 9, 2),
               realized_pl=50.0)
    rows = pnl.buckets([old, new], "day", SETTINGS, date(2026, 1, 1),
                       today=date(2026, 9, 10))
    assert rows[-1]["cumulative"] == 1050.0


# ------------------------------------------------------------- the hero row
def test_current_finds_the_period_she_is_living_in():
    p = _pos(opened=date(2026, 9, 1), closed_on=date(2026, 9, 2),
             realized_pl=100.0)
    rows = pnl.buckets([p], "month", SETTINGS, LIVE, today=date(2026, 9, 10),
                       limit=None)
    assert pnl.current(rows)["label"] == "September 2026"
