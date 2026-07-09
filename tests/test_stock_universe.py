"""Tests for the stock universe loader (uses the saved ticker files)."""

from __future__ import annotations

from src.data import stock_universe


def test_sp500_loaded():
    sp = stock_universe.sp500()
    assert len(sp) > 400          # S&P 500 is ~500 names
    assert "AAPL" in sp


def test_nasdaq100_loaded():
    nq = stock_universe.nasdaq100()
    assert len(nq) > 90
    assert "AAPL" in nq


def test_all_stocks_union_sorted():
    alls = stock_universe.all_stocks()
    assert alls == sorted(alls)
    assert "MSFT" in alls
    assert len(alls) > 450


def test_is_stock():
    assert stock_universe.is_stock("AAPL") is True
    assert stock_universe.is_stock("aapl") is True   # case-insensitive
    assert stock_universe.is_stock("SPX") is False   # index, not a stock


def test_liquid_etfs_curated_and_clean():
    etfs = stock_universe.liquid_etfs()
    assert "SPY" in etfs and "QQQ" in etfs
    assert all(s == s.upper() for s in etfs)
    # No leveraged or volatility products in the curated set.
    for banned in ("TQQQ", "SOXL", "SQQQ", "VXX", "UVXY", "SVXY"):
        assert banned not in etfs


def test_largest_etfs_biggest_funds_first():
    top = stock_universe.largest_etfs(5)
    assert top[:2] == ["SPY", "QQQ"]             # the two biggest by assets
    assert len(top) == 5
    # Never returns more ETFs than exist, and n=0 is empty.
    assert len(stock_universe.largest_etfs(999)) == len(stock_universe.liquid_etfs())
    assert stock_universe.largest_etfs(0) == []


def test_largest_stocks_ranked_by_market_cap():
    top = stock_universe.largest_stocks(10)
    assert len(top) == 10
    for mega in ("AAPL", "MSFT", "NVDA"):        # all deep-options mega-caps
        assert mega in stock_universe.largest_stocks(25)
    caps = stock_universe.market_caps()
    if caps:                                     # ranking is non-increasing by cap
        vals = [caps[s] for s in top if s in caps]
        assert vals == sorted(vals, reverse=True)


def test_largest_stocks_empty_without_caps(monkeypatch):
    """A missing caps file yields an empty list (callers fall back), not an error."""
    monkeypatch.setattr(stock_universe, "market_caps", lambda: {})
    assert stock_universe.largest_stocks(20) == []
