"""The dashboard's pictures, as pure builders.

Every function here returns an Altair chart and draws nothing. Vega renders to
canvas, so a rendered chart cannot be asserted - but a chart OBJECT serialises
to a spec, and that spec can be. tests/test_trade_charts.py reads these specs
the same way tests/test_report_charts.py reads the income report's.

THE RULE THAT RUNS THROUGH ALL OF THEM
--------------------------------------
Real money and practice money are separate series on a `book` field, and no
data frame built here ever holds a field that adds them. The account switch
picks which book is the FOREGROUND - coloured, and the only book in any total
on the page - and the other is drawn as a faded grey backdrop with a legend
naming it. That is not discipline, it is structure: the chart cannot plot a
combined bar because the number is not in its data.
"""

from __future__ import annotations

from typing import Any, Optional

import altair as alt
import pandas as pd

from ui import theme

# The bullet chart's qualitative bands, Stephen Few's way round: darkest at the
# bottom of the range, lightening as it improves. They are shares of the
# target, so they are the same three numbers on every row.
BAND_LOW, BAND_MID = 0.5, 0.8
BAND_FILLS = ["#B6DEC8", "#CBE7D8", "#DCEFE4"]   # 0.8+, 0.5-0.8, 0-0.5

# Room above the target so a month that beat the goal has somewhere to sit
# rather than pinning to the right edge and looking identical to exactly 100%.
BULLET_MAX = 1.35

# Bar sizes. The measure MUST be thinner than the bands - that contrast is the
# whole reason a bullet chart reads faster than a progress bar.
BAND_SIZE, MEASURE_SIZE = 28, 12

# The backdrop book: wider and pale, so it reads as a halo behind the real
# bars rather than as a second series competing with them.
BACK_SIZE, FORE_SIZE = 48, 26
BACK_OPACITY = 0.45

# How much taller than the foreground the backdrop may be before it stops
# helping. Her practice book ran nine times the size of her real one, and on a
# shared axis that pinned every real bar to a sliver at the bottom of the
# chart - the backdrop is there so a short real history has something to be
# read against, and one that flattens the thing it is context FOR has stopped
# doing that job. Applies to the bars and to the equity curve alike.
BACKDROP_MAX_RATIO = 3.0


def _tone_colour(tone: str) -> str:
    return {"good": theme.GREEN, "watch": theme.ACCENT_BRIGHT,
            "behind": theme.AMBER, "bad": theme.RED}.get(tone, theme.INK)


# ------------------------------------------------------------------- bullet
def goal_bullet(rows: list[dict[str, Any]]):
    """Target against actual for several goals at once, on one comparable scale.

    rows come from goals.bullet_rows(): this week, this month, year one.

    x is SHARE OF TARGET, not dollars, and that is the load-bearing decision.
    A shared dollar axis would put $808 and $42,000 on one scale and the weekly
    bar would be invisible. Faceting with independent axes would give three
    disconnected charts that no longer compare, which is the one thing putting
    them together is for. The dollars ride along as text at the end of each row,
    which is where a bullet chart puts them anyway.

    The amber tick is what a progress bar cannot say. A bar says "35%". The
    tick says "35%, and a steady plan would be at 16% today".
    """
    df = pd.DataFrame([{
        "Goal": r["label"],
        "Progress": min(max(r["pct"], 0.0), BULLET_MAX),
        "Pace": min(max(r["pace_pct"], 0.0), BULLET_MAX),
        "Target": 1.0,
        "Actual $": r["actual"],
        "Target $": r["target"],
        "text": r["text"],
        "colour": _tone_colour(r["tone"]),
        "low": BAND_LOW, "mid": BAND_MID, "full": BULLET_MAX,
    } for r in rows])
    order = list(df["Goal"])

    y = alt.Y("Goal:N", sort=order, title=None,
              scale=alt.Scale(paddingInner=0.4, paddingOuter=0.25),
              axis=alt.Axis(labelLimit=200, labelPadding=10, labelFontSize=13,
                            labelFontWeight="bold", labelColor=theme.INK))
    x = alt.X("full:Q", title=None,
              scale=alt.Scale(domain=[0, BULLET_MAX], nice=False),
              axis=alt.Axis(format=".0%", tickCount=5, labelFontSize=12,
                            labelColor=theme.SECONDARY))
    base = alt.Chart(df).encode(y=y)

    # Widest first, so each narrower band paints over the one behind it.
    bands = [
        base.mark_bar(size=BAND_SIZE, cornerRadiusEnd=3,
                      color=BAND_FILLS[0]).encode(x=x),
        base.mark_bar(size=BAND_SIZE, color=BAND_FILLS[1]).encode(
            x=alt.X("mid:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None)),
        base.mark_bar(size=BAND_SIZE, color=BAND_FILLS[2]).encode(
            x=alt.X("low:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None)),
    ]

    measure = base.mark_bar(size=MEASURE_SIZE, cornerRadiusEnd=2).encode(
        x=alt.X("Progress:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None),
        color=alt.Color("colour:N", scale=None, legend=None),
        tooltip=[alt.Tooltip("Goal:N"),
                 alt.Tooltip("Actual $:Q", format="$,.0f"),
                 alt.Tooltip("Target $:Q", format="$,.0f"),
                 alt.Tooltip("Progress:Q", title="of target", format=".0%")])

    target_tick = base.mark_tick(thickness=3, size=BAND_SIZE + 6,
                                 color=theme.INK).encode(
        x=alt.X("Target:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None))
    pace_tick = base.mark_tick(thickness=3, size=BAND_SIZE - 6,
                               color=theme.AMBER).encode(
        x=alt.X("Pace:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None),
        tooltip=[alt.Tooltip("Pace:Q", title="a steady plan would be at",
                             format=".0%")])

    labels = base.mark_text(align="left", dx=9, fontSize=13, fontWeight="bold",
                            color=theme.INK).encode(
        x=alt.X("full:Q", scale=alt.Scale(domain=[0, BULLET_MAX]), title=None),
        text=alt.Text("text:N"))

    return alt.layer(*bands, measure, target_tick, pace_tick, labels)


def _series_backdrop_helps(fore, back) -> bool:
    """Is the other book's curve still context rather than a wall?

    Same threshold as the bars: past BACKDROP_MAX_RATIO the backdrop stops
    being something the foreground can be read against and becomes the only
    thing on the chart with any height.
    """
    peak_back = float(back["cumulative"].abs().max() or 0.0)
    if peak_back <= 0:
        return False
    peak_fore = float(fore["cumulative"].abs().max() or 0.0) if not fore.empty else 0.0
    return peak_fore <= 0 or peak_back <= peak_fore * BACKDROP_MAX_RATIO


# --------------------------------------------------------------- equity curve
def cumulative_vs_target(series: list[dict[str, Any]], foreground: str = "real"):
    """Money banked, running total, against the ramp a steady plan would draw.

    series comes from goals.cumulative_series(): every row carries `book`, and
    the two books are separate series. There is no row that holds their sum.

    The dashed ramp is the piece the old running-total chart was missing
    entirely - it drew where she had got to with nothing to compare it against,
    which makes a rising line look like success at any angle.
    """
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    fore = df[df["book"] == foreground]
    back = df[df["book"] != foreground]

    x = alt.X("date:T", title=None,
              axis=alt.Axis(format="%d %b", labelFontSize=12,
                            labelColor=theme.SECONDARY, tickCount=6))
    y = alt.Y("cumulative:Q", title="Banked, running total ($)",
              axis=alt.Axis(format="$,.0f", tickCount=6, labelFontSize=12,
                            labelColor=theme.SECONDARY))

    layers = []
    # Behind everything, and never coloured: the other book is context, not a
    # competing result. And only while it still READS as context - her practice
    # book had banked $20,000 against the real book's $2,000, and on one shared
    # axis that pressed the green line flat along the bottom. The same guard the
    # bars use, for the same reason.
    if not back.empty and _series_backdrop_helps(fore, back):
        layers.append(alt.Chart(back).mark_line(
            strokeWidth=2, color=theme.BORDER_STRONG, opacity=BACK_OPACITY,
            interpolate="monotone").encode(
            x=x, y=y,
            tooltip=[alt.Tooltip("date:T", title="Date"),
                     alt.Tooltip("book:N", title="Book"),
                     alt.Tooltip("cumulative:Q", title="Banked", format="$,.0f")]))

    if not fore.empty:
        layers.append(alt.Chart(fore).mark_area(
            opacity=0.14, color=theme.ACCENT_BRIGHT,
            interpolate="monotone").encode(x=x, y=y))
        layers.append(alt.Chart(fore).mark_line(
            strokeWidth=2.5, color=theme.ACCENT, point=True,
            interpolate="monotone").encode(
            x=x, y=y,
            tooltip=[alt.Tooltip("date:T", title="Date"),
                     alt.Tooltip("cumulative:Q", title="Banked", format="$,.0f"),
                     alt.Tooltip("target:Q", title="A steady plan",
                                 format="$,.0f")]))
        if float(fore["target"].max()) > 0:
            layers.append(alt.Chart(fore).mark_line(
                strokeDash=[6, 4], strokeWidth=2, color=theme.AMBER).encode(
                x=x, y=alt.Y("target:Q", title=None)))
    return alt.layer(*layers)


# ----------------------------------------------------------------- pnl bars
# Bars get thinner as the grain gets finer: 45 daily bars and 12 monthly ones
# cannot wear the same width without one of them becoming a barcode. Sized for
# a wide desktop window - four monthly bars at 26px on a 1,600px chart is four
# pins in a field, and the width is information about nothing.
_BAR_SIZE = {"day": 9, "week": 22, "month": 46}
_BACK_SIZE = {"day": 15, "week": 36, "month": 74}
_GRAIN_WORD = {"day": "Day", "week": "Week", "month": "Month"}

def _backdrop_helps(df) -> bool:
    """Is the other book worth drawing behind this one?"""
    back = float(df["back"].abs().max() or 0.0)
    if back <= 0:
        return False
    fore = float(df["fore"].abs().max() or 0.0)
    return fore <= 0 or back <= fore * BACKDROP_MAX_RATIO


def pnl_bars(rows: list[dict[str, Any]], grain: str = "month",
             foreground: str = "real"):
    """Money banked per period, at whatever zoom she picked.

    rows come from pnl.buckets(), which carries `banked` for the selected book
    and `back` for the other one and has no key that adds them.

    THE TARGET IS A TICK PER BAR, NOT ONE LINE ACROSS THE CHART
    -----------------------------------------------------------
    A single horizontal rule only tells the truth when every period is worth
    the same. It is not: the month in progress has had fewer days than the
    ones behind it, a week straddling two months of different lengths prorates
    across both, and the month she funded the account in started mid-way. A
    tick sitting on each bar carries that period's OWN target, which is the
    only version of the line that is correct at every grain.

    It also borrows the bullet chart's vocabulary - a black tick is already
    what "the goal itself" looks like on this tab - so the two pictures teach
    each other rather than each inventing a notation.

    The backdrop book is a WIDER, paler bar on the same band as the
    foreground, not a bar beside it. Side by side would read as two results
    being compared; behind reads as history, which is what it is.
    """
    other = "practice" if foreground == "real" else "real"
    df = pd.DataFrame([{"label": r["label"], "short": r["short"],
                        "key": r["key"], "fore": r["banked"],
                        "back": r["back"], "target": r["target"],
                        "premium": r["premium"], "closed": r["closed"]}
                       for r in rows]).sort_values("key")
    order = list(df["label"])
    word = _GRAIN_WORD.get(grain, "Period")

    # The axis shows a short label, the tooltip the full one. "8/9" fits under
    # a 9px bar; "Mon 8/9" does not, and 45 of them overlap into a smear. Only
    # the day grain needs the trim, and Altair rejects an explicit None for
    # labelExpr rather than ignoring it, so the key is added or it is not.
    axis_kw: dict[str, Any] = {"labelAngle": 0, "labelFontSize": 12,
                               "labelColor": theme.SECONDARY, "labelPadding": 6}
    if grain == "day":
        axis_kw["labelExpr"] = "split(datum.label, ' ')[1]"
    x = alt.X("label:N", sort=order, title=None,
              scale=alt.Scale(paddingInner=0.35, paddingOuter=0.3),
              axis=alt.Axis(**axis_kw))
    # zero=True is load-bearing, not tidiness. A layered chart resolves one
    # shared y scale, and the tick layer does not carry a bar's implicit zero -
    # so the domain came out as [min(data), max(data)] and the bars rendered
    # off the bottom of the chart entirely. A P&L bar measured from $1,500
    # instead of $0 is a bar that lies about its own size.
    y_scale = alt.Scale(zero=True)
    y = alt.Y("fore:Q", title="Banked ($)", scale=y_scale,
              axis=alt.Axis(format="$,.0f", tickCount=5, labelFontSize=12,
                            labelColor=theme.SECONDARY))

    layers = []
    if _backdrop_helps(df):
        layers.append(alt.Chart(df).mark_bar(
            size=_BACK_SIZE.get(grain, BACK_SIZE), cornerRadiusEnd=3,
            color=theme.BORDER_STRONG, opacity=BACK_OPACITY).encode(
            x=x, y=alt.Y("back:Q", title="Banked ($)", scale=y_scale,
                         axis=alt.Axis(format="$,.0f", tickCount=5)),
            tooltip=[alt.Tooltip("label:N", title=word),
                     alt.Tooltip("back:Q", title=f"{other.title()} book",
                                 format="$,.0f")]))

    layers.append(alt.Chart(df).mark_bar(
        size=_BAR_SIZE.get(grain, FORE_SIZE), cornerRadiusEnd=3).encode(
        x=x, y=y,
        color=alt.condition("datum.fore >= 0",
                            alt.value(theme.GREEN), alt.value(theme.RED)),
        tooltip=[alt.Tooltip("label:N", title=word),
                 alt.Tooltip("fore:Q", title="Banked", format="$,.0f"),
                 alt.Tooltip("premium:Q", title="Premium sold", format="$,.0f"),
                 alt.Tooltip("closed:Q", title="Trades closed"),
                 alt.Tooltip("target:Q", title=f"{word} target",
                             format="$,.0f")]))

    if df["target"].sum() > 0:
        layers.append(alt.Chart(df[df["target"] > 0]).mark_tick(
            thickness=2, size=_BACK_SIZE.get(grain, BACK_SIZE),
            color=theme.INK).encode(
            x=x, y=alt.Y("target:Q", title=None, scale=y_scale),
            tooltip=[alt.Tooltip("label:N", title=word),
                     alt.Tooltip("target:Q", title=f"{word} target",
                                 format="$,.0f")]))
    return alt.layer(*layers)


# ------------------------------------------------------------------ calendar
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day_calendar(days: list[dict[str, Any]]):
    """One month as a grid: a green day earned, a red day cost, empty is empty.

    days comes from month_report.days(), which includes the days nothing
    happened on. They have to be there: a month drawn only on its earning days
    is a scatter of green with no shape, and the shape is the question - does
    her income cluster near expiry, or is it spread out?

    Zero is white, not the pale end of green. domainMid pins it, so a quiet day
    never reads as a small win.
    """
    df = pd.DataFrame([{
        "Day": d["day"],
        "Banked": d["banked"],
        "Weekday": _DOW[d["weekday"]],
        "Week": d["week_index"],
        "active": abs(d["banked"]) > 0,
    } for d in days])
    top = max(float(df["Banked"].abs().max() or 0.0), 1.0)

    base = alt.Chart(df).encode(
        x=alt.X("Weekday:N", sort=_DOW, title=None,
                axis=alt.Axis(labelAngle=0, labelFontSize=12, orient="top",
                              labelColor=theme.SECONDARY, ticks=False,
                              domain=False)),
        y=alt.Y("Week:O", title=None, axis=None))

    # Two rect layers: the quiet days get a flat tile fill so they read as
    # "nothing here" rather than as the palest shade of a win.
    quiet = base.transform_filter("datum.active === false").mark_rect(
        cornerRadius=6, color=theme.TILE, stroke="#FFFFFF", strokeWidth=3)
    active = base.transform_filter("datum.active").mark_rect(
        cornerRadius=6, stroke="#FFFFFF", strokeWidth=3).encode(
        color=alt.Color("Banked:Q", legend=None,
                        scale=alt.Scale(range=[theme.RED, "#FFFFFF", theme.GREEN],
                                        domain=[-top, 0, top], domainMid=0)),
        tooltip=[alt.Tooltip("Day:O", title="Day"),
                 alt.Tooltip("Banked:Q", format="$,.0f")])
    numbers = base.mark_text(fontSize=11, fontWeight="bold",
                             color=theme.SECONDARY, dy=-8).encode(
        text=alt.Text("Day:O"))
    amounts = base.transform_filter("datum.active").mark_text(
        fontSize=11, fontWeight="bold", color=theme.INK, dy=7).encode(
        text=alt.Text("Banked:Q", format="$,.0f"))
    return alt.layer(quiet, active, numbers, amounts)


# ------------------------------------------------------------------ drawdown
def drawdown(series: list[dict[str, Any]], foreground: str = "real"):
    """How far below your best day you have been, as a filled area under zero.

    Its own picture rather than a line on the equity chart: a drawdown drawn on
    the same axis as a rising total is a wiggle, and the point of the question
    is how deep the dips were, not whether the line went up.
    """
    rows = [r for r in series if r["book"] == foreground]
    peak, out = 0.0, []
    for r in sorted(rows, key=lambda r: r["date"]):
        peak = max(peak, r["cumulative"])
        out.append({"date": r["date"], "Below best": round(r["cumulative"] - peak, 2)})
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"])

    return alt.Chart(df).mark_area(
        color=theme.RED, opacity=0.18, interpolate="monotone",
        line={"color": theme.RED, "strokeWidth": 2}).encode(
        x=alt.X("date:T", title=None,
                axis=alt.Axis(format="%d %b", labelFontSize=12,
                              labelColor=theme.SECONDARY, tickCount=6)),
        y=alt.Y("Below best:Q", title="Below your best day ($)",
                axis=alt.Axis(format="$,.0f", tickCount=4, labelFontSize=12,
                              labelColor=theme.SECONDARY)),
        tooltip=[alt.Tooltip("date:T", title="Date"),
                 alt.Tooltip("Below best:Q", format="$,.0f")])
