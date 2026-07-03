"""Stock scorecard tests with synthetic data (no network)."""

from __future__ import annotations

from src.data import stock_analysis


def _rising(n=260, start=100.0, step=0.4):
    return [start + i * step for i in range(n)]


def _falling(n=260, start=200.0, step=0.4):
    return [start - i * step for i in range(n)]


STRONG = {
    "shortName": "Solid Co", "sector": "Technology",
    "marketCap": 500e9, "trailingPE": 22.0, "profitMargins": 0.25,
    "revenueGrowth": 0.12, "averageVolume": 8_000_000, "currentPrice": 200.0,
}


def test_rsi_bounds():
    assert stock_analysis.rsi(_rising()) is not None
    assert 0 <= stock_analysis.rsi(_rising()) <= 100
    assert stock_analysis.rsi([1, 2, 3]) is None   # not enough data


def test_uptrend_detected():
    a = stock_analysis.analyze("SOLID", STRONG, _rising())
    trend = next(m for m in a.technicals if m.label == "Trend")
    assert "Up" in trend.value


def test_strong_liquid_company_is_suitable():
    a = stock_analysis.analyze("SOLID", STRONG, _rising())
    assert a.liquid is True
    assert a.suitable is True
    assert "solid" in a.summary.lower() or "candidate" in a.summary.lower()


def test_strong_company_gets_high_grade():
    a = stock_analysis.analyze("SOLID", STRONG, _rising())
    assert a.grade in ("A", "B")


def test_weak_company_gets_low_grade():
    weak = dict(STRONG, profitMargins=-0.1, revenueGrowth=-0.2, trailingPE=-5,
                averageVolume=50_000)
    a = stock_analysis.analyze("WEAK", weak, _falling())
    assert a.grade in ("D", "F")


def test_illiquid_stock_flagged_unsuitable():
    thin = dict(STRONG, averageVolume=50_000)
    a = stock_analysis.analyze("THIN", thin, _rising())
    assert a.liquid is False
    assert a.suitable is False


def test_unprofitable_shrinking_company_has_watch_flags():
    weak = dict(STRONG, profitMargins=-0.1, revenueGrowth=-0.2, trailingPE=-5)
    a = stock_analysis.analyze("WEAK", weak, _falling())
    watches = [m for m in a.fundamentals + a.technicals if m.status == "watch"]
    assert len(watches) >= 2
    assert a.suitable is False
