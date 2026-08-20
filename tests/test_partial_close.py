"""Taking a credit spread apart on purpose: sell the long put, keep the short.

Her decision, and the one the app could not follow. She is not closing and not
rolling: she sells the long put back, banks what it is worth, and leaves the
short put open so it can be assigned and hand her the shares. From that fill
on, everything downstream has to change - the ledger, the risk, the follow-up
instruction, and the route into the wheel.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.engine import exit_rules, wheel
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import (cash_events, monthly_summary, parse_rows,
                                  pl_at, story)
from src.logging_tools.row import (COLUMNS, build_assign_row, build_close_row,
                                   build_leg_close_row, build_roll_row,
                                   build_row)

EXIT = {"profit_target_pct": 50, "stop_loss_multiple": 2.0, "time_exit_dte": 21}


def _spread(dte: int = 30) -> Trade:
    """Her shape: a 10-wide put credit spread on 1 contract, $300 collected."""
    return Trade(
        strategy_key="put_credit_spread", underlying="XYZ", contracts=1,
        underlying_price=112.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=100, delta=-0.25, premium=4.0, dte=dte),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=90, delta=-0.12, premium=1.0, dte=dte),
        ],
    )


SIZE = {"credit": 300.0, "max_loss": 700.0, "buying_power": 1000.0,
        "open_cash": 300.0}


def _rows(cash: float = 1400.0, for_assignment: bool = True,
          opened: date | None = None, sold_on: date | None = None):
    opened = opened or date.today() - timedelta(days=20)
    sold_on = sold_on or date.today() - timedelta(days=2)
    return [
        build_row(_spread(), "Put Credit Spread", SIZE, True, "",
                  trade_id="T1", opened_on=opened,
                  expiration_on=opened + timedelta(days=30)),
        build_leg_close_row("T1", "XYZ", "Put Credit Spread", cash,
                            strike=90.0, option_type="put", side="buy",
                            for_assignment=for_assignment, closed_on=sold_on),
    ]


def _position(**kw):
    return parse_rows(COLUMNS, _rows(**kw))[0]


# ------------------------------------------------------------------ the replay
def test_selling_the_long_put_leaves_only_the_short_one():
    p = _position()
    assert [(l.action, l.option_type, l.strike) for l in p.legs] == [
        (Action.SELL, OptionType.PUT, 100.0)]
    # Day one is untouched - the story still knows it was a spread.
    assert len(p.open_legs) == 2
    assert p.status == "open"


def test_the_cash_is_banked_on_the_day_it_landed():
    p = _position()
    assert p.leg_close_cash == 1400.0
    assert p.banked_income == 1400.0
    # Collected on this position now: the $300 credit and the $1,400 the long
    # put sold for. Both count towards "what have I kept".
    assert p.credit == 1700.0
    assert p.open_credit == 300.0

    events = cash_events([p])
    assert [(e["kind"], e["amount"]) for e in events] == [("legclose", 1400.0)]


def test_the_month_counts_it_but_not_as_roll_income():
    sold_on = date.today().replace(day=15)
    p = parse_rows(COLUMNS, _rows(sold_on=sold_on,
                                  opened=sold_on - timedelta(days=20)))[0]
    key = f"{sold_on.year:04d}-{sold_on.month:02d}"
    month = next(m for m in monthly_summary([p]) if m["month"] == key)
    assert month["realized_pl"] == 1400.0
    assert month["roll_income"] == 0.0


def test_the_risk_stops_being_the_width_of_the_spread():
    p = _position()
    # Cash-secured: 100 x 100 x 1 contract has to be there for the shares.
    assert p.assignment_cash_needed == 10000.0
    assert p.buying_power == 10000.0
    # Max loss is the whole strike less everything collected, not the $700 width.
    assert p.max_loss == 10000.0 - 300.0 - 1400.0
    assert not p.has_long_put

    # And the payoff follows the legs she actually holds: below the strike the
    # loss keeps going instead of flattening at the old long put.
    assert pl_at(p, 100.0) == 1700.0
    assert pl_at(p, 90.0) == 1700.0 - 1000.0
    assert pl_at(p, 80.0) == 1700.0 - 2000.0


def test_the_basis_is_the_strike_less_everything_collected():
    p = _position()
    # 100 strike, $1,700 collected on 100 shares.
    assert p.assignment_basis == 83.0


def test_intention_is_recorded_not_guessed():
    kept = _position(for_assignment=True)
    squeezed = _position(for_assignment=False)
    assert kept.awaiting_assignment
    # Same shape, different plan: she took the protection off to bank it and
    # still means to close. The exit rules must keep running there.
    assert not squeezed.awaiting_assignment
    assert exit_rules.evaluate(squeezed, EXIT, current_cost=2000.0,
                               underlying_price=95.0).action != "awaiting"


def test_the_story_reads_as_one_trade():
    p = _position()
    steps = story(p)
    assert [s["kind"] for s in steps] == ["open", "legclose"]
    assert "long" in steps[1]["what"].lower()
    assert steps[1]["cash"] == 1400.0
    assert steps[-1]["running"] == 1700.0


# ------------------------------------------------------------- the instruction
def test_the_exit_rules_stop_arguing_with_her_decision():
    p = _position()
    # Deep in the money: it costs far more than 2x the credit to buy the short
    # put back, which used to shout "stop loss - close now" at the exact moment
    # the plan is working.
    sig = exit_rules.evaluate(p, EXIT, current_cost=2600.0,
                              underlying_price=95.0)
    assert sig.action == "awaiting"
    assert "10,000" in " ".join(sig.notes)      # have the cash ready
    assert "83.00" in " ".join(sig.notes)       # the basis if assigned
    assert any("floor is gone" in n for n in sig.notes)


def test_the_21_day_clock_does_not_fire_on_a_trade_held_to_expiration():
    opened = date.today() - timedelta(days=25)
    rows = _rows(opened=opened, sold_on=date.today() - timedelta(days=1))
    p = parse_rows(COLUMNS, rows)[0]
    assert p.dte_left() == 5
    sig = exit_rules.evaluate(p, EXIT, current_cost=2600.0, underlying_price=95.0)
    assert sig.action == "awaiting"
    assert "5 days left" in sig.headline


def test_above_the_strike_it_says_the_put_may_simply_expire():
    p = _position()
    sig = exit_rules.evaluate(p, EXIT, current_cost=40.0, underlying_price=110.0)
    assert sig.action == "awaiting"
    assert sig.tone == "neutral"
    assert "expires" in sig.reason


# ------------------------------------------------------------------ the wheel
def test_the_assignment_button_appears_on_the_stripped_spread():
    p = _position()
    assert wheel.is_wheelable(p)
    # A lone short put can hand her the shares whether or not that was the
    # plan, so the button is there either way - what differs is the advice.
    assert wheel.is_wheelable(_position(for_assignment=False))


def test_assignment_carries_every_dollar_into_the_cost_basis():
    rows = _rows()
    rows.append(build_assign_row("T1", "XYZ", "Put Credit Spread", 100.0, 1,
                                 assigned_on=date.today()))
    p = parse_rows(COLUMNS, rows)[0]

    state = wheel.state_from(p, market_price=94.0)
    assert state is not None
    assert state.shares == 100
    # $300 credit + $1,400 from the long put = $17 a share off the 100 strike.
    assert state.premium_collected == 1700.0
    assert state.cost_basis == 83.0
    assert p.awaiting_assignment is False       # the shares are here now
    assert not p.legs                            # the put is gone, no call yet


def test_the_wheel_carries_on_from_there():
    """Sell a call against the shares, then close: one trade, one result."""
    rows = _rows()
    rows.append(build_assign_row("T1", "XYZ", "Put Credit Spread", 100.0, 1,
                                 assigned_on=date.today() - timedelta(days=1)))
    rows.append(build_roll_row("T1", "XYZ", "Put Credit Spread", 250.0,
                               new_strike=105.0,
                               new_expiration=date.today() + timedelta(days=30),
                               new_credit=250.0, rolled_on=date.today()))
    p = parse_rows(COLUMNS, rows)[0]
    state = wheel.state_from(p, market_price=99.0)
    assert state.call_strike == 105.0
    assert state.cost_basis == 100.0 - (1700.0 + 250.0) / 100
    assert p.banked_income == 1650.0            # 1400 leg + 250 call


def test_the_close_counts_the_leg_cash_once():
    rows = _rows()
    # Bought the short put back a week later for $2,600 - the whole trade lost
    # money, and the result has to say so exactly once.
    rows.append(build_close_row("T1", "XYZ", "Put Credit Spread",
                                exit_cost=2600.0, realized_pl=300.0 - 2600.0,
                                reason="Other", closed_on=date.today()))
    p = parse_rows(COLUMNS, rows)[0]
    assert p.realized_pl == -2300.0
    assert p.realized_total == -900.0           # -2,300 + the 1,400 banked
    steps = story(p)
    assert steps[-1]["running"] == -900.0


def test_a_short_leg_bought_back_is_the_same_event_the_other_way():
    """The call side of an iron condor taken off, put side left running."""
    rows = _rows(cash=-120.0, for_assignment=False)
    p = parse_rows(COLUMNS, rows)[0]
    assert p.leg_close_cash == -120.0
    # A fill that COST her does not inflate what she has collected.
    assert p.credit == 300.0


# ============================================================== on the page
# The forms above are only useful if they are on screen at the right moment,
# and the app's own numbers have to follow. These render the real My trades
# page through AppTest, the way tests/test_trades_tab.py does.
# ============================================================== 
import json

SPREAD_TRADE = "20260101-090000-XYZ"


def _open_spread_row(sold_long: bool = False, for_assignment: bool = True):
    """One open put credit spread in the log's own format, optionally with the
    long put already sold off. Invented numbers - this repo is public."""
    today = date.today()
    opened = today - timedelta(days=20)
    details = {
        "key": "put_credit_spread",
        "underlying_price": 112.0,
        "legs": [{"role": "short_put", "action": "sell", "type": "put",
                  "strike": 100.0, "delta": 0.25, "premium": 4.0, "qty": 1,
                  "dte": 30},
                 {"role": "long_put", "action": "buy", "type": "put",
                  "strike": 90.0, "delta": 0.12, "premium": 1.0, "qty": 1,
                  "dte": 30}],
        "open_cash": 300.0,
    }
    rows = [[
        opened.isoformat(), "XYZ", "Put Credit Spread", "100 / 90", 0.25, 30, 1,
        300.0, 700.0, 1000.0, "yes", "", SPREAD_TRADE, "open",
        (opened + timedelta(days=30)).isoformat(), "", "",
        json.dumps(details), "real",
    ]]
    if sold_long:
        rows.append(build_leg_close_row(
            SPREAD_TRADE, "XYZ", "Put Credit Spread", 1400.0, strike=90.0,
            option_type="put", side="buy", for_assignment=for_assignment,
            closed_on=today - timedelta(days=1), account="real"))
    return rows


def _page(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def test_the_form_is_on_the_card_of_an_open_credit_spread(app_with_rows):
    at = app_with_rows(_open_spread_row()).run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("Sell the long put" in l for l in labels), labels


def test_the_form_takes_a_fill_price_and_says_what_it_would_mean(app_with_rows):
    at = app_with_rows(_open_spread_row()).run()
    box = next(n for n in at.number_input if "SOLD the long put" in n.label)
    at = box.set_value(14.00).run()

    assert not at.exception
    body = _page(at)
    assert "1,400" in body                      # 14.00 x 100 x 1 contract
    assert "10,000" in body                     # what the shares would cost
    assert "83.00" in body                      # the basis they would land at
    # And the size of what she is taking on is said out loud, not implied.
    assert any("10,000" in str(w.value) for w in at.warning)


def test_a_big_fill_price_does_not_trip_the_typed_a_total_guard(app_with_rows):
    """A long put worth 22.00 a share is exactly the fill this form is for."""
    at = app_with_rows(_open_spread_row()).run()
    box = next(n for n in at.number_input if "SOLD the long put" in n.label)
    at = box.set_value(22.00).run()
    assert not any("looks like a dollar total" in str(w.value) for w in at.warning)


def test_the_card_switches_to_the_assignment_plan_once_it_is_recorded(app_with_rows):
    at = app_with_rows(_open_spread_row(sold_long=True)).run()
    assert not at.exception
    body = _page(at)
    assert "Waiting to be assigned" in body
    assert "83.00" in body                      # the basis it would land at
    # And the way to record the shares arriving is right there.
    labels = [e.label for e in at.expander]
    assert any("I was assigned" in l for l in labels), labels


def test_the_form_is_gone_once_there_is_no_long_put_left(app_with_rows):
    at = app_with_rows(_open_spread_row(sold_long=True)).run()
    labels = [e.label for e in at.expander]
    assert not any("Sell the long put" in l for l in labels), labels


def test_it_still_says_where_she_stands_in_dollars():
    """No "% kept" - she is not buying this back - but the dollar figure is
    what walking away today would cost, and that is worth seeing."""
    p = _position()
    sig = exit_rules.evaluate(p, EXIT, current_cost=2600.0, underlying_price=95.0)
    assert sig.pl_dollars == 1700.0 - 2600.0
    assert sig.profit_pct is None
