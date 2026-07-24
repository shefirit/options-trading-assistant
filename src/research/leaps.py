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

# How much each pillar counts toward the final score. They must sum to 1.0.
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "entry": 0.15,
    "quality": 0.20,
    "cost": 0.25,
    "odds": 0.20,
}

TRADING_DAYS_YEAR = 252
# What we treat as a LEAP: at least this many days to expiration.
MIN_LEAP_DTE = 300
# The strike we default to. 0.70-0.80 delta is the usual stock-replacement zone:
# deep enough that the option moves nearly like the shares, shallow enough that
# you are not tying up almost the whole share price.
DEFAULT_TARGET_DELTA = 0.75


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
    min_open_interest: int = 100
    sector: str = "All sectors"
    profitable_only: bool = True

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
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def weekly_closes(closes: list[float]) -> list[float]:
    """Squash daily closes into weekly ones (every 5th trading day, last first).

    Good enough for a weekly oscillator and it means the whole-market scan can
    run off the same batched daily download rather than a second request.
    """
    if not closes:
        return []
    weeks = [closes[i:i + 5] for i in range(0, len(closes), 5)]
    return [w[-1] for w in weeks if w]


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
    """Annualized realized volatility from daily closes, as a decimal (0.28)."""
    series = closes[-(lookback + 1):]
    if len(series) < 30:
        return None
    rets = []
    for i in range(1, len(series)):
        if series[i - 1] > 0:
            rets.append(math.log(series[i] / series[i - 1]))
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


def dividend_yield_pct(info: dict) -> float:
    """Yahoo has shipped this both as a fraction (0.0053) and as a percent
    (0.53) depending on the version, so normalize. Nobody yields 25%."""
    raw = info.get("dividendYield") or info.get("trailingAnnualDividendYield") or 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return value if value > 0.25 else value * 100.0


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

    result.read = (
        f"Over {result.years_used:.0f} years, this stock cleared {required_pct:+.1f}% in "
        f"{result.hit_rate:.0f}% of {horizon_days}-day stretches. A typical stretch "
        f"returned {result.median_pct:+.1f}%."
    )
    return result


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
def pick_contract(chain: OptionChain, target_delta: float = DEFAULT_TARGET_DELTA,
                  min_dte: int = MIN_LEAP_DTE) -> Optional[OptionContract]:
    """The call closest to the target delta at the furthest-out expiration
    that still counts as a LEAP. Falls back to the longest expiration on the
    board when nothing reaches `min_dte`."""
    calls = [c for c in chain.contracts
             if c.option_type == OptionType.CALL and c.mid > 0]
    if not calls:
        return None
    long_enough = [c for c in calls if c.dte >= min_dte]
    pool = long_enough or calls
    best_dte = max(c.dte for c in pool)
    at_expiry = [c for c in pool if c.dte == best_dte]
    with_delta = [c for c in at_expiry if c.abs_delta > 0]
    if with_delta:
        return min(with_delta, key=lambda c: abs(c.abs_delta - target_delta))
    # No greeks on the feed - approximate by moneyness instead. A 0.75 delta
    # call sits roughly 10-15% in the money on a year-out contract.
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

    econ.dividend_yield_pct = dividend_yield_pct(info)
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
            points += 10
            p.factors.append(f"Up {year_return:+.0f}% over the past year.")
        else:
            p.factors.append(f"Down {year_return:+.0f}% over the past year.")

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("Firm uptrend." if p.score >= 70 else
              "Mixed trend - not clearly up." if p.score >= 45 else
              "No uptrend to speak of. Paying for direction you do not have.")
    return p


def score_entry(closes: list[float], highs: Optional[list[float]] = None,
                lows: Optional[list[float]] = None) -> Pillar:
    """Is this a decent spot to buy, or are you chasing?"""
    p = Pillar(key="entry", label="Entry timing", weight=DEFAULT_WEIGHTS["entry"])
    if len(closes) < 60:
        p.measured, p.read = False, "Not enough price history to judge the entry."
        return p

    price = closes[-1]
    window = closes[-TRADING_DAYS_YEAR:] if len(closes) >= TRADING_DAYS_YEAR else closes
    high52 = max(window)
    off_high = (price / high52 - 1) * 100 if high52 > 0 else 0.0
    points = 0.0

    # A shallow pullback in an uptrend is the sweet spot. At the very highs you
    # are paying up; far below them the trend is usually already broken.
    drop = abs(off_high)
    if drop <= 2:
        points += 22
        p.factors.append(f"Sitting right at its 52-week high ({off_high:.0f}%) - "
                         "buying strength, but you are paying full price.")
    elif drop <= 12:
        points += 40
        p.factors.append(f"{drop:.0f}% below the 52-week high - a shallow pullback "
                         "inside an uptrend, the classic spot.")
    elif drop <= 20:
        points += 28
        p.factors.append(f"{drop:.0f}% below the high - a deeper dip. Fine if the "
                         "trend holds, riskier if it does not.")
    elif drop <= 30:
        points += 12
        p.factors.append(f"{drop:.0f}% below the high - the chart has taken real damage.")
    else:
        p.factors.append(f"{drop:.0f}% below the high - this is a broken chart, not a dip.")

    value = rsi(closes)
    if value is not None:
        if value >= 80:
            p.factors.append(f"RSI {value:.0f} - very overbought. Poor spot to start a position.")
        elif value >= 70:
            points += 10
            p.factors.append(f"RSI {value:.0f} - overbought but that can persist in a strong trend.")
        elif value >= 45:
            points += 25
            p.factors.append(f"RSI {value:.0f} - healthy, not stretched either way.")
        elif value >= 30:
            points += 18
            p.factors.append(f"RSI {value:.0f} - soft. Wait for it to turn up if you can.")
        else:
            points += 8
            p.factors.append(f"RSI {value:.0f} - deeply oversold. Cheap, but catching a "
                             "falling knife with a time limit attached.")

    weekly = weekly_closes(closes)
    wk, wd = stochastic(weekly, weekly_closes(highs) if highs else None,
                        weekly_closes(lows) if lows else None)
    if wk is not None and wd is not None:
        if 30 <= wk <= 80 and wk > wd:
            points += 35
            p.factors.append(f"Weekly stochastic {wk:.0f} and turning up through its "
                             "signal line - momentum is rebuilding.")
        elif wk > wd:
            points += 24
            p.factors.append(f"Weekly stochastic {wk:.0f}, above its signal line.")
        elif wk >= 85:
            points += 8
            p.factors.append(f"Weekly stochastic {wk:.0f} - near the top of its range "
                             "and rolling over.")
        else:
            points += 12
            p.factors.append(f"Weekly stochastic {wk:.0f}, below its signal line - "
                             "momentum still falling.")

    p.score = min(100.0, points)
    p.status = "good" if p.score >= 70 else "ok" if p.score >= 45 else "watch"
    p.read = ("Good spot to start a position." if p.score >= 70 else
              "Workable entry, not ideal." if p.score >= 45 else
              "Poor entry - either stretched or already broken.")
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

    debt = info.get("debtToEquity")      # Yahoo ships this as a percent (150 = 1.5x)
    if debt is not None:
        ratio = float(debt) / 100.0 if float(debt) > 5 else float(debt)
        points += 20 if ratio <= 0.5 else 14 if ratio <= 1.0 else 7 if ratio <= 2.0 else 0
        p.factors.append(f"Debt to equity {ratio:.1f}x - "
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
def blend(pillars: list[Pillar]) -> float:
    """Weighted score over the pillars we could actually measure."""
    live = [p for p in pillars if p.measured]
    total_weight = sum(p.weight for p in live)
    if total_weight <= 0:
        return 0.0
    return round(sum(p.score * p.weight for p in live) / total_weight, 1)


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
    candidate.score = blend(candidate.pillars)
    candidate.comparison = share_comparison(econ, spot)
    candidate.strike_ladder = strike_ladder(chain, spot, contract.dte, info)
    candidate.headline = _headline(candidate)
    candidate.summary = _summary(candidate)

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
        return f"{best.label} {best.score:.0f}/100."
    return (f"Strongest on {best.label.lower()} ({best.score:.0f}), "
            f"weakest on {worst.label.lower()} ({worst.score:.0f}).")


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
