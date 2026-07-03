"""Runs every rule that applies to a trade's strategy and returns the full
SOP checklist (a ValidationReport). This is the "make sure I do it correctly"
core - pure Python, no live data needed, so it is fully unit-tested.
"""

from __future__ import annotations

from typing import Optional

from src.engine import rules, sizing
from src.engine.config_loader import (
    allowed_underlyings_for,
    get_strategy,
    load_settings,
)
from src.engine.models import (
    CheckResult,
    CheckStatus,
    Trade,
    ValidationReport,
)


def validate_trade(
    trade: Trade,
    existing_month_bp: float = 0.0,
) -> ValidationReport:
    """Check a proposed trade against its strategy SOP.

    existing_month_bp: buying power you have already committed this month, so
    the monthly-limit check is realistic. Defaults to 0 if you are not tracking it.
    """
    strategy = get_strategy(trade.strategy_key)
    settings = load_settings()
    entry = strategy.get("entry", {})
    exit_rules = strategy.get("exit", {})
    risk = settings["risk_limits"]

    results: list[CheckResult] = []

    # 1. Right underlying for the option style.
    results.append(
        rules.check_underlying_style(trade, allowed_underlyings_for(trade.strategy_key))
    )

    # 2. Delta rules on the option(s) you sell / buy.
    if "short_leg_delta_max" in entry:
        results.extend(
            rules.check_short_leg_delta_max(trade, float(entry["short_leg_delta_max"]))
        )
    if "short_call_delta" in entry:
        r = rules.check_short_call_target_delta(trade, float(entry["short_call_delta"]))
        if r:
            results.append(r)
    if "long_leg_delta_min" in entry:
        r = rules.check_long_leaps_delta(trade, float(entry["long_leg_delta_min"]))
        if r:
            results.append(r)

    # 3. Timing (days to expiration).
    if "dte_min" in entry and "dte_max" in entry:
        results.append(rules.check_dte_range(trade, int(entry["dte_min"]), int(entry["dte_max"])))
    elif "short_call_dte_target" in entry:
        results.append(rules.check_dte_target(trade, int(entry["short_call_dte_target"])))
    elif "dte_target" in entry:
        results.append(rules.check_dte_target(trade, int(entry["dte_target"])))

    # 4. Credit strategies must actually pay you.
    if strategy.get("family") in ("credit_spread", "single_leg"):
        results.append(rules.check_is_credit(trade))

    # 5. Money / risk sizing.
    size = sizing.estimate(trade, strategy)
    results.append(
        rules.check_monthly_bp(size["buying_power"], existing_month_bp, float(risk["monthly_bp_limit"]))
    )

    # 6. Position delta red flag.
    results.append(rules.check_position_delta(trade, float(risk["position_delta_red_flag"])))

    # 7. Share-ownership reminder for covered calls.
    if strategy.get("requires_shares"):
        results.append(CheckResult(
            name="Own 100 real shares per contract",
            status=CheckStatus.INFO,
            message=f"This is a covered call - you must actually own 100 shares of "
                    f"{trade.underlying} for each contract before selling the call.",
        ))

    # 8. Any strategy-level warning (e.g. Model 3 is advanced).
    if strategy.get("warning"):
        results.append(CheckResult(
            name="Strategy caution",
            status=CheckStatus.WARN,
            message=strategy["warning"],
        ))

    # 9. Exit-plan reminders (not pass/fail).
    results.extend(rules.exit_plan_info(trade, exit_rules))

    return ValidationReport(
        strategy_key=trade.strategy_key,
        strategy_name=strategy.get("name", trade.strategy_key),
        underlying=trade.underlying,
        results=results,
    )
