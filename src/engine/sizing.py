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


def estimate(trade: Trade, strategy: dict[str, Any],
             broker_bp: float | None = None) -> dict[str, float]:
    """Return credit (income), max_loss, capital, buying_power, return_on_risk.

    Two different numbers, which this used to run together as one:

    - **capital** is cash actually deployed. A PMCC's LEAPS and a covered call's
      shares are bought and paid for, so that money is gone from the account
      until you sell. This is what a return is earned on, so it drives
      return_on_risk.
    - **buying_power** is what the BROKER reserves - thinkorswim's "BP Effect"
      column. On a defined-risk spread it equals the max loss. On anything you
      BUY outright it is zero: you paid cash, nothing is being held against you.
      TOS shows exactly 0.00 for Rita's three PMCCs, and the app used to report
      the full LEAPS cost, which is why its monthly figure read $44,892 against
      her broker's $14,830.

    broker_bp: the real BP Effect read off thinkorswim. Rita's ruling
    (2026-07-25) is that TOS is always right, so when this is supplied it wins
    over anything computed here. The app cannot see her broker's margin rules -
    house requirements differ from the Reg-T textbook - so the honest fallbacks
    below are only used until she types the real number.
    """
    basis = strategy.get("sizing", {}).get("max_loss_basis", "vertical_width")
    credit = trade.net_credit_total

    if basis == "vertical_width":
        # Defined risk: the broker holds exactly the most you can lose.
        max_loss = vertical_max_loss(trade)
        capital = buying_power = max_loss
    elif basis == "cash_secured":
        # Cash-secured means the whole strike is set aside. On a margin account
        # the broker holds far less (TOS held $3,483 where this says $12,225),
        # so this is the conservative read until she supplies the real one.
        max_loss = cash_secured_put_risk(trade)
        capital = buying_power = max_loss
    elif basis in ("shares_plus_protection", "ratio_risk"):
        # Covered call: income is the short call; capital is the 100 shares.
        credit = _short_call_income(trade)
        capital = shares_capital(trade)
        max_loss = capital                          # worst case, shares fall to zero
        buying_power = capital
    elif basis == "debit":
        # PMCC: income is the short call, capital is the LEAPS you bought - and
        # buying a long option costs cash, not buying power.
        credit = _short_call_income(trade)
        leaps = _long_call_cost(trade)
        capital = leaps if leaps > 0 else debit_risk(trade)
        max_loss = max(capital - credit, 0.0)       # LEAPS can expire worthless
        buying_power = 0.0
    else:
        max_loss = vertical_max_loss(trade)
        capital = buying_power = max_loss

    if broker_bp is not None:
        buying_power = max(float(broker_bp), 0.0)

    # Earned on the money actually at work, never on a zero buying power.
    basis_for_return = capital if capital > 0 else buying_power
    return_on_risk = (credit / basis_for_return) if basis_for_return > 0 else 0.0

    return {
        "credit": round(credit, 2),
        "max_loss": round(max_loss, 2),
        "capital": round(capital, 2),
        "buying_power": round(buying_power, 2),
        "return_on_risk": round(return_on_risk, 4),
    }
