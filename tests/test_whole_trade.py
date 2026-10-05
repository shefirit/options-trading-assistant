"""A rolled or winged trade, read as the whole trade.

Her CRWD condor's card on 2026-10-05: "closing it costs $2,314 against the
$836 you collected, so it is $1,478 down", the stop at "-177% of the credit",
the table pointing at a 240 call already lost, and one expiration printed for
legs on two dates. The trade had collected $1,475 and was $839 down; the 260
put, 4.7% below price, was the side still at risk.

Her ruling on the stop: "measure the stop on the whole trade".

Synthetic fixtures, shared with test_merge_condor.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import exit_rules, glance
from src.engine.positions import parse_rows, strike_cushion
from src.logging_tools.row import COLUMNS, build_roll_row
from test_merge_condor import CALL_WING, MERGE, PUT_WING, _put_roll  # noqa: E402

LATER = date(2026, 10, 23)
TODAY = date(2026, 10, 5)
EXIT = {"profit_target_pct": 50, "stop_loss_multiple": 2.0, "time_exit_dte": 21}

CONDOR_ROWS = [CALL_WING, PUT_WING, MERGE,
               _put_roll(date(2026, 9, 28), 230, 210),
               _put_roll(TODAY, 260, 250, expiration=LATER, cash=498.69,
                         credit=564.0)]


def _condor():
    return {p.trade_id: p for p in parse_rows(COLUMNS, CONDOR_ROWS)}["T-CALL"]


def test_everything_collected_is_counted():
    p = _condor()
    assert p.has_history
    assert p.whole_trade_collected == pytest.approx(272 + 430 + 274 + 498.69)


def test_the_stop_is_measured_on_the_whole_trade():
    p = _condor()
    # On the $836 credit alone a $2,600 buy-back is past 2x; on the $1,475
    # collected it is nowhere near.
    sig = exit_rules.evaluate(p, EXIT, current_cost=2600.0, today=TODAY)
    assert sig.action != "stop"
    assert sig.stop_base == pytest.approx(1474.69)
    assert sig.stop_pl == pytest.approx(1474.69 - 2600.0)


def test_a_rolled_spread_still_stops_on_the_whole_trade():
    """$430 opened, $50 banked on a roll: the stop is 2x $480."""
    roll = build_roll_row("T-PUT", "CRWD", "Put Credit Spread (Bull Put Spread)",
                          50.0, new_strike=210, new_expiration=LATER,
                          new_credit=300.0, rolled_on=date(2026, 9, 20),
                          account="real", option_type="put", new_long_strike=195)
    p = parse_rows(COLUMNS, [PUT_WING, roll])[0]
    cfg = {"stop_loss_multiple": 2.0}
    assert exit_rules.evaluate(p, cfg, current_cost=1400.0, today=TODAY).action != "stop"
    stopped = exit_rules.evaluate(p, cfg, current_cost=1450.0, today=TODAY)
    assert stopped.action == "stop"
    assert "whole trade" in stopped.reason


def test_a_trade_nothing_happened_to_is_unchanged():
    p = parse_rows(COLUMNS, [CALL_WING])[0]
    assert not p.has_history
    sig = exit_rules.evaluate(p, EXIT, current_cost=500.0, today=TODAY)
    assert sig.stop_base == p.credit
    assert sig.stop_pl == sig.pl_dollars


def test_the_card_headline_counts_the_whole_trade():
    line = glance.summary_line(_condor(), {"cost_to_close": 2314.0,
                                           "underlying_price": 272.67}, None)
    assert "$1,475 collected over the whole trade" in line
    assert "$839 down" in line
    assert "$836" not in line


def test_the_side_already_lost_is_not_the_one_watched():
    c = strike_cushion(_condor(), 272.67)
    assert (c["strike"], c["option_type"]) == (260, "put")
    assert c["room_pct"] == pytest.approx((272.67 - 260) / 272.67)
    assert [l["strike"] for l in c["locked"]] == [240]


def test_a_side_only_tested_is_still_the_one_watched():
    """At 245 the call side is tested but not through: it is the risk. The
    inverted put side (260/250) is past both strikes there, so it is locked."""
    c = strike_cushion(_condor(), 245.0)
    assert (c["strike"], c["option_type"]) == (240, "call")
    assert [l["strike"] for l in c["locked"]] == [260]


def test_a_normal_condor_inside_its_wings_locks_nothing():
    p = {x.trade_id: x for x in parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE])}["T-CALL"]
    assert strike_cushion(p, 230.0)["locked"] == []


def test_the_legs_line_names_both_dates():
    from ui.trades.open_trades import _legs_line
    line = _legs_line(_condor())
    assert "16/10/2026" in line and "23/10/2026" in line


def test_the_locked_side_gets_no_roll_advice():
    sig = exit_rules.evaluate(_condor(), EXIT, current_cost=2314.0,
                              underlying_price=272.67, today=TODAY)
    text = " ".join([sig.reason] + sig.notes)
    assert "roll up and out" not in text
    assert "240 call side is past both of its strikes" in text


def test_the_locked_side_does_not_set_the_red_flag_delta():
    from types import SimpleNamespace

    from src.engine import positions as pm
    p = _condor()
    deltas = {(240, "call"): 0.93, (250, "call"): 0.85,
              (260, "put"): 0.28, (250, "put"): 0.15}

    def fake(chain, leg, want, avoid=None):
        return SimpleNamespace(mid=1.0,
                               delta=deltas[(leg.strike, leg.option_type.value)])

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(pm, "_contract_for_leg", fake)
    try:
        out = pm.cost_to_close_from_chain(p, SimpleNamespace(underlying_price=272.67))
    finally:
        mp.undo()
    assert out["short_delta"] == pytest.approx(0.28)
