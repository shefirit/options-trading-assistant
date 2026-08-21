"""Quick Log: building a tracked position from what Rita reads off her TOS
fill - strikes, expiration, contracts, credit - with the chain filling in the
rest when it can."""

from __future__ import annotations

from src.data.chain import OptionChain, OptionContract
from src.engine.config_loader import load_strategies
from src.engine.models import Action, OptionType, Trade
from src.engine.quick_log import (
    apply_fill_prices,
    fill_from_chain,
    legs_from_strategy,
    sizing_from_fill,
)

STRATS = load_strategies()


def test_legs_for_put_credit_spread_match_the_yaml():
    legs = legs_from_strategy(STRATS["put_credit_spread"],
                              {"short_put": 5000, "long_put": 4975}, dte=45)
    assert [l.role for l in legs] == ["short_put", "long_put"]
    assert legs[0].action == Action.SELL and legs[1].action == Action.BUY
    assert all(l.option_type == OptionType.PUT for l in legs)
    assert legs[0].strike == 5000 and legs[1].strike == 4975
    assert all(l.dte == 45 for l in legs)
    assert all(l.delta == 0.0 and l.premium == 0.0 for l in legs)  # honest zeros


def test_legs_for_iron_condor_has_four():
    legs = legs_from_strategy(
        STRATS["iron_condor"],
        {"long_put": 4900, "short_put": 4950, "short_call": 5300,
         "long_call": 5350}, dte=30)
    assert len(legs) == 4
    by_role = {l.role: l for l in legs}
    assert by_role["short_put"].action == Action.SELL
    assert by_role["long_call"].strike == 5350


def test_legs_for_pmcc_split_near_and_far():
    legs = legs_from_strategy(
        STRATS["poor_mans_covered_call"],
        {"long_call_leaps": 150, "short_call": 220}, dte=30, leaps_dte=400)
    by_role = {l.role: l for l in legs}
    assert by_role["long_call_leaps"].dte == 400   # far-dated stock substitute
    assert by_role["short_call"].dte == 30         # the near monthly income leg


def test_ratio_leg_quantity_comes_from_yaml():
    legs = legs_from_strategy(
        STRATS["covered_call_model_3"],
        {"long_put_protection": 95, "short_put_ratio": 90, "short_call": 110},
        dte=21, leaps_dte=700)
    by_role = {l.role: l for l in legs}
    assert by_role["short_put_ratio"].quantity == 2
    assert by_role["short_put_ratio"].dte == 700   # protection block is far-dated
    assert by_role["short_call"].dte == 21


def _chain(exp: str) -> OptionChain:
    return OptionChain(underlying="SPX", underlying_price=5100.0, contracts=[
        OptionContract(option_type=OptionType.PUT, strike=5000, expiration=exp,
                       dte=45, delta=-0.22, bid=7.8, ask=8.2),
        OptionContract(option_type=OptionType.PUT, strike=4975, expiration=exp,
                       dte=45, delta=-0.17, bid=4.9, ask=5.1),
    ])


def test_fill_from_chain_fills_delta_and_mid():
    legs = legs_from_strategy(STRATS["put_credit_spread"],
                              {"short_put": 5000, "long_put": 4975}, dte=45)
    legs, notes = fill_from_chain(legs, _chain("2026-08-28"), "2026-08-28")
    assert notes == []
    assert legs[0].delta == -0.22 and legs[0].premium == 8.0
    assert legs[1].delta == -0.17 and legs[1].premium == 5.0


def test_fill_from_chain_reports_misses_honestly():
    legs = legs_from_strategy(STRATS["put_credit_spread"],
                              {"short_put": 5005, "long_put": 4975}, dte=45)
    legs, notes = fill_from_chain(legs, _chain("2026-08-28"), "2026-08-28")
    assert len(notes) == 1 and "5005" in notes[0]
    assert legs[0].delta == 0.0                    # left honestly blank
    assert legs[1].premium == 5.0                  # the listed one still fills


def _trade(legs, contracts=1) -> Trade:
    return Trade(strategy_key="x", underlying="SPX", contracts=contracts,
                 legs=legs)


def test_sizing_spread_uses_her_credit_not_chain_mids():
    legs = legs_from_strategy(STRATS["put_credit_spread"],
                              {"short_put": 5000, "long_put": 4975}, dte=45)
    s = sizing_from_fill(_trade(legs), STRATS["put_credit_spread"],
                         credit_total=300.0)
    # width 25 x 100 - her actual 300 credit
    assert s["max_loss"] == 2200.0
    assert s["buying_power"] == 2200.0
    assert s["credit"] == 300.0


def test_sizing_iron_condor_uses_wider_side():
    legs = legs_from_strategy(
        STRATS["iron_condor"],
        {"long_put": 4900, "short_put": 4950, "short_call": 5300,
         "long_call": 5350}, dte=30)
    s = sizing_from_fill(_trade(legs), STRATS["iron_condor"], credit_total=400.0)
    assert s["max_loss"] == 50 * 100 - 400.0       # both sides 50 wide here


def test_sizing_cash_secured_put():
    legs = legs_from_strategy(STRATS["cash_secured_put"],
                              {"short_put": 180}, dte=30)
    s = sizing_from_fill(_trade(legs), STRATS["cash_secured_put"],
                         credit_total=300.0)
    assert s["buying_power"] == 180 * 100 - 300.0


def test_sizing_pmcc_capital_is_the_net_debit():
    legs = legs_from_strategy(
        STRATS["poor_mans_covered_call"],
        {"long_call_leaps": 150, "short_call": 220}, dte=30, leaps_dte=400)
    s = sizing_from_fill(_trade(legs), STRATS["poor_mans_covered_call"],
                         credit_total=250.0, leaps_cost_total=6000.0)
    # The LEAPS cost 6000 but the short call handed 250 back the same day, so
    # 5750 is what actually left the account - and the worst case too, since a
    # collapse to zero costs her the LEAPS but never the credit she keeps.
    # Same netting the cash-secured put above already does.
    assert s["buying_power"] == 5750.0
    assert s["max_loss"] == 5750.0


def test_sizing_pmcc_open_cash_is_negative_and_carries_the_leaps():
    """The bug this whole ledger exists for: a PMCC pays money OUT to open."""
    legs = legs_from_strategy(
        STRATS["poor_mans_covered_call"],
        {"long_call_leaps": 100, "short_call": 130}, dte=30, leaps_dte=449)
    s = sizing_from_fill(_trade(legs), STRATS["poor_mans_covered_call"],
                         credit_total=150.0, leaps_cost_total=4000.0)
    assert s["open_cash"] == -3850.0     # 150 collected - 4,000 paid
    assert s["credit"] == 150.0          # the 50% target still measures on this
    assert s["shares_cost"] == 0.0


def test_sizing_covered_call_counts_shares_and_protection():
    legs = legs_from_strategy(
        STRATS["covered_call_model_1"],
        {"long_put_protection": 95, "short_call": 110}, dte=21, leaps_dte=700)
    s = sizing_from_fill(_trade(legs), STRATS["covered_call_model_1"],
                         credit_total=120.0, share_price=100.0,
                         protection_cost_total=300.0)
    # 10,000 of shares + 300 for the protective put - 120 collected. The put's
    # cost used to be dropped entirely: the form never even asked for it.
    assert s["buying_power"] == 10180.0
    assert s["open_cash"] == -10180.0
    assert s["shares_cost"] == 10000.0
    # But the CAPITAL is not the max loss - that is the whole point of Model 1.
    # The 95 put means the shares can only fall 5 points before it takes over:
    # 500 of share fall + 300 for the put - 120 collected = 680. Quoting the
    # 10,180 she laid out would misprice the safest model she has by 15x.
    assert s["max_loss"] == 680.0


def test_sizing_credit_shapes_open_cash_is_just_the_credit():
    """The ledger must not disturb the strategies that already worked."""
    legs = legs_from_strategy(STRATS["put_credit_spread"],
                              {"short_put": 5000, "long_put": 4950}, dte=45)
    s = sizing_from_fill(_trade(legs), STRATS["put_credit_spread"],
                         credit_total=500.0)
    assert s["open_cash"] == 500.0
    assert s["buying_power"] == 50 * 100 - 500.0


# ===========================================================================
# The LEAPS long call - the one strategy she BUYS. It was unloggable: the form
# offered a single box asking for the credit on her fill, so a call that cost
# $2,115 could only be recorded as a trade that paid her, and the put(s) sold
# alongside it had nowhere to go at all.
def test_legs_for_a_financed_leaps_carry_the_sold_puts():
    """The financing put is deliberately NOT in the strategy's `legs` block, so
    it can only come from her fill - how many, and at what strike."""
    legs = legs_from_strategy(STRATS["long_call_leaps"],
                              {"long_call_leaps": 70, "financing_put": 75},
                              dte=518, financing_puts=3)
    assert [l.role for l in legs] == ["long_call_leaps", "financing_put"]
    call, put = legs
    assert call.action == Action.BUY and call.option_type == OptionType.CALL
    assert put.action == Action.SELL and put.option_type == OptionType.PUT
    assert put.quantity == 3            # three sold against the one call
    assert put.dte == call.dte == 518   # one trade, one end date


def test_the_plain_leaps_still_has_exactly_one_leg():
    """The variant must never appear unasked."""
    legs = legs_from_strategy(STRATS["long_call_leaps"], {"long_call_leaps": 70},
                              dte=518)
    assert [l.role for l in legs] == ["long_call_leaps"]


def test_her_own_fill_prices_beat_the_chain_mids():
    """She types the two sides of a bought call separately, so those are exact
    fills - the chain's mid is an estimate of a different moment."""
    legs = legs_from_strategy(STRATS["long_call_leaps"],
                              {"long_call_leaps": 70, "financing_put": 75},
                              dte=518, financing_puts=3)
    for leg in legs:
        leg.premium = 99.0              # as if a chain had filled them
    apply_fill_prices(legs, {"long_call_leaps": 21.15, "financing_put": 6.25})
    assert [l.premium for l in legs] == [21.15, 6.25]


def test_sizing_a_plain_bought_leaps_is_the_debit_and_no_buying_power():
    legs = legs_from_strategy(STRATS["long_call_leaps"], {"long_call_leaps": 185},
                              dte=400)
    s = sizing_from_fill(_trade(legs), STRATS["long_call_leaps"],
                         credit_total=0.0, leaps_cost_total=3900.0)
    assert s["open_cash"] == -3900.0    # money OUT to open, like the PMCC
    assert s["max_loss"] == 3900.0      # it can go to zero, and that is all of it
    assert s["buying_power"] == 0.0     # bought with cash, nothing held against it
    assert s["credit"] == 0.0           # there is no credit on a call you bought


def test_sizing_a_financed_leaps_counts_the_collateral_not_just_the_debit():
    """The shape of the trade this whole feature was written for: a call bought
    for $2,115 with three puts sold against it for $1,875. Only $240 left the
    account - and $22,500 has to stand behind those puts until they expire.
    Reporting the $240 as the size of the position is the trap."""
    legs = legs_from_strategy(STRATS["long_call_leaps"],
                              {"long_call_leaps": 70, "financing_put": 75},
                              dte=518, financing_puts=3)
    s = sizing_from_fill(_trade(legs), STRATS["long_call_leaps"],
                         credit_total=1875.0, leaps_cost_total=2115.0)
    assert s["open_cash"] == -240.0            # what actually left the account
    assert s["max_loss"] == 22740.0            # 240 + the 22,500 promised
    assert s["buying_power"] == 20625.0        # strike x 300 less the credit
    assert s["credit"] == 0.0                  # the put credit is not income


def test_the_put_credit_is_never_logged_as_premium_sold():
    """It pays down what the call cost. Counted as a credit it would set a 50%
    profit target on money she never earned, and show up in the month report as
    income she did not collect."""
    legs = legs_from_strategy(STRATS["long_call_leaps"],
                              {"long_call_leaps": 70, "financing_put": 75},
                              dte=518, financing_puts=3)
    s = sizing_from_fill(_trade(legs), STRATS["long_call_leaps"],
                         credit_total=1875.0, leaps_cost_total=2115.0)
    assert s["credit"] == 0.0 and s["return_on_risk"] == 0.0


def test_correcting_a_financed_leaps_rebuilds_the_call_cost_from_the_legs():
    """resize_after_edit works backwards from the stored ledger. On every other
    shape the credit column gives it a foothold; here that column is zero by
    design, so the put's own fill price on the leg is the only way back to what
    the call cost."""
    from src.engine.quick_log import resize_after_edit

    legs = legs_from_strategy(STRATS["long_call_leaps"],
                              {"long_call_leaps": 70, "financing_put": 75},
                              dte=518, financing_puts=3)
    apply_fill_prices(legs, {"long_call_leaps": 21.15, "financing_put": 6.25})

    # Same trade, corrected from 1 contract to 2: everything scales.
    fresh = resize_after_edit(STRATS["long_call_leaps"], _trade(legs, contracts=2),
                              credit_total=0.0, old_credit=0.0,
                              old_open_cash=-240.0, old_shares_cost=0.0,
                              old_contracts=1)
    assert fresh["open_cash"] == -480.0
    assert fresh["max_loss"] == 45480.0
    assert fresh["buying_power"] == 41250.0


def test_correcting_a_plain_leaps_keeps_its_cost():
    from src.engine.quick_log import resize_after_edit

    legs = legs_from_strategy(STRATS["long_call_leaps"], {"long_call_leaps": 185},
                              dte=400)
    fresh = resize_after_edit(STRATS["long_call_leaps"], _trade(legs),
                              credit_total=0.0, old_credit=0.0,
                              old_open_cash=-3900.0, old_shares_cost=0.0,
                              old_contracts=1)
    assert fresh["open_cash"] == -3900.0 and fresh["max_loss"] == 3900.0
