"""Builds the "Today's Picks" report: who looks good to sell premium on, and why.

Pure composition - no network and no Streamlit. The app fetches the data
(chains, premium snapshots, fundamentals) and hands it here; this module turns
it into ranked, plain-English candidates:

  - Index picks (SPX, NDX, RUT, XSP): the trend-fitting credit-spread strategy
    with a REAL scanned setup at the SOP delta on the monthly expiration.
  - Income picks (stocks and ETFs): the cash-secured-put / covered-call read
    from the premium finder, plus the dividend and a risk picture.

Everything is decision support: candidates with reasons, never instructions.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field

from src.data import market_calendar, market_events, premium_finder
from src.data.chain import OptionChain
from src.data.market_context import MarketContext
from src.data.market_events import Event
from src.data.premium_finder import PremiumSnapshot
from src.engine import scanner
from src.engine.config_loader import get_strategy
from src.engine.models import Candidate, OptionType
from src.research import fundamentals


# ------------------------------------------------------------------ models
class MonthlyTarget(BaseModel):
    """The monthly (3rd Friday) expiration the picks aim at."""

    expiration: dt.date
    dte: int
    label: str                       # "Fri Aug 21 - about 44 days away (the monthly expiration)"
    within_sop: bool = True          # False when even the next monthly sits past dte_max


class DividendView(BaseModel):
    """A name's dividend, read from the already-fetched fundamentals dict."""

    pays: bool = False
    annual_rate: Optional[float] = None      # dollars per share per year
    yield_pct: Optional[float] = None        # 1.32 means 1.32% per year
    ex_div_date: Optional[dt.date] = None
    note: str = ""


class IndexPick(BaseModel):
    """A defined-risk credit-spread candidate with a real scanned setup.

    Usually a cash-settled index; also used for a bearish Call Credit Spread on a
    big, strong stock (american=True) when it is trending down.
    """

    symbol: str
    american: bool = False                    # True = stock/ETF (early assignment possible)
    price: Optional[float] = None
    trend: str = "unknown"
    strategy_key: str
    strategy_name: str
    strategy_reason: str = ""
    candidate: Optional[Candidate] = None    # the scanned monthly-expiration trade
    expiry_note: str = ""
    richness: str = "n/a"                    # Rich / Fair / Thin premium read
    iv_hv_ratio: Optional[float] = None
    liquidity: str = "n/a"
    spread_pct: Optional[float] = None
    open_interest: Optional[int] = None
    events: list[Event] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    sop_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str = ""


class IncomePick(BaseModel):
    """One stock/ETF income candidate: premium snapshot + dividend + risk math."""

    snapshot: PremiumSnapshot
    kind: str                                # "etf" | "stock"
    strategy_key: str
    dividend: DividendView = Field(default_factory=DividendView)
    bp_required: Optional[float] = None      # dollars this trade ties up (1 contract)
    bp_pct_of_limit: Optional[float] = None
    events: list[Event] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    sop_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CoveredCallPick(BaseModel):
    """One covered-call candidate: buy (or already own) 100 shares, sell a call.

    Kept separate from IncomePick because it answers a different question. The
    income picks are led by the PUT you would sell, and a downtrending name is
    a hard skip there - which is exactly why covered calls never appeared:
    fitting_strategy_key only ever reached for one on a downtrend, and the put
    verdict had already deleted those names. This side is judged on its own
    merits, on any trend, and only on what the call pays.
    """

    symbol: str
    kind: str = "stock"                       # "etf" | "stock"
    price: Optional[float] = None
    shares_cost: Optional[float] = None       # 100 shares
    call_strike: Optional[float] = None
    call_credit: Optional[float] = None       # dollars for 1 contract
    call_delta: Optional[float] = None        # ~0.30, her SOP's covered-call strike
    dte: Optional[int] = None
    monthly_yield_pct: Optional[float] = None      # credit / shares, this expiry
    annualized_yield_pct: Optional[float] = None   # same rate repeated for a year
    upside_pct: Optional[float] = None        # room to the strike before called away
    total_if_called_pct: Optional[float] = None    # credit + that upside
    downside_cushion_pct: Optional[float] = None   # how far it can fall before a loss
    strategy_key: str = "covered_call_model_2"
    strategy_name: str = ""
    grade: Optional[str] = None
    trend: str = "unknown"
    liquidity: str = "n/a"
    richness: str = "n/a"
    dividend: DividendView = Field(default_factory=DividendView)
    events: list[Event] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verdict: str = "okay"                     # "sell" | "okay" | "skip"
    verdict_reason: str = ""


class PicksReport(BaseModel):
    """Everything the Picks tab renders, built in one scan."""

    generated_at: str = ""                   # "14:32"
    scope: str = "quick"
    monthly: MonthlyTarget
    vix: Optional[float] = None
    funnel_note: str = ""
    index_picks: list[IndexPick] = Field(default_factory=list)
    bearish_picks: list[IndexPick] = Field(default_factory=list)   # bear call spreads on strong fallers
    income_picks: list[IncomePick] = Field(default_factory=list)
    covered_call_picks: list[CoveredCallPick] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)     # no data / errored
    left_out: list[str] = Field(default_factory=list)     # dropped for quality (with reason)


# ------------------------------------------------------------------ the monthly expiration
def monthly_target(today: Optional[dt.date] = None,
                   dte_min: int = 21, dte_max: int = 49) -> MonthlyTarget:
    """The next monthly (3rd Friday) expiration at least dte_min days out.

    Rolls to the following month when the nearest monthly is too close (your
    SOP never enters under ~21 days). Holiday Fridays shift back a day, same
    as real option expirations do.
    """
    today = today or dt.date.today()
    raw = market_events.next_opex(today)
    expiration = market_calendar.adjust_back_if_closed(raw)
    while (expiration - today).days < dte_min:
        raw = market_events.next_opex(raw + dt.timedelta(days=1))
        expiration = market_calendar.adjust_back_if_closed(raw)
    dte = (expiration - today).days
    within = dte <= dte_max
    label = f"{expiration:%a %b %d} - about {dte} days away (the monthly expiration)"
    if not within:
        label += " - a bit past your preferred entry window"
    return MonthlyTarget(expiration=expiration, dte=dte, label=label, within_sop=within)


def chain_for_expiration(chain: OptionChain, expiration: dt.date) -> OptionChain:
    """Only the contracts expiring exactly on `expiration` - never a nearby weekly."""
    iso = expiration.isoformat()
    kept = [c for c in chain.contracts if c.expiration == iso]
    return OptionChain(underlying=chain.underlying, underlying_price=chain.underlying_price,
                       fetched_at=chain.fetched_at, contracts=kept)


# ------------------------------------------------------------------ dividends
def _pos_float(value) -> Optional[float]:
    try:
        f = float(value)
        return f if f == f and f > 0 else None   # NaN-safe
    except (TypeError, ValueError):
        return None


def _epoch_date(value) -> Optional[dt.date]:
    """Yahoo's exDividendDate is epoch seconds; tolerate ISO strings and dates too."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        if isinstance(value, str):
            return dt.date.fromisoformat(value[:10])
        return dt.datetime.fromtimestamp(float(value), dt.timezone.utc).date()
    except (ValueError, OSError, OverflowError, TypeError):
        return None


def dividend_view(info: dict, price: Optional[float]) -> DividendView:
    """The dividend picture from the already-cached fundamentals dict (no fetch).

    Prefers the dollar rate divided by price (unambiguous units); only falls
    back to Yahoo's yield fields, which need unit normalization.
    """
    info = info or {}
    rate = (_pos_float(info.get("trailingAnnualDividendRate"))
            or _pos_float(info.get("dividendRate")))
    # One shared implementation of the rate-first rule (see fundamentals); this
    # view only differs in wanting None rather than 0.0 for a non-payer.
    computed = fundamentals.dividend_yield_pct(info, price)
    yield_pct = round(computed, 2) if computed else None

    ex_div = _epoch_date(info.get("exDividendDate"))
    pays = bool(yield_pct and yield_pct > 0)
    if pays and rate is not None:
        note = f"Pays about ${rate:,.2f}/share a year (~{yield_pct:.1f}% yield)."
    elif pays:
        note = f"Pays a dividend of about {yield_pct:.1f}% a year."
    else:
        note = "No dividend - the option premium is the whole income here."
    return DividendView(pays=pays, annual_rate=rate, yield_pct=yield_pct,
                        ex_div_date=ex_div, note=note)


# ------------------------------------------------------------------ SOP wording
def sop_summary(strategy: dict) -> list[str]:
    """The strategy's SOP rules from strategies.yaml as plain-English lines."""
    entry = strategy.get("entry", {})
    exits = strategy.get("exit", {})
    lines: list[str] = []

    if "short_leg_delta_max" in entry:
        d = float(entry["short_leg_delta_max"])
        lines.append(f"Sell the short leg at delta {d:.2f} or below - roughly a "
                     f"{(1 - d) * 100:.0f}% chance it expires worthless.")
    elif "short_call_delta" in entry:
        d = float(entry["short_call_delta"])
        lines.append(f"Sell the call at about delta {d:.2f}.")

    if "dte_min" in entry and "dte_max" in entry:
        line = (f"Enter {int(entry['dte_min'])}-{int(entry['dte_max'])} days to expiration "
                f"(prefer ~{int(entry.get('dte_target', entry['dte_max']))})")
        if "dte_min_us_style" in entry:
            line += (f"; stocks and ETFs use {int(entry['dte_min_us_style'])}-"
                     f"{int(entry['dte_max_us_style'])} days to dodge the early-assignment zone")
        lines.append(line + ".")
    elif "short_call_dte_target" in entry:
        lines.append(f"Sell the short call about {int(entry['short_call_dte_target'])} days out.")

    exit_bits = []
    if exits.get("stop_loss_multiple"):
        exit_bits.append(f"stop the loss at {float(exits['stop_loss_multiple']):g}x the credit")
    if exits.get("time_exit_dte"):
        exit_bits.append(f"close at {int(exits['time_exit_dte'])} days left no matter what")
    if exits.get("profit_target_pct"):
        exit_bits.append(f"take the win at {float(exits['profit_target_pct']):g}% of the credit")
    if exit_bits:
        lines.append("Exits, in priority order: " + "; ".join(exit_bits) + ".")
    return lines


# ------------------------------------------------------------------ strategy fit (income names)
def fitting_strategy_key(snap: PremiumSnapshot, monthly_bp: float,
                         vix: Optional[float] = None) -> str:
    """Which of the SOP's US-style strategies fits this snapshot.

    Mirrors the premium finder's plan: a downtrend means calls-only (the
    protective Model 1 collar when fear is high, else the classic Model 2);
    a name too pricey for the monthly buying power points to the PMCC;
    otherwise the cash secured put.
    """
    if snap.trend == "down":
        return "covered_call_model_1" if (vix is not None and vix >= 22) else "covered_call_model_2"
    if (snap.short_strike or 0) * 100 > monthly_bp:
        return "poor_mans_covered_call"
    return "cash_secured_put"


# ------------------------------------------------------------------ index picks
_TREND_WORD = {"up": "leaning up", "down": "leaning down",
               "sideways": "moving sideways", "unknown": "direction unclear"}


def build_index_pick(
    symbol: str,
    ctx: MarketContext,
    monthly_chain: OptionChain,
    hv: Optional[float],
    monthly: MonthlyTarget,
    today: Optional[dt.date] = None,
    fallback_chain: Optional[OptionChain] = None,
    earnings_date: Optional[dt.date] = None,
    american: bool = False,
) -> IndexPick:
    """One credit-spread candidate: the trend-fitting strategy plus a REAL scanned
    setup. Used for cash-settled indexes and for a bearish Call Credit Spread on a
    strong, big stock (american=True, which adds early-assignment and earnings
    warnings).

    Scans the monthly expiration first; if that sits outside the strategy's SOP
    window (it happens the week before expiration), scans the normal SOP window
    instead and says so in expiry_note.
    """
    today = today or dt.date.today()
    strategy_key = ctx.best_strategy_key or "iron_condor"
    strategy = get_strategy(strategy_key)

    cands: list[Candidate] = []
    used_chain = monthly_chain
    expiry_note = monthly.label
    if monthly_chain.contracts:
        cands = scanner.scan_setups(strategy_key, monthly_chain, width=None,
                                    contracts=1, max_setups=1)
    if not cands and fallback_chain is not None and fallback_chain.contracts:
        cands = scanner.scan_setups(strategy_key, fallback_chain, width=None,
                                    contracts=1, max_setups=1)
        if cands:
            used_chain = fallback_chain
            expiry_note = (f"The monthly ({monthly.expiration:%b %d}) sits outside this "
                           f"strategy's SOP window right now - showing a "
                           f"{cands[0].dte}-day setup instead.")
    cand = cands[0] if cands else None

    price = used_chain.underlying_price or ctx.price or None
    puts = [c for c in used_chain.contracts if c.option_type == OptionType.PUT]
    atm_iv = premium_finder._nearest_atm_iv(puts, price or 0.0)
    iv_hv = round(atm_iv / hv, 2) if (atm_iv and hv) else None
    richness = premium_finder._richness(iv_hv, atm_iv)

    spread_pct, oi, liq = None, None, "n/a"
    if cand is not None and cand.trade.short_legs:
        short_leg = max(cand.trade.short_legs, key=lambda l: l.abs_delta)
        contract = used_chain.find(short_leg.option_type, short_leg.dte or cand.dte or 0,
                                   short_leg.strike)
        if contract is not None:
            spread_pct, oi, liq = premium_finder._liquidity(contract)

    dte = cand.dte if cand else monthly.dte
    events = market_events.upcoming_events(
        from_date=today, horizon_days=max(45, dte), trade_dte=dte,
        earnings_date=earnings_date)

    why: list[str] = [f"{symbol} is {_TREND_WORD.get(ctx.trend, ctx.trend)} - "
                      f"{ctx.recommendation_reason}"]
    warnings: list[str] = []
    error = ""
    if cand is not None:
        why.append(f"A real {cand.dte}-day setup at your SOP delta collects about "
                   f"${cand.credit:,.0f} against ${cand.max_loss:,.0f} of risk "
                   f"({cand.return_on_risk * 100:.0f}% return on risk, 1 contract).")
        if not cand.fits_sop and cand.note:
            warnings.append(cand.note)
    else:
        error = "No setup at your SOP delta on this expiration right now."
    if richness in ("Rich", "Fair", "Thin"):
        why.append({"Rich": "Premium is Rich - you are paid more than its usual "
                            "moves would justify.",
                    "Fair": "Premium is Fair - about normal for how much it moves.",
                    "Thin": "Premium is Thin - it pays little for the risk right now.",
                    }[richness])
    if ctx.vol_bucket == "high":
        warnings.append("Volatility is high - premiums are rich but moves are bigger. "
                        "Keep size small and deltas low.")
    if liq == "Thin":
        warnings.append("The short strike is hard to trade right now (wide bid-ask spread).")
    for e in events:
        if not e.in_window:
            continue
        if e.kind in ("fomc", "jobs"):
            warnings.append(f"{e.label} on {e.date:%b %d} lands inside this trade window - "
                            f"{e.note}")
        elif e.kind == "earnings":
            warnings.append(f"Earnings on {e.date:%b %d} land inside this trade - your SOP "
                            "says no credit spreads through earnings. Pick an expiration "
                            "before it, or skip this name.")

    return IndexPick(
        symbol=symbol, american=american, price=price, trend=ctx.trend,
        strategy_key=strategy_key, strategy_name=strategy.get("name", strategy_key),
        strategy_reason=ctx.recommendation_reason,
        candidate=cand, expiry_note=expiry_note,
        richness=richness, iv_hv_ratio=iv_hv,
        liquidity=liq, spread_pct=spread_pct, open_interest=oi,
        events=events, why=why, sop_notes=sop_summary(strategy),
        warnings=warnings, error=error,
    )


def is_strong_bearish_stock(kind: str, symbol: str, trend: str, biggest: set) -> bool:
    """True only for a downtrending name that is one of the biggest, strongest
    STOCKS - measured by membership in the top-market-cap set (mega-caps: the
    largest, most established, most liquid companies). Everything else in a
    downtrend is left out (a bearish index play is the cleaner route).

    We gate on market cap, NOT the fundamentals letter grade: a downtrend already
    docks that grade, and Yahoo throttles the fundamentals fields on the hosted
    app - so an A/B grade gate hid essentially every bearish play there. For a
    defined-risk bear call spread (you never own the shares) big and liquid is
    what matters, and the top-cap set captures both.

    biggest: the set of top-market-cap tickers eligible for the bearish path.
    """
    return trend == "down" and kind == "stock" and symbol.upper() in biggest


# ------------------------------------------------------------------ income picks (stocks/ETFs)
def build_income_pick(
    snap: PremiumSnapshot,
    kind: str,
    info: dict,
    monthly: MonthlyTarget,
    monthly_bp: float,
    bp_limit: float,
    vix: Optional[float] = None,
    today: Optional[dt.date] = None,
) -> IncomePick:
    """One stock/ETF candidate: the premium read plus dividend, events, and risk."""
    today = today or dt.date.today()
    div = dividend_view(info, snap.price)

    if snap.error:
        return IncomePick(snapshot=snap, kind=kind, strategy_key="cash_secured_put",
                          dividend=div)

    strategy_key = fitting_strategy_key(snap, monthly_bp, vix)
    strategy = get_strategy(strategy_key)

    bp_required: Optional[float] = None
    if strategy_key == "cash_secured_put" and snap.short_strike:
        bp_required = round(snap.short_strike * 100 - (snap.credit_dollars or 0), 0)
    elif strategy_key.startswith("covered_call") and snap.price:
        bp_required = round(snap.price * 100, 0)   # the 100 shares it is written against
    bp_pct = round(bp_required / bp_limit * 100, 1) if (bp_required and bp_limit) else None

    ex_div_ahead = div.ex_div_date if (div.ex_div_date and div.ex_div_date >= today) else None
    dte = snap.dte or monthly.dte
    events = market_events.upcoming_events(
        from_date=today, horizon_days=max(45, dte), trade_dte=dte,
        earnings_date=snap.earnings_date, ex_div_date=ex_div_ahead)

    warnings: list[str] = list(snap.flags)
    if kind == "stock" and snap.earnings_date is None:
        warnings.append("Couldn't verify the earnings date (the data source sometimes hides "
                        "it on the hosted app). Check it in thinkorswim before entering.")
    if snap.dte:
        exp_used = today + dt.timedelta(days=snap.dte)
        if abs((exp_used - monthly.expiration).days) > 2:
            warnings.append(f"Priced on the expiration about {snap.dte} days out (the nearest "
                            f"listed one), not the {monthly.expiration:%b %d} monthly.")
    if (ex_div_ahead and strategy_key != "cash_secured_put"
            and (ex_div_ahead - today).days <= dte):
        warnings.append(f"Ex-dividend on {ex_div_ahead:%b %d} lands inside the window - a short "
                        "call can be assigned early right before it (the buyer wants the "
                        "dividend).")

    why: list[str] = []
    if snap.verdict_reason:
        why.append(snap.verdict_reason)
    if snap.credit_dollars and snap.monthly_yield_pct:
        why.append(f"Collects about ${snap.credit_dollars:,.0f} for the month "
                   f"(~{snap.monthly_yield_pct:.2f}% of the cash set aside).")
    if div.pays:
        why.append(div.note + " A bonus that only lands if you end up owning the shares.")

    return IncomePick(
        snapshot=snap, kind=kind, strategy_key=strategy_key, dividend=div,
        bp_required=bp_required, bp_pct_of_limit=bp_pct,
        events=events, why=why, sop_notes=sop_summary(strategy), warnings=warnings,
    )


# ------------------------------------------------------------------ ranking
def rank_index_picks(picks: list[IndexPick]) -> list[IndexPick]:
    """Real SOP-fitting setups first (richest return-on-risk first), errors last."""
    def key(p: IndexPick):
        has = p.candidate is not None
        fits = bool(has and p.candidate.fits_sop)
        ror = p.candidate.return_on_risk if has else 0.0
        return (has, fits, ror)
    return sorted(picks, key=key, reverse=True)


# ------------------------------------------------------- covered call picks
# A covered call is judged on what the CALL pays against the cost of the
# shares, so it needs its own bar. Rita's ruling: ignore the monthly buying
# power budget here and assume she can buy the 100 shares - she wants the
# candidates on their merits, with the yields, and will size it herself.
CC_MIN_MONTHLY_YIELD = 0.8      # % of the share cost - below this it is not worth the capital
CC_MIN_UPSIDE_PCT = 1.0         # % room to the strike - less and it is called away at once
# The bar for "good" rather than merely "okay". 1.25% a month on the shares is
# about 15% a year at her ~30-day expiries, which is a genuinely strong covered
# call on a quality large-cap - most sit nearer 1%.
CC_STRONG_MONTHLY_YIELD = 1.25
def cc_dte_window() -> tuple[int, int, int]:
    """(min, target, max) days for a covered call's short call, from config.

    Rita's call (2026-07-26): 30-45 days, not the nearest monthly - the
    monthlies sit at 26 and 54 right now, so neither lands in her window.

    Read from strategies.yaml rather than pinned here. This started life as
    three module constants, which quietly broke the rule the whole app is built
    on: her numbers live in config and the code follows. Change the rule there
    and the Picks candidates, the Find a trade scan and this all move together.
    """
    entry = get_strategy("covered_call_model_2").get("entry", {})
    target = int(entry.get("short_call_dte_target", 37))
    lo = int(entry.get("short_call_dte_min", max(target - 7, 14)))
    hi = int(entry.get("short_call_dte_max", target + 8))
    return lo, target, hi


def covered_call_model(trend: str, vix: Optional[float]) -> str:
    """Which of her three covered-call models fits.

    Her SOP: Model 1 (the collar) is the high-fear / bearish one, Model 2 (the
    classic put spread + short call) is the neutral default. Model 3 is the
    advanced zero-cost ratio and is never auto-suggested - losses accelerate
    below the short puts, so she picks that one deliberately or not at all.
    """
    if trend == "down":
        return "covered_call_model_1" if (vix is not None and vix >= 22) else "covered_call_model_2"
    return "covered_call_model_2"


def build_covered_call_pick(snap: PremiumSnapshot, kind: str, info: dict,
                            monthly: MonthlyTarget, vix: Optional[float] = None,
                            today: Optional[dt.date] = None) -> CoveredCallPick:
    """Score one name as a covered call, on the call side only."""
    today = today or dt.date.today()
    price = snap.price or 0.0
    shares = round(price * 100, 0) if price else None
    credit = snap.call_credit_dollars
    dte = snap.dte or monthly.dte or 30

    monthly_yield = ann_yield = upside = total_if_called = cushion = None
    if shares and credit:
        monthly_yield = round(credit / shares * 100, 2)
        # The same rate repeated to a year - simple, not compounded, because she
        # would have to keep finding the same trade every month to earn it.
        ann_yield = round(monthly_yield * 365 / max(dte, 1), 1)
    if price and snap.call_strike:
        upside = round((snap.call_strike - price) / price * 100, 2)
        if monthly_yield is not None:
            total_if_called = round(monthly_yield + max(upside, 0.0), 2)
    if price and credit:
        # Selling the call pays for this much of a fall before the shares lose.
        cushion = round(credit / 100 / price * 100, 2)

    key = covered_call_model(snap.trend, vix)
    strategy = get_strategy(key)
    pick = CoveredCallPick(
        symbol=snap.symbol, kind=kind, price=price or None, shares_cost=shares,
        call_strike=snap.call_strike, call_credit=credit, dte=dte,
        call_delta=snap.call_delta,
        monthly_yield_pct=monthly_yield, annualized_yield_pct=ann_yield,
        upside_pct=upside, total_if_called_pct=total_if_called,
        downside_cushion_pct=cushion,
        strategy_key=key, strategy_name=strategy.get("name", key),
        grade=snap.grade, trend=snap.trend, liquidity=snap.liquidity,
        richness=snap.richness, dividend=dividend_view(info, price or None),
        events=[],
    )
    _set_cc_verdict(pick, snap)
    _explain_cc(pick, snap)
    return pick


def _set_cc_verdict(pick: CoveredCallPick, snap: PremiumSnapshot) -> None:
    """Good, okay, or not worth it - judged only on the covered call."""
    if not pick.call_credit or pick.monthly_yield_pct is None:
        pick.verdict, pick.verdict_reason = "skip", (
            "No call price came back for this one, so there is nothing to judge.")
        return
    if pick.liquidity == "Thin":
        pick.verdict, pick.verdict_reason = "skip", (
            "Hard to trade - the gap between the buy and sell price would eat the premium.")
        return
    if pick.grade in ("D", "F"):
        pick.verdict, pick.verdict_reason = "skip", (
            f"Weak company (grade {pick.grade}). A covered call means OWNING the shares, "
            "so the quality of the business matters more than the premium.")
        return
    if pick.monthly_yield_pct < CC_MIN_MONTHLY_YIELD:
        pick.verdict, pick.verdict_reason = "skip", (
            f"The call only pays {pick.monthly_yield_pct:.2f}% of the share cost for the "
            "month - too little for the money you would tie up in the shares.")
        return
    if pick.upside_pct is not None and pick.upside_pct < CC_MIN_UPSIDE_PCT:
        pick.verdict, pick.verdict_reason = "skip", (
            f"Only {pick.upside_pct:.1f}% of room to the strike - the shares would very "
            "likely be called away straight away.")
        return
    if snap.earnings_before_expiry:
        pick.verdict, pick.verdict_reason = "okay", (
            "The premium is worth having, but earnings land before this expires - a "
            "coin-flip event while you hold the shares. Pick an earlier expiration or wait.")
        return
    if pick.trend == "down":
        pick.verdict, pick.verdict_reason = "okay", (
            "The call pays well, but the shares are in a downtrend - the premium cushions "
            "a fall, it does not stop one. This is what the protective models are for.")
        return
    if (pick.monthly_yield_pct >= CC_STRONG_MONTHLY_YIELD
            and pick.grade in ("A", "B", None)):
        pick.verdict, pick.verdict_reason = "sell", (
            "Strong name, easy to trade, and the call pays properly for the month.")
        return
    pick.verdict, pick.verdict_reason = "okay", (
        "A reasonable covered call - decent premium on a name worth owning.")


def _explain_cc(pick: CoveredCallPick, snap: PremiumSnapshot) -> None:
    p = pick
    if p.call_strike and p.price and p.call_credit:
        p.why.append(
            f"Buy 100 shares at about ${p.price:,.2f} (${p.shares_cost:,.0f}) and sell the "
            f"${p.call_strike:g} call {p.dte} days out for ${p.call_credit:,.0f}.")
    if p.monthly_yield_pct is not None:
        p.why.append(
            f"That is {p.monthly_yield_pct:.2f}% of the share cost for the month, or about "
            f"{p.annualized_yield_pct:.0f}% a year if you kept repeating it.")
    if p.total_if_called_pct is not None and p.upside_pct is not None:
        p.why.append(
            f"If it rises past ${p.call_strike:g} the shares are called away and you keep "
            f"{p.total_if_called_pct:.2f}% - the {p.monthly_yield_pct:.2f}% premium plus "
            f"{p.upside_pct:.2f}% of price rise. That is your cap for the month.")
    if p.downside_cushion_pct is not None:
        p.why.append(
            f"The premium covers the first {p.downside_cushion_pct:.2f}% of any fall. "
            "Below that the shares lose money like any other shares.")
    if p.dividend.pays and p.dividend.note:
        p.why.append(p.dividend.note)
        p.warnings.append(
            "You own the shares, so you collect the dividend - but a short call can be "
            "assigned early right before the ex-dividend date if it is in the money.")
    lo, _target, hi = cc_dte_window()
    if p.dte is not None and not (lo <= p.dte <= hi):
        p.warnings.append(
            f"This is {p.dte} days out, outside your {lo}-{hi} day window. "
            "Nothing inside that window trades well enough on this name today - the "
            "expirations in there are weeklies with almost no open interest, where the "
            "bid-ask gap costs more than the extra days of premium are worth.")
    p.warnings.append(
        "A covered call needs 100 real shares per contract. Buying them is the bulk of the "
        "money here, and the shares can fall further than the premium you collected.")


def rank_covered_call_picks(picks: list[CoveredCallPick]) -> list[CoveredCallPick]:
    """Best verdict first, then the fattest monthly yield."""
    return sorted(picks,
                  key=lambda p: (premium_finder._VERDICT_RANK.get(p.verdict, 0),
                                 p.monthly_yield_pct or 0.0), reverse=True)


def rank_income_picks(picks: list[IncomePick]) -> list[IncomePick]:
    """Verdict first (sell > okay > skip), then yield in 0.5% buckets, with the
    dividend breaking near-ties - so a nice dividend nudges, never dominates."""
    def key(p: IncomePick):
        s = p.snapshot
        ok = s.error == ""
        verdict = premium_finder._VERDICT_RANK.get(s.verdict, 0) if ok else 0
        y = s.monthly_yield_pct or 0.0
        return (ok, verdict, int(y / 0.5), p.dividend.yield_pct or 0.0, y)
    return sorted(picks, key=key, reverse=True)


def _keep_spreads(picks: list[IndexPick], label: str, left_out: list[str]) -> list[IndexPick]:
    """Keep spread picks that have a real, tradeable setup; drop the rest with a reason."""
    kept = []
    for p in picks:
        if p.candidate is None:
            left_out.append(f"{p.symbol} ({label}) - "
                            f"{p.error or 'no setup at your SOP delta right now'}")
        elif p.liquidity == "Thin":
            left_out.append(f"{p.symbol} ({label}) - hard to trade "
                            "(wide bid-ask spread on the short strike)")
        else:
            kept.append(p)
    return kept


def keep_best(index_picks: list[IndexPick], income_picks: list[IncomePick],
              bearish_picks: Optional[list[IndexPick]] = None):
    """Show only the best candidates, not the "skip"s.

    Drops (with a plain-English reason, so nothing is hidden):
      - income names the SOP grades "skip" - hard to trade, downtrend, weak
        company, or thin premium
      - index / bearish picks with no setup at the SOP delta, or a short strike
        that is hard to trade (a thin, wide bid-ask market)

    Returns (kept_index, kept_income, kept_bearish, left_out) in ranked order.
    """
    left_out: list[str] = []
    kept_ix = _keep_spreads(index_picks, "index", left_out)
    kept_bear = _keep_spreads(bearish_picks or [], "bearish", left_out)
    kept_inc = []
    for p in income_picks:
        if p.snapshot.verdict == "skip":
            reason = p.snapshot.verdict_reason or "not a good candidate right now"
            left_out.append(f"{p.snapshot.symbol} - {reason}")
        else:
            kept_inc.append(p)
    return kept_ix, kept_inc, kept_bear, left_out
