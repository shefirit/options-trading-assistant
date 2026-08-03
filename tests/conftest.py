"""Shared scaffolding for the tests that render the real app.

The plain smoke test runs with an EMPTY trade log, so it never draws a trade
card and never touches the forms that record what happened to a position. These
helpers seed one open PMCC so those forms exist on the page.

Every number here is INVENTED - this repo is public, so real positions never go
in source control. See the privacy rule in tools/import_history.py.
"""

import json
from datetime import date, timedelta

import pytest

from src.data.provider import DataProvider
from src.logging_tools.row import COLUMNS

PMCC = "Poor Man's Covered Call (PMCC)"


def pmcc_row(short_strike: float = 500.0, credit: float = 500.0,
             covered: bool = True):
    """One open PMCC row in the log's own format.

    The short call is dated a few weeks out from today so the position is live
    whenever this runs, rather than expiring the day the dates in it age.
    covered=False drops that call, which is what makes a PMCC "uncovered" - the
    state where the app offers the sell-a-call form instead of the roll form.
    """
    today = date.today()
    expiry = today + timedelta(days=32)
    legs = [
        {"role": "long_leaps", "action": "buy", "type": "call",
         "strike": 400.0, "delta": 0.82, "premium": 100.0, "qty": 1,
         "dte": 500},
    ]
    if covered:
        legs.append(
            {"role": "short_call", "action": "sell", "type": "call",
             "strike": short_strike, "delta": 0.30, "premium": credit / 100,
             "qty": 1, "dte": 32})
    details = {
        "key": "poor_mans_covered_call",
        "underlying_price": 480.0,
        "legs": legs,
        # $10,000 paid for the LEAPS, with the call credit taken against it.
        "open_cash": round(credit - 10000.0, 2),
    }
    return [
        (today - timedelta(days=7)).isoformat(), "SPY", PMCC,
        f"400 / {short_strike:g}", 0.30, 32, 1, credit, 9500.0, 0.0,
        "yes", "", "20260101-091500-SPY", "open", expiry.isoformat(),
        # Booked to the real account so the tab opens on the book holding it -
        # My trades starts on real money, and a paper-only row would sit behind
        # an account switch a test would then be silently testing instead.
        "", "", json.dumps(details), "real",
    ]


@pytest.fixture
def open_pmcc_row():
    """The seeder above, handed over as a fixture.

    tests/ is not a package, so a test module cannot import from conftest by
    name without the root conftest shadowing it. Fixtures are how pytest shares
    this properly.
    """
    return pmcc_row


def _app_with(monkeypatch, row):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(DataProvider, "create", classmethod(lambda cls: cls("demo")))
    from src.logging_tools import trade_logger
    monkeypatch.setattr(trade_logger, "fetch_all_rows",
                        lambda: (COLUMNS, [row], "local"))
    return AppTest.from_file("app.py", default_timeout=60)


@pytest.fixture
def app_with_one_pmcc(monkeypatch):
    """The app, offline, with that one PMCC open in My trades."""
    return _app_with(monkeypatch, pmcc_row())


@pytest.fixture
def app_with_uncovered_pmcc(monkeypatch):
    """The same PMCC with no call written against it, which is the only state
    where the sell-a-call form appears."""
    return _app_with(monkeypatch, pmcc_row(covered=False))
