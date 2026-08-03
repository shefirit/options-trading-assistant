"""The roll form has to actually render for a PMCC.

Everything the form shows is built from the position: which call she is short,
what it sold for, what the roll did to her money. None of that is exercised by
the plain smoke test, which runs with an empty log and therefore never draws a
single trade card. This seeds one open PMCC and renders My trades with it.

Every number below is INVENTED - this repo is public, so real positions never
go in source control. See the privacy rule in tools/import_history.py.
"""

import json
from datetime import date, timedelta

import pytest

from src.data.provider import DataProvider
from src.engine import positions
from src.logging_tools.row import COLUMNS

PMCC = "Poor Man's Covered Call (PMCC)"


def _open_row(short_strike: float = 500.0, credit: float = 500.0):
    """One open PMCC row in the log's own format.

    The short call is dated a few weeks out from today so the position is live
    whenever this runs, rather than expiring the day the dates in it age.
    """
    today = date.today()
    expiry = today + timedelta(days=32)
    details = {
        "key": "poor_mans_covered_call",
        "underlying_price": 480.0,
        "legs": [
            {"role": "long_leaps", "action": "buy", "type": "call",
             "strike": 400.0, "delta": 0.82, "premium": 100.0, "qty": 1,
             "dte": 500},
            {"role": "short_call", "action": "sell", "type": "call",
             "strike": short_strike, "delta": 0.30, "premium": credit / 100,
             "qty": 1, "dte": 32},
        ],
        # $10,000 paid for the LEAPS, with the call credit taken against it.
        "open_cash": round(credit - 10000.0, 2),
    }
    return [
        (today - timedelta(days=7)).isoformat(), "SPY", PMCC,
        f"400 / {short_strike:g}", 0.30, 32, 1, credit, 9500.0, 0.0,
        "yes", "", "20260101-091500-SPY", "open", expiry.isoformat(),
        # Booked to the real account so the tab opens on the book holding it -
        # My trades starts on real money, and a paper-only row would sit behind
        # an account switch this test would then be silently testing instead.
        "", "", json.dumps(details), "real",
    ]


# ------------------------------------------------------------ the position itself
def test_the_seeded_row_is_a_trackable_debit_position():
    """Guards the fixture: if this stops parsing as an open PMCC, the render
    test below would pass by simply drawing nothing."""
    parsed = positions.parse_rows(COLUMNS, [_open_row()])
    assert len(parsed) == 1
    p = parsed[0]
    assert p.is_debit and not p.is_uncovered
    assert p.credit == 500.0


def test_the_short_call_leg_is_the_call_not_the_leaps():
    """A PMCC holds two long-dated calls' worth of confusion: the form must
    name the call she sold, never the LEAPS she bought."""
    import app

    p = positions.parse_rows(COLUMNS, [_open_row()])[0]
    leg = app._short_call_leg(p)
    assert leg is not None
    assert leg.strike == 500.0
    assert app._call_label(leg.strike, p.expiration).startswith("the 500 call")


def test_the_short_call_leg_is_none_when_nothing_is_written():
    import app

    p = positions.parse_rows(COLUMNS, [_open_row()])[0]
    p.legs = [l for l in p.legs if l.role != "short_call"]
    assert app._short_call_leg(p) is None


def test_signed_money_shows_its_sign():
    import app

    assert app._signed(200.0) == "+$200"
    assert app._signed(-200.0) == "-$200"
    assert app._signed(0.0) == "+$0"


# ------------------------------------------------------------------ the rendering
@pytest.fixture
def app_with_one_pmcc(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(DataProvider, "create", classmethod(lambda cls: cls("demo")))
    from src.logging_tools import trade_logger
    monkeypatch.setattr(trade_logger, "fetch_all_rows",
                        lambda: (COLUMNS, [_open_row()], "local"))
    return AppTest.from_file("app.py", default_timeout=60)


def _fill_the_net_way(at, net_price: float, sold_price: float):
    """Type a roll the way a one-order fill reads.

    Prices go in the way thinkorswim prints them (per share); the form does the
    x100. On one contract 2.00 means $200.
    """
    next(n for n in at.number_input if n.label == "Strike").set_value(560.0).run()
    next(n for n in at.number_input
         if "Credit price on your fill" in n.label).set_value(net_price).run()
    return next(n for n in at.number_input
                if "sold for by itself" in n.label).set_value(sold_price).run()


def test_my_trades_renders_the_roll_form_for_an_open_pmcc(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    assert not at.exception
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"
    labels = [x.label for x in at.expander]
    assert any("Roll or close the short call" in l for l in labels), \
        f"the roll form is missing - expanders were {labels}"


def test_the_defaults_are_usable_the_moment_the_form_opens(app_with_one_pmcc):
    """The first version opened with strike 0.00 and an expiration a month from
    TODAY - which, on a call expiring further out than that, its own validation
    then rejected. A roll moves out from the call she holds, not from today."""
    at = app_with_one_pmcc.run()
    assert not at.exception
    current_expiry = date.today() + timedelta(days=32)

    strike = next(n for n in at.number_input if n.label == "Strike")
    assert strike.value == 500.0, "strike should start at the one she holds"
    expires = next(d for d in at.date_input if d.label == "Expires")
    assert expires.value > current_expiry, \
        f"{expires.value} is not after the call being rolled ({current_expiry})"


def test_prices_are_typed_the_way_thinkorswim_prints_them(app_with_one_pmcc):
    """She reads @1.50 off a fill; every box used to demand 150. Typing the
    price must record the total, so the x100 is the app's job not hers."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=9.00)
    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "&#36;200" in body or "$200" in body, "2.00 on 1 contract is $200"


def test_a_dollar_total_typed_into_a_price_box_is_flagged(app_with_one_pmcc):
    """Her habit from every other form in the app is to type totals. Here that
    would record 100x the real money, so it has to be caught out loud."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=200.0, sold_price=9.00)
    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "looks like a dollar total" in warnings


def test_typing_a_roll_shows_what_it_did_to_her_money(app_with_one_pmcc):
    """The whole point of the rebuild: a roll that pays $200 while the call it
    closed finished $200 down must say BOTH, not just bank the credit."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=9.00)

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "What this roll does to your money" in body
    assert "700" in body      # the buy-back derived from the net
    assert "200" in body      # both the credit banked and the call's result


def test_the_two_price_way_round_derives_the_net_instead_of_asking(app_with_one_pmcc):
    """When she has both leg prices, the net is worked out rather than typed -
    so what the app banks can never disagree with the prices she read."""
    at = app_with_one_pmcc.run()
    next(n for n in at.number_input if n.label == "Strike").set_value(560.0).run()
    next(n for n in at.number_input
         if "paid to buy back" in n.label).set_value(7.00).run()
    next(n for n in at.number_input
         if "got for the new call" in n.label).set_value(9.00).run()
    at = next(c for c in at.checkbox
              if "Use these two prices" in c.label).set_value(True).run()

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "net of" in body
    assert "200" in body      # 900 - 700, the same fill read the other way


def test_an_impossible_pair_of_figures_is_refused(app_with_one_pmcc):
    """Typing the new call's premium too low implies the buy-back PAID her.
    That cannot happen, and logging it would corrupt the trade's history."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=1.00)

    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "cannot happen" in warnings
