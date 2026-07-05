"""Free, no-signup option chains from CBOE's public delayed-quotes feed.

Yahoo blocks option chains from datacenter IPs (Streamlit Cloud), which is why the
scanner kept getting rate-limited on the hosted app. CBOE (the options exchange)
publishes delayed chains as public JSON with greeks and IV already included - no
API key, no account, and it works from cloud servers. Delayed ~15 minutes, which
is fine for 21-45 day trades.

Endpoint (one call returns the WHOLE chain for a name):
  ETFs / stocks: https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json
  Indices:       https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime
from typing import Any, Optional

from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes/options/"
# Indices are served under a leading underscore on CBOE.
_INDEX = {"SPX", "NDX", "RUT", "XSP", "VIX", "DJX", "MRUT", "XND"}


def cboe_symbol(underlying: str) -> str:
    u = underlying.upper().lstrip("^")
    return ("_" + u) if u in _INDEX else u


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_occ(sym: str):
    """OCC symbol -> (expiration 'YYYY-MM-DD', OptionType, strike). Reads from the
    right so it works for any root length (SPY, SPXW, ...):
      last 8 = strike x1000, then 1 = C/P, then 6 = YYMMDD, the rest is the root."""
    if len(sym) < 15:
        return None
    try:
        strike = int(sym[-8:]) / 1000.0
    except ValueError:
        return None
    cp = sym[-9]
    yymmdd = sym[-15:-9]
    if not yymmdd.isdigit():
        return None
    exp = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    otype = OptionType.CALL if cp == "C" else OptionType.PUT
    return exp, otype, strike


def _fetch(underlying: str) -> dict:
    url = _BASE + cboe_symbol(underlying) + ".json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _spot(data: dict) -> float:
    for key in ("current_price", "close", "prev_day_close", "last_trade_price"):
        v = _f(data.get(key))
        if v > 0:
            return v
    return 0.0


def _contract(o: dict, exp: str, otype: OptionType, strike: float, dte: int) -> OptionContract:
    return OptionContract(
        option_type=otype, strike=strike, expiration=exp, dte=dte,
        delta=_f(o.get("delta")), gamma=_f(o.get("gamma")),
        theta=_f(o.get("theta")), vega=_f(o.get("vega")), iv=_f(o.get("iv")),
        bid=_f(o.get("bid")), ask=_f(o.get("ask")),
        volume=int(_f(o.get("volume"))), open_interest=int(_f(o.get("open_interest"))),
    )


def _build(underlying: str, from_dte: int, to_dte: int,
           nearest_to: Optional[int] = None) -> OptionChain:
    raw = _fetch(underlying)
    data = raw.get("data") or {}
    spot = _spot(data)
    today = date.today()

    parsed = []
    for o in data.get("options", []):
        p = _parse_occ(str(o.get("option", "")))
        if p is None:
            continue
        exp, otype, strike = p
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte < 0:
            continue
        parsed.append((exp, otype, strike, dte, o))

    if nearest_to is not None and parsed:
        # Keep only the single expiration closest to the target (for fast lookups).
        target_exp = min({e for e, _, _, _, _ in parsed},
                         key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - today).days
                                           - nearest_to))
        parsed = [t for t in parsed if t[0] == target_exp]
    else:
        parsed = [t for t in parsed if from_dte <= t[3] <= to_dte]

    contracts = [_contract(o, exp, otype, strike, dte)
                 for (exp, otype, strike, dte, o) in parsed]
    return OptionChain(underlying=underlying.upper(), underlying_price=spot,
                       fetched_at=today.isoformat(), contracts=contracts)


def get_option_chain(underlying: str, from_dte: int = 15, to_dte: int = 70) -> OptionChain:
    """The chain for a DTE window, greeks straight from CBOE."""
    return _build(underlying, from_dte, to_dte)


def get_expiration_chain(underlying: str, target_dte: int = 30) -> OptionChain:
    """Just the single expiration nearest target_dte."""
    return _build(underlying, 0, 3650, nearest_to=target_dte)


def is_available(timeout: float = 8.0) -> bool:
    """True if CBOE's feed is reachable (used to pick it as the chain source)."""
    import concurrent.futures

    def _check() -> bool:
        try:
            return bool((_fetch("SPY").get("data") or {}).get("options"))
        except Exception:
            return False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_check).result(timeout=timeout)
    except Exception:
        return False
