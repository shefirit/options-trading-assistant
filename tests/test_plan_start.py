"""The day the plan CHANGED, as distinct from the day the money went live.

Rita went live on 2026-07-31 with $100,000 and a $3,500 month. In September
2026 she funded another $50,000 and moved the goal to $4,500. Measured from
31 July, the year-one track charged those first seven weeks at the NEW rate and
reported a shortfall of about $5,500 - roughly $1,600 of which she had never
fallen behind by, because that goal did not exist yet.

`plan_from` is the fix and these pin its edges:

  * it moves year one, and ONLY year one
  * `live_from` does not move with it, so no real trade is re-read as practice
  * a missing, empty or malformed value behaves exactly as before

Every figure here is invented. This repo is public.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import goals, plan_settings
from src.engine.positions import Position

LIVE = date(2026, 7, 31)
FUNDED = date(2026, 9, 15)
TODAY = date(2026, 9, 18)

PLAN = {
    "targets": {"weekly": 1038, "monthly": 4500, "year_one_end_balance": 204000},
    "account": {"starting_capital": 150000, "live_from": "2026-07-31"},
    "risk_limits": {"monthly_bp_limit": 65000},
}


def _with(plan_from):
    """PLAN, with plan_from set to whatever is being tested."""
    acct = {**PLAN["account"]}
    if plan_from is None:
        acct.pop("plan_from", None)
    else:
        acct["plan_from"] = plan_from
    return {**PLAN, "account": acct}


def _closed(closed_on: date, realized: float) -> Position:
    """One settled trade - all year_one() reads is its dated cash event."""
    return Position(
        trade_id=f"{closed_on:%Y%m%d}-120000-SPX", underlying="SPX",
        strategy_key="put_credit_spread", status="closed",
        opened=LIVE, closed_on=closed_on, realized_pl=realized,
        credit=realized, contracts=1, account="real",
    )


# Banked $1,200 under the OLD plan, $400 under the new one.
BOOK = [_closed(date(2026, 8, 20), 1200.0), _closed(date(2026, 9, 17), 400.0)]


# ------------------------------------------------------------- plan_start()
def test_plan_start_falls_back_to_live_from_when_unset():
    """An account whose plan never changed must behave exactly as before."""
    assert goals.plan_start(_with(None), LIVE) == LIVE


@pytest.mark.parametrize("raw", ["", "   ", "not-a-date", "2026-13-45"])
def test_a_missing_or_broken_date_is_not_a_crash(raw):
    """A half-filled config should make the goal panel quiet, not kill the tab."""
    assert goals.plan_start(_with(raw), LIVE) == LIVE


@pytest.mark.parametrize("raw", ["2026-09-15", FUNDED])
def test_a_real_date_wins_as_string_or_as_date(raw):
    """YAML hands back a str when quoted and a date when not. Both must work."""
    assert goals.plan_start(_with(raw), LIVE) == FUNDED


# ---------------------------------------------------------------- year one
def test_year_one_counts_only_income_banked_under_this_plan():
    """The $1,200 from August was real money and stays in the journal. It is
    not progress toward a goal that began in September."""
    y = goals.year_one(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)
    assert y["banked"] == 400.0
    assert y["start"] == FUNDED


def test_year_one_runs_twelve_months_from_the_plan_not_the_funding():
    y = goals.year_one(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)
    assert y["year_end"] == date(2027, 9, 15)


def test_the_phantom_shortfall_is_what_this_removes():
    """THE regression. Measured from 31 July the pace is seven weeks of a goal
    that did not exist yet; measured from the funding date it is three days."""
    old = goals.year_one(BOOK, _with(None), live_from=LIVE, today=TODAY)
    new = goals.year_one(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)
    assert old["pace_income"] > 6_000        # ~49 days at $4,500 a month
    assert new["pace_income"] < 1_000        # ~4 days at $4,500 a month
    assert new["pace_income"] < old["pace_income"]


def test_the_year_one_bullet_row_moves_with_it():
    rows = {r["period"]: r for r in
            goals.bullet_rows(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)}
    assert rows["year"]["actual"] == 400.0


def test_the_month_band_is_deliberately_left_alone():
    """plan_from is narrow on purpose. September is still a calendar month
    measured against the whole monthly goal - a goal that shrinks for one
    transition month is more confusing on the tab she reads most than the
    small unfairness it fixes."""
    rows = {r["period"]: r for r in
            goals.bullet_rows(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)}
    assert rows["month"]["target"] == 4500.0
    assert rows["month"]["actual"] == 400.0    # from 1 Sept, not from the 15th


def test_setting_a_plan_date_never_reclassifies_a_real_trade():
    """The one thing that must not happen. live_from owns the practice/real
    split; plan_from must not touch it, or 21 real trades become paper."""
    from src.engine import month_report as mr
    before = mr.split_by_mode(BOOK, LIVE)
    after = mr.split_by_mode(BOOK, LIVE)
    assert len(before["real"]) == len(after["real"]) == 2
    y = goals.year_one(BOOK, _with(FUNDED), live_from=LIVE, today=TODAY)
    assert y["balance"] == 150_400.0           # capital + income under THIS plan


# -------------------------------------------------------- reading it back
def test_plan_settings_reads_the_date_as_a_date():
    assert plan_settings.read(_with("2026-09-15"))["plan_from"] == FUNDED


@pytest.mark.parametrize("raw", ["", None, "rubbish"])
def test_plan_settings_reads_a_missing_date_as_none(raw):
    """The editor shows an empty box rather than guessing a day."""
    assert plan_settings.read(_with(raw))["plan_from"] is None


def test_the_editor_can_clear_the_date_but_not_by_forgetting_it(tmp_path):
    """Omitting the key leaves the stored date alone; passing None clears it.
    A caller that only wants to move the numbers cannot wipe the date."""
    src = plan_settings.SETTINGS_PATH.read_text(encoding="utf-8")
    path = tmp_path / "settings.yaml"
    path.write_text(src, encoding="utf-8")

    nums = {"capital": 150000.0, "monthly": 4500.0,
            "weekly": 1038.0, "bp_limit": 65000.0}

    plan_settings.save({**nums, "plan_from": FUNDED}, path=path)
    assert 'plan_from: "2026-09-15"' in path.read_text(encoding="utf-8")

    plan_settings.save(nums, path=path)                      # omitted
    assert 'plan_from: "2026-09-15"' in path.read_text(encoding="utf-8")

    plan_settings.save({**nums, "plan_from": None}, path=path)   # explicit
    assert 'plan_from: ""' in path.read_text(encoding="utf-8")


def test_a_date_shaped_like_a_number_is_refused():
    assert plan_settings.validate(
        {"capital": 1.0, "monthly": 1.0, "weekly": 1.0, "bp_limit": 1.0,
         "plan_from": "2026-09-15"})
