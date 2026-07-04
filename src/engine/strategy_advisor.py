"""Turns a symbol's full picture into a plain-English options plan.

Given everything the app knows about a symbol - price trend, TradingView's
indicator vote, the quality grade, liquidity, RSI, the fear gauge, upcoming
earnings, and what 100 shares would cost against your buying-power limit -
this recommends which of YOUR eight strategies fits, and why.

It always thinks in your SOP's monthly rhythm (21-35 days to expiration),
never day trades, and follows your rules:
  - Credit spreads / iron condors only on European-style indexes (SPX, NDX...).
  - Cash secured puts, covered calls, and PMCC on US-style ETFs and stocks.
  - No bearish stock play exists in your book - so a weak stock means
    "wait or use the index", never "force a trade".
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field

from src.data.stock_analysis import StockAnalysis

DTE_NOTE = ("Timing: aim for roughly a month to expiration (21-35 days) - "
            "your SOP's monthly rhythm, not day trading.")

# How each TradingView verdict counts toward the outlook score.
_TV_SCORE = {"Strong Buy": 2.0, "Buy": 1.0, "Neutral": 0.0,
             "Sell": -1.0, "Strong Sell": -2.0}

_NAMES = {
    "put_credit_spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "iron_condor": "Iron Condor",
    "cash_secured_put": "Cash Secured Put",
    "poor_mans_covered_call": "Poor Man's Covered Call",
    "covered_call_model_1": "Covered Call - Model 1 (Collar / Full Protection)",
    "covered_call_model_2": "Covered Call - Model 2 (Classic Spread)",
    "covered_call_model_3": "Covered Call - Model 3 (Zero-Cost Ratio)",
}


def _covered_call_play(outlook: str, vix) -> "Play":
    """Pick the covered-call MODEL that fits current conditions (all need 100
    shares). High market fear -> Model 1 (collar, full downside protection).
    Bullish and calm -> Model 3 (zero-cost ratio, cheap hedge, but advanced and
    needs daily management). Otherwise -> Model 2 (classic, balanced).

    Uses market fear (VIX), not the stock's RSI - a strong stock is usually
    "overbought," and that shouldn't force it into the defensive collar.
    """
    nervous = outlook == "bearish" or (vix is not None and vix >= 22)
    if nervous:
        return _play("covered_call_model_1",
                     "If you own 100 shares: the picture looks shaky (weak trend, high fear, or "
                     "overbought), so Model 1's collar buys a long put for FULL downside "
                     "protection while you still collect the call premium - the safe way to hold "
                     "through the bumps.")
    if outlook == "bullish" and (vix is None or vix < 18):
        return _play("covered_call_model_3",
                     "If you own 100 shares and can watch it daily: you're bullish and the market "
                     "is calm, so Model 3's zero-cost ratio makes the downside hedge nearly free "
                     "and keeps more of the call premium as income. Advanced - a hard drop below "
                     "the two short puts accelerates losses, so only take it if you'll manage it "
                     "actively.")
    return _play("covered_call_model_2",
                 "If you own 100 shares: a steady, neutral read favors Model 2 - the classic "
                 "covered call with a cheaper put-spread hedge, balancing monthly income and "
                 "protection.")


class Play(BaseModel):
    key: str
    name: str
    why: str


class StrategyAdvice(BaseModel):
    symbol: str
    kind: str                                   # "index" | "etf" | "stock"
    outlook: str                                # "bullish" | "neutral" | "bearish" | "avoid"
    outlook_reasons: list[str] = Field(default_factory=list)
    primary: Optional[Play] = None              # None = "no safe play right now"
    alternatives: list[Play] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    dte_note: str = DTE_NOTE


def _play(key: str, why: str) -> Play:
    return Play(key=key, name=_NAMES[key], why=why)


def _tv_signal(tv: dict) -> tuple[Optional[float], list[str]]:
    """Average TradingView score across daily/weekly, plus readable reasons."""
    if not tv:
        return None, []
    vals, reasons = [], []
    for label, rating in tv.items():
        score = _TV_SCORE.get(getattr(rating, "recommendation", ""), None)
        if score is not None:
            vals.append(score)
            reasons.append(f"TradingView {label}: {rating.recommendation}")
    return (sum(vals) / len(vals) if vals else None), reasons


def _rsi_value(analysis: Optional[StockAnalysis]) -> Optional[float]:
    if not analysis:
        return None
    for m in analysis.technicals:
        if "RSI" in m.label:
            try:
                return float(m.value)
            except ValueError:
                return None
    return None


def advise(
    symbol: str,
    kind: str,
    price: Optional[float],
    trend: str,
    vix: Optional[float] = None,
    tv: Optional[dict] = None,
    analysis: Optional[StockAnalysis] = None,
    earnings_date: Optional[dt.date] = None,
    today: Optional[dt.date] = None,
    capital: float = 100_000,
    monthly_bp: float = 50_000,
) -> StrategyAdvice:
    """The main entry: all signals in, one plan out."""
    today = today or dt.date.today()

    # ---------- outlook: trend + TradingView vote ----------
    trend_score = {"up": 1.5, "down": -1.5}.get(trend, 0.0)
    tv_avg, tv_reasons = _tv_signal(tv or {})
    score = trend_score + (tv_avg or 0.0)
    outlook = "bullish" if score >= 1.5 else "bearish" if score <= -1.5 else "neutral"

    reasons = [f"Price trend: {trend}"] + tv_reasons
    if analysis:
        reasons.append(f"Quality grade: {analysis.grade}")

    # ---------- cautions that apply regardless of the play ----------
    cautions: list[str] = []
    rsi = _rsi_value(analysis)
    if rsi is not None and rsi >= 70:
        cautions.append(f"RSI is {rsi:.0f} (overbought) - {symbol} has run up fast and "
                        "often cools off. Entering after a small pullback is safer.")
    if rsi is not None and rsi <= 30:
        cautions.append(f"RSI is {rsi:.0f} (oversold) - it is falling hard. Do not try to "
                        "catch a falling knife; wait for it to stabilize.")
    if vix is not None and vix >= 25:
        cautions.append(f"VIX is {vix:.0f} - fear is elevated. Premiums are rich, but keep "
                        "size small and deltas low.")
    if kind == "stock" and earnings_date and 0 <= (earnings_date - today).days <= 35:
        cautions.append(f"Earnings on {earnings_date:%b %d} lands inside a monthly trade "
                        "window. Pick an expiration BEFORE it, or wait until after - "
                        "options prices get crushed right after earnings.")

    # ---------- indexes: your credit-spread playbook ----------
    if kind == "index":
        if trend == "up":
            primary = _play("put_credit_spread",
                            "The index is trending up - you win as long as it does not "
                            "fall hard through your short strike.")
            alts = [_play("iron_condor", "If the climb stalls into a range, collect from "
                                         "both sides instead."),
                    _play("call_credit_spread", "Only if the trend flips down.")]
        elif trend == "down":
            primary = _play("call_credit_spread",
                            "The index is trending down - you win as long as it does not "
                            "rise hard through your short strike.")
            alts = [_play("iron_condor", "If the slide settles into a range."),
                    _play("put_credit_spread", "Only if the trend turns back up.")]
        else:
            primary = _play("iron_condor",
                            "No clear direction - a range-bound market pays you on both "
                            "sides at once.")
            alts = [_play("put_credit_spread", "If you lean slightly bullish."),
                    _play("call_credit_spread", "If you lean slightly bearish.")]
        return StrategyAdvice(symbol=symbol, kind=kind, outlook=outlook,
                              outlook_reasons=reasons, primary=primary,
                              alternatives=alts, cautions=cautions)

    # ---------- stocks: quality gate first ----------
    if kind == "stock" and analysis is not None and (
            not analysis.liquid or analysis.grade in ("D", "F")):
        cautions.insert(0, analysis.summary)
        return StrategyAdvice(
            symbol=symbol, kind=kind, outlook="avoid", outlook_reasons=reasons,
            primary=None,
            alternatives=[_play("put_credit_spread",
                                "Rather than force this name, trade the index (SPX) - "
                                "same premium-selling idea without single-company risk.")],
            cautions=cautions,
        )

    # ---------- ETFs and quality stocks: the US-style playbook ----------
    shares_cost = (price or 0) * 100
    affordable = price is not None and shares_cost <= monthly_bp
    if price is not None and not affordable:
        cautions.append(f"100 shares of {symbol} cost about ${shares_cost:,.0f} - over your "
                        f"${monthly_bp:,.0f} monthly buying-power limit. The Poor Man's "
                        "Covered Call gets similar exposure for a fraction of that.")

    if outlook == "bullish":
        if affordable:
            primary = _play("cash_secured_put",
                            f"{symbol} looks strong. Get paid up front to agree to buy it "
                            "a few percent cheaper - if it stays up you keep the cash, and "
                            "if you are assigned, you own a name you wanted anyway.")
            alts = [_play("poor_mans_covered_call",
                          "The same income idea with much less capital tied up."),
                    _covered_call_play(outlook, vix)]
        else:
            primary = _play("poor_mans_covered_call",
                            f"{symbol} looks strong but 100 shares are too expensive for "
                            "your budget. A deep, long-dated call stands in for the shares "
                            "and you sell monthly calls against it for income.")
            alts = [_covered_call_play(outlook, vix)]
    elif outlook == "neutral":
        if affordable:
            primary = _play("cash_secured_put",
                            "The read is neutral - sell a put further below the price "
                            "(lower delta), so you also win if it drifts sideways or "
                            "dips mildly.")
            alts = [_covered_call_play(outlook, vix),
                    _play("poor_mans_covered_call",
                          "Income with less capital, if you are still mildly positive "
                          "long-term.")]
        else:
            primary = _play("poor_mans_covered_call",
                            "Neutral read on an expensive name - the PMCC earns monthly "
                            "income without buying 100 shares.")
            alts = [_covered_call_play(outlook, vix)]
    else:  # bearish
        cautions.insert(0, f"{symbol} looks weak right now. Selling puts against a "
                           "downtrend is how beginners get hurt - your playbook has no "
                           "bearish stock strategy on purpose. Wait for the trend to turn.")
        return StrategyAdvice(
            symbol=symbol, kind=kind, outlook=outlook, outlook_reasons=reasons,
            primary=None,
            alternatives=[_play("call_credit_spread",
                                "If you expect the WHOLE market to slip, express that on "
                                "the index (SPX) with defined risk instead.")],
            cautions=cautions,
        )

    return StrategyAdvice(symbol=symbol, kind=kind, outlook=outlook,
                          outlook_reasons=reasons, primary=primary,
                          alternatives=alts, cautions=cautions)
