"""Loads settings.yaml and strategies.yaml so the rest of the app never
hardcodes a number. Change a rule in the YAML and everything follows.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

# Project root = two levels up from this file (src/engine/ -> project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@functools.lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    """Account, targets, risk limits, and allowed underlyings."""
    return _load_yaml(CONFIG_DIR / "settings.yaml")


@functools.lru_cache(maxsize=1)
def load_strategies() -> dict[str, Any]:
    """All 8 strategy definitions, keyed by strategy key."""
    data = _load_yaml(CONFIG_DIR / "strategies.yaml")
    return data.get("strategies", {})


def get_strategy(strategy_key: str) -> dict[str, Any]:
    strategies = load_strategies()
    if strategy_key not in strategies:
        raise KeyError(
            f"Unknown strategy '{strategy_key}'. "
            f"Known: {', '.join(sorted(strategies))}"
        )
    return strategies[strategy_key]


def allowed_underlyings_for(strategy_key: str) -> list[str]:
    """Which tickers this strategy may run on, based on option style.

    Credit spreads accept both European- and US-style names (SPX is the usual
    pick but not the only one). Covered calls / CSP / PMCC need US-style names.
    """
    from src.data import stock_universe

    settings = load_settings()
    strategy = get_strategy(strategy_key)
    style = strategy.get("underlying_style", "us")
    european = settings["underlyings"]["european_style"]
    us = settings["underlyings"]["us_style"]
    # US-style strategies (cash secured puts, covered calls, PMCC) can run on ETFs
    # plus any S&P 500 / Nasdaq-100 stock.
    us_all = list(us) + stock_universe.all_stocks()
    if style == "european_or_us":
        return list(european) + us_all
    if style == "european":
        return list(european)
    return us_all


def underlying_kind(underlying: str) -> str:
    """'index' (European, cash-settled) | 'etf' (US-style ETF) | 'stock'.

    Drives the SOP spread width: indexes 25-50 points, ETFs $25-50, stocks $5-10.
    """
    settings = load_settings()
    u = underlying.upper()
    if u in {s.upper() for s in settings["underlyings"]["european_style"]}:
        return "index"
    if u in {s.upper() for s in settings["underlyings"]["us_style"]}:
        return "etf"
    return "stock"


def is_european_style(underlying: str) -> bool:
    """True for cash-settled European-style index names (SPX, NDX, RUT, XSP).

    They have no early-assignment risk, so the SOP lets you enter as early as 21
    DTE. US-style stocks/ETFs can be assigned early, so they enter nearer 45.
    """
    european = load_settings()["underlyings"]["european_style"]
    return underlying.upper() in {s.upper() for s in european}


def clear_cache() -> None:
    """Call after editing a YAML file so the new values are picked up."""
    load_settings.cache_clear()
    load_strategies.cache_clear()
