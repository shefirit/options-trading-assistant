"""One place the UI asks for market data. It hides WHERE the data comes from:

  - LIVE (Schwab):  true real-time from your account (once Schwab is connected).
  - REAL (Yahoo):   real prices/chains, about 15 min delayed - free, works today.
  - DEMO:           bundled sample chains, if you are offline.

The UI code stays identical; only the source changes. It also provides the
market read (trend + VIX) and the per-stock fundamental/technical analysis.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from src.data import (
    cache,
    cboe_client,
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
    def _cboe_full(self, symbol: str) -> Optional[OptionChain]:
        """The whole CBOE chain for a name (all expirations), cached per symbol.
        CBOE is free, needs no key, and isn't IP-blocked like Yahoo, so it's the
        default chain source on the hosted app. One fetch feeds every window."""
        def _fetch():
            try:
                ch = cboe_client.get_option_chain(symbol, from_dte=0, to_dte=3650)
                return ch if ch.contracts else None
            except Exception:
                return None
        return cache.get_or_fetch(f"cfull:{symbol}", _fetch, 180)

    @staticmethod
    def _window(chain: OptionChain, lo: int, hi: int) -> OptionChain:
        kept = [c for c in chain.contracts if lo <= c.dte <= hi]
        return OptionChain(underlying=chain.underlying, underlying_price=chain.underlying_price,
                           fetched_at=chain.fetched_at, contracts=kept)

    @staticmethod
    def _nearest_expiration(chain: OptionChain, target_dte: int) -> OptionChain:
        if not chain.contracts:
            return chain
        exp = min({c.expiration for c in chain.contracts},
                  key=lambda e: abs(next(c.dte for c in chain.contracts if c.expiration == e)
                                    - target_dte))
        kept = [c for c in chain.contracts if c.expiration == exp]
        return OptionChain(underlying=chain.underlying, underlying_price=chain.underlying_price,
                           fetched_at=chain.fetched_at, contracts=kept)

    def get_chain(self, underlying: str, dte_min: Optional[int] = None,
                 dte_max: Optional[int] = None) -> OptionChain:
        """Real option chain for a name. Source order: Schwab (local real-time) ->
        CBOE (free, no key, the hosted default) -> Yahoo (fallback).
        dte_min/dte_max keep only the expirations you need."""
        underlying = underlying.upper()
        if self.mode == "schwab":
            return cache.get_or_fetch(f"chain:{underlying}",
                                      lambda: self._client.get_option_chain(underlying), 60)
        lo = 15 if dte_min is None else dte_min
        hi = 70 if dte_max is None else dte_max
        if self.is_real:
            full = self._cboe_full(underlying)
            if full is not None:
                return self._window(full, lo, hi)
        if self.mode == "yahoo":
            return cache.get_or_fetch(
                f"ychain:{underlying}:{lo}:{hi}",
                lambda: yfinance_client.get_option_chain(underlying, from_dte=lo, to_dte=hi), 120)
        return self._demo_chain(underlying)

    def _expiration_chain(self, symbol: str, target_dte: int) -> Optional[OptionChain]:
        """One expiration nearest target_dte, same source order as get_chain."""
        if self.is_real:
            full = self._cboe_full(symbol)
            if full is not None:
                return self._nearest_expiration(full, target_dte)
        if self.mode == "yahoo":
            return yfinance_client.get_expiration_chain(symbol, target_dte)
        return None

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
        return cache.get_or_fetch(
            f"leaps:{underlying}", lambda: self._expiration_chain(underlying, target_dte), 300)

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

    def get_history_closes(self, symbol: str) -> list[float]:
        """Daily closes, oldest first (a year) - same cache the market read uses."""
        if self.mode != "yahoo":
            return []
        return cache.get_or_fetch(f"hist:{symbol}",
                                  lambda: yfinance_client.get_history_closes(symbol), 300)

    def get_market_pulse(self, symbols: list[str]) -> dict[str, tuple[list[float], list[float]]]:
        """Recent closes for many ETFs in ONE batched request (the chart endpoint
        that stays reliable from cloud hosts) - feeds the sector-pulse grid.
        Empty result (Yahoo throttle) is not cached, so the next rerun retries."""
        if self.mode == "demo":
            from src.data import market_read
            return market_read.demo_pulse_history(symbols)
        if self.mode != "yahoo":
            return {}
        key = "pulse:" + ",".join(s.upper() for s in symbols)
        out = cache.get_or_fetch(
            key, lambda: yfinance_client.batch_history(symbols, period="5d"), 300)
        if not out:
            cache.clear(key)
        return out or {}

    def get_news(self, limit: int = 6) -> list:
        """Recent market-news headlines for the Market tab (a list of NewsItem).
        Free public RSS feeds (no key) - reliable from cloud hosts, unlike the
        quote APIs. Demo mode returns a canned list; an empty live result is not
        cached, so the next rerun retries."""
        from src.data import news_client
        if self.mode == "demo":
            return news_client.demo_headlines(limit)
        out = cache.get_or_fetch("news", lambda: news_client.fetch_headlines(limit), 900)
        if not out:
            cache.clear("news")
        return out or []

    # ---------- stage-1 market screen (the Picks tab's funnel) ----------
    def get_screen(self, cache_key: str, stocks: list[str], etfs: list[str],
                   rules) -> Optional[dict]:
        """Screen the whole universe with one batched history download.

        Returns {"results": [ScreenResult...], "finalists": [ScreenResult...]},
        cached for 6 hours (volume/trend barely move intraday). Returns None
        when the batch download came back empty (Yahoo throttling) - and does
        NOT cache that, so the next press retries.
        """
        if self.mode != "yahoo":
            return None

        def _fetch():
            from src.data import market_screener, stock_universe
            symbols = list(dict.fromkeys([*etfs, *stocks]))
            history = yfinance_client.batch_history(symbols)
            if not history:
                return None
            caps = stock_universe.market_caps()
            results = []
            for sym in etfs:
                closes, vols = history.get(sym.upper(), ([], []))
                results.append(market_screener.build_result(sym, "etf", closes, vols, rules))
            for sym in stocks:
                closes, vols = history.get(sym.upper(), ([], []))
                results.append(market_screener.build_result(sym, "stock", closes, vols, rules,
                                                            market_cap=caps.get(sym.upper())))
            return {"results": results,
                    "finalists": market_screener.finalists(results, rules)}

        out = cache.get_or_fetch(f"screen:{cache_key}", _fetch, 6 * 3600)
        if out is None:
            cache.clear(f"screen:{cache_key}")   # a throttled download must not stick for 6h
        return out

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
            chain = self._expiration_chain(symbol, target_dte)
            if chain is None:
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
               "underlying_price": None, "short_delta": None,
               "position_value": None, "open_pl": None,
               "options_pl": None, "shares_pl": None}

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
            else:
                chain = cache.get_or_fetch(
                    f"poschain:{sym}:{position.expiration}",
                    lambda: self._expiration_chain(sym, dte_left), 300)
        except Exception:
            chain = None
        if chain is None:
            return out

        from src.engine.positions import (cost_to_close_from_chain,
                                          position_value_from_chain)
        priced = cost_to_close_from_chain(position, chain)
        if priced is None:
            return out
        out.update(priced)
        out["priced"] = True
        if out["underlying_price"] is None:
            out["underlying_price"] = chain.underlying_price or None

        whole = self._price_whole_position(position, chain, out["underlying_price"])
        if whole is not None:
            # Forward everything the engine computed rather than naming fields
            # one by one: the covered calls' options_pl/shares_pl split was
            # silently dropped that way once already. "value" is the only rename.
            out["position_value"] = whole["value"]
            out.update({k: v for k, v in whole.items() if k != "value"})
        return out

    def _price_whole_position(self, position, near_chain,
                              underlying_price) -> Optional[dict]:
        """The PMCC / covered call total, which needs the far-dated leg priced.

        The chain above only covers the near expiration - all a credit spread
        ever needs. A PMCC's LEAPS sits a year out, in a different expiration
        entirely, so it takes a second fetch that gets merged in. Returns None
        (and the card simply shows no total) whenever any contract is missing,
        which is the honest answer rather than a total with a leg left out.
        """
        from src.engine.positions import position_value_from_chain

        far_legs = position.far_legs
        if not far_legs:
            return None

        contracts = list(near_chain.contracts)
        today = date.today()
        seen: set[str] = set()
        for leg in far_legs:
            exp = position.leg_expiration(leg)
            if exp is None:
                return None
            if exp.isoformat() in seen:
                continue
            seen.add(exp.isoformat())
            try:
                far_chain = cache.get_or_fetch(
                    f"farchain:{position.underlying.upper()}:{exp.isoformat()}",
                    lambda e=exp: self._expiration_chain(
                        position.underlying.upper(), max((e - today).days, 0)),
                    900)   # LEAPS barely move minute to minute; cache them longer
            except Exception:
                return None
            if far_chain is None:
                return None
            contracts.extend(far_chain.contracts)

        merged = OptionChain(underlying=near_chain.underlying,
                             underlying_price=near_chain.underlying_price,
                             contracts=contracts)
        return position_value_from_chain(position, merged, underlying_price)

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
