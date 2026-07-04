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
