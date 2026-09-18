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
    return build_row(t, name, {"credit": credit, "open_cash": credit,
                               "account": account},
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
