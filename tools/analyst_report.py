"""One-shot fundamental + technical + options report for any stock, ETF or index.

This is the data feed for the `stock-analyst` Claude agent. It pulls every
number the agent needs in a single run so the agent can spend its effort on
explaining rather than fetching.

    python tools/analyst_report.py AAPL
    python tools/analyst_report.py SPY QQQ IWM --dte 45

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

# Cash-settled European-style indexes. These are the ONLY names the SOP allows
# for credit spreads and iron condors (no early assignment risk).
INDEXES = {"SPX", "NDX", "RUT", "XSP", "^SPX", "^NDX", "^RUT", "VIX", "^VIX"}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:                       # noqa: BLE001
        return default if default is not None else f"unavailable ({exc.__class__.__name__})"


def _pct(v, digits=1):
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def _num(v, digits=2):
    return "n/a" if v is None else f"{v:,.{digits}f}"


def _kind(symbol: str) -> str:
    if symbol.upper().lstrip("^") in {s.lstrip("^") for s in INDEXES}:
        return "index"
    return "stock" if stock_universe.is_stock(symbol) else "etf"


def _sop_eligibility(kind: str) -> list[str]:
    """Which of the 8 SOP strategies this instrument is allowed to be used with."""
    if kind == "index":
        return [
            "ALLOWED: Put Credit Spread, Call Credit Spread, Iron Condor "
            "(cash-settled, no early assignment - this is what the SOP requires)",
            "Spread width guide: $25-50",
            "European-style, so entry at 21 DTE is acceptable",
        ]
    width = "$25-50" if kind == "etf" else "$5-10"
    return [
        "ALLOWED: Cash Secured Put, Covered Call (Models 1/2/3), PMCC",
        "NOT ALLOWED by the SOP: credit spreads and iron condors - these are "
        "American-style, so the short leg can be assigned early",
        f"Spread width guide if used inside a covered-call model: {width}",
        "American-style, so enter at 30-49 DTE to stay clear of the 21 DTE "
        "early-assignment zone",
    ]


def _days_until(d) -> int | None:
    if not isinstance(d, dt.date):
        return None
    return (d - dt.date.today()).days


def _best_chain(provider: DataProvider, symbol: str, dte: int):
    """The expiration nearest `dte` that actually has usable data.

    The provider's own nearest-expiration pick can land on a thin weekly where
    the CBOE feed returns a narrow strike band and all deltas are zero, which
    makes the premium read come back empty. So we look across a +/-15 day window
    and keep only expirations with real deltas and a wide enough strike ladder,
    then take the one closest to the target.
    """
    chain = _safe(lambda: provider.get_chain(symbol, max(dte - 15, 1), dte + 15), None)
    if not chain or not getattr(chain, "contracts", None):
        return None

    by_exp: dict = {}
    for c in chain.contracts:
        by_exp.setdefault(c.expiration, []).append(c)

    usable = []
    for exp, contracts in by_exp.items():
        puts = [c for c in contracts if c.option_type.value == "put"]
        if len(puts) < 20:
            continue
        if not any(c.abs_delta > 0 for c in puts):
            continue
        price = chain.underlying_price or 0
        # needs strikes far enough below spot to hold a 0.30 delta short put
        if price and min(c.strike for c in puts) > price * 0.93:
            continue
        usable.append((abs(contracts[0].dte - dte), exp, contracts))

    if not usable:
        return None
    _, _, best = min(usable, key=lambda t: t[0])
    return type(chain)(underlying=chain.underlying,
                       underlying_price=chain.underlying_price,
                       fetched_at=chain.fetched_at, contracts=best)


def _premium_picture(provider: DataProvider, symbol: str, dte: int):
    """premium_finder.snapshot() over a chain we know is populated."""
    chain = _best_chain(provider, symbol, dte)
    if chain is None:
        return None
    closes = _safe(lambda: yfinance_client.get_history_closes(symbol, period="6mo"), [])
    hv = premium_finder.annualized_vol(closes) if isinstance(closes, list) and closes else None
    trend = trend_from_prices(closes) if isinstance(closes, list) and closes else "unknown"
    earnings, _ = _safe(lambda: yfinance_client.get_calendar_dates(symbol), (None, None))
    return _safe(lambda: premium_finder.snapshot(
        symbol, chain, hv, trend=trend, monthly_bp=50_000,
        earnings_date=earnings if isinstance(earnings, dt.date) else None), None)


def report(symbol: str, provider: DataProvider, dte: int) -> None:
    symbol = symbol.upper()
    kind = _kind(symbol)
    line = "=" * 68
    print(f"\n{line}\n{symbol}  ({kind.upper()})   data: {provider.mode_label}\n{line}")

    price, change = _safe(lambda: provider.get_price_change(symbol), (None, None))
    print(f"Price: {_num(price)}   Today: "
          f"{'n/a' if change is None else f'{change:+.2f}%'}")

    vix = _safe(yfinance_client.get_vix)
    if isinstance(vix, float):
        zone = "inside the SOP comfort zone 13-25" if 13 <= vix <= 25 else \
               "OUTSIDE the SOP comfort zone 13-25"
        print(f"VIX: {vix:.2f}  ({zone})")

    print("\n-- SOP eligibility ---------------------------------------------")
    for rule in _sop_eligibility(kind):
        print(f"  {rule}")

    # ---------- fundamentals + technicals scorecard ----------
    analysis = _safe(lambda: provider.get_stock_analysis(symbol), None)
    if analysis and not isinstance(analysis, str):
        print(f"\n-- Scorecard ---------------------------------------------------")
        print(f"  {analysis.name}  |  sector: {analysis.sector or 'n/a'}")
        if kind == "stock":
            print(f"  Grade: {analysis.grade}   liquid: {analysis.liquid}   "
                  f"suitable for selling options: {analysis.suitable}")
            print(f"  Summary: {analysis.summary}")
            print("\n  FUNDAMENTALS")
            for m in analysis.fundamentals:
                print(f"    [{m.status:>5}] {m.label}: {m.value}\n            {m.read}")
        else:
            # An index or ETF holds many companies, so there is no single P/E,
            # profit margin or revenue line. The letter grade is built from those,
            # so showing it here would score the fund on data that cannot exist.
            print(f"  Liquid: {analysis.liquid}")
            print("  No company fundamentals: this is a basket of many companies, "
                  "so P/E, profit margin and revenue growth do not apply. "
                  "Judge it on trend, liquidity and volatility instead.")
        print("\n  TECHNICALS")
        for m in analysis.technicals:
            print(f"    [{m.status:>5}] {m.label}: {m.value}\n            {m.read}")
    else:
        print("\n-- Scorecard: unavailable (ETFs and indexes have no company "
              "fundamentals, or the feed was throttled) ----------------------")

    # ---------- price trend ----------
    closes = _safe(lambda: yfinance_client.get_history_closes(symbol, period="1y"), [])
    if isinstance(closes, list) and len(closes) > 200:
        sma50 = sum(closes[-50:]) / 50
        sma200 = sum(closes[-200:]) / 200
        last = closes[-1]
        print("\n-- Trend -------------------------------------------------------")
        print(f"  Trend read: {trend_from_prices(closes)}")
        print(f"  Price {last:,.2f} vs 50-day average {sma50:,.2f} "
              f"({'above' if last > sma50 else 'below'})")
        print(f"  Price {last:,.2f} vs 200-day average {sma200:,.2f} "
              f"({'above' if last > sma200 else 'below'})")
        hi, lo = max(closes), min(closes)
        print(f"  52-week range {lo:,.2f} to {hi:,.2f}  "
              f"(now {(last - lo) / (hi - lo) * 100:.0f}% of the way up that range)")
        hv = premium_finder.annualized_vol(closes)
        print(f"  Realized volatility (how much it actually moved): {_pct(hv)}")

    # ---------- TradingView second opinion ----------
    tv = _safe(lambda: provider.get_tradingview(symbol, is_index=(kind == "index")), {})
    if isinstance(tv, dict) and tv:
        print("\n-- TradingView technical rating (second opinion) ----------------")
        for interval, r in tv.items():
            print(f"  {interval:<7} {r.recommendation:<12} "
                  f"buy {r.buy} / neutral {r.neutral} / sell {r.sell}"
                  f"   MAs: {r.moving_avg or 'n/a'}   oscillators: {r.oscillators or 'n/a'}")

    # ---------- analyst ratings ----------
    ratings = _safe(lambda: provider.get_analyst_ratings(symbol), {})
    if isinstance(ratings, dict) and ratings:
        total = sum(ratings.values()) or 1
        bullish = ratings.get("strong_buy", 0) + ratings.get("buy", 0)
        print("\n-- Wall Street analysts ----------------------------------------")
        print(f"  strong buy {ratings.get('strong_buy', 0)} | buy {ratings.get('buy', 0)} "
              f"| hold {ratings.get('hold', 0)} | sell {ratings.get('sell', 0)} "
              f"| strong sell {ratings.get('strong_sell', 0)}")
        print(f"  {bullish / total * 100:.0f}% of analysts say buy")

    # ---------- earnings ----------
    print("\n-- Earnings ----------------------------------------------------")
    info = _safe(lambda: provider.get_earnings_info(symbol), {})
    if isinstance(info, dict) and info:
        ed = info.get("earnings_date")
        days = _days_until(ed)
        if ed:
            inside = days is not None and 0 <= days <= dte
            print(f"  Next earnings: {ed}  ({days} days away)")
            print(f"  Falls inside a {dte}-day trade window: "
                  f"{'YES - the SOP says do not hold through it' if inside else 'no'}")
        else:
            print("  Next earnings date: not published yet")
        if info.get("ex_div_date"):
            print(f"  Ex-dividend date: {info['ex_div_date']}  "
                  f"(assignment risk on short calls rises just before this)")
        if info.get("eps_avg"):
            print(f"  Analysts expect EPS of {_num(info.get('eps_avg'))} "
                  f"(range {_num(info.get('eps_low'))} to {_num(info.get('eps_high'))})")
    else:
        print("  No earnings calendar for this symbol (normal for ETFs and indexes).")

    eps = _safe(lambda: provider.get_eps_history(symbol), [])
    if isinstance(eps, list) and eps:
        recent = eps[-8:]
        beats = sum(1 for q in recent if q.get("beat"))
        print(f"\n  Track record - last {len(recent)} quarters: "
              f"beat expectations {beats} times, missed {len(recent) - beats}")
        for q in recent:
            mark = "BEAT" if q.get("beat") else "MISS"
            print(f"    {q.get('label', '?'):<9} expected {_num(q.get('estimate'))} "
                  f"-> delivered {_num(q.get('actual'))}   {mark} "
                  f"({_num(q.get('surprise_pct'), 1)}%)")

    # ---------- options premium picture ----------
    print(f"\n-- Options picture at ~{dte} DTE --------------------------------")
    snap = _premium_picture(provider, symbol, dte)
    if snap and not isinstance(snap, str) and not snap.error:
        print(f"  Expiration used: {_safe(lambda: snap.dte, '?')} days out")
        print(f"  Verdict: {snap.verdict.upper()} - {snap.verdict_reason}")
        print(f"  Suggested action: {snap.action}  ({snap.strategy})")
        print(f"  Put to sell:  strike {_num(snap.short_strike)}  "
              f"delta {_num(snap.short_delta)}  credit ${_num(snap.credit_dollars, 0)}")
        print(f"  Call to sell: strike {_num(snap.call_strike)}  "
              f"credit ${_num(snap.call_credit_dollars, 0)}")
        print(f"  Estimated probability of profit: {_num(snap.pop, 0)}%   "
              f"breakeven {_num(snap.breakeven)}   cushion {_num(snap.cushion_pct, 1)}%")
        print(f"  Implied volatility {_pct(snap.atm_iv)} vs realized {_pct(snap.hv)} "
              f"-> premium is {snap.richness}")
        print(f"  Liquidity: {snap.liquidity}  (bid-ask spread "
              f"{_num(snap.spread_pct, 1)}% of price, open interest {snap.open_interest})")
        print(f"  Capital tied up: ${_num(snap.capital_at_risk, 0)}   "
              f"monthly yield {_num(snap.monthly_yield_pct, 2)}%")
        if kind == "index" and any(
                k in (snap.strategy or "").lower()
                for k in ("covered call", "cash secured", "pmcc", "poor man")):
            print("  IGNORE the suggested strategy above: it assumes you can own "
                  "100 shares, and an index cannot be owned as shares. For an "
                  "index the SOP uses Put Credit Spread, Call Credit Spread or "
                  "Iron Condor. The strike, delta and credit numbers are still "
                  "valid as a read on how rich the premium is.")
        if snap.risk_note:
            print(f"  Risk: {snap.risk_note}")
        for flag in snap.flags:
            print(f"  FLAG: {flag}")
    else:
        err = getattr(snap, "error", None) or "no option data returned"
        print(f"  Not available: {err}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="+", help="tickers, e.g. AAPL SPY SPX")
    ap.add_argument("--dte", type=int, default=45,
                    help="days to expiration to price the options picture at (default 45)")
    args = ap.parse_args()

    # Warm the connection first. DataProvider.create() probes Yahoo with a hard
    # 8s timeout, and the very first TLS handshake of a cold run can exceed that,
    # which silently drops the whole report into demo mode. One untimed call
    # opens the connection so the probe that follows answers instantly.
    _safe(lambda: yfinance_client.get_price("SPY"))

    provider = DataProvider.create()
    if provider.mode == "demo":
        print("NOTE: could not reach live market data - retrying once...")
        _safe(lambda: yfinance_client.get_price("SPY"))
        provider = DataProvider.create()
    if provider.mode == "demo":
        print("WARNING: running on SAMPLE data. Every number below is fake and "
              "must not be used for a real trade decision.")

    for sym in args.symbols:
        report(sym, provider, args.dte)
    print()


if __name__ == "__main__":
    main()
