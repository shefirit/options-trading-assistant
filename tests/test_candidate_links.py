"""The Barchart links on the Candidate tab - they have to land somewhere real."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from ui.candidate import IV_RANK_URL, barchart_url


def test_symbol_link_points_at_that_symbols_options_history():
    url = barchart_url("AAPL", "stock")
    assert url == "https://www.barchart.com/stocks/quotes/AAPL/options-history"


def test_symbol_link_prefixes_indexes_with_a_dollar_and_encodes_it():
    # Barchart writes indexes as $SPX, and a bare $ does not survive a URL.
    url = barchart_url("SPX", "index")
    assert url == "https://www.barchart.com/stocks/quotes/%24SPX/options-history"


def test_symbol_link_does_not_double_the_dollar():
    assert barchart_url("$NDX", "index") == barchart_url("NDX", "index")


def test_symbol_link_leaves_etfs_alone():
    assert "%24" not in barchart_url("QQQ", "etf")


def test_symbol_link_uppercases():
    assert barchart_url("mu", "stock").endswith("/MU/options-history")


def test_screener_link_is_deep_linked_to_a_sorted_view():
    parts = urlparse(IV_RANK_URL)
    q = parse_qs(parts.query)
    assert parts.netloc == "www.barchart.com"
    assert parts.path.endswith("/iv-rank-percentile/high")
    # Sorted by IV Rank, highest first - the view that is actually useful.
    assert q["orderBy"] == ["optionsImpliedVolatilityRank1y"]
    assert q["orderDir"] == ["desc"]
