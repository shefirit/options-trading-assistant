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
    # "good" | "ok" | "watch" | "unknown".
    #
    # "unknown" means the number never arrived, and it is NOT a caution. It
    # used to be: a missing market cap, P/E or profit margin each scored
    # "watch", identical to a genuinely bad one, so when Yahoo throttled its
    # fundamentals from the hosted app - which it does routinely from cloud
    # IPs - NVDA came out graded F and described as illiquid. An absent
    # measurement is not a finding about the company.
    status: str = "ok"


class StockAnalysis(BaseModel):
    symbol: str
    name: str = ""
    price: Optional[float] = None
    sector: str = ""
    fundamentals: list[Metric] = Field(default_factory=list)
    technicals: list[Metric] = Field(default_factory=list)
    liquid: bool = True
    # Whether volume was actually READ. False means "not checked", which is a
    # different thing from illiquid and must not be reported as one.
    liquidity_checked: bool = True
    # Too few company numbers arrived to grade it. The grade is None and every
    # verdict built on it should say so rather than guess.
    data_partial: bool = False
    # "company" | "fund" | "unknown". is_fund above is the two-way view kept
    # for callers that only care about the fund case; "unknown" is NOT a fund.
    kind: str = "company"
    suitable: bool = True            # decent candidate for selling options?
    # A-F report card - for a COMPANY. None on a fund, which has no company to
    # grade: SPY is 500 of them, so profit margin and revenue growth are not
    # missing data, they are the wrong question. premium_finder already took
    # this line ("ETFs count as solid - they are baskets, not one company") and
    # graded funds as "ETF"; this used to score SPY a D off the blanks.
    grade: Optional[str] = "C"
    is_fund: bool = False
    summary: str = ""


# ---------- small technical helpers ----------
def sma(closes: list[float], n: int) -> Optional[float]:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index - 0 to 100. Over 70 is 'overbought', under 30 'oversold'.

    Delegates to indicators.rsi, which is Wilder's method - the one thinkorswim
    and TradingView use. This function used to average the last 14 gains and
    losses on its own, which is NOT RSI: real RSI seeds from the first 14 bars
    and then smooths forward across the whole series, so every earlier bar still
    counts. The difference is not cosmetic. On AAPL the two read 47.6 and 40.8,
    which straddles the line between "healthy" and "getting oversold".

    Worse, the app had three copies of this - a correct one behind the chart and
    naive ones behind the Overview, LEAPS and Screener tabs - so the same stock
    showed different momentum depending on which tab she opened. One
    implementation now, and it is the right one.
    """
    from src.engine import indicators

    series = indicators.rsi(closes, period)
    value = series[-1] if series else None
    return None if value is None else round(value, 1)


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
        return Metric(label="Company size", value="n/a",
                      read="Company size did not load - this is a missing number, "
                           "not a small company.", status="unknown")
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
        # This used to read "the company may not have steady profits", which
        # turned an absent field into an accusation. A loss-making company is
        # caught by the pe < 0 branch below; a blank one is just blank.
        return Metric(label="Valuation (P/E)", value="n/a",
                      read="Valuation did not load.", status="unknown")
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
        return Metric(label="Profit margin", value="n/a",
                      read="Profit margin did not load.", status="unknown")
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
        return Metric(label="Revenue growth", value="n/a",
                      read="Revenue growth did not load.", status="unknown")
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
        return Metric(label="Trend", value="n/a",
                      read="Not enough price history to read a trend.",
                      status="unknown")
    if price > s50 > s200:
        read, status, val = "Uptrend - price is above both moving averages. Healthy.", "good", "Up ▲"
    elif price < s50 < s200:
        read, status, val = "Downtrend - price is below both averages. Be cautious selling puts.", "watch", "Down ▼"
    else:
        read, status, val = "Sideways / choppy - no clear direction.", "ok", "Sideways →"
    return Metric(label="Trend", value=val, read=read, status=status)


def _rsi_metric(value: Optional[float]) -> Metric:
    if value is None:
        return Metric(label="Momentum (RSI)", value="n/a",
                      read="Not enough price history for a momentum read.",
                      status="unknown")
    if value >= 70:
        read, status = "Overbought - has run up fast and may pull back.", "watch"
    elif value <= 30:
        read, status = "Oversold - has dropped hard and may bounce.", "watch"
    else:
        read, status = "Neutral - not stretched either way.", "good"
    return Metric(label="Momentum (RSI)", value=f"{value:.0f}", read=read, status=status)


def _liquidity_metric(avg_vol: Optional[float]) -> Metric:
    if not avg_vol:
        # NOT a caution. This branch is what told her "NVDA does not trade
        # enough volume for comfortable options trading" - Yahoo had simply
        # not returned the field, which it often does not from a cloud host.
        return Metric(label="Trading volume", value="n/a",
                      read="Trading volume did not load, so liquidity could "
                           "not be checked here.", status="unknown")
    if avg_vol >= 5e6:
        read, status = "Very liquid - easy to get in and out at fair prices.", "good"
    elif avg_vol >= 1e6:
        read, status = "Liquid enough for options.", "good"
    elif avg_vol >= 300e3:
        read, status = "Moderate - spreads may be a bit wide.", "ok"
    else:
        read, status = "Thinly traded - options can be hard to fill. Avoid for now.", "watch"
    return Metric(label="Avg daily volume", value=f"{avg_vol/1e6:.1f}M shares", read=read, status=status)


_FUND_TYPES = {"etf", "mutualfund", "index", "fund"}

# Only a basket has these.
_FUND_FIELDS = ("totalAssets", "fundFamily", "navPrice", "category")

# Only a company has these.
_COMPANY_FIELDS = ("profitMargins", "revenueGrowth", "returnOnEquity", "sector")

# Proof the feed actually answered about a named security. Without one of
# these the response is not an answer, and nothing can be concluded from what
# it does not contain.
_IDENTITY_FIELDS = ("shortName", "longName")


def classify(info: dict[str, Any]) -> str:
    """"fund" | "company" | "unknown" - a basket, a company, or no answer?

    In order:
      1. quoteType settles it outright when the feed sends one.
      2. Fields only a fund has (total assets, fund family) mean fund.
      3. Fields only a company has (margins, sector) mean company.
      4. A NAMED response carrying neither is a fund - the feed answered and
         had no company economics to give. This is how GLD is caught on feeds
         that omit quoteType.
      5. Anything else is unknown.

    Step 5 is the one that was missing, and step 4 is why it mattered. Reading
    "fund" from the ABSENCE of company numbers is only sound when the feed
    actually replied; an empty response has no company economics either. Yahoo
    returns empty from cloud hosts routinely, so a throttled fetch used to make
    the app state, as fact, that NVDA is "a basket of many holdings, not one
    company". Absence of evidence is now evidence of nothing.
    """
    qt = str(info.get("quoteType") or info.get("typeDisp") or "").strip().lower()
    if qt:
        return "fund" if qt in _FUND_TYPES else "company"
    if any(info.get(k) is not None for k in _FUND_FIELDS):
        return "fund"
    if any(info.get(k) is not None for k in _COMPANY_FIELDS):
        return "company"
    if any(info.get(k) is not None for k in _IDENTITY_FIELDS):
        return "fund"
    return "unknown"


def has_economics(info: dict[str, Any]) -> bool:
    """Did this response carry actual NUMBERS about the security?

    Company margins and a sector, or a fund's assets and NAV - either counts.
    A response with neither is a name and nothing else, which on the hosted app
    is what a throttled Yahoo call looks like, and is the signal to go and ask
    Alpha Vantage instead.
    """
    return any(info.get(k) is not None for k in _COMPANY_FIELDS + _FUND_FIELDS)


def _is_fund(info: dict[str, Any]) -> bool:
    """Kept for callers that only care about the fund case. `unknown` is not a
    fund, so this is False for it - see classify() for the three-way answer."""
    return classify(info) == "fund"


def analyze(symbol: str, info: dict[str, Any], closes: list[float],
            avg_volume: Optional[float] = None) -> StockAnalysis:
    price = (info.get("currentPrice") or info.get("regularMarketPrice")
             or (closes[-1] if closes else None))

    kind = classify(info)
    is_fund = kind == "fund"
    # A fund is judged on how it trades, not on company accounts it does not
    # have. Scoring it against blank fundamentals is what made SPY a "D".
    # An UNKNOWN name still gets the fundamentals list, all of it reading
    # "did not load" - that is the honest picture, and dropping the rows would
    # hide the fact that anything was meant to be there.
    fundamentals = [] if is_fund else [
        _market_cap_metric(info.get("marketCap")),
        _pe_metric(info.get("trailingPE")),
        _margin_metric(info.get("profitMargins")),
        _growth_metric(info.get("revenueGrowth")),
    ]

    s50, s200 = sma(closes, 50), sma(closes, 200)
    # Prefer Yahoo's info field, but fall back to volume from price history
    # (avg_volume) - on the hosted app the info field is often missing even for
    # very liquid names, which used to wrongly flag every stock as illiquid.
    avg_vol = (info.get("averageVolume") or info.get("averageDailyVolume10Day")
               or avg_volume)
    technicals = [
        _trend_metric(price or 0, s50, s200),
        _rsi_metric(rsi(closes)),
        _liquidity_metric(avg_vol),
    ]

    vol_metric = next((m for m in technicals
                       if m.label in ("Avg daily volume", "Trading volume")), None)
    # Three states, not two: liquid, illiquid, and never checked. They used to
    # collapse into "not liquid", which is how the most heavily traded stock in
    # the market got told it does not trade enough volume.
    liquidity_checked = vol_metric is not None and vol_metric.status != "unknown"
    liquid = liquidity_checked and vol_metric.status in ("good", "ok")

    all_metrics = fundamentals + technicals
    known = [m for m in all_metrics if m.status != "unknown"]
    watches = sum(1 for m in known if m.status == "watch")
    goods = sum(1 for m in known if m.status == "good")
    suitable = liquid and watches <= 1 and (is_fund or goods >= 3)

    # Report-card grade: 2 points per green, 1 per neutral, 0 per caution -
    # scored over the metrics that actually ARRIVED. Grading a name on blanks
    # is what turned a throttled fundamentals fetch into an F.
    #
    # Under half the company numbers and there is no grade at all. A letter off
    # one metric is a guess wearing a report card, and she reads these to
    # decide what to sell options on.
    grade = None
    graded_on = len([m for m in fundamentals if m.status != "unknown"])
    enough = graded_on >= 2
    if not is_fund and known and enough:
        score = ((2 * goods + sum(1 for m in known if m.status == "ok"))
                 / (2 * len(known)))
        grade = "A" if score >= 0.85 else "B" if score >= 0.70 else \
                "C" if score >= 0.55 else "D" if score >= 0.40 else "F"

    partial = not is_fund and not enough

    if kind == "unknown":
        # Nothing at all came back - not even the fields every security has.
        # This used to be classified a FUND, on the reasoning that a name with
        # no company economics must be a basket, and then said so about
        # whatever she had typed in.
        summary = (f"No company details loaded for {symbol} at all, so there is "
                   "nothing here to grade and no way to say what kind of "
                   "security it is. The price and trend below come from price "
                   "history and are still real. Try again in a few minutes.")
    elif partial:
        summary = (f"Not enough of {symbol}'s company numbers loaded to grade it. "
                   "This is a data problem, not a verdict on the company - the "
                   "price and trend below are still real. Try again in a few "
                   "minutes, or look it up on your broker.")
    elif not liquidity_checked:
        summary = (f"{symbol}'s trading volume did not load, so this cannot say "
                   "whether it trades enough for comfortable options trading. "
                   "Everything else below is real. Check the volume on your "
                   "broker before selling options on it.")
    elif is_fund and liquid:
        summary = (f"{symbol} is a fund - a basket of many holdings, not one company - so "
                   "there is no company quality to grade. What matters for selling options "
                   "on it is that it trades heavily and moves steadily, and it does.")
    elif suitable:
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
        liquidity_checked=liquidity_checked,
        data_partial=partial,
        kind=kind,
        suitable=suitable,
        grade=grade,
        is_fund=is_fund,
        summary=summary,
    )
