"""Is this name a good candidate for a credit spread right now - and which side?

One question, answered in layers. Each layer measures one thing, says what it
found in plain English, and pushes points toward the put side, the call side,
or neither. Nothing here predicts. Every layer describes what has already
happened or what options are charging today.

The layers, and why each one is here:

  1. Structure    Higher highs and higher lows, or lower and lower. The oldest
                  definition of a trend there is, and the only one that reads
                  price itself rather than an average of it.
  2. Averages     Where price sits against its 50- and 200-day averages. Slower
                  than structure and far harder to argue with.
  3. Conviction   Volume on down days against volume on up days. A quiet
                  pullback is buyers resting; a loud one is sellers arriving.
  4. Rel strength This name against the broad market. Rising with everything
                  else is not the same as rising on its own merit.
  5. Volatility   IV Rank - how expensive options are against their own year.
                  This is the entry condition, not a tiebreaker: selling cheap
                  premium is the quiet way to lose at this.
  6. Market       The fear gauge. Above her stop level nothing else matters.
  7. Events       Earnings inside the trade window is a scheduled coin flip.
  8. Tradability  Can she actually get filled, and does the credit clear her
                  minimum? The call side often fails this where the put side
                  passes, which is skew doing exactly what skew does.

Pure functions only - no network, no Streamlit, no pandas - so the whole thing
is unit tested against hand-worked numbers.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from pydantic import BaseModel, Field

from src.data.chain import OptionChain
from src.engine.models import OptionType

PUT = "put"
CALL = "call"

# What a layer can be worth. Structure leads because it reads price directly;
# volatility matches it because a rich premium is the reason to be here at all.
W_STRUCTURE = 2.0
W_AVERAGES = 1.5
W_VOLATILITY = 1.5
W_CONVICTION = 1.0
W_STRENGTH = 1.0
W_MARKET = 1.0

# Verdict cutoffs, applied to the score a side actually earned.
GOOD = 4.0
WORKABLE = 1.5
POOR = -1.0


class Layer(BaseModel):
    key: str
    label: str
    value: str                       # what we measured, already formatted
    read: str                        # what it means, in plain English
    status: str = "ok"               # "good" | "ok" | "watch" | "bad" | "unknown"
    put_points: float = 0.0
    call_points: float = 0.0
    # Sides this layer rules out outright, whatever the score says.
    blocks: list[str] = Field(default_factory=list)
    # The most this layer could have contributed. Used so unknown layers lower
    # the confidence rather than silently counting as a negative.
    weight: float = 0.0

    @property
    def known(self) -> bool:
        return self.status != "unknown"


class SideVerdict(BaseModel):
    side: str                        # "put" | "call"
    name: str
    score: float = 0.0
    max_score: float = 0.0           # the most the KNOWN layers could have given
    verdict: str = "Poor fit"
    tone: str = "amber"              # "green" | "amber" | "red"
    headline: str = ""
    reasons: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def fit_pct(self) -> Optional[int]:
        """Score as a share of what was actually gradeable. None when nothing
        could be graded - which is not the same as a zero."""
        if self.max_score <= 0:
            return None
        return max(0, min(100, round((self.score / self.max_score) * 100)))


class ChainRead(BaseModel):
    """What the option chain says about actually trading this, at the width and
    deltas her rules ask for."""

    dte: Optional[int] = None
    rel_spread: Optional[float] = None      # median (ask-bid)/mid in the sell zone
    open_interest: Optional[int] = None     # median open interest in the sell zone
    width: Optional[float] = None
    put_credit_pct: Optional[float] = None  # credit as a fraction of the width
    call_credit_pct: Optional[float] = None
    put_short_strike: Optional[float] = None
    call_short_strike: Optional[float] = None


class CandidateReport(BaseModel):
    symbol: str
    kind: str = "stock"              # "index" | "etf" | "stock"
    price: Optional[float] = None
    as_of: dt.date
    layers: list[Layer] = Field(default_factory=list)
    put_side: SideVerdict
    call_side: SideVerdict
    best: str = "neither"            # "put" | "call" | "both" | "neither"
    summary: str = ""
    vol_source: str = ""             # which volatility source answered
    data_gaps: list[str] = Field(default_factory=list)

    @property
    def graded(self) -> int:
        return sum(1 for lay in self.layers if lay.known)


# --------------------------------------------------------------- price shapes
def pivots(values: list[float], k: int = 3, kind: str = "high") -> list[tuple[int, float]]:
    """Turning points: a bar that is the highest (or lowest) of the k bars each
    side of it. k=3 ignores day-to-day wiggle and keeps the swings you would
    actually mark on a chart by hand."""
    out: list[tuple[int, float]] = []
    if k < 1 or len(values) < 2 * k + 1:
        return out
    for i in range(k, len(values) - k):
        window = values[i - k:i + k + 1]
        centre = values[i]
        if kind == "high" and centre >= max(window):
            out.append((i, centre))
        elif kind == "low" and centre <= min(window):
            out.append((i, centre))
    # Neighbouring bars can both qualify on a flat top - keep the last of a run.
    pruned: list[tuple[int, float]] = []
    for item in out:
        if pruned and item[0] - pruned[-1][0] <= k:
            pruned[-1] = item
        else:
            pruned.append(item)
    return pruned


def _step(prev: float, last: float, tol: float) -> int:
    """+1 higher, -1 lower, 0 level. The tolerance matters: a range whose peaks
    come in a few cents apart is a flat range, and without a band it reads as a
    downtrend on a one-cent difference."""
    if prev <= 0:
        return 0
    if last > prev * (1 + tol):
        return 1
    if last < prev * (1 - tol):
        return -1
    return 0


def swing_structure(highs: list[float], lows: list[float], k: int = 3,
                    tol: float = 0.005) -> tuple[str, str]:
    """The Dow Theory read: (direction, what we saw).

    Higher highs AND higher lows is an uptrend. Lower highs AND lower lows is a
    downtrend. Anything else - including a flat range where the peaks match - is
    sideways, which is a real answer and not a failure to decide.
    """
    ph = pivots(highs, k, "high")
    pl = pivots(lows, k, "low")
    if len(ph) < 2 or len(pl) < 2:
        return "unknown", "Not enough swing highs and lows yet to read structure."

    prev_high, last_high = ph[-2][1], ph[-1][1]
    prev_low, last_low = pl[-2][1], pl[-1][1]
    h = _step(prev_high, last_high, tol)
    lo = _step(prev_low, last_low, tol)

    words = {1: "higher", -1: "lower", 0: "level"}
    detail = (f"Last two peaks {prev_high:,.2f} then {last_high:,.2f} ({words[h]}); "
              f"last two troughs {prev_low:,.2f} then {last_low:,.2f} ({words[lo]}).")

    if h > 0 and lo > 0:
        return "up", detail
    if h < 0 and lo < 0:
        return "down", detail
    return "sideways", detail


def sma(values: list[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def ma_stack(closes: list[float]) -> dict:
    """Price against its 50- and 200-day averages, and whether they are rising.

    Slope is measured over 20 bars, which is long enough that a single day
    cannot flip it and short enough to turn within a trade's lifetime.
    """
    out: dict = {"price": closes[-1] if closes else None,
                 "ma50": None, "ma200": None,
                 "ma50_rising": None, "ma200_rising": None, "golden": None}
    out["ma50"] = sma(closes, 50)
    out["ma200"] = sma(closes, 200)
    if len(closes) >= 70:
        prev = sma(closes[:-20], 50)
        if prev is not None and out["ma50"] is not None:
            out["ma50_rising"] = out["ma50"] > prev
    if len(closes) >= 220:
        prev = sma(closes[:-20], 200)
        if prev is not None and out["ma200"] is not None:
            out["ma200_rising"] = out["ma200"] > prev
    if out["ma50"] is not None and out["ma200"] is not None:
        out["golden"] = out["ma50"] > out["ma200"]
    return out


def volume_character(closes: list[float], volumes: list[float],
                     lookback: int = 20) -> Optional[float]:
    """Average volume on down days divided by average volume on up days.

    Under 1 means the selling is quieter than the buying - the signature of a
    pullback inside a trend that is still working. Over 1 means the down days
    are the busy ones, which is what a handover from buyers to sellers looks
    like. Returns None when there are not enough of both kinds of day to
    compare, because one down day is not a measurement.
    """
    if len(closes) < lookback + 1 or len(volumes) != len(closes):
        return None
    ups, downs = [], []
    for i in range(len(closes) - lookback, len(closes)):
        if closes[i] > closes[i - 1]:
            ups.append(volumes[i])
        elif closes[i] < closes[i - 1]:
            downs.append(volumes[i])
    if len(ups) < 3 or len(downs) < 3:
        return None
    up_avg = sum(ups) / len(ups)
    if up_avg <= 0:
        return None
    return round((sum(downs) / len(downs)) / up_avg, 2)


def relative_strength(closes: list[float], bench: list[float],
                      days: int = 63) -> Optional[float]:
    """How far this name beat (or trailed) the benchmark over roughly a quarter,
    in percentage points."""
    if len(closes) < days + 1 or len(bench) < days + 1:
        return None
    if closes[-days - 1] <= 0 or bench[-days - 1] <= 0:
        return None
    mine = (closes[-1] / closes[-days - 1] - 1) * 100
    theirs = (bench[-1] / bench[-days - 1] - 1) * 100
    return round(mine - theirs, 1)


def realized_vol_series(closes: list[float], window: int = 30) -> list[float]:
    """Rolling annualized volatility of daily returns - what the stock actually
    did, one value per bar once there is a full window behind it."""
    if len(closes) < window + 2:
        return []
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    out: list[float] = []
    for i in range(window, len(rets) + 1):
        w = rets[i - window:i]
        mean = sum(w) / len(w)
        var = sum((r - mean) ** 2 for r in w) / (len(w) - 1)
        out.append(math.sqrt(var) * math.sqrt(252) * 100)
    return out


def rank_in_range(series: list[float], lookback: int = 252) -> Optional[float]:
    """The IV Rank formula, applied to whatever series you hand it: where the
    latest value sits between the lowest and highest of the last year, 0 to 100.

    This is Barchart's and tastytrade's definition of IV Rank, so a rank built
    here from another series is directly comparable in shape - though not in
    meaning, which is why the caller has to say which series it used.
    """
    if len(series) < 30:
        return None
    window = series[-lookback:]
    lo, hi = min(window), max(window)
    if hi <= lo:
        return None
    return round((window[-1] - lo) / (hi - lo) * 100, 1)


# ----------------------------------------------------------------- the chain
def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def read_chain(chain: Optional[OptionChain], dte: Optional[int], width: float,
               put_delta: float = 0.25, call_delta: float = 0.10,
               zone: tuple[float, float] = (0.05, 0.35)) -> ChainRead:
    """What the chain says about trading this: how tight the market is where she
    would sell, and what credit each side actually pays at her width.

    The credit figures are the honest test. A call spread at 0.10 delta on a
    name with heavy put skew frequently cannot clear a 6%-of-width minimum
    while the put spread at 0.25 clears it comfortably, and no amount of
    chart-reading changes that.
    """
    out = ChainRead(width=width)
    if chain is None:
        return out
    use_dte = dte if dte in (chain.dtes() or []) else chain.nearest_dte(dte or 45)
    if use_dte is None:
        return out
    out.dte = use_dte

    lo, hi = zone
    spreads: list[float] = []
    ois: list[int] = []
    for kind in (OptionType.PUT, OptionType.CALL):
        for c in chain.by(kind, use_dte):
            if not (lo <= c.abs_delta <= hi):
                continue
            mid = c.mid
            if mid and mid > 0 and c.ask > 0 and c.bid >= 0:
                spreads.append((c.ask - c.bid) / mid)
            if c.open_interest:
                ois.append(c.open_interest)
    out.rel_spread = round(_median(spreads), 3) if spreads else None
    med_oi = _median([float(o) for o in ois])
    out.open_interest = int(med_oi) if med_oi is not None else None

    def side(kind: OptionType, target: float, further: int) -> tuple[Optional[float],
                                                                    Optional[float]]:
        legs = [c for c in chain.by(kind, use_dte) if c.mid > 0]
        if not legs:
            return None, None
        short = min(legs, key=lambda c: abs(c.abs_delta - target))
        long_strike = short.strike + further * width
        protect = chain.find(kind, use_dte, long_strike)
        if protect is None:
            # Not every chain lists that exact strike - take the nearest one.
            protect = min(legs, key=lambda c: abs(c.strike - long_strike))
            if abs(protect.strike - long_strike) > width * 0.5:
                return None, short.strike
        credit = short.mid - protect.mid
        real_width = abs(short.strike - protect.strike)
        if real_width <= 0:
            return None, short.strike
        return round(credit / real_width, 4), short.strike

    out.put_credit_pct, out.put_short_strike = side(OptionType.PUT, put_delta, -1)
    out.call_credit_pct, out.call_short_strike = side(OptionType.CALL, call_delta, +1)
    return out


# ------------------------------------------------------------------ layers
def _pct(x: Optional[float], places: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{places}f}%"


def _unknown(key: str, label: str, why: str, weight: float) -> Layer:
    return Layer(key=key, label=label, value="not available", read=why,
                 status="unknown", weight=weight)


def structure_layer(highs: list[float], lows: list[float]) -> Layer:
    direction, detail = swing_structure(highs, lows)
    if direction == "unknown":
        return _unknown("structure", "Price structure", detail, W_STRUCTURE)
    if direction == "up":
        return Layer(key="structure", label="Price structure", value="Uptrend",
                     read="Higher highs and higher lows - the staircase is going up. "
                          f"A put spread sits under that. {detail}",
                     status="good", put_points=W_STRUCTURE, call_points=-W_STRUCTURE,
                     weight=W_STRUCTURE)
    if direction == "down":
        return Layer(key="structure", label="Price structure", value="Downtrend",
                     read="Lower highs and lower lows - the staircase is going down. "
                          f"Selling puts under a falling price is the hard way. {detail}",
                     status="watch", put_points=-W_STRUCTURE, call_points=W_STRUCTURE,
                     weight=W_STRUCTURE)
    return Layer(key="structure", label="Price structure", value="Sideways",
                 read="Peaks and troughs are mixed, so there is no trend to lean on. "
                      f"A range pays both sides, which is condor territory. {detail}",
                 status="ok", put_points=W_STRUCTURE * 0.25,
                 call_points=W_STRUCTURE * 0.25, weight=W_STRUCTURE)


def averages_layer(closes: list[float]) -> Layer:
    st = ma_stack(closes)
    price, ma50, ma200 = st["price"], st["ma50"], st["ma200"]
    if price is None or ma50 is None:
        return _unknown("averages", "Moving averages",
                        "Needs at least 50 days of price history.", W_AVERAGES)
    above50 = price > ma50
    above200 = (price > ma200) if ma200 is not None else None
    golden = st["golden"]
    rising = st["ma50_rising"]

    bits = [f"{price:,.2f} is {'above' if above50 else 'below'} its 50-day ({ma50:,.2f})"]
    if ma200 is not None:
        bits.append(f"and {'above' if above200 else 'below'} its 200-day ({ma200:,.2f})")
    if rising is not None:
        bits.append(f"The 50-day is {'rising' if rising else 'falling'}.")
    detail = ", ".join(bits[:2]) + ". " + (bits[2] if len(bits) > 2 else "")

    score = 0.0
    if above50:
        score += 0.5
    else:
        score -= 0.5
    if above200 is True:
        score += 0.5
    elif above200 is False:
        score -= 0.5
    if golden is True:
        score += 0.25
    elif golden is False:
        score -= 0.25
    if rising is True:
        score += 0.25
    elif rising is False:
        score -= 0.25
    points = round(score / 1.5 * W_AVERAGES, 2)

    if points >= W_AVERAGES * 0.6:
        value, status = "Stacked up", "good"
        read = f"The averages line up behind an uptrend. {detail}"
    elif points <= -W_AVERAGES * 0.6:
        value, status = "Stacked down", "watch"
        read = f"The averages line up behind a downtrend. {detail}"
    else:
        value, status = "Mixed", "ok"
        read = ("The averages disagree with each other, which is what sideways looks "
                f"like on this measure. {detail}")
    return Layer(key="averages", label="Moving averages", value=value, read=read,
                 status=status, put_points=points, call_points=-points,
                 weight=W_AVERAGES)


def conviction_layer(closes: list[float], volumes: list[float],
                     direction: str) -> Layer:
    ratio = volume_character(closes, volumes)
    if ratio is None:
        return _unknown("conviction", "Volume conviction",
                        "Needs 20 days of volume with both up and down days in it.",
                        W_CONVICTION)
    value = f"{ratio:.2f}x"
    quiet = ratio <= 0.90
    loud = ratio >= 1.15

    if direction == "up":
        if quiet:
            return Layer(key="conviction", label="Volume conviction", value=value,
                         read=f"Down days trade on {ratio:.2f} times the volume of up "
                              "days, so the selling is the quiet part. That is a "
                              "pullback resting inside an uptrend, not a handover.",
                         status="good", put_points=W_CONVICTION,
                         call_points=-W_CONVICTION * 0.5, weight=W_CONVICTION)
        if loud:
            return Layer(key="conviction", label="Volume conviction", value=value,
                         read=f"Down days are the busy ones ({ratio:.2f}x up-day "
                              "volume) while price still reads as an uptrend. That is "
                              "the pattern that precedes a turn - sellers arriving "
                              "before the chart admits it.",
                         status="watch", put_points=-W_CONVICTION,
                         call_points=W_CONVICTION * 0.5, weight=W_CONVICTION)
    elif direction == "down":
        if loud:
            return Layer(key="conviction", label="Volume conviction", value=value,
                         read=f"Down days carry {ratio:.2f} times the up-day volume. "
                              "The selling has real weight behind it, which confirms "
                              "the downtrend rather than fading it.",
                         status="watch", put_points=-W_CONVICTION,
                         call_points=W_CONVICTION, weight=W_CONVICTION)
        if quiet:
            return Layer(key="conviction", label="Volume conviction", value=value,
                         read=f"Price is falling but the down days are quiet "
                              f"({ratio:.2f}x). Selling without conviction often means "
                              "the slide is running out of sellers.",
                         status="ok", put_points=W_CONVICTION * 0.5,
                         call_points=-W_CONVICTION * 0.5, weight=W_CONVICTION)
    return Layer(key="conviction", label="Volume conviction", value=value,
                 read=f"Down-day volume is {ratio:.2f} times up-day volume - close "
                      "enough to even that it says nothing either way.",
                 status="ok", weight=W_CONVICTION)


def strength_layer(closes: list[float], bench: list[float],
                   bench_name: str = "SPY", is_benchmark: bool = False) -> Layer:
    if is_benchmark:
        # SPX, XSP and SPY are the broad market. Measuring them against it is
        # not a missing number, it is a question that does not apply.
        return Layer(key="strength", label="Relative strength", value="is the market",
                     read="This name IS the broad market, so there is nothing to "
                          "measure it against. The layer applies to individual "
                          "stocks and sector funds, not to the index itself.",
                     status="ok", weight=W_STRENGTH)
    rs = relative_strength(closes, bench)
    if rs is None:
        return _unknown("strength", "Relative strength",
                        "Needs about a quarter of history for both this name and the "
                        "market.", W_STRENGTH)
    value = f"{rs:+.1f} pts vs {bench_name}"
    if rs >= 5:
        return Layer(key="strength", label="Relative strength", value=value,
                     read=f"Over the last quarter it beat {bench_name} by {rs:.1f} "
                          "percentage points. It is rising on its own merit, not just "
                          "floating up with everything else.",
                     status="good", put_points=W_STRENGTH, call_points=-W_STRENGTH,
                     weight=W_STRENGTH)
    if rs <= -5:
        return Layer(key="strength", label="Relative strength", value=value,
                     read=f"It trailed {bench_name} by {abs(rs):.1f} points over the "
                          "quarter. A laggard falls first and furthest when the market "
                          "wobbles.",
                     status="watch", put_points=-W_STRENGTH, call_points=W_STRENGTH,
                     weight=W_STRENGTH)
    return Layer(key="strength", label="Relative strength", value=value,
                 read=f"It moved roughly in line with {bench_name} - no edge either "
                      "way from this.",
                 status="ok", weight=W_STRENGTH)


def volatility_layer(iv_rank: Optional[float], source: str,
                     iv: Optional[float] = None,
                     hv: Optional[float] = None) -> Layer:
    """The entry condition. Everything else decides WHICH side; this decides
    whether selling premium is worth doing here at all."""
    if iv_rank is None:
        return _unknown("volatility", "Volatility (IV Rank)",
                        "No implied-volatility history for this name. Import a "
                        "Barchart IV Rank export, or type the rank in by hand, to "
                        "grade this layer.", W_VOLATILITY)

    extra = ""
    if iv is not None and hv:
        ratio = iv / hv
        extra = (f" Options are pricing {iv:.0f}% against {hv:.0f}% actually "
                 f"delivered ({ratio:.2f}x)"
                 + (" - you are being paid more than the movement so far justifies."
                    if ratio >= 1.05 else
                    " - the premium is not ahead of the real movement."))

    value = f"{iv_rank:.0f} ({source})"
    if iv_rank >= 50:
        return Layer(key="volatility", label="Volatility (IV Rank)", value=value,
                     read=f"Options are expensive against their own year - IV Rank "
                          f"{iv_rank:.0f} out of 100. This is the condition premium "
                          f"selling exists for.{extra}",
                     status="good", put_points=W_VOLATILITY, call_points=W_VOLATILITY,
                     weight=W_VOLATILITY)
    if iv_rank >= 30:
        return Layer(key="volatility", label="Volatility (IV Rank)", value=value,
                     read=f"IV Rank {iv_rank:.0f} - options are dearer than usual but "
                          f"not remarkable. Workable, not a gift.{extra}",
                     status="ok", put_points=W_VOLATILITY * 0.5,
                     call_points=W_VOLATILITY * 0.5, weight=W_VOLATILITY)
    if iv_rank >= 20:
        return Layer(key="volatility", label="Volatility (IV Rank)", value=value,
                     read=f"IV Rank {iv_rank:.0f} - middling to cheap. The credit will "
                          f"be thin for the risk you carry.{extra}",
                     status="watch", weight=W_VOLATILITY)
    return Layer(key="volatility", label="Volatility (IV Rank)", value=value,
                 read=f"IV Rank {iv_rank:.0f} - options are near their cheapest of the "
                      "year. Selling premium here pays you least exactly where the "
                      f"risk is unchanged. This is the quiet way to lose.{extra}",
                 status="bad", put_points=-W_VOLATILITY, call_points=-W_VOLATILITY,
                 weight=W_VOLATILITY)


def market_layer(vix: Optional[float], vix_stop: float = 28.0,
                 zone: tuple[float, float] = (13.0, 25.0)) -> Layer:
    if vix is None:
        return _unknown("market", "Market conditions",
                        "Could not read the VIX right now.", W_MARKET)
    lo, hi = zone
    value = f"VIX {vix:.1f}"
    if vix >= vix_stop:
        return Layer(key="market", label="Market conditions", value=value,
                     read=f"VIX {vix:.0f} is at or past your sit-it-out level of "
                          f"{vix_stop:.0f}. Big fast swings go straight through short "
                          "strikes, and the extra premium does not cover that.",
                     status="bad", blocks=[PUT, CALL], weight=W_MARKET)
    if vix > hi:
        return Layer(key="market", label="Market conditions", value=value,
                     read=f"VIX {vix:.0f} is above your comfort zone top of {hi:.0f}. "
                          "Premiums are rich for a reason - keep size small.",
                     status="watch", put_points=-W_MARKET * 0.5,
                     call_points=-W_MARKET * 0.5, weight=W_MARKET)
    if vix < lo:
        return Layer(key="market", label="Market conditions", value=value,
                     read=f"VIX {vix:.0f} is below your comfort zone floor of "
                          f"{lo:.0f}. Calm is pleasant to trade in but the premium is "
                          "thin, so the credit has to be checked hard.",
                     status="watch", put_points=-W_MARKET * 0.5,
                     call_points=-W_MARKET * 0.5, weight=W_MARKET)
    return Layer(key="market", label="Market conditions", value=value,
                 read=f"VIX {vix:.0f} sits inside your {lo:.0f}-{hi:.0f} comfort zone.",
                 status="good", weight=W_MARKET)


def events_layer(earnings: Optional[dt.date], today: dt.date, dte_hi: int,
                 kind: str) -> Layer:
    if kind in ("index", "etf"):
        what = "An index" if kind == "index" else "An ETF"
        return Layer(key="events", label="Events in the window", value="None",
                     read=f"{what} has no earnings date - it holds hundreds of "
                          "companies, so no single report can gap it. Macro dates "
                          "like FOMC still apply, and the Market tab tracks those.",
                     status="good")
    if earnings is None:
        return _unknown("events", "Events in the window",
                        "No earnings date found. Check it yourself before entering - "
                        "a missing date is not the same as a clear calendar.", 0.0)
    days = (earnings - today).days
    if 0 <= days <= dte_hi:
        return Layer(key="events", label="Events in the window",
                     value=f"Earnings {earnings:%b %d} ({days}d)",
                     read=f"Earnings lands {days} days out, inside the {dte_hi}-day "
                          "window you would be trading. That is a scheduled coin flip "
                          "that ignores every other layer here. Pick an expiration "
                          "before it, or wait until after.",
                     status="bad", blocks=[PUT, CALL])
    return Layer(key="events", label="Events in the window",
                 value=f"Earnings {earnings:%b %d}",
                 read=f"Earnings is {days} days out, clear of a {dte_hi}-day window.",
                 status="good")


def tradability_layer(read: ChainRead, min_credit_pct: float) -> Layer:
    """Liquidity and whether the credit clears her floor - one layer, because
    they are the same question: can this trade actually be put on properly."""
    if read.dte is None:
        return _unknown("tradability", "Tradability",
                        "No option chain available for this name right now.", 0.0)

    said: list[str] = []
    blocks: list[str] = []
    status = "good"

    def worse(level: str) -> None:
        nonlocal status
        if level == "bad" or status == "good":
            status = level

    if read.rel_spread is not None:
        pct = read.rel_spread * 100
        said.append(f"The bid-ask where you would sell is about {pct:.0f}% of the "
                    "premium.")
        if pct > 20:
            worse("bad")
            blocks.extend([PUT, CALL])
            said.append("You pay that twice, entering and exiting, so it comes "
                        "straight off a credit you have not earned yet. Too thin "
                        "to trade properly.")
        elif pct > 10:
            worse("watch")
            said.append("Wide enough to matter - work the limit price rather than "
                        "taking whatever the market shows.")
    if read.open_interest is not None:
        said.append(f"Median open interest is {read.open_interest:,}.")
        if read.open_interest < 50:
            worse("bad")
            if PUT not in blocks:
                blocks.extend([PUT, CALL])
            said.append("Too few contracts outstanding to be sure of getting out "
                        "in a hurry.")
        elif read.open_interest < 100:
            worse("watch")
            said.append("On the thin side, though still tradable.")

    floor = min_credit_pct * 100
    for side, pct in ((PUT, read.put_credit_pct), (CALL, read.call_credit_pct)):
        word = "put" if side == PUT else "call"
        if pct is None:
            said.append(f"The {word} side could not be priced from this chain.")
            continue
        said.append(f"The {word} side pays {pct * 100:.1f}% of the width.")
        if pct < min_credit_pct:
            blocks.append(side)
            worse("watch")
            said.append(f"That is under your {floor:.0f}% minimum, so the credit "
                        "does not cover the risk you would be taking.")

    value = f"{read.dte} DTE"
    if read.width:
        value += f", {read.width:g}-wide"
    return Layer(key="tradability", label="Tradability", value=value,
                 read=(f"Checked at {value}. " + " ".join(said)
                       if said else "Nothing measurable in the chain."),
                 status=status, blocks=sorted(set(blocks)))


# ------------------------------------------------------------------ verdict
def _verdict_words(score: float, blocked: bool) -> tuple[str, str]:
    if blocked:
        return "Stand aside", "red"
    if score >= GOOD:
        return "Good candidate", "green"
    if score >= WORKABLE:
        return "Workable", "amber"
    if score >= POOR:
        return "Poor fit", "amber"
    return "Wrong side", "red"


def _side(side: str, name: str, layers: list[Layer]) -> SideVerdict:
    score = 0.0
    max_score = 0.0
    reasons: list[str] = []
    blockers: list[str] = []
    for lay in layers:
        pts = lay.put_points if side == PUT else lay.call_points
        if lay.known:
            score += pts
            max_score += lay.weight
        if side in lay.blocks:
            blockers.append(f"{lay.label}: {lay.read}")
        if abs(pts) >= 0.75:
            reasons.append(f"{'+' if pts > 0 else ''}{pts:.2g}  {lay.label} - "
                           f"{lay.value}")
    score = round(score, 2)
    verdict, tone = _verdict_words(score, bool(blockers))
    return SideVerdict(side=side, name=name, score=score,
                       max_score=round(max_score, 2), verdict=verdict, tone=tone,
                       reasons=reasons, blockers=blockers)


def assess(
    symbol: str,
    *,
    kind: str = "stock",
    closes: Optional[list[float]] = None,
    highs: Optional[list[float]] = None,
    lows: Optional[list[float]] = None,
    volumes: Optional[list[float]] = None,
    bench_closes: Optional[list[float]] = None,
    bench_name: str = "SPY",
    is_benchmark: bool = False,
    iv_rank: Optional[float] = None,
    iv_rank_source: str = "",
    iv: Optional[float] = None,
    hv: Optional[float] = None,
    vix: Optional[float] = None,
    vix_stop: float = 28.0,
    vix_zone: tuple[float, float] = (13.0, 25.0),
    earnings: Optional[dt.date] = None,
    dte_hi: int = 45,
    chain_read: Optional[ChainRead] = None,
    min_credit_pct: float = 0.06,
    today: Optional[dt.date] = None,
) -> CandidateReport:
    """Every layer, both sides, one verdict each."""
    today = today or dt.date.today()
    closes = closes or []
    highs = highs or closes
    lows = lows or closes
    price = closes[-1] if closes else None

    structure = structure_layer(highs, lows)
    direction = {"Uptrend": "up", "Downtrend": "down"}.get(structure.value, "sideways")

    layers = [
        structure,
        averages_layer(closes),
        conviction_layer(closes, volumes or [], direction),
        strength_layer(closes, bench_closes or [], bench_name, is_benchmark),
        volatility_layer(iv_rank, iv_rank_source or "unknown source", iv, hv),
        market_layer(vix, vix_stop, vix_zone),
        events_layer(earnings, today, dte_hi, kind),
        tradability_layer(chain_read or ChainRead(), min_credit_pct),
    ]

    put_side = _side(PUT, "Put Credit Spread", layers)
    call_side = _side(CALL, "Call Credit Spread", layers)

    ok_put = not put_side.blocked and put_side.score >= WORKABLE
    ok_call = not call_side.blocked and call_side.score >= WORKABLE
    if ok_put and ok_call:
        best = "both"
    elif ok_put:
        best = PUT
    elif ok_call:
        best = CALL
    else:
        best = "neither"

    gaps = [lay.label for lay in layers if not lay.known]
    return CandidateReport(
        symbol=symbol, kind=kind, price=price, as_of=today, layers=layers,
        put_side=put_side, call_side=call_side, best=best,
        vol_source=iv_rank_source,
        summary=_summary(symbol, best, put_side, call_side, direction),
        data_gaps=gaps,
    )


def _summary(symbol: str, best: str, put_side: SideVerdict, call_side: SideVerdict,
             direction: str) -> str:
    if best == "both":
        return (f"{symbol} grades as workable on both sides, and structure reads "
                f"{direction}. Two workable sides at once is the range case - an "
                "iron condor is the shape that fits it, not two separate spreads.")
    if best == PUT:
        return (f"{symbol} favours the put side ({put_side.verdict.lower()}). You "
                "would be betting it does not fall through your short strike, which "
                "is the direction the evidence leans.")
    if best == CALL:
        return (f"{symbol} favours the call side ({call_side.verdict.lower()}). You "
                "would be betting it does not rise through your short strike.")
    if put_side.blocked and call_side.blocked:
        return (f"{symbol} is blocked on both sides right now. The blockers below are "
                "not scores to be outweighed - each one is a reason on its own.")
    return (f"{symbol} does not grade well enough on either side today. Nothing is "
            "wrong with waiting; a thin setup taken now costs more than a good one "
            "missed.")
