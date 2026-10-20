"""The two entry layers added after her CRWD condor (2026-10-20): where the sold
strike sits (location) and whether a move is still running into it (timing),
plus her five entry questions built from the layers.

Her ask: "i think i need better entering points for spreads, not only sticking
to managing rules". Her calls on CRWD went on six days after a 20% earnings
gap, at the edge of a +-$35 expected move, and the stock ran 40% higher.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from src.engine import candidate as c
from src.engine.candidate import CALL, PUT, ChainRead, Layer


def _bars(closes, pad=0.5):
    return [v + pad for v in closes], [v - pad for v in closes]


def _quiet(n=80, base=100.0):
    """A stock wobbling +-1% around one price - nothing to sell against."""
    out, v = [], base
    for i in range(n):
        v *= 1.01 if i % 2 == 0 else 1 / 1.01
        out.append(round(v, 4))
    return out


# --------------------------------------------------------------- expected move
def test_expected_move_is_one_standard_deviation_in_dollars():
    assert c.expected_move(100.0, 30.0, 45) == pytest.approx(100 * 0.30 * math.sqrt(45 / 365))


def test_crwd_expected_move_on_entry_day():
    """$203.42, about 50% volatility, 44 days: the +-$35 her 240 sat beside."""
    assert c.expected_move(203.42, 50.0, 44) == pytest.approx(35.31, abs=0.01)


@pytest.mark.parametrize("spot,vol,dte", [(None, 30, 45), (100, None, 45),
                                          (100, 30, 0), (100, 0, 45)])
def test_expected_move_needs_all_three(spot, vol, dte):
    assert c.expected_move(spot, vol, dte) is None


# ------------------------------------------------------------------ location
def _with_dip(n=120, dip_to=90.0, at=50):
    """Flat at 100 with one clean V down to `dip_to` - a swing low (support)."""
    closes = [100.0] * n
    for off, depth in ((-2, 0.6), (-1, 0.85), (0, 1.0), (1, 0.85), (2, 0.6)):
        closes[at + off] = 100 - (100 - dip_to) * depth
    return closes


def test_put_outside_the_move_and_behind_support_scores_full():
    closes = _with_dip()
    highs, lows = _bars(closes)
    lay = c.location_layer(100.0, highs, lows,
                           ChainRead(dte=45, put_short_strike=85.0), iv=30.0)
    assert lay.put_points == pytest.approx(c.W_LOCATION)
    assert lay.status == "good"
    assert "behind support at 89.50" in lay.read


def test_put_inside_the_move_and_in_front_of_support_is_marked_down():
    closes = _with_dip()
    highs, lows = _bars(closes)
    lay = c.location_layer(100.0, highs, lows,
                           ChainRead(dte=45, put_short_strike=99.8), iv=30.0)
    assert lay.put_points == pytest.approx(-0.75)
    assert lay.status == "watch"
    assert "INSIDE the expected move" in lay.read
    assert "in front of support" in lay.read


def test_call_over_a_stock_at_its_highs_has_no_ceiling():
    """Every bar higher than the last: no swing high above price at all."""
    closes = [100 + i * 0.5 for i in range(120)]
    highs = [v for v in closes]
    lows = [v - 0.5 for v in closes]
    spot = closes[-1]
    lay = c.location_layer(spot, highs, lows,
                           ChainRead(dte=45, call_short_strike=spot * 1.2), iv=30.0)
    assert "no ceiling over the strike" in lay.read
    assert lay.call_points == pytest.approx(0.0)       # +0.5 move, -0.5 no ceiling


def test_location_uses_actual_volatility_when_implied_is_missing():
    closes = _with_dip()
    highs, lows = _bars(closes)
    lay = c.location_layer(100.0, highs, lows,
                           ChainRead(dte=45, put_short_strike=85.0), hv=30.0)
    assert "30% actual volatility" in lay.read


def test_location_is_unknown_without_a_strike():
    closes = _with_dip()
    highs, lows = _bars(closes)
    assert c.location_layer(100.0, highs, lows, ChainRead(dte=45)).status == "unknown"


def test_the_freshest_high_counts_as_resistance():
    """A peak two bars old cannot be a confirmed swing point yet, and it was
    the one that mattered on CRWD (233.88). The 20-day high stands in."""
    closes = [100.0] * 100 + [104.0, 110.0, 106.0, 103.0]
    highs, lows = _bars(closes)
    _, res = c.swing_levels(highs, lows, closes[-1])
    assert 110.5 in res


# -------------------------------------------------------------------- timing
def test_a_big_jump_marks_the_call_side_down():
    closes = _quiet()
    closes += [closes[-1] * 1.15, closes[-1] * 1.15 * 0.99, closes[-1] * 1.15 * 0.995]
    highs, lows = _bars(closes)
    lay = c.timing_layer(closes, highs, lows)
    assert lay.call_points == pytest.approx(-c.W_TIMING)
    assert lay.put_points == 0.0
    assert lay.value.startswith("Jumped 15.0%")
    assert "2 sessions ago" in lay.value


def test_a_big_drop_marks_the_put_side_down():
    closes = _quiet()
    closes += [closes[-1] * 0.86, closes[-1] * 0.86 * 1.005]
    highs, lows = _bars(closes)
    lay = c.timing_layer(closes, highs, lows)
    assert lay.put_points == pytest.approx(-c.W_TIMING)
    assert lay.call_points == 0.0


def test_a_run_into_the_high_marks_the_call_side_down():
    closes = _quiet()
    for _ in range(20):
        closes.append(closes[-1] * 1.012)
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert lay.call_points == pytest.approx(-c.W_TIMING * 2 / 3)
    assert "20-day high" in lay.value


def test_a_pullback_inside_a_rise_is_the_put_entry():
    closes = _quiet()
    for _ in range(15):
        closes.append(closes[-1] * 1.012)
    for _ in range(3):
        closes.append(closes[-1] * 0.98)
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert lay.put_points == pytest.approx(c.W_TIMING / 2)
    assert lay.status == "good"
    assert lay.value.startswith("Pulled back")


def test_a_bounce_inside_a_fall_is_the_call_entry():
    closes = _quiet()
    for _ in range(15):
        closes.append(closes[-1] / 1.012)
    for _ in range(3):
        closes.append(closes[-1] * 1.02)
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert lay.call_points == pytest.approx(c.W_TIMING / 2)
    assert lay.value.startswith("Bounced")


def test_an_old_low_is_not_a_bounce():
    """CRWD on Oct 15: 15% off its high and still falling, but 6% above a
    September low. Read as a bounce, it told her to sell calls into a slide."""
    closes = _quiet()
    for _ in range(5):
        closes.append(closes[-1] / 1.015)        # the old low, 15+ bars back
    for _ in range(10):
        closes.append(closes[-1] * 1.012)
    for _ in range(5):
        closes.append(closes[-1] / 1.006)
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert not lay.value.startswith("Bounced")


def test_quiet_means_nothing_either_way():
    closes = _quiet()
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert (lay.put_points, lay.call_points, lay.value) == (0.0, 0.0, "Quiet")


def test_one_outlier_does_not_hide_the_next_move():
    """The normal day comes from the median, so an earnings gap in the window
    does not double it and make a later 20-day run look ordinary."""
    closes = _quiet(70)
    closes[30] = closes[29] * 1.20                  # an old earnings gap
    closes[31:] = [v * 1.20 for v in closes[31:]]
    for _ in range(20):
        closes.append(closes[-1] * 1.012)
    highs, lows = _bars(closes, pad=0.1)
    lay = c.timing_layer(closes, highs, lows)
    assert "20-day high" in lay.value


def test_timing_needs_three_months():
    closes = _quiet(40)
    highs, lows = _bars(closes)
    assert c.timing_layer(closes, highs, lows).status == "unknown"


# -------------------------------------------------------- the five questions
def _report(*layers):
    return c.CandidateReport(symbol="X", layers=list(layers), as_of=dt.date(2026, 10, 20),
                             put_side=c.SideVerdict(side=PUT, name="p"),
                             call_side=c.SideVerdict(side=CALL, name="c"))


def _lay(key, put=0.0, call=0.0, status="ok"):
    return Layer(key=key, label=key, value="v", read=f"{key} read", status=status,
                 put_points=put, call_points=call, weight=1.0)


def test_questions_follow_the_side_she_is_selling():
    rep = _report(_lay("structure", 2, -2, "good"), _lay("averages", 1.5, -1.5, "good"),
                  _lay("timing", 0, -1.5, "watch"))
    calls = {q["key"]: q["status"] for q in c.five_questions(rep, [CALL])}
    puts = {q["key"]: q["status"] for q in c.five_questions(rep, [PUT])}
    assert calls["trend"] == "watch" and puts["trend"] == "good"
    assert calls["timing"] == "watch" and puts["timing"] == "ok"


def test_a_trend_is_a_problem_for_a_condor():
    """Good for one wing exactly by being bad for the other - the worse stands."""
    rep = _report(_lay("structure", 2, -2, "good"), _lay("averages", 1.5, -1.5, "good"))
    q = {q["key"]: q["status"] for q in c.five_questions(rep, [PUT, CALL])}
    assert q["trend"] == "watch"


def test_earnings_inside_the_window_comes_through_as_a_block():
    rep = _report(_lay("events", status="bad"))
    q = {q["key"]: q["status"] for q in c.five_questions(rep, [PUT])}
    assert q["calendar"] == "bad"


def test_unmeasured_questions_say_so():
    q = {q["key"]: q["status"] for q in c.five_questions(_report(), [PUT])}
    assert set(q.values()) == {"unknown"}
    assert list(q) == ["trend", "volatility", "location", "timing", "calendar"]


def test_assess_grades_the_strikes_she_typed():
    closes = _with_dip()
    highs, lows = _bars(closes)
    rep = c.assess("X", closes=closes, highs=highs, lows=lows, iv=30.0,
                   chain_read=ChainRead(dte=45, put_short_strike=99.8),
                   put_short=85.0)
    loc = next(lay for lay in rep.layers if lay.key == "location")
    assert loc.put_points == pytest.approx(c.W_LOCATION)
    assert {lay.key for lay in rep.layers} >= {"location", "timing"}


def test_a_placed_trade_is_not_judged_on_strikes_she_did_not_sell():
    """Tradability blocks on the SOP-delta strikes; her own fill is already
    placed, so the log form's verdict leaves that layer out."""
    rep = _report(_lay("structure", 2, -2, "good"),
                  Layer(key="tradability", label="Tradability", value="v",
                        read="thin", status="bad", blocks=[PUT, CALL]))
    assert rep.layers and c.placed_verdict(rep, PUT).verdict == "Workable"
    assert not c.placed_verdict(rep, PUT).blocked


def test_the_trade_s_own_days_feed_the_expected_move():
    closes = _with_dip()
    highs, lows = _bars(closes)
    rep = c.assess("X", closes=closes, highs=highs, lows=lows, iv=30.0,
                   chain_read=ChainRead(), put_short=85.0, strike_dte=45)
    loc = next(lay for lay in rep.layers if lay.key == "location")
    assert loc.known and "45 days" in loc.read
