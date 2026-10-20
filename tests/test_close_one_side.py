"""One side of an iron condor bought back, the other side left open.

Her ask, 2026-10-15: "log the call spread close in the app. i don't have this
option to close only one side". Her CRWD condor's call side was due to expire
the next day with the stock between its strikes, so she bought that vertical
back as one order and kept the put side running. The close form ends the whole
trade and the leg form takes off one leg, so neither could record it.

The condor fixtures come from test_merge_condor: a call wing, a put wing
merged into it, and two put rolls after the merge.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import exit_rules
from src.engine.models import OptionType
from src.engine.positions import cash_events, parse_rows, story
from src.logging_tools.row import (COLUMNS, build_add_wing_row,
                                   build_close_row, build_roll_row,
                                   build_wing_close_row)
from test_merge_condor import (EXP, LATER, PUT_WING, _by_id,
                                     _crwd_today, _spread, _strikes)

CLOSED_ON = date(2026, 10, 15)


def _call_side_off(cash=-710.0, on=CLOSED_ON):
    return build_wing_close_row("T-CALL", "CRWD",
                                "Call Credit Spread (Bear Call Spread)",
                                "call", cash, short_strike=240, long_strike=250,
                                closed_on=on, account="real")


def _after():
    return _by_id(_crwd_today() + [_call_side_off()])["T-CALL"]


# ------------------------------------------------------------------ the row
def test_the_row_is_its_own_event_with_the_cash_banked():
    row = dict(zip(COLUMNS, _call_side_off()))
    assert row["Event"] == "wingclose"
    assert row["Trade ID"] == "T-CALL"
    assert row["Realized P&L $"] == -710.0
    assert row["Legs (strikes)"] == "240 / 250"
    assert '"type":"call"' in row["Details JSON"]
    assert "put side stays open" in row["Notes"]


# ------------------------------------------------------------- the replay
def test_the_call_legs_go_and_the_put_spread_stays():
    p = _after()
    assert p.status == "open"
    assert _strikes(p, OptionType.CALL) == []
    assert _strikes(p, OptionType.PUT) == [250, 260]
    assert not p.is_iron_condor_shape


def test_it_is_read_as_a_put_credit_spread_now():
    """Logged as a call credit spread, but the call page's rules no longer
    describe anything she holds."""
    p = _after()
    assert p.effective_strategy_key == "put_credit_spread"
    assert p.shown_strategy_name.startswith("Put Credit Spread")


def test_the_target_measures_the_side_still_open():
    """Before: 272 call + 564 put. The call wing's 272 is gone with it."""
    p = _after()
    assert p.credit == 564.0
    assert p.wing_credit == {}


def test_the_countdown_moves_to_the_put_side():
    assert _after().expiration == LATER


def test_the_cost_is_banked_on_the_day_it_was_paid():
    p = _after()
    assert p.leg_close_cash == -710.0
    days = [(e["date"], e["amount"]) for e in cash_events([p])
            if e["kind"] == "legclose"]
    assert days == [(CLOSED_ON, -710.0)]


def test_the_whole_trade_counts_every_dollar():
    """272 + 430 opened, 274 + 498.69 from the rolls, 710 paid today."""
    assert _after().whole_trade_collected == pytest.approx(764.69)


def test_the_risk_left_is_the_put_spread_less_everything_collected():
    p = _after()
    assert p.buying_power == 2000.0
    assert p.max_loss == pytest.approx(2000.0 - 764.69)


def test_the_story_says_which_side_closed():
    steps = [s for s in story(_after()) if s["kind"] == "legclose"]
    assert len(steps) == 1
    assert steps[0]["what"] == "You closed the call side"
    assert steps[0]["cash"] == -710.0


def test_the_stop_is_measured_on_the_whole_trade():
    p = _after()
    sig = exit_rules.evaluate(p, {"stop_loss_multiple": 2.0,
                                  "profit_target_pct": 50,
                                  "time_exit_dte": 21},
                              current_cost=1445.0, underlying_price=241.0,
                              today=CLOSED_ON)
    assert sig.stop_base == pytest.approx(764.69)


def test_closing_the_rest_later_adds_up_to_the_cash_that_moved():
    close = build_close_row("T-CALL", "CRWD",
                            "Call Credit Spread (Bear Call Spread)",
                            1445.0, 702.0 - 1445.0, "Closed early",
                            closed_on=date(2026, 10, 19), close_cash=-1445.0,
                            account="real")
    p = _by_id(_crwd_today() + [_call_side_off(), close])["T-CALL"]
    assert p.status == "closed"
    gross_in = 272.0 + 430.0 + 274.0 + 498.69
    assert p.realized_total == pytest.approx(gross_in - 710.0 - 1445.0)


# ------------------------------------------------ the side sold again
RESOLD_ON = date(2026, 10, 20)


def _new_call_side():
    """Her 20 Oct fill: SOLD -2 VERTICAL CRWD 23 OCT 237.5/247.5 CALL @.90."""
    return build_add_wing_row("T-CALL", "CRWD",
                              "Call Credit Spread (Bear Call Spread)", "call",
                              237.5, 247.5, 180.0, added_on=RESOLD_ON,
                              expiration=LATER, account="real")


def _resold(*more):
    return _by_id(_crwd_today() + [_call_side_off(), _new_call_side(),
                                   *more])["T-CALL"]


def test_a_call_side_sold_after_the_close_survives_it():
    """The close took every call leg with it, including the new ones sold five
    days later - the condor read as a lone put spread."""
    p = _resold()
    assert _strikes(p, OptionType.CALL) == [237.5, 247.5]
    assert _strikes(p, OptionType.PUT) == [250, 260]
    assert p.is_iron_condor_shape
    assert p.expiration == LATER


def test_the_target_measures_both_sides_now_open():
    p = _resold()
    assert p.credit == 564.0 + 180.0
    assert p.wing_credit == {"put": 564.0, "call": 180.0}


def test_the_whole_trade_adds_the_new_side():
    assert _resold().whole_trade_collected == pytest.approx(764.69 + 180.0)


def test_the_story_is_told_in_date_order():
    """Her Journal listed the 20 Oct call side before the 15 Oct close, and
    both before the September rolls."""
    steps = story(_resold())
    days = [s["on"] for s in steps]
    assert days == sorted(days)
    assert [s["kind"] for s in steps][-2:] == ["legclose", "wing"]
    assert steps[-1]["running"] == pytest.approx(764.69 + 180.0)


def test_a_roll_of_the_new_side_moves_the_new_legs():
    roll = build_roll_row("T-CALL", "CRWD",
                          "Call Credit Spread (Bear Call Spread)", 60.0,
                          242.5, LATER, 120.0, "",
                          rolled_on=date(2026, 10, 21), account="real",
                          option_type="call", new_long_strike=252.5)
    p = _resold(roll)
    assert _strikes(p, OptionType.CALL) == [242.5, 252.5]
    assert _strikes(p, OptionType.PUT) == [250, 260]


# ------------------------------------------------------------ edge cases
def test_closing_a_side_she_does_not_hold_changes_nothing_but_the_cash():
    """A put spread with no call side: the row is a typo, not a reason to drop
    a live position."""
    row = build_wing_close_row("T-PUT", "CRWD", "Put Credit Spread", "call",
                               -50.0, closed_on=CLOSED_ON, account="real")
    p = _by_id([PUT_WING, row])["T-PUT"]
    assert _strikes(p, OptionType.PUT) == [200, 215]
    assert p.effective_strategy_key == "put_credit_spread"


def test_closing_the_put_side_of_a_same_date_condor_leaves_a_call_spread():
    call = _spread("C1", date(2026, 9, 2), "call", 240, 250, 272.0)
    put = _spread("P1", date(2026, 9, 3), "put", 215, 200, 430.0)
    from src.logging_tools.row import build_merge_row
    merge = build_merge_row("P1", "C1", "CRWD", "Put Credit Spread",
                            merged_on=date(2026, 9, 4), account="real")
    off = build_wing_close_row("C1", "CRWD", "Call Credit Spread", "put",
                               -40.0, short_strike=215, long_strike=200,
                               closed_on=date(2026, 9, 30), account="real")
    p = _by_id([call, put, merge, off])["C1"]
    assert _strikes(p, OptionType.PUT) == []
    assert p.effective_strategy_key == "call_credit_spread"
    assert p.credit == 272.0
    assert p.expiration == EXP
    assert p.buying_power == 2000.0
    assert p.max_loss == pytest.approx(2000.0 - (272.0 + 430.0 - 40.0))


def test_a_condor_without_a_side_closed_is_unchanged():
    p = _by_id(_crwd_today())["T-CALL"]
    assert p.is_iron_condor_shape
    assert p.effective_strategy_key == "iron_condor"
    assert p.spread_side_left is None


def test_an_unknown_event_row_is_still_skipped_by_parse():
    """Guard for the rule this event relies on: a row whose event the replay
    does not know never becomes a phantom open trade."""
    row = list(_call_side_off())
    row[COLUMNS.index("Event")] = "somethingnew"
    ids = [p.trade_id for p in parse_rows(COLUMNS, _crwd_today() + [row])]
    assert ids.count("T-CALL") == 1


# ------------------------------------------------------------ the form
def test_the_card_offers_closing_one_side_and_records_it(app_with_rows,
                                                         monkeypatch):
    from src.logging_tools import trade_logger

    sent = []
    monkeypatch.setattr(trade_logger, "close_wing",
                        lambda *a, **k: sent.append((a, k)) or ("local", False))
    at = app_with_rows(_crwd_today()).run()
    assert not at.exception
    labels = [x.label for x in at.expander]
    assert any("Close one side, keep the other open" in l for l in labels), \
        f"the form is missing - expanders were {labels}"

    pick = next(r for r in at.radio if (r.key or "").startswith("sidepick_"))
    assert any("call side, 240 / 250" in o for o in pick.options)
    pick.set_value(next(o for o in pick.options if "call side" in o)).run()
    next(n for n in at.number_input
         if (n.key or "").startswith("sidecost_")).set_value(3.55).run()
    next(b for b in at.button
         if (b.key or "").startswith("sidebtn_")).click().run()

    assert len(sent) == 1
    args, kwargs = sent[0]
    assert args[0] == "T-CALL" and args[3] == "call"
    assert kwargs["cash"] == pytest.approx(-710.0)
    assert (kwargs["short_strike"], kwargs["long_strike"]) == (240.0, 250.0)


def test_a_plain_spread_card_does_not_offer_it(app_with_rows):
    at = app_with_rows([PUT_WING]).run()
    assert not at.exception
    assert not any("Close one side" in x.label for x in at.expander)
