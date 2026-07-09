"""Stage-1 market screen: cut the whole market down to the names worth a deep look.

The Picks tab can't fetch 550 option chains, so it screens first with cheap
price/volume history (one batched download) and only the survivors get the
full option-chain analysis. The bar, per Rita's rules: credible, LARGE, and
genuinely liquid - plus calm enough to sell premium on but not dead calm.

Pure logic - the app hands in the numbers, this decides who passes and why.
Every threshold comes from config/settings.yaml `picks:` so Rita can tune her
own bar without touching code.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from src.data.market_context import trend_from_prices
from src.data.premium_finder import annualized_vol

# Trend needs 50 closes and volatility needs 31 - under this there is no read.
MIN_HISTORY = 55


class ScreenRules(BaseModel):
    """The bar a name must clear (defaults mirror config/settings.yaml picks:)."""

    min_dollar_volume: float = 200_000_000    # avg $ traded/day - options liquidity proxy
    min_market_cap: float = 10_000_000_000    # stocks only: large companies only
    min_price: float = 15.0
    hv_min: float = 0.12                      # calmer than this pays no real premium
    hv_max: float = 0.80                      # wilder than this is danger, not income
    max_stock_finalists: int = 20
    max_etf_finalists: int = 10


def rules_from_config(cfg: Optional[dict]) -> ScreenRules:
    """ScreenRules from the settings.yaml `picks:` section (defaults fill gaps)."""
    cfg = cfg or {}
    fields = {k: cfg[k] for k in ScreenRules.model_fields if k in cfg}
    return ScreenRules(**fields)


class ScreenResult(BaseModel):
    """One name's screen outcome, with the plain-English reason when it fails."""

    symbol: str
    kind: str                                 # "stock" | "etf"
    price: Optional[float] = None
    dollar_volume: Optional[float] = None     # avg dollars traded per day
    hv: Optional[float] = None                # realized volatility, e.g. 0.25 = 25%/yr
    trend: str = "unknown"
    market_cap: Optional[float] = None
    passed: bool = False
    reject_reason: str = ""


def build_result(
    symbol: str,
    kind: str,
    closes: list[float],
    volumes: list[float],
    rules: ScreenRules,
    market_cap: Optional[float] = None,
    lookback: int = 30,
) -> ScreenResult:
    """Apply the screen to one name's daily closes + share volumes (oldest first)."""
    res = ScreenResult(symbol=symbol.upper(), kind=kind, market_cap=market_cap)
    if len(closes) < MIN_HISTORY:
        res.reject_reason = "no usable price history from the batch download"
        return res

    res.price = round(closes[-1], 2)
    window = min(lookback, len(closes))
    dollars = [c * v for c, v in zip(closes[-window:], volumes[-window:])]
    res.dollar_volume = round(sum(dollars) / len(dollars), 0) if dollars else None
    res.hv = annualized_vol(closes)
    res.trend = trend_from_prices(closes)

    if res.price < rules.min_price:
        res.reject_reason = (f"price under ${rules.min_price:g} - junk-priced names are "
                             "assignment traps")
    elif (kind == "stock" and market_cap is not None
          and market_cap < rules.min_market_cap):
        res.reject_reason = (f"market cap under ${rules.min_market_cap / 1e9:g}B - "
                             "not large enough for your bar")
    elif res.dollar_volume is None or res.dollar_volume < rules.min_dollar_volume:
        res.reject_reason = ("trades too few dollars a day - its options will have wide, "
                             "costly bid-ask spreads")
    elif res.hv is None:
        res.reject_reason = "couldn't compute how much it really moves"
    elif res.hv < rules.hv_min:
        res.reject_reason = (f"too calm ({res.hv * 100:.0f}%/yr) - the premium isn't "
                             "worth selling")
    elif res.hv > rules.hv_max:
        res.reject_reason = (f"swings too hard ({res.hv * 100:.0f}%/yr) - 'great premium' "
                             "here is really great danger")
    elif res.trend == "down":
        res.reject_reason = ("in a downtrend - your SOP never sells puts into a "
                             "falling name")
    else:
        res.passed = True
    return res


def finalists(results: list[ScreenResult], rules: ScreenRules) -> list[ScreenResult]:
    """The passers that get a full option-chain look: the biggest dollar-volume
    names first, capped per kind so stocks can't crowd out ETFs or vice versa."""
    passed = sorted((r for r in results if r.passed),
                    key=lambda r: r.dollar_volume or 0.0, reverse=True)
    caps = {"stock": rules.max_stock_finalists, "etf": rules.max_etf_finalists}
    taken = {"stock": 0, "etf": 0}
    out: list[ScreenResult] = []
    for r in passed:
        if taken.get(r.kind, 0) < caps.get(r.kind, 0):
            out.append(r)
            taken[r.kind] = taken.get(r.kind, 0) + 1
    return out


def funnel_note(results: list[ScreenResult], picked: list[ScreenResult]) -> str:
    """The one-line 'how the funnel narrowed' summary shown above the tables."""
    n_stocks = sum(1 for r in results if r.kind == "stock")
    n_etfs = sum(1 for r in results if r.kind == "etf")
    n_passed = sum(1 for r in results if r.passed)
    return (f"{n_stocks} stocks and {n_etfs} ETFs screened - {n_passed} cleared the size, "
            f"liquidity, trend, and volatility bars - the top {len(picked)} got the full "
            "option-chain look.")
