"""The shape of the report's charts.

Rita sent screenshots of these two rendered unreadably: the weekly bars ran
together into one solid block of green with no gaps, the y-axis labels lost
their first characters ("/29 - 7/5", "\\APL"), and the value at the end of the
longest bar was cut off ("$2,97" for $2,975).

Streamlit draws Vega charts to a canvas, so nothing here can be checked by
reading the page - these assert the spec instead, which is where each of those
three faults lived.
"""

from __future__ import annotations

from datetime import date

import pytest

from ui import income_report as ir


def _weeks():
    return [
        {"label": "29/6 - 5/7", "start": date(2026, 6, 29), "banked": 300.0,
         "premium": 400.0, "trades": 2},
        {"label": "6/7 - 12/7", "start": date(2026, 7, 6), "banked": 1490.0,
         "premium": 1600.0, "trades": 3},
        {"label": "13/7 - 19/7", "start": date(2026, 7, 13), "banked": 3378.0,
         "premium": 2475.0, "trades": 5},
    ]


def _producers():
    return [{"name": "SMH", "premium": 2975.0, "banked": 2000.0, "trades": 3},
            {"name": "AAPL", "premium": 720.0, "banked": 500.0, "trades": 1},
            {"name": "SPX", "premium": 525.0, "banked": 400.0, "trades": 1}]


def _y(spec):
    layer = spec["layer"][0] if "layer" in spec else spec
    return layer["encoding"]["y"]


def _x(spec):
    layer = spec["layer"][0] if "layer" in spec else spec
    return layer["encoding"]["x"]


# ------------------------------------------------------------- bars with gaps
def test_the_weekly_bars_are_spaced_by_band_padding_not_a_fixed_size():
    """The original bug: mark_bar(size=34) kept the bars a fixed height while
    the chart's height scaled with the number of weeks, so the bars grew until
    they touched and the chart read as one filled area."""
    spec = ir.weeks_chart(_weeks(), 808).to_dict()
    layer = spec["layer"][0]
    assert "size" not in layer["mark"], "a fixed bar size is what closed the gaps"
    scale = _y(spec)["scale"]
    assert scale["paddingInner"] >= 0.25
    assert scale["paddingOuter"] > 0


def test_the_producer_bars_are_spaced_the_same_way():
    spec = ir.producers_chart(_producers()).to_dict()
    assert "size" not in spec["layer"][0]["mark"]
    assert _y(spec)["scale"]["paddingInner"] >= 0.25


# ------------------------------------------------------- room for the labels
def test_the_week_labels_have_room_and_are_not_truncated():
    """"/29 - 7/5" was "29/6 - 5/7" with its first characters cut off."""
    axis = _y(ir.weeks_chart(_weeks(), 808).to_dict())["axis"]
    assert axis["labelLimit"] >= 150
    assert axis["labelPadding"] > 0


def test_the_ticker_labels_have_room():
    axis = _y(ir.producers_chart(_producers()).to_dict())["axis"]
    assert axis["labelLimit"] >= 100
    assert axis["labelPadding"] > 0


def test_the_longest_bar_leaves_headroom_for_its_value_label():
    """$2,975 printed at the end of the longest bar was clipped to "$2,97".
    The x scale has to end past the biggest value, not exactly on it."""
    spec = ir.producers_chart(_producers()).to_dict()
    scale = _x(spec)["scale"]
    assert scale["domainMax"] > 2975.0
    assert scale["domainMin"] == 0


# ------------------------------------------------------------ the goal line
def test_the_weekly_goal_is_drawn_as_a_second_layer():
    spec = ir.weeks_chart(_weeks(), 808).to_dict()
    assert len(spec["layer"]) == 2
    assert spec["layer"][1]["mark"]["type"] == "rule"


def test_no_goal_means_no_second_layer_rather_than_a_line_at_zero():
    spec = ir.weeks_chart(_weeks(), 0).to_dict()
    assert "layer" not in spec
    assert spec["mark"]["type"] == "bar"


# ------------------------------------------------------------- the date style
def test_week_labels_are_written_day_before_month():
    """Israel and Europe read 29/6, not 6/29. The label is built in
    month_report, so this is the test that the report shows her format."""
    from src.engine import month_report as mr
    from src.engine.positions import Position

    p = Position(trade_id="t", underlying="SPX", strategy_name="Put Credit Spread",
                 opened=date(2026, 6, 29), credit=300.0, open_credit=300.0,
                 open_cash=300.0, account="paper")
    report = mr.build([p], month="2026-06", live_from=date(2026, 7, 31),
                      mode="practice")
    # 29 June 2026 is a Monday, so the week runs 29/6 to 5/7.
    assert report["weeks"][0]["label"] == "29/6 - 5/7"
