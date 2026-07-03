"""Generates a realistic sample SPX option chain and saves it to
tests/fixtures/spx_chain.json. This lets the scanner be tested and demoed with
no live connection. Uses the Black-Scholes model so deltas and prices look real.

Run:  python tools/generate_fixture.py
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.chain import OptionChain, OptionContract  # noqa: E402
from src.engine.models import OptionType  # noqa: E402


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(S: float, K: float, T: float, r: float, iv: float, is_call: bool):
    """Return (delta, price) from Black-Scholes."""
    if T <= 0:
        intrinsic = max(0.0, (S - K) if is_call else (K - S))
        return (1.0 if is_call and S > K else 0.0), intrinsic
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    if is_call:
        delta = _norm_cdf(d1)
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return delta, max(price, 0.05)


def build_chain(underlying: str, S: float, low: int, high: int, step: int,
                iv: float = 0.15, r: float = 0.04) -> OptionChain:
    today = date(2026, 7, 2)
    contracts: list[OptionContract] = []

    # Weekly-style expirations so the DTE slider (21-35) actually changes results,
    # plus 45/60 for longer-dated strategies like PMCC checks.
    for dte in (21, 24, 28, 30, 32, 35, 45, 60):
        T = dte / 365.0
        exp = (today + timedelta(days=dte)).isoformat()
        strike = low
        while strike <= high:
            for is_call in (True, False):
                delta, price = bs(S, strike, T, r, iv, is_call)
                spread = max(0.05, price * 0.02)   # a small bid/ask spread
                contracts.append(OptionContract(
                    option_type=OptionType.CALL if is_call else OptionType.PUT,
                    strike=float(strike),
                    expiration=exp,
                    dte=dte,
                    delta=round(delta, 4),
                    gamma=0.0,
                    theta=round(-price * 0.01, 4),
                    vega=round(price * 0.05, 4),
                    iv=iv,
                    bid=round(max(price - spread / 2, 0.01), 2),
                    ask=round(price + spread / 2, 2),
                ))
            strike += step

    return OptionChain(
        underlying=underlying,
        underlying_price=S,
        fetched_at=today.isoformat(),
        contracts=contracts,
    )


if __name__ == "__main__":
    # Written to two places: sample_data/ (ships with the app for demo mode) and
    # tests/fixtures/ (used by the test suite). Same content, both kept in sync.
    out_dirs = [ROOT / "sample_data", ROOT / "tests" / "fixtures"]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)

    # SPX: index-sized, $5 strikes, wide range so both spread sides have room.
    spx = build_chain("SPX", 5100.0, low=4500, high=5700, step=5)
    # SPY: US-style ETF (for cash secured puts / covered calls), $1 strikes.
    spy = build_chain("SPY", 510.0, low=440, high=580, step=1)

    for d in out_dirs:
        spx.to_json(d / "spx_chain.json")
        spy.to_json(d / "spy_chain.json")
    print(f"Wrote {len(spx.contracts)} SPX and {len(spy.contracts)} SPY contracts to "
          f"{', '.join(str(d) for d in out_dirs)}")
