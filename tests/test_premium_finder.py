"""Tests for the premium finder (pure logic, synthetic chains)."""

from __future__ import annotations

from src.data.chain import OptionChain, OptionContract
from src.data.premium_finder import PremiumSnapshot, rank, snapshot
from src.engine.models import OptionType


def _put(strike, delta, mid, dte=30, iv=0.30):
    return OptionContract(option_type=OptionType.PUT, strike=strike, expiration="2026-08-01",
                          dte=dte, delta=delta, iv=iv, bid=mid - 0.05, ask=mid + 0.05)


def _call(strike, delta, mid, dte=30, iv=0.30):
    return OptionContract(option_type=OptionType.CALL, strike=strike, expiration="2026-08-01",
                          dte=dte, delta=delta, iv=iv, bid=mid - 0.05, ask=mid + 0.05)


def _chain(symbol, price, contracts):
    return OptionChain(underlying=symbol, underlying_price=price, contracts=contracts)


def test_snapshot_picks_030_delta_put_and_computes_yield():
    puts = [_put(190, -0.30, 3.80), _put(180, -0.15, 1.50), _put(200, -0.55, 8.0)]
    snap = snapshot("AAPL", _chain("AAPL", 200.0, puts), hv=0.25)
    assert snap.short_strike == 190
    assert abs(snap.short_delta - 0.30) < 1e-9
    assert snap.credit == 3.80
    assert snap.credit_dollars == 380
    # 3.80 / 190 = 2.0% for the cycle
    assert abs(snap.monthly_yield_pct - 2.0) < 0.01
    assert snap.annualized_yield_pct and snap.annualized_yield_pct > snap.monthly_yield_pct


def test_iv_hv_ratio_and_richness():
    puts = [_put(95, -0.30, 3.0, iv=0.40)]
    snap = snapshot("HOT", _chain("HOT", 100.0, puts), hv=0.20)  # IV 0.40 vs HV 0.20
    assert snap.iv_hv_ratio == 2.0
    assert snap.richness == "Rich"

    calm = snapshot("CALM", _chain("CALM", 100.0, [_put(95, -0.30, 1.0, iv=0.10)]), hv=0.20)
    assert calm.richness == "Thin"   # IV well below realized movement (ratio 0.5)


def test_richness_falls_back_to_iv_level_without_hv():
    hot = snapshot("X", _chain("X", 100.0, [_put(95, -0.30, 3.0, iv=0.45)]), hv=None)
    assert hot.richness == "Rich"


def test_no_options_returns_error():
    snap = snapshot("EMPTY", _chain("EMPTY", 100.0, []), hv=0.2)
    assert snap.error
    assert snap.monthly_yield_pct is None


def test_rank_puts_best_verdict_first_then_errors_last():
    a = snapshot("A", _chain("A", 100.0, [_put(95, -0.30, 1.0)]), hv=0.2)   # thin premium -> skip
    b = snapshot("B", _chain("B", 100.0, [_put(95, -0.30, 4.0, iv=0.5)]), hv=0.2)  # rich -> sell
    err = PremiumSnapshot(symbol="ERR", error="no data")
    ordered = rank([a, err, b])
    assert ordered[0].symbol == "B"      # best verdict first
    assert ordered[-1].symbol == "ERR"   # errors last


def test_verdict_rich_solid_is_sell():
    s = snapshot("AAPL", _chain("AAPL", 200.0, [_put(190, -0.30, 8.0, iv=0.5)]),
                 hv=0.25, trend="up", grade="A")
    assert s.richness == "Rich"
    assert s.verdict == "sell"


def test_verdict_weak_company_is_skip():
    s = snapshot("JUNK", _chain("JUNK", 100.0, [_put(95, -0.30, 4.0, iv=0.6)]),
                 hv=0.2, trend="up", grade="F")
    assert s.verdict == "skip"
    assert "weak" in s.verdict_reason.lower()


def test_verdict_thin_premium_is_skip():
    s = snapshot("CALM", _chain("CALM", 100.0, [_put(95, -0.30, 0.5, iv=0.12)]),
                 hv=0.20, trend="up", grade="A")
    assert s.richness == "Thin"
    assert s.verdict == "skip"


def test_verdict_earnings_makes_it_okay_not_sell():
    import datetime as dt
    today = dt.date(2026, 7, 1)
    s = snapshot("AAPL", _chain("AAPL", 200.0, [_put(190, -0.30, 8.0, iv=0.5, dte=30)]),
                 hv=0.25, trend="up", grade="A",
                 earnings_date=dt.date(2026, 7, 20), today=today)
    assert s.verdict == "okay"
    assert "earnings" in s.verdict_reason.lower()


def test_uptrend_plan_is_sell_puts_csp():
    chain = _chain("AAPL", 200.0, [_put(190, -0.30, 3.8), _call(210, 0.30, 3.5)])
    s = snapshot("AAPL", chain, hv=0.25, trend="up", monthly_bp=50_000)
    assert s.action == "Sell puts"
    assert s.strategy == "Cash Secured Put"
    assert s.capital_at_risk == 19000   # 190 x 100
    assert "Set aside" in s.risk_note
    # also computes the call side (covered call income)
    assert s.call_strike == 210
    assert s.call_credit_dollars == 350


def test_odds_and_cushion_computed():
    chain = _chain("AAPL", 200.0, [_put(190, -0.30, 3.8)])
    s = snapshot("AAPL", chain, hv=0.25, trend="up")
    assert s.pop == 70                       # 1 - 0.30 delta
    assert s.breakeven == 186.2              # 190 - 3.8
    # cushion = (200 - 186.2) / 200 = 6.9%
    assert abs(s.cushion_pct - 6.9) < 0.05


def test_liquidity_flag_on_wide_spread():
    wide = OptionContract(option_type=OptionType.PUT, strike=190, expiration="2026-08-01",
                          dte=30, delta=-0.30, iv=0.3, bid=2.0, ask=5.6, open_interest=10)
    s = snapshot("ILL", _chain("ILL", 200.0, [wide]), hv=0.25, trend="up")
    assert s.liquidity == "Thin"
    assert any("Hard to trade" in f for f in s.flags)


def test_earnings_before_expiry_flagged():
    import datetime as dt
    today = dt.date(2026, 7, 1)
    chain = _chain("AAPL", 200.0, [_put(190, -0.30, 3.8, dte=30)])
    s = snapshot("AAPL", chain, hv=0.25, trend="up",
                 earnings_date=dt.date(2026, 7, 20), today=today)
    assert s.earnings_before_expiry is True
    assert any("Earnings" in f for f in s.flags)
    assert "earnings" in s.recommendation.lower()


def test_grade_passed_through():
    s = snapshot("AAPL", _chain("AAPL", 200.0, [_put(190, -0.30, 3.8)]),
                 hv=0.25, trend="up", grade="A")
    assert s.grade == "A"


def test_expensive_uptrend_switches_to_pmcc():
    # 100 shares at $900 strike = $90k > $50k limit.
    chain = _chain("COST", 950.0, [_put(900, -0.30, 12.0), _call(1000, 0.30, 11.0)])
    s = snapshot("COST", chain, hv=0.2, trend="up", monthly_bp=50_000)
    assert s.action == "Sell puts"
    assert s.strategy == "Poor Man's Covered Call"
    assert "over your" in s.risk_note


def test_downtrend_plan_is_sell_calls():
    chain = _chain("WEAK", 100.0, [_put(95, -0.30, 3.0), _call(105, 0.30, 2.5)])
    s = snapshot("WEAK", chain, hv=0.2, trend="down", monthly_bp=50_000)
    assert s.action == "Sell calls"
    assert "Covered Call" in s.strategy
    assert "risky" in s.risk_note.lower() or "wait" in s.risk_note.lower()
