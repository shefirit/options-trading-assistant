"""The list of stocks you can trade: the full S&P 500 and Nasdaq-100.

The ticker lists live in sample_data/sp500.json and nasdaq100.json (refreshed
with tools/refresh_universe.py). You can also just type any ticker you want.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "sample_data"

# A small hand-picked shortlist shown first for quick access (big, liquid names).
FEATURED = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "JPM", "V", "COST", "HD", "WMT", "KO", "PG", "JNJ",
]


def _load(name: str) -> list[str]:
    path = DATA_DIR / name
    if path.exists():
        try:
            return [str(t) for t in json.loads(path.read_text(encoding="utf-8"))]
        except Exception:
            return []
    return []


@functools.lru_cache(maxsize=1)
def sp500() -> list[str]:
    return _load("sp500.json")


@functools.lru_cache(maxsize=1)
def nasdaq100() -> list[str]:
    return _load("nasdaq100.json")


@functools.lru_cache(maxsize=1)
def all_stocks() -> list[str]:
    """Every tradable stock ticker (S&P 500 + Nasdaq-100 + featured), sorted."""
    combined = set(sp500()) | set(nasdaq100()) | set(FEATURED)
    combined = {t for t in combined if t and t.isupper() and 1 <= len(t) <= 6}
    return sorted(combined)


def is_stock(symbol: str) -> bool:
    return symbol.upper() in set(all_stocks())


def in_nasdaq100(symbol: str) -> bool:
    return symbol.upper() in set(nasdaq100())
