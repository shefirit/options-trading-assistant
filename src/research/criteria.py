"""Instant Analyzer - your own rules, applied to any stock, pass or fail.

You decide what a good company looks like ("margin over 15%, growing more than
8%, debt under 1x"), and every stock gets graded against exactly that. No
black box: each rule shows the number it found, the bar it had to clear, and
how far off it was when it missed.

The near-miss part matters. A stock that fails one rule by a hair is a very
different animal from one that fails three by a mile, and a plain red X hides
that completely.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# Every field the rules can test, with how to read it out of Yahoo's `info`
# and how to display it. `scale` converts the raw value into the unit shown.
FIELDS: dict[str, dict[str, Any]] = {
    "market_cap_b":   {"label": "Market cap", "key": "marketCap", "unit": "$B",
                       "scale": 1e-9, "decimals": 1,
                       "help": "How big the company is. Bigger is steadier."},
    "pe":             {"label": "P/E ratio", "key": "trailingPE", "unit": "x",
                       "scale": 1.0, "decimals": 1,
                       "help": "Price divided by earnings. Lower is cheaper."},
    "forward_pe":     {"label": "Forward P/E", "key": "forwardPE", "unit": "x",
                       "scale": 1.0, "decimals": 1,
                       "help": "Same idea using next year's expected earnings."},
    "peg":            {"label": "PEG ratio", "key": "pegRatio", "unit": "x",
                       "scale": 1.0, "decimals": 2,
                       "help": "P/E measured against growth. Under 1 is the classic bargain."},
    "price_to_sales": {"label": "Price to sales", "key": "priceToSalesTrailing12Months",
                       "unit": "x", "scale": 1.0, "decimals": 1,
                       "help": "Useful when a company has little or no profit yet."},
    "profit_margin":  {"label": "Profit margin", "key": "profitMargins", "unit": "%",
                       "scale": 100.0, "decimals": 1,
                       "help": "What it keeps from every dollar of sales."},
    "operating_margin": {"label": "Operating margin", "key": "operatingMargins", "unit": "%",
                         "scale": 100.0, "decimals": 1,
                         "help": "Profit from the core business, before financing and tax."},
    "revenue_growth": {"label": "Revenue growth", "key": "revenueGrowth", "unit": "%",
                       "scale": 100.0, "decimals": 1,
                       "help": "Sales growth over the past year."},
    "earnings_growth": {"label": "Earnings growth", "key": "earningsGrowth", "unit": "%",
                        "scale": 100.0, "decimals": 1,
                        "help": "Profit growth over the past year."},
    "roe":            {"label": "Return on equity", "key": "returnOnEquity", "unit": "%",
                       "scale": 100.0, "decimals": 1,
                       "help": "How much profit it squeezes from shareholder money."},
    "debt_to_equity": {"label": "Debt to equity", "key": "debtToEquity", "unit": "x",
                       "scale": 0.01, "decimals": 2,
                       "help": "Borrowings against shareholder money. Under 1 is comfortable."},
    "current_ratio":  {"label": "Current ratio", "key": "currentRatio", "unit": "x",
                       "scale": 1.0, "decimals": 2,
                       "help": "Can it cover its short-term bills? Over 1 means yes."},
    "dividend_yield": {"label": "Dividend yield", "key": "dividendYield", "unit": "%",
                       "scale": 1.0, "decimals": 2,
                       "help": "Annual dividend as a percent of the share price."},
    "beta":           {"label": "Beta", "key": "beta", "unit": "x", "scale": 1.0,
                       "decimals": 2,
                       "help": "How violently it moves against the market. 1 = in line."},
    "avg_volume_m":   {"label": "Avg daily volume", "key": "averageVolume", "unit": "M",
                       "scale": 1e-6, "decimals": 1,
                       "help": "Shares traded a day. Liquidity for options."},
    # Computed from price history rather than read from `info`.
    "pct_off_high":   {"label": "Below 52-week high", "key": None, "unit": "%",
                       "scale": 1.0, "decimals": 1,
                       "help": "How far it has pulled back from its high."},
    "above_200dma":   {"label": "Above 200-day average", "key": None, "unit": "",
                       "scale": 1.0, "decimals": 0,
                       "help": "1 if the long-term trend is up, 0 if not."},
    "rsi":            {"label": "RSI", "key": None, "unit": "", "scale": 1.0, "decimals": 0,
                       "help": "Momentum, 0-100. Over 70 is overbought."},
    "year_return":    {"label": "1-year return", "key": None, "unit": "%", "scale": 1.0,
                       "decimals": 1, "help": "How it has done over the past year."},
}

OPS = {
    ">=": "at least",
    "<=": "at most",
    ">": "more than",
    "<": "less than",
}


class Criterion(BaseModel):
    field: str
    op: str = ">="
    value: float = 0.0
    enabled: bool = True

    @property
    def spec(self) -> dict[str, Any]:
        return FIELDS.get(self.field, {"label": self.field, "unit": "",
                                       "scale": 1.0, "decimals": 2, "help": ""})


class RuleResult(BaseModel):
    field: str
    label: str
    op: str
    threshold: float
    unit: str
    actual: Optional[float] = None
    passed: bool = False
    measured: bool = True
    miss_pct: Optional[float] = None      # how far off, as % of the threshold
    near_miss: bool = False
    read: str = ""


class AnalyzerResult(BaseModel):
    symbol: str
    name: str = ""
    price: Optional[float] = None
    rules: list[RuleResult] = Field(default_factory=list)
    passed_count: int = 0
    measured_count: int = 0
    near_misses: int = 0
    verdict: str = "fail"                 # "pass" | "near" | "fail"
    score: float = 0.0                    # percent of measurable rules passed
    summary: str = ""


# ---------- presets: sensible starting points she can edit ----------
PRESETS: dict[str, dict[str, Any]] = {
    "Quality compounder": {
        "note": "Big, profitable businesses that keep growing - the kind you can hold "
                "for years without checking daily.",
        "rules": [("market_cap_b", ">=", 10), ("profit_margin", ">=", 10),
                  ("revenue_growth", ">=", 5), ("roe", ">=", 12),
                  ("debt_to_equity", "<=", 1.5)],
    },
    "Reasonably priced": {
        "note": "Decent companies that are not priced for perfection.",
        "rules": [("market_cap_b", ">=", 5), ("pe", "<=", 25), ("peg", "<=", 2.0),
                  ("profit_margin", ">=", 5), ("revenue_growth", ">=", 0)],
    },
    "Good to sell options on": {
        "note": "What your SOP actually wants: big, liquid, profitable, and not in a "
                "downtrend - so puts and covered calls behave.",
        "rules": [("market_cap_b", ">=", 20), ("avg_volume_m", ">=", 2),
                  ("profit_margin", ">=", 5), ("above_200dma", ">=", 1),
                  ("beta", "<=", 1.6)],
    },
    "LEAPS candidate": {
        "note": "Durable growth in a live uptrend, not stretched - what a long-dated "
                "call needs from the underlying.",
        "rules": [("market_cap_b", ">=", 10), ("revenue_growth", ">=", 8),
                  ("profit_margin", ">=", 8), ("above_200dma", ">=", 1),
                  ("pct_off_high", "<=", 20), ("rsi", "<=", 75)],
    },
    "Dividend payer": {
        "note": "Income first: pays you to wait, and can afford to keep paying.",
        "rules": [("dividend_yield", ">=", 2.0), ("market_cap_b", ">=", 10),
                  ("profit_margin", ">=", 5), ("debt_to_equity", "<=", 2.0)],
    },
}


def preset(name: str) -> list[Criterion]:
    spec = PRESETS.get(name)
    if not spec:
        return []
    return [Criterion(field=f, op=op, value=float(v)) for f, op, v in spec["rules"]]


# ---------- evaluation ----------
def extract(info: dict, extras: Optional[dict] = None) -> dict[str, Optional[float]]:
    """Pull every testable field into one flat dict, already in display units."""
    info = info or {}
    extras = extras or {}
    out: dict[str, Optional[float]] = {}

    for field, spec in FIELDS.items():
        if field in extras:
            out[field] = extras[field]
            continue
        key = spec.get("key")
        if not key:
            out[field] = None
            continue
        raw = info.get(key)
        try:
            out[field] = float(raw) * spec["scale"] if raw is not None else None
        except (TypeError, ValueError):
            out[field] = None

    # Yahoo has shipped dividendYield as both a fraction and a percent.
    dy = out.get("dividend_yield")
    if dy is not None and 0 < dy <= 0.25:
        out["dividend_yield"] = dy * 100
    # debtToEquity arrives as a percent (150 = 1.5x); undo the scale if it was small.
    raw_de = info.get("debtToEquity")
    if raw_de is not None:
        try:
            value = float(raw_de)
            out["debt_to_equity"] = value / 100.0 if value > 5 else value
        except (TypeError, ValueError):
            pass
    return out


def _compare(actual: float, op: str, threshold: float) -> bool:
    if op == ">=":
        return actual >= threshold
    if op == "<=":
        return actual <= threshold
    if op == ">":
        return actual > threshold
    if op == "<":
        return actual < threshold
    return False


def evaluate(symbol: str, criteria: list[Criterion], info: Optional[dict] = None,
             extras: Optional[dict] = None, near_miss_pct: float = 10.0) -> AnalyzerResult:
    """Grade one stock against the rules. `near_miss_pct` is how close a failure
    has to be before we call it a near miss rather than a plain no."""
    info = info or {}
    values = extract(info, extras)
    result = AnalyzerResult(
        symbol=symbol.upper(),
        name=info.get("shortName") or info.get("longName") or "",
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
    )

    for c in [c for c in criteria if c.enabled]:
        spec = c.spec
        actual = values.get(c.field)
        row = RuleResult(field=c.field, label=spec["label"], op=c.op, threshold=c.value,
                         unit=spec.get("unit", ""), actual=actual)
        if actual is None:
            row.measured = False
            row.read = f"No data for {spec['label'].lower()} - this rule could not be checked."
        else:
            row.passed = _compare(actual, c.op, c.value)
            decimals = spec.get("decimals", 2)
            unit = spec.get("unit", "")
            shown = f"{actual:,.{decimals}f}{unit}"
            bar = f"{c.value:,.{decimals}f}{unit}"
            if row.passed:
                row.read = f"{shown} - clears the bar of {OPS.get(c.op, c.op)} {bar}."
            else:
                if c.value:
                    row.miss_pct = abs(actual - c.value) / abs(c.value) * 100
                    row.near_miss = row.miss_pct <= near_miss_pct
                row.read = (f"{shown} - misses {OPS.get(c.op, c.op)} {bar}"
                            + (f", but only by {row.miss_pct:.0f}%." if row.near_miss
                               else "."))
        result.rules.append(row)

    measurable = [r for r in result.rules if r.measured]
    result.measured_count = len(measurable)
    result.passed_count = sum(1 for r in measurable if r.passed)
    result.near_misses = sum(1 for r in measurable if r.near_miss)
    if measurable:
        result.score = round(100.0 * result.passed_count / len(measurable), 1)

    failures = result.measured_count - result.passed_count
    if failures == 0 and result.measured_count:
        result.verdict = "pass"
    elif failures and failures == result.near_misses:
        result.verdict = "near"
    else:
        result.verdict = "fail"

    result.summary = _summary(result)
    return result


def _summary(r: AnalyzerResult) -> str:
    if not r.measured_count:
        return f"None of your rules could be checked for {r.symbol} - no data came back."
    failed = [x for x in r.rules if x.measured and not x.passed]
    if r.verdict == "pass":
        return (f"{r.symbol} clears all {r.measured_count} of your rules. "
                "Passing your screen is a starting point, not a decision - it says "
                "nothing about the price you would pay today.")
    if r.verdict == "near":
        names = ", ".join(x.label.lower() for x in failed)
        return (f"{r.symbol} passes {r.passed_count} of {r.measured_count} rules and only "
                f"just misses on {names}. Worth a look if the rest is strong.")
    hard = [x for x in failed if not x.near_miss]
    names = ", ".join(x.label.lower() for x in hard[:3])
    return (f"{r.symbol} passes {r.passed_count} of {r.measured_count} rules. "
            f"It clearly fails on {names}"
            + (" and more." if len(hard) > 3 else "."))


def screen(symbols_info: dict[str, dict], criteria: list[Criterion],
           extras_by_symbol: Optional[dict[str, dict]] = None) -> list[AnalyzerResult]:
    """Run the same rules across many stocks, best-first."""
    extras_by_symbol = extras_by_symbol or {}
    results = [evaluate(sym, criteria, info, extras_by_symbol.get(sym.upper()))
               for sym, info in symbols_info.items()]
    results.sort(key=lambda r: (r.score, r.passed_count), reverse=True)
    return results
