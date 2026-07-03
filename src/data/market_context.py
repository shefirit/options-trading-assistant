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


class MarketContext(BaseModel):
    underlying: str
    price: float
    atm_iv: Optional[float] = None      # implied volatility of the near-the-money option
    vix: Optional[float] = None
    trend: str = "unknown"              # "up", "down", "sideways", "unknown"
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


def _rank_strategies(trend: str, vol_bucket: str) -> list[StrategySuggestion]:
    """Order strategies best-first for the current conditions.

    The idea, in plain English:
      - Sideways / calm market  -> Iron Condor (get paid on both sides).
      - Leaning up (bullish)     -> Put Credit Spread (win while price stays up).
      - Leaning down (bearish)   -> Call Credit Spread (win while price stays down).
      - When direction is unclear, the neutral Iron Condor leads.
    """
    condor = StrategySuggestion(
        strategy_key="iron_condor", name=_NAMES["iron_condor"],
        reason="Calm, range-bound market - you collect premium from both sides at once.",
    )
    put_cs = StrategySuggestion(
        strategy_key="put_credit_spread", name=_NAMES["put_credit_spread"],
        reason="Neutral-to-bullish lean - you win as long as price does not fall hard.",
    )
    call_cs = StrategySuggestion(
        strategy_key="call_credit_spread", name=_NAMES["call_credit_spread"],
        reason="Neutral-to-bearish lean - you win as long as price does not rise hard.",
    )

    if trend == "up":
        ordered = [put_cs, condor, call_cs]
    elif trend == "down":
        ordered = [call_cs, condor, put_cs]
    else:  # sideways or unknown
        ordered = [condor, put_cs, call_cs]

    # In a high-volatility market, add a size caution to the top pick's reason.
    if vol_bucket == "high":
        ordered[0] = ordered[0].model_copy(update={
            "reason": ordered[0].reason + " Volatility is high, so keep size small and deltas low."
        })
    return ordered


def build_context(
    underlying: str,
    price: float,
    vix: Optional[float] = None,
    trend: str = "unknown",
    atm_iv: Optional[float] = None,
) -> MarketContext:
    """Build the market read from lightweight inputs (no full option chain needed,
    so the snapshot loads fast on real data)."""
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


def trend_from_prices(prices: list[float]) -> str:
    """Simple trend from a list of recent daily closes (oldest first).

    Compares the short (20-day) and long (50-day) averages, the same idea traders
    use: short above long = uptrend.
    """
    if len(prices) < 50:
        return "unknown"
    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    spread = (sma20 - sma50) / sma50
    if spread > 0.01:
        return "up"
    if spread < -0.01:
        return "down"
    return "sideways"
