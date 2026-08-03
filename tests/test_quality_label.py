"""The Quality column must not call a stock an ETF.

`grade or "ETF"` printed "ETF" whenever the letter grade was missing. On the
hosted app Yahoo throttles the fundamentals call from datacenter IPs, so the
grade goes missing routinely and real companies were labelled as funds - which
is exactly what Rita saw for SOFI in the Picks table.
"""

from __future__ import annotations

from ui.components import quality_label


def test_a_real_grade_is_shown_as_is():
    assert quality_label("SOFI", "B") == "B"
    assert quality_label("AAPL", "A") == "A"
    assert quality_label("XYZ", "F") == "F"


def test_an_ungraded_stock_is_not_called_an_etf():
    """The bug: SOFI showed as ETF whenever fundamentals were throttled."""
    assert quality_label("SOFI", None) == "—"
    assert quality_label("AAPL", None) == "—"


def test_a_real_etf_still_says_etf():
    assert quality_label("SPY", None) == "ETF"
    assert quality_label("QQQ", None) == "ETF"


def test_an_index_says_index_rather_than_etf():
    """SPX is not a fund either - you cannot own shares of it."""
    assert quality_label("SPX", None) == "Index"
    assert quality_label("NDX", None) == "Index"


def test_a_missing_symbol_does_not_blow_up():
    assert quality_label("", None) == "—"
    assert quality_label(None, None) == "—"
