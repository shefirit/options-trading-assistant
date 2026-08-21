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
                       dte: int, leaps_dte: Optional[int] = None,
                       financing_puts: int = 0) -> list[Leg]:
    """One Leg per strategy leg definition - just strikes and dates, no Greeks.

    strikes maps role -> strike, e.g. {"short_put": 5000, "long_put": 4975}.
    leaps_dte, when given, applies to every leg except the short call: in a
    PMCC that is the LEAPS call, in the covered-call models it is the
    protective put (and its offsetting short puts), all far-dated while the
    short call is the near monthly. Credit spreads and CSPs never pass it.

    financing_puts adds the put(s) SOLD alongside a LEAPS long call to part-pay
    for it - the risk reversal variant in config/strategies.yaml. It is not in
    that strategy's `legs` block on purpose (it is a variant, not the strategy),
    so it cannot come from the loop above; the count comes from her fill and the
    strike from strikes["financing_put"]. Same expiration as the call, which is
    what her SOP requires and what the checklist verifies.
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
    if financing_puts > 0:
        legs.append(Leg(
            role="financing_put",
            action=Action.SELL,
            option_type=OptionType.PUT,
            strike=float(strikes.get("financing_put", 0.0)),
            quantity=int(financing_puts),
            dte=int(dte),
        ))
    return legs


def apply_fill_prices(legs: list[Leg], prices: dict[str, float]) -> list[Leg]:
    """Her own fill prices, per role, overwriting whatever the chain guessed.

    fill_from_chain fills every leg from today's mid, which is the best the app
    can do on a credit spread where she only types ONE net price for the whole
    order. On a bought call she types each side separately - what the call cost
    and what the put paid - and those are exact fills rather than estimates, so
    they must win: the SOP checks that read leg premiums (what the position
    commits her to, how much of the call the put actually funded) would
    otherwise report today's market back at her instead of her own trade.
    """
    for leg in legs:
        price = prices.get(leg.role)
        if price:
            leg.premium = round(abs(float(price)), 4)
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


def _value_at_zero(trade: Trade) -> float:
    """What the OPTIONS would be worth if the underlying went to zero: every
    put finishes worth its strike, every call worthless. The shares go to zero
    too, but their cost is already inside open_cash."""
    total = 0.0
    for leg in trade.legs:
        if leg.option_type != OptionType.PUT:
            continue
        sign = 1.0 if leg.action == Action.BUY else -1.0
        total += sign * leg.strike * 100 * leg.quantity * trade.contracts
    return total


def resize_after_edit(strat: dict[str, Any], trade: Trade, credit_total: float,
                      old_credit: float, old_open_cash: float,
                      old_shares_cost: float, old_contracts: int) -> dict[str, float]:
    """Fresh sizing after she corrects a trade's details.

    Correcting the contracts or a strike changes what the trade can lose, and
    leaving the old figures in place would quietly misreport her risk and her
    monthly buying power - the two numbers the whole dashboard is built on.

    The awkward part is that sizing_from_fill wants the ORIGINAL fill inputs
    (what the LEAPS cost, what the shares cost) and the log does not store them
    directly. It does store the ledger they produced, so they come back out of
    it by rearranging the same equations sizing_from_fill used going in:

        debit  : open_cash = credit - cost           -> cost = credit - open_cash
        shares : open_cash = credit - shares - prot  -> prot = credit - shares - open_cash

    Per-share values are recovered against the OLD contract count and then
    re-multiplied by the new one, which is what makes changing the contracts
    scale the position rather than merely relabel it.
    """
    basis = str(strat.get("sizing", {}).get("max_loss_basis", "vertical_width"))
    old_contracts = max(int(old_contracts), 1)

    leaps_cost = share_price = protection = None
    if basis == "long_premium":
        # A bought call logs no credit, so the equation above has nothing to
        # rearrange: open_cash = put credit - what the call cost, and both of
        # those are unknowns. The legs carry her fill prices, so the put side
        # comes off them and the call's cost is what is left of the ledger.
        # credit_total is the put credit here, not a credit collected.
        put_credit = sum(l.premium * l.quantity for l in trade.legs
                         if l.action == Action.SELL
                         and l.option_type == OptionType.PUT) * 100
        credit_total = round(put_credit * max(int(trade.contracts), 1), 2)
        per_contract = (put_credit * old_contracts - float(old_open_cash)) / old_contracts
        leaps_cost = per_contract * max(int(trade.contracts), 1)
    elif basis == "debit":
        # Per contract, so a corrected contract count scales the cost with it.
        per_contract = (float(old_credit) - float(old_open_cash)) / old_contracts
        leaps_cost = per_contract * max(int(trade.contracts), 1)
    elif basis in ("shares_plus_protection", "ratio_risk"):
        share_price = float(old_shares_cost) / (100.0 * old_contracts) or None
        per_contract = (float(old_credit) - float(old_shares_cost)
                        - float(old_open_cash)) / old_contracts
        protection = per_contract * max(int(trade.contracts), 1)

    return sizing_from_fill(trade, strat, credit_total,
                            leaps_cost_total=leaps_cost,
                            share_price=share_price,
                            protection_cost_total=protection)


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
    elif basis == "long_premium":
        # A LEAPS bought outright. There is no credit to collect: `credit_total`
        # here is whatever the optional financing put(s) paid, and that is money
        # off the price of the call, never income. Recorded as zero credit for
        # the same reason sizing.estimate does - a 50%-of-credit profit target
        # measured against it would be meaningless, and the month report would
        # count it as premium sold.
        cost = float(leaps_cost_total or 0.0)
        put_credit = float(credit_total or 0.0)
        credit = 0.0
        open_cash = put_credit - cost
        collateral = sum(l.strike * l.quantity for l in trade.legs
                         if l.action == Action.SELL
                         and l.option_type == OptionType.PUT) * 100 * contracts
        if collateral > 0:
            # With a put sold, what she COMMITS is the net debit plus the cash
            # standing behind the put - and that is also the worst case, since a
            # collapse to zero costs her the call and every cent of the shares
            # she promised to buy. The broker reserves the strike less the
            # premium it already handed her, exactly like her cash secured put.
            max_loss = max(cost - put_credit + collateral, 0.0)
            buying_power = max(collateral - put_credit, 0.0)
        else:
            # The plain one-leg version: what she paid is what she can lose, and
            # a bought option uses cash rather than buying power.
            max_loss = cost
            buying_power = 0.0
    elif basis in ("shares_plus_protection", "ratio_risk"):
        # Covered calls: 100 real shares per contract, plus whatever the put
        # side cost (Model 1's long put, Model 2's put spread; Model 3's ratio
        # is built to cost ~nothing and can even come in at a credit).
        px = float(share_price or trade.underlying_price or 0.0)
        shares_cost = px * 100 * contracts
        protection = float(protection_cost_total or 0.0)
        open_cash = credit - shares_cost - protection
        buying_power = max(shares_cost + protection - credit, 0.0)
        # The worst case is NOT the cash she laid out. The protective put means
        # a collar can never lose most of it, and Model 3's two short puts mean
        # the ratio can lose considerably MORE than it. Price the legs at zero
        # (puts finish worth their strike, calls worthless, shares worthless)
        # and read the real number off the payoff.
        max_loss = max(-(open_cash + _value_at_zero(trade)), 0.0)
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
