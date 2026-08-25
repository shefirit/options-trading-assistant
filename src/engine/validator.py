"""Runs every rule that applies to a trade's strategy and returns the full
SOP checklist (a ValidationReport). This is the "make sure I do it correctly"
core - pure Python, no live data needed, so it is fully unit-tested.
"""

from __future__ import annotations

from typing import Optional

from src.data import market_calendar
from src.engine import rules, sizing
from src.engine.config_loader import (
    allowed_underlyings_for,
    get_strategy,
    is_european_style,
    load_settings,
    preferred_entry_dte,
    underlying_fits_style,
)
from src.engine.models import (
    Action,
    CheckResult,
    CheckStatus,
    OptionType,
    Trade,
    ValidationReport,
)


def validate_trade(
    trade: Trade,
    existing_month_bp: float = 0.0,
    open_leaps_capital: float = 0.0,
) -> ValidationReport:
    """Check a proposed trade against its strategy SOP.

    existing_month_bp: buying power you have already committed this month, so
    the monthly-limit check is realistic. Defaults to 0 if you are not tracking it.

    open_leaps_capital: premium already tied up in other open LEAPS long calls.
    That cap is on the TOTAL, so a per-trade check alone would wave through
    three positions that add up to a quarter of the account.
    """
    strategy = get_strategy(trade.strategy_key)
    settings = load_settings()
    entry = strategy.get("entry", {})
    exit_rules = strategy.get("exit", {})
    risk = settings["risk_limits"]

    results: list[CheckResult] = []

    # 1. Right underlying for the option style. Judged by style, not by whether
    # the name is in our universe files - those only cover the S&P 500 and
    # Nasdaq-100, and the SOP allows any liquid name.
    results.append(
        rules.check_underlying_style(
            trade, allowed_underlyings_for(trade.strategy_key),
            fits_style=underlying_fits_style(trade.strategy_key, trade.underlying))
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
    # A REFERENCE band, not a limit - the LEAPS put, whose page declines to fix a
    # delta. Reported and never failed; see check_delta_reference.
    if "delta_reference_min" in entry and "delta_reference_max" in entry:
        r = rules.check_delta_reference(trade, float(entry["delta_reference_min"]),
                                        float(entry["delta_reference_max"]))
        if r:
            results.append(r)
    if strategy.get("family") == "long_call" and "long_leg_delta_min" in entry:
        # Same floor as the PMCC's but a different message: here the risk being
        # guarded against is buying an out-of-the-money lottery ticket, not
        # failing to cover a short call.
        r = rules.check_bought_call_delta(trade, float(entry["long_leg_delta_min"]))
        if r:
            results.append(r)

        # 2b. The optional financing put, if one was actually sold. Every check
        # here returns None on a plain one-leg LEAPS, so her default trade's
        # checklist is untouched - this is a variant, not a change to the
        # strategy. The config block only supplies the numbers.
        fp = strategy.get("financing_put") or {}
        for check in (
            rules.check_financing_put_delta(
                trade, float(fp.get("delta_min", 0.20)), float(fp.get("delta_max", 0.30))),
            # Her hard line, checked off the strike rather than the delta so it
            # still holds when the feed sends no greeks.
            rules.check_financing_put_not_itm(trade),
            rules.check_financing_put_expiration(trade) if fp.get("same_expiration", True)
            else None,
            rules.check_financing_put_ratio(trade, int(fp.get("ratio", 1)),
                                            int(fp.get("max_ratio", fp.get("ratio", 1)))),
            rules.check_financing_put_commitment(
                trade, monthly_bp_limit=float(risk["monthly_bp_limit"]),
                existing_month_bp=existing_month_bp),
        ):
            if check:
                results.append(check)
    elif "long_leg_delta_min" in entry:
        r = rules.check_long_leaps_delta(trade, float(entry["long_leg_delta_min"]))
        if r:
            results.append(r)

    # 2c. What the bought call costs to get in and out of - reported, never
    # enforced (her ruling, 2026-08-14). It returns None unless the trade
    # actually buys a call, so only the LEAPS long call and the PMCC see it; on
    # a cheap option she sells, a percentage spread reads high and means nothing.
    if strategy.get("family") in ("long_call", "diagonal"):
        r = rules.check_bought_call_spread(
            trade, stale_reason=market_calendar.quotes_are_stale())
        if r:
            results.append(r)

    # 3. Timing (days to expiration).
    if "dte_min" in entry and "dte_max" in entry:
        dte_min, dte_max = int(entry["dte_min"]), int(entry["dte_max"])
        # US-style stocks/ETFs use their own window (avoid the ~21-DTE early-assignment
        # zone, but still reach the ~monthly expiration); indices may enter as early as 21.
        if not is_european_style(trade.underlying):
            dte_min = int(entry.get("dte_min_us_style", dte_min))
            dte_max = int(entry.get("dte_max_us_style", dte_max))
        results.append(rules.check_dte_range(trade, dte_min, dte_max))
    elif "short_call_dte_target" in entry:
        results.append(rules.check_dte_target(trade, int(entry["short_call_dte_target"])))
    elif "dte_target" in entry:
        results.append(rules.check_dte_target(trade, int(entry["dte_target"])))

    # 3b. The range check above passes on a trade that is already out of time -
    # the entry window and the time exit overlap. Say so explicitly.
    #
    # Only for strategies you open and close as one trade (credit spreads, iron
    # condor, cash secured put - the ones with a real dte_min/dte_max window).
    # Covered calls and PMCC target a 21-day short call against a position you
    # keep and roll, so "you would close it immediately" would be nonsense there.
    time_exit = exit_rules.get("time_exit_dte")
    if time_exit and "dte_min" in entry and "dte_max" in entry:
        runway = rules.check_time_exit_runway(
            trade, int(time_exit),
            dte_target=preferred_entry_dte(strategy, trade.underlying))
        if runway:
            results.append(runway)

    # 4. Credit strategies must actually pay you.
    if strategy.get("family") in ("credit_spread", "single_leg"):
        results.append(rules.check_is_credit(trade))

    # 4b. ...and pay ENOUGH for the width being risked. A credit is not the same
    # as a credit worth taking. Gated on the config key so it only runs where the
    # SOP sets a floor (the three credit-spread strategies).
    if "min_credit_pct_of_width" in entry:
        thin = rules.check_min_credit_pct_of_width(
            trade, float(entry["min_credit_pct_of_width"]))
        if thin:
            results.append(thin)

    # 5. Money / risk sizing.
    size = sizing.estimate(trade, strategy)
    results.append(
        rules.check_monthly_bp(size["buying_power"], existing_month_bp, float(risk["monthly_bp_limit"]))
    )

    # 5b. Bought premium is capped by the SIZE of the bet, because it is the only
    # thing that still works when a long option gaps to worthless. The monthly
    # buying-power check above cannot catch this - a bought call uses cash, not
    # buying power, so it reports zero and always passes.
    sizing_cfg = strategy.get("sizing", {})
    if sizing_cfg.get("max_loss_basis") == "long_premium":
        # The NET debit, not `capital`. With a financing put sold those two stop
        # being the same number - capital picks up the put's collateral, and
        # measuring a 10%-of-account cap against collateral would fail every
        # variant trade for the wrong reason. Her SOP caps premium PAID; the
        # collateral is caught by the monthly buying-power check above.
        sold_put = any(l.action == Action.SELL and l.option_type == OptionType.PUT
                       for l in trade.legs)
        debit = rules.check_debit_size(
            size.get("debit", size["capital"]),
            float(settings["account"]["starting_capital"]),
            float(sizing_cfg.get("max_pct_of_account", 10)),
            open_leaps_capital=open_leaps_capital,
            target_positions=int(sizing_cfg.get("target_positions", 3)),
            has_financing_put=sold_put)
        if debit:
            results.append(debit)
        if "min_open_interest" in entry:
            oi = rules.check_open_interest(trade, int(entry["min_open_interest"]))
            if oi:
                results.append(oi)

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
