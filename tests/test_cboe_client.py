"""CBOE client parsing, with the HTTP layer mocked (no network, no key)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data import cboe_client as cboe
from src.engine.models import OptionType

_TODAY = date.today()
_NEAR = _TODAY + timedelta(days=30)
_FAR = _TODAY + timedelta(days=60)


def _occ(root: str, d: date, cp: str, strike: float) -> str:
    return f"{root}{d:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


def _fake_fetch(underlying):
    return {"data": {
        "current_price": 500.0,
        "options": [
            {"option": _occ("SPY", _NEAR, "P", 480), "bid": 2.0, "ask": 2.2, "iv": 0.20,
             "delta": -0.25, "gamma": 0.01, "theta": -0.05, "vega": 0.1,
             "volume": 50, "open_interest": 900},
            {"option": _occ("SPY", _NEAR, "C", 520), "bid": 1.8, "ask": 2.0, "iv": 0.19,
             "delta": 0.22, "gamma": 0.01, "theta": -0.04, "vega": 0.1,
             "volume": 40, "open_interest": 700},
            {"option": _occ("SPY", _FAR, "P", 470), "bid": 3.0, "ask": 3.3, "iv": 0.22,
             "delta": -0.20, "gamma": 0.01, "theta": -0.03, "vega": 0.2,
             "volume": 10, "open_interest": 300},
        ],
    }}


@pytest.fixture(autouse=True)
def _mock(monkeypatch):
    monkeypatch.setattr(cboe, "_fetch", _fake_fetch)


def test_index_symbols_get_underscore():
    assert cboe.cboe_symbol("SPX") == "_SPX"
    assert cboe.cboe_symbol("^NDX") == "_NDX"
    assert cboe.cboe_symbol("SPY") == "SPY"
    assert cboe.cboe_symbol("AAPL") == "AAPL"


def test_parse_occ():
    exp, otype, strike = cboe._parse_occ("SPY260706C00500000")
    assert exp == "2026-07-06"
    assert otype == OptionType.CALL
    assert strike == 500.0


def test_get_option_chain_window_and_greeks():
    chain = cboe.get_option_chain("SPY", from_dte=20, to_dte=40)
    assert chain.underlying == "SPY"
    assert chain.underlying_price == 500.0
    assert {c.dte for c in chain.contracts} == {30}      # far (60) excluded
    put = next(c for c in chain.contracts if c.option_type == OptionType.PUT)
    assert put.delta == -0.25          # greeks come straight from CBOE
    assert put.iv == 0.20
    assert put.strike == 480.0
    assert put.mid == pytest.approx(2.1)


def test_get_expiration_chain_picks_nearest():
    chain = cboe.get_expiration_chain("SPY", target_dte=58)   # nearest is the 60-DTE one
    assert {c.dte for c in chain.contracts} == {60}
