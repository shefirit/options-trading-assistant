"""Tests for TradingView rating parsing (mocked - no network)."""

from __future__ import annotations

from src.data import tradingview_client as tv


def test_exchange_mapping_from_yahoo_code():
    assert tv.exchange_for({"exchange": "NMS"})[0] == "NASDAQ"
    assert tv.exchange_for({"exchange": "NYQ"})[0] == "NYSE"
    # Unknown code still returns the fallback list (never empty).
    assert tv.exchange_for({"exchange": "???"})


def test_rating_color_by_recommendation():
    r = tv.TVRating(symbol="X", interval="daily", recommendation="Strong Buy")
    assert r.color == "green"
    assert tv.TVRating(symbol="X", interval="daily", recommendation="Sell").color == "red"
    assert tv.TVRating(symbol="X", interval="daily", recommendation="Neutral").color == "orange"


def test_get_ratings_parses_and_tries_exchanges(monkeypatch):
    calls = {"attempts": []}

    class FakeAnalysis:
        summary = {"RECOMMENDATION": "STRONG_BUY", "BUY": 18, "NEUTRAL": 8, "SELL": 0}
        moving_averages = {"RECOMMENDATION": "BUY"}
        oscillators = {"RECOMMENDATION": "NEUTRAL"}

    class FakeHandler:
        def __init__(self, symbol, screener, exchange, interval):
            calls["attempts"].append(exchange)
        def get_analysis(self):
            return FakeAnalysis()

    import tradingview_ta
    monkeypatch.setattr(tradingview_ta, "TA_Handler", FakeHandler)
    monkeypatch.setattr(tv, "_one", tv._one)  # ensure using real _one with fake handler

    ratings = tv.get_ratings("AAPL", ["NASDAQ"])
    assert "daily" in ratings and "weekly" in ratings
    assert ratings["daily"].recommendation == "Strong Buy"
    assert ratings["daily"].buy == 18
    assert ratings["daily"].moving_avg == "Buy"


def test_get_ratings_returns_empty_on_total_failure(monkeypatch):
    import tradingview_ta
    class BoomHandler:
        def __init__(self, *a, **k): pass
        def get_analysis(self): raise RuntimeError("no data")
    monkeypatch.setattr(tradingview_ta, "TA_Handler", BoomHandler)
    assert tv.get_ratings("ZZZZ", ["NASDAQ", "NYSE"]) == {}
