"""A plain-English read of current conditions, plus which of your strategies fit.

This mirrors the "Quick Market Condition Guide" in your Notion hub:
  calm / low VIX / range-bound  -> Iron Condor
  slightly bearish or flat       -> Call Credit Spread
  slightly bullish or flat       -> Put Credit Spread
  own stock, want income         -> Covered Call
  want cheap stock exposure      -> Poor Man's Covered Call
  want to buy stock at a discount-> Cash Secured Put

Everything degrades gracefully: if live VIX or price history is unavailable,
it still returns a useful read from what it does have.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from pydantic import BaseModel, Field

from src.data.chain import OptionChain
from src.engine.models import OptionType


class StrategySuggestion(BaseModel):
    strategy_key: str
    name: str
    reason: str
    # What put it in this position. Carried so the UI can show the reasoning
    # rather than an order she has to take on trust.
    score: float = 0.0
    trend_points: float = 0.0
    vol_points: float = 0.0
    # What you would trade it ON, and what you must already have. The board used
    # to show only the three index strategies, so the other six in her SOP had
    # no home on this tab at all and it looked like the app only knew three.
    instrument: str = "index"        # "index" | "us_style"
    traded_on: str = ""              # "SPX, NDX, RUT or XSP"
    requires: str = ""               # "100 shares you already own"
    advanced: bool = False           # needs daily management - flagged on screen


class MarketContext(BaseModel):
    underlying: str
    price: float
    atm_iv: Optional[float] = None      # implied volatility of the near-the-money option
    vix: Optional[float] = None
    trend: str = "unknown"              # "up", "down", "sideways", "unknown"
    # The gap between the 20- and 50-day averages, as a fraction, and the gap
    # it has to clear. Shown on the page so an unchanged ranking reads as a
    # fact about the market rather than as a stuck screen.
    trend_spread: Optional[float] = None
    trend_band: float = 0.01
    below_200: bool = False
    vol_bucket: str = "unknown"         # "low", "normal", "high"
    volatility_read: str = ""           # plain-English note on IV / VIX
    summary: str = ""
    # The single best-fit INDEX strategy for right now, plus a short reason.
    # Index-only on purpose: the Picks scan and the "set this up" button feed
    # this key straight into an SPX credit-spread scan, and a covered call
    # coming out of here would have nothing to scan.
    best_strategy_key: Optional[str] = None
    best_strategy_name: Optional[str] = None
    recommendation_reason: str = ""
    # The best index pick first, then the other two.
    suggestions: list[StrategySuggestion] = Field(default_factory=list)
    # Every strategy in the SOP ranked together - index and US-style side by
    # side, so the Market tab shows the whole book rather than three of it.
    board: list[StrategySuggestion] = Field(default_factory=list)


def _atm_iv(chain: OptionChain) -> Optional[float]:
    dte = chain.nearest_dte(45)
    if dte is None:
        return None
    calls = chain.by(OptionType.CALL, dte)
    if not calls:
        return None
    atm = min(calls, key=lambda c: abs(c.strike - chain.underlying_price))
    return atm.iv or None


def _volatility_read(vix: Optional[float], atm_iv: Optional[float]) -> tuple[str, str]:
    """Return (bucket, plain-English note). bucket is 'low' / 'normal' / 'high'."""
    gauge = vix if vix is not None else (atm_iv * 100 if atm_iv else None)
    label = "VIX" if vix is not None else "implied volatility"
    if gauge is None:
        return "unknown", "Volatility reading unavailable."
    if gauge < 15:
        return "low", (f"{label} is low ({gauge:.1f}). Option premiums are thin, and the "
                       "market expects calm - good for range-bound trades like Iron Condors.")
    if gauge < 25:
        return "normal", (f"{label} is moderate ({gauge:.1f}). Premiums are reasonable - "
                          "your usual credit spreads fit well.")
    return "high", (f"{label} is elevated ({gauge:.1f}). Premiums are fat but moves are bigger - "
                    "credit spreads pay more, but keep size small and deltas low.")


# ---------------------------------------------------------------- the board
# Every strategy in her SOP, described by the market shape it WANTS. Kept as
# data rather than nested ifs so the reasoning can be PRINTED - the section used
# to show an order with nothing behind it, which is why a correct answer that
# had not changed for six weeks looked broken.
#
# Three numbers describe each one:
#   lean       +1 wants price to drift up, -1 wants it to drift down, 0 has no
#              directional opinion at all.
#   range      0 to 1 - how much it wants price to STAY PUT. The condor is the
#              only pure one; it needs both wings left alone.
#   vol        +1 is PAID more when fear rises (it sells options), -1 is HURT by
#              it (it buys them, or it has two sides that a big swing can breach).
#
# The volatility column comes from her Notion hub's quick guide: "calm / low VIX
# / range-bound -> Iron Condor". The code used to implement only the range-bound
# half, so a VIX of 12 and a VIX of 28 gave the identical ranking.
class _Profile(NamedTuple):
    lean: float
    range_pref: float
    vol_pref: float
    name: str
    instrument: str          # "index" (cash-settled) | "us_style" (stocks/ETFs)
    traded_on: str
    requires: str
    reason: str
    # A flat penalty for how much skill and babysitting the trade demands, so a
    # board cannot float a hard strategy to the top on conditions alone. Her SOP
    # says Model 3 is never to be auto-suggested - losses accelerate below the
    # two short puts - so it carries the heaviest one and can still be found,
    # just never presented as the obvious move.
    handicap: float = 0.0
    advanced: bool = False


_INDEX_ON = "SPX, NDX, RUT or XSP"
_US_ON = "a stock or ETF (never SPX - these can be assigned early)"

# Order here is also the tie-break when scores are level, because sort() is
# stable. Her SOP sells call spreads at a stricter 0.10 delta precisely because
# markets drift up, so between the two one-sided spreads the put side leads.
_PROFILES: dict[str, _Profile] = {
    "put_credit_spread": _Profile(
        lean=1.0, range_pref=0.35, vol_pref=0.5,
        name="Put Credit Spread", instrument="index", traded_on=_INDEX_ON,
        requires="cash to cover the gap between your two strikes",
        reason="Neutral-to-bullish lean - you win as long as price does not fall hard."),
    "iron_condor": _Profile(
        lean=0.0, range_pref=1.0, vol_pref=-1.2,
        name="Iron Condor", instrument="index", traded_on=_INDEX_ON,
        requires="cash to cover the wider of the two wings",
        reason="Range-bound market - you collect premium from both sides at once."),
    "call_credit_spread": _Profile(
        lean=-1.0, range_pref=0.35, vol_pref=0.5,
        name="Call Credit Spread", instrument="index", traded_on=_INDEX_ON,
        requires="cash to cover the gap between your two strikes",
        reason="Neutral-to-bearish lean - you win as long as price does not rise hard."),
    "cash_secured_put": _Profile(
        lean=0.8, range_pref=0.4, vol_pref=0.6,
        name="Cash Secured Put", instrument="us_style", traded_on=_US_ON,
        requires="enough cash to buy 100 shares if it is put to you",
        reason="You get paid up front to agree to buy a name cheaper than it trades "
               "today. Best on something steady or rising that you would be happy to own."),
    "wheel": _Profile(
        lean=0.6, range_pref=0.3, vol_pref=0.5,
        name="The Wheel", instrument="us_style", traded_on=_US_ON,
        requires="cash for 100 shares now, and the patience to hold them later",
        reason="Sell puts until you end up owning the shares, then sell calls against "
               "them. A slow repeating income loop, not a one-off trade."),
    "poor_mans_covered_call": _Profile(
        lean=1.0, range_pref=0.1, vol_pref=-0.8,
        name="Poor Man's Covered Call", instrument="us_style", traded_on=_US_ON,
        requires="a deep, long-dated call that you BUY up front",
        reason="The covered-call idea for a fraction of the money - a long-dated call "
               "stands in for the 100 shares and you sell monthly calls against it.",
        handicap=0.3),
    "covered_call_model_1": _Profile(
        lean=-0.4, range_pref=0.2, vol_pref=0.9,
        name="Covered Call - Model 1 (Collar)", instrument="us_style", traded_on=_US_ON,
        requires="100 shares you already own",
        reason="The defensive one. A long put covers the whole downside while the short "
               "call still pays you - the way to hold through a rough patch."),
    "covered_call_model_2": _Profile(
        lean=0.2, range_pref=0.6, vol_pref=0.0,
        name="Covered Call - Model 2 (Classic)", instrument="us_style", traded_on=_US_ON,
        requires="100 shares you already own",
        reason="The everyday one. A classic covered call with a cheaper put-spread "
               "hedge, balancing monthly income against protection."),
    "covered_call_model_3": _Profile(
        lean=0.9, range_pref=0.35, vol_pref=-1.0,
        name="Covered Call - Model 3 (Zero-Cost Ratio)", instrument="us_style",
        traded_on=_US_ON,
        requires="100 shares, and the time to watch it every day",
        reason="The advanced one. The hedge costs almost nothing so you keep more of "
               "the premium, but a hard drop below the two short puts speeds up losses. "
               "Your SOP says to take this one deliberately or not at all, so it never "
               "sits at the top of this board however well conditions suit it.",
        handicap=1.5, advanced=True),
}

_INDEX_KEYS = [k for k, p in _PROFILES.items() if p.instrument == "index"]

# Plain-English names, kept short for the chips on the board.
_NAMES = {k: p.name for k, p in _PROFILES.items()}

# ---- how the two live readings turn into points ---------------------------
# Weights: the trend read carries the most, then how flat it is, then fear.
# Volatility is deliberately the smallest of the three - a real trend has to
# beat a merely calm reading, or a quiet uptrend would recommend a condor.
_TREND_WEIGHT = 2.0
_RANGE_WEIGHT = 2.0
_VOL_WEIGHT = 1.0

# Fear is measured as a distance from "ordinary", not slotted into buckets.
# Buckets were the reason the order never moved: VIX 15 and VIX 24 both landed
# in "normal" and scored exactly zero, so in practice only the trend counted -
# and the trend is a multi-week read. A continuous reading means a VIX drifting
# from 15 to 19 actually shows up in the ranking.
_VOL_MID = 18.0          # a VIX around here is ordinary
_VOL_STEP = 6.0          # points per this many VIX points
_VOL_CAP = 1.5


def _clamp(value: float, limit: float = _VOL_CAP) -> float:
    return max(-limit, min(limit, value))


def _lean_strength(trend: str, trend_spread: Optional[float], band: float) -> Optional[float]:
    """Which way the market leans AND how hard, as a number around -1.5 to 1.5.

    Returns None when the trend is unknown, which is NOT the same as flat - a
    failed price fetch used to score identically to a genuine range-bound
    market, so there was nothing on screen to notice.

    The magnitude is what makes this section move. The old code threw the gap
    away and kept only the up/down/sideways label, so a 20/50 gap widening from
    0.1% to 0.9% changed nothing at all on the page. Now it does, and the order
    can shift before the gap ever crosses the 1% band.
    """
    if trend == "unknown":
        return None
    if trend_spread is None:
        # Schwab and demo have no price history to measure; fall back to the label.
        return {"up": 1.0, "down": -1.0}.get(trend, 0.0)
    return _clamp(trend_spread / band) if band else 0.0


def _vol_strength(vix: Optional[float], atm_iv: Optional[float]) -> Optional[float]:
    """How far fear sits from ordinary: negative is calm, positive is nervous."""
    gauge = vix if vix is not None else (atm_iv * 100 if atm_iv else None)
    if gauge is None:
        return None
    return _clamp((gauge - _VOL_MID) / _VOL_STEP)


def _trend_note(profile: _Profile, lean: Optional[float]) -> str:
    """One line on whether the market is leaning the way this strategy wants."""
    if lean is None:
        return ("The trend could not be read, so this is ranked on how much the "
                "market is swinging alone.")
    if abs(lean) < 0.25:
        if profile.range_pref >= 0.6:
            return "There is no direction to lean on right now, which is what this one wants."
        return "There is no direction to lean on right now, so nothing pushes this up or down."
    # Graduated, because this line sits directly under a heading that may well
    # say "SPX is sideways" - calling a 0.4% gap a lean flatly contradicts it.
    strength = "leaning slightly" if abs(lean) < 0.7 else "leaning"
    way = f"{strength} {'up' if lean > 0 else 'down'}"
    if profile.lean == 0 or abs(profile.lean) < 0.25:
        return (f"The market is {way}, which a trade that just wants price to sit "
                "still has no use for.")
    with_it = (lean > 0) == (profile.lean > 0)
    if with_it:
        return f"The market is {way}, which is the direction this one wants."
    return f"The market is {way}, against the side this one needs to win."


def _vol_note(key: str, profile: _Profile, vol: Optional[float]) -> str:
    """One line on what the fear reading does to this strategy."""
    if vol is None or abs(vol) < 0.25:
        return ""
    nervous = vol > 0
    if key == "iron_condor":
        return ("Fear is up, and a condor is the one shape with BOTH sides exposed - "
                "big swings can breach either wing, so it drops down the list."
                if nervous else
                "The market is calm, which is exactly when a condor's two sides are "
                "least likely to be breached.")
    if profile.vol_pref > 0.2:
        return ("Fear is up, which fattens the premium this one collects."
                if nervous else
                "The market is calm, so there is less premium in this than usual.")
    if profile.vol_pref < -0.2:
        return ("Fear is up, and this one BUYS an option - that costs more now, so it "
                "slips down the list."
                if nervous else
                "The market is calm, so the option this one buys is cheap - its best "
                "conditions.")
    return ""


def _score_strategies(keys: list[str], trend: str, trend_spread: Optional[float],
                      band: float, vix: Optional[float],
                      atm_iv: Optional[float]) -> list[StrategySuggestion]:
    """Rank `keys` best-first for current conditions.

    Three inputs, all continuous:
      - LEAN, which way the market is going and how hard. Decides which
        direction a one-sided trade can hide behind.
      - FLATNESS, how little it is going anywhere. Pays the range-lovers.
      - FEAR, how much it is swinging. Pays the option SELLERS and charges the
        option BUYERS, and it is the smallest of the three so it can nudge the
        order without ever overruling a real trend.

    Every suggestion carries the points that put it where it is, so the UI can
    show the arithmetic instead of asking her to trust an unexplained order.
    """
    lean = _lean_strength(trend, trend_spread, band)
    vol = _vol_strength(vix, atm_iv)
    # An unknown trend is not a flat one, so it earns no range points either.
    flatness = max(0.0, 1.0 - abs(lean)) if lean is not None else 0.0

    scored = []
    for key in keys:
        p = _PROFILES[key]
        # Direction and flatness both come from the same trend read, so they are
        # reported together - that keeps "score = trend points + vol points" true.
        # The handicap rides with the trend points so that the reported
        # "score = trend points + vol points" stays true on screen.
        trend_pts = (p.lean * (lean or 0.0) * _TREND_WEIGHT
                     + p.range_pref * flatness * _RANGE_WEIGHT
                     - p.handicap)
        vol_pts = p.vol_pref * (vol or 0.0) * _VOL_WEIGHT
        reason = " ".join(x for x in (p.reason, _trend_note(p, lean),
                                      _vol_note(key, p, vol)) if x)
        scored.append(StrategySuggestion(
            strategy_key=key, name=p.name, reason=reason,
            score=round(trend_pts + vol_pts, 2),
            trend_points=round(trend_pts, 2), vol_points=round(vol_pts, 2),
            instrument=p.instrument, traded_on=p.traded_on, requires=p.requires,
            advanced=p.advanced))

    # Highest score first; ties keep _PROFILES order, which sort() preserves
    # because it is stable and `scored` was built in that order.
    scored.sort(key=lambda s: -s.score)

    # Fear now REORDERS the list, but the size caution has to survive that - it
    # is SOP guidance about how to trade whatever comes out on top, not a
    # comment on the condor.
    if vol is not None and vol >= 1.0:
        scored[0] = scored[0].model_copy(update={
            "reason": scored[0].reason + " Volatility is high, so keep size "
                                         "small and deltas low."})
    return scored


def build_context(
    underlying: str,
    price: float,
    vix: Optional[float] = None,
    trend: str = "unknown",
    atm_iv: Optional[float] = None,
    trend_spread: Optional[float] = None,
    below_200: bool = False,
) -> MarketContext:
    """Build the market read from lightweight inputs (no full option chain needed,
    so the snapshot loads fast on real data).

    trend_spread is optional evidence: the gap between the 20- and 50-day
    averages that produced `trend`. Callers that have it (the live path, via
    trend_detail) pass it so the page can show its working.
    """
    vol_bucket, vol_note = _volatility_read(vix, atm_iv)
    # Two rankings off one read. `suggestions` stays index-only because its top
    # key is fed straight into an SPX credit-spread scan and into the "set this
    # up" button; `board` is the whole SOP, which is what the Market tab shows.
    suggestions = _score_strategies(_INDEX_KEYS, trend, trend_spread, TREND_BAND,
                                    vix, atm_iv)
    board = _score_strategies(list(_PROFILES), trend, trend_spread, TREND_BAND,
                              vix, atm_iv)
    best = suggestions[0]

    trend_word = {
        "up": "leaning up", "down": "leaning down",
        "sideways": "moving sideways", "unknown": "direction unclear",
    }[trend]

    summary = f"{underlying} is at {price:,.2f} and {trend_word}. {vol_note}"

    return MarketContext(
        underlying=underlying,
        price=price,
        atm_iv=atm_iv,
        vix=vix,
        trend=trend,
        trend_spread=trend_spread,
        trend_band=TREND_BAND,
        below_200=below_200,
        vol_bucket=vol_bucket,
        volatility_read=vol_note,
        summary=summary,
        best_strategy_key=best.strategy_key,
        best_strategy_name=best.name,
        recommendation_reason=best.reason,
        suggestions=suggestions,
        board=board,
    )


def context_from_chain(
    chain: OptionChain, vix: Optional[float] = None, trend: str = "unknown",
) -> MarketContext:
    """Convenience wrapper when you already have a chain (demo mode / offline)."""
    return build_context(chain.underlying, chain.underlying_price, vix, trend, _atm_iv(chain))


def daily_sentiment(index_changes: list[Optional[float]], vix: Optional[float]) -> tuple[str, str]:
    """One-line read of how the market feels TODAY, from the big indexes + VIX.

    Returns (label, note) - e.g. ("🙂 Mildly positive and calm", "...").
    """
    changes = [c for c in index_changes if c is not None]
    if not changes:
        return "😐 No read yet", "Live daily changes are unavailable right now."
    avg = sum(changes) / len(changes)

    if avg >= 0.6:
        mood, icon = "Strongly positive", "😄"
    elif avg >= 0.15:
        mood, icon = "Mildly positive", "🙂"
    elif avg > -0.15:
        mood, icon = "Flat / mixed", "😐"
    elif avg > -0.6:
        mood, icon = "Mildly negative", "🙁"
    else:
        mood, icon = "Strongly negative", "😨"

    if vix is None:
        calm = ""
        calm_note = ""
    elif vix < 15:
        calm, calm_note = " and calm", "Fear is low - option premiums are on the thin side."
    elif vix < 25:
        calm, calm_note = "", "Volatility is moderate - normal conditions for selling premium."
    else:
        calm, calm_note = " and nervous", "Fear is elevated - premiums are rich but moves are bigger."

    note = (f"The big indexes are averaging {avg:+.2f}% today. {calm_note}").strip()
    return f"{icon} {mood}{calm}", note


# How far under the 200-day average counts as a real decline rather than noise
# sitting on the line.
LONG_TREND_BAND = 0.03


def trend_from_prices(prices: list[float]) -> str:
    """Trend from a list of recent daily closes (oldest first).

    Compares the short (20-day) and long (50-day) averages, the same idea traders
    use: short above long = uptrend. Then the 200-day average gets a veto.

    The 20/50 crossover only sees about six weeks, and a stock in a sustained
    slide can drift sideways for six weeks. SOFI read "sideways" on the
    crossover while sitting 23% below its 200-day average and down 23% on the
    year, which put it in the "puts you'd sell for income" list - exactly the
    falling-knife the SOP says to avoid, since a put can leave you owning it.

    So a name well below its 200-day is capped: a "sideways" or "down" short
    read becomes "down", and even a rising short read is only allowed up to
    "sideways". That last part matters - a stock that crashed and is genuinely
    recovering is still below its 200-day for months, and calling that a
    downtrend would throw away good candidates.
    """
    return trend_detail(prices)["trend"]


# How far apart the 20- and 50-day averages must be before it counts as a
# direction rather than noise. On a broad index the two averages sit close
# together for weeks at a time, which is why this section can honestly show the
# same answer for over a month - the replay of the last year of SPX had runs of
# 1 to 4 months. That is a trend signal behaving normally, not a stuck screen,
# and the fix is to SHOW the number rather than to narrow the band until the
# recommendation flip-flops.
TREND_BAND = 0.01


def trend_detail(prices: list[float]) -> dict:
    """The trend, plus the numbers that produced it.

    Split out from trend_from_prices so the Market tab can print its evidence:
    "the 20-day average is 0.2% above the 50-day, and it takes 1% to call a
    direction". Without that, an unchanged-but-correct answer is
    indistinguishable from a broken one.

    Returns trend plus sma20/sma50/spread/sma200/below_200 (None where there
    is not enough history to compute them).
    """
    out = {"trend": "unknown", "sma20": None, "sma50": None, "spread": None,
           "sma200": None, "below_200": False, "band": TREND_BAND,
           "days": len(prices)}
    if len(prices) < 50:
        return out

    sma20 = sum(prices[-20:]) / 20
    sma50 = sum(prices[-50:]) / 50
    spread = (sma20 - sma50) / sma50 if sma50 else 0.0
    short = ("up" if spread > TREND_BAND
             else "down" if spread < -TREND_BAND else "sideways")
    out.update(sma20=sma20, sma50=sma50, spread=spread, trend=short)

    if len(prices) >= 200:
        sma200 = sum(prices[-200:]) / 200
        out["sma200"] = sma200
        if sma200 > 0 and prices[-1] < sma200 * (1 - LONG_TREND_BAND):
            out["below_200"] = True
            out["trend"] = "sideways" if short == "up" else "down"
    return out


# The shortest history trend_detail can read anything from.
_MIN_HISTORY = 50


def trend_history(prices: list[float]) -> dict:
    """Replay the trend read day by day: how often has it ACTUALLY changed?

    The page used to state "over the past year this order changed about seven
    times" as fixed text. Nobody was measuring it, so it could quietly drift out
    of date - and it is the one number that answers "why is this always the
    same". Now it is computed from the same closes the live read uses.

    Returns {days, changes, run, trend, enough}:
      days     trading days replayed
      changes  times the trend read flipped across that window
      run      trading days it has held its CURRENT read
      trend    that current read

    `enough` is False when there is too little history to say anything, so the
    caller can stay quiet rather than print a misleading zero.
    """
    out = {"days": 0, "changes": 0, "run": 0, "trend": "unknown", "enough": False}
    if len(prices) < _MIN_HISTORY + 2:
        return out

    reads = [trend_detail(prices[:i])["trend"]
             for i in range(_MIN_HISTORY, len(prices) + 1)]
    changes = sum(1 for a, b in zip(reads, reads[1:]) if a != b)

    run = 1
    for earlier in reversed(reads[:-1]):
        if earlier != reads[-1]:
            break
        run += 1

    return {"days": len(reads), "changes": changes, "run": run,
            "trend": reads[-1], "enough": True}
