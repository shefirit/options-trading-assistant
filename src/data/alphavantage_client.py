"""Long earnings history from Alpha Vantage (free key, works on the hosted app).

Yahoo blocks its earnings endpoint from datacenter IPs, so on Streamlit Cloud the
"expected vs delivered" chart could only show ~4 quarters. Alpha Vantage's EARNINGS
endpoint is a plain API (not IP-blocked) and returns 100+ quarters of reported vs
estimated EPS, so we can show years of history.

The key is read from Streamlit secrets first (hosted app), then a local gitignored
file, then an env var - so it never has to live in the code.
"""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = PROJECT_ROOT / "alphavantage_key.txt"
_BASE = "https://www.alphavantage.co/query"


def get_key() -> Optional[str]:
    """The API key from st.secrets (cloud) -> local file -> env var."""
    try:
        import streamlit as st
        k = st.secrets.get("alphavantage_key")
        if k:
            return str(k).strip()
    except Exception:
        pass
    if KEY_FILE.exists():
        v = KEY_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    v = os.environ.get("ALPHAVANTAGE_KEY")
    return v.strip() if v else None


def set_key(key: str) -> None:
    """Save the key to the local gitignored file (for running on this PC)."""
    KEY_FILE.write_text(key.strip(), encoding="utf-8")


def is_configured() -> bool:
    return bool(get_key())


def _f(v: Any) -> Optional[float]:
    """Float, treating Alpha Vantage's 'None'/'' blanks and NaN as missing."""
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def get_eps_history(symbol: str, max_quarters: int = 24,
                    key: Optional[str] = None) -> list[dict[str, Any]]:
    """Reported vs estimated EPS per quarter, oldest-first, same shape the chart uses:
    {label, date, estimate, actual, surprise_pct, beat}. Empty list on any problem
    (no key, rate limit, network) so callers can fall back to Yahoo."""
    key = key or get_key()
    if not key:
        return []
    params = urllib.parse.urlencode(
        {"function": "EARNINGS", "symbol": symbol.upper(), "apikey": key})
    try:
        with urllib.request.urlopen(f"{_BASE}?{params}", timeout=15) as r:
            data = json.load(r)
    except Exception:
        return []
    quarters = data.get("quarterlyEarnings")
    if not quarters:   # rate-limited/invalid -> a "Note"/"Information" message, no data
        return []

    out: list[dict[str, Any]] = []
    for row in quarters:
        actual = _f(row.get("reportedEPS"))
        if actual is None:
            continue
        est = _f(row.get("estimatedEPS"))
        sp = _f(row.get("surprisePercentage"))
        beat = (sp >= 0) if sp is not None else (actual >= est if est is not None else None)
        fde = row.get("fiscalDateEnding") or ""
        try:
            y, m, _ = fde.split("-")
            label = f"{int(y)} Q{(int(m) - 1) // 3 + 1}"
        except Exception:
            label = fde or "?"
        out.append({
            "label": label,
            "date": row.get("reportedDate") or fde or None,
            "estimate": est,
            "actual": actual,
            "surprise_pct": sp,
            "beat": beat,
        })

    out = out[:max_quarters]   # Alpha Vantage returns newest-first; keep the recent N
    out.reverse()              # oldest-first for the chart
    return out


# Alpha Vantage's OVERVIEW field -> the Yahoo `info` key the app already reads,
# with how to convert it. Keeping Yahoo's names means every caller - the stats
# strip, the scorecard, the fundamentals metrics - works on the fallback without
# knowing it is one.
_OVERVIEW_MAP: dict[str, tuple[str, str]] = {
    "MarketCapitalization": ("marketCap", "num"),
    "PERatio": ("trailingPE", "num"),
    "ForwardPE": ("forwardPE", "num"),
    "PriceToSalesRatioTTM": ("priceToSalesTrailing12Months", "num"),
    "RevenueTTM": ("totalRevenue", "num"),
    "QuarterlyRevenueGrowthYOY": ("revenueGrowth", "num"),
    "QuarterlyEarningsGrowthYOY": ("earningsGrowth", "num"),
    "EPS": ("trailingEps", "num"),
    "ProfitMargin": ("profitMargins", "num"),
    "DividendYield": ("dividendYield", "num"),
    # Dollars per share carries no unit ambiguity, and dividend_yield_pct
    # prefers it over the yield field for exactly that reason.
    "DividendPerShare": ("dividendRate", "num"),
    "Beta": ("beta", "num"),
    "52WeekHigh": ("fiftyTwoWeekHigh", "num"),
    "52WeekLow": ("fiftyTwoWeekLow", "num"),
    "Name": ("shortName", "text"),
    "Exchange": ("exchange", "text"),
}

# AssetType -> Yahoo's quoteType, which is what tells a fund from a company.
_ASSET_TYPES = {"common stock": "EQUITY", "etf": "ETF", "mutual fund": "MUTUALFUND"}

# OVERVIEW carries the Wall Street tally too, which is the OTHER thing Yahoo
# stops answering from a datacenter IP ("No analyst data available for this
# name" on every stock on the hosted app). Same request, so reading it here
# costs nothing extra against the free daily quota.
_RATING_FIELDS = {
    "AnalystRatingStrongBuy": "strong_buy",
    "AnalystRatingBuy": "buy",
    "AnalystRatingHold": "hold",
    "AnalystRatingSell": "sell",
    "AnalystRatingStrongSell": "strong_sell",
}


def get_overview(symbol: str, key: Optional[str] = None) -> dict[str, Any]:
    """Company fundamentals shaped like Yahoo's `info` dict, or {} on any problem.

    The stand-in for when Yahoo's company-info endpoint refuses a datacenter IP,
    which on Streamlit Cloud it does often enough that Rita saw two ordinary
    mid-caps report "did not load" across the whole panel. This endpoint is a
    plain keyed API, so it answers from the hosted app exactly as it does here.

    NOT a full replacement - there is no average volume here, and the app already
    recovers that from price history. It covers what the scorecard and the stats
    strip actually print.
    """
    key = key or get_key()
    if not key:
        return {}
    params = urllib.parse.urlencode(
        {"function": "OVERVIEW", "symbol": symbol.upper(), "apikey": key})
    try:
        with urllib.request.urlopen(f"{_BASE}?{params}", timeout=15) as r:
            data = json.load(r)
    except Exception:
        return {}
    # A rate limit or an unknown ticker comes back as {} or as a "Note" /
    # "Information" message - either way there is no Symbol in it.
    if not isinstance(data, dict) or not data.get("Symbol"):
        return {}

    out: dict[str, Any] = {}
    for src, (dest, how) in _OVERVIEW_MAP.items():
        raw = data.get(src)
        if raw in (None, "", "None", "-"):
            continue
        if how == "num":
            v = _f(raw)
            if v is not None:
                out[dest] = v
        else:
            out[dest] = str(raw)

    sector = str(data.get("Sector") or "").strip()
    if sector:
        out["sector"] = sector.title()   # Alpha Vantage shouts: "TECHNOLOGY"
    asset = str(data.get("AssetType") or "").strip().lower()
    if asset in _ASSET_TYPES:
        out["quoteType"] = _ASSET_TYPES[asset]
    if out.get("shortName"):
        out["longName"] = out["shortName"]

    # Nested rather than flattened: these are not a Yahoo `info` field, and
    # pretending otherwise would let them be read by accident.
    ratings = {name: int(v) for field, name in _RATING_FIELDS.items()
               if (v := _f(data.get(field))) is not None}
    if any(ratings.values()):
        out["analystRatings"] = ratings
    return out
