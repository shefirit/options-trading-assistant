"""The Analyze tab's remembered tickers - kept on disk between visits.

Her ask after the session-only version shipped: "make them permanent like the
watchlist." Same storage shape, different job - this one is written
automatically and only decides what the dropdown offers.
"""

from __future__ import annotations

import json

import pytest

from src.data import recent_symbols, watchlist


@pytest.fixture
def store(tmp_path):
    return tmp_path / "analyze_recents.json"


def test_nothing_saved_yet_reads_as_empty(store):
    assert recent_symbols.read(store) == []


def test_a_remembered_ticker_survives_a_reread(store):
    recent_symbols.remember("TQQQ", store)
    assert recent_symbols.read(store) == ["TQQQ"]


def test_the_newest_ticker_comes_first(store):
    """The one she wants next is almost always the one she looked at last."""
    recent_symbols.remember("TQQQ", store)
    recent_symbols.remember("SQQQ", store)
    assert recent_symbols.read(store) == ["SQQQ", "TQQQ"]


def test_remembering_the_same_ticker_twice_does_not_duplicate_it(store):
    recent_symbols.remember("TQQQ", store)
    recent_symbols.remember("SQQQ", store)
    recent_symbols.remember("TQQQ", store)
    assert recent_symbols.read(store) == ["TQQQ", "SQQQ"]


def test_reselecting_the_front_ticker_is_a_no_op(store):
    """It runs on every rerun while she sits on one name - it must not churn
    the file or reshuffle anything."""
    recent_symbols.remember("SQQQ", store)
    recent_symbols.remember("TQQQ", store)
    before = store.read_text(encoding="utf-8")
    recent_symbols.remember("TQQQ", store)
    assert store.read_text(encoding="utf-8") == before


def test_the_list_is_capped(store):
    for i in range(recent_symbols.MAX_SYMBOLS + 10):
        recent_symbols.remember(f"AA{i:02d}", store)
    kept = recent_symbols.read(store)
    assert len(kept) == recent_symbols.MAX_SYMBOLS
    # The cap drops the OLDEST, never the one she just looked at.
    assert kept[0] == f"AA{recent_symbols.MAX_SYMBOLS + 9:02d}"


def test_junk_never_reaches_the_file(store):
    for bad in (None, "", "   ", "toolongticker", "BAD TICKER", 42):
        recent_symbols.remember(bad, store)
    assert recent_symbols.read(store) == []


def test_a_corrupt_file_reads_as_empty_rather_than_exploding(store):
    """Losing this list costs one retype; taking the Analyze tab down does not."""
    store.write_text("{ not json", encoding="utf-8")
    assert recent_symbols.read(store) == []


def test_it_survives_a_file_written_as_a_bare_list(store):
    store.write_text(json.dumps(["TQQQ", "SQQQ"]), encoding="utf-8")
    assert recent_symbols.read(store) == ["TQQQ", "SQQQ"]


def test_the_cleaning_rules_are_shared_with_the_watchlist():
    """Two copies of "what counts as a ticker" is how they drift apart."""
    assert recent_symbols.clean_symbols is watchlist.clean_symbols


def test_the_watchlist_keeps_its_own_cap():
    many = [f"AA{i:02d}" for i in range(watchlist.MAX_SYMBOLS + 5)]
    assert len(watchlist.clean_symbols(many)) == watchlist.MAX_SYMBOLS
    assert len(watchlist.clean_symbols(many, recent_symbols.MAX_SYMBOLS)) \
        == recent_symbols.MAX_SYMBOLS
