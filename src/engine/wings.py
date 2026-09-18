"""Adding the opposite wing to an open credit spread - the SOP checks.

Her Iron Condor page is written for a condor opened as a condor: both wings at
0.15 delta, so total delta lands on 0.30 and the win probability matches one
credit spread at 0.25. Legging in breaks that arithmetic, because the wing she
already holds was opened under a DIFFERENT page - the Put Credit Spread page
enters at 0.25 and the Call Credit Spread page at 0.10.

So a legged-in condor is usually wider-risk than the condor her SOP describes,
and the app says so in her own numbers. Rita's ruling (2026-09-18): WARN, never
block. She is making this call with live information the SOP could not
anticipate, and a rule that stops her acting on it is worse than one that tells
her what she is taking on.

Nothing here refuses a wing. `blocking()` is the one exception and it covers
two things that are not judgment calls at all: a wing on a different expiration
is not a condor, and a wing on the side she already holds is not a wing.

Pure functions: no Streamlit, no network, fully unit-tested.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.engine.models import Action, CheckResult, CheckStatus, OptionType
from src.engine.positions import Position

# Her SOP's own delta reference table, from the Iron Condor page. Used to say
# what a combined delta actually BUYS her, rather than only that it is over.
WIN_PROBABILITY = {0.10: 90, 0.15: 85, 0.20: 80, 0.30: 70, 0.50: 50, 0.60: 40}


def _win_probability(total_delta: float) -> Optional[int]:
    """The nearest row of her page's table, or None when it is off the end."""
    if total_delta <= 0:
        return None
    nearest = min(WIN_PROBABILITY, key=lambda d: abs(d - total_delta))
    return WIN_PROBABILITY[nearest] if abs(nearest - total_delta) <= 0.06 else None


def existing_side(position: Position) -> Optional[OptionType]:
    """Which side the open spread already has a SHORT leg on."""
    for leg in position.legs:
        if leg.action is Action.SELL:
            return leg.option_type
    return None


def missing_side(position: Position) -> Optional[OptionType]:
    """The side a wing would be added to - the opposite of what she holds."""
    have = existing_side(position)
    if have is None:
        return None
    return OptionType.CALL if have is OptionType.PUT else OptionType.PUT


def existing_short_delta(position: Position) -> float:
    """The open spread's short delta, unsigned. 0.0 when the row never carried
    one, which is not the same as zero risk - the caller says so."""
    return max((leg.abs_delta for leg in position.legs
                if leg.action is Action.SELL), default=0.0)


def existing_width(position: Position) -> float:
    """The open wing's width, from its own two legs."""
    side = existing_side(position)
    if side is None:
        return 0.0
    shorts = [l.strike for l in position.legs
              if l.action is Action.SELL and l.option_type is side]
    longs = [l.strike for l in position.legs
             if l.action is Action.BUY and l.option_type is side]
    if not shorts or not longs:
        return 0.0
    return abs(shorts[0] - longs[0])


def blocking(position: Position, option_type: str, short_strike: float,
             long_strike: float, expiration: Optional[date]) -> list[str]:
    """Reasons this is not a wing at all. Empty means go ahead.

    Deliberately short. Everything that is a judgment call lives in `checks()`
    and only warns; these three are the cases where saying yes would write a
    position into her log that is not the thing it claims to be.
    """
    out: list[str] = []
    side = OptionType.CALL if str(option_type).lower() == "call" else OptionType.PUT

    have = existing_side(position)
    if have is not None and side is have:
        out.append(
            f"This trade is already short a {have.value}. An iron condor needs "
            f"one wing on each side - adding a second {side.value} wing would "
            f"just be a bigger {side.value} spread, and the app would then "
            f"measure it against the wrong page.")

    if short_strike <= 0 or long_strike <= 0:
        out.append("Both strikes have to be filled in.")
    elif side is OptionType.CALL and long_strike <= short_strike:
        out.append(
            f"On a call wing the long strike protects ABOVE the short one, so "
            f"it has to be higher than {short_strike:g}. You have "
            f"{long_strike:g}.")
    elif side is OptionType.PUT and long_strike >= short_strike:
        out.append(
            f"On a put wing the long strike protects BELOW the short one, so "
            f"it has to be lower than {short_strike:g}. You have "
            f"{long_strike:g}.")

    if (expiration is not None and position.expiration is not None
            and expiration != position.expiration):
        out.append(
            f"An iron condor's two wings expire together. This trade expires "
            f"{position.expiration:%d %b %Y} and you have entered "
            f"{expiration:%d %b %Y}. Two different expirations is a different "
            f"trade (a double diagonal), and none of your eight strategies "
            f"covers it - log it as its own trade instead.")
    return out


def checks(position: Position, option_type: str, short_strike: float,
           long_strike: float, credit: float, short_delta: Optional[float],
           settings: dict[str, Any], strategy: dict[str, Any],
           quantity: int = 1) -> list[CheckResult]:
    """What she is taking on, in her own numbers. Never refuses anything.

    `strategy` is the iron_condor block from config - the page this position
    will be MANAGED by once the wing goes on, which is the one whose rules
    matter from here.
    """
    out: list[CheckResult] = []
    entry = strategy.get("entry", {}) or {}
    side = OptionType.CALL if str(option_type).lower() == "call" else OptionType.PUT

    # ---- 1. the delta her SOP is really about
    limit = float(entry.get("short_leg_delta_max", 0.15))
    have = existing_short_delta(position)
    new = abs(float(short_delta)) if short_delta is not None else 0.0
    total = round(have + new, 3)

    if short_delta is None:
        out.append(CheckResult(
            name="Combined short delta",
            status=CheckStatus.INFO,
            message=("No delta entered for the new wing, so the app cannot "
                     "check the one number your Iron Condor page is built "
                     f"around. Your open wing is at {have:.2f}; read the new "
                     "short strike's delta off thinkorswim and the two should "
                     f"add up to about {limit * 2:.2f}.")))
    elif total <= limit * 2 + 1e-9:
        out.append(CheckResult(
            name="Combined short delta",
            status=CheckStatus.PASS,
            message=(f"{have:.2f} + {new:.2f} = {total:.2f} total delta, which "
                     f"is inside the {limit * 2:.2f} your Iron Condor page "
                     f"targets ({limit:.2f} per leg). Roughly a "
                     f"{_win_probability(total) or 70}% win probability.")))
    else:
        prob = _win_probability(total)
        odds = (f" Your page's table puts that near a {prob}% win probability, "
                f"against the {_win_probability(limit * 2) or 70}% a condor "
                f"built to the page would have."
                if prob else "")
        out.append(CheckResult(
            name="Combined short delta",
            status=CheckStatus.WARN,
            message=(
                f"{have:.2f} + {new:.2f} = {total:.2f} total delta. Your Iron "
                f"Condor page wants {limit:.2f} per leg, {limit * 2:.2f} "
                f"combined.{odds} This is the thing that page calls its number "
                f"one beginner mistake - not because the trade is wrong, but "
                f"because a condor legged in from a {have:.2f} spread is a "
                f"wider bet than the one the 70% figure describes. Going ahead "
                f"is your call; the app just will not pretend the numbers "
                f"match."),
            expected=f"{limit * 2:.2f} combined",
            actual=f"{total:.2f}"))

    # ---- 2. both wings the same width, which is what makes max loss one wing
    widths = settings.get("spread_widths", {}) or {}
    kind = _underlying_kind(position.underlying, settings)
    want = float(widths.get("by_symbol", {}).get(
        position.underlying.upper(), widths.get(kind, 0)) or 0)
    new_width = abs(short_strike - long_strike)
    old_width = existing_width(position)

    if old_width > 0 and abs(new_width - old_width) > 1e-6:
        wider = max(new_width, old_width)
        out.append(CheckResult(
            name="Both wings the same width",
            status=CheckStatus.WARN,
            message=(
                f"Your open wing is {old_width:g} wide and this one is "
                f"{new_width:g}. That is allowed, but your max loss becomes the "
                f"WIDER side - {wider:g} - while you only collect the credit "
                f"once. Matching them keeps the risk on both sides the same."),
            expected=f"{old_width:g} wide", actual=f"{new_width:g} wide"))
    elif want and abs(new_width - want) > 1e-6:
        out.append(CheckResult(
            name="Wing width fits the underlying",
            status=CheckStatus.INFO,
            message=(f"{new_width:g} wide. Your SOP's width for this kind of "
                     f"underlying is {want:g}.")))

    # ---- 3. the minimum credit, measured the way her page measures it
    pct = entry.get("min_credit_pct_of_width")
    if pct:
        one_wing = max(new_width, old_width) or new_width
        floor = float(pct) * one_wing * 100
        total_credit = position.credit + credit
        ok = total_credit >= floor - 1e-9
        out.append(CheckResult(
            name=f"Net credit at least {float(pct) * 100:g}% of one wing",
            status=CheckStatus.PASS if ok else CheckStatus.WARN,
            message=(
                f"${total_credit:,.0f} collected across both wings "
                f"(${position.credit:,.0f} + ${credit:,.0f}) against a "
                f"${floor:,.0f} floor on a {one_wing:g}-wide wing."
                + ("" if ok else " Below your floor - the premium is not paying "
                                 "for the risk the second wing adds.")),
            expected=f"${floor:,.0f}", actual=f"${total_credit:,.0f}"))

    # ---- 3b. and the new wing judged on its own
    # The combined floor above is her Iron Condor page's rule and it is the
    # right one for the finished condor - but it is easily carried by the wing
    # she already holds. A second wing that collects almost nothing still adds
    # a side that can be breached, and each wing IS a credit spread, so the
    # credit-spread pages' own 6% floor is the fair test of it.
    if pct and new_width > 0:
        wing_floor = float(pct) * new_width * 100
        if credit < wing_floor - 1e-9:
            out.append(CheckResult(
                name="This wing pays for its own risk",
                status=CheckStatus.WARN,
                message=(
                    f"This wing collected ${credit:,.0f} on a {new_width:g}-wide "
                    f"spread. On its own it is a credit spread, and your credit "
                    f"spread pages want at least {float(pct) * 100:g}% of the "
                    f"width - ${wing_floor:,.0f}. The condor as a whole still "
                    f"clears the floor because the wing you already hold carries "
                    f"it, but this side is adding somewhere to lose without "
                    f"being paid much to take it on."),
                expected=f"${wing_floor:,.0f}", actual=f"${credit:,.0f}"))

    # ---- 4. the part that is pure good news, so it should be said out loud
    out.append(CheckResult(
        name="Buying power barely moves",
        status=CheckStatus.INFO,
        message=(
            "Price cannot break through both sides at once, so your broker "
            "holds margin for the wider wing only - not for two spreads. You "
            "collect a second credit for close to no extra buying power, which "
            "is the whole reason your SOP likes condors.")))

    # ---- 5. what changes about managing it
    out.append(CheckResult(
        name="It will be managed as an Iron Condor",
        status=CheckStatus.INFO,
        message=(
            f"Once this goes on, the app runs the Iron Condor page's rules "
            f"instead of the {position.strategy_name or 'credit spread'} "
            f"ones: close the WHOLE thing at 50% of the combined "
            f"${position.credit + credit:,.0f}, and at 21 DTE decide close or "
            f"roll per wing. Your page is explicit that one threatened wing is "
            f"not a reason to panic-close the other.")))

    return out


def _underlying_kind(symbol: str, settings: dict[str, Any]) -> str:
    """index / etf / stock, the same split the spread widths use."""
    sym = (symbol or "").upper()
    under = settings.get("underlyings", {}) or {}
    if sym in (under.get("european_style") or []):
        return "index"
    if sym in (under.get("us_style") or []):
        return "etf"
    return "stock"


def merge_candidates(position: Position,
                     others: list[Position]) -> list[Position]:
    """Open trades that look like the OTHER WING of this one.

    She logs each wing as it fills, which is right at the time - the second
    wing is often not planned when the first goes on. The result is two rows
    that are really one iron condor, showing as two cards with two separate 50%
    targets, each measured against half the credit. Her page's actual rule -
    close the whole thing at 50% of the NET credit - then applies to neither.

    What has to match is what makes two spreads one condor: the same
    underlying, the same expiration, the same book, and opposite sides. Nothing
    else is required, and contract counts deliberately are not: a 2-contract
    put wing against a 1-contract call wing is a lopsided condor, not two
    trades, and the form says so rather than hiding the pair.

    Returned newest first, because when there is more than one the most
    recently opened is nearly always the one she just filled.
    """
    if position.status != "open":
        return []
    side = existing_side(position)
    if side is None or position.expiration is None:
        return []

    out = []
    for other in others:
        if other is position or other.status != "open":
            continue
        if other.trade_id == position.trade_id:
            continue
        if other.underlying.upper() != position.underlying.upper():
            continue
        if other.expiration != position.expiration:
            continue
        if (other.account or "") != (position.account or ""):
            continue          # a paper wing is not part of a real condor
        if other.is_iron_condor_shape or other.is_debit:
            continue
        theirs = existing_side(other)
        if theirs is None or theirs is side:
            continue          # same side is a second spread, not a wing
        if not any(l.action is Action.BUY and l.option_type is theirs
                   for l in other.legs):
            continue          # no long leg: a naked put is not a wing
        out.append(other)
    return sorted(out, key=lambda p: p.opened or date.min, reverse=True)
