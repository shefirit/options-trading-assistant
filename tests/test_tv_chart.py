"""The Analyze tab's candle chart: payload building and page assembly."""

import json
import math
import re

import pandas as pd
import pytest

from ui import tv_chart


def _frame(n=300, start=100.0):
    """A synthetic daily history with a gentle wave, so indicators are defined."""
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    closes = [start + math.sin(i / 9) * 5 + i * 0.05 for i in range(n)]
    return pd.DataFrame({
        "Open": [c - 0.4 for c in closes],
        "High": [c + 1.2 for c in closes],
        "Low": [c - 1.2 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 10 for i in range(n)],
    }, index=idx)


ALL = {"bb", "ma", "ma200", "rsi", "macd"}


# ---------------------------------------------------------------- payload
def test_the_payload_carries_one_candle_per_visible_bar():
    p = tv_chart.build_payload(_frame(300), bars=126, wanted=set())
    assert len(p["candles"]) == 126
    assert len(p["volume"]) == 126
    first = p["candles"][0]
    assert set(first) == {"time", "open", "high", "low", "close"}


def test_bollinger_bands_are_already_drawn_at_the_left_edge():
    """Indicators are computed over the whole history and only then sliced, so a
    20-day band does not start 20 bars into the picture."""
    p = tv_chart.build_payload(_frame(300), bars=126, wanted={"bb"})
    assert len(p["bb"]["upper"]) == 126
    assert p["bb"]["upper"][0]["time"] == p["candles"][0]["time"]


def test_the_200_day_average_is_also_full_length_when_history_allows():
    p = tv_chart.build_payload(_frame(400), bars=126, wanted={"ma200"})
    assert len(p["ma200"]) == 126


def test_bands_sit_either_side_of_the_middle_line():
    p = tv_chart.build_payload(_frame(300), bars=60, wanted={"bb"})
    for u, m, l in zip(p["bb"]["upper"], p["bb"]["middle"], p["bb"]["lower"]):
        assert u["value"] > m["value"] > l["value"]


def test_only_the_requested_indicators_are_sent():
    p = tv_chart.build_payload(_frame(300), bars=60, wanted={"bb"})
    assert "bb" in p
    for absent in ("ma20", "ma50", "ma200", "rsi", "macd"):
        assert absent not in p


def test_every_indicator_can_be_switched_on_at_once():
    p = tv_chart.build_payload(_frame(400), bars=126, wanted=ALL)
    for key in ("bb", "ma20", "ma50", "ma200", "rsi", "macd"):
        assert p[key], f"{key} came back empty"
    assert {"line", "signal", "hist"} == set(p["macd"])


def test_rsi_values_stay_on_their_scale():
    p = tv_chart.build_payload(_frame(300), bars=126, wanted={"rsi"})
    assert all(0 <= d["value"] <= 100 for d in p["rsi"])


def test_a_short_history_still_produces_candles():
    """Fewer bars than an indicator needs: the candles must still draw, the
    indicator just comes back empty rather than blowing up."""
    p = tv_chart.build_payload(_frame(25), bars=126, wanted=ALL)
    assert len(p["candles"]) == 25
    assert p["ma200"] == []


def test_index_prices_are_shown_without_cents():
    """SPX at 7,400 with two decimals is noise; a $300 stock needs them."""
    assert tv_chart.build_payload(_frame(60, start=7400.0), 30, set())["digits"] == 0
    assert tv_chart.build_payload(_frame(60, start=300.0), 30, set())["digits"] == 2


def test_the_payload_is_json_serialisable():
    json.dumps(tv_chart.build_payload(_frame(400), 126, ALL))


# ---------------------------------------------------------------- weekly bars
def test_weekly_bars_fold_the_days_of_each_week():
    frame = _frame(60)
    weekly = tv_chart._weekly(frame)
    assert len(weekly) < len(frame)
    assert weekly["Volume"].sum() == pytest.approx(frame["Volume"].sum())
    assert weekly["High"].max() == pytest.approx(frame["High"].max())
    assert weekly["Low"].min() == pytest.approx(frame["Low"].min())


# ---------------------------------------------------------------- page
def test_the_page_inlines_the_charting_library_and_the_data():
    html = tv_chart.chart_html(tv_chart.build_payload(_frame(300), 126, ALL))
    assert "LightweightCharts" in html
    assert "addSeries" in html
    assert '"candles"' in html
    assert "createChart" in html


def test_the_page_credits_tradingview_for_the_library():
    """Lightweight Charts is Apache 2.0 - the attribution ships with the chart."""
    html = tv_chart.chart_html(tv_chart.build_payload(_frame(120), 60, set()))
    assert "tradingview.com" in html.lower()


def test_the_colour_key_lists_every_line_that_is_drawn():
    key = tv_chart._key(tv_chart.build_payload(_frame(400), 126, ALL))
    for label in ("Bollinger Bands", "20-day average", "50-day average",
                  "200-day average"):
        assert label in key


def test_the_colour_key_leaves_out_lines_that_are_switched_off():
    """No hunting for a teal 200-day line that isn't on the chart."""
    key = tv_chart._key(tv_chart.build_payload(_frame(400), 126, {"bb"}))
    assert "Bollinger Bands" in key
    assert "average" not in key


def test_the_colour_key_says_weeks_on_a_weekly_chart():
    key = tv_chart._key(tv_chart.build_payload(_frame(400), 52, {"ma"}, weekly=True))
    assert "20-week average" in key
    assert "20-day average" not in key


def test_the_chart_grows_for_each_panel_below_the_price():
    """Switching RSI or MACD on must not squeeze the candles into a sliver."""
    plain = tv_chart.build_payload(_frame(300), 126, {"bb"})
    with_rsi = tv_chart.build_payload(_frame(300), 126, {"bb", "rsi"})
    with_both = tv_chart.build_payload(_frame(300), 126, {"bb", "rsi", "macd"})
    assert tv_chart.chart_height(with_rsi) > tv_chart.chart_height(plain)
    assert tv_chart.chart_height(with_both) > tv_chart.chart_height(with_rsi)


def _our_script(html: str) -> str:
    """Just the chart's own code - the vendored library is 200KB of minified
    JavaScript that happens to contain most words you might search for."""
    return html.rsplit("<script>", 1)[1]


def test_the_rsi_panel_formats_its_own_scale():
    """A chart-level price formatter would print the 0-100 RSI as "70.00"."""
    code = _our_script(tv_chart.chart_html(tv_chart.build_payload(_frame(300), 126, {"rsi"})))
    assert "localization:" not in code       # the chart-level formatter, absent by design
    assert "precision: 0" in code            # the RSI series formats itself instead


def test_the_charting_library_is_actually_vendored():
    assert tv_chart._VENDOR.exists(), "lightweight-charts is missing from tools/vendor"
    assert len(tv_chart._library()) > 10_000


# ---------------------------------------------------------------- the presets
def test_bollinger_bands_moving_averages_and_rsi_are_on_by_default():
    on = {v["key"] for v in tv_chart._INDICATORS.values() if v["default"]}
    assert on == {"bb", "ma", "rsi"}


def test_one_year_of_daily_candles_is_the_default_view():
    assert tv_chart._DEFAULT_RANGE == "1 year"
    assert tv_chart._RANGES["1 year"][0] == 252
    assert tv_chart._CANDLES[tv_chart._DEFAULT_CANDLE] is False


def test_every_range_offers_both_candle_sizes():
    for label, (daily, weekly) in tv_chart._RANGES.items():
        assert daily > weekly > 0, label
