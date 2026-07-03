"""Talks to the Schwab Trader API and hands back our normalized OptionChain.

Setup (one time, Phase 0):
  1. Create an app at https://developer.schwab.com (Trader API - Individual).
     Set the callback URL to match SCHWAB_CALLBACK_URL in your .env
     (e.g. https://127.0.0.1:8182). Wait for the app status to become
     "Ready for Use" - this can take a few days.
  2. Copy .env.example to .env and paste in your App Key and App Secret.
  3. Run:  python -m src.data.schwab_client
     A browser window opens once for you to log in; a token.json is saved.
     After that the app logs in silently.

This module is written so the rest of the app never sees Schwab's raw format -
it always gets back the same OptionChain object the scanner and tests use.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType

# Index names Schwab expects with a leading "$".
_INDEX_SYMBOLS = {
    "SPX": "$SPX",
    "NDX": "$NDX",
    "RUT": "$RUT",
    "XSP": "$XSP",
    "VIX": "$VIX",
    "DJX": "$DJX",
}


def schwab_symbol(underlying: str) -> str:
    return _INDEX_SYMBOLS.get(underlying.upper(), underlying.upper())


class SchwabNotConfigured(RuntimeError):
    """Raised when .env / token are missing, so the UI can show a friendly note."""


class SchwabClient:
    """Thin wrapper around schwab-py. Import of schwab-py is lazy so the rest of
    the app (and the tests) run fine on a machine that has not set Schwab up yet.
    """

    def __init__(self, client: Any):
        self._client = client

    # ---------- construction ----------
    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> "SchwabClient":
        load_dotenv(env_path)
        app_key = os.getenv("SCHWAB_APP_KEY")
        app_secret = os.getenv("SCHWAB_APP_SECRET")
        callback = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
        token_path = os.getenv("SCHWAB_TOKEN_PATH", "token.json")

        if not app_key or not app_secret or app_key.startswith("your_"):
            raise SchwabNotConfigured(
                "Schwab is not set up yet. Copy .env.example to .env and add your "
                "App Key and App Secret from developer.schwab.com."
            )
        try:
            from schwab.auth import easy_client
        except ImportError as e:  # pragma: no cover - only if dependency missing
            raise SchwabNotConfigured(
                "The schwab-py library is not installed. Run: pip install schwab-py"
            ) from e

        client = easy_client(
            api_key=app_key,
            app_secret=app_secret,
            callback_url=callback,
            token_path=token_path,
        )
        return cls(client)

    @staticmethod
    def is_configured(env_path: str | Path = ".env") -> bool:
        load_dotenv(env_path)
        key = os.getenv("SCHWAB_APP_KEY")
        return bool(key) and not str(key).startswith("your_")

    # ---------- market data ----------
    def get_option_chain(self, underlying: str, from_dte: int = 20, to_dte: int = 70) -> OptionChain:
        """Fetch a chain and normalize it. Only pulls a DTE window to stay light."""
        resp = self._client.get_option_chain(schwab_symbol(underlying))
        data = resp.json()
        return _parse_chain(underlying, data, from_dte, to_dte)

    def get_quote(self, underlying: str) -> dict[str, Any]:
        resp = self._client.get_quote(schwab_symbol(underlying))
        return resp.json()

    def get_price(self, underlying: str) -> Optional[float]:
        try:
            q = self.get_quote(underlying)
            # Schwab quote shape: { "$SPX": { "quote": { "lastPrice": ... } } }
            first = next(iter(q.values()))
            return float(first.get("quote", {}).get("lastPrice"))
        except Exception:
            return None

    def get_buying_power(self) -> Optional[float]:
        """Best-effort read of available buying power from the first account."""
        try:
            accounts = self._client.get_accounts().json()
            acct = accounts[0]["securitiesAccount"]
            bal = acct.get("currentBalances", {})
            return float(bal.get("buyingPower") or bal.get("cashBalance") or 0.0)
        except Exception:
            return None


def _parse_chain(underlying: str, data: dict, from_dte: int, to_dte: int) -> OptionChain:
    """Convert Schwab's callExpDateMap / putExpDateMap into our OptionChain."""
    price = float(data.get("underlyingPrice") or 0.0)
    contracts: list[OptionContract] = []

    for opt_type, key in ((OptionType.CALL, "callExpDateMap"), (OptionType.PUT, "putExpDateMap")):
        exp_map = data.get(key, {}) or {}
        for exp_key, strikes in exp_map.items():
            # exp_key looks like "2026-08-15:44" (date:daysToExpiration)
            exp_date = exp_key.split(":")[0]
            for _strike, rows in strikes.items():
                if not rows:
                    continue
                row = rows[0]
                dte = int(row.get("daysToExpiration", 0))
                if not (from_dte <= dte <= to_dte):
                    continue
                contracts.append(OptionContract(
                    option_type=opt_type,
                    strike=float(row.get("strikePrice", 0.0)),
                    expiration=exp_date,
                    dte=dte,
                    delta=_num(row.get("delta")),
                    gamma=_num(row.get("gamma")),
                    theta=_num(row.get("theta")),
                    vega=_num(row.get("vega")),
                    iv=_num(row.get("volatility")) / 100.0 if row.get("volatility") else 0.0,
                    bid=_num(row.get("bid")),
                    ask=_num(row.get("ask")),
                ))

    return OptionChain(underlying=underlying, underlying_price=price, contracts=contracts)


def _num(value: Any) -> float:
    """Schwab uses -999.0 / 'NaN' for missing greeks - treat those as 0."""
    try:
        f = float(value)
        return 0.0 if f <= -999 else f
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":  # pragma: no cover - manual Phase 0 check
    print("Connecting to Schwab...")
    client = SchwabClient.from_env()
    chain = client.get_option_chain("SPX")
    bp = client.get_buying_power()
    print(f"Pulled {len(chain.contracts)} SPX contracts. Price={chain.underlying_price}")
    print(f"Account buying power: {bp}")
    near = chain.nearest_dte(45)
    print(f"Sample 45-DTE puts near the money:")
    for c in chain.by(OptionType.PUT, near)[:5]:
        print(f"  strike {c.strike:g}  delta {c.delta:+.3f}  mid {c.mid}")
