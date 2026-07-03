"""Black-Scholes greeks sanity checks (no network)."""

from __future__ import annotations

from src.data import greeks


def test_atm_call_delta_near_half():
    g = greeks.compute(spot=100, strike=100, dte=30, iv=0.20, is_call=True)
    assert 0.45 <= g["delta"] <= 0.60


def test_atm_put_delta_near_minus_half():
    g = greeks.compute(spot=100, strike=100, dte=30, iv=0.20, is_call=False)
    assert -0.60 <= g["delta"] <= -0.40


def test_deep_itm_call_delta_near_one():
    g = greeks.compute(spot=100, strike=60, dte=30, iv=0.20, is_call=True)
    assert g["delta"] > 0.95


def test_otm_put_small_negative_delta():
    # Strike below spot = out-of-the-money put: a small negative delta.
    g = greeks.compute(spot=100, strike=90, dte=30, iv=0.20, is_call=False)
    assert -0.20 < g["delta"] < 0.0


def test_put_call_delta_parity():
    c = greeks.compute(100, 105, 45, 0.25, True)["delta"]
    p = greeks.compute(100, 105, 45, 0.25, False)["delta"]
    assert abs((c - p) - 1.0) < 1e-6   # call delta - put delta = 1


def test_expired_option_is_intrinsic():
    assert greeks.compute(100, 90, 0, 0.2, True)["delta"] == 1.0    # ITM call
    assert greeks.compute(100, 110, 0, 0.2, True)["delta"] == 0.0   # OTM call
