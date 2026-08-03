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


def _fill_the_net_way(at, net: float, sold: float):
    """Type a roll the way a one-order fill reads: strike, net, new premium."""
    next(n for n in at.number_input
         if "New short call strike" in n.label).set_value(560.0).run()
    next(n for n in at.number_input
         if "Net credit on the fill" in n.label).set_value(net).run()
    return next(n for n in at.number_input
                if "sold for on its own" in n.label).set_value(sold).run()


def test_my_trades_renders_the_roll_form_for_an_open_pmcc(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    assert not at.exception
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"
    labels = [x.label for x in at.expander]
    assert any("Roll or close the short call" in l for l in labels), \
        f"the roll form is missing - expanders were {labels}"


def test_the_roll_form_asks_how_the_fill_reads(app_with_one_pmcc):
    """The two-ways-round radio is the fix for the confusing part: a one-order
    roll never prints its legs, so she must be able to say so."""
    at = app_with_one_pmcc.run()
    assert not at.exception
    questions = [r.label for r in at.radio]
    assert any("What does your fill say?" == q for q in questions), \
        f"radios were {questions}"
    fill = next(r for r in at.radio if r.label == "What does your fill say?")
    assert any("net price" in o for o in fill.options)
    assert any("Two prices" in o for o in fill.options)


def test_typing_a_roll_shows_what_it_did_to_her_money(app_with_one_pmcc):
    """The whole point of the rebuild: a roll that pays $200 while the call it
    closed finished $200 down must say BOTH, not just bank the credit."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net=200.0, sold=900.0)

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "What this roll does to your money" in body
    assert "700" in body      # the buy-back derived from the net
    assert "200" in body      # both the credit banked and the call's result


def test_the_two_price_way_round_derives_the_net_instead_of_asking(app_with_one_pmcc):
    """When she has both leg prices, the net is worked out rather than typed -
    so what the app banks can never disagree with the prices she read."""
    at = app_with_one_pmcc.run()
    strike = next(n for n in at.number_input
                  if "New short call strike" in n.label)
    strike.set_value(560.0).run()
    at = next(r for r in at.radio if r.label == "What does your fill say?") \
        .set_value("Two prices - what I paid, and what I got").run()
    next(n for n in at.number_input if "PAID" in n.label).set_value(700.0).run()
    at = next(n for n in at.number_input if "GOT" in n.label).set_value(900.0).run()

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Net credit on the order" in body
    assert "200" in body      # 900 - 700, the same fill read the other way


def test_an_impossible_pair_of_figures_is_refused(app_with_one_pmcc):
    """Typing the new call's premium too low implies the buy-back PAID her.
    That cannot happen, and logging it would corrupt the trade's history."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net=200.0, sold=100.0)

    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "cannot happen" in warnings
