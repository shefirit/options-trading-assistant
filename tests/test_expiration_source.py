"""Capturing the REAL expiration at the moment the trade is logged.

tests/test_expiration_match.py fixes the symptom: a stored date that was never
a listed expiration still prices, by matching the nearest one within a week.
This fixes the cause - the date should not have been wrong in the first place.

Where it was lost: an OptionContract carries its own expiration, scanner._leg()
kept only `dte`, and row.build_row() then reconstructed the date as
opened + dte. That arithmetic is a guess. Her FCX spread, opened on a Friday at
45 DTE, was filed against a Monday the exchange has never listed.

Quick Log was always fine - it asks her for the date and derives DTE from it.
The broken path was "Log this trade" in Find a trade, where the trade is built
from a live chain and the real date was in hand the whole time.

Old rows carry no per-leg date and must keep working on the arithmetic, so the
fallbacks are pinned here as tightly as the fix.

All synthetic. This repo is public.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from src.data.chain import OptionContract
from src.engine import scanner
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import Position, _parse_details
from src.logging_tools.row import COLUMNS, build_row

OPENED = date(2026, 9, 11)          # a Friday
REAL = date(2026, 10, 23)           # the Friday the trade is really on
NAIVE = OPENED + timedelta(days=45)  # 26 Oct, a Monday - never listed


def _contract(strike: float, exp: date = REAL, dte: int = 45) -> OptionContract:
    return OptionContract(option_type=OptionType.PUT, strike=strike,
                          expiration=exp.isoformat(), dte=dte, delta=-0.24,
                          bid=1.26, ask=2.0)


def _chain_trade() -> Trade:
    """The shape Find a trade builds: legs straight off a live chain."""
    return Trade(
        strategy_key="put_credit_spread", underlying="FCX", contracts=2,
        underlying_price=70.86,
        legs=[scanner._leg("short_put", Action.SELL, _contract(65.0)),
              scanner._leg("long_put", Action.BUY, _contract(60.0))])


def _row(trade: Trade, **kw) -> dict:
    row = build_row(trade, "Put Credit Spread", {"credit": 176.0}, True, "",
                    trade_id="T1", opened_on=OPENED, **kw)
    return dict(zip(COLUMNS, row))


# ------------------------------------------------- the date survives the trip
def test_the_scanner_keeps_the_contracts_own_expiration():
    """The loss point. A contract knows when it expires; the leg used to
    forget."""
    leg = scanner._leg("short_put", Action.SELL, _contract(65.0))
    assert leg.expiration == REAL
    assert leg.dte == 45


def test_a_trade_reports_its_near_expiration_as_a_date():
    t = _chain_trade()
    assert t.expiration == REAL
    assert t.dte == 45


def test_a_hand_built_trade_has_no_date_to_report():
    """None, not a guess. It is what tells build_row to fall back."""
    t = Trade(strategy_key="put_credit_spread", underlying="FCX",
              legs=[Leg(role="short_put", action=Action.SELL,
                        option_type=OptionType.PUT, strike=65.0, dte=45)])
    assert t.expiration is None


def test_a_pmcc_reports_the_NEAR_leg_not_the_leaps():
    """The row's one Expiration cell is the near date - what the exit rules
    count down to. The LEAPS date rides in the JSON per leg."""
    t = Trade(strategy_key="poor_mans_covered_call", underlying="AAPL", legs=[
        Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
            strike=150, dte=400, expiration=date(2027, 10, 15)),
        Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
            strike=220, dte=30, expiration=REAL),
    ])
    assert t.expiration == REAL


# ----------------------------------------------------------- what gets written
def test_the_row_stores_the_real_date_not_the_arithmetic():
    """THE fix. The naive answer is a Monday that never existed."""
    assert _row(_chain_trade())["Expiration"] == REAL.isoformat()
    assert REAL != NAIVE


def test_the_date_she_typed_still_wins():
    """Quick Log asks her outright. Her answer beats the chain's, because a
    backdated entry is describing a trade the chain no longer reflects."""
    typed = date(2026, 11, 20)
    assert _row(_chain_trade(), expiration_on=typed)["Expiration"] == typed.isoformat()


def test_a_trade_with_no_dated_leg_still_falls_back_to_opened_plus_dte():
    """Every row written before this change came from that arithmetic. It has
    to keep working, or old history stops pricing."""
    t = Trade(strategy_key="put_credit_spread", underlying="FCX", contracts=1,
              legs=[Leg(role="short_put", action=Action.SELL,
                        option_type=OptionType.PUT, strike=65.0, dte=45)])
    assert _row(t)["Expiration"] == NAIVE.isoformat()


def test_each_leg_carries_its_own_date_into_the_json():
    legs = json.loads(_row(_chain_trade())["Details JSON"])["legs"]
    assert [l["exp"] for l in legs] == [REAL.isoformat(), REAL.isoformat()]


def test_a_leg_with_no_date_writes_no_key_at_all():
    """Absent, not null. "Not captured" and "expires never" are different, and
    only the missing key can say the first one."""
    t = Trade(strategy_key="put_credit_spread", underlying="FCX", contracts=1,
              legs=[Leg(role="short_put", action=Action.SELL,
                        option_type=OptionType.PUT, strike=65.0, dte=45)])
    assert "exp" not in json.loads(_row(t)["Details JSON"])["legs"][0]


# --------------------------------------------------------- and comes back out
def test_the_leg_date_round_trips_through_the_log():
    blob = _row(_chain_trade())["Details JSON"]
    _data, legs = _parse_details(blob)
    assert [l.expiration for l in legs] == [REAL, REAL]


def test_leg_expiration_prefers_the_stored_date_over_the_arithmetic():
    """A row written today: the two disagree, and the stored one is right."""
    p = Position(trade_id="T1", underlying="FCX", strategy_key="put_credit_spread",
                 opened=OPENED, expiration=REAL, contracts=2,
                 legs=[Leg(role="short_put", action=Action.SELL,
                           option_type=OptionType.PUT, strike=65.0, dte=45,
                           expiration=REAL)])
    assert p.leg_expiration(p.legs[0]) == REAL      # not NAIVE


def test_leg_expiration_still_computes_it_for_an_older_row():
    p = Position(trade_id="T1", underlying="FCX", strategy_key="put_credit_spread",
                 opened=OPENED, expiration=NAIVE, contracts=2,
                 legs=[Leg(role="short_put", action=Action.SELL,
                           option_type=OptionType.PUT, strike=65.0, dte=45)])
    assert p.leg_expiration(p.legs[0]) == NAIVE


def test_a_pmcc_keeps_its_two_expirations_apart_through_the_log():
    """One row, two months. The near call and the LEAPS must not collapse onto
    a single date on the way out."""
    t = Trade(strategy_key="poor_mans_covered_call", underlying="AAPL", contracts=1,
              legs=[
                  Leg(role="long_call_leaps", action=Action.BUY,
                      option_type=OptionType.CALL, strike=150, dte=400,
                      expiration=date(2027, 10, 15)),
                  Leg(role="short_call", action=Action.SELL,
                      option_type=OptionType.CALL, strike=220, dte=30,
                      expiration=REAL),
              ])
    _data, legs = _parse_details(_row(t)["Details JSON"])
    assert {l.role: l.expiration for l in legs} == {
        "long_call_leaps": date(2027, 10, 15), "short_call": REAL}
