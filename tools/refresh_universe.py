"""Refresh the S&P 500 and Nasdaq-100 ticker lists from Wikipedia.

Run occasionally to keep the tradable universe current:
    python tools/refresh_universe.py          # ticker lists only
    python tools/refresh_universe.py caps     # market caps only (slow, ~5-10 min)
    python tools/refresh_universe.py all      # both

The market caps feed the Picks tab's "large companies only" screen. They only
need to be roughly right, so refreshing them every month or two is plenty.
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


def refresh_market_caps() -> None:
    """Fetch every S&P 500 name's market cap into sample_data/market_caps.json.

    One Yahoo call per ticker - run it locally (residential IP), not on the
    cloud. Names that fail are simply left out; the screen falls back to
    dollar volume as its size proxy for them.
    """
    import datetime as dt

    import yfinance as yf

    from src.data import stock_universe

    tickers = stock_universe.sp500()
    caps: dict[str, float] = {}
    for i, sym in enumerate(tickers, 1):
        try:
            cap = float(yf.Ticker(sym).fast_info["market_cap"])
            if cap > 0:
                caps[sym] = cap
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  ...{i}/{len(tickers)} ({len(caps)} caps so far)")
    payload = {
        "_comment": ("Approximate market caps in dollars for the Picks tab's "
                     "'large companies only' screen. Regenerate with: "
                     "python tools/refresh_universe.py caps"),
        "as_of": dt.date.today().isoformat(),
        "caps": caps,
    }
    (OUT / "market_caps.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"Saved {len(caps)} market caps to {OUT / 'market_caps.json'}")


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if arg == "caps":
        refresh_market_caps()
    elif arg == "all":
        refresh()
        refresh_market_caps()
    else:
        refresh()
