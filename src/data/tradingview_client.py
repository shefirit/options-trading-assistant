"""TradingView's technical rating - the same Buy/Sell gauge you see on their
charts, pulled through the well-known `tradingview-ta` library.

It aggregates dozens of indicators (moving averages + oscillators) into one
plain verdict: STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL. We show it as a
second opinion next to the app's own trend read.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

# Map Yahoo's exchange codes to the exchange names TradingView expects.
_EXCHANGE_MAP = {
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
    "NYQ": "NYSE", "NYS": "NYSE",
    "PCX": "AMEX", "ASE": "AMEX", "AMX": "AMEX",
}
# If we cannot tell, try the big ones in turn.
_FALLBACK_EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

# Index names need specific TradingView exchanges.
_INDEX_EXCHANGE = {"SPX": "SP", "NDX": "NASDAQ", "DJX": "DJ", "VIX": "CBOE"}

_PRETTY = {
    "STRONG_BUY": "Strong Buy", "BUY": "Buy", "NEUTRAL": "Neutral",
    "SELL": "Sell", "STRONG_SELL": "Strong Sell",
}


class TVRating(BaseModel):
    symbol: str
    interval: str                    # "daily" | "weekly"
    recommendation: str              # pretty text, e.g. "Strong Buy"
    buy: int = 0
    neutral: int = 0
    sell: int = 0
    moving_avg: Optional[str] = None      # rating from moving averages
    oscillators: Optional[str] = None     # rating from oscillators

    @property
    def color(self) -> str:
        r = self.recommendation.lower()
        if "strong buy" in r or r == "buy":
            return "green"
        if "sell" in r:
            return "red"
        return "orange"


def exchange_for(yf_info: dict[str, Any]) -> list[str]:
    """Candidate exchanges to try for a stock, best guess first."""
    code = str(yf_info.get("exchange", "")).upper()
    mapped = _EXCHANGE_MAP.get(code)
    order = ([mapped] if mapped else []) + [e for e in _FALLBACK_EXCHANGES if e != mapped]
    return order


def _one(symbol: str, exchange: str, screener: str, tv_interval, label: str) -> Optional[TVRating]:
    from tradingview_ta import TA_Handler
    handler = TA_Handler(symbol=symbol, screener=screener, exchange=exchange, interval=tv_interval)
    a = handler.get_analysis()
    s = a.summary
    return TVRating(
        symbol=symbol, interval=label,
        recommendation=_PRETTY.get(s.get("RECOMMENDATION", ""), s.get("RECOMMENDATION", "n/a")),
        buy=int(s.get("BUY", 0)), neutral=int(s.get("NEUTRAL", 0)), sell=int(s.get("SELL", 0)),
        moving_avg=_PRETTY.get(a.moving_averages.get("RECOMMENDATION", ""),
                               a.moving_averages.get("RECOMMENDATION")),
        oscillators=_PRETTY.get(a.oscillators.get("RECOMMENDATION", ""),
                                a.oscillators.get("RECOMMENDATION")),
    )


def get_ratings(
    symbol: str,
    exchanges: list[str],
    screener: str = "america",
) -> dict[str, TVRating]:
    """Daily + weekly ratings. Tries each candidate exchange until one works.

    Returns {} if TradingView cannot be reached or the symbol is not found.
    """
    try:
        from tradingview_ta import Interval
    except Exception:
        return {}

    intervals = [("daily", Interval.INTERVAL_1_DAY), ("weekly", Interval.INTERVAL_1_WEEK)]
    for exch in exchanges:
        try:
            out: dict[str, TVRating] = {}
            for label, iv in intervals:
                r = _one(symbol, exch, screener, iv, label)
                if r:
                    out[label] = r
            if out:
                return out
        except Exception:
            continue   # wrong exchange or transient error - try the next
    return {}


def get_index_ratings(underlying: str) -> dict[str, TVRating]:
    exch = _INDEX_EXCHANGE.get(underlying.upper())
    if not exch:
        return {}
    return get_ratings(underlying.upper(), [exch])
