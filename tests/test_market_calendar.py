"""Tests for the U.S. market open/closed calendar (pure date math, no network)."""

from __future__ import annotations

import datetime as dt

from src.data import market_calendar as cal
from src.data import market_events as me


def test_2026_holidays_match_nyse():
    h = cal.holidays(2026)
    expected = {
        dt.date(2026, 1, 1): "New Year's Day",
        dt.date(2026, 1, 19): "Martin Luther King Jr. Day",
        dt.date(2026, 2, 16): "Presidents' Day",
        dt.date(2026, 4, 3): "Good Friday",
        dt.date(2026, 5, 25): "Memorial Day",
        dt.date(2026, 6, 19): "Juneteenth",
        dt.date(2026, 7, 3): "Independence Day",   # July 4 is a Saturday -> observed Fri 3rd
        dt.date(2026, 9, 7): "Labor Day",
        dt.date(2026, 11, 26): "Thanksgiving",
        dt.date(2026, 12, 25): "Christmas",
    }
    assert h == expected


def test_weekend_and_holiday_closed():
    assert cal.is_market_open(dt.date(2026, 7, 3)) is False   # holiday
    assert cal.is_market_open(dt.date(2026, 7, 4)) is False   # Saturday
    assert cal.is_market_open(dt.date(2026, 7, 5)) is False   # Sunday
    assert cal.is_market_open(dt.date(2026, 7, 6)) is True    # Monday, open


def test_next_market_open_skips_holiday_weekend():
    # After the Fri 3rd holiday and the weekend, next open is Mon the 6th.
    assert cal.next_market_open(dt.date(2026, 7, 3)) == dt.date(2026, 7, 6)


def test_closed_reason():
    assert cal.closed_reason(dt.date(2026, 7, 3)) == "Independence Day"
    assert cal.closed_reason(dt.date(2026, 7, 4)) == "the weekend"
    assert cal.closed_reason(dt.date(2026, 7, 6)) is None


def test_jobs_report_shifts_off_holiday():
    # First Friday of July 2026 is the 3rd, a holiday - the jobs report shifts to
    # the trading day before (Thursday the 2nd), so it is not flagged on the 3rd.
    events = me.upcoming_events(from_date=dt.date(2026, 7, 1), horizon_days=20)
    jobs = [e for e in events if e.kind == "jobs"]
    assert jobs and jobs[0].date == dt.date(2026, 7, 2)


# ---------- when the quotes on screen are not live ----------
def test_quotes_are_stale_on_a_weekend():
    """Rita's point: a Sunday scan reads Friday's last prints, and those spreads
    are wide - KO showed 43% and AAPL an open interest of 2. That is stale data,
    not an untradable option, and the app was calling it "hard to trade"."""
    import datetime as dt

    from src.data.market_calendar import quotes_are_stale

    assert quotes_are_stale(dt.datetime(2026, 7, 25, 12, 0)) == "the weekend"   # Sat
    assert quotes_are_stale(dt.datetime(2026, 7, 26, 12, 0)) == "the weekend"   # Sun


def test_quotes_are_live_only_inside_the_session():
    import datetime as dt

    from src.data.market_calendar import quotes_are_stale

    monday = dt.date(2026, 7, 27)
    assert quotes_are_stale(dt.datetime.combine(monday, dt.time(10, 0))) is None
    assert quotes_are_stale(dt.datetime.combine(monday, dt.time(9, 30))) is None
    assert quotes_are_stale(dt.datetime.combine(monday, dt.time(15, 59))) is None
    # Either side of the bell the book is not being made.
    assert quotes_are_stale(dt.datetime.combine(monday, dt.time(9, 29))) == \
        "before the opening bell"
    assert quotes_are_stale(dt.datetime.combine(monday, dt.time(16, 0))) == \
        "after the closing bell"


def test_a_holiday_says_which_holiday():
    import datetime as dt

    from src.data.market_calendar import quotes_are_stale

    why = quotes_are_stale(dt.datetime(2026, 12, 25, 12, 0))   # Christmas, a Friday
    assert why and "christmas" in why.lower()
