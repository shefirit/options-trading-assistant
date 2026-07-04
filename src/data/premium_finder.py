"""Finds which stocks and ETFs pay the richest option premiums right now.

For each symbol it looks at one monthly expiration (~30 days out), takes the
put you'd typically SELL (around 0.30 delta - the cash-secured-put strike), and
works out:

  - Credit: the cash you collect for one contract.
  - Monthly yield: that credit as a % of the cash you set aside - the intuitive
    "how much do I get paid" number.
  - Implied volatility (IV): how much the market is paying for movement.
  - IV vs actual movement (IV/HV): whether the premium is RICH relative to how
    much the stock really moves - the seller's real edge.

Important truth built into the wording: a high premium is not free money. It
usually means the market expects a big move (a jumpy stock, or earnings coming).
Always weigh premium against the stock's quality grade and its earnings date.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from pydantic import BaseModel

from src.data.chain import OptionChain
from src.engine.models import OptionType

TARGET_DELTA = 0.30   # the strike a cash secured put / covered call usually sells


class PremiumSnapshot(BaseModel):
    symbol: str
    price: Optional[float] = None
    dte: Optional[int] = None
    # the PUT you'd sell (cash secured put)
    short_strike: Optional[float] = None
    short_delta: Optional[float] = None
    credit: Optional[float] = None            # per share
    credit_dollars: Optional[float] = None    # per contract (x100)
    monthly_yield_pct: Optional[float] = None
    annualized_yield_pct: Optional[float] = None
    # the CALL you'd sell (covered call) - the other side
    call_strike: Optional[float] = None
    call_credit_dollars: Optional[float] = None
    call_yield_pct: Optional[float] = None
    # the odds and the safety margin
    pop: Optional[float] = None               # est. probability of profit, %
    breakeven: Optional[float] = None         # strike - credit
    cushion_pct: Optional[float] = None       # % price can fall before you lose
    # can you actually trade it?
    spread_pct: Optional[float] = None        # bid-ask spread as % of mid
    open_interest: Optional[int] = None
    liquidity: str = "n/a"                    # "Good" | "OK" | "Thin"
    # volatility read
    atm_iv: Optional[float] = None            # e.g. 0.28
    hv: Optional[float] = None                # realized vol
    iv_hv_ratio: Optional[float] = None
    richness: str = "n/a"                     # "Rich" | "Fair" | "Thin"
    # context
    grade: Optional[str] = None               # A-F quality grade (stocks only)
    earnings_date: Optional[dt.date] = None
    earnings_before_expiry: bool = False
    flags: list[str] = []                     # warnings to show
    # the clear plan: what to do, and the risk
    trend: str = "unknown"
    action: str = ""                          # "Sell puts" | "Sell calls" | "Wait"
    strategy: str = ""                        # e.g. "Cash Secured Put"
    capital_at_risk: Optional[float] = None   # dollars tied up / at stake
    risk_note: str = ""
    recommendation: str = ""
    # the bottom-line call
    verdict: str = "okay"                     # "sell" | "okay" | "skip"
    verdict_reason: str = ""
    error: str = ""


def annualized_vol(closes: list[float], lookback: int = 30) -> Optional[float]:
    """Realized (historical) volatility from recent daily closes."""
    if len(closes) < lookback + 1:
        return None
    window = closes[-(lookback + 1):]
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))
            if window[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252), 4)


def _nearest_atm_iv(puts: list, price: float) -> Optional[float]:
    if not puts or not price:
        return None
    atm = min(puts, key=lambda c: abs(c.strike - price))
    return atm.iv or None


def _pick_by_delta(options: list, price: float, otm_side: str):
    """The option you'd sell: nearest ~0.30 delta, or ~5% out of the money if the
    chain has no usable deltas. otm_side: 'below' for puts, 'above' for calls."""
    with_delta = [o for o in options if o.abs_delta > 0]
    if with_delta:
        return min(with_delta, key=lambda o: abs(o.abs_delta - TARGET_DELTA))
    if price:
        if otm_side == "below":
            cands = [o for o in options if o.strike <= price * 0.95]
            return max(cands, key=lambda o: o.strike) if cands else None
        cands = [o for o in options if o.strike >= price * 1.05]
        return min(cands, key=lambda o: o.strike) if cands else None
    return None


def _plan(snap: "PremiumSnapshot", trend: str, monthly_bp: float) -> None:
    """Fill in the clear 'what do I do and what's the risk' fields, in place."""
    strike = snap.short_strike or 0
    shares_cost = strike * 100

    if trend == "down":
        snap.action = "Sell calls"
        snap.strategy = "Covered Call (only if you own 100 shares)"
        snap.capital_at_risk = round(shares_cost, 0)
        snap.risk_note = (f"Downtrend. Selling puts here is risky. If you already own 100 "
                          f"shares of {snap.symbol}, sell a call for income; the risk is the "
                          "shares falling further. If you do not own it, wait.")
        snap.recommendation = (f"Wait, or sell covered calls if you hold the shares. "
                               f"About {snap.call_yield_pct:.2f}% for the month on the call."
                               if snap.call_yield_pct else "Wait for the trend to steady.")
        return

    # up / sideways / unknown -> sell puts (get paid, maybe buy cheaper)
    snap.action = "Sell puts"
    if shares_cost > monthly_bp and shares_cost > 0:
        snap.strategy = "Poor Man's Covered Call"
        snap.capital_at_risk = None
        snap.risk_note = (f"100 shares cost about ${shares_cost:,.0f} - over your "
                          f"${monthly_bp:,.0f} monthly limit. A Poor Man's Covered Call gets "
                          "similar income for far less money.")
        snap.recommendation = (f"{snap.symbol} pays about {snap.monthly_yield_pct:.2f}% a month "
                               "but is pricey to own - use a PMCC instead of a cash secured put.")
    else:
        snap.strategy = "Cash Secured Put"
        snap.capital_at_risk = round(shares_cost, 0)
        snap.risk_note = (f"Set aside ${shares_cost:,.0f}. If {snap.symbol} falls below "
                          f"${strike:g} you buy 100 shares there (worst case, the stock keeps "
                          "dropping). Otherwise you keep the premium.")
        snap.recommendation = (
            f"Sell the ${strike:g} put: collect ${snap.credit_dollars:,.0f} "
            f"(~{snap.monthly_yield_pct:.2f}% for the month). "
            + ("Premium is rich for the risk - attractive." if snap.richness == "Rich"
               else "Fair premium." if snap.richness == "Fair"
               else "Premium is thin for how much it moves - look for a richer name."))


def _richness(iv_hv: Optional[float], atm_iv: Optional[float]) -> str:
    # "Fair" is the normal middle; "Rich" means clearly paid extra for the risk;
    # "Thin" is reserved for genuinely poor premium (IV well below the stock's
    # own movement).
    if iv_hv is not None:
        if iv_hv >= 1.15:
            return "Rich"
        if iv_hv >= 0.80:
            return "Fair"
        return "Thin"
    # No realized-vol comparison - fall back to the raw IV level.
    if atm_iv is None:
        return "n/a"
    if atm_iv >= 0.35:
        return "Rich"
    if atm_iv >= 0.16:
        return "Fair"
    return "Thin"


def _liquidity(short) -> tuple[Optional[float], Optional[int], str]:
    """Spread % of mid, open interest, and a Good/OK/Thin read."""
    oi = short.open_interest or 0
    if short.bid > 0 and short.ask > 0:
        mid = (short.bid + short.ask) / 2
        spread_pct = round((short.ask - short.bid) / mid * 100, 1) if mid > 0 else None
    else:
        spread_pct = None
    if spread_pct is None:
        return None, oi or None, "n/a"
    if spread_pct <= 6 and oi >= 250:
        liq = "Good"
    elif spread_pct <= 15:
        liq = "OK"
    else:
        liq = "Thin"
    return spread_pct, oi or None, liq


def snapshot(
    symbol: str,
    chain: OptionChain,
    hv: Optional[float],
    trend: str = "unknown",
    monthly_bp: float = 50_000,
    earnings_date: Optional[dt.date] = None,
    grade: Optional[str] = None,
    today: Optional[dt.date] = None,
) -> PremiumSnapshot:
    """Compute the premium picture + odds + safety + a clear plan (pure)."""
    today = today or dt.date.today()
    price = chain.underlying_price
    puts = [c for c in chain.contracts if c.option_type == OptionType.PUT]
    calls = [c for c in chain.contracts if c.option_type == OptionType.CALL]
    if not puts or not price:
        return PremiumSnapshot(symbol=symbol, price=price or None,
                               error="No option data available.")

    short = _pick_by_delta(puts, price, "below")
    if short is None or short.mid <= 0 or short.strike <= 0:
        return PremiumSnapshot(symbol=symbol, price=price, error="No sellable put found.")

    credit = short.mid
    dte = short.dte or 30
    monthly_yield = credit / short.strike * 100
    annualized = monthly_yield * (365 / dte) if dte else None
    atm_iv = _nearest_atm_iv(puts, price)
    iv_hv = round(atm_iv / hv, 2) if (atm_iv and hv) else None

    # odds + safety margin
    pop = round((1 - short.abs_delta) * 100)        # ~prob the put expires worthless
    breakeven = round(short.strike - credit, 2)
    cushion = round((price - breakeven) / price * 100, 1) if price else None
    spread_pct, oi, liq = _liquidity(short)

    earnings_in = bool(earnings_date and today <= earnings_date
                       <= today + dt.timedelta(days=dte))
    flags: list[str] = []
    if liq == "Thin":
        flags.append("Hard to trade (wide bid-ask spread)")
    if earnings_in:
        flags.append(f"Earnings {earnings_date:%b %d} - before your expiry")

    snap = PremiumSnapshot(
        symbol=symbol, price=round(price, 2), dte=dte,
        short_strike=short.strike, short_delta=round(short.abs_delta, 3),
        credit=round(credit, 2), credit_dollars=round(credit * 100, 0),
        monthly_yield_pct=round(monthly_yield, 2),
        annualized_yield_pct=round(annualized, 1) if annualized else None,
        pop=pop, breakeven=breakeven, cushion_pct=cushion,
        spread_pct=spread_pct, open_interest=oi, liquidity=liq,
        atm_iv=round(atm_iv, 4) if atm_iv else None,
        hv=round(hv, 4) if hv else None,
        iv_hv_ratio=iv_hv,
        richness=_richness(iv_hv, atm_iv),
        grade=grade, earnings_date=earnings_date, earnings_before_expiry=earnings_in,
        flags=flags, trend=trend,
    )

    # The other side: the call you'd sell (covered call income).
    call = _pick_by_delta(calls, price, "above")
    if call is not None and call.mid > 0 and call.strike > 0:
        snap.call_strike = call.strike
        snap.call_credit_dollars = round(call.mid * 100, 0)
        snap.call_yield_pct = round(call.mid / price * 100, 2)

    _plan(snap, trend, monthly_bp)
    if earnings_in:
        snap.recommendation += (f" Heads up: earnings on {earnings_date:%b %d} lands before "
                                "expiry - either pick a nearer expiration or wait until after.")
    _set_verdict(snap)
    return snap


def _set_verdict(snap: PremiumSnapshot) -> None:
    """The bottom-line call, weighing the things that actually matter to a
    beginner selling a cash secured put: is the company solid (a put can leave
    you owning it), is the premium a genuinely good deal for the risk, can you
    trade it cleanly, and is the timing clear of earnings?

    ETFs (no letter grade) count as solid - they are baskets, not one company.
    """
    g = snap.grade
    solid = g in ("A", "B") or g is None

    # --- any one of these is a deal-breaker -> skip ---
    if snap.action != "Sell puts":
        snap.verdict, snap.verdict_reason = "skip", (
            "It's in a downtrend - don't sell puts into a falling stock. Sell calls here only if "
            "you already own the shares.")
        return
    if g in ("D", "F"):
        snap.verdict, snap.verdict_reason = "skip", (
            f"Weak company (grade {g}). If the put assigns you the shares, you're stuck owning a "
            "poor business - the quality matters more than the premium.")
        return
    if snap.liquidity == "Thin":
        snap.verdict, snap.verdict_reason = "skip", (
            "Hard to trade - the gap between the buy and sell price is so wide you'd lose money "
            "just getting in and out.")
        return
    if snap.richness == "Thin":
        snap.verdict, snap.verdict_reason = "skip", (
            "The premium is a poor deal - this name swings around a lot but pays little for the "
            "risk. Look for a name that pays you more for the same movement.")
        return

    # --- good setup, bad timing -> caps at 'okay' ---
    if snap.earnings_before_expiry:
        snap.verdict, snap.verdict_reason = "okay", (
            "Solid otherwise, but an earnings report lands before your option expires (a coin-flip "
            "event). Pick an expiration before earnings, or wait until they've passed.")
        return

    if snap.richness == "n/a":
        snap.verdict, snap.verdict_reason = "okay", (
            "Priced reasonably, but I couldn't gauge how good the premium deal is - double-check "
            "it yourself before selling.")
        return

    # --- the good cases ---
    if snap.richness == "Rich" and solid:
        snap.verdict, snap.verdict_reason = "sell", (
            "Rich premium on a strong, easy-to-trade name - one of the better ones to sell right "
            "now.")
    elif snap.richness == "Rich":
        snap.verdict, snap.verdict_reason = "sell", (
            "Rich premium and easy to trade - good, though the company is only average quality "
            "(grade C), so be happy to own it if assigned.")
    elif solid:
        snap.verdict, snap.verdict_reason = "sell", (
            "Fair premium on a strong name - a steady, sensible one to sell.")
    else:
        snap.verdict, snap.verdict_reason = "okay", (
            "Fair premium on an average name - fine if you'd genuinely be happy owning the shares.")


_VERDICT_RANK = {"sell": 3, "okay": 2, "skip": 1}


def rank(snapshots: list[PremiumSnapshot]) -> list[PremiumSnapshot]:
    """Best call first (Good to sell > Okay > Skip), then by monthly income.
    Errors sink to the bottom."""
    return sorted(
        snapshots,
        key=lambda s: (s.error == "",
                       _VERDICT_RANK.get(s.verdict, 0) if not s.error else 0,
                       s.monthly_yield_pct or 0.0),
        reverse=True,
    )
