"""Several names side by side - the row builder behind the Analyze comparison."""

from __future__ import annotations

import datetime as dt

import pytest

from src.research import compare


def _closes(n=300, start=100.0, step=0.2):
    return [start + i * step for i in range(n)]


class _Analysis:
    def __init__(self, name="Test Co", grade="B", is_fund=False, price=None):
        self.name, self.grade, self.is_fund, self.price = name, grade, is_fund, price


def test_a_full_row_carries_every_column():
    closes = _closes()
    row = compare.build_row(
        "nvda", kind="stock", price=160.0, change_pct=1.25, closes=closes,
        analysis=_Analysis(name="NVIDIA", grade="A"),
        info={"trailingPE": 45.2, "revenueGrowth": 0.62},
        earnings_date=dt.date(2026, 11, 12), trend="up",
        today=dt.date(2026, 8, 14))

    assert row.symbol == "NVDA"          # uppercased
    assert row.name == "NVIDIA"
    assert row.price == 160.0
    assert row.change_pct == 1.25
    assert row.grade == "A"
    assert row.pe == pytest.approx(45.2)
    assert row.rev_growth_pct == pytest.approx(62.0)
    assert row.days_to_earnings == 90
    assert row.trend == "up"
    assert row.rsi is not None
    assert row.note == ""


def test_a_fund_is_labelled_not_graded():
    """SPY is 500 companies - a letter grade there would be an invented finding."""
    row = compare.build_row("SPY", analysis=_Analysis(grade="D", is_fund=True))
    assert row.grade == "ETF"


def test_missing_fundamentals_leave_blanks_not_zeros():
    """Yahoo throttles fundamentals from cloud IPs far more readily than price
    history, so this is the NORMAL hosted case. A zero P/E reads as a finding."""
    row = compare.build_row("AAPL", price=305.0, closes=_closes(), info={})
    assert row.price == 305.0
    assert row.pe is None
    assert row.rev_growth_pct is None
    assert row.days_to_earnings is None


def test_price_falls_back_to_the_last_close():
    row = compare.build_row("AAPL", closes=_closes())
    assert row.price == pytest.approx(_closes()[-1])


def test_a_row_with_no_price_at_all_says_so():
    row = compare.build_row("ZZZZ")
    assert row.price is None
    assert "No price" in row.note


def test_price_but_no_history_is_flagged_rather_than_shown_blank():
    row = compare.build_row("ZZZZ", price=10.0)
    assert row.year_pct is None
    assert "Price only" in row.note


def test_off_high_is_negative_and_zero_at_the_top():
    rising = _closes()                       # ends at its own high
    assert compare.build_row("UP", closes=rising).off_high_pct == pytest.approx(0.0)

    fallen = rising + [rising[-1] * 0.8]
    assert compare.build_row("DOWN", closes=fallen).off_high_pct == pytest.approx(-20.0)


def test_the_year_column_measures_a_year_back():
    closes = _closes(n=400, start=100.0, step=0.0)
    closes[-1 - compare.TRADING_DAYS_YEAR] = 50.0
    assert compare.build_row("X", closes=closes).year_pct == pytest.approx(100.0)


def test_earnings_already_past_reads_negative_rather_than_hiding():
    row = compare.build_row("X", earnings_date=dt.date(2026, 8, 1),
                            today=dt.date(2026, 8, 14))
    assert row.days_to_earnings == -13


def test_rows_keep_her_own_order():
    """The switcher row above is newest-first; a table that reshuffles under it
    is a table she has to re-read."""
    rows = [compare.build_row(s) for s in ("AAPL", "SMH", "NVDA")]
    ordered = compare.sort_rows(rows, ["NVDA", "AAPL", "SMH"])
    assert [r.symbol for r in ordered] == ["NVDA", "AAPL", "SMH"]


def test_sorting_without_an_order_changes_nothing():
    rows = [compare.build_row(s) for s in ("AAPL", "SMH")]
    assert [r.symbol for r in compare.sort_rows(rows)] == ["AAPL", "SMH"]


def test_junk_numbers_never_reach_a_column():
    row = compare.build_row("X", price="not a number",
                            info={"trailingPE": None, "revenueGrowth": "n/a"})
    assert row.price is None
    assert row.pe is None
    assert row.rev_growth_pct is None


def test_the_dataframe_has_one_row_per_ticker():
    from ui import components

    rows = [compare.build_row(s, price=10.0, closes=_closes())
            for s in ("NVDA", "AAPL")]
    frame = components.compare_dataframe(rows)
    assert list(frame["Symbol"]) == ["NVDA", "AAPL"]
    assert set(components.compare_column_config()).issubset(set(frame.columns))
