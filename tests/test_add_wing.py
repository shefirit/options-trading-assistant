"""Turning an open credit spread into an iron condor by selling the other side.

Her ask, 2026-09-18: "sometimes i want to add to credit spread the oposite side
so it makes it iron condor."

It is ONE trade, not two, and that is the part with money in it. Price cannot
break through both sides at expiration, so the broker holds margin for the
wider wing only - logging the second wing as its own trade would double the
buying power the app thinks she has committed and measure each half against its
own 50% target, which is not what her Iron Condor page says.

Her ruling on the delta conflict: WARN, never block. The Iron Condor page wants
0.15 per leg; her Put Credit Spread page enters at 0.25, so legging in almost
always lands above what the condor page describes. The app shows her the number
and she decides.

All synthetic. This repo is public.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import config_loader, wings
from src.engine.models import Action, CheckStatus, Leg, OptionType, Trade
from src.engine.positions import parse_rows
from src.logging_tools.row import COLUMNS, build_add_wing_row, build_row

OPENED = date(2026, 9, 11)
EXP = date(2026, 10, 23)
ADDED = date(2026, 9, 18)


def _spread_rows(*extra):
    """An open 65/60 put credit spread at 0.25 delta, plus any event rows."""
    legs = [Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=65.0, premium=1.64, delta=-0.25, dte=42, expiration=EXP),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=60.0, premium=0.76, dte=42, expiration=EXP)]
    t = Trade(strategy_key="put_credit_spread", underlying="FCX", legs=legs,
              contracts=2, underlying_price=70.86)
    open_row = build_row(t, "Put Credit Spread",
                         {"credit": 176.0, "open_cash": 176.0}, True, "",
                         trade_id="IC1", opened_on=OPENED)
    return [open_row, *extra]


def _wing_row(**kw):
    args = dict(trade_id="IC1", underlying="FCX",
                strategy_name="Put Credit Spread", option_type="call",
                short_strike=78.0, long_strike=83.0, credit=120.0,
                short_delta=0.15, quantity=1, added_on=ADDED,
                expiration=EXP, account="real")
    args.update(kw)
    return build_add_wing_row(**args)


def _spread():
    return parse_rows(COLUMNS, _spread_rows())[0]


def _condor():
    return parse_rows(COLUMNS, _spread_rows(_wing_row()))[0]


# ------------------------------------------------------------ the replay
def test_the_wing_adds_two_legs_on_the_other_side():
    p = _condor()
    got = {(l.role, l.action, l.option_type, l.strike) for l in p.legs}
    assert (("short_call", Action.SELL, OptionType.CALL, 78.0)) in got
    assert (("long_call", Action.BUY, OptionType.CALL, 83.0)) in got
    assert len(p.legs) == 4


def test_the_credit_is_added_not_replaced():
    """A roll REPLACES the credit, because the old leg is gone. A wing adds to
    it, because her page closes the whole condor at 50% of everything
    collected."""
    assert _spread().credit == 176.0
    assert _condor().credit == 296.0


def test_the_wing_banks_nothing():
    """Nothing was closed, so no income was realised. A wing that banked its
    credit would show as a profitable month before the trade even resolved."""
    from src.engine.positions import cash_events
    assert cash_events([_condor()]) == []


def test_the_new_legs_share_the_trades_expiration():
    p = _condor()
    assert {l.expiration for l in p.legs} == {EXP}
    assert {l.dte for l in p.legs} == {42}


def test_the_short_call_delta_keeps_the_chains_sign():
    """Puts negative, calls positive - the same convention every other leg in
    the app uses, so a position-delta sum does not silently flip."""
    call = next(l for l in _condor().legs if l.role == "short_call")
    assert call.delta == pytest.approx(0.15)
    put = next(l for l in _condor().legs if l.role == "short_put")
    assert put.delta < 0


def test_a_put_wing_stores_its_delta_negative():
    rows = _spread_rows(_wing_row(option_type="put", short_strike=55.0,
                                  long_strike=50.0, short_delta=0.12))
    # (contrived - the base spread is already a put spread - but the sign
    # convention must hold whichever side is added)
    leg = [l for l in parse_rows(COLUMNS, rows)[0].legs
           if l.role == "short_put" and l.strike == 55.0]
    assert leg and leg[0].delta == pytest.approx(-0.12)


def test_the_wing_event_is_kept_for_the_card():
    w = _condor().wings
    assert len(w) == 1
    assert (w[0].option_type, w[0].short_strike, w[0].long_strike,
            w[0].credit, w[0].added_on) == ("call", 78.0, 83.0, 120.0, ADDED)


# --------------------------------------------- the shape renames the trade
def test_the_position_is_now_run_as_an_iron_condor():
    """The row still says Put Credit Spread - it was one when she opened it -
    but the condor page is the only one with a rule for two wings."""
    p = _condor()
    assert p.strategy_key == "put_credit_spread"
    assert p.is_iron_condor_shape
    assert p.effective_strategy_key == "iron_condor"


def test_a_plain_spread_is_not_mistaken_for_a_condor():
    p = _spread()
    assert not p.is_iron_condor_shape
    assert p.effective_strategy_key == "put_credit_spread"


def test_the_condor_exit_rules_are_what_get_applied():
    """The point of the rename: 50% of the COMBINED credit."""
    cfg = config_loader.get_strategy(_condor().effective_strategy_key)
    assert cfg["exit"]["profit_target_pct"] == 50
    assert cfg["name"] == "Iron Condor"


# ------------------------------------------------------- what may be refused
def test_a_second_wing_on_the_same_side_is_refused():
    """Two put wings is a bigger put spread, not a condor."""
    stop = wings.blocking(_spread(), "put", 55.0, 50.0, EXP)
    assert stop and "already short a put" in stop[0]


def test_protection_on_the_wrong_side_of_the_short_strike_is_refused():
    assert wings.blocking(_spread(), "call", 78.0, 73.0, EXP)   # long below short
    assert not wings.blocking(_spread(), "call", 78.0, 83.0, EXP)


def test_a_different_expiration_is_refused():
    """Two expirations is a double diagonal, which is not one of her eight."""
    stop = wings.blocking(_spread(), "call", 78.0, 83.0, date(2026, 11, 20))
    assert stop and "expire together" in stop[0]


def test_nothing_else_is_ever_refused():
    """Her ruling. A wildly out-of-SOP wing still goes through - it warns."""
    assert wings.blocking(_spread(), "call", 78.0, 83.0, EXP) == []


# --------------------------------------------------------------- the warnings
def _checks(**kw):
    args = dict(option_type="call", short_strike=78.0, long_strike=83.0,
                credit=120.0, short_delta=0.15)
    args.update(kw)
    return {c.name: c for c in wings.checks(
        _spread(), settings=config_loader.load_settings(),
        strategy=config_loader.get_strategy("iron_condor"), **args)}


def test_legging_in_from_a_025_spread_warns_about_the_combined_delta():
    """THE warning. 0.25 + 0.15 = 0.40 against the 0.30 her page targets."""
    c = _checks()["Combined short delta"]
    assert c.status is CheckStatus.WARN
    assert "0.40" in c.message and "0.30" in c.message
    assert c.actual == "0.40"


def test_a_compliant_condor_passes_the_delta_check():
    """If the open wing were at 0.15, legging in lands exactly on the page."""
    c = _checks(short_delta=0.05)
    assert c["Combined short delta"].status is CheckStatus.PASS


def test_no_delta_typed_says_what_it_cannot_check():
    c = _checks(short_delta=None)["Combined short delta"]
    assert c.status is CheckStatus.INFO
    assert "cannot check" in c.message


def test_a_mismatched_width_warns_that_max_loss_follows_the_wider_side():
    c = _checks(long_strike=88.0)["Both wings the same width"]      # 10 vs 5
    assert c.status is CheckStatus.WARN
    assert "wider" in c.message.lower()


def test_matching_widths_raise_no_width_warning():
    assert "Both wings the same width" not in _checks()


def test_the_combined_floor_is_measured_on_BOTH_wings():
    """Her Iron Condor page's rule: net credit against 6% of ONE wing's width.
    $176 + $1 still clears a $30 floor, so this check passes - correctly. It is
    the finished condor it judges, not the wing that was just added."""
    c = _checks(credit=1.0)
    key = next(k for k in c if k.startswith("Net credit"))
    assert c[key].status is CheckStatus.PASS


def test_a_near_worthless_second_wing_is_still_called_out():
    """The gap the combined floor leaves: the wing she already holds carries
    it, so a wing collecting $1 sails through while adding a whole second side
    that can be breached. Judged against the credit spread pages' own 6%."""
    c = _checks(credit=1.0)["This wing pays for its own risk"]
    assert c.status is CheckStatus.WARN
    assert c.expected == "$30" and c.actual == "$1"


def test_a_healthy_second_wing_raises_no_such_warning():
    assert "This wing pays for its own risk" not in _checks()


def test_she_is_told_the_buying_power_barely_moves():
    """The reason to do this at all, said out loud rather than left implied."""
    assert _checks()["Buying power barely moves"].status is CheckStatus.INFO


def test_she_is_told_the_management_rules_change():
    c = _checks()["It will be managed as an Iron Condor"]
    assert "50%" in c.message and "296" in c.message


# ------------------------------------------------------- who may be offered it
def test_the_form_is_offered_on_a_one_sided_credit_spread():
    from ui.trades.actions import _can_add_wing
    assert _can_add_wing(_spread())


def test_the_form_is_not_offered_once_it_is_already_a_condor():
    from ui.trades.actions import _can_add_wing
    assert not _can_add_wing(_condor())
