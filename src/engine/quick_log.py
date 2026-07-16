"""Turns a trade Rita ALREADY placed in thinkorswim into a tracked position
with as little typing as possible.

The Quick Log form asks only what she can read off her TOS fill: strategy,
strikes, expiration, contracts, and the credit she collected. Everything else
(per-leg delta and mid, the money math) is filled in here - from the live
chain when it carries the exact contracts, and honestly left blank when it
does not, because exit tracking only needs her credit, the strikes, and the
expiration. Pure module: no network, no Streamlit, fully unit-tested.
"""

from __future__ import annotations

from typing import Any, Optional

from src.engine.models import Action, Leg, OptionType, Trade


def legs_from_strategy(strat: dict[str, Any], strikes: dict[str, float],
                       dte: int, leaps_dte: Optional[int] = None) -> list[Leg]:
    """One Leg per strategy leg definition - just strikes and dates, no Greeks.

    strikes maps role -> strike, e.g. {"short_put": 5000, "long_put": 4975}.
    leaps_dte, when given, applies to every leg except the short call: in a
    PMCC that is the LEAPS call, in the covered-call models it is the
    protective put (and its offsetting short puts), all far-dated while the
    short call is the near monthly. Credit spreads and CSPs never pass it.
    """
    legs: list[Leg] = []
    for leg_def in strat.get("legs", []):
        role = str(leg_def["role"])
        far = leaps_dte is not None and role != "short_call"
        legs.append(Leg(
            role=role,
            action=Action(leg_def["action"]),
            option_type=OptionType(leg_def["option_type"]),
            strike=float(strikes.get(role, 0.0)),
            quantity=int(leg_def.get("quantity", 1)),
            dte=int(leaps_dte if far else dte),
        ))
    return legs


def fill_from_chain(legs: list[Leg], chain, expiration_iso: str,
                    leaps_expiration_iso: Optional[str] = None,
                    ) -> tuple[list[Leg], list[str]]:
    """Fill each leg's delta and premium from the live chain, where possible.

    Matches by option type + expiration string + exact strike (the same match
    the tracker's cost-to-close uses). Returns the legs plus plain-English
    notes for anything not found. A miss is cosmetic: exits are checked
    against her actual credit and a fresh chain, never these entry numbers.
    """
    notes: list[str] = []
    dtes = [leg.dte for leg in legs if leg.dte is not None]
    near_dte = min(dtes) if dtes else None

    for leg in legs:
        exp = expiration_iso
        if (leaps_expiration_iso and near_dte is not None
                and leg.dte is not None and leg.dte != near_dte):
            exp = leaps_expiration_iso
        contract = next(
            (c for c in chain.contracts
             if c.option_type == leg.option_type and c.expiration == exp
             and abs(c.strike - leg.strike) < 1e-6),
            None)
        if contract is None or contract.mid <= 0:
            notes.append(
                f"Could not read live numbers for the {leg.strike:g} "
                f"{leg.option_type.value} expiring {exp} - saved without its "
                "delta. Tracking still works from your credit and strikes.")
            continue
        leg.delta = contract.delta
        leg.premium = contract.mid
    return legs, notes


def sizing_from_fill(trade: Trade, strat: dict[str, Any], credit_total: float,
                     leaps_cost_total: Optional[float] = None,
                     share_price: Optional[float] = None,
                     protection_cost_total: Optional[float] = None,
                     ) -> dict[str, float]:
    """Money math from the numbers on her TOS fill, not from chain mids.

    Same shape as sizing.estimate (credit / max_loss / buying_power /
    return_on_risk), but the credit is exactly what she collected, so the
    tracker's 50% target and 2x stop measure against reality.

    Also returns the two ledger fields the tracker needs:

      open_cash    signed net cash the day she opened it. Positive on the credit
                   shapes (they pay her). NEGATIVE on the debit shapes, where the
                   LEAPS / shares / protective put cost more than the call
                   collected - the money the old model dropped on the floor.
      shares_cost  what the 100 real shares per contract cost, if any, so the
                   position can be valued later at today's share price.

    credit_total stays the SHORT CALL's premium on the debit shapes: it is the
    basis for the 50% profit target, not the size of the position.
    """
    basis = str(strat.get("sizing", {}).get("max_loss_basis", "vertical_width"))
    contracts = max(int(trade.contracts), 1)
    credit = float(credit_total)
    shares_cost = 0.0

    if basis == "cash_secured":
        shorts = [l for l in trade.legs if l.action == Action.SELL]
        strike = shorts[0].strike if shorts else 0.0
        max_loss = max(strike * 100 * contracts - credit, 0.0)
        buying_power = max_loss
        open_cash = credit
    elif basis == "debit":
        # PMCC: she paid for the LEAPS and collected the short call against it.
        # Net cash out is the real capital and the real worst case: if the stock
        # went to zero the LEAPS expires worthless and she still keeps the call
        # credit, so she can never lose more than she laid out.
        cost = float(leaps_cost_total or 0.0)
        open_cash = credit - cost
        buying_power = max(cost - credit, 0.0)
        max_loss = buying_power
    elif basis in ("shares_plus_protection", "ratio_risk"):
        # Covered calls: 100 real shares per contract, plus whatever the put
        # side cost (Model 1's long put, Model 2's put spread; Model 3's ratio
        # is built to cost ~nothing and can even come in at a credit).
        px = float(share_price or trade.underlying_price or 0.0)
        shares_cost = px * 100 * contracts
        protection = float(protection_cost_total or 0.0)
        open_cash = credit - shares_cost - protection
        buying_power = max(shares_cost + protection - credit, 0.0)
        max_loss = buying_power
    else:
        # vertical_width (spreads, iron condor): risk = the wider single side.
        put_w = trade.vertical_width(OptionType.PUT) or 0.0
        call_w = trade.vertical_width(OptionType.CALL) or 0.0
        width = max(put_w, call_w)
        max_loss = max(width * 100 * contracts - credit, 0.0)
        buying_power = max_loss
        open_cash = credit

    return_on_risk = (credit / buying_power) if buying_power > 0 else 0.0
    return {
        "credit": round(credit, 2),
        "max_loss": round(max_loss, 2),
        "buying_power": round(buying_power, 2),
        "return_on_risk": round(return_on_risk, 4),
        "open_cash": round(open_cash, 2),
        "shares_cost": round(shares_cost, 2),
    }
