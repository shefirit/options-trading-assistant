"""Black-Scholes greeks.

Free data sources (like Yahoo) give us the option's price and its implied
volatility, but not its delta. Delta is the number your SOP rules are built on
(short leg under 0.10), so we compute it here from the same inputs a broker uses.
The numbers line up closely with what you see in thinkorswim.
"""

from __future__ import annotations

import math

RISK_FREE_RATE = 0.04   # a reasonable short-term rate; delta is barely sensitive to it


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def compute(
    spot: float, strike: float, dte: int, iv: float, is_call: bool,
    r: float = RISK_FREE_RATE,
) -> dict[str, float]:
    """Return delta, gamma, theta, vega for one option.

    delta is signed the standard way: calls positive, puts negative.
    theta is per day; vega is per 1 percentage-point change in volatility.
    """
    T = max(dte, 0) / 365.0
    if T <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        # At/■after expiration delta is just "in the money or not".
        itm = (spot > strike) if is_call else (spot < strike)
        return {"delta": (1.0 if is_call else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT

    if is_call:
        delta = _norm_cdf(d1)
        theta = (-(spot * _norm_pdf(d1) * iv) / (2 * sqrtT)
                 - r * strike * math.exp(-r * T) * _norm_cdf(d2))
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * _norm_pdf(d1) * iv) / (2 * sqrtT)
                 + r * strike * math.exp(-r * T) * _norm_cdf(-d2))

    gamma = _norm_pdf(d1) / (spot * iv * sqrtT)
    vega = spot * _norm_pdf(d1) * sqrtT / 100.0   # per 1 vol point
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta / 365.0, 4),   # per day
        "vega": round(vega, 4),
    }
