"""Real market data from Yahoo Finance (free, no account, no keys).

This gives the app REAL prices, REAL option chains, REAL volatility, and the
history needed to read the trend - about 15 minutes delayed, which is fine for
the 21-45 day trades you place. When your Schwab account is connected later, the
app upgrades to true real-time automatically.

Yahoo gives us each option's price and implied volatility but not its delta, so
we compute the greeks ourselves (see greeks.py).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Optional

from src.data import greeks

# Yahoo prints noisy 404s to the console for symbols that lack some data (e.g. an
# ETF has no "fundamentals"). We handle those cases ourselves, so quiet the logger.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType

# Yahoo tickers for the index names (options live under the "^" symbols).
_SYMBOL_MAP = {
    "SPX": "^SPX", "NDX": "^NDX", "RUT": "^RUT", "XSP": "^XSP",
    "VIX": "^VIX", "DJX": "^DJI",
}


def yahoo_symbol(underlying: str) -> str:
    return _SYMBOL_MAP.get(underlying.upper(), underlying.upper())


def _f(value: Any) -> float:
    """Float that treats missing / NaN as 0 (Yahoo leaves blanks as NaN)."""
    try:
        f = float(value)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    return int(_f(value))


def _ticker(underlying: str):
    import yfinance as yf
    return yf.Ticker(yahoo_symbol(underlying))


def _fast_last_price(t) -> Optional[float]:
    """Robust last-price read (fast_info's .get is unreliable across versions)."""
    try:
        return float(t.fast_info["last_price"])
    except Exception:
        try:
            hist = t.history(period="1d")
            return float(hist["Close"].iloc[-1]) if len(hist) else None
        except Exception:
            return None


def is_available(timeout: float = 8.0) -> bool:
    """True if yfinance is installed and Yahoo is reachable right now.

    Runs with a hard timeout so a slow/blocked network (common on cloud hosts)
    can never hang the app's startup - it just falls back to demo data instead.
    """
    import concurrent.futures

    def _check() -> bool:
        try:
            import yfinance as yf  # noqa: F401
            return _fast_last_price(_ticker("SPY")) is not None
        except Exception:
            return False

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_check).result(timeout=timeout)
    except Exception:
        return False


def get_price(underlying: str) -> Optional[float]:
    try:
        return _fast_last_price(_ticker(underlying))
    except Exception:
        return None


def get_price_change(underlying: str) -> tuple[Optional[float], Optional[float]]:
    """(last price, % change vs yesterday's close) - for the market tiles."""
    try:
        t = _ticker(underlying)
        fi = t.fast_info
        last = float(fi["last_price"])
        prev = float(fi["previous_close"])
        pct = (last - prev) / prev * 100 if prev else None
        return last, pct
    except Exception:
        try:
            hist = _ticker(underlying).history(period="2d")
            closes = [float(x) for x in hist["Close"].tolist()]
            if len(closes) >= 2:
                return closes[-1], (closes[-1] - closes[-2]) / closes[-2] * 100
            if closes:
                return closes[-1], None
        except Exception:
            pass
        return None, None


def get_vix() -> Optional[float]:
    return get_price("VIX")


def get_history_closes(underlying: str, period: str = "1y") -> list[float]:
    """Daily closing prices, oldest first - used to read the trend."""
    try:
        hist = _ticker(underlying).history(period=period)
        return [float(x) for x in hist["Close"].tolist()]
    except Exception:
        return []


def _contracts_from_expiration(t, exp: str, dte: int, spot: float) -> list[OptionContract]:
    """Parse one expiration's calls+puts into OptionContracts (greeks computed)."""
    oc = t.option_chain(exp)
    out: list[OptionContract] = []
    for frame, opt_type, is_call in (
        (oc.calls, OptionType.CALL, True),
        (oc.puts, OptionType.PUT, False),
    ):
        for _, r in frame.iterrows():
            iv = _f(r.get("impliedVolatility"))
            strike = _f(r.get("strike"))
            g = greeks.compute(spot, strike, dte, iv, is_call)
            out.append(OptionContract(
                option_type=opt_type, strike=strike, expiration=exp, dte=dte,
                delta=g["delta"], gamma=g["gamma"], theta=g["theta"], vega=g["vega"],
                iv=iv, bid=_f(r.get("bid")), ask=_f(r.get("ask")),
                volume=_i(r.get("volume")), open_interest=_i(r.get("openInterest")),
            ))
    return out


def _expirations_with_dte(t) -> list[tuple[str, int]]:
    today = date.today()
    out = []
    for exp in (t.options or []):
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append((exp, (exp_date - today).days))
    return out


def get_option_chain(underlying: str, from_dte: int = 15, to_dte: int = 70) -> OptionChain:
    """Fetch a real option chain and normalize it, computing greeks from IV.

    Only pulls expirations inside the DTE window, so it stays fast.
    """
    t = _ticker(underlying)
    spot = get_price(underlying) or 0.0
    contracts: list[OptionContract] = []
    for exp, dte in _expirations_with_dte(t):
        if from_dte <= dte <= to_dte:
            contracts.extend(_contracts_from_expiration(t, exp, dte, spot))
    return OptionChain(underlying=underlying, underlying_price=spot,
                       fetched_at=date.today().isoformat(), contracts=contracts)


def get_expiration_chain(underlying: str, target_dte: int = 30) -> OptionChain:
    """Just the single expiration nearest to target_dte - fast (2 API calls).

    Used by the premium finder, which scans many symbols and only needs one
    monthly expiration per name.
    """
    t = _ticker(underlying)
    spot = get_price(underlying) or 0.0
    exps = _expirations_with_dte(t)
    valid = [(e, d) for e, d in exps if d >= 1]
    if not valid:
        return OptionChain(underlying=underlying, underlying_price=spot, contracts=[])
    exp, dte = min(valid, key=lambda ed: abs(ed[1] - target_dte))
    return OptionChain(underlying=underlying, underlying_price=spot,
                       fetched_at=date.today().isoformat(),
                       contracts=_contracts_from_expiration(t, exp, dte, spot))


def historical_volatility(underlying: str, lookback_days: int = 30) -> Optional[float]:
    """Annualized realized volatility from recent daily closes (e.g. 0.28 = 28%).

    This is how much the stock ACTUALLY moved - compared against implied
    volatility, it tells you whether option premiums are rich or cheap.
    """
    closes = get_history_closes(underlying, period="3mo")
    if len(closes) < lookback_days + 1:
        return None
    window = closes[-(lookback_days + 1):]
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))
            if window[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252), 4)


def get_calendar_dates(underlying: str):
    """Return (next_earnings_date, ex_dividend_date) or (None, None)."""
    import datetime as _dt
    try:
        cal = _ticker(underlying).calendar or {}
    except Exception:
        return None, None

    def _as_date(v):
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, _dt.datetime):
            return v.date()
        return v if isinstance(v, _dt.date) else None

    return _as_date(cal.get("Earnings Date")), _as_date(cal.get("Ex-Dividend Date"))


def get_analyst_ratings(underlying: str) -> dict[str, int]:
    """Wall Street analyst counts: how many say buy / hold / sell right now."""
    try:
        rec = _ticker(underlying).recommendations
        if rec is None or len(rec) == 0:
            return {}
        row = rec.iloc[0]   # "0m" = the current month
        return {
            "strong_buy": int(row.get("strongBuy", 0)),
            "buy": int(row.get("buy", 0)),
            "hold": int(row.get("hold", 0)),
            "sell": int(row.get("sell", 0)),
            "strong_sell": int(row.get("strongSell", 0)),
        }
    except Exception:
        return {}


def get_eps_history(underlying: str, max_quarters: int = 16) -> list[dict[str, Any]]:
    """Past quarters: what analysts expected vs what the company delivered.

    Each item: {label, date, estimate, actual, surprise_pct, beat}. Oldest first.

    Tries the earnings-calendar endpoint first (up to ~4 years of history). If that
    is empty - which happens on datacenter IPs (Streamlit Cloud) where Yahoo blocks
    that specific endpoint - it falls back to the earnings-history endpoint, which
    rides the same quoteSummary API as the analyst ratings (so it keeps working
    where the calendar one is blocked), giving the last ~4 quarters.
    """
    t = _ticker(underlying)
    for source in (_eps_from_calendar, _eps_from_history, _eps_from_income):
        out = source(t, max_quarters)
        if out:
            return out
    return []


def _eps_from_calendar(t, max_quarters: int) -> list[dict[str, Any]]:
    try:
        ed = t.get_earnings_dates(limit=28)   # reaches back ~4+ years
        if ed is None or len(ed) == 0:
            return []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for when, row in ed.iterrows():
        actual = row.get("Reported EPS")
        if actual is None or (isinstance(actual, float) and math.isnan(actual)):
            continue   # future quarter - nothing reported yet
        est_f = _f(row.get("EPS Estimate"))
        actual_f = _f(actual)
        surprise_raw = row.get("Surprise(%)")
        has_surprise = surprise_raw is not None and not (
            isinstance(surprise_raw, float) and math.isnan(surprise_raw))
        beat = (_f(surprise_raw) >= 0) if has_surprise else (actual_f >= est_f)
        out.append({
            "label": f"{when.year} Q{(when.month - 1) // 3 + 1}",
            "date": when.date().isoformat(),
            "estimate": est_f, "actual": actual_f,
            "surprise_pct": _f(surprise_raw), "beat": beat,
        })
    out = out[:max_quarters]
    out.reverse()   # oldest first for charting
    return out


def _eps_from_history(t, max_quarters: int) -> list[dict[str, Any]]:
    """Fallback: the quoteSummary earnings-history endpoint (last ~4 quarters).
    Columns: epsActual, epsEstimate, surprisePercent (a fraction, e.g. 0.045)."""
    try:
        eh = t.get_earnings_history()
    except Exception:
        eh = None
    if eh is None or len(eh) == 0:
        return []
    out: list[dict[str, Any]] = []
    for when, row in eh.iterrows():   # index is the quarter date, oldest first
        actual = row.get("epsActual")
        if actual is None or (isinstance(actual, float) and math.isnan(actual)):
            continue
        est_f = _f(row.get("epsEstimate"))
        actual_f = _f(actual)
        sp = row.get("surprisePercent")
        has_sp = sp is not None and not (isinstance(sp, float) and math.isnan(sp))
        surprise_pct = _f(sp) * 100 if has_sp else 0.0   # fraction -> percent
        beat = (surprise_pct >= 0) if has_sp else (actual_f >= est_f)
        try:
            qd = when.date() if hasattr(when, "date") else when
            label = f"{qd.year} Q{(qd.month - 1) // 3 + 1}"
            date_iso = qd.isoformat()
        except Exception:
            label, date_iso = str(when), None
        out.append({
            "label": label, "date": date_iso,
            "estimate": est_f, "actual": actual_f,
            "surprise_pct": surprise_pct, "beat": beat,
        })
    return out[-max_quarters:]


def _eps_from_income(t, max_quarters: int) -> list[dict[str, Any]]:
    """Last-resort fallback: delivered EPS from the quarterly income statement
    (a different Yahoo endpoint again). No analyst estimate here, so beat/miss is
    unknown (beat=None) - the chart shows these as neutral 'delivered' points."""
    try:
        qi = t.quarterly_income_stmt
    except Exception:
        return []
    if qi is None or len(qi) == 0:
        return []
    row = None
    for name in ("Diluted EPS", "Basic EPS"):
        if name in qi.index:
            row = qi.loc[name]
            break
    if row is None:
        return []
    out: list[dict[str, Any]] = []
    for when, val in row.items():
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        try:
            qd = when.date() if hasattr(when, "date") else when
            label = f"{qd.year} Q{(qd.month - 1) // 3 + 1}"
            date_iso = qd.isoformat()
        except Exception:
            label, date_iso = str(when), None
        out.append({
            "label": label, "date": date_iso,
            "estimate": None, "actual": _f(val),
            "surprise_pct": None, "beat": None,
        })
    out.sort(key=lambda d: d["date"] or "")   # oldest first
    return out[-max_quarters:]


def get_price_frame(underlying: str, period: str = "1y"):
    """Daily closes as a DataFrame (for the price chart). None on failure."""
    try:
        hist = _ticker(underlying).history(period=period)
        if hist is None or len(hist) == 0:
            return None
        df = hist[["Close"]].copy()
        df.index = df.index.tz_localize(None)
        return df
    except Exception:
        return None


def get_earnings_info(underlying: str) -> dict[str, Any]:
    """Next earnings/ex-div dates plus analyst EPS & revenue estimates."""
    import datetime as _dt
    try:
        cal = _ticker(underlying).calendar or {}
    except Exception:
        return {}

    def _as_date(v):
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, _dt.datetime):
            return v.date()
        return v if isinstance(v, _dt.date) else None

    return {
        "earnings_date": _as_date(cal.get("Earnings Date")),
        "ex_div_date": _as_date(cal.get("Ex-Dividend Date")),
        "eps_avg": cal.get("Earnings Average"),
        "eps_low": cal.get("Earnings Low"),
        "eps_high": cal.get("Earnings High"),
        "rev_avg": cal.get("Revenue Average"),
    }


def get_fundamentals(underlying: str) -> dict[str, Any]:
    """Raw fundamentals + a few technicals for the stock-analysis panel."""
    try:
        t = _ticker(underlying)
        info = t.info or {}
    except Exception:
        info = {}
    return info
