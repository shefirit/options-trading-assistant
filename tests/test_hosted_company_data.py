"""What the hosted app does when Yahoo refuses to answer about a company.

Yahoo throttles its company-info endpoint from datacenter IPs, so on Streamlit
Cloud it routinely returns nothing for a name it answers perfectly from a
laptop. Three things used to turn that into what looked like a broken ticker:

  1. The blank was CACHED like a real answer - an hour for the stats strip and
     the analyst tally - so one unlucky moment stuck for the rest of the hour.
  2. The strip and the fundamentals checks fetched the same call SEPARATELY, so
     they could disagree on screen: "Mkt Cap n/a" above, "$5.3B" below.
  3. There was no second source, so a blank was simply the end of it.

Rita hit all three on SKWD and ACIW - two ordinary mid-caps that Yahoo has full
data for. These tests pin the fixes. No network: every fetch is a stub.
"""

from __future__ import annotations

import pytest

from src.data import cache
from src.data.provider import DataProvider


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


FULL_INFO = {
    "quoteType": "EQUITY", "shortName": "ACI Worldwide, Inc.",
    "sector": "Technology", "marketCap": 5.29e9, "trailingPE": 23.8,
    "profitMargins": 0.124, "revenueGrowth": 0.073, "averageVolume": 1_045_447,
    "currentPrice": 52.41,
}


def _provider(monkeypatch, *, infos, closes=None, ratings=None, overview=None):
    """A yahoo-mode provider whose every outbound call is a stub.

    `infos` is a list of successive answers from Yahoo's company-info endpoint,
    so a test can hand back a blank and then a real one.
    """
    from src.data import alphavantage_client as av
    from src.data import yfinance_client as yc

    calls = {"info": 0, "overview": 0, "ratings": 0}

    def fake_info(symbol):
        calls["info"] += 1
        return dict(infos[min(calls["info"] - 1, len(infos) - 1)])

    def fake_overview(symbol, key=None):
        calls["overview"] += 1
        return dict(overview or {})

    monkeypatch.setattr(yc, "get_fundamentals", fake_info)
    monkeypatch.setattr(yc, "get_history_closes",
                        lambda s, period="1y": list(closes or [50.0] * 250))
    monkeypatch.setattr(yc, "get_avg_volume", lambda s: 1_000_000)
    monkeypatch.setattr(yc, "get_analyst_ratings",
                        lambda s: dict(ratings) if ratings else {})
    monkeypatch.setattr(av, "is_configured", lambda: bool(overview))
    monkeypatch.setattr(av, "get_overview", fake_overview)
    return DataProvider("yahoo"), calls


# ---------------------------------------------------------------- the cache
def test_blank_company_info_is_not_held_for_an_hour(monkeypatch):
    """The bug she saw: one throttled call, and the name stays broken."""
    p, calls = _provider(monkeypatch, infos=[{}, FULL_INFO])

    assert p.get_raw_info("ACIW") == {}          # throttled
    cache._STORE["info:ACIW"] = (cache._STORE["info:ACIW"][0] - 31,
                                 *cache._STORE["info:ACIW"][1:])
    assert p.get_raw_info("ACIW")["marketCap"] == 5.29e9   # retried, and got it
    assert calls["info"] == 2


def test_a_real_answer_still_gets_the_full_hour(monkeypatch):
    """The retry window must not turn every panel into a fresh Yahoo call."""
    p, calls = _provider(monkeypatch, infos=[FULL_INFO])
    for _ in range(5):
        p.get_raw_info("ACIW")
    assert calls["info"] == 1


def test_an_index_answer_is_kept_even_with_no_economics(monkeypatch):
    """An index has no margins to report and that is not a failed fetch."""
    p, calls = _provider(monkeypatch,
                         infos=[{"quoteType": "INDEX", "shortName": "S&P 500"}])
    p.get_raw_info("SPX")
    p.get_raw_info("SPX")
    assert calls["info"] == 1


# ------------------------------------------------------- one fetch, one truth
def test_strip_and_grade_read_the_same_fetch(monkeypatch):
    """Two panels, one call - they can no longer contradict each other."""
    p, calls = _provider(monkeypatch, infos=[FULL_INFO])

    analysis = p.get_stock_analysis("ACIW")
    info = p.get_raw_info("ACIW")

    assert calls["info"] == 1
    assert info["marketCap"] == 5.29e9
    assert analysis.grade is not None
    cap = next(m for m in analysis.fundamentals if "market cap" in m.label.lower())
    assert cap.value == "$5.3B"


def test_a_partial_verdict_is_not_pinned(monkeypatch):
    """"Not enough loaded to grade it" has to clear itself on a refresh."""
    p, _ = _provider(monkeypatch, infos=[{}, FULL_INFO])

    first = p.get_stock_analysis("ACIW")
    assert first.data_partial and first.grade is None

    for key in ("analysis:ACIW", "info:ACIW"):
        cache._STORE[key] = (cache._STORE[key][0] - 31, *cache._STORE[key][1:])
    assert p.get_stock_analysis("ACIW").grade is not None


# ------------------------------------------------------ the second source
AV_OVERVIEW = {
    "quoteType": "EQUITY", "shortName": "ACI Worldwide Inc", "sector": "Technology",
    "marketCap": 5.29e9, "trailingPE": 23.82, "profitMargins": 0.124,
    "revenueGrowth": 0.073, "trailingEps": 2.2,
    "analystRatings": {"strong_buy": 0, "buy": 3, "hold": 1,
                       "sell": 0, "strong_sell": 0},
}


def test_alpha_vantage_fills_in_when_yahoo_is_blank(monkeypatch):
    p, calls = _provider(monkeypatch, infos=[{}], overview=AV_OVERVIEW)

    analysis = p.get_stock_analysis("ACIW")

    assert calls["overview"] == 1
    assert analysis.grade is not None
    assert not analysis.data_partial
    assert p.get_raw_info("ACIW")["marketCap"] == 5.29e9


def test_yahoo_wins_where_it_answered(monkeypatch):
    """The fallback fills gaps; it never overwrites a fresher live number."""
    p, _ = _provider(monkeypatch,
                     infos=[{"marketCap": 5.4e9}],
                     overview={**AV_OVERVIEW, "marketCap": 5.29e9})
    assert p.get_raw_info("ACIW")["marketCap"] == 5.4e9


def test_alpha_vantage_is_not_called_when_yahoo_answers(monkeypatch):
    """The free key allows 25 requests a day - do not spend them needlessly."""
    p, calls = _provider(monkeypatch, infos=[FULL_INFO], overview=AV_OVERVIEW)
    p.get_stock_analysis("ACIW")
    p.get_raw_info("ACIW")
    assert calls["overview"] == 0


def test_analyst_tally_falls_back_and_shares_one_request(monkeypatch):
    """Yahoo blocks the ratings endpoint separately from company info, so this
    fallback has to fire even when the fundamentals came through - and reuse the
    same Alpha Vantage response rather than buying a second one."""
    p, calls = _provider(monkeypatch, infos=[{}], ratings={}, overview=AV_OVERVIEW)

    p.get_stock_analysis("ACIW")
    assert p.get_analyst_ratings("ACIW")["buy"] == 3
    assert calls["overview"] == 1


def test_empty_analyst_tally_is_not_held_for_an_hour(monkeypatch):
    p, _ = _provider(monkeypatch, infos=[FULL_INFO], ratings={})
    assert p.get_analyst_ratings("ACIW") == {}
    ts, value, ttl = cache._STORE["analysts:ACIW"]
    assert ttl == cache.RETRY_AFTER
