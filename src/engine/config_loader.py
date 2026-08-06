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


def underlying_fits_style(strategy_key: str, underlying: str) -> bool:
    """Can this strategy run on this ticker, including one typed by hand?

    allowed_underlyings_for() can only offer names the universe files know, and
    those cover the S&P 500 and Nasdaq-100 only. A liquid name outside them
    (SOFI, HOOD) is still a legitimate underlying - the SOP says "any liquid
    stock, ETF, or index" - so the real constraint is option style: European
    strategies need a cash-settled index, US-style ones need something with
    shares behind it.
    """
    settings = load_settings()
    style = get_strategy(strategy_key).get("underlying_style", "us")
    is_index = underlying.upper() in {s.upper()
                                      for s in settings["underlyings"]["european_style"]}
    if style == "european":
        return is_index
    if style == "us":
        return not is_index
    return True


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


def default_spread_width(underlying: str) -> float:
    """The SOP spread width for this name, in points.

    One place, because the width is a POINT distance and the SOP's tiers assume
    an SPX-sized index. A name at a different scale needs the same rule applied
    at its own scale, which is what the by_symbol overrides in settings.yaml
    are for - XSP being one tenth of SPX is the case that forced it.
    """
    settings = load_settings()
    cfg = settings.get("spread_widths", {}) or {}
    u = underlying.upper()
    for sym, width in (cfg.get("by_symbol", {}) or {}).items():
        if str(sym).upper() == u:
            return float(width)
    return float(cfg.get(underlying_kind(u), 25))


def is_european_style(underlying: str) -> bool:
    """True for cash-settled European-style index names (SPX, NDX, RUT, XSP).

    They have no early-assignment risk, so the SOP lets you enter as early as 21
    DTE. US-style stocks/ETFs can be assigned early, so they enter nearer 45.
    """
    european = load_settings()["underlyings"]["european_style"]
    return underlying.upper() in {s.upper() for s in european}


def entry_dte_window(strategy: dict[str, Any], underlying: str,
                     dte_min: int = 21, dte_max: int = 44) -> tuple[int, int]:
    """The days-to-expiration span this strategy may enter `underlying` at.

    Credit spreads and cash secured puts carry their own dte_min/dte_max; covered
    calls and PMCC only name a short-call target, so we take a band around it.
    US-style names then override with their wider window (they avoid the ~21-DTE
    early-assignment zone).
    """
    entry = strategy.get("entry", {})
    if "dte_min" in entry and "dte_max" in entry:
        lo, hi = int(entry["dte_min"]), int(entry["dte_max"])
    elif "short_call_dte_min" in entry and "short_call_dte_max" in entry:
        # An explicit window beats one guessed around the target. The covered
        # call models carry 30-45 now, and target +/- a guess would have made
        # the scan reach out to 51.
        lo, hi = int(entry["short_call_dte_min"]), int(entry["short_call_dte_max"])
    elif "short_call_dte_target" in entry:
        t = int(entry["short_call_dte_target"])
        lo, hi = max(14, t - 7), t + 14
    else:
        lo, hi = dte_min, dte_max
    if not is_european_style(underlying):
        lo = max(lo, int(entry.get("dte_min_us_style", lo)))
        hi = int(entry.get("dte_max_us_style", hi))
    return lo, hi


def preferred_entry_dte(strategy: dict[str, Any], underlying: str) -> int | None:
    """The days-to-expiration the SOP actually wants at entry (45 on the credit
    spreads), clamped into the window this underlying is allowed to use.

    The window alone is not enough to rank setups by: 21 and 45 both sit inside
    it, and only one of them leaves room before the 21-DTE time exit.
    """
    entry = strategy.get("entry", {})
    target = entry.get("dte_target", entry.get("short_call_dte_target"))
    if target is None:
        return None
    lo, hi = entry_dte_window(strategy, underlying)
    return int(min(max(int(target), lo), hi))


def clear_cache() -> None:
    """Call after editing a YAML file so the new values are picked up."""
    load_settings.cache_clear()
    load_strategies.cache_clear()


def default_strategy_key(settings: dict[str, Any],
                         keys: "list[str]") -> str:
    """Which strategy a picker should open on.

    The strategies list keeps her course's order - Model 1, Model 2, Model 3,
    then the spreads - so reordering the file to change the default would
    reorder every dropdown in the app with it. This picks the SELECTED one
    instead, from config/settings.yaml `defaults.strategy`.

    Falls back to the first key when the setting is missing or names a strategy
    that no longer exists, so a typo in the config makes the pickers ordinary
    rather than broken.
    """
    if not keys:
        return ""
    wanted = str((settings.get("defaults") or {}).get("strategy") or "").strip()
    return wanted if wanted in keys else keys[0]
