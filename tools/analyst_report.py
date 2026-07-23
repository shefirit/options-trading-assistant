"""One-shot fundamental + technical research report on any stock, ETF or index.

This is the data feed for the `stock-analyst` Claude agent. It pulls everything
the agent needs in a single run so the agent can spend its effort on explaining
rather than fetching.

    python tools/analyst_report.py AAPL
    python tools/analyst_report.py AAPL MSFT NVDA

The report answers one question: is this a good company and a good chart, and is
it therefore a sensible thing to sell options on. It deliberately does NOT pick
strikes, size positions or recommend a strategy - that is the app's job, and
mixing the two buried the actual research under trade mechanics.

Everything is wrapped defensively: if one feed is throttled the rest of the
report still prints, and the missing part says so instead of crashing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# The metric text contains arrows and other symbols. Windows consoles default to
# cp1252, which raises on those, so force UTF-8 output here.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

from src.data import premium_finder, stock_universe, yfinance_client  # noqa: E402
from src.data.market_context import trend_from_prices  # noqa: E402
from src.data.provider import DataProvider  # noqa: E402
from src.data.stock_analysis import rsi, sma  # noqa: E402
from src.engine import recommender  # noqa: E402

INDEXES = {"SPX", "NDX", "RUT", "XSP", "^SPX", "^NDX", "^RUT", "VIX", "^VIX"}

BENCHMARK = "SPY"          # what "the market" means for relative strength


# ---------------------------------------------------------------- formatting
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:                              # noqa: BLE001
        return default


def _f(v):
    """Coerce to float, or None. yfinance mixes in strings and NaN."""
    try:
        out = float(v)
        return None if out != out else out         # NaN check
    except (TypeError, ValueError):
        return None


def _pct(v, digits=1):
    v = _f(v)
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def _num(v, digits=2):
    v = _f(v)
    return "n/a" if v is None else f"{v:,.{digits}f}"


def _big(v):
    v = _f(v)
    if v is None:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            return f"${v / size:.2f}{unit}"
    return f"${v:,.0f}"


def _row(label, value, read=""):
    print(f"  {label:<34} {value:>14}   {read}")


def _head(title):
    print(f"\n-- {title} " + "-" * max(4, 62 - len(title)))


def _kind(symbol: str) -> str:
    if symbol.upper().lstrip("^") in {s.lstrip("^") for s in INDEXES}:
        return "index"
    return "stock" if stock_universe.is_stock(symbol) else "etf"


def _days_until(d):
    return (d - dt.date.today()).days if isinstance(d, dt.date) else None


def _ohlcv(symbol: str, period: str = "2y") -> list[dict]:
    """Daily open/high/low/close/volume for the candlestick chart.

    provider.get_price_frame returns a Close-only frame (it feeds a line chart),
    so the candles need their own fetch.
    """
    frame = yfinance_client._ticker(symbol).history(period=period)
    if frame is None or len(frame) == 0:
        return []
    rows = []
    for idx, r in frame.iterrows():
        try:
            o, hi, lo, c = (float(r["Open"]), float(r["High"]),
                            float(r["Low"]), float(r["Close"]))
        except (TypeError, ValueError, KeyError):
            continue
        if any(v != v for v in (o, hi, lo, c)):        # NaN rows
            continue
        rows.append({"d": idx.date().isoformat(), "o": o, "h": hi, "l": lo, "c": c,
                     "v": float(r.get("Volume") or 0)})
    return rows


def _dividend_yield_pct(info: dict):
    """yfinance has shipped this as a fraction AND as a percent across versions.

    Under 0.12 it is almost certainly a fraction (0.0033 = 0.33%); between 0.12
    and 25 it is already a percent; above 25 it is junk. Returns percent units.
    """
    raw = _f(info.get("dividendYield"))
    if raw is None or raw <= 0:
        return None
    if raw < 0.12:
        return raw * 100
    return raw if raw <= 25 else None


# ---------------------------------------------------------------- sections
def _what_it_is(symbol: str, kind: str, info: dict, analysis) -> None:
    _head("What it is")
    name = info.get("longName") or info.get("shortName") or (
        analysis.name if analysis else symbol)
    print(f"  {name}")
    if kind == "stock":
        print(f"  Sector: {info.get('sector') or 'n/a'}   "
              f"Industry: {info.get('industry') or 'n/a'}")
        emp = _f(info.get("fullTimeEmployees"))
        if emp:
            print(f"  Employees: {emp:,.0f}")
        summary = (info.get("longBusinessSummary") or "").strip()
        if summary:
            print("\n  How it makes money (company's own description):")
            for chunk in _wrap(summary, 92):
                print(f"    {chunk}")
    elif kind == "etf":
        print("  This is an ETF - a fund holding many companies at once. There is "
              "no single\n  P/E, profit margin or revenue line, because those "
              "belong to the holdings.")
        cat = info.get("category") or info.get("fundFamily")
        if cat:
            print(f"  Category: {cat}")
    else:
        print("  This is an INDEX - a number tracking a basket of companies. You "
              "cannot own\n  shares of it. Company fundamentals do not apply.")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:8]                               # keep it readable


def _valuation(info: dict) -> None:
    _head("Valuation - what you pay for what you get")
    pe = _f(info.get("trailingPE"))
    fpe = _f(info.get("forwardPE"))
    _row("Market value (market cap)", _big(info.get("marketCap")))
    _row("Enterprise value", _big(info.get("enterpriseValue")),
         "market cap plus debt, minus cash")
    _row("P/E (trailing)", _num(pe),
         "price per $1 of last year's profit"
         + ("" if pe is None else _pe_read(pe)))
    _row("P/E (forward)", _num(fpe),
         "same, using next year's expected profit")
    _row("PEG ratio", _num(info.get("pegRatio")),
         "P/E divided by growth. Under 1 is cheap for the growth")
    _row("Price to book", _num(info.get("priceToBook")),
         "price vs accounting net worth")
    _row("Price to sales", _num(info.get("priceToSalesTrailing12Months")),
         "price per $1 of revenue")
    _row("Earnings per share (trailing)", _num(info.get("trailingEps")))
    _row("Earnings per share (forward)", _num(info.get("forwardEps")))


def _pe_read(pe: float) -> str:
    if pe <= 0:
        return " - negative, the company is losing money"
    if pe < 15:
        return " - cheap end"
    if pe < 25:
        return " - around the market's normal range"
    if pe < 40:
        return " - expensive, growth is priced in"
    return " - very expensive, a lot must go right"


def _profitability(info: dict) -> None:
    _head("Profitability - how good the business is")
    _row("Gross margin", _pct(info.get("grossMargins")),
         "kept from each sale before overheads")
    _row("Operating margin", _pct(info.get("operatingMargins")),
         "kept after running the business")
    _row("Net profit margin", _pct(info.get("profitMargins")),
         "kept as actual profit after everything")
    _row("Return on equity", _pct(info.get("returnOnEquity")),
         "profit per $1 of owners' money")
    _row("Return on assets", _pct(info.get("returnOnAssets")),
         "profit per $1 of everything it owns")
    _row("Revenue (last 12 months)", _big(info.get("totalRevenue")))
    _row("EBITDA", _big(info.get("ebitda")),
         "rough cash profit before interest, tax and write-downs")


def _growth(info: dict) -> None:
    _head("Growth - is it getting bigger")
    _row("Revenue growth (year)", _pct(info.get("revenueGrowth")))
    _row("Earnings growth (year)", _pct(info.get("earningsGrowth")))
    _row("Earnings growth (quarter)", _pct(info.get("earningsQuarterlyGrowth")))


def _balance_sheet(info: dict) -> None:
    _head("Financial strength - can it survive a bad year")
    d2e = _f(info.get("debtToEquity"))
    _row("Cash on hand", _big(info.get("totalCash")))
    _row("Total debt", _big(info.get("totalDebt")))
    _row("Debt to equity", _num(d2e),
         "debt per 100 of owners' money" + ("" if d2e is None else _d2e_read(d2e)))
    _row("Current ratio", _num(info.get("currentRatio")),
         "short-term assets vs short-term bills. Over 1 is healthy")
    _row("Free cash flow", _big(info.get("freeCashflow")),
         "real cash left after running and investing")
    _row("Operating cash flow", _big(info.get("operatingCashflow")))


def _d2e_read(d2e: float) -> str:
    if d2e < 50:
        return " - low debt, conservative"
    if d2e < 100:
        return " - moderate debt, normal"
    if d2e < 200:
        return " - high debt, watch it"
    return " - very high debt, fragile in a downturn"


def _dividend(info: dict) -> None:
    dy = _dividend_yield_pct(info)
    if dy is None:
        return
    _head("Dividend")
    _row("Dividend yield", f"{dy:.2f}%", "cash paid per year, as % of price")
    _row("Payout ratio", _pct(info.get("payoutRatio")),
         "share of profit paid out. Over 100% is unsustainable")


def _sentiment(info: dict, ratings: dict) -> None:
    _head("Who owns it and what the pros think")
    price = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
    tgt = _f(info.get("targetMeanPrice"))
    _row("Institutional ownership", _pct(info.get("heldPercentInstitutions")),
         "held by funds and pensions")
    _row("Short interest (% of float)", _pct(info.get("shortPercentOfFloat")),
         "bet against it. Over 10% is heavily shorted")
    _row("Beta", _num(info.get("beta")),
         "moves vs the market. 1.0 = same, 2.0 = twice as jumpy")
    if tgt:
        upside = f"{(tgt / price - 1) * 100:+.1f}%" if price else "n/a"
        _row("Analyst price target (average)", _num(tgt), f"{upside} vs today")
        _row("Target range", f"{_num(info.get('targetLowPrice'))} - "
                             f"{_num(info.get('targetHighPrice'))}",
             f"{info.get('numberOfAnalystOpinions') or '?'} analysts")
    if info.get("recommendationKey"):
        _row("Consensus rating", str(info["recommendationKey"]).upper())
    if ratings:
        total = sum(ratings.values()) or 1
        bullish = ratings.get("strong_buy", 0) + ratings.get("buy", 0)
        print(f"  Analyst breakdown: strong buy {ratings.get('strong_buy', 0)} | "
              f"buy {ratings.get('buy', 0)} | hold {ratings.get('hold', 0)} | "
              f"sell {ratings.get('sell', 0)} | "
              f"strong sell {ratings.get('strong_sell', 0)}"
              f"   ({bullish / total * 100:.0f}% say buy)")


def _returns(closes: list[float]) -> dict:
    """Total return over standard lookbacks, in percent."""
    out, last = {}, closes[-1]
    for label, back in (("1 month", 21), ("3 months", 63),
                        ("6 months", 126), ("1 year", 252)):
        if len(closes) > back:
            out[label] = (last / closes[-back - 1] - 1) * 100
    return out


def _technicals(symbol: str, closes: list[float], bench: list[float],
                long_closes: list[float], long_bench: list[float]) -> None:
    """`closes` is one year (52-week stats). `long_closes` is two years, which is
    what the 1-year RETURN needs - a 1y fetch comes back with exactly 252 bars,
    one short of what a full 252-day lookback requires."""
    _head("Technicals - what the price is actually doing")
    last = closes[-1]
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    print(f"  Trend read: {trend_from_prices(closes)}")
    for label, val in (("20-day average", s20), ("50-day average", s50),
                       ("200-day average", s200)):
        if val:
            gap = (last / val - 1) * 100
            _row(label, _num(val),
                 f"price is {gap:+.1f}% {'above' if gap >= 0 else 'below'} it")
    if s50 and s200:
        cross = ("50-day is ABOVE the 200-day (golden cross - the healthy "
                 "long-term setup)" if s50 > s200 else
                 "50-day is BELOW the 200-day (death cross - long-term "
                 "trend is damaged)")
        print(f"  {cross}")

    r = rsi(closes)
    if r is not None:
        read = ("overbought, has run up fast" if r > 70 else
                "oversold, has been beaten down" if r < 30 else "neutral")
        _row("RSI (momentum, 0-100)", _num(r, 0), read)

    hi, lo = max(closes), min(closes)
    _row("52-week high", _num(hi), f"{(last / hi - 1) * 100:+.1f}% from here")
    _row("52-week low", _num(lo), f"{(last / lo - 1) * 100:+.1f}% from here")
    _row("Position in 52-week range", f"{(last - lo) / (hi - lo) * 100:.0f}%",
         "0% = at the low, 100% = at the high")

    # drawdown from the running peak
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    _row("Worst drop from a peak (1y)", f"{worst * 100:.1f}%",
         "the deepest fall it put you through")

    hv = premium_finder.annualized_vol(closes)
    _row("Realized volatility", _pct(hv), "how much it ACTUALLY moved, annualized")
    moves = [abs(closes[i] / closes[i - 1] - 1) for i in range(-21, 0)]
    _row("Average daily move (last month)", _pct(sum(moves) / len(moves), 2))

    print("\n  RETURNS")
    rets = _returns(long_closes or closes)
    src_bench = long_bench or bench
    # Comparing the benchmark against itself just prints "beat by 0.0 points".
    bench_rets = ({} if symbol.upper() == BENCHMARK
                  else _returns(src_bench) if len(src_bench) > 252 else {})
    for label, val in rets.items():
        extra = ""
        if label in bench_rets:
            diff = val - bench_rets[label]
            extra = (f"   vs {BENCHMARK} {bench_rets[label]:+.1f}%  "
                     f"-> {'BEAT' if diff >= 0 else 'LAGGED'} the market "
                     f"by {abs(diff):.1f} points")
        print(f"    {label:<10} {val:+7.1f}%{extra}")


def _volume(symbol: str) -> None:
    """Volume comes from get_avg_volume, not get_price_frame.

    get_price_frame returns a Close-only frame (it feeds the price chart), so
    there is no Volume column to read there.
    """
    recent = _f(_safe(lambda: yfinance_client.get_avg_volume(symbol, "1mo")))
    older = _f(_safe(lambda: yfinance_client.get_avg_volume(symbol, "6mo")))
    if not recent or not older:
        return
    shift = (recent / older - 1) * 100
    _head("Trading activity")
    _row("Average daily volume (1 month)", f"{recent / 1e6:,.1f}M shares")
    _row("Average daily volume (6 months)", f"{older / 1e6:,.1f}M shares")
    _row("Recent vs normal", f"{shift:+.0f}%",
         "busier than usual - something is going on" if shift > 15 else
         "quieter than usual" if shift < -15 else "about normal")
    if recent < 1e6:
        print("  WARNING: under 1M shares a day. Thin stocks usually have wide, "
              "expensive options.")


def _earnings(symbol: str, provider: DataProvider, kind: str) -> None:
    _head("Earnings")
    if kind != "stock":
        print("  Not applicable - a fund or index does not report earnings.")
        return
    info = _safe(lambda: provider.get_earnings_info(symbol), {}) or {}
    ed = info.get("earnings_date")
    days = _days_until(ed)
    if ed and days is not None and days < 0:
        # Yahoo keeps showing the last reported date until it publishes the next
        # one, so a negative count means the quarter is already out.
        print(f"  Last reported: {ed}  ({abs(days)} days ago). The next date is "
              f"not published yet - expect roughly three months after this one.")
    elif ed:
        print(f"  Next earnings: {ed}  ({days} days away)")
        print("  Earnings are the single biggest scheduled source of a surprise "
              "gap in the price.")
    else:
        print("  Next earnings date: not published yet")
    if info.get("ex_div_date"):
        print(f"  Ex-dividend date: {info['ex_div_date']}")
    if info.get("eps_avg"):
        print(f"  Analysts expect EPS of {_num(info.get('eps_avg'))} "
              f"(range {_num(info.get('eps_low'))} to {_num(info.get('eps_high'))})")

    eps = _safe(lambda: provider.get_eps_history(symbol), []) or []
    if eps:
        recent = eps[-8:]
        beats = sum(1 for q in recent if q.get("beat"))
        print(f"\n  Track record - last {len(recent)} quarters: "
              f"beat {beats}, missed {len(recent) - beats}")
        for q in recent:
            print(f"    {q.get('label', '?'):<9} expected {_num(q.get('estimate'))} "
                  f"-> delivered {_num(q.get('actual'))}   "
                  f"{'BEAT' if q.get('beat') else 'MISS'} "
                  f"({_num(q.get('surprise_pct'), 1)}%)")


def _quotes_look_broken(snap) -> bool:
    """True when the feed's quotes cannot be describing a real market.

    Stale weekly expirations come back from CBOE with a placeholder bid, a wild
    ask and zero open interest - NVDA once printed a 130% spread with no open
    interest, which is nonsense for the most heavily traded options in the US.
    Reporting that as "Thin" would veto a name on a data glitch, so it has to be
    detected and called what it is: missing data.
    """
    spread = _f(snap.spread_pct)
    no_oi = not snap.open_interest
    # 15% is the app's own "Thin" line (premium_finder._liquidity). A quote that
    # wide AND with no open-interest field at all is a feed that did not answer,
    # not a market that is genuinely illiquid - a real thin market still reports
    # its open interest.
    return no_oi and spread is not None and spread > 15


def _premium_read(symbol: str, provider: DataProvider, dte: int):
    """The volatility and liquidity read, always taken from a monthly expiration.

    Asking for "nearest to 45 days" lands on whatever expiration happens to be
    closest, and that is often a thin weekly. Two runs minutes apart gave NVDA a
    1.02x implied-to-realized ratio and then 1.19x purely because they priced
    different dates - and that ratio is the number deciding whether a name is
    worth selling at all. The monthly (third Friday) expirations carry the real
    open interest, so pin the read to one and it stops moving.
    """
    monthly = _safe(lambda: recommender.monthly_target().dte)
    snap = None
    if monthly:
        snap = _safe(lambda: provider.get_premium_snapshot(symbol, target_dte=monthly))
    if snap is None or getattr(snap, "error", None) or _quotes_look_broken(snap):
        fallback = _safe(lambda: provider.get_premium_snapshot(symbol, target_dte=dte))
        if fallback is not None and not getattr(fallback, "error", None) \
                and not _quotes_look_broken(fallback):
            snap = fallback
        elif snap is None:
            snap = fallback
    return snap


def _options_suitability(symbol: str, provider: DataProvider, dte: int) -> None:
    """Only the facts that decide whether options on this name are worth selling.

    No strikes, no strategy, no position sizing - the app does that. What matters
    here is whether the options market is deep enough to trade and whether the
    premium is priced above what the stock actually moves.
    """
    _head("Is it a good options candidate")
    snap = _premium_read(symbol, provider, dte)

    if snap is None or getattr(snap, "error", None):
        print(f"  No option data: {getattr(snap, 'error', 'none returned')}")
        return

    iv, hv = _f(snap.atm_iv), _f(snap.hv)
    if _quotes_look_broken(snap):
        print(f"  Options liquidity: DATA UNAVAILABLE at the {snap.dte}-day "
              f"expiration.")
        print(f"  The feed returned a {_num(snap.spread_pct, 1)}% bid-ask spread "
              f"with no open interest,\n  which is not a real market. Treat this "
              f"as missing data, NOT as a thin-liquidity\n  verdict. Check the "
              f"live chain in thinkorswim before judging this name.")
    else:
        _row("Options liquidity", snap.liquidity,
             f"bid-ask {_num(snap.spread_pct, 1)}% wide, "
             f"open interest {snap.open_interest}")
    _row("Implied volatility", _pct(iv), "movement option buyers are paying for")
    _row("Realized volatility", _pct(hv), "movement the stock actually delivered")
    if iv and hv:
        ratio = iv / hv
        if ratio >= 1.15:
            read = "GOOD for a seller - you are paid more than it moves"
        elif ratio >= 1.0:
            read = "slight edge to the seller"
        else:
            read = ("BAD for a seller - you are paid LESS than it actually "
                    "moves")
        _row("Implied vs realized", f"{ratio:.2f}x", read)
    _row("Premium richness", snap.richness)
    for flag in snap.flags:
        print(f"  FLAG: {flag}")
    print("\n  (Strike selection, strategy choice and position sizing are the "
          "app's job,\n  not this report's. This section only says whether the "
          "name is worth trading at all.)")


# ---------------------------------------------------------------- driver
def report(symbol: str, provider: DataProvider, dte: int) -> dict:
    """Print the text report and return the same numbers as a dict for --html."""
    symbol = symbol.upper()
    kind = _kind(symbol)
    data: dict = {"symbol": symbol, "kind": kind, "mode_label": provider.mode_label}
    line = "=" * 78
    print(f"\n{line}\n{symbol}  ({kind.upper()})   data: {provider.mode_label}\n{line}")

    price, change = _safe(lambda: provider.get_price_change(symbol), (None, None))
    data["price"], data["change"] = price, change
    print(f"Price: {_num(price)}   Today: "
          f"{'n/a' if change is None else f'{change:+.2f}%'}")
    vix = _f(_safe(yfinance_client.get_vix))
    if vix:
        print(f"VIX (market-wide fear gauge): {vix:.2f}")

    info = _safe(lambda: provider.get_raw_info(symbol), {}) or {}
    analysis = _safe(lambda: provider.get_stock_analysis(symbol))
    data["info"] = info

    _what_it_is(symbol, kind, info, analysis)

    if kind == "stock":
        if analysis:
            _head("Quick scorecard")
            print(f"  Grade: {analysis.grade}   {analysis.summary}")
        _valuation(info)
        _profitability(info)
        _growth(info)
        _balance_sheet(info)
        _dividend(info)
        data["ratings"] = _safe(lambda: provider.get_analyst_ratings(symbol), {}) or {}
        _sentiment(info, data["ratings"])

    closes = _safe(lambda: yfinance_client.get_history_closes(symbol, "1y"), []) or []
    if len(closes) > 200:
        bench = _safe(lambda: yfinance_client.get_history_closes(BENCHMARK, "1y"), []) or []
        long_closes = _safe(
            lambda: yfinance_client.get_history_closes(symbol, "2y"), []) or []
        long_bench = _safe(
            lambda: yfinance_client.get_history_closes(BENCHMARK, "2y"), []) or []
        _technicals(symbol, closes, bench, long_closes, long_bench)

        # The candles need their own OHLC fetch, and the moving averages drawn on
        # them must be computed from the SAME series the candles come from, or the
        # 200-day line sits a few cents off the bars it is supposed to describe.
        # These stay on the 1-year series: the ladder is a 52-week high and low,
        # and the volatility and trend reads are defined over a year.
        bars = _safe(lambda: _ohlcv(symbol), []) or []
        if len(bars) > 200:
            data["ohlc"] = bars
            data["chart_closes"] = [b["c"] for b in bars]

        data["closes"] = closes
        data["hv"] = premium_finder.annualized_vol(closes)
        data["trend"] = trend_from_prices(closes)
        data["levels"] = [("52w low", min(closes)), ("200-day", sma(closes, 200)),
                          ("50-day", sma(closes, 50)), ("52w high", max(closes))]
        stock_r = _returns(long_closes or closes)
        bench_r = ({} if symbol.upper() == BENCHMARK
                   else _returns(long_bench or bench)
                   if len(long_bench or bench) > 252 else {})
        data["rs_rows"] = [(k, stock_r[k], bench_r[k])
                           for k in stock_r if k in bench_r]
    else:
        _head("Technicals")
        print("  Not enough price history returned (feed may be throttled).")

    _volume(symbol)

    tv = _safe(lambda: provider.get_tradingview(symbol, is_index=(kind == "index")), {})
    if tv:
        _head("TradingView technical rating (independent second opinion)")
        for interval, r in tv.items():
            print(f"  {interval:<7} {r.recommendation:<12} "
                  f"buy {r.buy} / neutral {r.neutral} / sell {r.sell}"
                  f"   MAs: {r.moving_avg or 'n/a'}   "
                  f"oscillators: {r.oscillators or 'n/a'}")

    _earnings(symbol, provider, kind)
    _options_suitability(symbol, provider, dte)

    if kind == "stock":
        einfo = _safe(lambda: provider.get_earnings_info(symbol), {}) or {}
        data["earnings_days"] = _days_until(einfo.get("earnings_date"))
        data["eps"] = _safe(lambda: provider.get_eps_history(symbol), []) or []
    snap = _premium_read(symbol, provider, dte)
    data["snap"] = snap
    data["liq_broken"] = bool(snap is not None and not getattr(snap, "error", None)
                              and _quotes_look_broken(snap))
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="+", help="tickers, e.g. AAPL MSFT SPY")
    ap.add_argument("--dte", type=int, default=45,
                    help="days to expiration used only for the liquidity and "
                         "volatility read (default 45)")
    ap.add_argument("--html", metavar="PATH",
                    help="also write a visual HTML report here (one symbol only)")
    args = ap.parse_args()

    provider = DataProvider.create()
    if provider.mode == "demo":
        print("WARNING: running on SAMPLE data. Every number below is fake and "
              "must not be used for a real decision.")

    collected = [report(sym, provider, args.dte) for sym in args.symbols]
    print()

    if args.html:
        from tools import report_html
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report_html.render(collected[0]), encoding="utf-8")
        print(f"Visual report written to {out}")
        if len(collected) > 1:
            print("(--html renders the first symbol only)")


if __name__ == "__main__":
    main()
