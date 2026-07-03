"""Refresh the S&P 500 and Nasdaq-100 ticker lists from Wikipedia.

Run occasionally to keep the tradable universe current:
    python tools/refresh_universe.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT = ROOT / "sample_data"


def _html(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def _clean(tickers) -> list[str]:
    out = {str(t).replace(".", "-").strip().upper() for t in tickers}
    return sorted(t for t in out if t and t.isupper() and 1 <= len(t) <= 6)


def refresh() -> None:
    OUT.mkdir(exist_ok=True)

    sp = pd.read_html(io.StringIO(_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")))[0]
    sp500 = _clean(sp["Symbol"].astype(str))
    (OUT / "sp500.json").write_text(json.dumps(sp500), encoding="utf-8")

    tabs = pd.read_html(io.StringIO(_html("https://en.wikipedia.org/wiki/Nasdaq-100")))
    nasdaq100: list[str] = []
    for tbl in tabs:
        cols = [str(c) for c in tbl.columns]
        match = next((c for c in tbl.columns if "Ticker" in str(c) or "Symbol" in str(c)), None)
        if match is not None:
            nasdaq100 = _clean(tbl[match].astype(str))
            if nasdaq100:
                break
    if nasdaq100:
        (OUT / "nasdaq100.json").write_text(json.dumps(nasdaq100), encoding="utf-8")

    print(f"Saved {len(sp500)} S&P 500 and {len(nasdaq100)} Nasdaq-100 tickers to {OUT}")


if __name__ == "__main__":
    refresh()
