"""The LEAPS put - selling a cash secured put a year or more out.

Added to her SOP on 2026-08-25. Mechanically it is the Cash Secured Put she
already trades, stretched from 30-45 days to 300-550, so most of the machinery
is shared and these tests pin the three places it deliberately differs:

  1. It scans the far end of the chain, where her other short-put strategies
     have nothing listed at all.
  2. It has NO delta limit. Her page declines to set one - "treat ~0.10-0.15 as
     a rough starting reference, not a hard rule" - so the band is a scan target
     and a reported comment, and nothing is ever failed on it. A test here
     guards against a future session quietly promoting it to a rule.
  3. It has NO profit target, stop or time exit, because her page states none.
     The strategy is passive by design.

No network: everything runs against a hand-built far-dated chain.
"""

from __future__ import annotations

import pytest

from src.data.chain import OptionChain, OptionContract
from src.engine import scanner
from src.engine.config_loader import get_strategy
from src.engine.models import CheckStatus, OptionType
from src.engine.validator import validate_trade


def _put(strike, delta, mid, dte):
    return OptionContract(option_type=OptionType.PUT, strike=strike, expiration="2027-12-17",
                          dte=dte, delta=-abs(delta), iv=0.42, bid=mid - 0.5, ask=mid + 0.5,
                          open_interest=400)


@pytest.fixture
def leaps_chain() -> OptionChain:
    """A far-dated chain shaped like the real thing: a handful of expirations a
    year or more out, strikes running from near the money down to deep OTM.

    The numbers are modelled on her page's own MU example - roughly a $930
    stock with a $500-ish strike at about 0.11 delta paying about $60 a share.
    """
    contracts = []
    for dte in (388, 479, 514):
        for strike, delta, mid in [(900, 0.44, 150.0), (700, 0.26, 95.0),
                                   (560, 0.16, 68.0), (530, 0.12, 60.0),
                                   (450, 0.08, 42.0)]:
            contracts.append(_put(strike, delta, mid, dte))
    return OptionChain(underlying="MU", underlying_price=929.0, contracts=contracts)


# ------------------------------------------------------------------ the rules
def test_it_is_a_cash_secured_put_with_a_year_on_the_clock():
    s = get_strategy("leaps_put")
    assert s["family"] == "single_leg"
    assert s["underlying_style"] == "us"          # you cannot be assigned an index
    assert s["sizing"]["max_loss_basis"] == "cash_secured"
    assert (s["entry"]["dte_min"], s["entry"]["dte_max"]) == (300, 550)


def test_no_delta_limit_is_configured():
    """Her page declines to fix a delta, so the app must not either.

    If this fails because someone added short_leg_delta_max to leaps_put, read
    her Notion page before "fixing" the test: a limit there turns a number she
    called a starting reference into a rule that refuses trades.
    """
    entry = get_strategy("leaps_put")["entry"]
    assert "short_leg_delta_max" not in entry
    assert entry["short_leg_delta_target"] == 0.12
    assert (entry["delta_reference_min"], entry["delta_reference_max"]) == (0.10, 0.15)


def test_no_exit_rules_are_invented():
    """Passive by design - her page states no target, no stop, no time exit."""
    exit_cfg = get_strategy("leaps_put")["exit"]
    assert exit_cfg["accepts_assignment"] is True
    for invented in ("profit_target_pct", "stop_loss_multiple", "time_exit_dte"):
        assert invented not in exit_cfg


# ------------------------------------------------------------------ the scan
def test_scan_finds_setups_a_year_or_more_out(leaps_chain):
    cands = scanner.scan_setups("leaps_put", leaps_chain, contracts=1, max_setups=10)

    assert len(cands) == 3                        # one per expiration in the window
    for c in cands:
        assert 300 <= c.dte <= 550
        leg = c.trade.legs[0]
        assert leg.option_type == OptionType.PUT
        assert leg.action.value == "sell"
        # Aimed at the reference band, not at the CSP's 0.30.
        assert leg.abs_delta <= 0.16
        assert c.credit > 0


def test_the_cash_tied_up_is_the_full_strike(leaps_chain):
    """Cash secured means cash secured - the whole strike x 100, less the credit."""
    cand = scanner.scan_setups("leaps_put", leaps_chain, contracts=1, max_setups=1)[0]
    strike = cand.trade.legs[0].strike
    assert cand.buying_power == pytest.approx(strike * 100 - cand.credit, rel=0.02)


def test_the_csp_scan_is_untouched(leaps_chain):
    """The shared short-put scanner still enforces the CSP's real delta limit."""
    entry = get_strategy("cash_secured_put")["entry"]
    assert entry["short_leg_delta_max"] == 0.30


# ------------------------------------------------------------- the checklist
def _first_trade(chain):
    return scanner.scan_setups("leaps_put", chain, contracts=1, max_setups=1)[0].trade


def test_delta_is_reported_never_failed(leaps_chain):
    trade = _first_trade(leaps_chain)
    report = validate_trade(trade, existing_month_bp=0.0)

    delta_checks = [r for r in report.results if "reference" in r.name.lower()]
    assert len(delta_checks) == 1
    assert delta_checks[0].status is CheckStatus.PASS
    assert "0.10-0.15" in delta_checks[0].expected


def test_a_delta_outside_the_band_still_passes(leaps_chain):
    """The whole point: outside the reference is a comment, not a refusal."""
    from src.engine import rules

    trade = _first_trade(leaps_chain)
    trade.legs[0].delta = -0.28          # well above the 0.15 reference

    check = rules.check_delta_reference(trade, 0.10, 0.15)
    assert check.status is CheckStatus.INFO      # never FAIL
    assert "0.280" in check.actual

    report = validate_trade(trade, existing_month_bp=0.0)
    assert not any(r.status is CheckStatus.FAIL and "reference" in r.name.lower()
                   for r in report.results)


def test_the_window_check_rejects_a_short_dated_put(leaps_chain):
    """A 45-day put is a Cash Secured Put, and logging it as this is a mistake
    worth catching - the capital is committed for a completely different span."""
    trade = _first_trade(leaps_chain)
    trade.legs[0].dte = 45

    report = validate_trade(trade, existing_month_bp=0.0)
    dte_checks = [r for r in report.results if "days to expiration" in r.name.lower()]
    assert dte_checks and dte_checks[0].status is CheckStatus.FAIL


# ------------------------------------------------------------- after the fill
def test_assignment_button_is_offered():
    """Assignment is the accepted outcome here, same as her CSP and Wheel - her
    page says so outright ("the same CSP -> Wheel pattern")."""
    from src.engine import wheel

    class _Leg:
        action = "sell"
        option_type = "put"

    class _Position:
        strategy_key = "leaps_put"
        status = "open"
        assigned_strike = None
        awaiting_assignment = False
        legs = [_Leg()]

    assert wheel.is_wheelable(_Position()) is True
