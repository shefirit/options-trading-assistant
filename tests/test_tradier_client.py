"""Tradier client parsing, with the HTTP layer mocked (no network, no token)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data import tradier_client as td
from src.engine.models import OptionType

_TODAY = date.today()
_EXP_NEAR = (_TODAY + timedelta(days=30)).isoformat()
_EXP_FAR = (_TODAY + timedelta(days=60)).isoformat()


def _fake_request(path, params):
    if path.endswith("/expirations"):
        return {"expirations": {"date": [_EXP_NEAR, _EXP_FAR]}}
    if path.endswith("/quotes"):
        return {"quotes": {"quote": {"symbol": params["symbols"], "last": 500.0}}}
    if path.endswith("/chains"):
        exp = params["expiration"]
        return {"options": {"option": [
            {"option_type": "put", "strike": 480, "bid": 2.0, "ask": 2.2,
             "volume": 50, "open_interest": 900,
             "greeks": {"mid_iv": 0.20, "delta": -0.25, "gamma": 0.01,
                        "theta": -0.05, "vega": 0.1}},
            {"option_type": "call", "strike": 520, "bid": 1.8, "ask": 2.0,
             "volume": 40, "open_interest": 700,
             "greeks": {"mid_iv": 0.19, "delta": 0.22}},
        ]}}
    raise AssertionError(f"unexpected path {path}")


@pytest.fixture(autouse=True)
def _mock(monkeypatch):
    monkeypatch.setattr(td, "_request", _fake_request)
    monkeypatch.setattr(td, "get_key", lambda: "TESTTOKEN")


def test_get_price():
    assert td.get_price("SPY") == 500.0


def test_index_symbol_strips_caret():
    assert td.tradier_symbol("^SPX") == "SPX"
    assert td.tradier_symbol("spy") == "SPY"


def test_get_option_chain_filters_by_dte_window():
    chain = td.get_option_chain("SPY", from_dte=20, to_dte=40)   # only the ~30 DTE expiry
    assert chain.underlying == "SPY"
    assert chain.underlying_price == 500.0
    dtes = {c.dte for c in chain.contracts}
    assert dtes == {30}
    assert len(chain.contracts) == 2


def test_greeks_recomputed_from_iv():
    chain = td.get_option_chain("SPY", from_dte=20, to_dte=40)
    put = next(c for c in chain.contracts if c.option_type == OptionType.PUT)
    # delta recomputed via Black-Scholes from IV -> negative for a put, near the money-ish
    assert put.delta < 0
    assert 0.0 < put.abs_delta < 0.5
    assert put.iv == 0.20
    assert put.mid == pytest.approx(2.1)


def test_get_expiration_chain_picks_nearest():
    chain = td.get_expiration_chain("SPY", target_dte=55)   # nearest is the 60-DTE expiry
    assert {c.dte for c in chain.contracts} == {60}
