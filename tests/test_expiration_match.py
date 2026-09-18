"""Pricing a leg whose stored expiration was never a real one.

Reported 2026-09-18: her open FCX put credit spread read "Could not price this
right now" while every other open trade priced fine.

The log stores DTE AT ENTRY, not an expiration date, so a leg's expiration is
computed as opened + dte. She opened FCX on Friday 11 September at 45 DTE, which
computes to Monday 26 October - and FCX has never listed a 26 October
expiration. The real one beside it is Friday 23 October, three days away, and
the chain quoted both her strikes perfectly well.

The fetcher already knew the stored date could be synthetic: _expiration_chain()
asks for the NEAREST expiration, not an exact one. Only the matchers disagreed,
demanding an exact string match and returning None.

Her SOP takes the nearest real expiration to a 45-day target, so this was never
an FCX quirk - it is every trade whose entry date plus DTE misses a Friday.

All synthetic. This repo is public.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine.models import Action, Leg, OptionType
from src.engine.positions import (Position, cost_to_close_from_chain,
                                  position_value_from_chain)

OPENED = date(2026, 9, 11)          # a Friday
COMPUTED = date(2026, 10, 26)       # opened + 45 - a MONDAY, never listed
REAL = date(2026, 10, 23)           # the Friday the trade is actually on


def _spread() -> Position:
    """Her FCX shape: 2 contracts, short the 65 put, long the 60."""
    return Position(
        trade_id="20260911-142542-FCX", underlying="FCX",
        strategy_key="put_credit_spread", status="open",
        opened=OPENED, expiration=COMPUTED, contracts=2, credit=176.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=65.0, premium=1.64, dte=45, quantity=1),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=60.0, premium=0.76, dte=45, quantity=1),
        ])


def _chain(*expirations: date) -> OptionChain:
    """Both her strikes, quoted on each expiration given."""
    contracts = []
    for exp in expirations:
        contracts += [
            OptionContract(option_type=OptionType.PUT, strike=65.0,
                           expiration=exp.isoformat(), dte=38, delta=-0.2436,
                           bid=1.26, ask=2.0),
            OptionContract(option_type=OptionType.PUT, strike=60.0,
                           expiration=exp.isoformat(), dte=38, delta=-0.1136,
                           bid=0.48, ask=0.96),
        ]
    return OptionChain(underlying="FCX", underlying_price=70.86,
                       contracts=contracts)


# --------------------------------------------------------------- the report
def test_a_stored_date_that_never_existed_still_prices():
    """THE regression. The chain carries 23 October and quotes both strikes;
    nothing about that should read as unpriceable."""
    out = cost_to_close_from_chain(_spread(), _chain(REAL))
    assert out is not None, "the FCX spread read as unpriceable again"
    # buy back the 65 put at 1.63, sell the 60 put at 0.72 -> 0.91 x 100 x 2
    assert out["cost_to_close"] == 182.0
    assert out["short_delta"] == 0.244


def test_the_whole_position_valuation_gets_the_same_treatment():
    """Both matchers had the same exact-date bug; fixing one would have left
    the card blank for a different reason."""
    out = position_value_from_chain(_spread(), _chain(REAL), 70.86)
    assert out is not None
    assert out["value"] == -182.0


# ------------------------------------------------------------- the edges
def test_an_exact_date_still_wins_over_a_nearer_wrong_one():
    """Schwab hands back a FULL chain with every expiration in it. The stored
    date must keep winning outright there - its distance is zero - or a
    correctly dated position would start pricing off its neighbour."""
    chain = _chain(REAL, COMPUTED, date(2026, 10, 30))
    for c in chain.contracts:              # make the exact date distinguishable
        if c.expiration == COMPUTED.isoformat():
            c.bid, c.ask = 9.0, 9.0
    out = cost_to_close_from_chain(_spread(), chain)
    # 65 put at 9.0 minus 60 put at 9.0 = 0 -> the exact expiration was used
    assert out["cost_to_close"] == 0.0


def test_a_far_off_expiration_is_still_refused():
    """The tolerance is what keeps this honest. A month away is not 'near' -
    quoting her spread off November would be worse than saying nothing."""
    assert cost_to_close_from_chain(_spread(), _chain(date(2026, 11, 20))) is None


@pytest.mark.parametrize("gap,priced", [(0, True), (3, True), (7, True),
                                        (8, False), (30, False)])
def test_the_tolerance_is_one_week(gap, priced):
    exp = COMPUTED - timedelta(days=gap)
    assert (cost_to_close_from_chain(_spread(), _chain(exp)) is not None) is priced


def test_a_missing_strike_is_still_unpriceable():
    """Nearest-date matching must not become nearest-STRIKE matching. A chain
    without her strike cannot price her trade, however close the date."""
    chain = OptionChain(underlying="FCX", underlying_price=70.86, contracts=[
        OptionContract(option_type=OptionType.PUT, strike=55.0,
                       expiration=REAL.isoformat(), dte=38, delta=-0.05,
                       bid=0.2, ask=0.3),
    ])
    assert cost_to_close_from_chain(_spread(), chain) is None


def test_a_call_is_never_matched_to_a_put():
    chain = OptionChain(underlying="FCX", underlying_price=70.86, contracts=[
        OptionContract(option_type=OptionType.CALL, strike=65.0,
                       expiration=REAL.isoformat(), dte=38, delta=0.75,
                       bid=6.0, ask=6.4),
        OptionContract(option_type=OptionType.CALL, strike=60.0,
                       expiration=REAL.isoformat(), dte=38, delta=0.85,
                       bid=11.0, ask=11.4),
    ])
    assert cost_to_close_from_chain(_spread(), chain) is None


def test_a_junk_expiration_in_the_feed_is_skipped_not_raised():
    """A malformed date from a feed should cost that contract, not the tab."""
    chain = _chain(REAL)
    chain.contracts.append(
        OptionContract(option_type=OptionType.PUT, strike=65.0,
                       expiration="not-a-date", dte=38, delta=-0.24,
                       bid=99.0, ask=99.0))
    out = cost_to_close_from_chain(_spread(), chain)
    assert out["cost_to_close"] == 182.0
