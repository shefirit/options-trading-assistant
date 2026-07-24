"""Analyst ratings and price targets - with the reality check attached.

Wall Street targets are useful as a sentiment reading and close to useless as
a forecast. They are 12-month by convention, they cluster upward (sell ratings
are rare), and they get revised toward the price rather than the other way
round. So this module shows the consensus, and then does something almost no
tool bothers with: it checks the target against how often the stock has
ACTUALLY made that much ground in a year.

"Analysts see +27%" reads very differently next to "this stock has gained 27%
or more in 34% of its past one-year stretches".
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.research.leaps import historical_base_rate

# The five buckets Yahoo reports, weighted 1 (strong buy) to 5 (strong sell) -
# the same scale the street quotes as a "mean recommendation".
_BUCKETS = [
    ("strong_buy", "Strong buy", 1.0),
    ("buy", "Buy", 2.0),
    ("hold", "Hold", 3.0),
    ("sell", "Sell", 4.0),
    ("strong_sell", "Strong sell", 5.0),
]


class RatingBucket(BaseModel):
    key: str
    label: str
    count: int = 0
    pct: float = 0.0


class AnalystView(BaseModel):
    symbol: str
    price: Optional[float] = None

    buckets: list[RatingBucket] = Field(default_factory=list)
    total_analysts: int = 0
    mean_score: Optional[float] = None      # 1 = strong buy, 5 = strong sell
    consensus: str = "No coverage"
    bullish_pct: Optional[float] = None     # buy + strong buy, as % of all

    target_mean: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    target_median: Optional[float] = None
    upside_pct: Optional[float] = None
    high_upside_pct: Optional[float] = None
    low_upside_pct: Optional[float] = None
    dispersion_pct: Optional[float] = None  # high-to-low spread vs price

    # the reality check
    base_rate_pct: Optional[float] = None   # how often it made that move in a year
    base_rate_years: Optional[float] = None
    median_year_pct: Optional[float] = None

    agreement: str = ""                     # how much analysts agree with each other
    reality_check: str = ""
    summary: str = ""
    status: str = "ok"


def _consensus_label(score: Optional[float]) -> str:
    if score is None:
        return "No coverage"
    if score <= 1.5:
        return "Strong buy"
    if score <= 2.4:
        return "Buy"
    if score <= 3.4:
        return "Hold"
    if score <= 4.4:
        return "Sell"
    return "Strong sell"


def build(symbol: str, price: Optional[float], ratings: Optional[dict] = None,
          info: Optional[dict] = None, closes: Optional[list[float]] = None) -> AnalystView:
    """`ratings` is the buy/hold/sell counts, `info` the raw Yahoo fields
    (targetMeanPrice and friends), `closes` daily history for the base rate."""
    ratings = ratings or {}
    info = info or {}
    view = AnalystView(symbol=symbol.upper(), price=price)

    total = 0
    for key, label, _weight in _BUCKETS:
        count = int(ratings.get(key) or 0)
        total += count
        view.buckets.append(RatingBucket(key=key, label=label, count=count))
    view.total_analysts = total

    if total:
        for bucket in view.buckets:
            bucket.pct = round(100.0 * bucket.count / total, 1)
        weighted = sum(int(ratings.get(k) or 0) * w for k, _l, w in _BUCKETS)
        view.mean_score = round(weighted / total, 2)
        bullish = sum(int(ratings.get(k) or 0) for k in ("strong_buy", "buy"))
        view.bullish_pct = round(100.0 * bullish / total, 1)
    else:
        view.mean_score = info.get("recommendationMean")
    view.consensus = _consensus_label(view.mean_score)

    view.target_mean = info.get("targetMeanPrice")
    view.target_high = info.get("targetHighPrice")
    view.target_low = info.get("targetLowPrice")
    view.target_median = info.get("targetMedianPrice")

    if price and price > 0:
        if view.target_mean:
            view.upside_pct = (view.target_mean / price - 1) * 100
        if view.target_high:
            view.high_upside_pct = (view.target_high / price - 1) * 100
        if view.target_low:
            view.low_upside_pct = (view.target_low / price - 1) * 100
        if view.target_high and view.target_low:
            view.dispersion_pct = (view.target_high - view.target_low) / price * 100

    view.agreement = _agreement(view)
    _reality_check(view, closes or [])
    view.summary = _summary(view)
    view.status = ("good" if view.consensus in ("Strong buy", "Buy") else
                   "watch" if view.consensus in ("Sell", "Strong sell") else "ok")
    return view


def _agreement(v: AnalystView) -> str:
    if v.dispersion_pct is None:
        return ""
    if v.dispersion_pct <= 25:
        return (f"Analysts broadly agree - their high and low targets are only "
                f"{v.dispersion_pct:.0f}% of the share price apart.")
    if v.dispersion_pct <= 60:
        return (f"Normal disagreement - {v.dispersion_pct:.0f}% between the most and "
                "least optimistic.")
    return (f"Analysts disagree sharply - {v.dispersion_pct:.0f}% between the highest and "
            "lowest target. Nobody really knows what this is worth.")


def _reality_check(v: AnalystView, closes: list[float]) -> None:
    """Compare the consensus target to what the stock has historically done."""
    if v.upside_pct is None or not closes:
        return
    base = historical_base_rate(closes, 365, v.upside_pct)
    if base.hit_rate is None:
        return
    v.base_rate_pct = base.hit_rate
    v.base_rate_years = base.years_used
    v.median_year_pct = base.median_pct

    hit, years = base.hit_rate, base.years_used
    if hit >= 55:
        tone = ("a move it has made more often than not, so the target is not a stretch")
    elif hit >= 35:
        tone = "a move it manages in a minority of years"
    elif hit >= 20:
        tone = "a move it has rarely managed"
    else:
        tone = "a move it has almost never managed in a single year"
    v.reality_check = (
        f"The consensus target implies {v.upside_pct:+.0f}% in twelve months. Over the past "
        f"{years:.0f} years this stock cleared that in {hit:.0f}% of one-year stretches - "
        f"{tone}. A typical year returned {base.median_pct:+.0f}%.")


def _summary(v: AnalystView) -> str:
    if not v.total_analysts and v.target_mean is None:
        return f"No analyst coverage found for {v.symbol}."

    parts = []
    if v.total_analysts:
        parts.append(f"{v.total_analysts} analysts cover {v.symbol} and the consensus is "
                     f"{v.consensus.lower()}"
                     + (f" ({v.bullish_pct:.0f}% rate it buy or better)."
                        if v.bullish_pct is not None else "."))
    if v.target_mean and v.upside_pct is not None:
        parts.append(f"Average target ${v.target_mean:,.2f}, {v.upside_pct:+.1f}% from here"
                     + (f" (range ${v.target_low:,.0f} to ${v.target_high:,.0f})."
                        if v.target_low and v.target_high else "."))
    if v.reality_check:
        parts.append(v.reality_check)
    parts.append("Targets are opinions on a twelve-month view, they cluster optimistic, "
                 "and they get revised toward the price as often as the price moves "
                 "toward them. Treat them as sentiment, not a forecast.")
    return " ".join(parts)
