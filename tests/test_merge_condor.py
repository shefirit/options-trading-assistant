"""Two rows that are really one iron condor.

Her report, 2026-09-18: "CRWD - you see 2 trades. but it's iron condor. please
fix. XSP also."

She had sold a CRWD call spread on 2 September and a CRWD put spread on 17
September - same expiration, same size. Both rows are true; there is one trade.
The app read them as two, which meant two cards, two 50% targets each measured
against half the credit, and her Iron Condor page's actual rule (close the
whole thing at 50% of the NET credit) applying to neither.

The merge is its own row saying how to read the two together. Neither original
is touched, and deleting the merge row puts them back.

All synthetic - her real strikes are not in here. This repo is public.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.engine import positions as pos_mod, wings
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import parse_rows
from src.logging_tools.row import COLUMNS, build_merge_row, build_row

EXP = date(2026, 10, 16)


def _spread(trade_id, opened, side, short, long_, credit, contracts=2,
            account="real", expiration=EXP, delta=0.2):
    kind = OptionType.CALL if side == "call" else OptionType.PUT
    sign = 1 if side == "call" else -1
    dte = (expiration - opened).days
    legs = [
        Leg(role=f"short_{side}", action=Action.SELL, option_type=kind,
            strike=short, premium=1.5, delta=sign * delta, dte=dte,
            expiration=expiration),
        Leg(role=f"long_{side}", action=Action.BUY, option_type=kind,
            strike=long_, premium=0.6, dte=dte, expiration=expiration),
    ]
    name = ("Call Credit Spread (Bear Call Spread)" if side == "call"
            else "Put Credit Spread (Bull Put Spread)")
    t = Trade(strategy_key=f"{side}_credit_spread", underlying="CRWD",
              legs=legs, contracts=contracts, underlying_price=230.0)
    # Max loss and buying power as the open row records them - the width less
    # the credit. This is what a merged condor has to OVERRIDE, so the fixture
    # has to carry it or the tests below prove nothing.
    risk = round(abs(short - long_) * 100 * contracts - credit, 2)
    return build_row(t, name, {"credit": credit, "open_cash": credit,
                               "account": account, "max_loss": risk,
                               "buying_power": risk},
                     True, "", trade_id=trade_id, opened_on=opened)


CALL_WING = _spread("T-CALL", date(2026, 9, 2), "call", 240, 250, 272.0)
PUT_WING = _spread("T-PUT", date(2026, 9, 17), "put", 215, 200, 430.0)
MERGE = build_merge_row("T-PUT", "T-CALL", "CRWD",
                        "Put Credit Spread (Bull Put Spread)",
                        merged_on=date(2026, 9, 18), account="real")


def _by_id(rows):
    return {p.trade_id: p for p in parse_rows(COLUMNS, rows)}


# --------------------------------------------------------------- the merge
def test_the_two_become_one_condor():
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-CALL"]
    assert p.is_iron_condor_shape
    assert p.effective_strategy_key == "iron_condor"
    assert len(p.legs) == 4
    assert {l.strike for l in p.legs} == {240, 250, 215, 200}


def test_the_credits_add_up_so_the_target_is_on_the_whole_thing():
    """The defect she actually felt: two 50% targets on half the credit each."""
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-CALL"]
    assert p.credit == 702.0                      # 272 + 430


def test_the_absorbed_trade_is_neither_open_nor_closed():
    """"merged" drops it from the open cards without inventing a closed trade
    in her journal - it never ended, it joined."""
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-PUT"]
    assert p.status == "merged"
    assert p.merged_into == "T-CALL"
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE])
    assert [x.trade_id for x in pos_mod.open_positions(positions)] == ["T-CALL"]
    assert pos_mod.closed_positions(positions) == []


def test_the_absorbed_premium_still_counts_as_sold_when_she_sold_it():
    """She DID sell that wing in September. Only `credit` moves; open_credit
    stays put, so the month's premium-sold figure is unchanged."""
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-PUT"]
    assert p.open_credit == 430.0


def test_nothing_is_banked_by_a_merge():
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE])
    assert pos_mod.cash_events(positions) == []


def test_without_the_merge_row_they_stay_two_trades():
    """The row is the whole mechanism - delete it and this is undone."""
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING])
    assert len(pos_mod.open_positions(positions)) == 2
    assert not any(p.is_iron_condor_shape for p in positions)


@pytest.mark.parametrize("rows", [
    [CALL_WING, MERGE],                       # target present, absorbed missing
    [PUT_WING, MERGE],                        # absorbed present, target missing
])
def test_a_merge_naming_a_missing_trade_is_ignored(rows):
    """A typo in a trade id must not silently drop a live position."""
    positions = parse_rows(COLUMNS, rows)
    assert all(p.status == "open" for p in positions)


def test_a_trade_cannot_be_merged_into_itself():
    self_merge = build_merge_row("T-CALL", "T-CALL", "CRWD", "x")
    p = _by_id([CALL_WING, self_merge])["T-CALL"]
    assert p.status == "open" and len(p.legs) == 2


# ----------------------------------------------------------- the detection
def _open(rows):
    return pos_mod.open_positions(parse_rows(COLUMNS, rows))


def test_the_other_wing_is_found():
    open_pos = _open([CALL_WING, PUT_WING])
    call = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert [m.trade_id for m in wings.merge_candidates(call, open_pos)] == ["T-PUT"]


def test_a_spread_on_the_same_side_is_not_a_wing():
    """Two put spreads is two trades, however alike they look."""
    other = _spread("T-PUT2", date(2026, 9, 16), "put", 210, 195, 300.0)
    open_pos = _open([PUT_WING, other])
    put = next(p for p in open_pos if p.trade_id == "T-PUT")
    assert wings.merge_candidates(put, open_pos) == []


def test_a_different_expiration_is_not_a_wing():
    other = _spread("T-NOV", date(2026, 9, 17), "put", 215, 200, 430.0,
                    expiration=date(2026, 11, 20))
    open_pos = _open([CALL_WING, other])
    call = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert wings.merge_candidates(call, open_pos) == []


def test_a_paper_wing_is_never_part_of_a_real_condor():
    """The one that would have bitten: her third XSP spread is on the practice
    book. Merging the two books would put paper money in a real position."""
    paper = _spread("T-PAPER", date(2026, 9, 17), "put", 215, 200, 430.0,
                    account="paper")
    open_pos = _open([CALL_WING, paper])
    call = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert wings.merge_candidates(call, open_pos) == []


def test_a_naked_put_is_not_a_wing():
    """A cash secured put has no long leg, so it cannot be half a condor."""
    legs = [Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=215, premium=2.0, delta=-0.3, dte=29, expiration=EXP)]
    t = Trade(strategy_key="cash_secured_put", underlying="CRWD", legs=legs,
              contracts=2, underlying_price=230.0)
    csp = build_row(t, "Cash Secured Put (CSP)",
                    {"credit": 400.0, "account": "real"}, True, "",
                    trade_id="T-CSP", opened_on=date(2026, 9, 17))
    open_pos = _open([CALL_WING, csp])
    call = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert wings.merge_candidates(call, open_pos) == []


def test_a_finished_condor_is_not_offered_another_wing():
    open_pos = _open([CALL_WING, PUT_WING, MERGE])
    condor = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert wings.merge_candidates(condor, open_pos) == []


def test_different_sizes_still_count_as_one_condor():
    """Lopsided, not separate. The form warns; the detection must still pair
    them, or she cannot record what she actually filled."""
    small = _spread("T-SMALL", date(2026, 9, 17), "put", 215, 200, 215.0,
                    contracts=1)
    open_pos = _open([CALL_WING, small])
    call = next(p for p in open_pos if p.trade_id == "T-CALL")
    assert [m.trade_id for m in wings.merge_candidates(call, open_pos)] == ["T-SMALL"]


# ------------------------------------------------- what the broker holds
def test_the_condor_is_priced_off_the_WIDER_wing():
    """Her ask, 2026-09-18. CRWD's call wing is 10 wide and its put wing 15;
    the position was carrying the 10-wide figure because that wing was logged
    first, understating the risk by a third. Price can only breach one side, so
    the risk is the wider wing less everything collected on both."""
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-CALL"]
    # wider wing 15 x 100 x 2 contracts = 3,000
    assert p.buying_power == 3000.0        # what the broker holds, gross
    assert p.max_loss == 2298.0            # less the 702 collected on both


def test_the_absorbed_wings_risk_stops_counting_separately():
    """Otherwise the double count she asked to be rid of just moves off the
    cards and into the monthly buying-power budget."""
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE])
    absorbed = next(p for p in positions if p.trade_id == "T-PUT")
    assert absorbed.buying_power == 0.0 and absorbed.max_loss == 0.0
    assert pos_mod.bp_in_use(positions) == 3000.0


def test_both_credits_count_against_the_risk():
    """open_credit is only the wing logged first. Using it alone would
    overstate the risk by the whole second credit."""
    p = _by_id([CALL_WING, PUT_WING, MERGE])["T-CALL"]
    assert p.open_credit == 272.0          # unchanged - it is a month figure
    assert p.max_loss == 3000.0 - 702.0    # but BOTH credits reduce the risk


def test_a_matched_condor_is_priced_the_same_either_way():
    """A condor whose wings are equal must not change value by being merged."""
    even_put = _spread("T-EVEN", date(2026, 9, 17), "put", 215, 205, 430.0)
    merge = build_merge_row("T-EVEN", "T-CALL", "CRWD", "x",
                            merged_on=date(2026, 9, 18), account="real")
    p = _by_id([CALL_WING, even_put, merge])["T-CALL"]
    assert p.max_loss == 10 * 100 * 2 - 702.0


def test_a_lopsided_condor_is_priced_off_the_side_that_can_hurt_her():
    wide = _spread("T-WIDE", date(2026, 9, 17), "put", 215, 165, 430.0)
    merge = build_merge_row("T-WIDE", "T-CALL", "CRWD", "x",
                            merged_on=date(2026, 9, 18), account="real")
    p = _by_id([CALL_WING, wide, merge])["T-CALL"]
    assert p.max_loss == 50 * 100 * 2 - 702.0      # the 50-wide put wing


def test_a_plain_spread_is_priced_the_same_way():
    """The gross/net split is not a condor rule - it is how her broker works on
    every defined-risk trade, so a one-sided spread reads the same way."""
    p = _by_id([CALL_WING])["T-CALL"]
    assert p.buying_power == 10 * 100 * 2          # the whole width
    assert p.max_loss == 10 * 100 * 2 - 272.0      # less the credit


# ------------------------------ corrections that must survive the replay
def test_a_roll_puts_back_the_size_it_took_off():
    """Her paper SMH is short TWO 615 calls against one LEAPS. Buying the call
    back drops the leg, and the roll that writes the next one used to re-add it
    with a hardcoded quantity of 1 - silently turning a ratio into a single
    every time that side was rolled, and wiping any correction she had made."""
    from datetime import timedelta
    from src.engine.models import Trade
    from src.logging_tools.row import build_roll_row

    opened = date(2026, 7, 21)
    legs = [
        Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
            strike=460.0, premium=180.0, dte=331),
        Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
            strike=615.0, premium=4.6, dte=87, quantity=2),
    ]
    t = Trade(strategy_key="poor_mans_covered_call", underlying="SMH",
              legs=legs, contracts=1, underlying_price=563.0)
    row = build_row(t, "Poor Man's Covered Call (PMCC)",
                    {"credit": 460.0, "account": "paper"}, True, "",
                    trade_id="SMH1", opened_on=opened)
    # bought back with nothing written, then a new call sold a week later
    back = build_roll_row("SMH1", "SMH", "PMCC", -300.0,
                          rolled_on=opened + timedelta(days=7), account="paper")
    again = build_roll_row("SMH1", "SMH", "PMCC", 470.0, new_strike=615.0,
                           new_expiration=opened + timedelta(days=94),
                           new_credit=470.0,
                           rolled_on=opened + timedelta(days=8), account="paper")

    p = parse_rows(COLUMNS, [row, back, again])[0]
    short = next(l for l in p.legs if l.role == "short_call")
    assert short.quantity == 2, "the ratio collapsed to a single on the roll"


def test_a_typed_bp_effect_can_be_corrected_after_the_fact():
    """Her standing ruling is that TOS is right. That has to be sayable about a
    trade already in the log, not only when it is first written - the app
    cannot derive the margin on SMH's uncovered call, so her broker's number is
    the only honest source."""
    from src.logging_tools.row import build_edit_row

    edit = build_edit_row("T-CALL", "CRWD", "Call Credit Spread",
                          {"bp_effect": 6563.5}, edited_on=date(2026, 9, 18))
    p = _by_id([CALL_WING, edit])["T-CALL"]
    assert p.bp_effect == 6563.5


def test_one_malformed_edit_does_not_take_the_whole_log_down():
    """A legs block written as a JSON string rather than a list used to raise
    out of parse_rows, so ONE bad row made every trade in her sheet
    unreadable."""
    import json as _json
    from src.logging_tools.row import build_edit_row

    as_string = build_edit_row(
        "T-CALL", "CRWD", "Call Credit Spread",
        {"legs": _json.dumps([{"role": "short_call", "action": "sell",
                               "type": "call", "strike": 240.0, "qty": 5}])},
        edited_on=date(2026, 9, 18))
    p = _by_id([CALL_WING, as_string])["T-CALL"]
    assert [l.quantity for l in p.legs] == [5]      # parsed, not crashed

    junk = build_edit_row("T-CALL", "CRWD", "Call Credit Spread",
                          {"legs": ["not-a-leg", 42]}, edited_on=date(2026, 9, 18))
    assert parse_rows(COLUMNS, [CALL_WING, junk])    # still reads
