"""Unit normalizers for Yahoo's fundamentals fields.

Every case in here was a real wrong number in the app at some point. The
values are the ones Yahoo actually returned on 2026-07-25, not invented ones.
"""

from __future__ import annotations

import pytest

from src.research import criteria, fundamentals


# ---------- dividend yield ----------
def test_the_dollar_rate_settles_the_units():
    # AAPL as Yahoo reports it: rate in dollars, yield already a percent.
    info = {"trailingAnnualDividendRate": 1.04, "dividendYield": 0.34}
    assert fundamentals.dividend_yield_pct(info, 333.02) == pytest.approx(0.312, abs=0.01)


def test_a_fifth_of_a_percent_is_not_a_fifth_of_the_price():
    """The old threshold sat at 0.25, so any yield reported in percent form
    below that was multiplied by 100. AAPL and QQQ escaped by luck at 0.34
    and 0.41; anything genuinely under 0.25% did not."""
    assert fundamentals.dividend_yield_pct({"dividendYield": 0.20}) == pytest.approx(0.20)


def test_non_payers_and_junk_come_back_as_zero():
    assert fundamentals.dividend_yield_pct({}) == 0.0
    assert fundamentals.dividend_yield_pct({"dividendYield": 0}) == 0.0
    assert fundamentals.dividend_yield_pct({"dividendYield": 900}) == 0.0


# ---------- debt to equity ----------
@pytest.mark.parametrize("symbol, yahoo, expected", [
    ("MNST", 1.082, 0.011),      # one of the least indebted large caps there is
    ("ODFL", 0.909, 0.009),
    ("NVDA", 6.555, 0.066),      # used to clear the old >5 guard by a hair
    ("GOOGL", 18.859, 0.189),
    ("KO", 124.943, 1.249),
    ("T", 129.054, 1.291),
])
def test_debt_to_equity_is_always_a_percent(symbol, yahoo, expected):
    got = fundamentals.debt_to_equity_ratio({"debtToEquity": yahoo})
    assert got == pytest.approx(expected, abs=0.001), symbol


def test_a_nearly_debt_free_company_is_not_called_leveraged():
    """Monster reports 1.082, meaning 0.011x. The old rule kept any value at
    or below 5 as-is, turning that into 1.08x - a company with essentially no
    debt scored as though it carried a dollar of debt per dollar of equity."""
    assert fundamentals.debt_to_equity_ratio({"debtToEquity": 1.082}) < 0.02


def test_missing_or_negative_debt_is_unknown_not_zero():
    assert fundamentals.debt_to_equity_ratio({}) is None
    assert fundamentals.debt_to_equity_ratio({"debtToEquity": None}) is None
    assert fundamentals.debt_to_equity_ratio({"debtToEquity": "n/a"}) is None
    assert fundamentals.debt_to_equity_ratio({"debtToEquity": -5}) is None


# ---------- the Instant Analyzer reads the same way ----------
def test_instant_analyzer_agrees_with_the_shared_normalizer():
    info = {"debtToEquity": 1.082, "trailingAnnualDividendRate": 2.06,
            "dividendYield": 2.58}
    values = criteria.extract(info, {"price": 82.25})
    assert values["debt_to_equity"] == pytest.approx(0.011, abs=0.001)
    assert values["dividend_yield"] == pytest.approx(2.505, abs=0.01)


def test_a_debt_free_stock_passes_a_low_debt_rule():
    """The bug that mattered: she writes "debt to equity below 0.5" and the
    most debt-free names in the market failed it."""
    rules = [criteria.Criterion(field="debt_to_equity", op="<=", value=0.5)]
    result = criteria.evaluate("MNST", rules, {"debtToEquity": 1.082}, {})
    assert result.verdict == "pass"
    assert [r.passed for r in result.rules] == [True]
