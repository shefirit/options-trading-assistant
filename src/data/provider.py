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
    trend_detail,
    trend_from_prices,
)
from src.data.schwab_client import SchwabClient
from src.data.stock_analysis import StockAnalysis
from src.engine.models import OptionType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = PROJECT_ROOT / "sample_data"

# What a chain has to look like before the premium read can do anything with it.
# The CBOE feed sprinkles thin weeklies among the real expirations: a couple of
# dozen strikes hugging the money, no deltas at all. Landing on one made the
# Premium tab say "No sellable put found" for names as liquid as SPY.
MIN_PUTS = 20             # a real strike ladder, not a stub
STRIKE_REACH = 0.93       # strikes must run at least this far below spot
EXPIRATION_WINDOW = 15    # days either side of the target we'll look in

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


def _worth_keeping(info: dict) -> bool:
    """Is this company-info answer worth holding for the full hour?

    Yes if it carries real numbers, and yes if the feed at least said outright
    what the security is - an index has no economics to give and that is not a
    failure. Everything else is a throttled blank, and a blank cached for an
    hour is what made two perfectly ordinary mid-caps look broken all morning
    while the same names loaded fine a minute later.
    """
    if not info:
        return False
    return (stock_analysis.has_economics(info)
            or bool(info.get("quoteType") or info.get("typeDisp")))


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
    def _only(chain: OptionChain, contracts: list) -> OptionChain:
        """The same chain, narrowed to one expiration's contracts."""
        return OptionChain(underlying=chain.underlying, underlying_price=chain.underlying_price,
                           fetched_at=chain.fetched_at, contracts=contracts)

    @classmethod
    def _nearest_expiration(cls, chain: OptionChain, target_dte: int) -> OptionChain:
        if not chain.contracts:
            return chain
        exp = min({c.expiration for c in chain.contracts},
                  key=lambda e: abs(next(c.dte for c in chain.contracts if c.expiration == e)
                                    - target_dte))
        return cls._only(chain, [c for c in chain.contracts if c.expiration == exp])

    @staticmethod
    def _is_tradable(contracts: list, price: float) -> bool:
        """Can you actually pick a put to sell out of this expiration?

        Three ways a chain fails that: too few strikes to be a real ladder, no
        deltas at all (so the ~0.30 delta pick has nothing to sort on), or a
        strike band that stops short of the ground the sold put needs to sit on.
        """
        puts = [c for c in contracts if c.option_type == OptionType.PUT]
        if len(puts) < MIN_PUTS or not any(c.abs_delta > 0 for c in puts):
            return False
        return not price or min(c.strike for c in puts) <= price * STRIKE_REACH

    @classmethod
    def _tradable_expiration(cls, chain: OptionChain, target_dte: int,
                             window: int = EXPIRATION_WINDOW) -> OptionChain:
        """The expiration nearest target_dte that you can actually trade from.

        Picking purely by "closest to the target DTE" lands on thin weeklies.
        SPY at 45 DTE picked one holding 29 puts, a 735-763 strike band against
        a 742.97 spot, and every delta 0.0 - so the premium finder had nothing
        to offer and the panel read "No sellable put found" for one of the most
        liquid names on the board. So look across a window either side of the
        target and keep only the expirations that pass _is_tradable.

        If none do, still hand back the nearest one: a thin chain the caller can
        judge for itself is a better answer than no chain at all.
        """
        if not chain.contracts:
            return chain
        by_exp: dict[str, list] = {}
        for c in chain.contracts:
            by_exp.setdefault(c.expiration, []).append(c)
        usable = [(abs(cs[0].dte - target_dte), exp, cs) for exp, cs in by_exp.items()
                  if abs(cs[0].dte - target_dte) <= window
                  and cls._is_tradable(cs, chain.underlying_price)]
        if not usable:
            return cls._nearest_expiration(chain, target_dte)
        return cls._only(chain, min(usable, key=lambda t: (t[0], t[1]))[2])

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

    def _expiration_chain(self, symbol: str, target_dte: int,
                          tradable: bool = False) -> Optional[OptionChain]:
        """One expiration near target_dte, same source order as get_chain.

        tradable=False takes the expiration nearest the target, full stop. That
        is what pricing an OPEN position needs: her trade sits on one specific
        expiration and must be priced on that one, thin or not.

        tradable=True is for picking a NEW trade, where any expiration in the
        neighbourhood will do and a thin weekly is simply the wrong one to show.
        """
        if self.is_real:
            full = self._cboe_full(symbol)
            if full is not None:
                return (self._tradable_expiration(full, target_dte) if tradable
                        else self._nearest_expiration(full, target_dte))
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
        """A far-dated chain for the strategies that trade one: the PMCC's long
        LEAPS (~7 months), the LEAPS long call (~420 days), the LEAPS put (~450).

        target_dte is part of the cache key, and has to be: this returns ONE
        expiration - the one nearest the target - so keying on the symbol alone
        meant whichever strategy scanned first handed its expiration to the next
        one. A PMCC scan followed by a LEAPS scan on the same name inside five
        minutes got a 7-month chain and found nothing a year out.
        """
        underlying = underlying.upper()
        return cache.get_or_fetch(
            f"leaps:{underlying}:{int(target_dte)}",
            lambda: self._expiration_chain(underlying, target_dte, tradable=True), 300)

    # ---------- market read (lightweight - no full option chain fetch) ----------
    def get_market_context(self, underlying: str) -> MarketContext:
        if self.mode == "yahoo":
            price = cache.get_or_fetch(f"px:{underlying}",
                                       lambda: yfinance_client.get_price(underlying), 120) or 0.0
            vix = cache.get_or_fetch("vix", yfinance_client.get_vix, 120)
            closes = cache.get_or_fetch(f"hist:{underlying}",
                                        lambda: yfinance_client.get_history_closes(underlying), 300)
            # trend_detail rather than trend_from_prices: the Market tab shows
            # the 20-vs-50-day gap that produced the trend, so an unchanged
            # ranking reads as a fact about the market and not a stuck screen.
            detail = trend_detail(closes or [])
            return build_context(underlying, price, vix=vix,
                                 trend=detail["trend"],
                                 trend_spread=detail["spread"],
                                 below_200=detail["below_200"])
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
            # A year, not the 3mo default: the trend read only applies its
            # 200-day veto when it HAS 200 closes. On 3 months the screen passed
            # names that stage 2 then rejected as downtrends, so a falling stock
            # burned one of the few finalist slots and turned up in "left out"
            # instead of a real candidate. Same number of requests, just fuller
            # rows, since these are batched by ticker not by day.
            history = yfinance_client.batch_history(symbols, period="1y")
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

    # ---------- company info: ONE fetch per name, shared by every panel ----------
    def _av_overview(self, symbol: str) -> dict:
        """Alpha Vantage's company snapshot, cached for a day.

        One request per name serves BOTH the fundamentals fallback and the
        analyst tally, which matters - the free key allows 25 requests a day.
        """
        from src.data import alphavantage_client as av
        if not av.is_configured():
            return {}
        return cache.get_or_fetch(f"avinfo:{symbol}", lambda: av.get_overview(symbol),
                                  86_400, keep=bool)

    def _company_info(self, symbol: str) -> dict:
        """The company-info dict for `symbol` - Yahoo, falling back to Alpha Vantage.

        Every company panel reads this one: the stats strip, the A-F grade, the
        fundamentals checks, the price calculator. They used to fetch it
        SEPARATELY - one copy behind `analysis:SYM`, another behind `info:SYM` -
        which doubled the load on the single Yahoo endpoint that throttles, and
        let the two copies disagree on screen. Rita's ACIW showed "Mkt Cap n/a"
        in the strip and $5.3B in the checks directly below it, from two calls
        made seconds apart.

        Yahoo blocks this endpoint from datacenter IPs often enough that the
        hosted app needs a second source, so when nothing usable comes back we
        ask Alpha Vantage, whose plain keyed API answers from the cloud exactly
        as it does from her laptop. Anything Yahoo DID send still wins on the
        merge - it is the fresher of the two.
        """
        symbol = symbol.upper()

        def _fetch() -> dict:
            info = yfinance_client.get_fundamentals(symbol) if self.mode == "yahoo" else {}
            if stock_analysis.has_economics(info):
                return info
            return {**self._av_overview(symbol), **info}

        return cache.get_or_fetch(f"info:{symbol}", _fetch, 3600, keep=_worth_keeping)

    # ---------- per-stock fundamental + technical analysis ----------
    def get_stock_analysis(self, symbol: str) -> Optional[StockAnalysis]:
        """Only meaningful with real data (Yahoo). Returns None in demo mode."""
        symbol = symbol.upper()
        if self.mode != "yahoo":
            return None

        def _fetch() -> StockAnalysis:
            info = self._company_info(symbol)
            closes = yfinance_client.get_history_closes(symbol, period="1y")
            # On the hosted app Yahoo often drops the info volume fields; recover
            # them from price history so liquid names aren't wrongly flagged.
            avg_vol = None
            if not (info.get("averageVolume") or info.get("averageDailyVolume10Day")):
                avg_vol = yfinance_client.get_avg_volume(symbol)
            return stock_analysis.analyze(symbol, info, closes, avg_volume=avg_vol)

        # A verdict built on numbers that never arrived is held for seconds, not
        # minutes: she reads this panel to decide what to sell options on, and
        # "not enough loaded to grade it" should clear itself on a refresh.
        return cache.get_or_fetch(
            f"analysis:{symbol}", _fetch, 300,
            keep=lambda a: a is not None and not a.data_partial and a.kind != "unknown")

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
                                      lambda: yfinance_client.get_earnings_info(symbol),
                                      3600, keep=bool)
        return {}

    def get_raw_info(self, symbol: str) -> dict:
        """The company-info dict behind the key-stats strip - the SAME fetch the
        grade and the fundamentals checks read, so they can never disagree."""
        if self.mode != "yahoo":
            return {}
        return self._company_info(symbol)

    def get_price_change(self, symbol: str):
        """(price, today's % change) for one symbol."""
        if self.mode == "yahoo":
            return cache.get_or_fetch(f"tile:{symbol}",
                                      lambda: yfinance_client.get_price_change(symbol), 120)
        return (None, None)

    def get_analyst_ratings(self, symbol: str) -> dict:
        """Wall Street buy/hold/sell counts - real data only.

        Yahoo first, Alpha Vantage's tally behind it. This is the other endpoint
        the hosted app cannot reach: "No analyst data available for this name"
        showed on stocks with a dozen analysts covering them, and being empty it
        then sat in the cache for an hour looking like a fact about the company.
        """
        if self.mode != "yahoo":
            return {}
        symbol = symbol.upper()

        def _fetch() -> dict:
            out = yfinance_client.get_analyst_ratings(symbol)
            if any(out.values()):
                return out
            return self._av_overview(symbol).get("analystRatings") or {}

        return cache.get_or_fetch(f"analysts:{symbol}", _fetch, 3600,
                                  keep=lambda r: any((r or {}).values()))

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

    def get_ohlc(self, symbol: str, period: str = "2y", interval: str = "1d"):
        """Candle bars for the Analyze tab's chart. None when we have no real data.

        Unlike get_price_frame this runs in Schwab mode too - price HISTORY comes
        from Yahoo's chart endpoint either way, and the Schwab connection only
        replaces the option chain and live quote.
        """
        if not self.is_real:
            return None
        return cache.get_or_fetch(
            f"ohlc:{symbol}:{period}:{interval}",
            lambda: yfinance_client.get_ohlc_frame(symbol, period, interval), 600)

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

    # ---------- research tools (seasonality, analyst, LEAPS, valuation) ----------
    def get_long_closes(self, symbol: str, period: str = "20y") -> list[float]:
        """As many years of daily closes as Yahoo will give us.

        Everything in the Research tab that talks about "how often has this
        stock actually done that" rides on this one call, so it is cached hard
        (a day) - twenty years of history does not change intraday.
        """
        symbol = symbol.upper()
        if not self.is_real:
            return []

        def _fetch() -> list[float]:
            frame = yfinance_client.get_price_frame(symbol, period)
            if frame is None or len(frame) == 0:
                return []
            return [float(c) for c in frame["Close"] if c and float(c) > 0]

        return cache.get_or_fetch(f"longcloses:{symbol}:{period}", _fetch, 24 * 3600) or []

    def get_seasonality(self, symbol: str, lookback_years: int = 20):
        """Month-by-month history. Uses Yahoo's adjusted closes, so these are
        TOTAL returns with dividends reinvested - the right basis for asking
        what owning it through a month actually paid."""
        symbol = symbol.upper()
        if not self.is_real:
            return None

        def _fetch():
            from src.research import seasonality
            frame = yfinance_client.get_price_frame(symbol, "max")
            points = seasonality.frame_to_points(frame)
            return seasonality.build(symbol, points, max_years=lookback_years)

        return cache.get_or_fetch(f"season:{symbol}:{lookback_years}", _fetch, 24 * 3600)

    def get_analyst_view(self, symbol: str):
        """Consensus rating and targets, with the historical reality check."""
        symbol = symbol.upper()
        if not self.is_real:
            return None

        def _fetch():
            from src.research import analyst
            info = yfinance_client.get_fundamentals(symbol)
            price = (info.get("currentPrice") or info.get("regularMarketPrice")
                     or yfinance_client.get_price(symbol))
            return analyst.build(symbol, price, yfinance_client.get_analyst_ratings(symbol),
                                 info, self.get_long_closes(symbol))

        return cache.get_or_fetch(f"analystview:{symbol}", _fetch, 3600)

    def get_options_view(self, symbol: str, target_dte: int = 30):
        """Implied volatility, expected move and put/call sentiment."""
        symbol = symbol.upper()

        def _fetch():
            from src.research import options_view
            chain = self.get_chain(symbol, dte_min=1, dte_max=400)
            if chain is None or not chain.contracts:
                return None
            return options_view.build(chain, self.get_long_closes(symbol), target_dte)

        return cache.get_or_fetch(f"optview:{symbol}:{target_dte}", _fetch, 300)

    def get_leaps_candidate(self, symbol: str, target_delta: Optional[float] = None):
        """The full LEAPS scorecard for one stock: chart, quality, the real
        contract's economics, and the odds from its own history.

        target_delta defaults to the SOP's floor rather than a number pinned
        here, so the Analyze tab and Find a trade cannot price different
        contracts for the same name.
        """
        symbol = symbol.upper()
        if target_delta is None:
            from src.research import leaps as _leaps
            target_delta = _leaps.target_delta()

        def _fetch():
            from src.research import leaps
            closes = self.get_long_closes(symbol)
            info = yfinance_client.get_fundamentals(symbol) if self.is_real else {}
            candidate = leaps.score_setup(symbol, closes, market_cap=info.get("marketCap"),
                                          info=info)
            # The WHOLE long-dated window, not one expiration. This used to call
            # get_leaps_chain, which narrows to a single expiration near the
            # target - and it chooses that expiration with _is_tradable, a test
            # that reads the PUT ladder because it was written for the premium
            # finder. For a long call that is the wrong question asked of the
            # wrong side of the chain, and it handed the picker 308-day
            # expirations and strikes with an open interest of 2. The picker
            # needs every expiration in range to honour her rules, because the
            # compliant contract is routinely a date further out.
            chain = self.get_chain(symbol, dte_min=leaps.min_leap_dte(),
                                   dte_max=leaps.max_leap_dte())
            if chain is None or not chain.contracts:
                # Some names genuinely list nothing that far out. Widen so she
                # still gets a scored contract - score_full now flags it as
                # non-compliant rather than presenting it as a clean pick.
                chain = self.get_chain(symbol, dte_min=200, dte_max=800)
            iv_pct = None
            if chain is not None:
                contract = leaps.pick_contract(chain, target_delta)
                if contract is not None and contract.iv:
                    iv_pct = leaps.vol_percentile(closes, contract.iv * 100)
            earnings = self.get_earnings_info(symbol) if self.is_real else {}
            next_earnings = earnings.get("earnings_date") if earnings else None
            candidate.earnings_date = next_earnings
            if next_earnings:
                candidate.days_to_earnings = (next_earnings - date.today()).days
            target = info.get("targetMeanPrice")
            candidate.analyst_target = float(target) if target else None
            return leaps.score_full(candidate, chain, closes, info, target_delta, iv_pct)

        return cache.get_or_fetch(f"leapscand:{symbol}:{target_delta}", _fetch, 600)

    def get_leaps_scan(self, cache_key: str, symbols: list[str]) -> Optional[list]:
        """Stage one of the LEAPS Finder: score a whole universe on price action.

        One batched history download covers hundreds of names. Deliberately no
        option data here - a chain per stock would take many minutes. She picks
        from this list and we price the actual contract on demand.

        Returns None (uncached) when the batch came back empty, so a throttled
        download retries on the next press rather than sticking for hours.
        """
        if self.mode != "yahoo":
            return None

        def _fetch():
            from src.data import stock_universe
            from src.research import leaps
            names = [s.upper() for s in dict.fromkeys(symbols)]
            history = yfinance_client.batch_history(names, period="2y")
            if not history:
                return None
            caps = stock_universe.market_caps()
            out = []
            for symbol in names:
                closes, volumes = history.get(symbol, ([], []))
                if len(closes) < 60:
                    continue
                out.append(leaps.score_setup(symbol, closes, volumes,
                                             market_cap=caps.get(symbol)))
            return out

        result = cache.get_or_fetch(f"leapsscan:{cache_key}", _fetch, 6 * 3600)
        if result is None:
            cache.clear(f"leapsscan:{cache_key}")
        return result

    # ---------- premium finder ----------
    def call_quote(self, symbol: str, lo: int, target: int, hi: int) -> Optional[dict]:
        """The call she would sell, read straight from the cached chain.

        A covered call needs five numbers - the expiration, the ~0.30 delta
        strike, its delta, what it pays, and whether it can be traded. Getting
        them via a second premium snapshot meant recomputing the whole put-side
        analysis (realized vol, implied vol, the plan, the verdict) and throwing
        all of it away: about half a second per name, which is twenty seconds of
        a scan. Everything else the pick needs - quality, trend, richness,
        earnings - is already on the put snapshot the scan just built.
        """
        dte = self.best_call_expiration(symbol, lo, target, hi)
        full = self._cboe_full(symbol.upper()) if self.is_real else None
        if dte is None or full is None:
            return None
        calls = [c for c in full.contracts
                 if c.dte == dte and c.option_type == OptionType.CALL and c.abs_delta > 0]
        if not calls:
            return None
        call = min(calls, key=lambda c: abs(c.abs_delta - 0.30))
        mid = call.mid
        if not mid or mid <= 0:
            return None
        spread_pct = None
        if call.bid > 0 and call.ask > 0:
            m = (call.bid + call.ask) / 2
            spread_pct = ((call.ask - call.bid) / m * 100) if m > 0 else None
        oi = call.open_interest or 0
        if spread_pct is None:
            liquidity = "n/a"
        elif spread_pct <= 6 and oi >= 250:
            liquidity = "Good"
        elif spread_pct <= 15:
            liquidity = "OK"
        else:
            liquidity = "Thin"
        return {"dte": dte, "strike": call.strike, "delta": round(call.abs_delta, 2),
                "credit": round(mid * 100, 0), "liquidity": liquidity,
                "price": full.underlying_price}

    def best_call_expiration(self, symbol: str, lo: int, target: int, hi: int,
                             ) -> Optional[int]:
        """The days-to-expiration of the best CALL to sell on this name.

        Answered from the one cached full chain, in a single pass, rather than
        by asking for a snapshot at five different targets and seeing which came
        back tradable. Each of those snapshots re-fetched six months of history,
        the earnings calendar and the fundamentals over the network, so the
        covered-call search was costing four extra round trips per name and
        adding about a minute to a Quick look.

        Prefers her window (lo..hi), nearest `target`, keeping only expirations
        whose ~0.30 delta call is actually tradable. Falls back to the most
        tradable expiration outside the window, then to None so the caller can
        use its own default.
        """
        symbol = symbol.upper()
        full = self._cboe_full(symbol) if self.is_real else None
        if full is None or not full.contracts:
            return None
        price = full.underlying_price or 0.0

        def sellable(dte: int) -> bool:
            calls = [c for c in full.contracts
                     if c.dte == dte and c.option_type == OptionType.CALL]
            if len(calls) < MIN_PUTS or not any(c.abs_delta > 0 for c in calls):
                return False
            # The strike she would actually sell, and whether its quote is one a
            # seller can live with - a 40% bid-ask gap eats the premium whole.
            target_call = min((c for c in calls if c.abs_delta > 0),
                              key=lambda c: abs(c.abs_delta - 0.30))
            if not (target_call.bid > 0 and target_call.ask > 0):
                return False
            mid = (target_call.bid + target_call.ask) / 2
            if mid <= 0:
                return False
            return ((target_call.ask - target_call.bid) / mid * 100) <= 15

        dtes = sorted({c.dte for c in full.contracts if c.dte > 0})
        inside = [d for d in dtes if lo <= d <= hi and sellable(d)]
        if inside:
            return min(inside, key=lambda d: abs(d - target))
        outside = [d for d in dtes if sellable(d)]
        if outside:
            return min(outside, key=lambda d: abs(d - target))
        return None

    def get_premium_snapshot(self, symbol: str, target_dte: int = 30, monthly_bp: float = 50_000):
        """Premium + a clear plan (sell puts/calls, strategy, risk) - real data only."""
        symbol = symbol.upper()
        if self.mode != "yahoo":
            return premium_finder.PremiumSnapshot(
                symbol=symbol, error="Needs real market data.")

        def _fetch():
            from src.engine import config_loader
            chain = self._expiration_chain(symbol, target_dte, tradable=True)
            if chain is None:
                chain = yfinance_client.get_expiration_chain(symbol, target_dte)
            # A full year, not 6 months, so the volatility rank has a 52-week
            # range to sit in. Safe to widen: annualized_vol reads only the last
            # 31 closes and trend_from_prices only the last 50, so neither moves.
            closes = yfinance_client.get_history_closes(symbol, period="1y")
            hv = premium_finder.annualized_vol(closes)
            trend = trend_from_prices(closes)
            earnings, _ = yfinance_client.get_calendar_dates(symbol)
            grade = None
            if config_loader.underlying_kind(symbol) == "stock":   # ETFs have no meaningful grade
                info = self._company_info(symbol)
                avg_vol = None
                if not (info.get("averageVolume") or info.get("averageDailyVolume10Day")):
                    avg_vol = yfinance_client.get_avg_volume(symbol)
                grade = stock_analysis.analyze(symbol, info, closes,
                                               avg_volume=avg_vol).grade
            return premium_finder.snapshot(symbol, chain, hv, trend, monthly_bp,
                                           earnings_date=earnings, grade=grade,
                                           closes=closes)

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
        # None here is not a failure: an uncovered PMCC has nothing sold to buy
        # back. The whole-position pricing below still has to run, so this must
        # never return early.
        priced = cost_to_close_from_chain(position, chain)
        if priced is not None:
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

        if not position.is_debit and not position.is_long_premium:
            return None      # a credit spread has no long side worth a card

        # Usually the LEAPS sits in a different expiration and needs its own
        # fetch. Once she is uncovered it IS the near expiration, so the chain
        # already in hand covers everything and far_legs is legitimately empty.
        contracts = list(near_chain.contracts)
        today = date.today()
        seen: set[str] = set()
        for leg in position.far_legs:
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
