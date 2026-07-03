"""Scanner tests, run against the saved SPX fixture (no live connection)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine import scanner
from src.engine.models import OptionType
from src.engine.validator import validate_trade


def _call(strike, delta, mid, dte, iv=0.25):
    return OptionContract(option_type=OptionType.CALL, strike=strike, expiration="2026-08-01",
                          dte=dte, delta=delta, iv=iv, bid=mid - 0.1, ask=mid + 0.1,
                          open_interest=800)


def _stock_call_chain(symbol="AAPL", price=200.0):
    """A US-style stock chain with calls at a few deltas/expirations."""
    contracts = []
    for dte in (21, 28, 35):
        for strike, delta in [(205, 0.40), (210, 0.30), (215, 0.22), (220, 0.15)]:
            contracts.append(_call(strike, delta, mid=max(0.5, (210 - strike) * 0.1 + 3), dte=dte))
    return OptionChain(underlying=symbol, underlying_price=price, contracts=contracts)

FIXTURE = Path(__file__).parent / "fixtures" / "spx_chain.json"
SPY_FIXTURE = Path(__file__).parent / "fixtures" / "spy_chain.json"


@pytest.fixture(scope="module")
def chain() -> OptionChain:
    return OptionChain.from_json(FIXTURE)


@pytest.fixture(scope="module")
def spy_chain() -> OptionChain:
    return OptionChain.from_json(SPY_FIXTURE)


def test_fixture_loads(chain):
    assert chain.underlying == "SPX"
    assert chain.underlying_price == 5100.0
    assert 45 in chain.dtes()


def test_put_credit_spread_scan_respects_sop(chain):
    cands = scanner.scan("put_credit_spread", chain, width=25, max_candidates=10)
    assert cands, "expected at least one put credit spread candidate"
    for c in cands:
        assert c.credit > 0
        assert 21 <= c.dte <= 35
        assert c.max_loss > 0
        if c.fits_sop:
            # Fully-fitting candidates obey the delta rule and pass the checklist.
            assert c.short_delta <= 0.10 + 1e-9
            assert validate_trade(c.trade).passed
        else:
            # Near-misses are only slightly over the limit, and say so.
            assert 0.10 < c.short_delta <= 0.13 + 1e-9
            assert "over" in c.note.lower()


def test_call_credit_spread_scan(chain):
    cands = scanner.scan("call_credit_spread", chain, width=25, max_candidates=5)
    assert cands
    # SOP-passing candidates come first and obey the delta rule.
    assert cands[0].fits_sop
    assert all(c.short_delta <= 0.10 + 1e-9 for c in cands if c.fits_sop)


def test_near_misses_are_flagged_and_ranked_after_fits(chain):
    cands = scanner.scan("put_credit_spread", chain, width=25, max_candidates=15)
    flags = [c.fits_sop for c in cands]
    # Once the first near-miss appears, no SOP-passing trade may follow it.
    if False in flags:
        first_miss = flags.index(False)
        assert all(f is False for f in flags[first_miss:])


def test_dte_slider_changes_expiration(chain):
    # The bug Rita reported: picking a different DTE must actually change the scan.
    low = scanner.scan("put_credit_spread", chain, width=25, target_dte=21)
    high = scanner.scan("put_credit_spread", chain, width=25, target_dte=35)
    assert low and high
    assert all(c.dte == 21 for c in low)
    assert all(c.dte == 35 for c in high)


def test_iron_condor_scan_has_four_legs(chain):
    cands = scanner.scan("iron_condor", chain, width=25, max_candidates=5)
    assert cands
    assert all(len(c.trade.legs) == 4 for c in cands)
    assert all(c.credit > 0 for c in cands)


def test_cash_secured_put_scan_on_us_style(spy_chain):
    # CSP needs a US-style name you can own (SPY), not cash-settled SPX.
    cands = scanner.scan("cash_secured_put", spy_chain, max_candidates=5)
    assert cands
    for c in cands:
        assert c.buying_power > 0            # cash reserved to buy the shares
        assert c.buying_power <= 50000       # 1 contract stays under the monthly BP limit
        assert len(c.trade.legs) == 1        # single short put


def test_cash_secured_put_on_spx_returns_nothing(chain):
    # SPX is cash-settled, so a CSP cannot apply - scanner should find no candidates.
    assert scanner.scan("cash_secured_put", chain, max_candidates=5) == []


def test_candidates_ranked_by_return_on_risk(chain):
    cands = scanner.scan("put_credit_spread", chain, width=25, max_candidates=10)
    # Within each group (fits first, then near-misses), richest premium first.
    keys = [(c.fits_sop, c.return_on_risk) for c in cands]
    assert keys == sorted(keys, reverse=True)


def test_unsupported_family_raises(chain):
    with pytest.raises(ValueError):
        scanner.scan("covered_call_model_1", chain)


# ---------- focused "best setups" scan ----------

def test_scan_setups_returns_few_across_dtes(chain):
    setups = scanner.scan_setups("put_credit_spread", chain, width=25, max_setups=4)
    assert 1 <= len(setups) <= 4
    dtes = [c.dte for c in setups]
    # a spread of expirations, all inside the 21-44 window, sorted soonest first
    assert dtes == sorted(dtes)
    assert all(21 <= d <= 44 for d in dtes)
    assert len(set(dtes)) == len(dtes)   # one per expiration


def test_scan_setups_short_leg_at_sop_delta(chain):
    # SOP short-leg limit is 0.10; setups should sit just under it, not far below.
    setups = scanner.scan_setups("put_credit_spread", chain, width=25)
    assert setups
    for c in setups:
        assert c.short_delta <= 0.10 + 1e-9
        assert c.short_delta >= 0.06        # near the target, not a far-OTM 0.03
        assert c.fits_sop


def test_scan_setups_csp_uses_030_delta(spy_chain):
    setups = scanner.scan_setups("cash_secured_put", spy_chain, max_setups=3)
    assert setups
    for c in setups:
        assert 0.22 <= c.short_delta <= 0.30 + 1e-9   # near the 0.30 SOP target
        assert len(c.trade.legs) == 1


def test_scan_setups_iron_condor_four_legs(chain):
    setups = scanner.scan_setups("iron_condor", chain, width=25, max_setups=3)
    assert setups
    assert all(len(c.trade.legs) == 4 for c in setups)


def test_covered_calls_are_now_scannable():
    assert scanner.can_scan("covered_call_model_1")
    assert scanner.can_scan("poor_mans_covered_call")


def test_covered_call_scan_short_call_at_030():
    setups = scanner.scan_setups("covered_call_model_1", _stock_call_chain(price=200.0),
                                 max_setups=3)
    assert setups
    for c in setups:
        assert len(c.trade.legs) == 1                     # just the short call
        assert 0.22 <= c.short_delta <= 0.30 + 1e-9       # near the 0.30 SOP target
        assert c.credit > 0                               # premium income
        assert c.buying_power == pytest.approx(20000)     # 100 shares x $200
        assert validate_trade(c.trade).passed


def test_covered_call_scan_empty_when_shares_exceed_bp():
    # 100 shares at $900 = $90k, over the $50k monthly limit -> no setups.
    setups = scanner.scan_setups("covered_call_model_1", _stock_call_chain(price=900.0))
    assert setups == []


def test_pmcc_scan_pairs_leaps_with_short_call():
    near = _stock_call_chain(price=200.0)
    leaps = OptionChain(underlying="AAPL", underlying_price=200.0,
                        contracts=[_call(150, 0.85, mid=56.0, dte=210)])
    setups = scanner.scan_setups("poor_mans_covered_call", near, max_setups=2, leaps_chain=leaps)
    assert setups
    for c in setups:
        assert len(c.trade.legs) == 2                     # LEAPS + short call
        assert c.credit > 0                               # short-call income
        assert c.buying_power == pytest.approx(5600, abs=300)   # LEAPS cost ~ $5,600


def test_pmcc_scan_empty_without_leaps():
    assert scanner.scan_setups("poor_mans_covered_call", _stock_call_chain()) == []
