"""The wheel followed from the first put to the shares being called away.

tests/test_wheel.py pins down the EXIT RULES for a wheel put (no stop, no time
exit, assignment is the plan). This file is the other half: what happens after
assignment actually lands - the shares, the falling cost basis, and what being
called away would really pay.

The story, with invented numbers (this repo is public): she sells a 50 put for
$150, is assigned at 50, and writes calls against the 100 shares. Every credit
comes off what those shares cost her.
"""

import json
from datetime import date, timedelta

from src.engine import positions, wheel
from src.logging_tools.row import COLUMNS, build_assign_row, build_roll_row

WHEEL = "The Wheel (CSP into Covered Call)"
TRADE = "20260101-090000-XYZ"


def _put_row(strike: float = 50.0, credit: float = 150.0, contracts: int = 1):
    """The open short put, in the shape Quick Log writes it."""
    today = date.today()
    details = {
        "key": "wheel",
        "underlying_price": 52.0,
        "legs": [{"role": "short_put", "action": "sell", "type": "put",
                  "strike": strike, "delta": 0.28, "premium": credit / 100,
                  "qty": 1, "dte": 35}],
        "open_cash": credit,
    }
    collateral = strike * 100 * contracts - credit
    return [
        (today - timedelta(days=40)).isoformat(), "XYZ", WHEEL,
        f"{strike:g}", 0.28, 35, contracts, credit, collateral, collateral,
        "yes", "", TRADE, "open", (today - timedelta(days=5)).isoformat(),
        "", "", json.dumps(details), "real",
    ]


def _parse(*rows):
    return positions.parse_rows(COLUMNS, list(rows))[0]


def _assigned(strike: float = 50.0, contracts: int = 1, credit: float = 150.0):
    return _parse(_put_row(strike, credit, contracts),
                  build_assign_row(TRADE, "XYZ", WHEEL, strike, contracts))


def _call(strike: float, credit: float = 0.0):
    return build_roll_row(TRADE, "XYZ", WHEEL, credit, new_strike=strike,
                          new_expiration=date.today() + timedelta(days=30),
                          new_credit=credit)


# ------------------------------------------------------------------ assignment
def test_assignment_keeps_it_one_trade_and_buys_the_shares():
    p = _assigned()
    assert p.status == "open", "assignment must not end the trade"
    assert p.assigned_strike == 50.0
    assert p.shares_cost == 5000.0
    assert p.open_cash == -4850.0        # +$150 credit, then $5,000 out


def test_the_put_leg_is_gone_once_it_is_exercised():
    """She does not hold that put any more, so exit rules must not price it."""
    p = _assigned()
    assert p.legs == []
    assert p.expiration is None
    # Nothing written against the shares yet, which is what puts the
    # sell-a-call form on screen.
    assert p.is_uncovered


def test_buying_the_shares_is_not_counted_as_a_loss():
    """Assignment moves money from cash into stock. Counted as income it would
    show a $5,000 loss in the month she was assigned - on a strategy where
    being assigned is the plan."""
    assert _assigned().roll_income == 0.0


# ------------------------------------------------------------------ cost basis
def test_cost_basis_starts_at_the_strike_less_the_put_premium():
    state = wheel.state_from(_assigned())
    assert state.shares == 100
    assert state.paid_per_share == 50.0
    assert state.premium_collected == 150.0
    assert state.cost_basis == 48.5      # 50 - 150/100
    assert state.break_even == 48.5


def test_every_call_written_afterwards_lowers_the_basis():
    """The heart of the wheel: two calls at $80 each take another $1.60 a share
    off what the shares cost her."""
    p = _parse(_put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
               _call(53.0, credit=80.0), _call(54.0, credit=80.0))
    state = wheel.state_from(p)
    assert state.premium_collected == 310.0      # 150 put + 80 + 80
    assert state.cost_basis == 46.9              # 50 - 310/100
    assert state.premium_per_share == 3.10


def test_a_bigger_position_divides_the_premium_over_every_share():
    p = _assigned(contracts=2, credit=300.0)
    state = wheel.state_from(p)
    assert state.shares == 200
    assert p.shares_cost == 10000.0
    assert state.cost_basis == 48.5              # 50 - 300/200


# ------------------------------------------------------- where she stands now
def test_shares_under_the_basis_are_not_a_loss_yet():
    state = wheel.state_from(_assigned(), market_price=47.0)
    assert state.below_basis
    assert state.unrealised == -150.0            # (47 - 48.5) x 100


def test_shares_above_the_basis_show_what_selling_would_bank():
    state = wheel.state_from(_assigned(), market_price=51.0)
    assert not state.below_basis
    assert state.unrealised == 250.0             # (51 - 48.5) x 100


def test_being_called_away_is_priced_against_the_basis_not_the_strike_paid():
    """A 49 call looks like a loss against the 50 she paid and is really a $50
    win against her 48.50 basis. That gap is the whole reason the premium has
    to be part of the sum."""
    p = _parse(_put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
               _call(49.0))
    state = wheel.state_from(p)
    assert state.call_strike == 49.0
    assert state.called_away_profit == 50.0      # (49 - 48.5) x 100


def test_a_call_written_below_the_basis_would_lock_in_a_loss():
    """Her SOP says never write one there, so the app has to be able to see
    it."""
    p = _parse(_put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
               _call(47.0))
    assert wheel.state_from(p).called_away_profit == -150.0


# ------------------------------------------------------------- who gets asked
def test_a_short_put_can_be_assigned():
    assert wheel.is_wheelable(_parse(_put_row()))


def test_a_position_already_assigned_is_not_offered_again():
    assert not wheel.is_wheelable(_assigned())


def test_an_unassigned_put_has_no_wheel_numbers():
    assert wheel.state_from(_parse(_put_row())) is None


def test_assignment_cash_leaves_her_account():
    assert wheel.assignment_cash(50.0, 1) == -5000.0
    assert wheel.assignment_cash(50.0, 3) == -15000.0


# ------------------------------------------------------------------- the story
def test_the_shares_are_paid_for_on_the_day_they_arrived():
    """The share purchase used to be folded into the opening line, which dated
    $5,000 to a day it did not move and buried the put's premium inside the
    same number. Day one collected $150; the money left five weeks later."""
    p = _assigned()
    steps = positions.story(p)

    opened = next(s for s in steps if s["kind"] == "open")
    assigned = next(s for s in steps if s["kind"] == "assign")
    assert opened["cash"] == 150.0, "day one was the put's credit, nothing else"
    assert assigned["cash"] == -5000.0
    assert assigned["on"] == p.assigned_on
    # The invariant: the story still adds up to the ledger it came from.
    assert steps[-1]["running"] == round(p.open_cash, 2)


def test_the_premium_tally_holds_the_shares_apart_from_the_calls():
    """What the wheel is really about: every credit against what the stock
    cost. The two must never be netted into one figure."""
    p = _parse(_put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
               _call(55.0, credit=120.0))
    assert p.premium_collected == 150.0 + 120.0
    assert p.open_bought_cost == 5000.0


# ------------------------------------------------------- what she sees on screen
def _text(at):
    return " ".join(str(m.value) for m in at.markdown)


def test_an_open_put_is_offered_the_assignment_button(app_with_rows):
    at = app_with_rows([_put_row()]).run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("I was assigned" in l for l in labels), f"expanders were {labels}"


def test_the_wheel_card_spells_out_the_cost_basis(app_with_rows):
    """The whole ask: basis, premiums, and what being called away pays."""
    at = app_with_rows([
        _put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
        _call(53.0, credit=80.0),
    ]).run()
    assert not at.exception
    body = _text(at)
    assert "Your wheel on XYZ" in body
    assert "100 shares, bought at 50" in body
    assert "230" in body       # 150 put + 80 call, every credit counted
    assert "47.70" in body     # 50 - 230/100, the basis after premium
    assert "530" in body       # called away at 53 against a 47.70 basis


def test_the_card_says_nothing_is_earning_when_no_call_is_written(app_with_rows):
    at = app_with_rows([
        _put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
    ]).run()
    assert not at.exception
    assert "Nothing is earning on these shares" in _text(at)


def test_an_assigned_wheel_is_not_offered_assignment_again(app_with_rows):
    at = app_with_rows([
        _put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
    ]).run()
    labels = [e.label for e in at.expander]
    assert not any("I was assigned" in l for l in labels)


def test_an_assigned_wheel_offers_the_sell_a_call_form(app_with_rows):
    """Assignment leaves her holding shares and nothing written against them,
    which is exactly the state that form exists for."""
    at = app_with_rows([
        _put_row(), build_assign_row(TRADE, "XYZ", WHEEL, 50.0, 1),
    ]).run()
    labels = [e.label for e in at.expander]
    assert any("Sell a call against it" in l for l in labels), \
        f"expanders were {labels}"
