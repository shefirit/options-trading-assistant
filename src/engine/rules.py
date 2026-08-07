"""Individual SOP checks. Each function looks at one rule and returns a
CheckResult (green / red / warning / info) written in plain English.

The validator (validator.py) runs the ones that apply to a given strategy and
collects them into the checklist the user sees. Keeping each rule tiny makes
them easy to read and easy to unit-test.
"""

from __future__ import annotations

from typing import Any, Optional

from src.engine.models import (
    Action,
    CheckResult,
    CheckStatus,
    OptionType,
    Trade,
)

# How far from a target delta / DTE we still call it "on target".
DELTA_TOLERANCE = 0.05      # e.g. target 0.30 is fine between 0.25 and 0.35
DTE_TOLERANCE = 7           # days

# The entry window (21-45) and the 21-DTE time exit overlap, so a trade entered
# at the very bottom of the window passes the range check and still has to be
# closed within days. Below this much room the trade is over almost as soon as
# it starts: no time for the 50% profit target, and all the cost of entering.
MIN_TIME_EXIT_RUNWAY = 10   # days between entry and the time exit
VERY_SHORT_RUNWAY = 4       # at or under this, the trade is over before it starts

# The minimum-credit check's name starts with this. The scanner matches on it to
# tell a thin-credit setup apart from other failures, so both sides must agree -
# hence one constant rather than the string written out twice.
MIN_CREDIT_CHECK_PREFIX = "Credit at least"


def check_underlying_style(trade: Trade, allowed: list[str],
                           fits_style: Optional[bool] = None) -> CheckResult:
    """Is this a name the strategy can actually run on?

    `allowed` only holds the names the app can offer, and those come from the
    S&P 500 / Nasdaq-100 universe files. A liquid name outside them (SOFI) is
    still valid per the SOP - "any liquid stock, ETF, or index" - so the caller
    passes `fits_style`, the option-style verdict, and that decides when given.
    """
    ok = trade.underlying in allowed if fits_style is None else fits_style
    # The allowed list can be hundreds of names, so summarize instead of listing all.
    has_index = any(s in allowed for s in ("SPX", "NDX", "RUT", "XSP"))
    if has_index:   # credit spreads / iron condors - any liquid name per the SOP
        hint = ("Credit spreads use any liquid stock, ETF, or index (SPX, QQQ, AAPL...). "
                "Just make sure it's liquid enough to enter and exit easily.")
        expected = "any liquid stock, ETF, or index"
    else:           # cash secured puts / covered calls / PMCC
        hint = ("Cash secured puts and covered calls need a name you can own shares of - "
                "a stock or an ETF. Cash-settled indexes (SPX, NDX, RUT, XSP) can never "
                "be assigned, so there are no shares for them to land on.")
        expected = "US-style stock or ETF"
    return CheckResult(
        name="Right underlying for this strategy",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"{trade.underlying} is allowed for this strategy."
            if ok else f"{trade.underlying} is not allowed here. {hint}"
        ),
        expected=expected,
        actual=trade.underlying,
    )


def delta_missing(leg) -> bool:
    """Whether this leg's delta was never filled in.

    Leg.delta defaults to 0.0, and a real option chain never returns exactly
    zero - even a far out-of-the-money strike prices at 0.001-something. So an
    exact 0.0 on a leg means "the chain lookup did not fill this in", not "this
    option has no sensitivity to price".

    The distinction matters because the checks below compare against a MAXIMUM.
    An unfilled 0.0 sails under every limit and used to come back as a green
    tick reading "delta 0.000 - within your limit", which is the checklist
    telling her a trade was verified when nothing was measured.
    """
    return leg.delta == 0.0


def _tell_apart(value: float, limit: float, most: int = 5) -> str:
    """Format `value` with just enough decimals to look different from `limit`.

    A real SPX scan produced "has delta 0.250 - OVER your 0.25 limit", because
    the delta was 0.2503 and three decimals round it onto the limit. The
    verdict was right and the evidence beside it looked like a typo, which is
    the fastest way to make her stop trusting the checklist.
    """
    for places in range(3, most + 1):
        if f"{value:.{places}f}" != f"{limit:.{places}f}":
            return f"{value:.{places}f}"
    return f"{value:.{most}f}"


def check_short_leg_delta_max(trade: Trade, max_delta: float) -> list[CheckResult]:
    """Every option you SELL must have delta at or under the limit (e.g. < 0.10)."""
    results: list[CheckResult] = []
    for leg in trade.short_legs:
        name = f"Short {leg.option_type.value} delta under {max_delta:.2f}"
        if delta_missing(leg):
            results.append(CheckResult(
                name=name,
                status=CheckStatus.WARN,
                message=(
                    f"No delta came through for the {leg.strike:g} "
                    f"{leg.option_type.value} you are selling, so this rule "
                    "could not be checked. Read the delta off the option chain "
                    f"in thinkorswim - your SOP wants it at or under "
                    f"{max_delta:.2f}."
                ),
                expected=f"<= {max_delta:.2f}",
                actual="not available",
            ))
            continue
        ok = leg.abs_delta <= max_delta + 1e-9
        shown = _tell_apart(leg.abs_delta, max_delta) if not ok else f"{leg.abs_delta:.3f}"
        results.append(
            CheckResult(
                name=name,
                status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                message=(
                    f"Short {leg.option_type.value} at strike {leg.strike:g} has delta "
                    f"{shown} - "
                    + ("within your limit." if ok else f"OVER your {max_delta:.2f} limit. "
                       "This leg is too close to the money - move further out.")
                ),
                expected=f"<= {max_delta:.2f}",
                actual=shown,
            )
        )
    return results


def check_short_call_target_delta(trade: Trade, target: float) -> Optional[CheckResult]:
    """Covered calls / PMCC: sell the short call near a target delta (e.g. 0.30)."""
    short_calls = [
        leg for leg in trade.legs
        if leg.action == Action.SELL and leg.option_type == OptionType.CALL
    ]
    if not short_calls:
        return None
    leg = short_calls[0]
    if delta_missing(leg):
        return CheckResult(
            name=f"Short call near delta {target:.2f}",
            status=CheckStatus.WARN,
            message=(f"No delta came through for the {leg.strike:g} call you are "
                     f"selling, so this could not be checked. Your SOP aims for "
                     f"about {target:.2f} - read it off the chain in thinkorswim."),
            expected=f"~{target:.2f}", actual="not available")
    low, high = target - DELTA_TOLERANCE, target + DELTA_TOLERANCE
    ok = low <= leg.abs_delta <= high
    return CheckResult(
        name=f"Short call near delta {target:.2f}",
        status=CheckStatus.PASS if ok else CheckStatus.WARN,
        message=(
            f"Short call delta is {leg.abs_delta:.3f}. Target is {target:.2f} "
            f"(fine between {low:.2f} and {high:.2f})."
            + ("" if ok else " Consider a strike closer to your 0.30 target.")
        ),
        expected=f"~{target:.2f}",
        actual=f"{leg.abs_delta:.3f}",
    )


def check_bought_call_delta(trade: Trade, min_delta: float) -> Optional[CheckResult]:
    """LEAPS long call: the bought call must be at least this deep.

    A FLOOR, not a band. Deeper is never the mistake here - it is more like the
    stock, which is the whole point. The mistake is going shallower: an
    out-of-the-money call is nearly all time premium, so it swings violently,
    gets crushed when implied volatility falls, and expires worthless if the
    stock merely drifts. At 0.70 you capture about 70 cents of every dollar the
    stock moves, on a much smoother ride.
    """
    long_calls = [leg for leg in trade.legs
                  if leg.action == Action.BUY and leg.option_type == OptionType.CALL]
    if not long_calls:
        return None
    leg = max(long_calls, key=lambda l: l.abs_delta)
    name = f"Bought call delta at least {min_delta:.2f}"
    if delta_missing(leg):
        return CheckResult(
            name=name, status=CheckStatus.WARN,
            message=(f"No delta came through for the {leg.strike:g} call, so this could "
                     f"not be checked. Your SOP wants {min_delta:.2f} or deeper."),
            expected=f">= {min_delta:.2f}", actual="not available")

    d = leg.abs_delta
    ok = d >= min_delta - 1e-9
    message = (
        f"Delta is {d:.3f}, at or below your {min_delta:.2f} floor. Most of what you "
        "would pay here is time premium, which swings hard and goes to zero if the "
        "stock only drifts. Choose a lower strike, deeper in the money."
        if not ok else
        f"Delta is {d:.3f}. Most of the price is real (intrinsic) value, so it tracks "
        "the stock closely and time decay stays slow.")
    return CheckResult(
        name=name, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=message, expected=f">= {min_delta:.2f}", actual=f"{d:.3f}")


def check_open_interest(trade: Trade, minimum: int) -> Optional[CheckResult]:
    """Enough people trade this contract that you can get back OUT of it.

    Liquidity matters more on a LEAPS than anywhere else in this book. You are
    holding one contract for months and the exit is a single sale - if only a
    handful trade, the gap between bid and ask eats the move you were right
    about. Worth paying up for a further expiration to get it.
    """
    quoted = [leg for leg in trade.legs if leg.open_interest is not None]
    if not quoted:
        return None
    leg = min(quoted, key=lambda l: l.open_interest or 0)
    oi = leg.open_interest or 0
    ok = oi >= minimum
    return CheckResult(
        name=f"Open interest at least {minimum}",
        status=CheckStatus.PASS if ok else CheckStatus.WARN,
        message=(
            f"The {leg.strike:g} contract has {oi:,} open contracts."
            + ("" if ok else f" Your SOP wants {minimum}+. Thin contracts have a wide "
                             "bid-ask gap, so getting out costs more than it should - "
                             "try a further expiration, which often trades better.")),
        expected=f">= {minimum}", actual=f"{oi:,}")


def check_debit_size(capital: float, account_size: float, max_pct: float,
                     open_leaps_capital: float = 0.0,
                     target_positions: int = 3) -> Optional[CheckResult]:
    """Bought premium only: is this bet small enough to survive losing all of it?

    This strategy has NO stop loss on purpose, so size at entry is the entire
    risk control - there is no second line of defence further down. The cap is
    on ALL open LEAPS together, not on one trade, because three separate 8%
    positions is a 24% bet on one idea wearing three tickers.

    open_leaps_capital: premium already committed to other open LEAPS.
    """
    if capital <= 0 or account_size <= 0:
        return None
    total = capital + max(open_leaps_capital, 0.0)
    pct = total / account_size * 100
    ok = pct <= max_pct + 1e-9
    share = max_pct / max(target_positions, 1)
    held = (f" You already hold ${open_leaps_capital:,.0f} of LEAPS, so this would take "
            f"the total to ${total:,.0f}." if open_leaps_capital > 0 else "")
    return CheckResult(
        name=f"All LEAPS together under {max_pct:g}% of the account",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"You would pay ${capital:,.0f}, which puts {pct:.1f}% of your "
            f"${account_size:,.0f} account into bought calls.{held} Every cent of that "
            "can be lost, and this strategy has no stop to catch it - the size IS the "
            "risk control."
            + ("" if ok else f" Your SOP caps all LEAPS at {max_pct:g}% together, spread "
                             f"across about {target_positions} names - roughly "
                             f"{share:.1f}% each. Buy fewer contracts, or pick a cheaper "
                             "stock.")),
        expected=f"<= {max_pct:g}%", actual=f"{pct:.1f}%")


def check_long_leaps_delta(trade: Trade, min_delta: float) -> Optional[CheckResult]:
    """PMCC: the long LEAPS call should be deep in the money (delta >= ~0.80)."""
    long_calls = [
        leg for leg in trade.legs
        if leg.action == Action.BUY and leg.option_type == OptionType.CALL
    ]
    if not long_calls:
        return None
    leg = max(long_calls, key=lambda l: l.abs_delta)
    if delta_missing(leg):
        return CheckResult(
            name=f"Long LEAPS delta at least {min_delta:.2f}",
            status=CheckStatus.WARN,
            message=(f"No delta came through for the {leg.strike:g} LEAPS call, so "
                     f"this could not be checked. Your SOP wants it deep in the "
                     f"money, at {min_delta:.2f} or higher."),
            expected=f">= {min_delta:.2f}", actual="not available")
    ok = leg.abs_delta >= min_delta - 1e-9
    return CheckResult(
        name=f"Long LEAPS delta at least {min_delta:.2f}",
        status=CheckStatus.PASS if ok else CheckStatus.WARN,
        message=(
            f"Long LEAPS delta is {leg.abs_delta:.3f}. Deep-in-the-money (>= {min_delta:.2f}) "
            "makes it behave like the stock."
            + ("" if ok else " A higher-delta LEAPS tracks the stock more closely.")
        ),
        expected=f">= {min_delta:.2f}",
        actual=f"{leg.abs_delta:.3f}",
    )


def check_dte_range(trade: Trade, dte_min: int, dte_max: int) -> CheckResult:
    dte = trade.dte
    if dte is None:
        return CheckResult(
            name="Days to expiration in range",
            status=CheckStatus.INFO,
            message="No expiration set on the trade yet.",
        )
    ok = dte_min <= dte <= dte_max
    return CheckResult(
        name=f"Days to expiration {dte_min}-{dte_max}",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"Trade has {dte} days to expiration - "
            + ("inside your window." if ok else f"outside your {dte_min}-{dte_max} day window.")
        ),
        expected=f"{dte_min}-{dte_max} days",
        actual=f"{dte} days",
    )


def check_dte_target(trade: Trade, target: int) -> CheckResult:
    dte = trade.dte
    if dte is None:
        return CheckResult(
            name="Days to expiration near target",
            status=CheckStatus.INFO,
            message="No expiration set on the trade yet.",
        )
    ok = abs(dte - target) <= DTE_TOLERANCE
    return CheckResult(
        name=f"Days to expiration near {target}",
        status=CheckStatus.PASS if ok else CheckStatus.WARN,
        message=(
            f"Trade has {dte} days to expiration - target is about {target} "
            f"(give or take {DTE_TOLERANCE})."
            + ("" if ok else " A bit off your usual timing.")
        ),
        expected=f"~{target} days",
        actual=f"{dte} days",
    )


def check_time_exit_runway(trade: Trade, time_exit_dte: int,
                           dte_target: Optional[int] = None) -> Optional[CheckResult]:
    """How many days you actually get between entering and your time exit.

    The DTE range check can pass on a trade that is already finished: enter at
    23 days with a 21-day time exit and the plan says close it in two days. The
    range rule says nothing about that, so this check says it out loud.
    """
    dte = trade.dte
    if dte is None:
        return None
    runway = dte - time_exit_dte
    name = f"Room before your {time_exit_dte}-day time exit"
    target_hint = (f" Your SOP prefers about {dte_target} days at entry - "
                   f"a later expiration gives the trade room to work." if dte_target else "")
    # Never a FAIL: her SOP explicitly allows entering a European index at 21 DTE,
    # and a rule the app invented must not overrule one she bought and wrote down.
    # It also must not silently vanish setups from the scan - the scanner drops
    # anything that FAILS, so she would never learn why they disappeared.
    if runway <= 0:
        return CheckResult(
            name=name,
            status=CheckStatus.WARN,
            message=(f"This trade has {dte} days to expiration, which is already at or past "
                     f"your {time_exit_dte}-day time exit - by your own rule you would be "
                     f"closing it the day you open it.{target_hint}"),
            expected=f"more than {time_exit_dte} days to expiration",
            actual=f"{dte} days",
        )
    days = f"{runway} day" + ("" if runway == 1 else "s")
    if runway < MIN_TIME_EXIT_RUNWAY:
        # Scale the wording to the actual squeeze: 2 days really is "closing it
        # as you open it", 9 days is merely tight, and calling both the same
        # thing is how a warning stops meaning anything.
        squeeze = ("You would be closing this trade almost as soon as you open it, with "
                   "little chance for the 50% profit target to be reached."
                   if runway <= VERY_SHORT_RUNWAY else
                   "That is a short runway for the 50% profit target to be reached before "
                   "the time exit forces you out.")
        return CheckResult(
            name=name,
            status=CheckStatus.WARN,
            message=f"Only {days} between entering and your {time_exit_dte}-day time exit. "
                    f"{squeeze}{target_hint}",
            expected=f"at least {MIN_TIME_EXIT_RUNWAY} days of room",
            actual=days,
        )
    return CheckResult(
        name=name,
        status=CheckStatus.PASS,
        message=(f"{days} between entering and your {time_exit_dte}-day time exit - "
                 f"room for the trade to work."),
        expected=f"at least {MIN_TIME_EXIT_RUNWAY} days of room",
        actual=days,
    )


def check_is_credit(trade: Trade) -> CheckResult:
    ok = trade.is_credit
    return CheckResult(
        name="Trade brings in a credit",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"You collect ${trade.net_credit_total:,.0f} up front."
            if ok
            else f"This trade is a DEBIT of ${abs(trade.net_credit_total):,.0f} - a credit "
            "strategy should pay you, not cost you. Check your strikes and prices."
        ),
        expected="net credit (money in)",
        actual=(
            f"+${trade.net_credit_total:,.0f}" if ok
            else f"-${abs(trade.net_credit_total):,.0f}"
        ),
    )


def check_min_credit_pct_of_width(
    trade: Trade, min_pct: float
) -> Optional[CheckResult]:
    """The credit has to be worth the width you are risking.

    Her SOP floor is 6% of the spread width. It used to be a flat $3.00, which
    only ever worked at SPX size: on a $5-wide stock spread $3.00 would be 60%
    of the width, which does not exist at these deltas, so the flat rule
    silently blocked every single-stock spread. A percentage scales to any
    underlying and any width.

    Returns None when there is no width to measure against (a cash secured put),
    so the caller can append whatever comes back without checking first.
    """
    width = trade.spread_width
    if width is None or width <= 0:
        return None
    credit = trade.net_credit_per_share
    needed = round(width * min_pct, 2)
    want_pct = min_pct * 100
    got_pct = credit / width * 100
    ok = credit >= needed - 1e-9
    return CheckResult(
        name=f"{MIN_CREDIT_CHECK_PREFIX} {want_pct:.0f}% of spread width",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"You collect ${credit:.2f} per share on a ${width:g} wide spread, "
            f"which is {got_pct:.1f}% of the width. Your floor is {want_pct:.0f}% "
            f"(${needed:.2f} here)."
            if ok else
            f"You only collect ${credit:.2f} per share on a ${width:g} wide spread, "
            f"which is {got_pct:.1f}% of the width. Your SOP floor is {want_pct:.0f}%, "
            f"so you need at least ${needed:.2f}. That is too little credit for the "
            f"risk - widen the spread, move the short strike closer to the money, or "
            f"wait for richer premium."
        ),
        expected=f"at least ${needed:.2f} per share ({want_pct:.0f}% of ${width:g})",
        actual=f"${credit:.2f} per share ({got_pct:.1f}%)",
    )


def check_monthly_bp(
    trade_bp: float, existing_month_bp: float, limit: float
) -> CheckResult:
    projected = trade_bp + existing_month_bp
    ok = projected <= limit
    # "Already committed", not "already tied up": her limit counts every trade
    # opened this month, closed ones included, so the number does not fall back
    # when she takes a win.
    return CheckResult(
        name=f"Monthly buying power under ${limit:,.0f}",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"This trade ties up ${trade_bp:,.0f}. With ${existing_month_bp:,.0f} already "
            f"committed this month, you'd be at ${projected:,.0f} of your ${limit:,.0f} "
            f"budget"
            + (f", leaving ${limit - projected:,.0f} for the rest of the month." if ok
               else f". That is ${projected - limit:,.0f} OVER your monthly budget - size "
                    f"down, or wait for next month.")
        ),
        expected=f"<= ${limit:,.0f}",
        actual=f"${projected:,.0f}",
    )


def check_position_delta(trade: Trade, red_flag: float) -> CheckResult:
    net = abs(trade.net_position_delta)
    ok = net <= red_flag
    return CheckResult(
        name=f"Position delta under red-flag ({red_flag:g})",
        status=CheckStatus.PASS if ok else CheckStatus.WARN,
        message=(
            f"Net position delta is {trade.net_position_delta:,.0f} share-equivalents. "
            + ("Within your comfort zone." if ok else
               f"Its size ({net:,.0f}) is past your {red_flag:g} red flag - the position "
               "leans strongly one way. Watch it closely or reduce size.")
        ),
        expected=f"|delta| <= {red_flag:g}",
        actual=f"{trade.net_position_delta:,.0f}",
    )


def profit_target_keep(credit: float, profit_target_pct: float) -> float:
    """What she keeps when the profit target is hit, in cents.

    The checklist and the "set these alerts in TOS" card each worked this out on
    their own - credit * pct/100 in one, credit - credit*(1 - pct/100) in the
    other. Algebraically the same, but in floating point they land either side
    of a half cent, and Python rounds .5 to the nearest EVEN dollar, so one
    could print $207 while the other printed $208 for the same trade. She types
    these into a TOS alert, so they have to be one number, computed once.
    """
    return round(round(credit, 2) * float(profit_target_pct) / 100, 2)


def exit_plan_info(trade: Trade, exit_rules: dict[str, Any]) -> list[CheckResult]:
    """Not pass/fail - just reminders of your exits, with the dollar levels filled in."""
    out: list[CheckResult] = []
    credit = trade.net_credit_total
    pt = exit_rules.get("profit_target_pct")
    if pt and credit > 0:
        keep = profit_target_keep(credit, pt)
        out.append(CheckResult(
            name=f"Profit target {pt:g}%",
            status=CheckStatus.INFO,
            message=f"Plan to close when you can keep about ${keep:,.0f} "
                    f"({pt:g}% of the ${credit:,.0f} credit).",
        ))
    sl = exit_rules.get("stop_loss_multiple")
    if sl and credit > 0:
        loss_at = credit * sl
        out.append(CheckResult(
            name=f"Stop loss {sl:g}x credit",
            status=CheckStatus.INFO,
            message=f"Plan to close if the loss reaches about ${loss_at:,.0f} "
                    f"({sl:g}x the credit received).",
        ))
    te = exit_rules.get("time_exit_dte")
    if te:
        out.append(CheckResult(
            name=f"Time exit at {te} DTE",
            status=CheckStatus.INFO,
            message=f"Close no matter what once {te} days to expiration are left.",
        ))
    return out
