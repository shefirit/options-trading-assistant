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


def check_underlying_style(trade: Trade, allowed: list[str]) -> CheckResult:
    ok = trade.underlying in allowed
    # The allowed list can be hundreds of stocks, so summarize instead of listing all.
    hint = (
        "Credit spreads and iron condors should use European-style, cash-settled index names "
        "(SPX, NDX, RUT, XSP) so there is no early-assignment risk."
        if len(allowed) < 20 else
        "Cash secured puts and covered calls need a US-style name you can own shares of - "
        "an ETF (SPY, QQQ, IWM, DIA) or an S&P 500 / Nasdaq-100 stock."
    )
    return CheckResult(
        name="Right underlying for this strategy",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"{trade.underlying} is allowed for this strategy."
            if ok else f"{trade.underlying} is not allowed here. {hint}"
        ),
        expected=("European-style index" if len(allowed) < 20 else "US-style stock or ETF"),
        actual=trade.underlying,
    )


def check_short_leg_delta_max(trade: Trade, max_delta: float) -> list[CheckResult]:
    """Every option you SELL must have delta at or under the limit (e.g. < 0.10)."""
    results: list[CheckResult] = []
    for leg in trade.short_legs:
        ok = leg.abs_delta <= max_delta + 1e-9
        results.append(
            CheckResult(
                name=f"Short {leg.option_type.value} delta under {max_delta:.2f}",
                status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                message=(
                    f"Short {leg.option_type.value} at strike {leg.strike:g} has delta "
                    f"{leg.abs_delta:.3f} - "
                    + ("within your limit." if ok else f"OVER your {max_delta:.2f} limit. "
                       "This leg is too close to the money - move further out.")
                ),
                expected=f"<= {max_delta:.2f}",
                actual=f"{leg.abs_delta:.3f}",
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


def check_long_leaps_delta(trade: Trade, min_delta: float) -> Optional[CheckResult]:
    """PMCC: the long LEAPS call should be deep in the money (delta >= ~0.80)."""
    long_calls = [
        leg for leg in trade.legs
        if leg.action == Action.BUY and leg.option_type == OptionType.CALL
    ]
    if not long_calls:
        return None
    leg = max(long_calls, key=lambda l: l.abs_delta)
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


def check_monthly_bp(
    trade_bp: float, existing_month_bp: float, limit: float
) -> CheckResult:
    projected = trade_bp + existing_month_bp
    ok = projected <= limit
    return CheckResult(
        name=f"Monthly buying power under ${limit:,.0f}",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        message=(
            f"This trade ties up ${trade_bp:,.0f}. With ${existing_month_bp:,.0f} already used "
            f"this month, you'd be at ${projected:,.0f} of your ${limit:,.0f} limit."
            + ("" if ok else " That is OVER your monthly limit - size down or skip.")
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


def exit_plan_info(trade: Trade, exit_rules: dict[str, Any]) -> list[CheckResult]:
    """Not pass/fail - just reminders of your exits, with the dollar levels filled in."""
    out: list[CheckResult] = []
    credit = trade.net_credit_total
    pt = exit_rules.get("profit_target_pct")
    if pt and credit > 0:
        keep = credit * pt / 100
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
