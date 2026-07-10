"""The Market tab's read logic: config thresholds, the day's verdict (must
behave exactly like the old hardcoded version), the plain-English brief, the
sector-pulse rows, and the demo data builder.
"""

import datetime as dt

import pytest

from src.data import market_read
from src.data.market_context import build_context
from src.data.market_events import Event


def _event(kind: str, days_away: int, label: str = "", in_window: bool = False) -> Event:
    return Event(date=dt.date(2026, 7, 15), days_away=days_away,
                 label=label or ("FOMC rate decision" if kind == "fomc" else "Event"),
                 kind=kind, in_window=in_window)


# ------------------------------------------------------------------ config
def test_read_cfg_defaults():
    cfg = market_read.read_cfg({})
    assert cfg == {"vix_zone_low": 13.0, "vix_zone_high": 25.0,
                   "vix_caution": 20.0, "vix_stop": 28.0}
    assert market_read.read_cfg(None) == cfg


def test_read_cfg_partial_override_keeps_other_defaults():
    cfg = market_read.read_cfg({"market_read": {"vix_stop": 30}})
    assert cfg["vix_stop"] == 30.0
    assert cfg["vix_caution"] == 20.0
    assert cfg["vix_zone_low"] == 13.0


# ------------------------------------------------------------------ verdict
def test_verdict_identical_to_old_hardcoded_behavior():
    cfg = market_read.read_cfg({})

    ctx = build_context("SPX", 5000.0, vix=28.0)
    assert market_read.trading_verdict(ctx, [], cfg)[:2] == ("Sit this one out", "red")

    ctx = build_context("SPX", 5000.0, vix=20.0)
    assert market_read.trading_verdict(ctx, [], cfg)[:2] == ("Okay - but keep size small", "amber")

    ctx = build_context("SPX", 5000.0, vix=14.0)
    assert market_read.trading_verdict(ctx, [], cfg)[:2] == ("Good conditions to sell premium", "green")

    ctx = build_context("SPX", 5000.0, vix=None)
    assert market_read.trading_verdict(ctx, [], cfg)[:2] == ("Read the market before you trade", "amber")


def test_verdict_big_event_beats_calm_vix():
    cfg = market_read.read_cfg({})
    ctx = build_context("SPX", 5000.0, vix=15.0)
    headline, tone, why = market_read.trading_verdict(ctx, [_event("fomc", 1)], cfg)
    assert (headline, tone) == ("Trade carefully today", "amber")
    assert "tomorrow" in why

    ctx = build_context("SPX", 5000.0, vix=29.0)
    assert market_read.trading_verdict(ctx, [_event("fomc", 1)], cfg)[0] == "Sit this one out"


def test_verdict_respects_config_thresholds():
    ctx = build_context("SPX", 5000.0, vix=30.0)
    cfg = market_read.read_cfg({"market_read": {"vix_stop": 35}})
    assert market_read.trading_verdict(ctx, [], cfg)[:2] == ("Okay - but keep size small", "amber")

    ctx = build_context("SPX", 5000.0, vix=16.0)
    cfg = market_read.read_cfg({"market_read": {"vix_caution": 15}})
    assert market_read.trading_verdict(ctx, [], cfg)[0] == "Okay - but keep size small"


# ------------------------------------------------------------------ next big event
def test_next_big_event_picks_first_market_mover():
    evs = [_event("opex", 3, "Opex"), _event("cpi", 5, "CPI inflation report"),
           _event("fomc", 10, "FOMC")]
    assert market_read.next_big_event(evs).kind == "cpi"


def test_next_big_event_none_when_only_minor_events():
    evs = [_event("opex", 1, "Opex"), _event("earnings", 2, "Earnings")]
    assert market_read.next_big_event(evs) is None


# ------------------------------------------------------------------ today's brief
_PULSE = [
    {"symbol": "SPY", "label": "S&P 500", "group": "Indexes", "change_pct": 0.8},
    {"symbol": "XLK", "label": "Tech", "group": "Sectors", "change_pct": 1.9},
    {"symbol": "XLE", "label": "Energy", "group": "Sectors", "change_pct": -1.4},
]


def test_brief_weaves_trend_leader_laggard_and_event():
    cfg = market_read.read_cfg({})
    ev = _event("cpi", 4, "CPI inflation report", in_window=True)
    text = market_read.build_brief([0.5, 0.6], 14.0, "up", _PULSE, ev, cfg)
    assert "leaning up" in text                     # trend word
    assert "Tech" in text and "led" in text         # leader
    assert "Energy" in text and "lagged" in text     # laggard
    assert "CPI inflation report" in text            # next big event
    # deterministic
    assert text == market_read.build_brief([0.5, 0.6], 14.0, "up", _PULSE, ev, cfg)


def test_brief_takeaway_calm_vs_nervous():
    cfg = market_read.read_cfg({})
    calm = market_read.build_brief([0.2], 14.0, "sideways", [], None, cfg)
    assert "comfortable" in calm.lower()
    nervous = market_read.build_brief([-0.2], 30.0, "down", [], None, cfg)
    assert "fear is high" in nervous.lower()


def test_brief_event_in_window_drives_the_caution():
    cfg = market_read.read_cfg({})
    ev = _event("fomc", 2, "Fed interest-rate decision (FOMC)", in_window=True)
    text = market_read.build_brief([0.1], 15.0, "up", [], ev, cfg)
    assert "trade window" in text.lower()
    assert "FOMC" in text


def test_brief_survives_missing_vix_and_empty_pulse():
    cfg = market_read.read_cfg({})
    text = market_read.build_brief([], None, "unknown", [], None, cfg)
    assert isinstance(text, str) and text.strip()


# ------------------------------------------------------------------ sector pulse
def test_pulse_rows_from_batch_history_shape():
    history = {"SPY": ([100.0, 101.0], [1.0, 1.0]), "QQQ": ([50.0], [1.0])}
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
def test_demo_pulse_history_deterministic_small_moves():
    syms = ["SPY", "QQQ", "GLD", "XLE"]
    a = market_read.demo_pulse_history(syms)
    assert a == market_read.demo_pulse_history(syms)
    for sym in syms:
        closes, vols = a[sym]
        assert len(closes) == 2 and len(vols) == 2
        assert abs((closes[-1] / closes[-2] - 1) * 100) <= 2.0
