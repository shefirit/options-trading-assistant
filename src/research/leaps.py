"""LEAPS Finder - long-dated calls held as a position in their own right.

A LEAP is a call option a year or more out. You buy it instead of buying the
shares: far less cash up front, similar upside per dollar moved, and a hard
floor on the loss (you can never lose more than you paid). The catch is the
part beginners miss - you are paying for time, and if the stock merely sits
still you lose every cent of it. Shares that go nowhere cost you nothing.

So the whole job of this module is to answer one question honestly:

    Is this stock likely to rise ENOUGH, SOON ENOUGH, to be worth what the
    option costs - and would I be better off just buying the shares?

Five things decide that, and each one is scored 0-100 with its own plain
reasons, then blended with the weights below:

  Trend    - is the stock actually in a durable uptrend? You are paying for
             direction, so you had better have some.
  Entry    - is this a sensible spot to buy, or are you chasing a vertical?
  Quality  - will the company still be compounding in one to two years? You
             are holding a long time and you cannot roll away a bad business.
  Cost     - what does the time premium actually cost, annualized, at the
             term you are buying - plus the dividends you give up by holding
             calls instead of shares. This is the number most tools skimp on.
  Odds     - how often has THIS stock, over its own history, made the move
             you need in the time you have? Plus how much leverage you get
             for the money and how brutal the total-loss line is.

Cost and Odds together are 45% of the score. That is deliberate. When you BUY
options, the price you pay and the odds you need are roughly half the outcome,
and a tool that weights them lightly will happily hand you a wonderful company
whose options are the most expensive they have been all year.

Nothing here is a recommendation. It is a scorecard that shows its working.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from src.data.chain import OptionChain, OptionContract
from src.engine.models import OptionType
from src.research import fundamentals as _fundamentals

# How much each pillar counts toward the final score. They must sum to 1.0.
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "entry": 0.15,
    "quality": 0.20,
    "cost": 0.25,
    "odds": 0.20,
}

TRADING_DAYS_YEAR = 252
# Her SOP judges the trend over a year and a HALF, not a year - long enough to
# contain a real drawdown and show whether the name climbs back out of it.
EIGHTEEN_MONTHS = 378


# ---------------------------------------------------------------- the SOP
# This module used to carry its own numbers - a 300-day LEAP floor, a 0.75
# delta default, 100 open interest. They were sensible generic defaults and
# they no longer matter, because the LEAPS long call is a STRATEGY now with
# rules in config/strategies.yaml. Rita's rule for this whole app is that her
# numbers live in config and the code follows, so the Finder reads them too.
# Otherwise the Analyze tab scores a name against one standard while Find a
# trade validates it against another.
def sop() -> dict:
    """The LEAPS long call's entry rules, straight from strategies.yaml."""
    from src.engine.config_loader import get_strategy
    try:
        return get_strategy("long_call_leaps").get("entry", {}) or {}
    except Exception:
        return {}


def _rule(name: str, fallback: float) -> float:
    value = sop().get(name)
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def min_leap_dte() -> int:
    """A LEAP is 365+ days by her SOP - not the 300 this module once used."""
    return int(_rule("dte_min", 365))


def target_dte() -> int:
    return int(_rule("dte_target", 400))


def max_leap_dte() -> int:
    """The furthest out her SOP will go. Past this the extra time stops buying
    anything - the contracts barely trade and you pay for months you will never
    hold through, since the roll-forward rule closes the trade at 180 days."""
    return int(_rule("dte_max", 800))


def target_delta() -> float:
    """The SOP floor. Deeper is fine; shallower is the mistake."""
    return _rule("long_leg_delta_min", 0.70)


def target_delta_pref() -> float:
    """The delta her SOP actually aims at, a touch above the floor."""
    return _rule("long_leg_delta_target", 0.72)


def min_open_interest() -> int:
    return int(_rule("min_open_interest", 250))


# Where the Finder starts CALLING a spread wide in words. Not an SOP rule and
# deliberately not in config: her ruling (2026-08-14) is that the spread gets
# noticed, never enforced. Nothing below drops a contract for exceeding it.
WIDE_SPREAD_PCT = 20.0


def vix_min() -> float:
    return _rule("vix_min", 15.0)


def band_max() -> float:
    """How far up the Bollinger range she will still buy. 0 = lower band."""
    return _rule("bollinger_band_max", 0.50)


def rsi_max() -> float:
    return _rule("rsi_max", 45.0)


# Kept as module names because callers import them; both now follow the SOP.
MIN_LEAP_DTE = min_leap_dte()
DEFAULT_TARGET_DELTA = target_delta()
MAX_LEAP_DTE = max_leap_dte()


# ---------------------------------------------------------------- data models
class Pillar(BaseModel):
    """One of the five scored categories."""
    key: str
    label: str
    weight: float
    score: float = 0.0                  # 0-100
    status: str = "ok"                  # "good" | "ok" | "watch"
    read: str = ""                      # one-line plain-English verdict
    factors: list[str] = Field(default_factory=list)   # the working behind it
    measured: bool = True               # False when we lacked the data


class BaseRate(BaseModel):
    """How often this stock has historically made the move you need.

    Computed from its own daily closes: slide a window of `horizon_days`
    across every day of history and count how often the forward return
    cleared the bar. This is the number that turns "it needs to rise 14%"
    into "it has done that in 62% of past 371-day stretches".
    """
    horizon_days: int = 0
    required_pct: float = 0.0
    windows: int = 0                     # how many overlapping windows we had
    hit_rate: Optional[float] = None     # percent that cleared the bar
    median_pct: Optional[float] = None   # typical forward return over that span
    p10_pct: Optional[float] = None      # a bad outcome (10th percentile)
    p90_pct: Optional[float] = None      # a good one (90th percentile)
    loss_rate: Optional[float] = None    # percent of windows that finished below
                                         # the strike, i.e. the LEAP expired worthless
    years_used: float = 0.0
    read: str = ""
    # The shape of those outcomes, for the chart: one entry per bucket with
    # {"from", "to", "mid", "pct", "clears"}. `pct` is the share of windows in
    # the bucket, `clears` says the bucket sits at or above the required move.
    distribution: list[dict] = Field(default_factory=list)


class LeapEconomics(BaseModel):
    """The actual money maths of one specific contract."""
    strike: float = 0.0
    expiration: str = ""
    dte: int = 0
    premium: float = 0.0                 # per share (mid)
    cost_dollars: float = 0.0            # per contract, what you pay
    delta: float = 0.0
    iv: Optional[float] = None

    intrinsic: float = 0.0
    extrinsic: float = 0.0               # the time premium - the part that decays
    extrinsic_pct_of_spot: float = 0.0
    extrinsic_ann_pct: float = 0.0       # annualized, the true "rent" on the trade

    cost_pct_of_spot: float = 0.0
    breakeven: float = 0.0
    required_move_pct: float = 0.0       # to breakeven by expiration
    required_move_ann_pct: float = 0.0

    leverage: float = 0.0                # exposure per dollar vs owning shares
    max_loss: float = 0.0                # the whole premium
    total_loss_price: float = 0.0        # at or below this at expiry = worth zero
    total_loss_drop_pct: float = 0.0     # how far the stock can fall to get there

    dividend_yield_pct: float = 0.0      # what the shares pay that you forgo
    dividend_give_up_pct: float = 0.0    # over the life of the contract

    all_in_cost_ann_pct: float = 0.0     # extrinsic + dividends given up, annualized

    spread_pct: Optional[float] = None   # bid-ask as % of mid - fill quality
    open_interest: int = 0
    liquidity: str = "n/a"               # "Good" | "OK" | "Thin"


class ShareComparison(BaseModel):
    """The honest side-by-side: this contract versus just buying shares."""
    shares_for_same_cash: float = 0.0
    share_cost: float = 0.0              # 100 shares
    leverage: float = 0.0
    leap_max_loss: float = 0.0
    shares_loss_at_total_loss: float = 0.0   # what shares lose at the LEAP's zero point
    verdict: str = ""


class LeapsCandidate(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    price: Optional[float] = None
    market_cap: Optional[float] = None
    avg_volume: Optional[float] = None

    score: float = 0.0
    raw_score: float = 0.0               # before the critical-pillar cap
    gated: bool = False                  # True when a failing pillar held it down
    stage: str = "setup"                 # "setup" (no option data yet) | "full"
    rank: Optional[int] = None
    pillars: list[Pillar] = Field(default_factory=list)

    # the chart-level facts, same ones the card shows
    pct_off_52w_high: Optional[float] = None
    high_52w: Optional[float] = None
    sma50: Optional[float] = None
    sma200: Optional[float] = None
    rsi: Optional[float] = None
    weekly_k: Optional[float] = None
    weekly_d: Optional[float] = None
    realized_vol_pct: Optional[float] = None

    iv_30d_pct: Optional[float] = None
    iv_percentile: Optional[float] = None
    earnings_date: Optional[dt.date] = None
    days_to_earnings: Optional[int] = None
    analyst_target: Optional[float] = None

    econ: Optional[LeapEconomics] = None
    base_rate: Optional[BaseRate] = None
    comparison: Optional[ShareComparison] = None
    strike_ladder: list[dict] = Field(default_factory=list)

    flags: list[str] = Field(default_factory=list)
    headline: str = ""
    summary: str = ""

    def pillar(self, key: str) -> Optional[Pillar]:
        return next((p for p in self.pillars if p.key == key), None)


class Filters(BaseModel):
    """Scan criteria. Defaults are deliberately looser on the chart signals and
    tighter on the things that actually decide a LEAP's outcome."""
    min_market_cap_b: float = 10.0
    min_avg_volume_m: float = 1.0
    # Her SOP's floor, not the generic 100 this used to carry. A LEAPS is exited
    # in one sale, so a thin contract costs more to leave than it pays.
    min_open_interest: int = Field(default_factory=min_open_interest)
    sector: str = "All sectors"
    profitable_only: bool = True      # SOP: PE must be positive

    require_above_200dma: bool = True
    require_above_50dma: bool = False
    require_k_above_d: bool = False
    stoch_min: float = 0.0
    stoch_max: float = 100.0

    # The ones their scanner does not have.
    max_pct_off_high: float = 35.0       # ignore broken charts
    max_iv_percentile: Optional[float] = None   # do not buy peak-priced options
    max_required_move_ann_pct: Optional[float] = None
    min_base_rate: Optional[float] = None
    min_score: float = 0.0
    hide_earnings_within_days: Optional[int] = None


# ------------------------------------------------------------ small utilities
def sma(values: list[float], length: int) -> Optional[float]:
    return sum(values[-length:]) / length if len(values) >= length else None


def rsi(values: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI, the last value - one shared implementation.

    This was its own naive average of the last 14 bars, which disagreed with
    both the chart and thinkorswim. See stock_analysis.rsi for the full note.
    """
    from src.data.stock_analysis import rsi as _rsi

    return _rsi(values, period)


def weekly_closes(closes: list[float]) -> list[float]:
    """Squash daily closes into weekly ones: every 5th trading day, oldest first.

    Counted back from the NEWEST bar, which matters. Grouping forward from the
    oldest bar meant the week boundaries moved whenever the history length
    changed, so the same latest price produced a different weekly reading
    depending on how far back the data happened to reach - on AAPL the weekly
    %K ranged from 67.5 to 75.0 across five one-day shifts of the start date.
    An indicator has to answer to recent prices, not to how much history a
    given tab asked for.

    Still an approximation of real calendar weeks (it cannot see holidays), but
    a stable one, and it lets the whole-market scan run off the same batched
    daily download rather than a second request.
    """
    if not closes:
        return []
    return [closes[i] for i in range(len(closes) - 1, -1, -5)][::-1]


def stochastic(closes: list[float], highs: Optional[list[float]] = None,
               lows: Optional[list[float]] = None, period: int = 14,
               smooth: int = 3) -> tuple[Optional[float], Optional[float]]:
    """Slow stochastic (%K, %D) - where price sits inside its recent range.

    Pass real highs and lows when you have them. With closes alone we use the
    range of closes, which runs a touch narrower than the textbook version but
    tells the same story.
    """
    highs = highs or closes
    lows = lows or closes
    n = min(len(closes), len(highs), len(lows))
    if n < period + smooth:
        return None, None

    raw: list[float] = []
    for end in range(n - (smooth + 2), n):
        window_hi = max(highs[max(0, end - period + 1):end + 1])
        window_lo = min(lows[max(0, end - period + 1):end + 1])
        span = window_hi - window_lo
        raw.append(50.0 if span <= 0 else (closes[end] - window_lo) / span * 100.0)

    if len(raw) < smooth:
        return None, None
    k_values = [sum(raw[i:i + smooth]) / smooth for i in range(len(raw) - smooth + 1)]
    k = k_values[-1]
    d = sum(k_values[-smooth:]) / min(smooth, len(k_values))
    return round(k, 1), round(d, 1)


def realized_vol(closes: list[float], lookback: int = TRADING_DAYS_YEAR) -> Optional[float]:
    """Annualized realized volatility from daily closes, as a decimal (0.28).

    Non-finite closes are dropped FIRST, the same as premium_finder does. Yahoo
    returns the odd NaN close (a holiday boundary, a row with no print) and a
    single one used to poison the whole calculation there: the standard
    deviation came out NaN, the IV/HV ratio came out NaN, and every name in the
    Picks scan was graded "Thin" premium and silently dropped. This copy had the
    same hole - the LEAPS cost pillar is scored off this number, so one bad row
    could quietly hand a name an unmeasurable cost score.

    Note the LOOKBACK differs from premium_finder's on purpose: that one reads
    30 days (near-term volatility, for judging whether option premium is rich),
    this one reads a year (the horizon a LEAPS is actually held over). Same
    maths, different question - so the two are expected to disagree.
    """
    clean = [c for c in closes
             if isinstance(c, (int, float)) and math.isfinite(c) and c > 0]
    series = clean[-(lookback + 1):]
    if len(series) < 30:
        return None
    rets = [math.log(series[i] / series[i - 1]) for i in range(1, len(series))
            if series[i - 1] > 0]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_YEAR)


def vol_percentile(closes: list[float], current_iv_pct: float,
                   window: int = 30, lookback: int = TRADING_DAYS_YEAR) -> Optional[float]:
    """Where today's implied volatility sits against this stock's own past year
    of realized volatility, as a percentile.

    A word on what this is and is not. The paid tools quote an "IV percentile"
    built from a stored history of implied volatility. Free data does not give
    us that history, so rather than invent it we compare today's IV to the
    distribution of the stock's ACTUAL 30-day volatility over the past year.

    It answers a slightly different question - "is the market charging a lot
    relative to how much this stock normally moves?" - and for deciding whether
    to buy or sell premium that is arguably the more useful one.
    """
    if current_iv_pct is None or current_iv_pct <= 0 or len(closes) < window + 60:
        return None
    series = closes[-(lookback + window):]
    samples = []
    for end in range(window, len(series)):
        rv = realized_vol(series[end - window:end + 1], lookback=window)
        if rv:
            samples.append(rv * 100)
    if len(samples) < 30:
        return None
    below = sum(1 for s in samples if s <= current_iv_pct)
    return round(100.0 * below / len(samples), 1)


# Re-exported so callers and tests can keep reaching for it here; the units
# logic itself lives in fundamentals.py, shared with the Instant Analyzer.
dividend_yield_pct = _fundamentals.dividend_yield_pct


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[idx]


# ------------------------------------------------------------- the base rate
def historical_base_rate(closes: list[float], horizon_days: int, required_pct: float,
                         strike_drop_pct: Optional[float] = None) -> BaseRate:
    """How often did this stock clear `required_pct` over `horizon_days`?

    We slide the window one trading day at a time across every day of history
    we have. The windows overlap heavily, so this is a texture reading rather
    than a clean statistical sample - but it is the stock's OWN behaviour,
    which beats a generic assumption or a 12-month analyst target.

    `strike_drop_pct` (a negative number, how far price would have to fall to
    reach the strike) gives us the total-loss rate: how often the LEAP would
    have expired worthless.
    """
    result = BaseRate(horizon_days=horizon_days, required_pct=required_pct)
    # Daily closes -> trading days. Roughly 252 trading days per 365 calendar.
    span = max(1, int(round(horizon_days * TRADING_DAYS_YEAR / 365)))
    if len(closes) < span + 30:
        result.read = ("Not enough price history to work out how often this stock "
                       "has made that move before.")
        return result

    forwards: list[float] = []
    for i in range(len(closes) - span):
        start, end = closes[i], closes[i + span]
        if start > 0:
            forwards.append((end / start - 1.0) * 100.0)
    if not forwards:
        result.read = "Not enough overlapping windows to measure."
        return result

    result.windows = len(forwards)
    result.years_used = round(len(closes) / TRADING_DAYS_YEAR, 1)
    result.hit_rate = 100.0 * sum(1 for f in forwards if f >= required_pct) / len(forwards)
    result.median_pct = _percentile(forwards, 50)
    result.p10_pct = _percentile(forwards, 10)
    result.p90_pct = _percentile(forwards, 90)
    if strike_drop_pct is not None:
        result.loss_rate = 100.0 * sum(1 for f in forwards if f <= strike_drop_pct) / len(forwards)

    result.distribution = _histogram(forwards, required_pct)

    result.read = (
        f"Over {result.years_used:.0f} years, this stock cleared {required_pct:+.1f}% in "
        f"{result.hit_rate:.0f}% of {horizon_days}-day stretches. A typical stretch "
        f"returned {result.median_pct:+.1f}%."
    )
    return result


def _histogram(values: list[float], required_pct: float,
               buckets: int = 24) -> list[dict]:
    """Bucket the forward returns so the spread can be drawn, not just described.

    The bar chart is the point of this: a hit rate of 62% says little next to
    seeing where the bar you must clear actually falls inside the pile of
    everything this stock has done.
    """
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:                      # every window identical
        return [{"from": low, "to": high, "mid": low, "pct": 100.0,
                 "clears": low >= required_pct}]
    width = (high - low) / buckets
    counts = [0] * buckets
    for v in values:
        idx = min(buckets - 1, int((v - low) / width))
        counts[idx] += 1
    total = len(values)
    rows = []
    for i, count in enumerate(counts):
        start, end = low + i * width, low + (i + 1) * width
        mid = (start + end) / 2
        rows.append({"from": round(start, 2), "to": round(end, 2),
                     "mid": round(mid, 2),
                     "pct": round(100.0 * count / total, 2),
                     # a bucket counts as clearing when its midpoint does, which
                     # keeps the colour split visually where the line sits
                     "clears": mid >= required_pct})
    return rows


def probability_above(spot: float, target: float, dte: int, iv: float,
                      drift: float = 0.0) -> Optional[float]:
    """Textbook lognormal odds of finishing above `target`, as a percent.

    A cross-check on the base rate, using the option market's own implied
    volatility. If the two disagree wildly that is itself worth seeing: the
    market is pricing a different future than this stock's history suggests.
    """
    if spot <= 0 or target <= 0 or dte <= 0 or iv <= 0:
        return None
    T = dte / 365.0
    sigma_t = iv * math.sqrt(T)
    d2 = (math.log(spot / target) + (drift - 0.5 * iv * iv) * T) / sigma_t
    return round(100 * 0.5 * (1 + math.erf(d2 / math.sqrt(2))), 1)


# ------------------------------------------------------ picking the contract
# Deltas arrive from the feed at four decimals and get READ at two. AEP listed a
# 0.6999 delta against her 0.70 floor: rejected by a ten-thousandth, while the
# rejection message said "Delta is 0.70, shallower than your 0.70 floor" - a
# sentence no one can act on. Half a delta point of slack keeps the rule honest
# at the precision she actually sees.
DELTA_TOLERANCE = 0.005


def stock_substitute_delta() -> float:
    """Where a directional LEAPS stops being one and becomes a stock substitute.

    Not a new rule - it is her PMCC's own delta floor, read from config. Her SOP
    draws exactly this line: the PMCC buys 0.80+ as a stock replacement to rent
    out, while this strategy buys 0.70-0.80 as a bet on the move. A call deeper
    than the PMCC floor is priced like shares and barely leverages the move.
    """
    from src.engine.config_loader import get_strategy
    try:
        entry = get_strategy("poor_mans_covered_call").get("entry", {}) or {}
        return float(entry.get("long_leg_delta_min", 0.80))
    except Exception:
        return 0.80


def spread_pct(contract: OptionContract) -> Optional[float]:
    """Bid-ask as a percentage of mid, or None when there is no usable quote."""
    if contract.bid <= 0 or contract.ask <= 0 or contract.ask < contract.bid:
        return None
    mid = (contract.bid + contract.ask) / 2
    return round((contract.ask - contract.bid) / mid * 100, 1) if mid > 0 else None


def meets_sop(contract: OptionContract, delta_floor: float, min_dte: int,
              max_dte: int, min_oi: int) -> bool:
    """Does this contract satisfy every hard contract rule at once?

    The bid-ask spread is deliberately NOT one of them. It was briefly, on
    2026-08-14, and she removed it the same day: noticed, never enforced.
    """
    return (contract.abs_delta >= delta_floor - DELTA_TOLERANCE
            and min_dte <= contract.dte <= max_dte
            and contract.open_interest >= min_oi)


def breaches(contract: OptionContract, delta_floor: Optional[float] = None,
             min_dte: Optional[int] = None, max_dte: Optional[int] = None,
             min_oi: Optional[int] = None) -> list[str]:
    """The SOP contract rules this one breaks, in plain English. Empty when it
    passes.

    This exists so the Finder can never present a rule-breaking contract in
    silence. When no compliant contract is listed at all, `pick_contract` still
    returns the closest thing - showing her nothing would be worse - but it has
    to come with the reason attached.
    """
    delta_floor = target_delta() if delta_floor is None else delta_floor
    min_dte = min_leap_dte() if min_dte is None else min_dte
    max_dte = max_leap_dte() if max_dte is None else max_dte
    min_oi = min_open_interest() if min_oi is None else min_oi

    out: list[str] = []
    if contract.dte < min_dte:
        out.append(
            f"This contract expires in {contract.dte} days, under your {min_dte}-day "
            "LEAPS minimum. Your SOP buys that much time on purpose - it is what "
            "lets the trade sit through a crash without a stop loss.")
    elif contract.dte > max_dte:
        out.append(
            f"This contract runs {contract.dte} days, past your {max_dte}-day ceiling. "
            "You would be paying for months the roll-forward rule closes out anyway.")
    # A delta of exactly 0 means the feed sent no greeks, not a shallow option.
    if contract.abs_delta and contract.abs_delta < delta_floor - DELTA_TOLERANCE:
        out.append(
            f"Delta is {contract.abs_delta:.2f}, shallower than your {delta_floor:.2f} "
            "floor. A shallower call is cheaper but needs a bigger move, which is the "
            "trade your SOP deliberately does not take.")
    deep = stock_substitute_delta()
    if contract.abs_delta >= deep:
        out.append(
            f"Delta is {contract.abs_delta:.2f}, at or past the {deep:.2f} your PMCC uses. "
            "This deep, the call tracks the shares almost one for one - you are paying "
            "for a stock substitute rather than the leveraged bet on a move that this "
            "strategy is for. It is usually a sign the strikes you actually want have "
            "no open interest.")
    if contract.open_interest < min_oi:
        out.append(
            f"Open interest is {contract.open_interest} against your {min_oi} floor. "
            "That is how many contracts exist at this strike - too few and you will "
            "give away real money on the spread when you try to sell out.")
    return out


def spread_note(contract: OptionContract) -> Optional[str]:
    """A plain-English remark when the spread is wide - NOT a rule breach.

    Kept out of `breaches()` on purpose: that list is what her SOP refuses, and
    the spread is not something it refuses. This is the "pay attention to it"
    half, with no limit attached.
    """
    pct = spread_pct(contract)
    if pct is None or pct <= WIDE_SPREAD_PCT:
        return None
    return (
        f"Wide bid-ask: {pct:.0f}% of what the option is worth. Nothing in your SOP "
        "refuses this, but it is worth seeing - open interest counts contracts that "
        "exist, and this counts what they cost you to get in and out of. You would "
        "start the trade down by that much and pay it again on the way out. Quotes go "
        "wide when the US market is shut, so check it again while trading is open.")


def _shortfall(contract: OptionContract, delta_floor: float, min_dte: int,
               max_dte: int, min_oi: int, deep: float) -> tuple:
    """How badly a contract misses the rules - sortable, least-bad first.

    The three rules are NOT equally severe, and ranking them by a simple count
    proved it on live data: MAR listed a 0.24 delta call with 266 open interest
    alongside a 0.70 delta call with 56. Each breaks exactly one rule, so
    counting picked the 0.24 - a far out-of-the-money lottery ticket, which is
    not the same trade at all.

    So they are ordered by what they cost her. Delta decides WHAT the trade is.
    DTE decides how long she has to be right. Open interest is friction on the
    way out - real money, but it does not change the position she is holding.

    Delta counts as wrong on BOTH sides. Open interest piles up deep in the
    money, so ranking on it alone walked straight out of the strategy the other
    way: LIN came back at delta 0.94 for $19,325, which is buying the shares
    with extra steps.
    """
    off_strategy = (contract.abs_delta < delta_floor - DELTA_TOLERANCE
                    or contract.abs_delta >= deep)
    pct = spread_pct(contract)
    return (
        off_strategy,
        not (min_dte <= contract.dte <= max_dte),
        contract.open_interest < min_oi,
        -contract.open_interest,
        abs(contract.abs_delta - max(delta_floor, target_delta_pref())),
        # Last, and only as a tiebreak between contracts that are otherwise
        # equally good: prefer the one that is cheaper to trade out of. This is
        # "pay attention to the spread" without it limiting anything - it can
        # never push a contract out, only order two that already tie. Unquotable
        # sorts last of the ties rather than first.
        pct if pct is not None else float("inf"),
    )


def pick_contract(chain: OptionChain, target_delta: float = DEFAULT_TARGET_DELTA,
                  min_dte: int = MIN_LEAP_DTE, min_oi: Optional[int] = None,
                  max_dte: Optional[int] = None) -> Optional[OptionContract]:
    """The best call that satisfies every hard contract rule at once.

    `target_delta` is a FLOOR, not a bullseye - her SOP says 70 delta or deeper,
    so among the contracts that clear it we take the shallowest, which is the
    cheapest way to satisfy the rule.

    This used to lock onto one expiration (the furthest out) and pick the closest
    delta on it, looking at neither open interest nor the DTE ceiling. That
    quietly returned untradable contracts: on a live scan it handed back 308-day
    expirations against a 365-day rule, and strikes with open interest of 2, 3
    and 7 against a floor of 250 - while a compliant contract sat one expiration
    further out on the same board. So the search now runs across EVERY
    expiration, and only falls back when the board genuinely has nothing that
    qualifies. Callers pair that fallback with `breaches()` to say what is wrong
    with it rather than presenting it as a clean pick.
    """
    calls = [c for c in chain.contracts
             if c.option_type == OptionType.CALL and c.mid > 0]
    if not calls:
        return None

    min_oi = min_open_interest() if min_oi is None else min_oi
    max_dte = max_leap_dte() if max_dte is None else max_dte

    compliant = [c for c in calls
                 if meets_sop(c, target_delta, min_dte, max_dte, min_oi)]
    if compliant:
        # Longest expiration inside the window, because the whole point of the
        # extra time is room to be wrong; then the delta nearest her configured
        # target on it.
        #
        # That last step used to take the SHALLOWEST delta clearing the floor,
        # on the logic that deeper only costs more. It backfired on EBAY, where
        # every strike from 0.70 to 0.75 had an open interest under 70 and the
        # only liquid one left was delta 0.94 - so "shallowest that qualifies"
        # returned a $5,725 near-stock-substitute. Aiming at the target instead
        # of the floor picks the same contract when the band is liquid and lets
        # breaches() speak up when it is not.
        preferred = max(target_delta, target_delta_pref())
        best_dte = max(c.dte for c in compliant)
        at_expiry = [c for c in compliant if c.dte == best_dte]
        # Delta first, then - only to separate contracts that tie on it - the
        # one that is cheaper to trade out of. The spread never removes a
        # contract from consideration, it just orders equals.
        return min(at_expiry, key=lambda c: (
            abs(c.abs_delta - preferred),
            spread_pct(c) if spread_pct(c) is not None else float("inf")))

    # Nothing on the board qualifies. Hand back the LEAST-BAD contract so the
    # tab still has something to score, and let the caller say what it breaks.
    in_window = [c for c in calls if min_dte <= c.dte <= max_dte]
    pool = in_window or calls
    with_delta = [c for c in pool if c.abs_delta > 0]
    if with_delta:
        deep = stock_substitute_delta()
        return min(with_delta, key=lambda c: _shortfall(
            c, target_delta, min_dte, max_dte, min_oi, deep))
    # No greeks on the feed - approximate by moneyness instead. A 0.75 delta
    # call sits roughly 10-15% in the money on a year-out contract.
    best_dte = max(c.dte for c in pool)
    at_expiry = [c for c in pool if c.dte == best_dte]
    spot = chain.underlying_price
    wanted = spot * (1 - (target_delta - 0.5) * 0.55)
    return min(at_expiry, key=lambda c: abs(c.strike - wanted))


def economics(contract: OptionContract, spot: float, info: Optional[dict] = None,
              multiplier: int = 100) -> LeapEconomics:
    """Turn one contract into the numbers that decide whether to buy it."""
    info = info or {}
    premium = contract.mid
    dte = max(contract.dte, 1)
    years = dte / 365.0

    econ = LeapEconomics(
        strike=contract.strike, expiration=contract.expiration, dte=contract.dte,
        premium=premium, cost_dollars=round(premium * multiplier, 2),
        delta=abs(contract.delta), iv=contract.iv or None,
        open_interest=contract.open_interest,
    )

    econ.intrinsic = max(spot - contract.strike, 0.0)
    econ.extrinsic = max(premium - econ.intrinsic, 0.0)
    if spot > 0:
        econ.extrinsic_pct_of_spot = econ.extrinsic / spot * 100.0
        econ.extrinsic_ann_pct = econ.extrinsic_pct_of_spot / years
        econ.cost_pct_of_spot = premium / spot * 100.0

    econ.breakeven = round(contract.strike + premium, 2)
    if spot > 0:
        econ.required_move_pct = (econ.breakeven / spot - 1.0) * 100.0
        econ.required_move_ann_pct = econ.required_move_pct / years

    if premium > 0 and spot > 0 and econ.delta > 0:
        econ.leverage = round(econ.delta * spot / premium, 2)
    econ.max_loss = econ.cost_dollars
    econ.total_loss_price = contract.strike
    if spot > 0:
        econ.total_loss_drop_pct = (contract.strike / spot - 1.0) * 100.0

    econ.dividend_yield_pct = dividend_yield_pct(info, spot)
    econ.dividend_give_up_pct = econ.dividend_yield_pct * years
    econ.all_in_cost_ann_pct = econ.extrinsic_ann_pct + econ.dividend_yield_pct

    if contract.bid > 0 and contract.ask > 0 and premium > 0:
        econ.spread_pct = round((contract.ask - contract.bid) / premium * 100.0, 1)
    oi, spread = contract.open_interest, econ.spread_pct
    if oi >= 500 and (spread is None or spread <= 8):
        econ.liquidity = "Good"
    elif oi >= 100 and (spread is None or spread <= 15):
        econ.liquidity = "OK"
    else:
        econ.liquidity = "Thin"
    return econ


def strike_ladder(chain: OptionChain, spot: float, dte: int,
                  info: Optional[dict] = None) -> list[dict]:
    """The same maths across every strike at one expiration.

    This is where the real decision gets made. A deeper strike costs more but
    needs a smaller move; a shallower one is cheap leverage that needs a big
    move. Seeing them side by side beats being handed a single fixed delta.
    """
    calls = [c for c in chain.contracts
             if c.option_type == OptionType.CALL and c.dte == dte and c.mid > 0]
    rows = []
    for c in sorted(calls, key=lambda x: x.strike):
        if spot > 0 and not (0.55 * spot <= c.strike <= 1.15 * spot):
            continue                       # ignore the far tails, they are noise
        e = economics(c, spot, info)
        rows.append({
            "strike": c.strike,
            "delta": round(e.delta, 2) if e.delta else None,
            "premium": e.premium,
            "cost": e.cost_dollars,
            "cost_pct_of_spot": round(e.cost_pct_of_spot, 1),
            "extrinsic_ann_pct": round(e.extrinsic_ann_pct, 1),
            "breakeven": e.breakeven,
            "required_move_pct": round(e.required_move_pct, 1),
            "leverage": e.leverage,
            "total_loss_drop_pct": round(e.total_loss_drop_pct, 1),
            "open_interest": c.open_interest,
        })
    return rows


# ------------------------------------------------------------------- pillars
def _band(value: Optional[float], bands: list[tuple[float, float]],
          default: float = 0.0) -> float:
    """First band whose threshold `value` is at or below wins."""
    if value is None:
        return default
    for threshold, points in bands:
        if value <= threshold:
            return points
    return default


def score_trend(closes: list[float]) -> Pillar:
    """Is there a durable uptrend to pay for?"""
    p = Pillar(key="trend", label="Trend", weight=DEFAULT_WEIGHTS["trend"])
    if len(closes) < 60:
        p.measured, p.read = False, "Not enough price history to judge the trend."
        return p

    price = closes[-1]
    s50, s200 = sma(closes, 50), sma(closes, 200)
    points = 0.0

    if s200:
        if price > s200:
            points += 25
            p.factors.append("Price is above the 200-day average - the long-term trend is up.")
        else:
            p.factors.append("Price is below the 200-day average - the long-term trend is down. "
                             "A LEAP here is betting against the tide.")
        prior200 = sma(closes[:-21], 200)
        if prior200 and s200 > prior200:
            points += 20
            p.factors.append("The 200-day average is still rising.")
        elif prior200:
            p.factors.append("The 200-day average has started to roll over.")
    else:
        p.factors.append("Less than 200 days of history - long-term trend unknown.")

    if s50:
        if price > s50:
            points += 15
            p.factors.append("Price is above the 50-day average.")
        else:
            p.factors.append("Price is below the 50-day average - it has lost short-term footing.")
    if s50 and s200:
        if s50 > s200:
            points += 15
            p.factors.append("The 50-day sits above the 200-day - the healthy configuration.")
        else:
            p.factors.append("The 50-day is below the 200-day - a weak configuration.")

    # Higher lows: is the recent floor above the one before it?
    if len(closes) >= 126:
        recent_low, prior_low = min(closes[-63:]), min(closes[-126:-63])
        if recent_low > prior_low:
            points += 15
            p.factors.append("It is making higher lows - buyers keep stepping in earlier.")
        else:
            p.factors.append("Recent lows are no higher than the previous ones.")

    # Twelve-month momentum, the one factor with real academic legs behind it.
    if len(closes) >= TRADING_DAYS_YEAR:
        year_return = (price / closes[-TRADING_DAYS_YEAR] - 1) * 100
        if year_return > 0:
            points += 5
            p.factors.append(f"Up {year_return:+.0f}% over the past year.")
        else:
            p.factors.append(f"Down {year_return:+.0f}% over the past year.")

    # Eighteen months, which is the window her SOP actually names. A year is the
    # conventional momentum lookback and it was the only one measured here, but
    # the extra six months is the point: it reaches back far enough to include a
    # real drawdown and show whether this name RECOVERS from one. That is what
    # the SOP is asking for, and it is the whole reason the trade can sit through
    # a crash without a stop.
    if len(closes) >= EIGHTEEN_MONTHS:
        long_return = (price / closes[-EIGHTEEN_MONTHS] - 1) * 100
        if long_return > 0:
            points += 10
            p.factors.append(
                f"Up {long_return:+.0f}% over the past 18 months - your SOP's window, "
                "long enough to show it recovers from a real dip.")
        else:
            p.factors.append(
                f"Down {long_return:+.0f}% over the past 18 months. Your SOP wants an "
                "18-month uptrend, so this one does not qualify.")
    elif len(closes) >= TRADING_DAYS_YEAR:
        p.factors.append("Less than 18 months of history, so your SOP's full trend "
                         "window could not be checked.")

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("Firm uptrend." if p.score >= 70 else
              "Mixed trend - not clearly up." if p.score >= 45 else
              "No uptrend to speak of. Paying for direction you do not have.")
    return p


def band_position(closes: list[float]) -> Optional[float]:
    """Where price sits across the Bollinger range: 0.0 = lower band, 1.0 = upper.

    Daily closes, 20-period, 2 standard deviations - the settings her SOP names.
    Values outside 0-1 are real: price does break the bands about 5% of the
    time, and a reading below 0 is the strongest buy signal this strategy has.
    """
    from src.engine import indicators

    if len(closes) < 20:
        return None
    upper, _mid, lower = indicators.bollinger(closes, length=20, mult=2.0)
    hi, lo = upper[-1], lower[-1]
    if hi is None or lo is None or hi <= lo:
        return None
    return (closes[-1] - lo) / (hi - lo)


def macd_turning_up(closes: list[float]) -> Optional[bool]:
    """True when the MACD line is at or above its signal, or flattening toward
    it - her SOP's "lines beginning to flatten out" confirmation."""
    from src.engine import indicators

    if len(closes) < 40:
        return None
    line, signal, _hist = indicators.macd(closes)
    if line[-1] is None or signal[-1] is None or line[-2] is None or signal[-2] is None:
        return None
    gap_now = line[-1] - signal[-1]
    gap_before = line[-2] - signal[-2]
    return gap_now >= 0 or gap_now > gap_before      # crossed up, or closing the gap


def score_entry(closes: list[float], highs: Optional[list[float]] = None,
                lows: Optional[list[float]] = None) -> Pillar:
    """Is this a decent spot to buy, judged by HER SOP?

    Rewritten to follow the LEAPS long call SOP rather than generic practice,
    and the two disagree in the one place that matters most. The old version
    gave its best score to RSI 45-70 ("healthy, not stretched") and its worst
    to oversold, calling that "catching a falling knife". Her SOP buys exactly
    what that penalised: the stock pressed against its LOWER Bollinger band
    with RSI heading toward oversold. Scoring it the old way meant the Analyze
    tab talked her out of the entry Find a trade was built to check.

    Ranked by what the SOP actually weighs:
      Bollinger band position - the primary signal, so it carries most points
      RSI                     - confirmation, wants high 30s to low 40s
      MACD                    - confirmation, wants flattening or crossing up
    """
    p = Pillar(key="entry", label="Entry timing", weight=DEFAULT_WEIGHTS["entry"])
    if len(closes) < 60:
        p.measured, p.read = False, "Not enough price history to judge the entry."
        return p

    points = 0.0
    ceiling = band_max()

    pos = band_position(closes)
    if pos is None:
        p.factors.append("Bollinger bands could not be computed for this name.")
    elif pos <= 0.0:
        points += 55
        p.factors.append(
            "Price has pushed BELOW its lower Bollinger band - the roughly 5% "
            "outlier your SOP waits for. This is the ideal entry.")
    elif pos <= 0.25:
        points += 50
        p.factors.append(
            f"Sitting at the lower Bollinger band ({pos * 100:.0f}% up the range). "
            "This is the entry your SOP calls ideal.")
    elif pos <= ceiling:
        points += 32
        p.factors.append(
            f"{pos * 100:.0f}% up the Bollinger range - in the lower half, so an "
            "acceptable entry. Accept that it may still fall to the lower band.")
    elif pos <= 0.75:
        points += 10
        p.factors.append(
            f"{pos * 100:.0f}% up the Bollinger range - above the halfway line your "
            "SOP allows. Waiting for a pullback costs nothing.")
    else:
        p.factors.append(
            f"{pos * 100:.0f}% up the Bollinger range, near the UPPER band. Your SOP "
            "treats this as where you EXIT, not where you buy.")

    value = rsi(closes)
    limit = rsi_max()
    if value is not None:
        if value <= 30:
            points += 25
            p.factors.append(
                f"RSI {value:.0f} - fully oversold. Cheap, though a knife this sharp "
                "usually wants a day or two to steady.")
        elif value <= limit:
            points += 30
            p.factors.append(
                f"RSI {value:.0f} - heading toward oversold, under your {limit:.0f} "
                "ceiling. Exactly the zone your SOP buys in.")
        elif value < 70:
            points += 8
            p.factors.append(
                f"RSI {value:.0f} - above your {limit:.0f} ceiling. Not stretched, but "
                "not the pullback this strategy waits for either.")
        else:
            p.factors.append(f"RSI {value:.0f} - overbought. Your SOP does not buy here.")

    turning = macd_turning_up(closes)
    if turning is True:
        points += 15
        p.factors.append("MACD is flattening or crossing upward - the confirmation "
                         "your SOP looks for.")
    elif turning is False:
        p.factors.append("MACD is still falling away from its signal line. Your SOP "
                         "waits for it to flatten first.")

    # How far it has fallen, which the band position alone cannot tell you. A
    # stock 45% off its high is pinned to its lower band too, and reads as a
    # textbook entry on the signals above - but her SOP's fourth criterion says
    # the drop must be the MARKET falling, never a broken story. Nothing in the
    # price series proves which it is, so a collapse is penalised and named
    # rather than scored as a bargain.
    price = closes[-1]
    window = closes[-TRADING_DAYS_YEAR:] if len(closes) >= TRADING_DAYS_YEAR else closes
    high52 = max(window)
    drop = abs((price / high52 - 1) * 100) if high52 > 0 else 0.0
    if drop > 35:
        points -= 45
        p.factors.append(
            f"Down {drop:.0f}% from its 52-week high. That is not a pullback, it is a "
            "broken chart - and your SOP wants a name that RECOVERS from every dip. "
            "Check what actually happened before going near it.")
    elif drop > 25:
        points -= 20
        p.factors.append(
            f"Down {drop:.0f}% from its 52-week high - a deep fall. Fine if the whole "
            "market fell with it, a warning sign if this name fell alone.")

    p.score = max(0.0, min(100.0, points))
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("Textbook entry by your SOP - low in the range and turning." if p.score >= 70
              else "Workable entry, not the ideal one." if p.score >= 45 else
              "Not an entry your SOP would take - too high in the range, or still falling.")
    return p


def score_quality(info: dict, market_cap: Optional[float] = None) -> Pillar:
    """Will this company still be compounding in one to two years?

    You cannot roll a LEAP away from a deteriorating business the way you can
    manage a 30-day trade. Over a year the fundamentals get a vote.
    """
    p = Pillar(key="quality", label="Quality", weight=DEFAULT_WEIGHTS["quality"])
    info = info or {}
    cap = market_cap or info.get("marketCap")
    if not info and not cap:
        p.measured, p.read = False, "No fundamentals loaded yet."
        return p

    points = 0.0
    if cap:
        cap_points = 25 if cap >= 200e9 else 20 if cap >= 50e9 else \
                     15 if cap >= 10e9 else 7 if cap >= 2e9 else 0
        points += cap_points
        size = ("Mega-cap" if cap >= 200e9 else "Large-cap" if cap >= 10e9 else
                "Mid-cap" if cap >= 2e9 else "Small-cap")
        p.factors.append(f"{size} - ${cap / 1e9:,.0f}B. "
                         + ("Big and durable." if cap >= 50e9 else
                            "Established." if cap >= 10e9 else
                            "Smaller companies can move violently over a year."))

    margin = info.get("profitMargins")
    if margin is not None:
        pct = margin * 100
        points += 20 if pct >= 15 else 14 if pct >= 8 else 8 if pct >= 0 else 0
        p.factors.append(f"Profit margin {pct:.0f}% - "
                         + ("very profitable." if pct >= 15 else
                            "solidly profitable." if pct >= 8 else
                            "thin profits." if pct >= 0 else
                            "losing money. A year is a long time to hold that."))

    growth = info.get("revenueGrowth")
    if growth is not None:
        pct = growth * 100
        points += 20 if pct >= 15 else 15 if pct >= 5 else 8 if pct >= 0 else 0
        p.factors.append(f"Revenue growth {pct:+.0f}% - "
                         + ("growing fast." if pct >= 15 else
                            "growing steadily." if pct >= 5 else
                            "roughly flat." if pct >= 0 else "shrinking."))

    roe = info.get("returnOnEquity")
    if roe is not None:
        pct = roe * 100
        points += 15 if pct >= 15 else 9 if pct >= 8 else 3 if pct > 0 else 0
        p.factors.append(f"Return on equity {pct:.0f}% - "
                         + ("high-quality compounder." if pct >= 15 else
                            "reasonable returns on capital." if pct >= 8 else
                            "weak returns on capital."))

    ratio = _fundamentals.debt_to_equity_ratio(info)
    if ratio is not None:
        points += 20 if ratio <= 0.5 else 14 if ratio <= 1.0 else 7 if ratio <= 2.0 else 0
        # Below 0.1x a single decimal collapses everything to "0.0x", which
        # hides the difference between almost no debt and none at all.
        shown = f"{ratio:.2f}x" if ratio < 0.1 else f"{ratio:.1f}x"
        p.factors.append(f"Debt to equity {shown} - "
                         + ("very little debt." if ratio <= 0.5 else
                            "manageable debt." if ratio <= 1.0 else
                            "carrying real debt - watch it if rates or sales turn."))

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("Durable business - fine to hold for a year or two." if p.score >= 70 else
              "Decent but with soft spots." if p.score >= 45 else
              "Shaky fundamentals for a long hold.")
    return p


def score_cost(econ: LeapEconomics, realized_vol_pct: Optional[float] = None,
               iv_percentile: Optional[float] = None) -> Pillar:
    """What the option actually costs you - the pillar most tools underweight.

    Three separate costs, and only the first is obvious:
      1. The time premium, annualized. Pure rent. Never recovered.
      2. Whether the implied volatility you are paying is above or below what
         the stock actually delivers. Buying at a premium to realized vol is a
         headwind on every single day you hold.
      3. The dividends the shares would have paid you and the calls will not.
    """
    p = Pillar(key="cost", label="Cost of the option", weight=DEFAULT_WEIGHTS["cost"])
    points = 0.0

    ann = econ.extrinsic_ann_pct
    points += _band(ann, [(3, 40), (5, 33), (8, 24), (12, 14), (16, 6)], 0)
    p.factors.append(
        f"Time premium costs {ann:.1f}% of the share price per year "
        f"(${econ.extrinsic:.2f} per share over {econ.dte} days). "
        + ("Cheap rent." if ann <= 5 else "Reasonable." if ann <= 8 else
           "Expensive - the stock must work hard just to cover it." if ann <= 12 else
           "Very expensive. This is where LEAPS quietly lose money."))

    if econ.iv and realized_vol_pct:
        ratio = (econ.iv * 100) / realized_vol_pct
        points += _band(ratio, [(0.90, 35), (1.05, 27), (1.20, 17), (1.40, 8)], 0)
        p.factors.append(
            f"Implied volatility {econ.iv * 100:.0f}% versus {realized_vol_pct:.0f}% "
            f"actually realized ({ratio:.2f}x). "
            + ("You are paying less than this stock has been moving - a tailwind."
               if ratio <= 0.95 else
               "Roughly fair." if ratio <= 1.05 else
               "You are paying up for volatility the stock has not been delivering."))
    else:
        points += 17
        p.factors.append("No implied volatility on the feed - cannot check whether the "
                         "option is priced above or below what the stock actually does.")

    if iv_percentile is not None:
        points += _band(iv_percentile, [(25, 15), (50, 11), (75, 5)], 0)
        p.factors.append(
            f"Implied volatility sits at the {iv_percentile:.0f}th percentile of its "
            f"own past year. "
            + ("Options are cheap by their own standards - a good time to buy them."
               if iv_percentile <= 30 else
               "Middle of the range." if iv_percentile <= 60 else
               "Options are near their most expensive of the year. Buying here means "
               "paying peak premium."))
    else:
        points += 8

    give_up = econ.dividend_yield_pct
    points += _band(give_up, [(0.01, 10), (1.5, 8), (3.0, 4), (5.0, 1)], 0)
    if give_up > 0.01:
        p.factors.append(
            f"The shares pay {give_up:.1f}% a year in dividends and the call pays you "
            f"nothing - that is {econ.dividend_give_up_pct:.1f}% given up over the life "
            "of this contract, on top of the time premium.")
    else:
        p.factors.append("No dividend, so holding calls instead of shares costs you "
                         "nothing on that front.")

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = (f"Cheap to own - all in about {econ.all_in_cost_ann_pct:.1f}% a year."
              if p.score >= 70 else
              f"Fair price - all in about {econ.all_in_cost_ann_pct:.1f}% a year."
              if p.score >= 45 else
              f"Expensive - all in about {econ.all_in_cost_ann_pct:.1f}% a year before "
              "the stock does anything.")
    return p


def score_odds(econ: LeapEconomics, base: Optional[BaseRate],
               implied_prob: Optional[float] = None) -> Pillar:
    """Do the odds and the leverage justify the risk of a total loss?"""
    p = Pillar(key="odds", label="Odds and leverage", weight=DEFAULT_WEIGHTS["odds"])
    points = 0.0

    if base and base.hit_rate is not None:
        points += _band(-base.hit_rate, [(-70, 45), (-60, 37), (-50, 28), (-40, 16), (-30, 7)], 0)
        p.factors.append(
            f"It needs {econ.required_move_pct:+.1f}% in {econ.dte} days to break even. "
            f"Over {base.years_used:.0f} years of its own history this stock managed that "
            f"in {base.hit_rate:.0f}% of comparable stretches "
            f"(a typical stretch returned {base.median_pct:+.1f}%).")
        if base.loss_rate is not None:
            p.factors.append(
                f"In {base.loss_rate:.0f}% of those stretches it finished below "
                f"${econ.strike:.2f} - where this contract expires worthless.")
    else:
        points += 20
        p.factors.append(f"Needs {econ.required_move_pct:+.1f}% in {econ.dte} days to break "
                         "even. Not enough history to say how often it has done that.")

    if implied_prob is not None:
        p.factors.append(f"The option market's own maths puts the odds of finishing above "
                         f"breakeven at about {implied_prob:.0f}%.")

    lev = econ.leverage
    if lev:
        if lev >= 5:
            points += 8
            p.factors.append(f"{lev:.1f}x exposure per dollar - that is lottery-ticket "
                             "territory, not stock replacement.")
        elif lev >= 2.0:
            points += 30
            p.factors.append(f"{lev:.1f}x exposure per dollar versus owning shares - "
                             "solid stock-replacement leverage.")
        elif lev >= 1.4:
            points += 22
            p.factors.append(f"{lev:.1f}x exposure per dollar - mild leverage.")
        else:
            points += 8
            p.factors.append(f"Only {lev:.1f}x exposure per dollar - you are tying up "
                             "nearly as much cash as the shares would need, for less "
                             "safety. Consider just buying the stock.")

    drop = abs(econ.total_loss_drop_pct)
    points += _band(-drop, [(-30, 25), (-20, 20), (-12, 13), (-6, 6)], 2)
    p.factors.append(
        f"A {drop:.0f}% fall to ${econ.total_loss_price:.2f} wipes this contract out "
        f"completely (-${econ.max_loss:,.0f}), while a shareholder would be down "
        f"only {drop:.0f}%. That asymmetry is the whole risk of the strategy.")

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("The odds and the leverage line up." if p.score >= 70 else
              "Playable, but the required move is not a gimme." if p.score >= 45 else
              "The move it needs is one this stock rarely makes.")
    return p


# ------------------------------------------------------------------ assembly
# A pillar at or below this is not a weak spot, it is a reason not to trade.
CRITICAL_FLOOR = 35.0
# The best overall score a candidate can show while one of those is failing.
CAPPED_SCORE = 55.0
# Cost and odds are the two that can sink a LEAP on their own. A wonderful
# company bought at a terrible price is still a losing trade, and a plain
# average lets three strong pillars bury that.
CRITICAL_PILLARS = ("cost", "odds")


def blend(pillars: list[Pillar]) -> float:
    """Weighted score over the pillars we could actually measure."""
    live = [p for p in pillars if p.measured]
    total_weight = sum(p.weight for p in live)
    if total_weight <= 0:
        return 0.0
    return round(sum(p.score * p.weight for p in live) / total_weight, 1)


def failing_pillars(pillars: list[Pillar]) -> list[Pillar]:
    """The critical pillars that are failing outright, worst first."""
    bad = [p for p in pillars
           if p.measured and p.key in CRITICAL_PILLARS and p.score <= CRITICAL_FLOOR]
    return sorted(bad, key=lambda p: p.score)


def apply_gate(score: float, pillars: list[Pillar]) -> float:
    """Hold the headline score down while a make-or-break pillar is failing.

    Weighting cost and odds at 45% was meant to stop a great company with
    terrible option pricing scoring well. It does not quite manage it: a stock
    can score 100 on trend, 94 on quality and still carry a cost pillar of 22 -
    "very expensive, this is where LEAPS quietly lose money" - and the average
    lands near 74, which reads as a green light.

    So a failing critical pillar caps the headline instead of merely dragging on
    it. Ranking still works below the cap, and the reason is always shown.
    """
    return min(score, CAPPED_SCORE) if failing_pillars(pillars) else score


def share_comparison(econ: LeapEconomics, spot: float) -> ShareComparison:
    cost = econ.cost_dollars
    cmp_ = ShareComparison(
        shares_for_same_cash=round(cost / spot, 1) if spot > 0 else 0.0,
        share_cost=round(spot * 100, 2),
        leverage=econ.leverage,
        leap_max_loss=econ.max_loss,
        shares_loss_at_total_loss=round(abs(econ.total_loss_drop_pct) / 100 * spot * 100, 2),
    )
    if econ.leverage >= 2.0:
        cmp_.verdict = (
            f"${cost:,.0f} buys you the upside of about {econ.leverage:.1f}x that much "
            f"stock. The same cash would buy only {cmp_.shares_for_same_cash:.0f} shares "
            f"outright. In exchange, a drop to ${econ.total_loss_price:.2f} costs you "
            f"everything, where a shareholder would be down "
            f"${cmp_.shares_loss_at_total_loss:,.0f} and still own the stock.")
    else:
        cmp_.verdict = (
            f"At ${cost:,.0f} for {econ.leverage:.1f}x exposure, this contract is not "
            f"giving you much leverage for the risk. 100 shares cost ${cmp_.share_cost:,.0f} "
            "and can never expire worthless. Buying the stock may simply be better here.")
    return cmp_


def score_setup(symbol: str, closes: list[float], volumes: Optional[list[float]] = None,
                market_cap: Optional[float] = None, info: Optional[dict] = None
                ) -> LeapsCandidate:
    """Stage one: rank the whole universe on price action alone.

    This runs off one batched history download for hundreds of names, so it
    deliberately uses no option data - fetching a chain per stock would take
    many minutes. The chart pillars are real; Cost and Odds come later, once
    she picks a name worth pricing.
    """
    info = info or {}
    candidate = LeapsCandidate(symbol=symbol.upper(), stage="setup",
                               name=info.get("shortName") or info.get("longName") or "",
                               sector=info.get("sector") or "",
                               market_cap=market_cap or info.get("marketCap"))
    if not closes:
        candidate.summary = f"No price history for {candidate.symbol}."
        return candidate

    candidate.price = closes[-1]
    window = closes[-TRADING_DAYS_YEAR:] if len(closes) >= TRADING_DAYS_YEAR else closes
    candidate.high_52w = max(window)
    if candidate.high_52w:
        candidate.pct_off_52w_high = (candidate.price / candidate.high_52w - 1) * 100
    candidate.sma50, candidate.sma200 = sma(closes, 50), sma(closes, 200)
    candidate.rsi = rsi(closes)
    weekly = weekly_closes(closes)
    candidate.weekly_k, candidate.weekly_d = stochastic(weekly)
    rv = realized_vol(closes)
    candidate.realized_vol_pct = round(rv * 100, 1) if rv else None
    if volumes:
        recent = [v for v in volumes[-30:] if v]
        candidate.avg_volume = sum(recent) / len(recent) if recent else None

    trend = score_trend(closes)
    entry = score_entry(closes)
    quality = score_quality(info, market_cap)
    candidate.pillars = [trend, entry, quality]
    candidate.score = blend(candidate.pillars)
    candidate.headline = _headline(candidate)
    candidate.summary = (
        f"Setup score only - trend and entry from the chart"
        + (", quality from fundamentals" if quality.measured else "")
        + ". Price the actual contract to score what it costs and the odds it needs.")
    return candidate


def score_full(candidate: LeapsCandidate, chain: Optional[OptionChain],
               closes: list[float], info: Optional[dict] = None,
               target_delta: float = DEFAULT_TARGET_DELTA,
               iv_percentile: Optional[float] = None) -> LeapsCandidate:
    """Stage two: price the real contract and finish the scorecard."""
    info = info or {}
    spot = candidate.price or (chain.underlying_price if chain else 0.0)
    if not chain or not spot:
        candidate.flags.append("No option chain available - showing the chart score only.")
        return candidate

    contract = pick_contract(chain, target_delta)
    if contract is None:
        candidate.flags.append("No long-dated calls found for this symbol.")
        return candidate

    econ = economics(contract, spot, info)
    candidate.econ = econ
    candidate.iv_percentile = iv_percentile
    candidate.stage = "full"

    base = historical_base_rate(closes, econ.dte, econ.required_move_pct,
                                strike_drop_pct=econ.total_loss_drop_pct)
    candidate.base_rate = base

    implied = probability_above(spot, econ.breakeven, econ.dte,
                                econ.iv or 0.0) if econ.iv else None

    quality = candidate.pillar("quality")
    if quality is None or not quality.measured:
        quality = score_quality(info, candidate.market_cap)

    trend = candidate.pillar("trend") or score_trend(closes)
    entry = candidate.pillar("entry") or score_entry(closes)
    cost = score_cost(econ, candidate.realized_vol_pct, iv_percentile)
    odds = score_odds(econ, base, implied)

    candidate.pillars = [trend, entry, quality, cost, odds]
    candidate.raw_score = blend(candidate.pillars)
    candidate.score = apply_gate(candidate.raw_score, candidate.pillars)
    candidate.gated = candidate.score < candidate.raw_score
    candidate.comparison = share_comparison(econ, spot)
    candidate.strike_ladder = strike_ladder(chain, spot, contract.dte, info)
    candidate.headline = _headline(candidate)
    candidate.summary = _summary(candidate)

    # The hard contract rules come first. Everything below is a judgement call;
    # these are the ones her SOP simply does not bend on, so if the board had
    # nothing compliant and we fell back to the nearest contract, she reads that
    # before she reads the score.
    broken = breaches(contract, target_delta)
    if broken:
        candidate.flags.append(
            "This contract does not meet your SOP - it is the closest one listed, "
            "not a valid pick.")
        candidate.flags.extend(broken)

    # Separate from the breaches above, and deliberately so: this one is a
    # remark, not a rule. Nothing refuses the trade over it.
    note = spread_note(contract)
    if note:
        candidate.flags.append(note)

    if econ.liquidity == "Thin":
        candidate.flags.append(
            "Thin option - wide spread or little open interest. You will lose real money "
            "on the fill and may struggle to sell it later.")
    if candidate.days_to_earnings is not None and candidate.days_to_earnings <= 14:
        candidate.flags.append(
            f"Earnings in {candidate.days_to_earnings} days. For a {econ.dte}-day hold "
            "one report is not a reason to skip the trade, but it can move the price "
            "before you have any cushion.")
    if econ.extrinsic_ann_pct > 12:
        candidate.flags.append(
            f"Time premium is running at {econ.extrinsic_ann_pct:.0f}% a year. The stock "
            "has to beat that before you make a cent.")
    return candidate


def _headline(c: LeapsCandidate) -> str:
    live = [p for p in c.pillars if p.measured]
    if not live:
        return ""
    best = max(live, key=lambda p: p.score)
    worst = min(live, key=lambda p: p.score)
    if best.key == worst.key:
        base = f"{best.label} {best.score:.0f}/100."
    else:
        base = (f"Strongest on {best.label.lower()} ({best.score:.0f}), "
                f"weakest on {worst.label.lower()} ({worst.score:.0f}).")
    if c.gated:
        failing = failing_pillars(c.pillars)
        names = " and ".join(p.label.lower() for p in failing)
        base += (f" Held to {CAPPED_SCORE:.0f} because {names} "
                 f"{'are' if len(failing) > 1 else 'is'} failing - it would have "
                 f"scored {c.raw_score:.0f} on the average alone.")
    return base


def _summary(c: LeapsCandidate) -> str:
    econ, base = c.econ, c.base_rate
    if not econ:
        return c.summary
    parts = [
        f"The ${econ.strike:.0f} call expiring {econ.expiration} ({econ.dte} days) costs "
        f"${econ.cost_dollars:,.0f} and breaks even at ${econ.breakeven:.2f}, "
        f"{econ.required_move_pct:+.1f}% above today."
    ]
    if base and base.hit_rate is not None:
        parts.append(f"This stock has cleared that in {base.hit_rate:.0f}% of comparable "
                     f"stretches over the past {base.years_used:.0f} years.")
    parts.append(f"All in, you are paying about {econ.all_in_cost_ann_pct:.1f}% a year in "
                 "time premium and forgone dividends.")
    if c.score >= 70:
        parts.append("The scorecard likes this one.")
    elif c.score >= 50:
        parts.append("A reasonable candidate with real trade-offs - read the weak pillar.")
    else:
        parts.append("The scorecard does not like this one. Read the weak pillars before "
                     "you talk yourself into it.")
    return " ".join(parts)


# ------------------------------------------------------------------ scanning
def passes(candidate: LeapsCandidate, f: Filters) -> bool:
    """Apply the scan criteria. Anything we could not measure does not exclude."""
    c = candidate
    if f.min_market_cap_b and c.market_cap and c.market_cap < f.min_market_cap_b * 1e9:
        return False
    if f.min_avg_volume_m and c.avg_volume and c.avg_volume < f.min_avg_volume_m * 1e6:
        return False
    if f.sector and f.sector != "All sectors" and c.sector and c.sector != f.sector:
        return False
    if f.require_above_200dma and c.sma200 and c.price and c.price < c.sma200:
        return False
    if f.require_above_50dma and c.sma50 and c.price and c.price < c.sma50:
        return False
    if f.require_k_above_d and c.weekly_k is not None and c.weekly_d is not None \
            and c.weekly_k <= c.weekly_d:
        return False
    if c.weekly_k is not None and not (f.stoch_min <= c.weekly_k <= f.stoch_max):
        return False
    if c.pct_off_52w_high is not None and abs(c.pct_off_52w_high) > f.max_pct_off_high:
        return False
    if f.max_iv_percentile is not None and c.iv_percentile is not None \
            and c.iv_percentile > f.max_iv_percentile:
        return False
    if f.max_required_move_ann_pct is not None and c.econ \
            and c.econ.required_move_ann_pct > f.max_required_move_ann_pct:
        return False
    if f.min_base_rate is not None and c.base_rate and c.base_rate.hit_rate is not None \
            and c.base_rate.hit_rate < f.min_base_rate:
        return False
    if f.hide_earnings_within_days is not None and c.days_to_earnings is not None \
            and c.days_to_earnings <= f.hide_earnings_within_days:
        return False
    if c.score < f.min_score:
        return False
    if f.min_open_interest and c.econ and c.econ.open_interest \
            and c.econ.open_interest < f.min_open_interest:
        return False
    return True


def rank(candidates: Iterable[LeapsCandidate], f: Optional[Filters] = None
         ) -> list[LeapsCandidate]:
    """Filter, sort best-first, and stamp the rank onto each one."""
    f = f or Filters()
    kept = [c for c in candidates if passes(c, f)]
    kept.sort(key=lambda c: c.score, reverse=True)
    for i, c in enumerate(kept, start=1):
        c.rank = i
    return kept
