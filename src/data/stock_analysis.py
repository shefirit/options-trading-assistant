"""Turns raw Yahoo data into a beginner-friendly scorecard for a stock:
is it a solid, liquid company (fundamentals) and what is the price doing
(technicals)? Every number gets a plain-English read and a simple traffic light.

This helps answer "is this a good stock to sell options on?" - you generally
want big, profitable, liquid companies in a steady or rising trend.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Metric(BaseModel):
    label: str
    value: str                       # already formatted for display
    read: str                        # plain-English meaning
    status: str = "ok"               # "good" | "ok" | "watch"


class StockAnalysis(BaseModel):
    symbol: str
    name: str = ""
    price: Optional[float] = None
    sector: str = ""
    fundamentals: list[Metric] = Field(default_factory=list)
    technicals: list[Metric] = Field(default_factory=list)
    liquid: bool = True
    suitable: bool = True            # decent candidate for selling options?
    grade: str = "C"                 # A-F report-card grade from the metrics
    summary: str = ""


# ---------- small technical helpers ----------
def sma(closes: list[float], n: int) -> Optional[float]:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index - 0 to 100. Over 70 is 'overbought', under 30 'oversold'."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _fmt_big(n: Optional[float]) -> str:
    if not n:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= size:
            return f"${n / size:.1f}{unit}"
    return f"${n:,.0f}"


# ---------- fundamentals ----------
def _market_cap_metric(cap: Optional[float]) -> Metric:
    if not cap:
        return Metric(label="Company size", value="n/a", read="Size unknown.", status="watch")
    if cap >= 200e9:
        read, status = "Mega-cap - one of the biggest, most stable companies.", "good"
    elif cap >= 10e9:
        read, status = "Large-cap - big, established company. Good for beginners.", "good"
    elif cap >= 2e9:
        read, status = "Mid-cap - decent size but more ups and downs.", "ok"
    else:
        read, status = "Small-cap - riskier and can move sharply. Be careful.", "watch"
    return Metric(label="Company size (market cap)", value=_fmt_big(cap), read=read, status=status)


def _pe_metric(pe: Optional[float]) -> Metric:
    if pe is None:
        return Metric(label="Valuation (P/E)", value="n/a",
                      read="No P/E - the company may not have steady profits.", status="watch")
    if pe < 0:
        return Metric(label="Valuation (P/E)", value=f"{pe:.1f}",
                      read="Negative - the company is not profitable right now. Caution.",
                      status="watch")
    if pe < 20:
        read, status = "Reasonably priced for its earnings.", "good"
    elif pe < 35:
        read, status = "Fairly to fully priced.", "ok"
    else:
        read, status = "Expensive - lots of growth is already priced in.", "watch"
    return Metric(label="Valuation (P/E)", value=f"{pe:.1f}", read=read, status=status)


def _margin_metric(m: Optional[float]) -> Metric:
    if m is None:
        return Metric(label="Profit margin", value="n/a", read="Profitability unknown.", status="watch")
    pct = m * 100
    if pct >= 20:
        read, status = "Very profitable - keeps a big slice of every sale.", "good"
    elif pct >= 8:
        read, status = "Solidly profitable.", "good"
    elif pct >= 0:
        read, status = "Thin profits - watch this.", "ok"
    else:
        read, status = "Losing money right now. Caution.", "watch"
    return Metric(label="Profit margin", value=f"{pct:.0f}%", read=read, status=status)


def _growth_metric(g: Optional[float]) -> Metric:
    if g is None:
        return Metric(label="Revenue growth", value="n/a", read="Growth unknown.", status="ok")
    pct = g * 100
    if pct >= 15:
        read, status = "Growing fast.", "good"
    elif pct >= 3:
        read, status = "Growing steadily.", "good"
    elif pct >= 0:
        read, status = "Roughly flat sales.", "ok"
    else:
        read, status = "Sales shrinking. Caution.", "watch"
    return Metric(label="Revenue growth (yr)", value=f"{pct:+.0f}%", read=read, status=status)


# ---------- technicals ----------
def _trend_metric(price: float, s50: Optional[float], s200: Optional[float]) -> Metric:
    if not (price and s50 and s200):
        return Metric(label="Trend", value="n/a", read="Not enough history.", status="ok")
    if price > s50 > s200:
        read, status, val = "Uptrend - price is above both moving averages. Healthy.", "good", "Up ▲"
    elif price < s50 < s200:
        read, status, val = "Downtrend - price is below both averages. Be cautious selling puts.", "watch", "Down ▼"
    else:
        read, status, val = "Sideways / choppy - no clear direction.", "ok", "Sideways →"
    return Metric(label="Trend", value=val, read=read, status=status)


def _rsi_metric(value: Optional[float]) -> Metric:
    if value is None:
        return Metric(label="Momentum (RSI)", value="n/a", read="Not enough history.", status="ok")
    if value >= 70:
        read, status = "Overbought - has run up fast and may pull back.", "watch"
    elif value <= 30:
        read, status = "Oversold - has dropped hard and may bounce.", "watch"
    else:
        read, status = "Neutral - not stretched either way.", "good"
    return Metric(label="Momentum (RSI)", value=f"{value:.0f}", read=read, status=status)


def _liquidity_metric(avg_vol: Optional[float]) -> Metric:
    if not avg_vol:
        return Metric(label="Trading volume", value="n/a",
                      read="Volume unknown - may be hard to trade.", status="watch")
    if avg_vol >= 5e6:
        read, status = "Very liquid - easy to get in and out at fair prices.", "good"
    elif avg_vol >= 1e6:
        read, status = "Liquid enough for options.", "good"
    elif avg_vol >= 300e3:
        read, status = "Moderate - spreads may be a bit wide.", "ok"
    else:
        read, status = "Thinly traded - options can be hard to fill. Avoid for now.", "watch"
    return Metric(label="Avg daily volume", value=f"{avg_vol/1e6:.1f}M shares", read=read, status=status)


def analyze(symbol: str, info: dict[str, Any], closes: list[float]) -> StockAnalysis:
    price = (info.get("currentPrice") or info.get("regularMarketPrice")
             or (closes[-1] if closes else None))

    fundamentals = [
        _market_cap_metric(info.get("marketCap")),
        _pe_metric(info.get("trailingPE")),
        _margin_metric(info.get("profitMargins")),
        _growth_metric(info.get("revenueGrowth")),
    ]

    s50, s200 = sma(closes, 50), sma(closes, 200)
    avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day")
    technicals = [
        _trend_metric(price or 0, s50, s200),
        _rsi_metric(rsi(closes)),
        _liquidity_metric(avg_vol),
    ]

    liquid = any(m.label == "Avg daily volume" and m.status in ("good", "ok") for m in technicals)
    watches = sum(1 for m in fundamentals + technicals if m.status == "watch")
    goods = sum(1 for m in fundamentals + technicals if m.status == "good")
    suitable = liquid and watches <= 1 and goods >= 3

    # Report-card grade: 2 points per green, 1 per neutral, 0 per caution.
    all_metrics = fundamentals + technicals
    score = (2 * goods + sum(1 for m in all_metrics if m.status == "ok")) / (2 * len(all_metrics))
    grade = "A" if score >= 0.85 else "B" if score >= 0.70 else \
            "C" if score >= 0.55 else "D" if score >= 0.40 else "F"

    if suitable:
        summary = (f"{symbol} looks like a solid, liquid company - a reasonable candidate for "
                   "selling options like cash secured puts or covered calls.")
    elif not liquid:
        summary = (f"{symbol} does not trade enough volume for comfortable options trading. "
                   "Better to pick a bigger, more liquid name.")
    else:
        summary = (f"{symbol} is a mixed picture ({watches} caution flag(s)). Read the notes "
                   "below and lean toward safer, higher-quality names while you are learning.")

    return StockAnalysis(
        symbol=symbol,
        name=info.get("shortName") or info.get("longName") or symbol,
        price=price,
        sector=info.get("sector") or "",
        fundamentals=fundamentals,
        technicals=technicals,
        liquid=liquid,
        suitable=suitable,
        grade=grade,
        summary=summary,
    )
