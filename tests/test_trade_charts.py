"""The shape of the dashboard's charts.

Streamlit draws Vega to a canvas, so nothing here can be checked by reading the
page. These assert the SPEC instead, the same way test_report_charts.py does.

The one that matters most is at the bottom: no chart's data may contain a field
that adds real money to practice money. That is the rule the whole two-book
design rests on, and it is checked structurally rather than trusted.

Every number here is invented. This repo is public.
"""

from __future__ import annotations

from datetime import date

import pytest

from ui.trades import charts

SETTINGS = {
    "targets": {"weekly": 808, "monthly": 3500, "year_one_end_balance": 142000},
    "account": {"starting_capital": 100000},
    "risk_limits": {"monthly_bp_limit": 50000},
}


def _bullet_rows():
    return [
        {"label": "THIS WEEK", "sub": "", "actual": 400.0, "target": 808.0,
         "pace": 346.0, "pct": 0.495, "pace_pct": 0.428,
         "text": "$400 of $808", "tone": "watch", "period": "week"},
        {"label": "THIS MONTH", "sub": "", "actual": 1240.0, "target": 3500.0,
         "pace": 565.0, "pct": 0.354, "pace_pct": 0.161,
         "text": "$1,240 of $3,500", "tone": "watch", "period": "month"},
        {"label": "YEAR ONE", "sub": "", "actual": 1240.0, "target": 42000.0,
         "pace": 677.0, "pct": 0.03, "pace_pct": 0.016,
         "text": "$1,240 of $42,000", "tone": "watch", "period": "year"},
    ]


def _series():
    return [
        {"date": date(2026, 6, 10), "banked": 300.0, "cumulative": 300.0,
         "target": 0.0, "book": "practice"},
        {"date": date(2026, 6, 28), "banked": 800.0, "cumulative": 800.0,
         "target": 0.0, "book": "practice"},
        {"date": date(2026, 8, 1), "banked": 0.0, "cumulative": 0.0,
         "target": 113.0, "book": "real"},
        {"date": date(2026, 8, 4), "banked": 1200.0, "cumulative": 1200.0,
         "target": 452.0, "book": "real"},
        {"date": date(2026, 8, 5), "banked": 1200.0, "cumulative": 1200.0,
         "target": 565.0, "book": "real"},
    ]


def _months():
    """pnl.buckets() shape - `banked` is the selected book, `back` the other,
    and there is deliberately no key holding their sum."""
    return [
        {"key": "2026-06-01", "label": "June 2026", "short": "Jun",
         "grain": "month", "banked": 0.0, "back": 800.0, "target": 0.0,
         "premium": 0.0, "opened": 1, "closed": 1, "cumulative": 0.0,
         "pace_target": 0.0, "is_current": False, "is_future": False},
        {"key": "2026-08-01", "label": "August 2026", "short": "Aug",
         "grain": "month", "banked": 1200.0, "back": 0.0, "target": 565.0,
         "premium": 900.0, "opened": 1, "closed": 1, "cumulative": 1200.0,
         "pace_target": 565.0, "is_current": False, "is_future": False},
    ]


def _days():
    out = []
    for i in range(1, 32):
        out.append({"date": date(2026, 8, i), "day": i,
                    "banked": 1200.0 if i == 4 else (-300.0 if i == 11 else 0.0),
                    "premium": 0.0, "trades": 0,
                    "weekday": date(2026, 8, i).weekday(),
                    "week_index": (i + 4) // 7, "is_future": i > 5})
    return out


def _layers(chart):
    spec = chart.to_dict()
    return spec.get("layer", [spec])


def _marks(chart):
    """Every layer's mark as a dict, so `size` and `opacity` can be read."""
    out = []
    for layer in _layers(chart):
        mark = layer.get("mark")
        out.append(mark if isinstance(mark, dict) else {"type": mark})
    return out


# ------------------------------------------------------------------- bullet
def test_the_measure_bar_is_thinner_than_the_range_bands():
    """The contrast that makes a bullet chart read faster than a progress bar.
    A measure as thick as its bands is just a stacked bar."""
    sizes = [m["size"] for m in _marks(charts.goal_bullet(_bullet_rows()))
             if m["type"] == "bar" and "size" in m]
    assert len(sizes) >= 4                      # three bands plus the measure
    assert min(sizes) < max(sizes)
    assert charts.MEASURE_SIZE < charts.BAND_SIZE


def test_the_bands_get_lighter_as_the_range_improves():
    """Few's convention: darkest at the bottom of the range. Reversed, the
    chart says a bad month is the good one."""
    colours = [m["color"] for m in _marks(charts.goal_bullet(_bullet_rows()))
               if m["type"] == "bar" and "color" in m]
    assert colours[:3] == charts.BAND_FILLS


def test_x_is_share_of_target_so_808_and_42000_are_comparable():
    """Dollars on a shared axis would make the weekly bar invisible beside the
    year-one one, which is the only reason to draw them together."""
    x = _layers(charts.goal_bullet(_bullet_rows()))[0]["encoding"]["x"]
    assert x["axis"]["format"] == ".0%"
    assert x["scale"]["domain"] == [0, charts.BULLET_MAX]


def test_there_is_room_above_the_target_for_a_month_that_beat_it():
    assert charts.BULLET_MAX > 1.0


def test_the_target_and_pace_ticks_are_both_there_and_are_different_marks():
    """The pace tick is the whole point - "35%, and a steady plan would be at
    16% today" is the sentence a progress bar cannot say."""
    ticks = [m for m in _marks(charts.goal_bullet(_bullet_rows()))
             if m["type"] == "tick"]
    assert len(ticks) == 2
    colours = {t["color"] for t in ticks}
    assert charts.theme.INK in colours and charts.theme.AMBER in colours


def test_the_target_tick_encodes_a_field_not_a_hardcoded_position():
    ticks = [l for l in _layers(charts.goal_bullet(_bullet_rows()))
             if (l.get("mark") or {}).get("type") == "tick"]
    assert all("field" in t["encoding"]["x"] for t in ticks)


def test_every_row_prints_its_dollars_as_text():
    texts = [m for m in _marks(charts.goal_bullet(_bullet_rows()))
             if m["type"] == "text"]
    assert texts, "the dollars must ride along - a percentage alone is not money"


# ------------------------------------------------------------- equity curve
def test_the_cumulative_chart_carries_a_dashed_target_ramp():
    """The piece the old running-total chart was missing entirely: a rising
    line looks like success at any angle until something is beside it."""
    dashed = [m for m in _marks(charts.cumulative_vs_target(_series()))
              if m.get("strokeDash")]
    assert len(dashed) == 1


def test_the_other_book_is_drawn_behind_and_never_coloured():
    marks = _marks(charts.cumulative_vs_target(_series(), foreground="real"))
    back = marks[0]
    assert back["color"] == charts.theme.BORDER_STRONG
    assert back["opacity"] < 0.6


def test_switching_the_foreground_swaps_which_book_is_faded():
    """Symmetric on purpose - looking at the practice book should not lose the
    comparison to the real one."""
    marks = _marks(charts.cumulative_vs_target(_series(), foreground="practice"))
    assert marks[0]["color"] == charts.theme.BORDER_STRONG


def test_a_book_with_nothing_in_it_simply_is_not_drawn():
    only_real = [r for r in _series() if r["book"] == "real"]
    marks = _marks(charts.cumulative_vs_target(only_real))
    assert all(m.get("color") != charts.theme.BORDER_STRONG for m in marks)


# ----------------------------------------------------------------- pnl bars
def _no_target_months():
    return [dict(m, target=0.0) for m in _months()]


def test_the_backdrop_book_is_wider_and_paler_than_the_foreground():
    """Wider and behind reads as history. Side by side would read as two
    results being compared, which they must never be."""
    marks = _marks(charts.pnl_bars(_months()))
    back, fore = marks[0], marks[1]
    assert back["opacity"] < 0.6
    assert back["size"] > fore["size"]
    assert back["color"] == charts.theme.BORDER_STRONG


def test_the_target_is_a_tick_on_each_bar_not_one_line_across_the_chart():
    """A single horizontal rule only tells the truth when every period is
    worth the same, and they are not: the month in progress has had fewer days
    than the finished ones, and a week straddling two months prorates across
    both. A tick per bar carries that period's OWN target, which is the only
    version that is correct at every grain."""
    marks = _marks(charts.pnl_bars(_months()))
    ticks = [m for m in marks if m["type"] == "tick"]
    assert len(ticks) == 1, "one tick layer, encoding a field per bar"
    assert not [m for m in marks if m["type"] == "rule"], (
        "a flat rule would be wrong on every partial period")
    tick_layer = [l for l in _layers(charts.pnl_bars(_months()))
                  if l["mark"]["type"] == "tick"][0]
    assert tick_layer["encoding"]["y"]["field"] == "target"


def test_the_target_tick_disappears_when_there_is_no_target():
    """A practice book has no plan, and neither does a month before she funded
    the account. Drawing a target of zero would read as a goal she missed."""
    marks = _marks(charts.pnl_bars(_no_target_months()))
    assert not [m for m in marks if m["type"] == "tick"]


def test_a_backdrop_that_would_flatten_the_foreground_is_left_off():
    """Caught on her real log: the practice book had banked $9,219 in a month
    against the real book's $1,102, and on one shared axis every real bar was a
    sliver at the bottom. The backdrop exists so a short real history has
    something to be READ AGAINST - one that flattens the thing it is context
    for has stopped doing that job."""
    swamped = [dict(m, banked=100.0, back=100.0 * charts.BACKDROP_MAX_RATIO * 2)
               for m in _months()]
    marks = _marks(charts.pnl_bars(swamped))
    bars = [m for m in marks if m["type"] == "bar"]
    assert len(bars) == 1, "only the foreground should be drawn"
    # The foreground carries its colour in a conditional ENCODING (green above
    # zero, red below), so a mark-level colour is the backdrop's signature.
    assert bars[0].get("color") != charts.theme.BORDER_STRONG


def test_a_backdrop_within_scale_is_still_drawn():
    """The guard is a ceiling, not a removal. A comparable other book is the
    whole reason the backdrop exists."""
    fine = [dict(m, banked=100.0, back=200.0) for m in _months()]
    bars = [m for m in _marks(charts.pnl_bars(fine)) if m["type"] == "bar"]
    assert len(bars) == 2
    assert bars[0]["color"] == charts.theme.BORDER_STRONG


def test_the_equity_curve_drops_a_swamping_backdrop_too():
    """Same threshold, same reason - her practice curve reached $20,000 against
    the real book's $2,000 and pressed the green line flat."""
    series = [
        {"date": date(2026, 8, 1), "cumulative": 500.0, "banked": 500.0,
         "target": 400.0, "book": "real"},
        {"date": date(2026, 8, 1), "cumulative": 90000.0, "banked": 90000.0,
         "target": 0.0, "book": "practice"},
    ]
    lines = [m for m in _marks(charts.cumulative_vs_target(series))
             if m["type"] == "line"]
    assert all(m.get("color") != charts.theme.BORDER_STRONG for m in lines)


def test_every_bar_is_measured_from_zero():
    """A layered chart resolves ONE shared y scale, and a tick layer does not
    carry a bar's implicit zero - so the domain came out as [min, max] and the
    bars rendered off the bottom of the chart entirely. Caught on the real log:
    the axis ran $1,500 to $3,500 and there were no bars on it at all.

    A P&L bar measured from $1,500 instead of $0 is a bar that lies about its
    own size, so every layer that shares this scale pins zero explicitly.
    """
    for layer in _layers(charts.pnl_bars(_months())):
        scale = layer["encoding"]["y"].get("scale", {})
        assert scale.get("zero") is True, (
            f"{layer['mark']['type']} layer can float its own baseline")


def test_periods_are_oldest_on_the_left():
    x = _layers(charts.pnl_bars(_months()))[0]["encoding"]["x"]
    assert x["sort"] == ["June 2026", "August 2026"]


@pytest.mark.parametrize("grain", ["day", "week", "month"])
def test_bars_get_thinner_as_the_zoom_gets_finer(grain):
    """Forty-five daily bars and twelve monthly ones cannot wear the same
    width without one of them becoming a barcode."""
    rows = [dict(m, grain=grain) for m in _months()]
    fore = [m for m in _marks(charts.pnl_bars(rows, grain))
            if m["type"] == "bar"][1]
    assert fore["size"] == charts._BAR_SIZE[grain]


# ----------------------------------------------------------------- calendar
def test_a_day_that_earned_nothing_is_white_not_pale_green():
    """domainMid pins zero, so a quiet day never reads as a small win."""
    layers = _layers(charts.day_calendar(_days()))
    colour = next(l["encoding"]["color"] for l in layers
                  if "color" in l.get("encoding", {}))
    assert colour["scale"]["domainMid"] == 0
    assert colour["scale"]["range"][1] == "#FFFFFF"


def test_the_calendar_covers_every_day_of_the_month_including_the_empty_ones():
    """A month drawn only on its earning days is a scatter with no shape, and
    the shape is the question."""
    spec = charts.day_calendar(_days()).to_dict()
    data = next(iter(spec["datasets"].values()))
    assert len(data) == 31


def test_the_week_runs_monday_first():
    x = _layers(charts.day_calendar(_days()))[0]["encoding"]["x"]
    assert x["sort"][0] == "Mon" and x["sort"][-1] == "Sun"


# ----------------------------------------------------------------- drawdown
def test_a_drawdown_is_never_positive():
    spec = charts.drawdown(_series()).to_dict()
    data = next(iter(spec["datasets"].values()))
    assert all(row["Below best"] <= 0 for row in data)


def test_the_drawdown_reads_only_the_foreground_book():
    spec = charts.drawdown(_series(), foreground="real").to_dict()
    data = next(iter(spec["datasets"].values()))
    assert len(data) == 3          # the three real rows, not all five


# ------------------------------------------------- the rule the design rests on
@pytest.mark.parametrize("build", [
    lambda: charts.goal_bullet(_bullet_rows()),
    lambda: charts.cumulative_vs_target(_series()),
    lambda: charts.pnl_bars(_months()),
    lambda: charts.drawdown(_series()),
])
def test_no_chart_ever_carries_a_field_that_adds_the_two_books(build):
    """The structural guarantee. Practice money is drawn as context and never
    summed into real money, and the way that is enforced is that the combined
    number does not exist anywhere in the data a chart was handed.

    Real banked 1,200 and practice banked 800, so 2,000 must appear nowhere.
    """
    spec = build().to_dict()
    for data in spec.get("datasets", {}).values():
        for row in data:
            assert 2000.0 not in [v for v in row.values()
                                  if isinstance(v, (int, float))]
