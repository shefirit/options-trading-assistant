"""provider.get_chain narrows the Yahoo fetch window to what a scan actually
needs, instead of always pulling every expiration 15-70 DTE out. Fewer
requests per scan = faster and less likely to trip Yahoo's rate limit."""

from __future__ import annotations

from src.data import cache
from src.data.chain import OptionChain
from src.data.provider import DataProvider


def _empty_chain(underlying="SPX"):
    return OptionChain(underlying=underlying, underlying_price=5000.0, contracts=[])


def test_default_window_matches_old_behavior(monkeypatch):
    cache.clear()
    calls = []
    monkeypatch.setattr(
        "src.data.yfinance_client.get_option_chain",
        lambda underlying, from_dte=15, to_dte=70: calls.append((from_dte, to_dte)) or _empty_chain())
    provider = DataProvider("yahoo")
    provider.get_chain("SPX")
    assert calls == [(15, 70)]


def test_custom_window_is_forwarded_and_cached_separately(monkeypatch):
    cache.clear()
    calls = []
    monkeypatch.setattr(
        "src.data.yfinance_client.get_option_chain",
        lambda underlying, from_dte=15, to_dte=70: calls.append((from_dte, to_dte)) or _empty_chain())
    provider = DataProvider("yahoo")
    provider.get_chain("SPX", dte_min=14, dte_max=52)
    provider.get_chain("SPX", dte_min=14, dte_max=52)   # second call should hit the cache
    provider.get_chain("SPX")                           # different window -> a real fetch

    assert calls == [(14, 52), (15, 70)]
