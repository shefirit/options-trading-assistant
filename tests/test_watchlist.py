"""The watchlist: names the Full sweep must always screen.

The sweep keeps only finalists that clear every bar, so a name she is actively
researching can vanish on a day it misses one. These are force-added so she
sees the reason rather than a gap.
"""

from __future__ import annotations

import json

from src.data import stock_universe, watchlist


def test_it_reads_back_what_was_saved(tmp_path):
    p = tmp_path / "watchlist.json"
    assert watchlist.save(["SOFI", "RIVN"], p) == ["SOFI", "RIVN"]
    assert watchlist.read(p) == ["SOFI", "RIVN"]


def test_tickers_are_uppercased_and_trimmed(tmp_path):
    p = tmp_path / "watchlist.json"
    assert watchlist.save([" sofi ", "rivn"], p) == ["SOFI", "RIVN"]


def test_duplicates_go_but_order_stays(tmp_path):
    p = tmp_path / "watchlist.json"
    assert watchlist.save(["SOFI", "AAPL", "sofi"], p) == ["SOFI", "AAPL"]


def test_junk_entries_are_dropped(tmp_path):
    p = tmp_path / "watchlist.json"
    assert watchlist.save(["SOFI", "", "   ", None, "WAYTOOLONGTICKER", "A B"], p) == ["SOFI"]


def test_it_is_capped(tmp_path):
    """Past a few dozen the sweep slows down for little gain."""
    p = tmp_path / "watchlist.json"
    many = [f"SYM{i}" for i in range(60)]
    assert len(watchlist.save(many, p)) == watchlist.MAX_SYMBOLS


def test_no_file_is_an_empty_list_not_a_crash(tmp_path):
    assert watchlist.read(tmp_path / "nope.json") == []


def test_a_corrupt_file_does_not_take_the_picks_tab_down(tmp_path):
    p = tmp_path / "watchlist.json"
    p.write_text("{ this is not json", encoding="utf-8")
    assert watchlist.read(p) == []


def test_a_bare_list_file_still_reads(tmp_path):
    """Tolerate a hand-edited file that is just a JSON array."""
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps(["SOFI", "KO"]), encoding="utf-8")
    assert watchlist.read(p) == ["SOFI", "KO"]


def test_clearing_it_works(tmp_path):
    p = tmp_path / "watchlist.json"
    watchlist.save(["SOFI"], p)
    assert watchlist.save([], p) == []
    assert watchlist.read(p) == []


# ---------------- the widened universe ----------------

def test_the_universe_now_reaches_past_the_sp500():
    """SOFI is over $20B and outside the S&P 500 - the case that started this."""
    universe = set(stock_universe.all_stocks())
    assert "SOFI" in universe
    assert "SOFI" not in set(stock_universe.sp500())
    assert len(universe) > 900


def test_every_stock_has_a_market_cap():
    """The screen rejects a stock for being too small only when it HAS a cap, so
    a missing one silently skips the size bar. Coverage has to be complete or
    the bar quietly stops applying to exactly the newer, smaller names."""
    caps = stock_universe.market_caps()
    missing = [s for s in stock_universe.all_stocks() if s not in caps]
    assert not missing, f"{len(missing)} stocks have no market cap: {missing[:10]}"
