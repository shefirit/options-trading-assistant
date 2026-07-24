"""Seasonality tests with synthetic price history (no network)."""

from __future__ import annotations

import datetime as dt

from src.research import seasonality


def _points(years: int, monthly: dict[int, float] | None = None,
            start: float = 100.0) -> list[tuple[dt.date, float]]:
    """Daily-ish closes where each calendar month applies a fixed return.

    `monthly` maps month number to the return that month always delivers, so
    a test can build a stock that reliably rises in January.
    """
    monthly = monthly or {}
    points: list[tuple[dt.date, float]] = []
    price = start
    for year in range(2005, 2005 + years):
        for month in range(1, 13):
            change = monthly.get(month, 0.5)
            # Two observations a month is enough - the module keys off the last
            # close in each month.
            points.append((dt.date(year, month, 14), price))
            price *= (1 + change / 100)
            points.append((dt.date(year, month, 28), price))
    return points


def test_monthly_returns_are_month_over_month():
    points = _points(2)
    returns = seasonality.monthly_returns(points)
    # 24 months of data, minus the very first (no prior month to compare to).
    assert len(returns) == 23
    for value in returns.values():
        assert abs(value - 0.5) < 1e-6


def test_gap_in_history_does_not_invent_a_return():
    points = [
        (dt.date(2020, 1, 31), 100.0),
        # February missing entirely
        (dt.date(2020, 3, 31), 120.0),
    ]
    returns = seasonality.monthly_returns(points)
    assert (2020, 3) not in returns   # would have been a bogus two-month jump
    assert returns == {}


def test_strong_month_is_detected_and_ranked():
    result = seasonality.build("TEST", _points(20, {1: 6.0}))
    january = result.months[0]
    assert january.name == "January"
    assert january.avg_pct > 5
    assert january.hit_rate == 100.0
    assert january.rank == 1
    assert january.status == "good"
    assert result.best_month.month == 1


def test_weak_month_is_flagged():
    result = seasonality.build("TEST", _points(20, {10: -4.0}))
    october = result.months[9]
    assert october.avg_pct < 0
    assert october.hit_rate == 0.0
    assert october.status == "watch"
    assert result.worst_month.month == 10


def test_short_history_is_called_out_not_dressed_up():
    result = seasonality.build("TEST", _points(2))
    assert result.enough_history is False
    assert "too short" in result.summary.lower()


def test_max_years_trims_the_oldest_data():
    result = seasonality.build("TEST", _points(30), max_years=10)
    assert result.years_covered == 10
    assert result.last_year - result.first_year == 9


def test_this_month_and_next_month_wrap_around_december():
    points = _points(10)
    result = seasonality.build("TEST", points, today=dt.date(2024, 12, 15))
    assert result.this_month.name == "December"
    assert result.next_month.name == "January"


def test_year_row_only_totals_a_complete_year():
    result = seasonality.build("TEST", _points(3))
    # Newest first; the first year in the data has no January return (nothing
    # before it), so it cannot have a full-year figure.
    oldest = result.rows[-1]
    assert oldest.returns[0] is None
    assert oldest.full_year_pct is None
    complete = result.rows[0]
    assert complete.full_year_pct is not None


def test_empty_history_is_handled():
    result = seasonality.build("TEST", [])
    assert result.months == []
    assert "not enough" in result.summary.lower()


def test_frame_to_points_skips_a_missing_close_column():
    assert seasonality.frame_to_points(None) == []
