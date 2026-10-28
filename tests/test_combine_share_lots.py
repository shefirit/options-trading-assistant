"""Two trades holding the same shares, combined so one call covers them all.

Rita's case (2026-10-27): 200 shares in thinkorswim - 100 from an assigned put
and 100 from another trade - with a 2-contract call sold against all of them.
The app had two trades of 100 shares each, and a call can only be recorded on
one trade, so the 2-contract call had nowhere to go.

Invented numbers throughout (this repo is public): a 50 put sold for $150 and
assigned, plus 100 shares bought outright at 54. Average 52.
"""

import json
from datetime import date, timedelta

from src.engine import month_report, positions, wheel
from src.logging_tools import twa_tracker
from src.logging_tools.row import (COLUMNS, build_assign_row, build_merge_row,
                                   build_roll_row)

WHEEL = "The Wheel (CSP into Covered Call)"
BUYWRITE = "Buy / Write"
PUT_LOT = "20260901-090000-XYZ"
BOUGHT_LOT = "20260910-090000-XYZ"
TODAY = date.today()
START = TODAY - timedelta(days=90)


def _row(day, name, trade_id, details, contracts=1, credit=0.0, risk=0.0,
         strike="", account="real"):
    return [day.isoformat(), "XYZ", name, strike, "", 35, contracts, credit,
            risk, risk, "yes", "", trade_id, "open", "", "", "",
            json.dumps(details), account]


def _put_lot(account="real"):
    """A 50 put sold for $150, assigned 20 days ago."""
    details = {"key": "wheel", "underlying_price": 52.0,
               "legs": [{"role": "short_put", "action": "sell", "type": "put",
                         "strike": 50.0, "delta": 0.28, "premium": 1.5,
                         "qty": 1, "dte": 35}],
               "open_cash": 150.0}
    return [_row(TODAY - timedelta(days=50), WHEEL, PUT_LOT, details,
                 credit=150.0, risk=4850.0, strike="50", account=account),
            build_assign_row(PUT_LOT, "XYZ", WHEEL, 50.0, 1,
                             assigned_on=TODAY - timedelta(days=20),
                             account=account)]


def _bought_lot(account="real"):
    """100 shares bought outright at 54, no call yet."""
    details = {"key": "weekly_covered_call", "underlying_price": 54.0,
               "legs": [], "open_cash": -5400.0, "shares_cost": 5400.0}
    return [_row(TODAY - timedelta(days=10), BUYWRITE, BOUGHT_LOT, details,
                 risk=5400.0, account=account)]


def _merge(on=TODAY):
    return build_merge_row(PUT_LOT, BOUGHT_LOT, "XYZ", WHEEL, merged_on=on,
                           note="Shares combined", account="real")


def _call(trade_id, name, credit=200.0, on=TODAY):
    """One call written against the trade - 2 contracts once combined."""
    return build_roll_row(trade_id, "XYZ", name, credit, new_strike=56.0,
                          new_expiration=TODAY + timedelta(days=10),
                          new_credit=credit, rolled_on=on, account="real")


def _parse(*rows):
    return {p.trade_id: p for p in positions.parse_rows(COLUMNS, list(rows))}


def _combined():
    return _parse(*_put_lot(), *_bought_lot(), _merge(),
                  _call(BOUGHT_LOT, BUYWRITE))


# -------------------------------------------------------------- the position
def test_two_lots_become_one_trade_of_200_shares():
    book = _combined()
    keep, gone = book[BOUGHT_LOT], book[PUT_LOT]
    assert keep.status == "open"
    assert gone.status == "merged" and gone.merged_into == BOUGHT_LOT
    assert keep.contracts == 2
    assert keep.shares_cost == 10400.0
    # Both opening ledgers: -5,400 for the bought shares, +150 - 5,000 for
    # the put and its assignment.
    assert keep.open_cash == -10250.0


def test_the_call_written_after_combining_covers_every_share():
    keep = _combined()[BOUGHT_LOT]
    calls = [l for l in keep.legs if l.option_type.value == "call"]
    assert len(calls) == 1
    assert calls[0].quantity * keep.contracts == 2


def test_cost_basis_is_the_average_less_all_the_premium():
    """thinkorswim shows 52 a share. The app takes the premium off that: the
    put's $150 on the other lot and the $200 call, over 200 shares."""
    s = wheel.state_from(_combined()[BOUGHT_LOT], market_price=53.0)
    assert s.shares == 200
    assert s.paid_per_share == 52.0
    assert s.premium_collected == 350.0
    assert s.cost_basis == 50.25
    assert s.unrealised == 550.0


def test_combining_into_an_assigned_lot_uses_the_average_too():
    book = _parse(*_put_lot(), *_bought_lot(),
                  build_merge_row(BOUGHT_LOT, PUT_LOT, "XYZ", BUYWRITE,
                                  merged_on=TODAY, account="real"))
    s = wheel.state_from(book[PUT_LOT])
    assert book[BOUGHT_LOT].status == "merged"
    assert s.shares == 200 and s.paid_per_share == 52.0
    assert s.premium_collected == 150.0


def test_risk_moves_to_the_trade_that_stays():
    book = _combined()
    assert book[PUT_LOT].buying_power == 0.0
    assert book[BOUGHT_LOT].buying_power == 4850.0 + 5400.0


def test_a_lot_with_a_call_open_is_not_combined():
    """A call written against 100 shares would turn into a call against 200
    the moment the lots joined - a fill she never made."""
    book = _parse(*_put_lot(), *_bought_lot(),
                  _call(BOUGHT_LOT, BUYWRITE, on=TODAY - timedelta(days=2)),
                  _merge())
    assert book[PUT_LOT].status == "open"
    assert book[BOUGHT_LOT].contracts == 1


def test_deleting_the_merge_row_splits_them_again():
    book = _parse(*_put_lot(), *_bought_lot())
    assert book[PUT_LOT].status == "open" and book[BOUGHT_LOT].contracts == 1


# ------------------------------------------------------------------ the money
def test_the_story_adds_up_to_the_ledger():
    keep = _combined()[BOUGHT_LOT]
    steps = positions.story(keep)
    merge = [s for s in steps if s["kind"] == "merge"]
    assert len(merge) == 1 and merge[0]["cash"] == -4850.0
    assert "100 more shares" in merge[0]["what"]
    opening = next(s for s in steps if s["kind"] == "open")
    assert opening["cash"] == -5400.0, "day one bought 100 shares, not 200"
    assert steps[-1]["running"] == round(keep.open_cash + keep.banked_income, 2)


def test_premium_is_counted_once_in_the_month_report():
    book = _combined()
    events = month_report.premium_events(list(book.values()),
                                         month_report.ALL_TIME)
    assert sorted(e["amount"] for e in events) == [150.0, 200.0]


def test_candidates_are_same_symbol_same_book_and_uncovered():
    book = _parse(*_put_lot(), *_bought_lot())
    keep, other = book[BOUGHT_LOT], book[PUT_LOT]
    assert wheel.share_lot_candidates(keep, [keep, other]) == [other]

    paper = _parse(*_put_lot(account="paper"), *_bought_lot())
    assert wheel.share_lot_candidates(paper[BOUGHT_LOT],
                                      list(paper.values())) == []

    covered = _parse(*_put_lot(), *_bought_lot(), _call(PUT_LOT, WHEEL))
    assert wheel.share_lot_candidates(covered[BOUGHT_LOT],
                                      list(covered.values())) == []


# ---------------------------------------------------------- the TWA tracker
def _cells(p):
    rows, why = twa_tracker.rows_for(p, START)
    assert why == ""
    return [r["cells"] for r in rows]


def test_tracker_shows_one_row_of_200_shares_at_the_average():
    keep = _combined()[BOUGHT_LOT]
    rows = _cells(keep)
    stock = [r for r in rows if r["Strategy"] == "STOCK"]
    assert len(stock) == 1
    assert stock[0]["Quantity"] == 2 and stock[0]["Premium $"] == 52.0
    cc = [r for r in rows if r["Strategy"] == "CC"]
    assert len(cc) == 1
    assert cc[0]["Quantity"] == 2
    assert cc[0]["Covered Call Collateral"] == 10400.0


def test_tracker_dates_a_later_call_to_the_day_it_was_sold():
    """Shares bought on their own, call sold days later. The tracker used to
    put the call on the day the shares were bought and 'roll' it to itself."""
    p = _parse(*_bought_lot(), _call(BOUGHT_LOT, BUYWRITE))[BOUGHT_LOT]
    cc = [r for r in _cells(p) if r["Strategy"] == "CC"]
    assert len(cc) == 1
    assert cc[0]["Open Date"] == TODAY.isoformat()
    assert cc[0]["Premium $"] == 2.0
    assert "Rolled" not in cc[0]["Notes"]


def test_tracker_closes_the_folded_lot_at_cost_so_nothing_counts_twice():
    gone = _combined()[PUT_LOT]
    rows = _cells(gone)
    put = next(r for r in rows if r["Strategy"] == "PUT")
    assert put["Premium $"] == 1.5, "the put's premium stays on the tracker"
    stock = next(r for r in rows if r["Strategy"] == "STOCK")
    assert stock["Closed Date"] == TODAY.isoformat()
    assert stock["Close $"] == stock["Premium $"] == 50.0
    assert BOUGHT_LOT in stock["Notes"]


# ------------------------------------------------- the Journal, at today's price
def test_journal_shows_what_the_shares_are_worth_today():
    """Rita, 2026-10-28: the Journal showed the premiums and what the stock
    cost - "but what about stock appreciation?" 200 shares paid 52 a share,
    53 today, one call worth $100 to buy back."""
    keep = _combined()
    keep = keep[BOUGHT_LOT]
    live = {"underlying_price": 53.0, "shares_pl": 200.0,
            "open_pl": round(keep.open_cash + keep.banked_income
                             + 53.0 * 200 - 100.0, 2)}
    t = positions.standing_today(keep, live)
    assert t["shares"] == 200 and t["paid_per_share"] == 52.0
    assert t["shares_value"] == 10600.0
    assert t["shares_gain"] == 200.0
    assert t["options_now"] == -100.0
    # Cash so far (-10,050) + shares (10,600) - the call (100).
    assert t["if_closed"] == 450.0


def test_journal_today_lines_match_the_shares_only_pricing():
    """Between calls the shares are the whole position - same arithmetic."""
    p = _parse(*_put_lot(), *_bought_lot(), _merge())[BOUGHT_LOT]
    live = positions.shares_only_value(p, 50.0)
    live["underlying_price"] = 50.0
    t = positions.standing_today(p, live)
    assert t["shares_gain"] == -400.0
    assert t["options_now"] == 0.0
    assert t["if_closed"] == round(p.open_cash + p.banked_income + 10000.0, 2)


def test_no_today_lines_without_shares_or_a_price():
    p = _combined()[BOUGHT_LOT]
    assert positions.standing_today(p, None) is None
    assert positions.standing_today(p, {"underlying_price": None}) is None


def test_day_one_tells_only_what_happened_that_day():
    """TQQQ read "Bought 100 shares, sold the 77.5 call" on a day she only
    bought shares; SOFI read "Bought 100 shares" on the day she sold a put."""
    book = _combined()
    bought = positions.story(book[BOUGHT_LOT])[0]
    assert bought["detail"] == "Bought 100 shares"
    put = positions.story(_parse(*_put_lot())[PUT_LOT])[0]
    assert put["detail"] == "Sold the 50 put"
