"""The trade log -> positions parser, plus the live cost-to-close math."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import (
    Position,
    _to_date,
    bp_in_use,
    bp_committed_this_month,
    closed_positions,
    cost_to_close_from_chain,
    monthly_summary,
    open_positions,
    parse_rows,
    performance,
    strike_cushion,
)
from src.logging_tools.row import (COLUMNS, build_close_row, build_edit_row,
                                   build_reopen_row, build_row)


def _trade() -> Trade:
    return Trade(
        strategy_key="put_credit_spread", underlying="SPX", contracts=1,
        underlying_price=5100.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=5000, delta=-0.20, premium=8.0, dte=30),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=4975, delta=-0.15, premium=5.0, dte=30),
        ],
    )


SIZE = {"credit": 300.0, "max_loss": 2200.0, "buying_power": 2200.0}


def test_open_row_round_trips_into_a_position():
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "note",
                    trade_id="20260705-1-SPX")
    positions = parse_rows(COLUMNS, [row])
    assert len(positions) == 1
    p = positions[0]
    assert p.status == "open"
    assert p.trade_id == "20260705-1-SPX"
    assert p.underlying == "SPX"
    assert p.strategy_key == "put_credit_spread"
    assert p.credit == 300.0
    assert p.buying_power == 2200.0
    assert len(p.legs) == 2 and p.legs[0].strike == 5000
    assert p.expiration == date.today() + timedelta(days=30)
    assert p.dte_left() == 30
    assert p.can_track


def test_close_row_folds_into_the_open_position():
    open_row = build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                         trade_id="T1")
    close_row = build_close_row("T1", "SPX", "Put Credit Spread",
                                exit_cost=150.0, realized_pl=150.0,
                                reason="Profit target (50%) hit")
    positions = parse_rows(COLUMNS, [open_row, close_row])
    assert len(positions) == 1
    p = positions[0]
    assert p.status == "closed"
    assert p.exit_cost == 150.0
    assert p.realized_pl == 150.0
    assert "Profit target" in p.exit_reason
    assert open_positions(positions) == []
    assert closed_positions(positions) == [p]


def test_legacy_and_test_rows():
    legacy = ["2026-06-20", "SPX", "Put Credit Spread", "5000 / 4975",
              0.08, 45, 1, 300, 2200, 2200, "yes", "old row"]
    test_row = ["TEST 2026-07-05", "-", "connection test",
                "-", "-", "-", "-", "-", "-", "-", "-", "delete me"]
    positions = parse_rows(COLUMNS[:12], [legacy, test_row])
    assert len(positions) == 1
    assert positions[0].status == "legacy"
    assert not positions[0].can_track


def test_bp_in_use_counts_only_open_trades():
    rows = [
        build_row(_trade(), "Put Credit Spread", SIZE, True, "", trade_id="A"),
        build_row(_trade(), "Put Credit Spread", SIZE, True, "", trade_id="B"),
        build_close_row("B", "SPX", "Put Credit Spread", 150.0, 150.0, "50%"),
    ]
    positions = parse_rows(COLUMNS, rows)
    assert bp_in_use(positions) == 2200.0


def test_bp_committed_ignores_trades_opened_earlier_months():
    today = date(2026, 7, 18)
    last_month = date(2026, 6, 10)
    rows = [
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="THIS", opened_on=today),
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="OLD", opened_on=last_month),
    ]
    positions = parse_rows(COLUMNS, rows)
    # An open June trade is still capital at work, but it spent JUNE's budget.
    assert bp_in_use(positions) == 4400.0
    assert bp_committed_this_month(positions, today=today) == 2200.0


def test_bp_committed_counts_trades_already_closed_this_month():
    """Rita's ruling: the monthly limit is cumulative deployment, not a snapshot
    of what is tied up right now. Taking a win early frees the risk; it does not
    hand back that slice of the month's budget."""
    today = date(2026, 7, 18)
    rows = [
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="OPEN", opened_on=today),
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="SHUT", opened_on=today),
        build_close_row("SHUT", "SPX", "Put Credit Spread", 150.0, 150.0, "50%"),
    ]
    positions = parse_rows(COLUMNS, rows)
    # One open + one closed, both opened this month = both counted.
    assert bp_committed_this_month(positions, today=today) == 4400.0
    # The still-open figure is a different question and still answers it.
    assert bp_in_use(positions) == 2200.0


def test_bp_committed_matches_the_month_view_tile():
    """The two numbers used to disagree on screen - one counted closed trades,
    the other did not - while both were shown against the same limit."""
    from src.engine.positions import monthly_summary

    today = date(2026, 7, 18)
    rows = [
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="OPEN", opened_on=today),
        build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                  trade_id="SHUT", opened_on=today),
        build_close_row("SHUT", "SPX", "Put Credit Spread", 150.0, 150.0, "50%"),
    ]
    positions = parse_rows(COLUMNS, rows)
    july = next(m for m in monthly_summary(positions, today=today)
                if m["month"] == "2026-07")
    assert july["bp_opened"] == bp_committed_this_month(positions, today=today)


def test_cost_to_close_from_chain():
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "", trade_id="T1")
    p = parse_rows(COLUMNS, [row])[0]
    exp = p.expiration.isoformat()
    chain = OptionChain(underlying="SPX", underlying_price=5080.0, contracts=[
        OptionContract(option_type=OptionType.PUT, strike=5000, expiration=exp,
                       dte=30, delta=-0.10, bid=4.0, ask=4.4),
        OptionContract(option_type=OptionType.PUT, strike=4975, expiration=exp,
                       dte=30, delta=-0.07, bid=2.0, ask=2.2),
    ])
    out = cost_to_close_from_chain(p, chain)
    # buy back the 5000 put at 4.2, sell the 4975 put at 2.1 -> $210 to close
    assert out["cost_to_close"] == 210.0
    assert out["short_delta"] == 0.10


def test_cost_to_close_none_when_contract_missing():
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "", trade_id="T1")
    p = parse_rows(COLUMNS, [row])[0]
    chain = OptionChain(underlying="SPX", underlying_price=5080.0, contracts=[])
    assert cost_to_close_from_chain(p, chain) is None


def test_cost_to_close_skips_far_dated_legs():
    """A PMCC prices only the short call (the leg the 50% rule applies to)."""
    p = Position(
        trade_id="T1", underlying="AAPL", strategy_key="poor_mans_covered_call",
        opened=date.today(), expiration=date.today() + timedelta(days=30),
        contracts=1, credit=250.0,
        legs=[
            Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
                strike=150, premium=60.0, dte=400),
            Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
                strike=220, premium=2.5, dte=30),
        ])
    exp = p.expiration.isoformat()
    chain = OptionChain(underlying="AAPL", underlying_price=210.0, contracts=[
        OptionContract(option_type=OptionType.CALL, strike=220, expiration=exp,
                       dte=30, delta=0.25, bid=1.4, ask=1.6),
    ])
    out = cost_to_close_from_chain(p, chain)
    assert out["cost_to_close"] == 150.0    # only the short call, at 1.5 mid


def test_to_date_handles_sheet_utc_instants():
    """The sheet hands ISO dates back as UTC instants; we want the LOCAL
    calendar day of that instant, not a truncation (which shifts a day for
    anyone east of Greenwich, like Rita in Israel)."""
    from datetime import datetime, timezone
    instant = datetime(2026, 7, 4, 21, 0, 0, tzinfo=timezone.utc)
    assert _to_date("2026-07-04T21:00:00.000Z") == instant.astimezone().date()
    assert _to_date("2026-07-05") == date(2026, 7, 5)
    assert _to_date(date(2026, 7, 5)) == date(2026, 7, 5)
    assert _to_date("not a date") is None
    assert _to_date("") is None


def test_backdated_rows_round_trip():
    """Quick Log backdating / history import: the dates given are the dates
    that come back out of the log."""
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "old trade",
                    trade_id="T1", opened_on=date(2026, 6, 5),
                    expiration_on=date(2026, 7, 20))
    close = build_close_row("T1", "SPX", "Put Credit Spread", 150.0, 150.0,
                            "Profit target (50%) hit", "stayed patient",
                            closed_on=date(2026, 6, 25))
    p = parse_rows(COLUMNS, [row, close])[0]
    assert p.opened == date(2026, 6, 5)
    assert p.expiration == date(2026, 7, 20)
    assert p.closed_on == date(2026, 6, 25)
    assert p.realized_pl == 150.0


def test_build_row_defaults_unchanged():
    """Without the new params, rows behave exactly as before (today)."""
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "", trade_id="T1")
    assert row[0] == date.today().isoformat()
    assert row[14] == (date.today() + timedelta(days=30)).isoformat()


# ------------------------------------------------- price vs the strike she sold
def _leg(action, opt_type, strike, qty=1):
    return Leg(role="x", action=action, option_type=opt_type, strike=strike,
               quantity=qty, dte=30)


def test_strike_cushion_on_a_put_credit_spread():
    """Her SPX put spread: price above the short put = room to fall."""
    p = Position(trade_id="T1", underlying="SPX", legs=[
        _leg(Action.SELL, OptionType.PUT, 5000),
        _leg(Action.BUY, OptionType.PUT, 4975)])
    c = strike_cushion(p, 5100.0)
    assert c["strike"] == 5000 and c["option_type"] == "put"
    assert abs(c["room_pct"] - (100 / 5100)) < 1e-9   # ~2.0% of room
    assert c["breached"] is False


def test_strike_cushion_picks_the_side_price_is_nearest():
    """An iron condor has two short strikes - report whichever one price is
    closer to. Here 5100 sits nearer the 4950 put than the 5300 call."""
    p = Position(trade_id="T1", underlying="SPX", legs=[
        _leg(Action.BUY, OptionType.PUT, 4900),
        _leg(Action.SELL, OptionType.PUT, 4950),
        _leg(Action.SELL, OptionType.CALL, 5300),
        _leg(Action.BUY, OptionType.CALL, 5350)])
    c = strike_cushion(p, 5100.0)
    assert c["strike"] == 4950 and c["option_type"] == "put"
    assert abs(c["room_pct"] - (150 / 5100)) < 1e-9   # ~2.9%, vs 3.9% call side
    assert c["breached"] is False


def test_strike_cushion_ignores_the_long_leaps_in_a_pmcc():
    """Only the call you SOLD counts - never the LEAPS you bought."""
    p = Position(trade_id="T1", underlying="AAPL", legs=[
        _leg(Action.BUY, OptionType.CALL, 150),
        _leg(Action.SELL, OptionType.CALL, 220)])
    c = strike_cushion(p, 210.0)
    assert c["strike"] == 220 and c["option_type"] == "call"
    assert abs(c["room_pct"] - (10 / 210)) < 1e-9     # ~4.8% of room to rise


def test_strike_cushion_flags_a_breached_strike():
    p = Position(trade_id="T1", underlying="AAPL", legs=[
        _leg(Action.SELL, OptionType.CALL, 200)])
    c = strike_cushion(p, 210.0)
    assert c["breached"] is True
    assert c["room_pct"] < 0


def test_strike_cushion_none_without_a_price_or_short_leg():
    p = Position(trade_id="T1", underlying="SPX", legs=[
        _leg(Action.SELL, OptionType.PUT, 5000)])
    assert strike_cushion(p, None) is None
    assert strike_cushion(p, 0.0) is None
    long_only = Position(trade_id="T2", underlying="SPX", legs=[
        _leg(Action.BUY, OptionType.PUT, 5000)])
    assert strike_cushion(long_only, 5100.0) is None


def test_performance_summary():
    today = date(2026, 7, 8)   # a Wednesday; the week started Monday July 6
    def closed(pl, closed_on, name="Put Credit Spread"):
        return Position(trade_id=f"t{pl}{closed_on}", underlying="SPX",
                        strategy_name=name, status="closed",
                        closed_on=closed_on, realized_pl=pl, credit=300)
    positions = [
        closed(150.0, date(2026, 7, 7)),                       # this week + month
        closed(-90.0, date(2026, 7, 2), "Iron Condor"),        # this month only
        closed(200.0, date(2026, 6, 10)),                      # older
    ]
    perf = performance(positions, today=today)
    assert perf["closed_count"] == 3
    assert perf["week_pl"] == 150.0
    assert perf["month_pl"] == 60.0
    assert perf["total_pl"] == 260.0
    assert abs(perf["win_rate"] - 2 / 3) < 1e-9
    assert perf["avg_win"] == 175.0
    assert perf["avg_loss"] == -90.0
    assert perf["by_strategy"]["Iron Condor"]["trades"] == 1
    assert [c["total"] for c in perf["cumulative"]] == [200.0, 110.0, 260.0]


# ------------------------------------------------------------------ month view
def _pos(opened=None, closed_on=None, pl=None, status=None, bp=0.0,
         reason="", name="Put Credit Spread") -> Position:
    if status is None:
        status = "closed" if closed_on else "open"
    return Position(
        trade_id=f"t-{opened}-{closed_on}-{pl}", underlying="SPX",
        strategy_name=name, status=status, opened=opened, closed_on=closed_on,
        realized_pl=pl, buying_power=bp, credit=300, exit_reason=reason)


def test_monthly_summary_profit_lands_in_the_close_month():
    today = date(2026, 7, 8)
    positions = [
        # opened June, closed July: listed in both months, profit only in July
        _pos(opened=date(2026, 6, 20), closed_on=date(2026, 7, 7), pl=150.0,
             bp=2200.0, reason="Profit target (50%) hit"),
        # opened and closed inside June
        _pos(opened=date(2026, 6, 2), closed_on=date(2026, 6, 25), pl=-90.0,
             bp=1800.0, reason="Stop loss hit"),
        # still open, opened July
        _pos(opened=date(2026, 7, 3), bp=2500.0),
    ]
    months = monthly_summary(positions, today=today)
    assert [m["month"] for m in months] == ["2026-07", "2026-06"]

    july = months[0]
    assert july["label"] == "July 2026"
    assert july["realized_pl"] == 150.0
    assert july["closed_count"] == 1 and july["wins"] == 1
    assert july["win_rate"] == 1.0
    assert july["opened_count"] == 1 and july["still_open"] == 1
    assert july["bp_opened"] == 2500.0
    tags = {r["tag"] for r in july["rows"]}
    assert tags == {"closed", "opened"}

    june = months[1]
    assert june["realized_pl"] == -90.0          # July's win is NOT June money
    assert june["closed_count"] == 1 and june["wins"] == 0
    assert june["opened_count"] == 2
    assert june["bp_opened"] == 4000.0
    june_tags = sorted(r["tag"] for r in june["rows"])
    assert june_tags == ["both", "opened"]       # same-month close + carried one

    # the month view and the results dashboard must never disagree
    perf = performance(positions, today=today)
    assert july["realized_pl"] == perf["month_pl"]


def test_monthly_summary_current_month_present_when_empty():
    months = monthly_summary([], today=date(2026, 7, 8))
    assert len(months) == 1
    m = months[0]
    assert m["month"] == "2026-07"
    assert m["realized_pl"] == 0.0
    assert m["win_rate"] is None
    assert m["rows"] == [] and m["lessons"] == []


def test_monthly_summary_rules_and_lessons():
    today = date(2026, 7, 8)
    positions = [
        _pos(opened=date(2026, 7, 1), closed_on=date(2026, 7, 3), pl=100.0,
             reason="Profit target (50%) hit - patience pays"),
        _pos(opened=date(2026, 7, 1), closed_on=date(2026, 7, 6), pl=-50.0,
             reason="Other - panicked and closed early"),
        _pos(opened=date(2026, 7, 1), closed_on=date(2026, 7, 7), pl=80.0,
             reason="21 DTE time exit"),
    ]
    july = monthly_summary(positions, today=today)[0]
    assert july["closed_count"] == 3
    assert july["rules_followed"] == 2           # "Other" does not count
    assert july["lessons"] == ["panicked and closed early", "patience pays"]


def test_credit_roll_at_21_dte_counts_as_rules_followed():
    """Since the 2026-07-14 SOP change, rolling for a net credit at 21 DTE is a
    compliant exit - only drifting past 21 DTE with no decision breaks the rule.
    A roll at any other moment ("Rolled to a new position") still does not count.
    """
    today = date(2026, 7, 8)
    positions = [
        _pos(opened=date(2026, 7, 1), closed_on=date(2026, 7, 5), pl=40.0,
             reason="21 DTE credit roll (opened a new spread) - took the credit"),
        _pos(opened=date(2026, 7, 1), closed_on=date(2026, 7, 6), pl=-20.0,
             reason="Rolled to a new position"),
    ]
    july = monthly_summary(positions, today=today)[0]
    assert july["closed_count"] == 2
    assert july["rules_followed"] == 1
    assert july["lessons"] == ["took the credit"]


# ---------- buying power the way thinkorswim counts it ----------
def test_bp_effect_is_zero_on_a_position_bought_outright():
    """Rita's ruling: TOS is always right. A PMCC's LEAPS is paid for in cash,
    so the broker holds nothing against it - TOS shows BP Effect 0.00 where the
    app used to log the whole LEAPS cost."""
    from src.engine.positions import Position

    pmcc = Position(underlying="QQQ", open_cash=-17092.0, buying_power=17092.0)
    assert pmcc.is_debit
    assert pmcc.bp_effect == 0.0
    # The cash is still real and still tracked, just not as buying power.
    assert pmcc.capital_at_risk == 17092.0

    spread = Position(underlying="SPX", open_cash=415.0, buying_power=2085.0)
    assert spread.bp_effect == 2085.0


def test_a_bp_effect_typed_from_tos_beats_the_estimate():
    from src.engine.positions import Position

    csp = Position(underlying="PLTR", open_cash=575.0, buying_power=12225.0,
                   bp_override=3482.70)
    assert csp.bp_effect == 3482.70


def test_monthly_budget_ignores_cash_bought_positions():
    """The app read $44,892 committed where her broker read $14,830, because
    three PMCCs were counted at their full LEAPS cost."""
    from src.engine.positions import Position, bp_committed_this_month

    today = date(2026, 7, 18)
    positions = [
        Position(underlying="QQQ", opened=date(2026, 7, 16),
                 open_cash=-17092.0, buying_power=17092.0),
        Position(underlying="SPX", opened=date(2026, 7, 2),
                 open_cash=415.0, buying_power=2085.0),
    ]
    # Only the spread reserves buying power.
    assert bp_committed_this_month(positions, today=today) == 2085.0


def test_shares_are_margined_even_though_she_paid_for_them():
    """The split is options vs stock, not paid vs collected. TOS holds 0.00
    against her PMCC LEAPS and 18,312.75 against her IWM shares."""
    from src.engine.positions import Position

    pmcc = Position(underlying="DIA", open_cash=-14360.0, buying_power=14360.0,
                    shares_cost=0.0)
    covered = Position(underlying="IWM", open_cash=-30601.0, buying_power=30601.0,
                       shares_cost=30048.0)
    assert pmcc.bp_effect == 0.0
    assert covered.bp_effect > 0, "stock bought on margin does hold buying power"
    # And her real TOS number still overrides the conservative estimate.
    covered.bp_override = 18312.75
    assert covered.bp_effect == 18312.75


def test_a_later_close_row_corrects_an_earlier_one():
    """How a mistyped fill gets fixed without deleting anything: the log is an
    event log read in order, so a second close for the same trade wins."""
    today = date(2026, 7, 18)
    rows = [
        build_row(_trade(), "Iron Condor", SIZE, True, "",
                  trade_id="NDX1", opened_on=today),
        build_close_row("NDX1", "NDX", "Iron Condor", 2300.0, 1470.0, "21 DTE time exit"),
        build_close_row("NDX1", "NDX", "Iron Condor", 2260.0, 1510.0,
                        "21 DTE time exit", note="Corrected fill"),
    ]
    positions = parse_rows(COLUMNS, rows)
    ndx = next(p for p in positions if p.trade_id == "NDX1")
    assert ndx.exit_cost == 2260.0
    assert ndx.realized_pl == 1510.0
    assert "Corrected fill" in ndx.exit_reason
    # And the trade is still ONE closed position, not two.
    assert len([p for p in positions if p.trade_id == "NDX1"]) == 1
    assert ndx.status == "closed"


def test_a_correction_does_not_double_count_in_the_month():
    from src.engine.positions import monthly_summary

    today = date(2026, 7, 18)
    opened, closed = date(2026, 7, 2), date(2026, 7, 16)
    # closed_on must be passed explicitly. It defaults to the real today, so a
    # test that asserts on a fixed month only passed while the calendar happened
    # to sit in that month - this one broke the day July became August.
    rows = [
        build_row(_trade(), "Iron Condor", SIZE, True, "",
                  trade_id="NDX1", opened_on=opened),
        build_close_row("NDX1", "NDX", "Iron Condor", 2300.0, 1470.0, "21 DTE time exit",
                        closed_on=closed),
        build_close_row("NDX1", "NDX", "Iron Condor", 2260.0, 1510.0, "21 DTE time exit",
                        closed_on=closed),
    ]
    july = next(m for m in monthly_summary(parse_rows(COLUMNS, rows), today=today)
                if m["month"] == "2026-07")
    assert july["closed_count"] == 1
    assert july["realized_pl"] == 1510.0


def test_the_account_stamp_round_trips_through_the_log():
    """The account is written into Details JSON, not a new column - a new
    column would mean redeploying the Apps Script."""
    row = build_row(_trade(), "Put Credit Spread", {**SIZE, "account": "paper"},
                    True, "practice run", trade_id="20260803-1-SPX")
    p = parse_rows(COLUMNS, [row])[0]
    assert p.account == "paper"

    real = build_row(_trade(), "Put Credit Spread", {**SIZE, "account": "real"},
                     True, "", trade_id="20260803-2-SPX")
    assert parse_rows(COLUMNS, [real])[0].account == "real"


def test_a_row_with_no_account_stamp_parses_as_unknown():
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                    trade_id="20260803-3-SPX")
    assert parse_rows(COLUMNS, [row])[0].account == ""


def test_a_junk_account_value_is_ignored_rather_than_trusted():
    row = build_row(_trade(), "Put Credit Spread", {**SIZE, "account": "REAL-ish"},
                    True, "", trade_id="20260803-4-SPX")
    assert parse_rows(COLUMNS, [row])[0].account == ""


def test_the_account_column_carries_through_roll_and_close_rows():
    """Every row of a trade says which book it is in, so the sheet can be
    sorted or filtered on that one column."""
    from datetime import date as _date

    from src.logging_tools.row import build_close_row, build_roll_row

    opened = build_row(_trade(), "Put Credit Spread", {**SIZE, "account": "real"},
                       True, "", trade_id="T1")
    rolled = build_roll_row("T1", "SPX", "Put Credit Spread", 90.0,
                            new_strike=7100.0, new_expiration=_date(2026, 10, 16),
                            new_credit=140.0, account="real")
    closed = build_close_row("T1", "SPX", "Put Credit Spread", 210.0, 210.0,
                             "Profit target (50%) hit", account="real")
    acct = COLUMNS.index("Account")
    assert [opened[acct], rolled[acct], closed[acct]] == ["real", "real", "real"]

    p = parse_rows(COLUMNS, [opened, rolled, closed])[0]
    assert p.account == "real"
    assert p.status == "closed"


def test_an_older_row_with_no_account_column_at_all_still_parses():
    """Her sheet's existing rows are 18 columns wide. They must keep working -
    the account simply reads as unknown and falls back to the go-live date."""
    row = build_row(_trade(), "Put Credit Spread", SIZE, True, "",
                    trade_id="OLD")[:18]
    positions = parse_rows(COLUMNS[:18], [row])
    assert len(positions) == 1
    assert positions[0].account == ""
    assert positions[0].underlying == "SPX"


# ---------- correcting details typed wrong (the "edit" event) ----------
def _opened(trade_id: str = "E1") -> list:
    return build_row(_trade(), "Put Credit Spread", SIZE, True, "first note",
                     trade_id=trade_id)


def test_an_edit_corrects_the_contract_count():
    """Her actual complaint: typed the details wrong and could not fix them."""
    edit = build_edit_row("E1", "SPX", "Put Credit Spread",
                          {"contracts": 3}, summary="contracts 1 -> 3")
    p = parse_rows(COLUMNS, [_opened(), edit])[0]
    assert p.contracts == 3


def test_an_edit_only_touches_the_fields_it_names():
    """Absent means "she did not change this", NOT "set it to blank" - a form
    that submitted everything would otherwise wipe what it had no input for."""
    edit = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 2})
    p = parse_rows(COLUMNS, [_opened(), edit])[0]
    assert p.contracts == 2
    assert p.underlying == "SPX"
    assert p.note == "first note"
    assert p.credit == 300.0
    assert p.expiration is not None
    assert [leg.strike for leg in p.legs] == [5000.0, 4975.0]


def test_an_edit_can_move_the_strikes_and_the_day_one_snapshot_with_them():
    legs = [{"role": "short_put", "action": "sell", "type": "put", "strike": 5050,
             "delta": -0.2, "premium": 8.0, "qty": 1, "dte": 30},
            {"role": "long_put", "action": "buy", "type": "put", "strike": 5025,
             "delta": -0.15, "premium": 5.0, "qty": 1, "dte": 30}]
    edit = build_edit_row("E1", "SPX", "Put Credit Spread",
                          {"legs": legs, "strikes": "5050 / 5025"})
    p = parse_rows(COLUMNS, [_opened(), edit])[0]
    assert [leg.strike for leg in p.legs] == [5050.0, 5025.0]
    assert [leg.strike for leg in p.open_legs] == [5050.0, 5025.0]


def test_the_last_edit_wins_so_a_correction_can_be_corrected():
    first = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 5})
    second = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 2})
    p = parse_rows(COLUMNS, [_opened(), first, second])[0]
    assert p.contracts == 2


def test_an_edit_leaves_the_rolls_and_the_close_alone():
    from src.logging_tools.row import build_roll_row

    rolled = build_roll_row("E1", "SPX", "Put Credit Spread", 90.0,
                            new_strike=7100.0, new_credit=140.0)
    closed = build_close_row("E1", "SPX", "Put Credit Spread", 210.0, 210.0,
                             "Profit target (50%) hit")
    edit = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 2})
    p = parse_rows(COLUMNS, [_opened(), rolled, edit, closed])[0]
    assert p.contracts == 2
    assert p.status == "closed"
    assert p.roll_income == 90.0
    assert p.realized_pl == 210.0


def test_an_edit_can_correct_one_roll_by_its_write_order():
    """Rolls are addressed by the order they were WRITTEN, not by date -
    correcting a roll's date would renumber a date-sorted list underneath the
    very edit doing the correcting."""
    from src.logging_tools.row import build_roll_row

    r1 = build_roll_row("E1", "SPX", "Put Credit Spread", 100.0, new_strike=7100.0,
                        rolled_on=date(2026, 7, 16))
    r2 = build_roll_row("E1", "SPX", "Put Credit Spread", 200.0, new_strike=7200.0,
                        rolled_on=date(2026, 7, 27))
    fix = build_edit_row("E1", "SPX", "Put Credit Spread", {"open_cash": 241.0},
                         target="roll", roll_index=1)
    p = parse_rows(COLUMNS, [_opened(), r1, r2, fix])[0]
    assert p.roll_income == pytest.approx(100.0 + 241.0)


def test_an_edit_for_an_unknown_trade_is_ignored():
    edit = build_edit_row("GHOST", "SPX", "Put Credit Spread", {"contracts": 9})
    positions = parse_rows(COLUMNS, [_opened(), edit])
    assert len(positions) == 1
    assert positions[0].contracts == 1


def test_an_edit_row_reads_as_a_correction_in_the_sheet():
    """The Notes cell has to say what changed, or the sheet grows mystery rows."""
    edit = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 3},
                          summary="contracts 1 -> 3")
    assert edit[COLUMNS.index("Event")] == "edit"
    assert edit[COLUMNS.index("Notes")] == "contracts 1 -> 3"
    assert edit[COLUMNS.index("Trade ID")] == "E1"
    assert edit[COLUMNS.index("Realized P&L $")] == ""


def test_an_edit_cannot_change_the_strategy_or_the_ticker():
    """Locked on purpose: either makes it a different trade."""
    edit = build_edit_row("E1", "SPX", "Put Credit Spread",
                          {"strategy": "iron_condor", "underlying": "NDX",
                           "contracts": 2})
    p = parse_rows(COLUMNS, [_opened(), edit])[0]
    assert p.strategy_name == "Put Credit Spread"
    assert p.underlying == "SPX"
    assert p.contracts == 2


def test_a_roll_carries_the_index_a_correction_must_use():
    """Position.rolls is DATE ordered; corrections address WRITE order. The UI
    reads the number off the event so the two cannot be confused."""
    from src.logging_tools.row import build_roll_row

    late = build_roll_row("E1", "SPX", "Put Credit Spread", 100.0, new_strike=7100.0,
                          rolled_on=date(2026, 8, 20))
    early = build_roll_row("E1", "SPX", "Put Credit Spread", 200.0, new_strike=7200.0,
                           rolled_on=date(2026, 7, 1))
    p = parse_rows(COLUMNS, [_opened(), late, early])[0]

    # Shown oldest-first by date, but written the other way round.
    assert [r.rolled_on for r in p.rolls] == [date(2026, 7, 1), date(2026, 8, 20)]
    assert [r.seq for r in p.rolls] == [1, 0]

    # Correcting the one displayed FIRST must hit the row written second.
    fix = build_edit_row("E1", "SPX", "Put Credit Spread", {"open_cash": 250.0},
                         target="roll", roll_index=p.rolls[0].seq)
    fixed = parse_rows(COLUMNS, [_opened(), late, early, fix])[0]
    assert fixed.roll_income == pytest.approx(100.0 + 250.0)


def test_correcting_a_pmcc_leaps_cost_reprices_the_whole_position():
    """On a PMCC the credit can be right while the cost basis is not, and every
    risk figure hangs off the cost rather than off the credit.

    Invented numbers - this repo is public and her real fills belong in her
    Google Sheet, not in source control.
    """
    from src.engine.models import Action as A, Leg as L, OptionType as OT
    from src.engine.quick_log import resize_after_edit

    strat = {"sizing": {"max_loss_basis": "debit"}}
    trade = Trade(strategy_key="poor_mans_covered_call", underlying="TEST",
                  contracts=1, underlying_price=300.0,
                  legs=[L(role="long_call_leaps", action=A.BUY, option_type=OT.CALL,
                          strike=200, premium=100.0, dte=400),
                        L(role="short_call", action=A.SELL, option_type=OT.CALL,
                          strike=340, premium=10.0, dte=40)])
    # The LEAPS really cost 10,000; whatever was logged before does not matter,
    # because the corrected cost is handed in as the ledger it implies.
    fresh = resize_after_edit(strat, trade, 1000.0, old_credit=1000.0,
                              old_open_cash=1000.0 - 10000.0,
                              old_shares_cost=0.0, old_contracts=1)
    assert fresh["open_cash"] == pytest.approx(-9000.0)
    assert fresh["max_loss"] == pytest.approx(9000.0)
    assert fresh["buying_power"] == pytest.approx(9000.0)


def test_correcting_a_pmcc_leaps_cost_scales_with_the_contracts():
    """Per contract, so changing the count resizes the position rather than
    relabelling it."""
    from src.engine.models import Action as A, Leg as L, OptionType as OT
    from src.engine.quick_log import resize_after_edit

    strat = {"sizing": {"max_loss_basis": "debit"}}
    trade = Trade(strategy_key="poor_mans_covered_call", underlying="TEST",
                  contracts=2, underlying_price=300.0,
                  legs=[L(role="long_call_leaps", action=A.BUY, option_type=OT.CALL,
                          strike=200, premium=100.0, dte=400),
                        L(role="short_call", action=A.SELL, option_type=OT.CALL,
                          strike=340, premium=10.0, dte=40)])
    fresh = resize_after_edit(strat, trade, 2000.0, old_credit=1000.0,
                              old_open_cash=1000.0 - 10000.0,
                              old_shares_cost=0.0, old_contracts=1)
    assert fresh["open_cash"] == pytest.approx(-18000.0)
    assert fresh["buying_power"] == pytest.approx(18000.0)


# ---------- taking back a close that never happened (the "reopen" event) ----------
def test_a_reopen_puts_the_trade_back_on_the_books():
    """Her actual complaint: a close landed on a trade she had not closed, and
    it left her open trades with no way back but deleting the whole record."""
    closed = build_close_row("E1", "SPX", "Put Credit Spread", 100.0, 200.0,
                             "Profit target (50%) hit")
    back = build_reopen_row("E1", "SPX", "Put Credit Spread")
    p = parse_rows(COLUMNS, [_opened(), closed, back])[0]
    assert p.status == "open"
    assert open_positions([p]) == [p]
    assert closed_positions([p]) == []


def test_a_reopen_stops_the_close_counting_as_a_result():
    """Money she never banked must leave the month with the close."""
    closed = build_close_row("E1", "SPX", "Put Credit Spread", 100.0, 200.0,
                             "Profit target (50%) hit")
    back = build_reopen_row("E1", "SPX", "Put Credit Spread")
    p = parse_rows(COLUMNS, [_opened(), closed, back])[0]
    assert p.realized_pl is None
    assert p.closed_on is None
    assert p.exit_cost is None
    assert p.close_cash is None
    assert p.exit_reason == ""


def test_a_reopen_keeps_the_rolls_and_the_corrections():
    """Back exactly as it stood - that is the whole point of not deleting it."""
    from src.logging_tools.row import build_roll_row

    rolled = build_roll_row("E1", "SPX", "Put Credit Spread", 90.0,
                            new_strike=7100.0, new_credit=140.0)
    edit = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 2})
    closed = build_close_row("E1", "SPX", "Put Credit Spread", 100.0, 200.0, "50%")
    back = build_reopen_row("E1", "SPX", "Put Credit Spread")
    p = parse_rows(COLUMNS, [_opened(), rolled, edit, closed, back])[0]
    assert p.status == "open"
    assert p.contracts == 2
    assert p.roll_income == 90.0
    assert len(p.rolls) == 1


def test_closing_again_after_a_reopen_closes_it_again():
    """Last word wins, the same way a corrected close supersedes the first."""
    first = build_close_row("E1", "SPX", "Put Credit Spread", 100.0, 200.0, "50%")
    back = build_reopen_row("E1", "SPX", "Put Credit Spread")
    again = build_close_row("E1", "SPX", "Put Credit Spread", 150.0, 150.0,
                            "21 DTE time exit")
    p = parse_rows(COLUMNS, [_opened(), first, back, again])[0]
    assert p.status == "closed"
    assert p.realized_pl == 150.0
    assert p.exit_reason == "21 DTE time exit"


def test_a_reopen_for_an_unknown_trade_is_ignored():
    back = build_reopen_row("GHOST", "SPX", "Put Credit Spread")
    positions = parse_rows(COLUMNS, [_opened(), back])
    assert len(positions) == 1
    assert positions[0].status == "open"


def test_a_reopen_row_reads_as_itself_in_the_sheet():
    back = build_reopen_row("E1", "SPX", "Put Credit Spread", account="real")
    assert back[COLUMNS.index("Event")] == "reopen"
    assert back[COLUMNS.index("Trade ID")] == "E1"
    assert back[COLUMNS.index("Realized P&L $")] == ""
    assert back[COLUMNS.index("Exit Cost $")] == ""
    # The Apps Script files a row by this cell, so a real trade's reopen has to
    # land in the real book rather than in the practice tab.
    assert back[COLUMNS.index("Account")] == "real"


def test_a_correction_is_filed_in_the_trades_own_book():
    """The sheet routes a row to its tab by the Account cell. Blank meant every
    correction - to a real trade as much as a practice one - was filed in the
    practice book."""
    edit = build_edit_row("E1", "SPX", "Put Credit Spread", {"contracts": 2},
                          account="real")
    assert edit[COLUMNS.index("Account")] == "real"
    # And it still applies to the trade, whichever tab it was read from.
    assert parse_rows(COLUMNS, [_opened(), edit])[0].contracts == 2
