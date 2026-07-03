"""Money math for a trade: credit, worst-case loss, and buying power used.

Kept separate so the numbers are easy to read and to test. All dollar amounts
are for the whole position (already multiplied by 100 and by contract count).
"""

from __future__ import annotations

from typing import Any

from src.engine.models import Action, OptionType, Trade


def vertical_max_loss(trade: Trade) -> float:
    """Defined-risk vertical / iron condor: risk = (width - credit) x 100 x contracts.

    For an iron condor we use the wider single side, because price can only
    breach one side at expiration.
    """
    put_width = trade.vertical_width(OptionType.PUT) or 0.0
    call_width = trade.vertical_width(OptionType.CALL) or 0.0
    width = max(put_width, call_width)
    credit_per_share = trade.net_credit_per_share
    per_contract = (width - credit_per_share) * 100
    return max(per_contract, 0.0) * trade.contracts


def cash_secured_put_risk(trade: Trade) -> float:
    """Cash you must set aside: (strike x 100 - credit) per contract."""
    shorts = trade.short_legs
    if not shorts:
        return 0.0
    strike = shorts[0].strike
    per_contract = strike * 100 - trade.net_credit_per_share * 100
    return max(per_contract, 0.0) * trade.contracts


def debit_risk(trade: Trade) -> float:
    """Diagonals / PMCC: most you can lose is the net debit paid."""
    net = trade.net_credit_per_share  # negative for a debit
    return max(-net, 0.0) * 100 * trade.contracts


def _short_call_income(trade: Trade) -> float:
    """Premium collected from the call(s) you SELL - the income of a covered call / PMCC."""
    total = sum(l.premium * l.quantity for l in trade.legs
                if l.action == Action.SELL and l.option_type == OptionType.CALL)
    return total * 100 * trade.contracts


def _long_call_cost(trade: Trade) -> float:
    """Cost of the long call(s) you BUY - the LEAPS in a PMCC."""
    total = sum(l.premium * l.quantity for l in trade.legs
                if l.action == Action.BUY and l.option_type == OptionType.CALL)
    return total * 100 * trade.contracts


def shares_capital(trade: Trade) -> float:
    """Cost of the 100 shares per contract a covered call is written against."""
    price = trade.underlying_price or 0.0
    return price * 100 * trade.contracts


def estimate(trade: Trade, strategy: dict[str, Any]) -> dict[str, float]:
    """Return credit (income), max_loss, buying_power, and return_on_risk.

    Buying power is the capital tied up: the max loss for defined-risk spreads,
    the reserved cash for a cash-secured put, the shares for a covered call, or
    the LEAPS cost for a PMCC. return_on_risk is income / capital.
    """
    basis = strategy.get("sizing", {}).get("max_loss_basis", "vertical_width")
    credit = trade.net_credit_total

    if basis == "vertical_width":
        max_loss = vertical_max_loss(trade)
        buying_power = max_loss
    elif basis == "cash_secured":
        max_loss = cash_secured_put_risk(trade)
        buying_power = max_loss
    elif basis in ("shares_plus_protection", "ratio_risk"):
        # Covered call: income is the short call; capital is the 100 shares.
        credit = _short_call_income(trade)
        buying_power = shares_capital(trade)
        max_loss = buying_power                    # worst case, shares fall to zero
    elif basis == "debit":
        # PMCC: income is the short call; capital is the LEAPS you bought.
        credit = _short_call_income(trade)
        leaps = _long_call_cost(trade)
        buying_power = leaps if leaps > 0 else debit_risk(trade)
        max_loss = max(buying_power - credit, 0.0)  # LEAPS can expire worthless
    else:
        max_loss = vertical_max_loss(trade)
        buying_power = max_loss

    return_on_risk = (credit / buying_power) if buying_power > 0 else 0.0

    return {
        "credit": round(credit, 2),
        "max_loss": round(max_loss, 2),
        "buying_power": round(buying_power, 2),
        "return_on_risk": round(return_on_risk, 4),
    }
