"""A plain-English read of current conditions, plus which of your strategies fit.

This mirrors the "Quick Market Condition Guide" in your Notion hub:
  calm / low VIX / range-bound  -> Iron Condor
  slightly bearish or flat       -> Call Credit Spread
  slightly bullish or flat       -> Put Credit Spread
  own stock, want income         -> Covered Call
  want cheap stock exposure      -> Poor Man's Covered Call
  want to buy stock at a discount-> Cash Secured Put

Everything degrades gracefully: if live VIX or price history is unavailable,
it still returns a useful read from what it does have.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.data.chain import OptionChain
from src.engine.models import OptionType


class StrategySuggestion(BaseModel):
    strategy_key: str
    name: str
    reason: str
    # What put it in this position. Carried so the UI can show the reasoning
    # rather than an order she has to take on trust.
    score: float = 0.0
    trend_points: float = 0.0
    vol_points: float = 0.0


class MarketContext(BaseModel):
    underlying: str
    price: float
    atm_iv: Optional[float] = None      # implied volatility of the near-the-money option
    vix: Optional[float] = None
    trend: str = "unknown"              # "up", "down", "sideways", "unknown"
    # The gap between the 20- and 50-day averages, as a fraction, and the gap
    # it has to clear. Shown on the page so an unchanged ranking reads as a
    # fact about the market rather than as a stuck screen.
    trend_spread: Optional[float] = None
    trend_band: float = 0.01
    below_200: bool = False
    vol_bucket: str = "unknown"         # "low", "normal", "high"
    volatility_read: str = ""           # plain-English note on IV / VIX
    summary: str = ""
    # The single best-fit strategy for right now, plus a short reason.
    best_strategy_key: Optional[str] = None
    best_strategy_name: Optional[str] = None
    recommendation_reason: str = ""
    # The best pick first, then a couple of alternatives.
    suggestions: list[StrategySuggestion] = Field(default_factory=list)


def _atm_iv(chain: OptionChain) -> Optional[float]:
    dte = chain.nearest_dte(45)
    if dte is None:
        return None
    calls = chain.by(OptionType.CALL, dte)
    if not calls:
        return None
    atm = min(calls, key=lambda c: abs(c.strike - chain.underlying_price))
    return atm.iv or None


def _volatility_read(vix: Optional[float], atm_iv: Optional[float]) -> tuple[str, str]:
    """Return (bucket, plain-English note). bucket is 'low' / 'normal' / 'high'."""
    gauge = vix if vix is not None else (atm_iv * 100 if atm_iv else None)
    label = "VIX" if vix is not None else "implied volatility"
    if gauge is None:
        return "unknown", "Volatility reading unavailable."
    if gauge < 15:
        return "low", (f"{label} is low ({gauge:.1f}). Option premiums are thin, and the "
                       "market expects calm - good for range-bound trades like Iron Condors.")
    if gauge < 25:
        return "normal", (f"{label} is moderate ({gauge:.1f}). Premiums are reasonable - "
                          "your usual credit spreads fit well.")
    return "high", (f"{label} is elevated ({gauge:.1f}). Premiums are fat but moves are bigger - "
                    "credit spreads pay more, but keep size small and deltas low.")


# Plain-English names used in reasons.
_NAMES = {
    "iron_condor": "Iron Condor",
    "put_credit_spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "cash_secured_put": "Cash Secured Put",
}


# How much each condition moves a strategy up or down the list. Kept as data
# rather than nested ifs so the reasoning can be PRINTED - the section used to
# show an order with nothing behind it, which is why a correct answer that had
# not changed for six weeks looked broken.
#
# The volatility half comes straight from her Notion hub's quick guide:
# "calm / low VIX / range-bound -> Iron Condor". The code used to implement
# only the range-bound half, so a VIX of 12 and a VIX of 28 gave the identical
# ranking. An iron condor is the one shape here with BOTH sides exposed, so
# calm should promote it and a swinging market should push it down the list.
_TREND_POINTS = {
    "up":       {"put_credit_spread": 2.0, "iron_condor": 0.0, "call_credit_spread": -2.0},
    "down":     {"put_credit_spread": -2.0, "iron_condor": 0.0, "call_credit_spread": 2.0},
    "sideways": {"put_credit_spread": 0.0, "iron_condor": 2.0, "call_credit_spread": 0.0},
    "unknown":  {"put_credit_spread": 0.0, "iron_condor": 0.0, "call_credit_spread": 0.0},
}
_VOL_POINTS = {
    "low":     {"iron_condor": 1.5, "put_credit_spread": 0.0, "call_credit_spread": 0.0},
    "normal":  {"iron_condor": 0.0, "put_credit_spread": 0.0, "call_credit_spread": 0.0},
    "high":    {"iron_condor": -2.0, "put_credit_spread": 0.5, "call_credit_spread": 0.5},
    "unknown": {"iron_condor": 0.0, "put_credit_spread": 0.0, "call_credit_spread": 0.0},
}
# The tie-break when nothing separates them. Her SOP sells call spreads at a
# stricter 0.10 delta precisely because markets drift up, so between the two
# one-sided spreads the put side is the calmer default.
_TIE_ORDER = ["put_credit_spread", "iron_condor", "call_credit_spread"]

_BASE_REASON = {
    "iron_condor":
        "Range-bound market - you collect premium from both sides at once.",
    "put_credit_spread":
        "Neutral-to-bullish lean - you win as long as price does not fall hard.",
    "call_credit_spread":
        "Neutral-to-bearish lean - you win as long as price does not rise hard.",
}
# What the volatility read ADDS to a strategy's reason, so the ranking explains
# the half of itself that used to be invisible.
_VOL_REASON = {
    ("iron_condor", "low"):
        "The market is calm, which is exactly when a condor's two sides are "
        "least likely to be breached.",
    ("iron_condor", "high"):
        "Fear is elevated, and a condor is the one shape with BOTH sides "
        "exposed - big swings can breach either wing, so it drops down the list.",
    ("put_credit_spread", "high"):
        "Premiums are fat right now, and only one side of this is exposed.",
    ("call_credit_spread", "high"):
        "Premiums are fat right now, and only one side of this is exposed.",
}


def _rank_strategies(trend: str, vol_bucket: str) -> list[StrategySuggestion]:
    """Order the three index strategies best-first for current conditions.

    Two inputs, not one:
      - TREND decides which direction you can lean. Up favours the put spread,
        down the call spread, sideways the condor.
      - VOLATILITY decides how safe it is to have two sides exposed. Calm
        promotes the condor; a nervous market demotes it below the one-sided
        spreads, whatever the trend is doing.

    Every suggestion carries the points that put it where it is, so the UI can
    show the arithmetic instead of asking her to trust an unexplained order.
    """
    trend_pts = _TREND_POINTS.get(trend, _TREND_POINTS["unknown"])
    vol_pts = _VOL_POINTS.get(vol_bucket, _VOL_POINTS["unknown"])

    scored = []
    for key in _TIE_ORDER:
        score = trend_pts[key] + vol_pts[key]
        reason = _BASE_REASON[key]
        extra = _VOL_REASON.get((key, vol_bucket))
        if extra:
            reason = f"{reason} {extra}"
        scored.append(StrategySuggestion(
            strategy_key=key, name=_NAMES[key], reason=reason,
            score=round(score, 2), trend_points=trend_pts[key],
            vol_points=vol_pts[key]))

    # Highest score first; ties keep _TIE_ORDER, which sort() preserves because
    # it is stable and `scored` was built in that order.
    scored.sort(key=lambda s: -s.score)

    # High volatility now REORDERS the list, but the size caution has to
    # survive that - it is SOP guidance about how to trade whatever comes out
    # on top, not a comment on the condor.
    if vol_bucket == "high":
        scored[0] = scored[0].model_copy(update={
            "reason": scored[0].reason + " Volatility is high, so keep size "
                                         "small and deltas low."})
    return scored


def build_context(
    underlying: str,
    price: float,
    vix: Optional[float] = None,
    trend: str = "unknown",
    atm_iv: Optional[float] = None,
    trend_spread: Optional[float] = None,
    below_200: bool = False,
) -> MarketContext:
    """Build the market read from lightweight inputs (no full option chain needed,
    so the snapshot loads fast on real data).

    trend_spread is optional evidence: the gap between the 20- and 50-day
    averages that produced `trend`. Callers that have it (the live path, via
    trend_detail) pass it so the page can show its working.
    """
    vol_bucket, vol_note = _volatility_read(vix, atm_iv)
    suggestions = _rank_strategies(trend, vol_bucket)
    best = suggestions[0]

    trend_word = {
        "up": "leaning up", "down": "leaning down",
        "sideways": "moving sideways", "unknown": "direction unclear",
    }[trend]

    summary = f"{underlying} is at {price:,.2f} and {trend_word}. {vol_note}"

    return MarketContext(
        underlying=underlying,
        price=price,
        atm_iv=atm_iv,
        vix=vix,
        trend=trend,
        trend_spread=trend_spread,
        trend_band=TREND_BAND,
        below_200=below_200,
        vol_bucket=vol_bucket,
        volatility_read=vol_note,
        summary=summary,
        best_strategy_key=best.strategy_key,
        best_strategy_name=best.name,
        recommendation_reason=best.reason,
        suggestions=suggestions,
    )


def context_from_chain(
    chain: OptionChain, vix: Optional[float] = None, trend: str = "unknown",
) -> MarketContext:
    """Convenience wrapper when you already have a chain (demo mode / offline)."""
    return build_context(chain.underlying, chain.underlying_price, vix, trend, _atm_iv(chain))


def daily_sentiment(index_changes: list[Optional[float]], vix: Optional[float]) -> tuple[str, str]:
    """One-line read of how the market feels TODAY, from the big indexes + VIX.

    Returns (label, note) - e.g. ("🙂 Mildly positive and calm", "...").
    """
    changes = [c for c in index_changes if c is not None]
    if not changes:
        return "😐 No read yet", "Live daily changes are unavailable right now."
    avg = sum(changes) / len(changes)

    if avg >= 0.6:
        mood, icon = "Strongly positive", "😄"
    elif avg >= 0.15:
        mood, icon = "Mildly positive", "🙂"
    elif avg > -0.15:
        mood, icon = "Flat / mixed", "😐"
    elif avg > -0.6:
        mood, icon = "Mildly negative", "🙁"
    else:
        mood, icon = "Strongly negative", "😨"

    if vix is None:
        calm = ""
        calm_note = ""
    elif vix < 15:
        calm, calm_note = " and calm", "Fear is low - option premiums are on the thin side."
    elif vix < 25:
        calm, calm_note = "", "Volatility is moderate - normal conditions for selling premium."
    else:
        calm, calm_note = " and nervous", "Fear is elevated - premiums are rich but moves are bigger."

    note = (f"The big indexes are averaging {avg:+.2f}% today. {calm_note}").strip()
    return f"{icon} {mood}{calm}", note


# How far under the 200-day average counts as a real decline rather than noise
# sitting on the line.
LONG_TREND_BAND = 0.03


def trend_from_prices(prices: list[float]) -> str:
    """Trend from a list of recent daily closes (oldest first).

    Compares the short (20-day) and long (50-day) averages, the same idea traders
    use: short above long = uptrend. Then the 200-day average gets a veto.

    The 20/50 crossover only sees about six weeks, and a stock in a sustained
    slide can drift sideways for six weeks. SOFI read "sideways" on the
    crossover while sitting 23% below its 200-day average and down 23% on the
    year, which put it in the "puts you'd sell for income" list - exactly the
    falling-knife the SOP says to avoid, since a put can leave you owning it.

    So a name well below its 200-day is capped: a "sideways" or "down" short
    read becomes "down", and even a rising short read is only allowed up to
    "sideways". That last part matters - a stock that crashed and is genuinely
    recovering is still below its 200-day for months, and calling that a
    downtrend would throw away good candidates.
    """
    return trend_detail(prices)["trend"]


# How far apart the 20- and 50-day averages must be before it counts as a
# direction rather than noise. On a broad index the two averages sit close
# together for weeks at a time, which is why this section can honestly show the
# same answer for over a month - the replay of the last year of SPX had runs of
# 1 to 4 months. That is a trend signal behaving normally, not a stuck screen,
# and the fix is to SHOW the number rather than to narrow the band until the
# recommendation flip-flops.
TREND_BAND = 0.01


def trend_detail(prices: list[float]) -> dict:
    """The trend, plus the numbers that produced it.

    Split out from trend_from_prices so the Market tab can print its evidence:
    "the 20-day average is 0.2% above the 50-day, and it takes 1% to call a
    direction". Without that, an unchanged-but-correct answer is
    indistinguishable from a broken one.

    Returns trend plus sma20/sma50/spread/sma200/below_200 (None where there
    is not enough history to compute them).
    """
    out = {"trend": "unknown", "sma20": None, "sma50": None, "spread": None,
           "sma200": None, "below_200": False, "band": TREND_BAND,
           "days": len(prices)}
    if len(prices) < 50:
        return out

    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    spread = (sma20 - sma50) / sma50 if sma50 else 0.0
    short = ("up" if spread > TREND_BAND
             else "down" if spread < -TREND_BAND else "sideways")
    out.update(sma20=sma20, sma50=sma50, spread=spread, trend=short)

    if len(prices) >= 200:
        sma200 = sum(prices[-200:]) / 200
        out["sma200"] = sma200
        if sma200 > 0 and prices[-1] < sma200 * (1 - LONG_TREND_BAND):
            out["below_200"] = True
            out["trend"] = "sideways" if short == "up" else "down"
    return out
