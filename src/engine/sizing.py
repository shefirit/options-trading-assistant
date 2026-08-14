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


def _short_put_credit(trade: Trade) -> float:
    """Premium collected from the put(s) you SELL.

    On a LEAPS long call this is the financing put - money that part-pays for
    the call. It is NOT income: nothing here should ever add it to a profit
    figure, because you are still net out of pocket on the trade.
    """
    total = sum(l.premium * l.quantity for l in trade.legs
                if l.action == Action.SELL and l.option_type == OptionType.PUT)
    return total * 100 * trade.contracts


def _short_put_collateral(trade: Trade) -> float:
    """Cash that must sit behind a sold put: strike x 100 per contract.

    Deliberately NOT netted against the credit here. Those are two different
    questions - what the trade cost you, and what you must have in the account -
    and netting them once at the wrong moment is how the credit gets counted
    twice, making the position look thousands of dollars cheaper than it is.
    """
    total = sum(l.strike * l.quantity for l in trade.legs
                if l.action == Action.SELL and l.option_type == OptionType.PUT)
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
    # Cash actually paid for bought premium, kept apart from `capital` because
    # the two stop being the same number the moment a financing put is sold.
    # Her SOP's 10%-of-account cap is on what she PAID, not on the collateral.
    debit: float | None = None

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
    elif basis == "long_premium":
        # A bought call on its own. There is no credit to collect and nothing to
        # net against: what you paid is exactly what you can lose, and every
        # cent of it really can go. This is the only strategy here where
        # max_loss equals capital equals 100% of the position - which is why the
        # dashboards must never call this number "worst case" and move on.
        credit = 0.0
        call_cost = _long_call_cost(trade) or debit_risk(trade)
        put_credit = _short_put_credit(trade)
        if put_credit > 0:
            # ...and the optional financing put changes all three of those.
            # Worked through on her numbers: a $38 call is $3,800 out, the $150
            # put pays $1,200 back, so the DEBIT is $2,600 - but $15,000 has to
            # sit behind that put regardless. Cash committed is $17,600, and if
            # the stock went to zero she would lose every cent of it (call
            # worthless, assigned 100 shares at $150 now worth nothing, keeping
            # the $1,200). Capital and max loss coincide here, which is the
            # honest headline: with a cash-secured put, what you commit IS what
            # you can lose. The one-leg version risks $3,800.
            collateral = _short_put_collateral(trade)
            debit = call_cost - put_credit
            capital = max_loss = debit + collateral
            # The broker reserves the strike less the premium it already handed
            # you - the same arithmetic as her cash secured put, so the monthly
            # buying-power check sees this variant the way TOS will.
            buying_power = max(collateral - put_credit, 0.0)
        else:
            debit = capital = max_loss = call_cost
            buying_power = 0.0                      # cash, not margin
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
        "debit": round(debit if debit is not None else capital, 2),
    }
