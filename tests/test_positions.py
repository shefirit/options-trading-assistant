"""The trade log -> positions parser, plus the live cost-to-close math."""

from __future__ import annotations

from datetime import date, timedelta

from src.data.chain import OptionChain, OptionContract
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import (
    Position,
    bp_in_use,
    closed_positions,
    cost_to_close_from_chain,
    open_positions,
    parse_rows,
    performance,
)
from src.logging_tools.row import COLUMNS, build_close_row, build_row


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
