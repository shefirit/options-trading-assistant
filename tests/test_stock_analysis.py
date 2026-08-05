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


def test_missing_info_volume_falls_back_to_history_volume():
    """On the hosted app Yahoo drops the info volume field even for huge names.
    A liquid stock must not be flagged illiquid when the fallback is supplied."""
    no_vol = dict(STRONG)
    no_vol.pop("averageVolume")
    # Without a fallback, volume is unknown -> flagged not liquid.
    a = stock_analysis.analyze("NVDA", no_vol, _rising())
    assert a.liquid is False
    # With the history-derived fallback (e.g. NVDA ~150M shares/day), it's liquid.
    b = stock_analysis.analyze("NVDA", no_vol, _rising(), avg_volume=150_000_000)
    assert b.liquid is True
    assert b.suitable is True
    vol_metric = next(m for m in b.technicals if m.label == "Avg daily volume")
    assert vol_metric.status == "good"


def test_info_volume_wins_over_fallback_when_present():
    a = stock_analysis.analyze("SOLID", STRONG, _rising(), avg_volume=50_000)
    vol_metric = next(m for m in a.technicals if m.label == "Avg daily volume")
    assert "8.0M" in vol_metric.value   # used the info field, not the fallback


# ============================================================================
# Missing data must not read as a bad company.
#
# Yahoo throttles its fundamentals from cloud hosts, which is exactly Rita's
# hosted setup. Every absent field used to score "watch" - identical to a
# genuinely bad number - so NVDA came back graded F, called a fund, and
# described as not trading enough volume. All three were the app inventing a
# verdict out of a failed fetch.
# ============================================================================
def _rising_closes():
    return [100 * (1.0006 ** i) for i in range(260)]


FULL_INFO = {"quoteType": "EQUITY", "sector": "Technology", "marketCap": 3.4e12,
             "trailingPE": 18.0, "profitMargins": 0.55, "revenueGrowth": 0.60,
             "averageVolume": 2.0e8}


def test_a_throttled_fetch_does_not_grade_the_company_at_all():
    """No grade beats a wrong grade. A letter off one surviving metric is a
    guess wearing a report card, and she reads these to pick what to sell."""
    a = stock_analysis.analyze("NVDA", {"quoteType": "EQUITY", "sector": "Technology"},
                _rising_closes())
    assert a.grade is None
    assert a.data_partial is True
    assert "did not load" in a.summary or "not enough" in a.summary.lower()


def test_a_missing_number_is_never_a_caution():
    a = stock_analysis.analyze("NVDA", {"quoteType": "EQUITY", "sector": "Technology"},
                _rising_closes())
    for m in a.fundamentals:
        assert m.status != "watch", f"{m.label} scored a caution for missing data"
        assert m.status == "unknown"


def test_missing_volume_is_not_reported_as_illiquid():
    """The exact sentence she would have seen for the most heavily traded stock
    in the market: "does not trade enough volume for comfortable options
    trading. Better to pick a bigger, more liquid name.\""""
    a = stock_analysis.analyze("NVDA", FULL_INFO | {"averageVolume": None}, _rising_closes())
    assert a.liquidity_checked is False
    assert "does not trade enough volume" not in a.summary
    assert "did not load" in a.summary


def test_liquidity_stays_conservative_when_it_could_not_be_checked():
    """Unverified is not the same as verified-good. The app must not call a
    name tradeable on data it never saw."""
    a = stock_analysis.analyze("NVDA", FULL_INFO | {"averageVolume": None}, _rising_closes())
    assert a.liquid is False and a.liquidity_checked is False


def test_a_pe_that_did_not_load_makes_no_claim_about_profits():
    """It used to read "No P/E - the company may not have steady profits",
    turning an absent field into an accusation."""
    a = stock_analysis.analyze("NVDA", FULL_INFO | {"trailingPE": None}, _rising_closes())
    pe = next(m for m in a.fundamentals if "P/E" in m.label)
    assert pe.status == "unknown"
    assert "may not" not in pe.read.lower()


def test_full_data_still_grades_normally():
    """The fix must not soften a real verdict - only stop inventing one."""
    a = stock_analysis.analyze("NVDA", FULL_INFO, _rising_closes())
    assert a.grade in ("A", "B")
    assert a.data_partial is False and a.liquidity_checked is True


def test_a_genuinely_bad_number_still_counts_against_it():
    weak = FULL_INFO | {"marketCap": 4e8, "profitMargins": -0.25,
                        "revenueGrowth": -0.30, "trailingPE": -5.0}
    a = stock_analysis.analyze("TINY", weak, _rising_closes())
    assert a.grade in ("D", "F")
    assert any(m.status == "watch" for m in a.fundamentals)


def test_the_grade_is_scored_only_on_metrics_that_arrived():
    """Two good numbers out of two known must not be dragged down by two that
    never loaded."""
    half = {"quoteType": "EQUITY", "sector": "Technology", "marketCap": 3.4e12,
            "profitMargins": 0.55, "averageVolume": 2.0e8}
    a = stock_analysis.analyze("NVDA", half, _rising_closes())
    assert a.grade in ("A", "B"), f"graded {a.grade} on blanks"
