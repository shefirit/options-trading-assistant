"""Real option chains from Tradier (free developer token, works on the hosted app).

Yahoo blocks option-chain requests from datacenter IPs (Streamlit Cloud), which is
why the scanner kept hitting "Yahoo temporarily blocked new requests." Tradier's API
is a plain keyed endpoint that is NOT IP-blocked, so it serves reliable chains from
the hosted app. Its free developer sandbox gives ~15-minute delayed data - fine for
21-45 day trades.

Setup for the user (once):
  1. Sign up free at developer.tradier.com and open the Dashboard.
  2. Copy the sandbox Access Token.
  3. Paste it into the app: sidebar "Options data (Tradier)" -> Save, OR on the hosted
     app add it under Settings -> Secrets as:  tradier_token = "YOUR_TOKEN"

The token is read from Streamlit secrets first (hosted), then a local gitignored
file, then an env var.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from src.data import greeks
from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = PROJECT_ROOT / "tradier_token.txt"

# Sandbox = free, delayed data. Production needs a funded/brokerage token.
_SANDBOX_BASE = "https://sandbox.tradier.com/v1"
_PROD_BASE = "https://api.tradier.com/v1"


def get_key() -> Optional[str]:
    """The Tradier token from st.secrets (cloud) -> local file -> env var."""
    try:
        import streamlit as st
        k = st.secrets.get("tradier_token")
        if k:
            return str(k).strip()
    except Exception:
        pass
    if KEY_FILE.exists():
        v = KEY_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    import os
    v = os.environ.get("TRADIER_TOKEN")
    return v.strip() if v else None


def set_key(token: str) -> None:
    KEY_FILE.write_text(token.strip(), encoding="utf-8")


def is_configured() -> bool:
    return bool(get_key())


def _base() -> str:
    """Sandbox by default; set tradier_env='production' in secrets to use live."""
    try:
        import streamlit as st
        if str(st.secrets.get("tradier_env", "")).strip().lower() == "production":
            return _PROD_BASE
    except Exception:
        pass
    return _SANDBOX_BASE


# Tradier uses plain root symbols (no leading "^" like Yahoo).
def tradier_symbol(underlying: str) -> str:
    return underlying.upper().lstrip("^")


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _request(path: str, params: dict) -> dict:
    token = get_key()
    if not token:
        raise RuntimeError("No Tradier token saved.")
    url = _base() + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _as_list(node: Any) -> list:
    """Tradier returns a single object when there's one result, a list when many."""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def get_price(underlying: str) -> Optional[float]:
    try:
        data = _request("/markets/quotes", {"symbols": tradier_symbol(underlying)})
        quotes = _as_list((data.get("quotes") or {}).get("quote"))
        if not quotes:
            return None
        q = quotes[0]
        price = q.get("last") or q.get("close") or q.get("prevclose")
        return _f(price) or None
    except Exception:
        return None


def _expirations(symbol: str) -> list[str]:
    data = _request("/markets/options/expirations",
                    {"symbol": symbol, "includeAllRoots": "true", "strikes": "false"})
    node = (data.get("expirations") or {}).get("date")
    return [str(d) for d in _as_list(node)]


def _dte(exp: str, today: Optional[date] = None) -> Optional[int]:
    try:
        d = datetime.strptime(exp, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - (today or date.today())).days


def _contracts_for_expiration(symbol: str, exp: str, dte: int, spot: float) -> list[OptionContract]:
    data = _request("/markets/options/chains",
                    {"symbol": symbol, "expiration": exp, "greeks": "true"})
    out: list[OptionContract] = []
    for o in _as_list((data.get("options") or {}).get("option")):
        otype = OptionType.CALL if o.get("option_type") == "call" else OptionType.PUT
        strike = _f(o.get("strike"))
        g = o.get("greeks") or {}
        iv = _f(g.get("mid_iv") or g.get("smv_vol"))
        # Prefer computing greeks from IV (consistent with the Yahoo path and robust
        # to Tradier returning null greeks on delayed data); fall back to Tradier's.
        if iv > 0 and spot > 0 and strike > 0 and dte >= 0:
            gk = greeks.compute(spot, strike, dte, iv, otype == OptionType.CALL)
            delta, gamma, theta, vega = gk["delta"], gk["gamma"], gk["theta"], gk["vega"]
        else:
            delta = _f(g.get("delta"))
            gamma, theta, vega = _f(g.get("gamma")), _f(g.get("theta")), _f(g.get("vega"))
        out.append(OptionContract(
            option_type=otype, strike=strike, expiration=exp, dte=dte,
            delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv,
            bid=_f(o.get("bid")), ask=_f(o.get("ask")),
            volume=int(_f(o.get("volume"))), open_interest=int(_f(o.get("open_interest"))),
        ))
    return out


def get_option_chain(underlying: str, from_dte: int = 15, to_dte: int = 70) -> OptionChain:
    """A real option chain for the DTE window, greeks computed from IV."""
    symbol = tradier_symbol(underlying)
    spot = get_price(underlying) or 0.0
    contracts: list[OptionContract] = []
    for exp in _expirations(symbol):
        d = _dte(exp)
        if d is not None and from_dte <= d <= to_dte:
            contracts.extend(_contracts_for_expiration(symbol, exp, d, spot))
    return OptionChain(underlying=underlying.upper(), underlying_price=spot,
                       fetched_at=date.today().isoformat(), contracts=contracts)


def get_expiration_chain(underlying: str, target_dte: int = 30) -> OptionChain:
    """Just the single expiration nearest target_dte (fast - two API calls)."""
    symbol = tradier_symbol(underlying)
    spot = get_price(underlying) or 0.0
    dated = [(e, d) for e in _expirations(symbol)
             if (d := _dte(e)) is not None and d >= 1]
    if not dated:
        return OptionChain(underlying=underlying.upper(), underlying_price=spot, contracts=[])
    exp, dte = min(dated, key=lambda ed: abs(ed[1] - target_dte))
    return OptionChain(underlying=underlying.upper(), underlying_price=spot,
                       fetched_at=date.today().isoformat(),
                       contracts=_contracts_for_expiration(symbol, exp, dte, spot))


def is_available(timeout: float = 8.0) -> bool:
    """True if a token is set and Tradier answers - used to pick it as the source."""
    if not is_configured():
        return False
    import concurrent.futures

    def _check() -> bool:
        try:
            return bool(_expirations("SPY"))
        except Exception:
            return False
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_check).result(timeout=timeout)
    except Exception:
        return False
