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
