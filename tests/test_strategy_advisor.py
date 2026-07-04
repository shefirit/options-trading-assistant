"""Tests for the symbol -> strategy advisor (pure logic, no network)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from src.data import stock_analysis
from src.engine.strategy_advisor import advise


def _tv(daily="Buy", weekly="Buy"):
    return {"daily": SimpleNamespace(recommendation=daily),
            "weekly": SimpleNamespace(recommendation=weekly)}


def _rising(n=260):
    return [100 + i * 0.4 for i in range(n)]


def _falling(n=260):
    return [200 - i * 0.4 for i in range(n)]


STRONG_INFO = {
    "shortName": "Solid Co", "sector": "Tech", "marketCap": 500e9,
    "trailingPE": 22.0, "profitMargins": 0.25, "revenueGrowth": 0.12,
    "averageVolume": 8_000_000, "currentPrice": 200.0,
}


def _analysis(info=None, closes=None):
    return stock_analysis.analyze("TEST", info or STRONG_INFO, closes or _rising())


def test_index_uptrend_recommends_put_credit_spread():
    a = advise("SPX", "index", 7500, trend="up", tv=_tv())
    assert a.primary.key == "put_credit_spread"


def test_index_sideways_recommends_iron_condor():
    a = advise("SPX", "index", 7500, trend="sideways", tv=_tv("Neutral", "Neutral"))
    assert a.primary.key == "iron_condor"


def test_index_downtrend_recommends_call_credit_spread():
    a = advise("NDX", "index", 29000, trend="down", tv=_tv("Sell", "Sell"))
    assert a.primary.key == "call_credit_spread"


def test_bullish_affordable_stock_recommends_csp():
    a = advise("TEST", "stock", 200.0, trend="up", tv=_tv("Strong Buy", "Buy"),
               analysis=_analysis())
    assert a.outlook == "bullish"
    assert a.primary.key == "cash_secured_put"
    # PMCC offered as the capital-light alternative.
    assert any(alt.key == "poor_mans_covered_call" for alt in a.alternatives)


def test_expensive_stock_prefers_pmcc():
    # 100 shares at $900 = $90k > her $50k monthly buying-power limit.
    info = dict(STRONG_INFO, currentPrice=900.0)
    a = advise("COST", "stock", 900.0, trend="up", tv=_tv(),
               analysis=_analysis(info))
    assert a.primary.key == "poor_mans_covered_call"
    assert any("monthly buying-power limit" in c for c in a.cautions)


def test_bearish_stock_gets_no_primary():
    a = advise("WEAK", "stock", 150.0, trend="down", tv=_tv("Sell", "Strong Sell"),
               analysis=_analysis(closes=_falling()))
    assert a.outlook == "bearish"
    assert a.primary is None
    assert any("Wait for the trend" in c for c in a.cautions)
    # Bearish view redirected to the index, per her SOP.
    assert any(alt.key == "call_credit_spread" for alt in a.alternatives)


def test_low_grade_stock_is_avoided():
    weak_info = dict(STRONG_INFO, profitMargins=-0.1, revenueGrowth=-0.2,
                     trailingPE=-5, averageVolume=50_000)
    a = advise("JUNK", "stock", 20.0, trend="up", tv=_tv(),
               analysis=_analysis(weak_info, _falling()))
    assert a.outlook == "avoid"
    assert a.primary is None


def test_earnings_inside_window_warns():
    today = dt.date(2026, 7, 2)
    a = advise("TEST", "stock", 200.0, trend="up", tv=_tv(),
               analysis=_analysis(),
               earnings_date=dt.date(2026, 7, 20), today=today)
    assert any("Earnings on Jul 20" in c for c in a.cautions)


def test_etf_skips_quality_gate():
    # ETFs have odd "fundamentals" - they must not be auto-avoided.
    a = advise("SPY", "etf", 750.0, trend="up", tv=_tv())
    assert a.outlook == "bullish"
    assert a.primary is not None


def test_monthly_dte_note_always_present():
    a = advise("SPX", "index", 7500, trend="up", tv=_tv())
    assert "21-35" in a.dte_note


def _cc_keys(a):
    return [alt.key for alt in a.alternatives if alt.key.startswith("covered_call")]


def test_covered_call_model3_when_bullish_and_calm():
    # Confident + calm market -> the zero-cost ratio (Model 3).
    a = advise("TEST", "stock", 200.0, trend="up", vix=12.0, tv=_tv("Strong Buy", "Buy"),
               analysis=_analysis())
    assert a.outlook == "bullish"
    assert "covered_call_model_3" in _cc_keys(a)


def test_covered_call_model1_when_fear_is_high():
    # Elevated fear -> the protective collar (Model 1), even on a strong stock.
    a = advise("TEST", "stock", 200.0, trend="up", vix=26.0, tv=_tv("Strong Buy", "Buy"),
               analysis=_analysis())
    assert "covered_call_model_1" in _cc_keys(a)


def test_covered_call_model2_when_neutral():
    # Steady/neutral -> the classic covered call (Model 2).
    a = advise("TEST", "stock", 200.0, trend="sideways", vix=15.0, tv=_tv("Neutral", "Neutral"),
               analysis=_analysis())
    assert a.outlook == "neutral"
    assert "covered_call_model_2" in _cc_keys(a)
