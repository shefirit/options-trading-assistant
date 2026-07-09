"""Stage-1 screen tests - synthetic price series, no network."""

from __future__ import annotations

from src.data import market_screener as ms


def _series(n: int = 70, base: float = 100.0, drift: float = 0.4,
            jitter: float = 0.008, volume: float = 5_000_000):
    """Daily closes with a controllable trend (drift/day) and volatility
    (alternating +/- jitter), plus a flat share volume."""
    closes = [(base + drift * i) * (1 + jitter * (-1) ** i) for i in range(n)]
    return closes, [volume] * n


RULES = ms.ScreenRules()


def test_rules_from_config_overrides_and_defaults():
    rules = ms.rules_from_config({"min_price": 25, "max_stock_finalists": 5, "junk": 1})
    assert rules.min_price == 25
    assert rules.max_stock_finalists == 5
    assert rules.min_dollar_volume == 200_000_000     # default kept


def test_a_solid_large_name_passes():
    closes, vols = _series()
    r = ms.build_result("AAPL", "stock", closes, vols, RULES, market_cap=3e12)
    assert r.passed, r.reject_reason
    assert r.trend == "up"
    assert r.hv is not None and RULES.hv_min <= r.hv <= RULES.hv_max
    assert r.dollar_volume and r.dollar_volume >= RULES.min_dollar_volume


def test_reject_reasons_fire_one_by_one():
    closes, vols = _series()

    short, short_v = _series(n=30)
    assert "history" in ms.build_result("X", "stock", short, short_v, RULES).reject_reason

    cheap, cheap_v = _series(base=8.0, drift=0.02, jitter=0.012)
    assert "price under" in ms.build_result("X", "stock", cheap, cheap_v, RULES,
                                            market_cap=2e10).reject_reason

    small_cap = ms.build_result("X", "stock", closes, vols, RULES, market_cap=5e9)
    assert "not large enough" in small_cap.reject_reason

    thin, thin_v = _series(volume=50_000)
    assert "too few dollars" in ms.build_result("X", "stock", thin, thin_v, RULES,
                                                market_cap=2e10).reject_reason

    calm, calm_v = _series(jitter=0.0005)
    assert "too calm" in ms.build_result("X", "stock", calm, calm_v, RULES,
                                         market_cap=2e10).reject_reason

    wild, wild_v = _series(jitter=0.06, base=300.0)
    assert "swings too hard" in ms.build_result("X", "stock", wild, wild_v, RULES,
                                                market_cap=2e10).reject_reason

    falling, falling_v = _series(base=300.0, drift=-1.2)
    assert "downtrend" in ms.build_result("X", "stock", falling, falling_v, RULES,
                                          market_cap=2e10).reject_reason


def test_unknown_market_cap_falls_back_to_volume_proxy():
    """No caps file (or a missing name) must not reject a stock on size alone."""
    closes, vols = _series()
    r = ms.build_result("NEW", "stock", closes, vols, RULES, market_cap=None)
    assert r.passed, r.reject_reason


def test_etfs_are_never_size_gated_by_market_cap():
    closes, vols = _series()
    r = ms.build_result("SPY", "etf", closes, vols, RULES, market_cap=None)
    assert r.passed


def test_finalists_are_capped_per_kind_and_ranked_by_dollar_volume():
    results = []
    for i in range(25):    # 25 passing stocks, rising dollar volume
        closes, vols = _series(volume=3_000_000 + i * 1_000_000)
        results.append(ms.build_result(f"S{i}", "stock", closes, vols, RULES, market_cap=5e10))
    for i in range(12):    # 12 passing ETFs
        closes, vols = _series(volume=4_000_000 + i * 1_000_000)
        results.append(ms.build_result(f"E{i}", "etf", closes, vols, RULES))
    assert all(r.passed for r in results)

    picked = ms.finalists(results, RULES)
    stocks = [r for r in picked if r.kind == "stock"]
    etfs = [r for r in picked if r.kind == "etf"]
    assert len(stocks) == RULES.max_stock_finalists
    assert len(etfs) == RULES.max_etf_finalists
    # The biggest names made it; the smallest were cut.
    assert {r.symbol for r in stocks} == {f"S{i}" for i in range(5, 25)}
    assert {r.symbol for r in etfs} == {f"E{i}" for i in range(2, 12)}
    vols_order = [r.dollar_volume for r in picked]
    assert vols_order == sorted(vols_order, reverse=True)


def test_funnel_note_counts_the_stages():
    closes, vols = _series()
    ok = ms.build_result("A", "stock", closes, vols, RULES, market_cap=5e10)
    bad = ms.build_result("B", "stock", closes[:20], vols[:20], RULES)
    etf = ms.build_result("C", "etf", closes, vols, RULES)
    picked = ms.finalists([ok, bad, etf], RULES)
    note = ms.funnel_note([ok, bad, etf], picked)
    assert "2 stocks" in note and "1 ETFs" in note
    assert "2 cleared" in note and "top 2" in note
