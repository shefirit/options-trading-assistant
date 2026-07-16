"""The SOP spread width, applied at each name's own scale.

The rule is "indexes and ETFs $25-50, individual stocks $5-10". That is a
distance in POINTS, and it was written for an SPX-sized index. XSP is one tenth
of SPX, so the same 25 points buys a spread ten times wider in percentage terms
and risks SPX money on the index you would pick precisely to trade smaller.
"""

from __future__ import annotations

from src.data.chain import OptionChain, OptionContract
from src.engine.config_loader import default_spread_width, underlying_kind
from src.engine.models import OptionType
from src.engine.scanner import _auto_width


def test_the_sop_tiers_still_hold_for_normal_names():
    assert default_spread_width("SPX") == 25.0
    assert default_spread_width("NDX") == 25.0
    assert default_spread_width("RUT") == 25.0
    assert default_spread_width("SPY") == 25.0     # ETF, same tier
    assert default_spread_width("AAPL") == 5.0     # individual stock


def test_xsp_gets_the_rule_at_its_own_scale():
    """XSP is still an index - the tier just cannot be read in raw points."""
    assert underlying_kind("XSP") == "index"
    assert default_spread_width("XSP") == 5.0      # not 25
    assert default_spread_width("xsp") == 5.0      # case does not matter


def test_the_scanner_default_reads_the_same_config():
    """Two width defaults used to disagree: the scanner answered 25 for XSP
    because it is an index, while the form had its own copy of the rule."""
    assert _auto_width(753.0, "XSP") == 5.0
    assert _auto_width(7535.0, "SPX") == 25.0
    assert _auto_width(230.0, "AAPL") == 5.0


def _spx_like_chain(spot: float, step: float) -> OptionChain:
    """A put chain around `spot` on a `step` strike grid, priced so the credit
    scales with the strike distance - enough to compare widths honestly."""
    contracts = []
    k = spot * 0.90
    while k <= spot * 1.02:
        # crude but monotonic: further OTM is cheaper, in proportion to spot
        moneyness = (spot - k) / spot
        mid = max(spot * 0.012 * (1 - moneyness * 8), spot * 0.0004)
        contracts.append(OptionContract(
            option_type=OptionType.PUT, strike=round(k, 2),
            expiration="2026-08-21", dte=36,
            delta=-max(0.02, 0.45 - moneyness * 9),
            bid=round(mid * 0.98, 2), ask=round(mid * 1.02, 2),
            open_interest=5000, volume=500))
        k += step
    return OptionChain(underlying="TEST", underlying_price=spot,
                       contracts=contracts)


def test_xsp_at_its_width_is_the_spx_trade_at_a_tenth_the_size():
    """The point of the override: 5 on XSP must be the same TRADE as 50 on SPX,
    a tenth of the money - not a tenth of the trade."""
    spx = _spx_like_chain(7535.0, 5.0)
    xsp = _spx_like_chain(753.5, 1.0)

    spx_width = default_spread_width("SPX") * 2      # her usual top of the tier
    xsp_width = default_spread_width("XSP")

    # Same fraction of the index, which is what makes the risk comparable.
    assert abs(spx_width / 7535.0 - xsp_width / 753.5) < 0.0005
    # And a tenth of the dollars at stake.
    assert abs((spx_width * 100) / (xsp_width * 100) - 10.0) < 0.01


def test_the_old_behaviour_would_have_risked_ten_times_too_much_on_xsp():
    """Guards the regression: a plain 'index -> 25' rule puts $2,500 at risk on
    a 753 index instead of ~$500, and calls it the SOP."""
    old_rule = 25.0                       # what 'it is an index' used to answer
    now = default_spread_width("XSP")
    assert now < old_rule
    assert old_rule / now == 5.0
    # As a share of the index, the old answer was over 3% wide.
    assert old_rule / 753.5 > 0.03
    assert now / 753.5 < 0.007
