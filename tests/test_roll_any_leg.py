"""Rolling is not a call-only affair any more.

Her SOP has always said to roll a threatened cash secured put or credit spread
down and out for a credit, and the app has always TOLD her to - "the trade is
in trouble, your SOP says roll down and out". There was simply no way to record
it: the roll form only ever appeared on a PMCC, and every roll row written was
assumed to be a call. Closing and re-logging was the only route, and it turned
one put rolled four times into five unrelated trades with four losses between
them and no sign they were the same position.

These cover the replay (does the log still describe what she holds?), the money
that has to be behind it, and the form actually appearing on the card.

Every number here is INVENTED - this repo is public. See tests/conftest.py.
"""

from datetime import date, timedelta

import pytest

from src.engine import positions
from src.engine.models import Action, OptionType
from src.logging_tools.row import COLUMNS, build_roll_row


def _rolled(open_row, **kwargs):
    """The log after one roll: the open row, then the roll row it produced."""
    trade_id = open_row[12]
    kwargs.setdefault("underlying", open_row[1])
    kwargs.setdefault("strategy_name", open_row[2])
    roll = build_roll_row(trade_id, kwargs.pop("underlying"),
                          kwargs.pop("strategy_name"), **kwargs)
    return positions.parse_rows(COLUMNS, [open_row, roll])[0]


# --------------------------------------------------------------- a cash secured put
def test_rolling_a_csp_moves_the_put_she_is_short(open_csp_row):
    """The whole point. Roll the 100 put down to 95 and out a month, and the
    app has to be watching the 95 put - not still counting down to a contract
    she bought back."""
    out = date.today() + timedelta(days=60)
    p = _rolled(open_csp_row(strike=100.0, credit=200.0),
                cash=90.0, new_strike=95.0, new_expiration=out,
                new_credit=260.0, option_type="put")

    assert len(p.legs) == 1
    leg = p.legs[0]
    assert leg.option_type == OptionType.PUT and leg.action == Action.SELL
    assert leg.strike == 95.0
    assert p.expiration == out
    assert p.credit == 260.0, "the 50% target measures against the NEW put"


def test_the_roll_credit_is_banked_on_the_day_it_landed(open_csp_row):
    p = _rolled(open_csp_row(), cash=90.0, new_strike=95.0,
                new_expiration=date.today() + timedelta(days=60),
                new_credit=260.0, option_type="put")
    assert p.roll_income == 90.0
    assert p.status == "open", "a roll must never end the trade"


def test_rolling_a_csp_down_frees_the_collateral_it_no_longer_needs(open_csp_row):
    """The reason this is not cosmetic. A 100-strike put ties up $10,000; rolled
    down to 95 it ties up $9,500. Both figures feed her monthly buying-power
    guardrail, and the log used to keep reporting the strike she left behind."""
    p = _rolled(open_csp_row(strike=100.0, credit=200.0),
                cash=90.0, new_strike=95.0,
                new_expiration=date.today() + timedelta(days=60),
                new_credit=260.0, option_type="put")

    assert p.buying_power == 9500.0
    # What she can still lose: the strike less every dollar collected so far -
    # the opening credit and the roll's own.
    assert p.max_loss == pytest.approx(9500.0 - 200.0 - 90.0)


def test_a_put_roll_never_invents_a_call(open_csp_row):
    """Read as a call, the replay finds no short call, appends a brand new one,
    and the trade silently grows a leg she never sold."""
    p = _rolled(open_csp_row(), cash=90.0, new_strike=95.0,
                new_expiration=date.today() + timedelta(days=60),
                new_credit=260.0, option_type="put")
    assert not [l for l in p.legs if l.option_type == OptionType.CALL]


# ------------------------------------------------------------- a put credit spread
def test_rolling_a_spread_moves_the_protection_with_it(open_put_spread_row):
    """A vertical rolls as ONE order with four legs. Move the short leg alone
    and the app prices a 90/95 spread she does not hold, with her protection
    stranded at an expiration that has passed."""
    out = date.today() + timedelta(days=60)
    p = _rolled(open_put_spread_row(short_strike=100.0, long_strike=95.0),
                cash=60.0, new_strike=95.0, new_expiration=out,
                new_credit=180.0, option_type="put", new_long_strike=90.0)

    shorts = [l for l in p.legs if l.action == Action.SELL]
    longs = [l for l in p.legs if l.action == Action.BUY]
    assert [l.strike for l in shorts] == [95.0]
    assert [l.strike for l in longs] == [90.0]
    assert p.leg_expiration(shorts[0]) == out
    assert p.leg_expiration(longs[0]) == out, \
        "both legs of a vertical expire together"


def test_rolling_a_spread_wider_raises_the_risk_it_reports(open_put_spread_row):
    """Roll a 5-wide spread into a 10-wide one and twice as much is on the
    line. The log reported the old width until the strikes were re-read."""
    p = _rolled(open_put_spread_row(short_strike=100.0, long_strike=95.0,
                                    credit=150.0),
                cash=60.0, new_strike=95.0,
                new_expiration=date.today() + timedelta(days=60),
                new_credit=250.0, option_type="put", new_long_strike=85.0)

    assert p.max_loss == pytest.approx(1000.0 - 150.0 - 60.0)
    assert p.buying_power == p.max_loss


# ------------------------------------------------------------------ old rows still read
def test_a_roll_row_with_no_side_recorded_is_still_a_call(open_pmcc_row):
    """Every roll in her log was written before puts could be rolled, and none
    of them says which side. They were all calls, and they have to stay calls."""
    row = build_roll_row("20260101-091500-SPY", "SPY", "PMCC", 200.0,
                         new_strike=560.0,
                         new_expiration=date.today() + timedelta(days=60),
                         new_credit=900.0)
    row[17] = ""            # Details JSON, the way older versions wrote it
    p = positions.parse_rows(COLUMNS, [open_pmcc_row(), row])[0]

    short_calls = [l for l in p.legs
                   if l.action == Action.SELL and l.option_type == OptionType.CALL]
    assert [l.strike for l in short_calls] == [560.0]


def test_a_pmcc_call_roll_leaves_the_leaps_where_it_is(open_pmcc_row):
    """The LEAPS is a bought call too. Dragging it along with the call written
    against it would rewrite the trade's whole cost basis."""
    p = _rolled(open_pmcc_row(), cash=200.0, new_strike=560.0,
                new_expiration=date.today() + timedelta(days=60),
                new_credit=900.0, option_type="call")
    leaps = [l for l in p.legs if l.action == Action.BUY]
    assert [l.strike for l in leaps] == [400.0]
    assert p.max_loss == 9500.0, "a PMCC's logged risk is not recomputed"


# ----------------------------------------------------------------- the row itself
def test_the_row_records_which_side_rolled():
    row = build_roll_row("t", "SOFI", "CSP", 90.0, new_strike=95.0,
                         new_expiration=date.today(), new_credit=260.0,
                         option_type="put")
    assert '"type":"put"' in row[17]
    assert "put" in row[11], f"the note should say what happened: {row[11]}"


def test_only_the_short_strike_goes_in_the_legs_column():
    """The replay reads that cell as one number. A "95 / 90" there parses as
    nothing, which it would then take for "bought it back and wrote nothing" -
    deleting a leg she still holds."""
    row = build_roll_row("t", "SOFI", "PCS", 60.0, new_strike=95.0,
                         new_expiration=date.today(), new_credit=180.0,
                         option_type="put", new_long_strike=90.0)
    assert row[3] == "95"
    assert '"long_strike":90' in row[17].replace(".0", "")


# --------------------------------------------------------------------- on the card
def test_the_card_offers_to_roll_a_cash_secured_put(app_with_one_csp):
    """It offered nothing but Close before. The exit rules were already telling
    her to roll it."""
    at = app_with_one_csp.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert any("Roll it" in l for l in labels), \
        f"no roll form on a CSP - expanders were {labels}"


def test_the_csp_form_talks_about_puts_not_calls(app_with_one_csp):
    """A form that says "the call you sold" over a put she sold is a form she
    cannot trust to be recording the right thing."""
    at = app_with_one_csp.run()
    assert not at.exception
    labels = [n.label for n in at.number_input]
    assert any("put" in (l or "").lower() for l in labels), \
        f"nothing on the form names the put: {labels}"
    assert not any("call" in (l or "").lower() for l in labels), \
        f"a call is named on a put-only trade: {labels}"


def test_a_spread_asks_for_both_strikes(app_with_one_put_spread):
    """Only asking for the short one would leave her old protection behind."""
    at = app_with_one_put_spread.run()
    assert not at.exception
    labels = [n.label for n in at.number_input]
    assert any("Strike you SOLD" in (l or "") for l in labels), labels
    assert any("Strike you BOUGHT" in (l or "") for l in labels), labels


def test_a_csp_is_not_offered_the_buy_it_back_path(app_with_one_csp):
    """Buying back the only leg of a CSP leaves nothing - that is a CLOSE, and
    recording it as a roll would leave a live trade in the book that no longer
    exists."""
    at = app_with_one_csp.run()
    assert not at.exception
    assert not [r for r in at.radio if r.label == "What did you do?"]
