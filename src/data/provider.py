"""One place the UI asks for market data. It hides WHERE the data comes from:

  - LIVE (Schwab):  true real-time from your account (once Schwab is connected).
  - REAL (Yahoo):   real prices/chains, about 15 min delayed - free, works today.
  - DEMO:           bundled sample chains, if you are offline.

The UI code stays identical; only the source changes. It also provides the
market read (trend + VIX) and the per-stock fundamental/technical analysis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.data import (
    cache,
    market_events,
    premium_finder,
    stock_analysis,
    tradingview_client,
    yfinance_client,
)
from src.data.chain import OptionChain
from src.data.market_context import (
    MarketContext,
    build_context,
    context_from_chain,
    trend_from_prices,
)
from src.data.schwab_client import SchwabClient
from src.data.stock_analysis import StockAnalysis

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = PROJECT_ROOT / "sample_data"

_DEMO_FILES = {
    "SPX": "spx_chain.json", "NDX": "spx_chain.json",
    "RUT": "spx_chain.json", "XSP": "spx_chain.json",
    "SPY": "spy_chain.json", "QQQ": "spy_chain.json",
    "IWM": "spy_chain.json", "DIA": "spy_chain.json",
    # Added liquid ETFs share the ETF sample chain when offline (real data in live mode).
    "GLD": "spy_chain.json", "SLV": "spy_chain.json", "TLT": "spy_chain.json",
    "EEM": "spy_chain.json", "EFA": "spy_chain.json", "XLF": "spy_chain.json",
    "XLE": "spy_chain.json", "XLK": "spy_chain.json", "XLV": "spy_chain.json",
    "SMH": "spy_chain.json",
}


class DataProvider:
    def __init__(self, mode: str, client: Optional[SchwabClient] = None):
        self.mode = mode                 # "schwab" | "yahoo" | "demo"
        self._client = client

    @property
    def is_live(self) -> bool:
        """True when using true real-time Schwab data."""
        return self.mode == "schwab"

    @property
    def is_real(self) -> bool:
        """True when using real market data (Schwab live OR Yahoo delayed)."""
        return self.mode in ("schwab", "yahoo")

    @property
    def mode_label(self) -> str:
        return {
            "schwab": "LIVE (Schwab, real-time)",
            "yahoo": "REAL (Yahoo, ~15 min delayed)",
            "demo": "DEMO (sample data)",
        }[self.mode]

    @classmethod
    def create(cls) -> "DataProvider":
        """Prefer Schwab live, then real Yahoo data, then offline demo."""
        if SchwabClient.is_configured():
            try:
                return cls("schwab", SchwabClient.from_env())
            except Exception:
                pass
        if yfinance_client.is_available():
            return cls("yahoo")
        return cls("demo")

    # ---------- option chains ----------
    def get_chain(self, underlying: str, dte_min: Optional[int] = None,
                 dte_max: Optional[int] = None) -> OptionChain:
        """dte_min/dte_max narrow which expirations are pulled from Yahoo - pass
        the strategy's real window (scanner.strategy_dte_window) instead of the
        wide default so a scan makes far fewer requests and is less likely to
        trip Yahoo's rate limit."""
        underlying = underlying.upper()
        if self.mode == "schwab":
            return cache.get_or_fetch(f"chain:{underlying}",
                                      lambda: self._client.get_option_chain(underlying), 60)
        if self.mode == "yahoo":
            lo = 15 if dte_min is None else dte_min
            hi = 70 if dte_max is None else dte_max
            return cache.get_or_fetch(
                f"ychain:{underlying}:{lo}:{hi}",
                lambda: yfinance_client.get_option_chain(underlying, from_dte=lo, to_dte=hi), 120)
        return self._demo_chain(underlying)

    # ---------- market tiles (the head-of-app overview) ----------
    TILE_SYMBOLS = ["SPX", "NDX", "RUT", "VIX"]

    def get_market_tiles(self) -> list[dict]:
        """Price + today's % change for the big indexes and VIX."""
        tiles = []
        for s in self.TILE_SYMBOLS:
            if self.mode == "yahoo":
                price, pct = cache.get_or_fetch(
                    f"tile:{s}", lambda s=s: yfinance_client.get_price_change(s), 120)
            elif self.mode == "schwab":
                price, pct = self._client.get_price(s), None
            else:  # demo
                price = {"SPX": 5100.0, "NDX": 18400.0, "RUT": 2050.0, "VIX": 13.5}[s]
                pct = None
            tiles.append({"symbol": s, "price": price, "change_pct": pct})
        return tiles

    def get_leaps_chain(self, underlying: str, target_dte: int = 210) -> Optional[OptionChain]:
        """A far-dated chain (~7 months out) for a PMCC's long LEAPS. Real data only."""
        underlying = underlying.upper()
        if self.mode == "yahoo":
            return cache.get_or_fetch(
                f"leaps:{underlying}",
                lambda: yfinance_client.get_expiration_chain(underlying, target_dte), 300)
        return None

    # ---------- market read (lightweight - no full option chain fetch) ----------
    def get_market_context(self, underlying: str) -> MarketContext:
        if self.mode == "yahoo":
            price = cache.get_or_fetch(f"px:{underlying}",
                                       lambda: yfinance_client.get_price(underlying), 120) or 0.0
            vix = cache.get_or_fetch("vix", yfinance_client.get_vix, 120)
            closes = cache.get_or_fetch(f"hist:{underlying}",
                                        lambda: yfinance_client.get_history_closes(underlying), 300)
            return build_context(underlying, price, vix=vix, trend=trend_from_prices(closes))
        if self.mode == "schwab":
            price = self._client.get_price(underlying) or 0.0
            return build_context(underlying, price, vix=self._client.get_price("VIX"))
        # demo: the sample chain is local and cheap, so use it for a richer read.
        return context_from_chain(self.get_chain(underlying), vix=13.5, trend="sideways")

    # ---------- per-stock fundamental + technical analysis ----------
    def get_stock_analysis(self, symbol: str) -> Optional[StockAnalysis]:
        """Only meaningful with real data (Yahoo). Returns None in demo mode."""
        symbol = symbol.upper()
        if self.mode != "yahoo":
            return None

        def _fetch() -> StockAnalysis:
            info = yfinance_client.get_fundamentals(symbol)
            closes = yfinance_client.get_history_closes(symbol, period="1y")
            # On the hosted app Yahoo often drops the info volume fields; recover
            # them from price history so liquid names aren't wrongly flagged.
            avg_vol = None
            if not (info.get("averageVolume") or info.get("averageDailyVolume10Day")):
                avg_vol = yfinance_client.get_avg_volume(symbol)
            return stock_analysis.analyze(symbol, info, closes, avg_volume=avg_vol)

        return cache.get_or_fetch(f"analysis:{symbol}", _fetch, 300)

    # ---------- upcoming events ----------
    def get_macro_events(self, trade_dte: Optional[int] = None):
        """Market-wide events (options expiration, jobs report, Fed decisions)."""
        return market_events.upcoming_events(trade_dte=trade_dte)

    def get_stock_calendar(self, symbol: str):
        """(next_earnings_date, ex_dividend_date) for a stock - real data only."""
        if self.mode == "yahoo":
            return cache.get_or_fetch(f"cal:{symbol}",
                                      lambda: yfinance_client.get_calendar_dates(symbol), 3600)
        return (None, None)

    def get_earnings_info(self, symbol: str) -> dict:
        """Earnings/ex-div dates + analyst EPS/revenue estimates - real data only."""
        if self.mode == "yahoo":
            return cache.get_or_fetch(f"earn:{symbol}",
                                      lambda: yfinance_client.get_earnings_info(symbol), 3600)
        return {}

    def get_raw_info(self, symbol: str) -> dict:
        """Raw Yahoo fundamentals dict (for the key-stats strip)."""
        if self.mode != "yahoo":
            return {}
        return cache.get_or_fetch(f"info:{symbol}",
                                  lambda: yfinance_client.get_fundamentals(symbol), 3600)

    def get_price_change(self, symbol: str):
        """(price, today's % change) for one symbol."""
        if self.mode == "yahoo":
            return cache.get_or_fetch(f"tile:{symbol}",
                                      lambda: yfinance_client.get_price_change(symbol), 120)
        return (None, None)

    def get_analyst_ratings(self, symbol: str) -> dict:
        """Wall Street buy/hold/sell counts - real data only."""
        if self.mode != "yahoo":
            return {}
        return cache.get_or_fetch(f"analysts:{symbol}",
                                  lambda: yfinance_client.get_analyst_ratings(symbol), 3600)

    def get_eps_history(self, symbol: str) -> list:
        """Past quarters of expected-vs-delivered earnings.

        Alpha Vantage first (years of history, works on the hosted app where Yahoo's
        earnings endpoint is IP-blocked); Yahoo as the fallback. Cached 24h - earnings
        only change quarterly, and it keeps well under Alpha Vantage's free daily limit.
        """
        from src.data import alphavantage_client as av
        symbol = symbol.upper()
        if av.is_configured():
            out = cache.get_or_fetch(f"aveps:{symbol}",
                                     lambda: av.get_eps_history(symbol, 24), 86_400)
            if out:
                return out
        if self.mode != "yahoo":
            return []
        return cache.get_or_fetch(f"epshist:{symbol}",
                                  lambda: yfinance_client.get_eps_history(symbol), 3600)

    def get_price_frame(self, symbol: str, period: str = "1y"):
        """A year of daily closes for the price chart - real data only."""
        if self.mode != "yahoo":
            return None
        return cache.get_or_fetch(f"pxframe:{symbol}:{period}",
                                  lambda: yfinance_client.get_price_frame(symbol, period), 600)

    # ---------- TradingView second-opinion technical rating ----------
    def get_tradingview(self, symbol: str, is_index: bool = False) -> dict:
        """TradingView's Buy/Sell rating (daily + weekly). Needs internet."""
        if not self.is_real:
            return {}
        symbol = symbol.upper()

        def _fetch():
            if is_index:
                return tradingview_client.get_index_ratings(symbol)
            info = yfinance_client.get_fundamentals(symbol)
            return tradingview_client.get_ratings(symbol, tradingview_client.exchange_for(info))

        return cache.get_or_fetch(f"tv:{symbol}:{is_index}", _fetch, 600)

    # ---------- premium finder ----------
    def get_premium_snapshot(self, symbol: str, target_dte: int = 30, monthly_bp: float = 50_000):
        """Premium + a clear plan (sell puts/calls, strategy, risk) - real data only."""
        symbol = symbol.upper()
        if self.mode != "yahoo":
            return premium_finder.PremiumSnapshot(
                symbol=symbol, error="Needs real market data.")

        def _fetch():
            from src.data import stock_universe
            chain = yfinance_client.get_expiration_chain(symbol, target_dte)
            closes = yfinance_client.get_history_closes(symbol, period="6mo")
            hv = premium_finder.annualized_vol(closes)
            trend = trend_from_prices(closes)
            earnings, _ = yfinance_client.get_calendar_dates(symbol)
            grade = None
            if stock_universe.is_stock(symbol):   # ETFs have no meaningful grade
                info = yfinance_client.get_fundamentals(symbol)
                avg_vol = None
                if not (info.get("averageVolume") or info.get("averageDailyVolume10Day")):
                    avg_vol = yfinance_client.get_avg_volume(symbol)
                grade = stock_analysis.analyze(symbol, info, closes,
                                               avg_volume=avg_vol).grade
            return premium_finder.snapshot(symbol, chain, hv, trend, monthly_bp,
                                           earnings_date=earnings, grade=grade)

        return cache.get_or_fetch(f"prem:{symbol}:{target_dte}", _fetch, 300)

    # ---------- open-position pricing (the My trades tab) ----------
    def price_position(self, position) -> dict:
        """Live numbers for one open position: what it costs to close now,
        today's underlying price, and the short leg's current delta.

        Degrades gracefully: on the hosted app Yahoo sometimes blocks option
        chains, so "priced" may be False while the underlying price still
        works - the time and strike checks then still run.
        """
        out = {"priced": False, "cost_to_close": None,
               "underlying_price": None, "short_delta": None}

        sym = position.underlying.upper()
        if self.is_real:
            try:
                if self.mode == "schwab":
                    out["underlying_price"] = self._client.get_price(sym)
                else:
                    out["underlying_price"] = cache.get_or_fetch(
                        f"px:{sym}", lambda: yfinance_client.get_price(sym), 120)
            except Exception:
                pass

        dte_left = position.dte_left()
        if not position.can_track or dte_left is None or dte_left < 0:
            return out

        chain = None
        try:
            if self.mode == "schwab":
                chain = self.get_chain(sym)
            elif self.mode == "yahoo":
                chain = cache.get_or_fetch(
                    f"poschain:{sym}:{position.expiration}",
                    lambda: yfinance_client.get_expiration_chain(sym, dte_left), 300)
        except Exception:
            chain = None
        if chain is None:
            return out

        from src.engine.positions import cost_to_close_from_chain
        priced = cost_to_close_from_chain(position, chain)
        if priced is None:
            return out
        out.update(priced)
        out["priced"] = True
        if out["underlying_price"] is None:
            out["underlying_price"] = chain.underlying_price or None
        return out

    def get_buying_power_used(self) -> Optional[float]:
        if self.mode == "schwab":
            return self._client.get_buying_power()
        return None

    # ---------- demo ----------
    def _demo_chain(self, underlying: str) -> OptionChain:
        filename = _DEMO_FILES.get(underlying, "spx_chain.json")
        chain = OptionChain.from_json(SAMPLE_DIR / filename)
        chain.underlying = underlying
        return chain
