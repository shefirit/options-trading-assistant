"""The Market tab's read logic: config thresholds, the VIX comfort zone, the
day's verdict (must behave exactly like the old hardcoded version), expected
move math, premium richness, the sector pulse rows, and the demo data builders.
"""

import datetime as dt
import math

import pytest

from src.data import market_read
from src.data.market_context import build_context
from src.data.market_events import Event


# ------------------------------------------------------------------ config
def test_read_cfg_defaults():
    cfg = market_read.read_cfg({})
    assert cfg == {"vix_zone_low": 13.0, "vix_zone_high": 25.0,
                   "vix_caution": 20.0, "vix_stop": 28.0}
    # None settings behaves the same as empty settings.
    assert market_read.read_cfg(None) == cfg


def test_read_cfg_partial_override_keeps_other_defaults():
    cfg = market_read.read_cfg({"market_read": {"vix_stop": 30}})
    assert cfg["vix_stop"] == 30.0
    assert cfg["vix_caution"] == 20.0
    assert cfg["vix_zone_low"] == 13.0
    assert cfg["vix_zone_high"] == 25.0


# ------------------------------------------------------------------ VIX zone
def test_zone_below_inside_above_and_unknown():
    zone, text, tone = market_read.classify_vix_zone(11.0, 13, 25)
    assert (zone, tone) == ("below", "amber") and "11.0" in text

    zone, text, tone = market_read.classify_vix_zone(17.4, 13, 25)
    assert (zone, tone) == ("inside", "green") and "13-25" in text

    zone, text, tone = market_read.classify_vix_zone(27.0, 13, 25)
    assert (zone, tone) == ("above", "red")

    zone, _text, tone = market_read.classify_vix_zone(None, 13, 25)
    assert (zone, tone) == ("unknown", "amber")


def test_zone_boundaries_count_as_inside():
    assert market_read.classify_vix_zone(13.0, 13, 25)[0] == "inside"
    assert market_read.classify_vix_zone(25.0, 13, 25)[0] == "inside"


# ------------------------------------------------------------------ expected move
def test_expected_move_math():
    em = market_read.expected_move(5000.0, 0.20, 30)
    assert em is not None
    points, pct = em
    assert points == pytest.approx(5000 * 0.20 * math.sqrt(30 / 365))
    assert pct == pytest.approx(points / 5000 * 100)


def test_expected_move_needs_all_positive_inputs():
    assert market_read.expected_move(None, 0.2, 30) is None
    assert market_read.expected_move(0, 0.2, 30) is None
    assert market_read.expected_move(5000, None, 30) is None
    assert market_read.expected_move(5000, 0.0, 30) is None
    assert market_read.expected_move(5000, 0.2, 0) is None
    assert market_read.expected_move(5000, 0.2, None) is None


# ------------------------------------------------------------------ richness
def test_richness_read_reuses_premium_finder_thresholds():
    assert market_read.richness_read(0.40, 0.20) == ("Rich", 2.0)   # >= 1.15
    assert market_read.richness_read(0.18, 0.20) == ("Fair", 0.9)   # >= 0.80
    assert market_read.richness_read(0.10, 0.20) == ("Thin", 0.5)

    # No realized vol -> falls back to the raw IV level.
    label, ratio = market_read.richness_read(0.45, None)
    assert (label, ratio) == ("Rich", None)
    label, ratio = market_read.richness_read(None, 0.2)
    assert (label, ratio) == ("n/a", None)


# ------------------------------------------------------------------ verdict
def _event(kind: str, days_away: int) -> Event:
    return Event(date=dt.date(2026, 7, 15), days_away=days_away,
                 label="FOMC rate decision" if kind == "fomc" else "Jobs report",
                 kind=kind)


def test_verdict_identical_to_old_hardcoded_behavior():
    cfg = market_read.read_cfg({})

    ctx = build_context("SPX", 5000.0, vix=28.0)
    headline, tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert (headline, tone) == ("Sit this one out", "red")

    ctx = build_context("SPX", 5000.0, vix=20.0)
    headline, tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert (headline, tone) == ("Okay - but keep size small", "amber")

    ctx = build_context("SPX", 5000.0, vix=14.0)
    headline, tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert (headline, tone) == ("Good conditions to sell premium", "green")

    ctx = build_context("SPX", 5000.0, vix=None)
    headline, tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert (headline, tone) == ("Read the market before you trade", "amber")


def test_verdict_big_event_beats_calm_vix():
    cfg = market_read.read_cfg({})
    ctx = build_context("SPX", 5000.0, vix=15.0)
    headline, tone, why = market_read.trading_verdict(ctx, [_event("fomc", 1)], cfg)
    assert (headline, tone) == ("Trade carefully today", "amber")
    assert "tomorrow" in why

    # ...but a stop-level VIX still wins over the event.
    ctx = build_context("SPX", 5000.0, vix=29.0)
    headline, _tone, _why = market_read.trading_verdict(ctx, [_event("fomc", 1)], cfg)
    assert headline == "Sit this one out"


def test_verdict_respects_config_thresholds():
    ctx = build_context("SPX", 5000.0, vix=30.0)
    cfg = market_read.read_cfg({"market_read": {"vix_stop": 35}})
    headline, tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert (headline, tone) == ("Okay - but keep size small", "amber")

    ctx = build_context("SPX", 5000.0, vix=16.0)
    cfg = market_read.read_cfg({"market_read": {"vix_caution": 15}})
    headline, _tone, _why = market_read.trading_verdict(ctx, [], cfg)
    assert headline == "Okay - but keep size small"


# ------------------------------------------------------------------ sector pulse
def test_pulse_rows_from_batch_history_shape():
    history = {"SPY": ([100.0, 101.0], [1.0, 1.0]),
               "QQQ": ([50.0], [1.0])}
    rows = market_read.build_pulse_rows(history, ["SPY", "QQQ", "GLD"])
    by_sym = {r["symbol"]: r for r in rows}

    assert by_sym["SPY"]["change_pct"] == pytest.approx(1.0)
    assert by_sym["SPY"]["label"] == "S&P 500"
    assert by_sym["SPY"]["group"] == "Indexes"
    assert by_sym["QQQ"]["change_pct"] is None          # only one close
    assert by_sym["GLD"]["change_pct"] is None          # missing from the batch
    assert by_sym["GLD"]["group"] == "Other assets"


def test_pulse_rows_unknown_ticker_falls_back_to_symbol():
    rows = market_read.build_pulse_rows({"ZZZT": ([10.0, 10.1], [1.0, 1.0])}, ["ZZZT"])
    assert rows[0]["label"] == "ZZZT"
    assert rows[0]["group"] == "Other assets"


def test_pulse_rows_empty_batch_means_retry_note():
    assert market_read.build_pulse_rows({}, ["SPY", "QQQ"]) == []


def test_pulse_rows_zero_prev_close_gives_none():
    rows = market_read.build_pulse_rows({"SPY": ([0.0, 101.0], [1.0, 1.0])}, ["SPY"])
    assert rows[0]["change_pct"] is None


# ------------------------------------------------------------------ demo data
def test_demo_vix_frame_deterministic_and_shaped_like_yahoo():
    day = dt.date(2026, 7, 10)
    a = market_read.demo_vix_frame(today=day)
    b = market_read.demo_vix_frame(today=day)
    assert a.equals(b)
    assert list(a.columns) == ["Close"]
    assert len(a) == 252
    assert float(a["Close"].iloc[-1]) == 13.5        # matches the demo VIX tile
    assert float(a["Close"].min()) > 9               # stays in a believable range
    assert float(a["Close"].max()) < 30


def test_demo_pulse_history_deterministic_small_moves():
    syms = ["SPY", "QQQ", "GLD", "XLE"]
    a = market_read.demo_pulse_history(syms)
    assert a == market_read.demo_pulse_history(syms)
    for sym in syms:
        closes, vols = a[sym]
        assert len(closes) == 2 and len(vols) == 2
        change = (closes[-1] / closes[-2] - 1) * 100
        assert abs(change) <= 2.0
