"""Her plan against reality - the target maths behind the goals dashboard.

Two things here are worth more than the rest put together:

  * elapsed_target counts DAYS, not months. She funded on 31 July, so July owes
    one day of the $3,500 and not the whole thing. Counting whole months would
    have told her she was $3,500 behind before she placed a trade.
  * span_report gives an all-time view a target that means something. The old
    all-time view handed a whole account's total to a band that divided by the
    ONE MONTH goal, so six days of trading read as a percentage of $3,500.

Every number below is invented. This repo is public.
"""

from __future__ import annotations

from datetime import date

from src.engine import goals
from src.engine.positions import Position, RollEvent

LIVE = date(2026, 7, 31)
TODAY = date(2026, 8, 5)

SETTINGS = {
    "targets": {"weekly": 808, "monthly": 3500, "year_one_end_balance": 142000},
    "account": {"starting_capital": 100000, "live_from": "2026-07-31"},
    "risk_limits": {"monthly_bp_limit": 50000},
}


def _pos(trade_id="t1", opened=date(2026, 8, 3), credit=300.0, closed_on=None,
         realized_pl=None, exit_reason="Profit target (50%) hit", rolls=None,
         strategy="Put Credit Spread", underlying="SPX") -> Position:
    return Position(
        trade_id=trade_id, underlying=underlying, strategy_name=strategy,
        opened=opened, credit=credit, open_credit=credit, open_cash=credit,
        status="closed" if closed_on else "open", closed_on=closed_on,
        realized_pl=realized_pl, exit_reason=exit_reason, rolls=rolls or [])


# ------------------------------------------------------------- targets_from
def test_targets_come_from_config_not_from_code():
    t = goals.targets_from(SETTINGS)
    assert t == {"weekly": 808.0, "monthly": 3500.0, "capital": 100000.0,
                 "year_one": 142000.0, "bp_limit": 50000.0}


def test_a_half_filled_settings_file_gives_zeros_rather_than_raising():
    """A missing goal should make the panel quiet, not crash the tab."""
    assert goals.targets_from({})["monthly"] == 0.0


# ----------------------------------------------------------- elapsed_target
def test_the_first_month_is_prorated_by_days():
    """Live on 31 July, asked on 5 August. July owes 1 day of 31; August owes
    5 of 31. Not two whole months, and not one."""
    got = goals.elapsed_target(3500, LIVE, TODAY)
    expected = 3500 * (1 / 31) + 3500 * (5 / 31)
    assert round(got, 2) == round(expected, 2)
    assert got < 3500          # the headline mistake this replaces


def test_a_month_lived_end_to_end_is_worth_the_whole_goal():
    got = goals.elapsed_target(3500, date(2026, 9, 1), date(2026, 9, 30))
    assert round(got, 2) == 3500.0


def test_three_whole_months_are_worth_three_goals():
    got = goals.elapsed_target(3500, date(2026, 9, 1), date(2026, 11, 30))
    assert round(got, 2) == 10500.0


def test_the_first_day_live_is_worth_one_day_not_nothing():
    got = goals.elapsed_target(3500, LIVE, LIVE)
    assert round(got, 2) == round(3500 / 31, 2)


def test_a_start_in_the_future_or_no_goal_is_zero_not_a_crash():
    assert goals.elapsed_target(3500, date(2027, 1, 1), TODAY) == 0.0
    assert goals.elapsed_target(0, LIVE, TODAY) == 0.0
    assert goals.elapsed_target(3500, None, TODAY) == 0.0


# -------------------------------------------------------------- month_target
def test_a_finished_month_targets_the_whole_goal():
    assert goals.month_target(3500, "2026-09", LIVE, date(2026, 11, 1)) == 3500.0


def test_the_month_she_went_live_targets_only_the_days_she_was_live():
    """July 2026 has 31 days and she funded on the 31st, so July's target is one
    day's worth - not $3,500 she never had a chance to earn."""
    got = goals.month_target(3500, "2026-07", LIVE, date(2026, 8, 20))
    assert round(got, 2) == round(3500 / 31, 2)


def test_the_month_in_progress_targets_the_days_gone_so_far():
    got = goals.month_target(3500, "2026-08", LIVE, TODAY)
    assert round(got, 2) == round(3500 * 5 / 31, 2)


# --------------------------------------------------------------- bullet_rows
def test_there_are_exactly_three_rows_in_week_month_year_order():
    rows = goals.bullet_rows([], SETTINGS, LIVE, TODAY)
    assert [r["period"] for r in rows] == ["week", "month", "year"]
    assert [r["label"] for r in rows] == ["THIS WEEK", "THIS MONTH", "YEAR ONE"]


def test_year_one_targets_the_income_half_not_the_headline_balance():
    """$142,000 is where the account ends up. $42,000 is what she has to earn.
    Measuring against $142,000 would open the bar at 70% for doing nothing."""
    rows = goals.bullet_rows([], SETTINGS, LIVE, TODAY)
    assert rows[2]["target"] == 42000.0


def test_a_practice_trade_never_changes_a_real_row():
    paper = _pos(opened=date(2026, 7, 1), closed_on=date(2026, 8, 4),
                 realized_pl=900.0)
    real = goals.bullet_rows([paper], SETTINGS, LIVE, TODAY, mode="real")
    assert all(r["actual"] == 0.0 for r in real)
    practice = goals.bullet_rows([paper], SETTINGS, LIVE, TODAY, mode="practice")
    assert practice[1]["actual"] == 900.0


def test_every_row_carries_a_pace_marker_not_just_a_target():
    """The thing a bullet chart has that a progress bar does not: where a steady
    plan would be TODAY, not just where the finish line is."""
    rows = goals.bullet_rows([], SETTINGS, LIVE, TODAY)
    month = rows[1]
    assert month["pace"] == goals.elapsed_target(3500, date(2026, 8, 1), TODAY)
    assert 0 < month["pace"] < month["target"]


def test_tone_is_a_word_not_a_colour():
    p = _pos(opened=date(2026, 8, 1), closed_on=date(2026, 8, 4), realized_pl=5000.0)
    rows = goals.bullet_rows([p], SETTINGS, LIVE, TODAY)
    assert rows[1]["tone"] == "good"
    assert goals.bullet_rows([], SETTINGS, LIVE, TODAY)[1]["tone"] == "behind"


# ---------------------------------------------------------- cumulative_series
def test_every_row_names_its_book_and_none_holds_a_combined_total():
    paper = _pos("p1", opened=date(2026, 7, 1), closed_on=date(2026, 7, 20),
                 realized_pl=400.0)
    real = _pos("r1", opened=date(2026, 8, 1), closed_on=date(2026, 8, 4),
                realized_pl=600.0)
    rows = goals.cumulative_series([paper, real], SETTINGS, LIVE, TODAY)
    assert rows, "expected some series"
    assert {r["book"] for r in rows} == {"real", "practice"}
    for r in rows:
        assert r["book"] in ("real", "practice")
        assert 1000.0 not in r.values()      # 400 + 600 must not exist anywhere


def test_the_real_ramp_starts_the_day_she_funded_not_at_the_first_trade():
    real = _pos("r1", opened=date(2026, 8, 3), closed_on=date(2026, 8, 4),
                realized_pl=600.0)
    rows = [r for r in goals.cumulative_series([real], SETTINGS, LIVE, TODAY)
            if r["book"] == "real"]
    last = max(rows, key=lambda r: r["date"])
    assert last["target"] == goals.elapsed_target(3500, LIVE, last["date"])


def test_a_roll_credit_lands_in_the_curve_on_the_day_it_was_rolled():
    p = _pos("r1", opened=date(2026, 8, 1),
             rolls=[RollEvent(rolled_on=date(2026, 8, 4), cash=250.0)])
    rows = [r for r in goals.cumulative_series([p], SETTINGS, LIVE, TODAY)
            if r["book"] == "real"]
    assert max(r["cumulative"] for r in rows) == 250.0


# --------------------------------------------------------------------- year_one
def test_the_balance_is_capital_plus_what_she_banked():
    p = _pos(opened=date(2026, 8, 1), closed_on=date(2026, 8, 4), realized_pl=1200.0)
    y = goals.year_one([p], SETTINGS, LIVE, TODAY)
    assert y["banked"] == 1200.0
    assert y["balance"] == 101200.0
    assert y["to_earn"] == 42000.0


def test_progress_is_measured_against_the_forty_two_thousand_of_income():
    p = _pos(opened=date(2026, 8, 1), closed_on=date(2026, 8, 4), realized_pl=4200.0)
    y = goals.year_one([p], SETTINGS, LIVE, TODAY)
    assert round(y["pct"], 4) == 0.1        # 4,200 of 42,000, not of 142,000


def test_an_account_that_has_earned_nothing_reads_zero_percent():
    y = goals.year_one([], SETTINGS, LIVE, TODAY)
    assert y["pct"] == 0.0
    assert y["balance"] == 100000.0


def test_practice_money_never_reaches_the_year_one_balance():
    paper = _pos(opened=date(2026, 7, 1), closed_on=date(2026, 8, 4),
                 realized_pl=9000.0)
    y = goals.year_one([paper], SETTINGS, LIVE, TODAY)
    assert y["banked"] == 0.0 and y["balance"] == 100000.0


# ------------------------------------------------------------------ month_table
def test_the_two_books_are_separate_keys_with_no_key_that_adds_them():
    paper = _pos("p1", opened=date(2026, 7, 1), closed_on=date(2026, 8, 4),
                 realized_pl=400.0)
    real = _pos("r1", opened=date(2026, 8, 1), closed_on=date(2026, 8, 4),
                realized_pl=600.0)
    rows = goals.month_table([paper, real], SETTINGS, LIVE, TODAY)
    aug = next(r for r in rows if r["month"] == "2026-08")
    assert aug["real"] == 600.0 and aug["practice"] == 400.0
    assert 1000.0 not in aug.values()


def test_each_month_carries_the_target_it_was_actually_aiming_at():
    rows = goals.month_table([], SETTINGS, LIVE, TODAY)
    aug = next(r for r in rows if r["month"] == "2026-08")
    assert round(aug["target"], 2) == round(3500 * 5 / 31, 2)


# ------------------------------------------------------------------ span_report
def test_a_six_day_old_account_is_never_measured_against_a_whole_month():
    """THE regression test. The old all-time view showed a whole account's
    history as a percentage of the ONE MONTH $3,500 goal."""
    p = _pos(opened=date(2026, 8, 1), closed_on=date(2026, 8, 4), realized_pl=600.0)
    rep = goals.span_report([p], SETTINGS, LIVE, TODAY)
    assert rep["span_target"] != 3500.0
    assert 500 < rep["span_target"] < 800        # ~$678 for 31 Jul - 5 Aug
    assert rep["span_pct"] == rep["banked"] / rep["span_target"]


def test_the_span_totals_still_come_from_the_month_report():
    p = _pos(opened=date(2026, 8, 1), closed_on=date(2026, 8, 4), realized_pl=600.0)
    rep = goals.span_report([p], SETTINGS, LIVE, TODAY)
    assert rep["banked"] == 600.0
    assert rep["label"] == "All time"
    # The span starts the day she funded, not the day of her first trade - the
    # days she was live and waiting are part of the plan too.
    assert rep["first_activity"] == LIVE


def test_an_empty_real_book_still_has_a_target_it_is_behind():
    """Six days funded and nothing banked is $0 of about $677, not "no target".
    Saying she is behind is the honest answer and the useful one."""
    rep = goals.span_report([], SETTINGS, LIVE, TODAY)
    assert rep["span_target"] > 0
    assert rep["span_pct"] == 0.0        # and no divide-by-zero


def test_with_nothing_logged_and_no_go_live_date_there_is_no_span():
    rep = goals.span_report([], SETTINGS, live_from=None, today=TODAY,
                            mode="practice")
    assert rep["span_target"] == 0.0 and rep["span_pct"] == 0.0
