"""Quick Log: building a tracked position from what Rita reads off her TOS
fill - strikes, expiration, contracts, credit - with the chain filling in the
rest when it can."""

from __future__ import annotations

from src.data.chain import OptionChain, OptionContract
from src.engine.config_loader import load_strategies
from src.engine.models import Action, OptionType, Trade
from src.engine.quick_log import (
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
