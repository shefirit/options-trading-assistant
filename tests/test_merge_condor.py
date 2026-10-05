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


# ------------------------------------------- taking part of a leg off
def _pmcc_rows(*extra):
    """A LEAPS with TWO short calls against it - one covered, one naked."""
    from src.engine.models import Trade
    legs = [
        Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
            strike=460.0, premium=180.0, dte=331),
        Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
            strike=615.0, premium=4.6, dte=28, quantity=2),
    ]
    t = Trade(strategy_key="poor_mans_covered_call", underlying="SMH",
              legs=legs, contracts=1, underlying_price=564.0)
    return [build_row(t, "Poor Man's Covered Call (PMCC)",
                      {"credit": 460.0, "account": "paper"}, True, "",
                      trade_id="SMH1", opened_on=date(2026, 7, 21)), *extra]


def test_closing_one_of_two_short_calls_leaves_the_other():
    """Her ask: close the NAKED call. One of the two is covered by the LEAPS and
    stays; recording the whole leg would report her out of a position she is
    still in."""
    from src.logging_tools.row import build_leg_close_row

    partial = build_leg_close_row(
        "SMH1", "SMH", "PMCC", cash=-458.0, strike=615.0, option_type="call",
        side="sell", quantity=1, closed_on=date(2026, 9, 18), account="paper")
    p = parse_rows(COLUMNS, _pmcc_rows(partial))[0]
    short = next(l for l in p.legs if l.role == "short_call")
    assert short.quantity == 1
    assert not p.is_uncovered            # the LEAPS still has a call on it


def test_closing_the_whole_leg_still_removes_it():
    """No quantity means the whole leg, which is what every legclose written
    before partials existed meant."""
    from src.logging_tools.row import build_leg_close_row

    whole = build_leg_close_row(
        "SMH1", "SMH", "PMCC", cash=-916.0, strike=615.0, option_type="call",
        side="sell", closed_on=date(2026, 9, 18), account="paper")
    p = parse_rows(COLUMNS, _pmcc_rows(whole))[0]
    assert not [l for l in p.legs if l.role == "short_call"]


def test_taking_more_than_is_there_removes_the_leg():
    """A typo must not leave a leg with a negative or zero size sitting in the
    position for the pricer to trip over."""
    from src.logging_tools.row import build_leg_close_row

    toomany = build_leg_close_row(
        "SMH1", "SMH", "PMCC", cash=-1400.0, strike=615.0, option_type="call",
        side="sell", quantity=9, closed_on=date(2026, 9, 18), account="paper")
    p = parse_rows(COLUMNS, _pmcc_rows(toomany))[0]
    assert not [l for l in p.legs if l.role == "short_call"]


def test_the_cost_of_buying_it_back_is_banked_on_the_day():
    from src.logging_tools.row import build_leg_close_row

    partial = build_leg_close_row(
        "SMH1", "SMH", "PMCC", cash=-458.0, strike=615.0, option_type="call",
        side="sell", quantity=1, closed_on=date(2026, 9, 18), account="paper")
    p = parse_rows(COLUMNS, _pmcc_rows(partial))[0]
    assert [(e.cash, e.quantity) for e in p.leg_closes] == [(-458.0, 1)]


# ------------------------------------------------- the condor closes as a whole
# Her report, 2026-09-30: an SPX call wing merged on 18 September still showed
# as an open trade on expiration day, flagged "Take the win", though she had
# bought the whole condor back on the 23rd. Merges ran after closes and refused
# a target that was no longer open, so the wing never joined.
from src.logging_tools.row import build_close_row  # noqa: E402

CONDOR_CLOSE = build_close_row("T-CALL", "CRWD",
                               "Call Credit Spread (Bear Call Spread)",
                               exit_cost=500.0, realized_pl=202.0,
                               reason="Closed early",
                               closed_on=date(2026, 9, 23), account="real")


def test_closing_the_condor_closes_the_merged_wing_too():
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE, CONDOR_CLOSE])
    by_id = {p.trade_id: p for p in positions}
    assert pos_mod.open_positions(positions) == []
    assert by_id["T-PUT"].status == "merged"
    assert by_id["T-CALL"].status == "closed"
    assert by_id["T-CALL"].is_iron_condor_shape


def test_the_merged_wing_is_not_counted_twice_once_closed():
    """One closed trade in the journal, carrying the condor's one result."""
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, MERGE, CONDOR_CLOSE])
    closed = pos_mod.closed_positions(positions)
    assert [p.trade_id for p in closed] == ["T-CALL"]
    assert closed[0].realized_total == 202.0


def test_a_merge_written_after_the_close_is_refused():
    """That wing was never part of the trade she ended - it stays its own."""
    late = build_merge_row("T-PUT", "T-CALL", "CRWD",
                           "Put Credit Spread (Bull Put Spread)",
                           merged_on=date(2026, 9, 25), account="real")
    positions = parse_rows(COLUMNS, [CALL_WING, PUT_WING, CONDOR_CLOSE, late])
    assert [p.trade_id for p in pos_mod.open_positions(positions)] == ["T-PUT"]


# ------------------------------------------------- a roll after the merge
# Her CRWD condor, 2026-10-05: the put side rolled on 28 September, after the
# wings were merged on the 18th, showed as TWO put spreads - the closed one and
# the new one. Merges ran after rolls, so the roll found no put on the call
# trade, wrote a fresh one, and the merge then added the old wing beside it.
from src.logging_tools.row import build_roll_row  # noqa: E402


def _put_roll(on, short, long_, expiration=EXP, cash=274.0, credit=342.0):
    return build_roll_row("T-CALL", "CRWD",
                          "Call Credit Spread (Bear Call Spread)", cash,
                          new_strike=short, new_expiration=expiration,
                          new_credit=credit, rolled_on=on, account="real",
                          option_type="put", new_long_strike=long_)


def _strikes(p, kind):
    return sorted(l.strike for l in p.legs if l.option_type is kind)


def test_a_roll_after_the_merge_moves_the_merged_wing():
    rows = [CALL_WING, PUT_WING, MERGE, _put_roll(date(2026, 9, 28), 230, 210)]
    p = _by_id(rows)["T-CALL"]
    assert _strikes(p, OptionType.PUT) == [210, 230]
    assert _strikes(p, OptionType.CALL) == [240, 250]
    assert p.is_iron_condor_shape


def test_a_second_roll_to_a_later_date_moves_the_legs_there():
    """Both put legs land on the new date; the calls stay where they were."""
    later = date(2026, 10, 23)
    rows = [CALL_WING, PUT_WING, MERGE,
            _put_roll(date(2026, 9, 28), 230, 210),
            _put_roll(date(2026, 10, 5), 260, 250, expiration=later,
                      cash=498.69, credit=564.0)]
    p = _by_id(rows)["T-CALL"]
    assert _strikes(p, OptionType.PUT) == [250, 260]
    for leg in p.legs:
        want = later if leg.option_type is OptionType.PUT else EXP
        assert p.leg_expiration(leg) == want
    assert p.banked_income == pytest.approx(274.0 + 498.69)


def test_a_roll_before_the_merge_still_happens_first():
    """A call roll dated before the merge is the call trade's own history."""
    call_roll = build_roll_row("T-CALL", "CRWD",
                               "Call Credit Spread (Bear Call Spread)", 50.0,
                               new_strike=245, new_expiration=EXP,
                               new_credit=200.0, rolled_on=date(2026, 9, 10),
                               account="real", option_type="call",
                               new_long_strike=255)
    p = _by_id([CALL_WING, call_roll, PUT_WING, MERGE])["T-CALL"]
    assert _strikes(p, OptionType.CALL) == [245, 255]
    assert _strikes(p, OptionType.PUT) == [200, 215]


def test_a_rolled_leg_that_carried_its_date_takes_the_new_one():
    """leg_expiration() reads the stored date first, so a roll has to move it."""
    later = date(2026, 10, 23)
    rows = [PUT_WING, build_roll_row(
        "T-PUT", "CRWD", "Put Credit Spread (Bull Put Spread)", 100.0,
        new_strike=210, new_expiration=later, new_credit=300.0,
        rolled_on=date(2026, 9, 28), account="real", option_type="put",
        new_long_strike=200)]
    p = _by_id(rows)["T-PUT"]
    assert all(p.leg_expiration(l) == later for l in p.legs)


# ------------------------------------- wings on two dates, wings that overlap
# Her CRWD condor after the 2026-10-05 put roll: the calls still on 16 October,
# the puts moved to the 23rd and up to 260/250 - above the 240 short call. The
# app measured risk at the near date only, so it never saw a condor: max loss
# stayed at the 3,024 left by the first roll, the card ran call-spread rules,
# and the countdown followed the puts while the calls expired a week earlier.
LATER = date(2026, 10, 23)


def _crwd_today():
    return [CALL_WING, PUT_WING, MERGE,
            _put_roll(date(2026, 9, 28), 230, 210),
            _put_roll(date(2026, 10, 5), 260, 250, expiration=LATER,
                      cash=498.69, credit=564.0)]


def test_wings_on_two_dates_are_still_a_condor():
    p = _by_id(_crwd_today())["T-CALL"]
    assert p.is_iron_condor_shape
    assert p.effective_strategy_key == "iron_condor"


def test_wings_on_two_dates_can_each_lose_their_whole_width():
    """The calls can expire at full loss on the 16th and price can still fall
    through the puts by the 23rd - so both widths, less all 1,474.69 collected.
    The gross is what thinkorswim holds: two verticals, 4,000."""
    p = _by_id(_crwd_today())["T-CALL"]
    assert p.buying_power == 4000.0
    assert p.max_loss == pytest.approx(4000.0 - 1474.69)


def test_split_dates_add_up_even_when_the_wings_do_not_overlap():
    rows = [CALL_WING, PUT_WING, MERGE,
            _put_roll(date(2026, 10, 5), 215, 200, expiration=LATER)]
    p = _by_id(rows)["T-CALL"]
    assert p.buying_power == (10 + 15) * 100 * 2


def test_an_inverted_condor_on_one_date_can_lose_both_widths():
    """Short put above the short call: at 250 both wings are in
    the money together, so the wider-wing rule would understate it by half."""
    inv = _spread("T-INV", date(2026, 9, 17), "put", 260, 250, 430.0)
    merge = build_merge_row("T-INV", "T-CALL", "CRWD", "x",
                            merged_on=date(2026, 9, 18), account="real")
    p = _by_id([CALL_WING, inv, merge])["T-CALL"]
    assert p.is_iron_condor_shape
    assert p.buying_power == 4000.0
    assert p.max_loss == 4000.0 - 702.0


def test_overlapping_wings_lose_more_than_one_width_but_less_than_two():
    """Puts 255/245 against calls 240/250: at 245 the put wing is lost and the
    call wing half lost - 15 points, not 10 and not 20."""
    lap = _spread("T-LAP", date(2026, 9, 17), "put", 255, 245, 430.0)
    merge = build_merge_row("T-LAP", "T-CALL", "CRWD", "x",
                            merged_on=date(2026, 9, 18), account="real")
    p = _by_id([CALL_WING, lap, merge])["T-CALL"]
    assert p.buying_power == 15 * 100 * 2


def test_the_countdown_follows_the_wing_that_expires_first():
    p = _by_id(_crwd_today())["T-CALL"]
    assert p.expiration == EXP
    assert p.dte_left(date(2026, 10, 5)) == 11


def test_once_the_near_wing_is_off_the_countdown_moves_to_the_other():
    from src.logging_tools.row import build_leg_close_row

    off = [build_leg_close_row("T-CALL", "CRWD", "Call Credit Spread", cash=c,
                               strike=k, option_type="call", side=s,
                               closed_on=EXP, account="real")
           for k, s, c in ((240, "sell", 0.0), (250, "buy", 0.0))]
    p = _by_id(_crwd_today() + off)["T-CALL"]
    assert _strikes(p, OptionType.CALL) == []
    assert p.expiration == LATER


def test_a_plain_spread_rolled_out_counts_down_to_the_new_date():
    rows = [PUT_WING, build_roll_row(
        "T-PUT", "CRWD", "Put Credit Spread (Bull Put Spread)", 100.0,
        new_strike=210, new_expiration=LATER, new_credit=300.0,
        rolled_on=date(2026, 9, 28), account="real", option_type="put",
        new_long_strike=200)]
    assert _by_id(rows)["T-PUT"].expiration == LATER


def test_a_pmcc_roll_still_counts_down_to_the_new_short_call():
    """The LEAPS is the far leg and the short call the near one, so the roll's
    own date is still the answer - and a PMCC is never a condor."""
    new_exp = date(2026, 10, 30)
    roll = build_roll_row("SMH1", "SMH", "PMCC", 120.0, new_strike=630.0,
                          new_expiration=new_exp, new_credit=400.0,
                          rolled_on=date(2026, 9, 18), account="paper")
    p = parse_rows(COLUMNS, _pmcc_rows(roll))[0]
    assert p.expiration == new_exp
    assert not p.is_iron_condor_shape


# ---------------------------------------- live exit checks on the split condor
# The same CRWD condor, priced. Three things were wrong on 2026-10-05: only
# the call wing was priced (the near-dte legs), the chain fetched carried only
# one expiration, and the credit the 50% target and stop measure against was
# the put wing's new 564 alone - the 272 call wing had dropped out of it.
from src.data.chain import OptionChain, OptionContract  # noqa: E402
from src.engine import exit_rules  # noqa: E402
from src.engine.positions import cost_to_close_from_chain  # noqa: E402


def _quote(kind, strike, exp, mid, delta=0.2):
    return OptionContract(option_type=kind, strike=strike,
                          expiration=exp.isoformat(), dte=0,
                          delta=delta if kind is OptionType.CALL else -delta,
                          bid=mid, ask=mid)


# The right contracts, plus the same strikes on the OTHER week at prices that
# would give a different answer if a leg were matched to the wrong date.
CRWD_CHAIN = [
    _quote(OptionType.CALL, 240, EXP, 6.0, delta=0.35),
    _quote(OptionType.CALL, 250, EXP, 2.0),
    _quote(OptionType.PUT, 260, LATER, 9.0, delta=0.6),
    _quote(OptionType.PUT, 250, LATER, 4.0),
    _quote(OptionType.CALL, 240, LATER, 7.5),
    _quote(OptionType.CALL, 250, LATER, 3.5),
    _quote(OptionType.PUT, 260, EXP, 8.0),
    _quote(OptionType.PUT, 250, EXP, 2.5),
]


def _chain(contracts):
    return OptionChain(underlying="CRWD", underlying_price=255.0,
                       contracts=list(contracts))


def test_a_put_roll_keeps_the_call_wings_credit_in_the_basis():
    """272 for the untouched calls plus the puts' newest 564 - not 564 alone."""
    p = _by_id(_crwd_today())["T-CALL"]
    assert p.credit == 272.0 + 564.0
    assert p.wing_credit == {"call": 272.0, "put": 564.0}


def test_each_roll_replaces_only_its_own_wings_part():
    rows = [CALL_WING, PUT_WING, MERGE, _put_roll(date(2026, 9, 28), 230, 210)]
    assert _by_id(rows)["T-CALL"].credit == 272.0 + 342.0


def test_rolling_the_call_wing_keeps_the_put_wings_credit():
    call_roll = build_roll_row("T-CALL", "CRWD",
                               "Call Credit Spread (Bear Call Spread)", 40.0,
                               new_strike=245, new_expiration=LATER,
                               new_credit=310.0, rolled_on=date(2026, 9, 25),
                               account="real", option_type="call",
                               new_long_strike=255)
    p = _by_id([CALL_WING, PUT_WING, MERGE, call_roll])["T-CALL"]
    assert p.credit == 430.0 + 310.0


def test_a_plain_spread_roll_still_replaces_the_credit():
    rows = [PUT_WING, build_roll_row(
        "T-PUT", "CRWD", "Put Credit Spread (Bull Put Spread)", 100.0,
        new_strike=210, new_expiration=LATER, new_credit=300.0,
        rolled_on=date(2026, 9, 28), account="real", option_type="put",
        new_long_strike=200)]
    assert _by_id(rows)["T-PUT"].credit == 300.0


def test_a_condor_logged_as_one_row_splits_its_credit_by_the_fills():
    """No wing was ever added, so nothing recorded the split. The fills do:
    puts netted 0.90 and calls 0.60, so the puts carry 60% of the 300."""
    legs = [
        Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
            strike=215, premium=1.5, dte=44, expiration=EXP),
        Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
            strike=200, premium=0.6, dte=44, expiration=EXP),
        Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
            strike=240, premium=1.0, dte=44, expiration=EXP),
        Leg(role="long_call", action=Action.BUY, option_type=OptionType.CALL,
            strike=250, premium=0.4, dte=44, expiration=EXP),
    ]
    t = Trade(strategy_key="iron_condor", underlying="CRWD", legs=legs,
              contracts=2, underlying_price=230.0)
    row = build_row(t, "Iron Condor", {"credit": 300.0, "open_cash": 300.0,
                                       "account": "real"},
                    True, "", trade_id="T-IC", opened_on=date(2026, 9, 2))
    roll = build_roll_row("T-IC", "CRWD", "Iron Condor", 50.0,
                          new_strike=210, new_expiration=LATER,
                          new_credit=200.0, rolled_on=date(2026, 9, 28),
                          account="real", option_type="put",
                          new_long_strike=195)
    p = _by_id([row, roll])["T-IC"]
    assert p.credit == pytest.approx(120.0 + 200.0)


def test_the_split_condor_spans_both_dates():
    assert _by_id(_crwd_today())["T-CALL"].split_expirations == [EXP, LATER]


def test_both_wings_are_priced_each_on_its_own_date():
    """Calls 6.00 - 2.00 on the 16th, puts 9.00 - 4.00 on the 23rd: 9.00 a
    share, x100 x2 contracts. Pricing the calls alone said 800."""
    p = _by_id(_crwd_today())["T-CALL"]
    out = cost_to_close_from_chain(p, _chain(CRWD_CHAIN))
    assert out["cost_to_close"] == 1800.0
    assert out["short_delta"] == 0.6


def test_a_wing_missing_from_its_week_is_not_priced_off_the_other_week():
    """The 16th is exactly seven days from the 23rd - inside the matcher's
    tolerance - so without the guard the puts would quietly take the 16th's
    quotes. Unpriced is the honest answer."""
    p = _by_id(_crwd_today())["T-CALL"]
    no_later_puts = [c for c in CRWD_CHAIN
                     if not (c.option_type is OptionType.PUT
                             and c.expiration == LATER.isoformat())]
    assert cost_to_close_from_chain(p, _chain(no_later_puts)) is None


def test_the_profit_target_measures_against_both_wings():
    """836 collected and 400 to close is 52% kept - the take-the-win line.
    Against the 564 alone it read 29% and said hold."""
    p = _by_id(_crwd_today())["T-CALL"]
    sig = exit_rules.evaluate(p, {"profit_target_pct": 50,
                                  "stop_loss_multiple": 2},
                              current_cost=400.0, today=date(2026, 10, 5))
    assert sig.action == "profit"
    assert sig.profit_pct == pytest.approx((836.0 - 400.0) / 836.0 * 100)


def test_the_stop_measures_against_both_wings():
    """2x of 836 is a 1,672 loss, so the stop sits at 2,508 to close - against
    564 alone it fired at 1,692."""
    p = _by_id(_crwd_today())["T-CALL"]
    cfg = {"stop_loss_multiple": 2}
    assert exit_rules.evaluate(p, cfg, current_cost=2400.0,
                               today=date(2026, 10, 5)).action != "stop"
    assert exit_rules.evaluate(p, cfg, current_cost=2508.0,
                               today=date(2026, 10, 5)).action == "stop"


def test_price_position_fetches_each_wing_date_and_merges_them(monkeypatch):
    """The provider used to fetch ONE expiration - the near one - so the put
    wing's contracts were never in the chain at all."""
    from datetime import timedelta

    from src.data import cache, yfinance_client
    from src.data.provider import DataProvider
    from src.engine.positions import Position

    today = date.today()
    near, far = today + timedelta(days=11), today + timedelta(days=18)
    p = Position(
        trade_id="SPLIT", underlying="ZZSPLIT",
        opened=today - timedelta(days=33), expiration=near, contracts=2,
        credit=836.0, open_cash=702.0,
        legs=[
            Leg(role="short_call", action=Action.SELL,
                option_type=OptionType.CALL, strike=240, expiration=near),
            Leg(role="long_call", action=Action.BUY,
                option_type=OptionType.CALL, strike=250, expiration=near),
            Leg(role="short_put", action=Action.SELL,
                option_type=OptionType.PUT, strike=260, expiration=far),
            Leg(role="long_put", action=Action.BUY,
                option_type=OptionType.PUT, strike=250, expiration=far),
        ])
    # Each fetch returns ONE expiration, as _expiration_chain really does.
    by_date = {near: EXP, far: LATER}
    asked = []

    def one_expiration(sym, target_dte, tradable=False):
        d = today + timedelta(days=target_dte)
        asked.append(d)
        return _chain(c.model_copy(update={"expiration": d.isoformat()})
                      for c in CRWD_CHAIN
                      if c.expiration == by_date[d].isoformat())

    provider = DataProvider("yahoo")
    monkeypatch.setattr(provider, "_expiration_chain", one_expiration)
    monkeypatch.setattr(yfinance_client, "get_price", lambda s: 255.0)
    for d in (near, far):
        cache.clear(f"poschain:ZZSPLIT:{d.isoformat()}")
    cache.clear("px:ZZSPLIT")

    out = provider.price_position(p)
    assert sorted(asked) == [near, far]
    assert out["priced"] is True
    assert out["cost_to_close"] == 1800.0


# ------------------------------------------- what the screens call it, and its story
# Her report, 2026-10-05: "the hosted app doesn't show that it's iron condor and
# journal doesn't follow the changes". Every screen printed the name the first
# wing was logged under, and the story put the put side's credit on day one.
def test_a_merged_condor_is_shown_as_an_iron_condor():
    by_id = _by_id([CALL_WING, PUT_WING, MERGE])
    assert by_id["T-CALL"].shown_strategy_name == "Iron Condor"
    assert by_id["T-CALL"].strategy_name.startswith("Call Credit Spread")


def test_a_plain_spread_keeps_its_own_name():
    p = _by_id([CALL_WING])["T-CALL"]
    assert p.shown_strategy_name == p.strategy_name


def test_the_story_tells_the_added_wing_on_its_own_day():
    rows = [CALL_WING, PUT_WING, MERGE, _put_roll(date(2026, 9, 28), 230, 210)]
    steps = pos_mod.story(_by_id(rows)["T-CALL"])
    assert [s["kind"] for s in steps] == ["open", "wing", "roll"]
    assert steps[0]["cash"] == 272.0
    assert steps[1]["on"] == date(2026, 9, 17)
    assert steps[1]["cash"] == 430.0
    assert "215 put" in steps[1]["detail"]
    assert steps[-1]["running"] == pytest.approx(272.0 + 430.0 + 274.0)
