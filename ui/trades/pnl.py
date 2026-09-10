"""The Profit page: what she made, at whatever zoom she picks.

WHAT THIS REPLACES
------------------
The day/week/month picture existed before this page, in three places she could
not get to. The day calendar was inside a collapsed expander, inside one month,
skipped entirely when that month had no paying day and empty for all-time. The
weekly bars only rendered inside a single month's income report. The month bars
sat at the bottom of a different section. Nothing let her change the zoom, and
"what did I make this week" had no answer anywhere on the tab.

One switch now drives the whole page. Day, Week, Month and All change the
number in the hero and the bars under it, and nothing else about the page
moves - so changing the zoom is reading the same report at a different
distance rather than arriving somewhere new.

ALL IS A SCOPE, NOT A GRAIN
---------------------------
The first three buttons bucket time. "All" is the same monthly buckets with the
trim taken off and an all-time summary above them, because a single bar
covering fourteen months is not a picture of anything. It is also the only
place the drawdown belongs: how deep the dips got is a question about a whole
history, not about one month.

Dollar signs: &#36; inside HTML, \\$ inside theme.note(). A raw pair turns
Streamlit's markdown into LaTeX and garbles the line.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

import pandas as pd
import streamlit as st

from ui import income_report, theme
from ui.trades import charts

# The switch, and what each option means to the engine. "All" is month buckets
# with no trim - see the module docstring.
VIEWS = {
    "Day": ("day", -1),
    "Week": ("week", -1),
    "Month": ("month", -1),
    "All": ("month", None),
}

# What one bucket is called in a sentence, singular and plural.
_NOUN = {"day": ("day", "days"), "week": ("week", "weeks"),
         "month": ("month", "months")}


def render(all_pos, settings, live_from, mode: str, every_pos=None,
           today=None) -> None:
    """The whole Profit page.

    all_pos is ONE book - the account switch has already scoped it. every_pos
    is the unsplit log, which the bars need so they can draw the other book
    faded behind this one. No total on this page ever comes from every_pos.
    """
    from src.engine import pnl as pnl_mod

    today = today or _dt.date.today()
    every_pos = all_pos if every_pos is None else every_pos

    theme.section("What you actually made, at whatever zoom you want",
                  "Profit and loss")

    choice = st.segmented_control(
        "Zoom", list(VIEWS), default="Month", key="pnl_grain",
        label_visibility="collapsed") or "Month"
    grain, limit = VIEWS[choice]

    rows = pnl_mod.buckets(every_pos, grain, settings, live_from, today, mode,
                           limit=limit)
    if not rows:
        theme.note("**Nothing logged yet.** This page fills in on its own from "
                   "your first trade - there is nothing extra to type.")
        return

    summary = pnl_mod.summary(rows)
    _hero(rows, summary, choice, grain, pnl_mod, today)
    _stats(summary, grain, choice)

    st.write("")
    income_report._render(charts.pnl_bars(rows, grain, mode), height=280,
                          labels=[])
    _bars_legend(rows, grain, mode)

    st.write("")
    _running_total(every_pos, settings, live_from, mode, today)

    if grain == "day":
        _calendar(all_pos, live_from, mode, today)
    if choice == "All":
        _drawdown(every_pos, settings, live_from, mode, today)

    st.write("")
    _table(rows, grain, choice)


# --------------------------------------------------------------- the hero
def _hero(rows, summary, choice: str, grain: str, pnl_mod, today) -> None:
    """The one number, and what it is measured against.

    Deliberately NOT worded "banked this month of your $3,500 goal" - the
    dashboard band already says exactly that about this month, and the same
    sentence twice on one tab is how the old page ended up feeling like it
    repeated itself.
    """
    if choice == "All":
        banked = summary["banked"]
        target = summary["target"]
        label = "BANKED SINCE YOU STARTED"
        sub = (f"across {summary['active']} earning "
               f"{_NOUN['month'][summary['active'] != 1]} of "
               f"{summary['buckets']}")
        if target > 0:
            ahead = banked - target
            word = "ahead of" if ahead >= 0 else "behind"
            sub = (f"a steady plan over the same stretch would have made "
                   f"{theme.money(target)}, so you are "
                   f"{theme.money(abs(ahead))} {word} it")
        theme.hero_stat(label, theme.signed(banked), sub,
                        "up" if banked >= 0 else "down")
        return

    cur = pnl_mod.current(rows)
    if cur is None:
        return
    banked = cur["banked"]
    paced = cur["pace_target"]
    noun = _NOUN[grain][0]

    if paced > 0:
        ahead = banked - paced
        word = "ahead of" if ahead >= 0 else "behind"
        sub = (f"{theme.money(abs(ahead))} {word} where a steady plan would be "
               f"by now  ·  {theme.money(cur['target'])} is the whole "
               f"{noun}")
    elif cur["target"] > 0:
        sub = f"{theme.money(cur['target'])} is what this {noun} is worth"
    else:
        sub = f"this {noun} so far"

    theme.hero_stat(f"{cur['label'].upper()}", theme.signed(banked), sub,
                    "up" if banked >= 0 else "down")


def _stats(summary: dict, grain: str, choice: str) -> None:
    """The supporting numbers - what the hero does not say on its own."""
    one, many = _NOUN[grain]
    active = summary["active"]
    avg = summary["average"]
    best, worst = summary["best"], summary["worst"]

    cards = [
        theme.stat("TOTAL BANKED",
                   theme.signed(summary["banked"]),
                   f"over {summary['buckets']} {many}",
                   "up" if summary["banked"] >= 0 else "down"),
        theme.stat("PREMIUM SOLD", theme.money(summary["premium"]),
                   f"{summary['opened']} opened, {summary['closed']} closed"),
        theme.stat(f"AVERAGE EARNING {one.upper()}",
                   theme.signed(avg) if avg is not None else "-",
                   (f"{active} of {summary['buckets']} {many} settled money"
                    if active else f"no {one} has settled money yet"),
                   "up" if avg and avg > 0 else "down" if avg else "neutral"),
    ]
    if best is not None:
        cards.append(theme.stat(
            f"BEST {one.upper()}", theme.signed(best["banked"]),
            best["label"], "up" if best["banked"] >= 0 else "down"))
    if worst is not None and worst["banked"] < 0:
        cards.append(theme.stat(
            f"WORST {one.upper()}", theme.signed(worst["banked"]),
            worst["label"], "down"))
    theme.stat_row(cards)


def _bars_legend(rows, grain: str, mode: str) -> None:
    one, many = _NOUN[grain]
    theme.explain(
        "How to read this",
        f"Each bar is one {one}. **Green** is money that settled into your "
        f"account, **red** is a {one} that cost you. The **black tick** on a "
        f"bar is what that {one} is worth against your monthly goal - a tick "
        f"per bar rather than one line across the chart, because the {one} "
        f"you are in now has had less time than the finished ones and a flat "
        f"line would score it as a miss.\n\n"
        f"Empty {many} are drawn on purpose. A chart with only the earning "
        f"{many} on it is a scatter with no shape, and the shape is the "
        f"question - is your income spread out, or does it all land in the "
        f"same week of the month?")
    if not any(r["back"] for r in rows):
        return
    other = "practice" if mode == "real" else "real-money"
    fore = max((abs(r["banked"]) for r in rows), default=0.0)
    back = max((abs(r["back"]) for r in rows), default=0.0)

    if back <= fore * charts.BACKDROP_MAX_RATIO or fore <= 0:
        theme.legend_note(
            f"The wider faded bars are your {other} book. They are never added "
            f"into any total on this page - they are here so a book with a "
            f"short history still has something to be read against.")
    else:
        # Saying nothing would be worse than either drawing it or not: a book
        # that quietly vanishes from a chart is the kind of thing that makes
        # her wonder what else is missing.
        theme.legend_note(
            f"Your {other} book is left off this chart. Its best {one} was "
            f"{theme.money(back)} against {theme.money(fore)} here, and on one "
            f"shared scale that flattens every bar above into a sliver. Switch "
            f"accounts at the top of the tab to see that book on its own.")


# ------------------------------------------------------------ the pictures
def _running_total(every_pos, settings, live_from, mode: str, today) -> None:
    """Every dollar banked, against the ramp a steady plan would draw."""
    from src.engine import goals

    series = goals.cumulative_series(every_pos, settings, live_from, today)
    if not series:
        return
    t = goals.targets_from(settings)
    theme.section("Every dollar you have banked, against the plan", "Total")
    income_report._render(charts.cumulative_vs_target(series, mode), height=250)
    theme.explain(
        "How to read this",
        "The solid green line is your money, running total. The **dashed amber "
        f"line** is what a steady **\\${t['monthly']:,.0f} a month** would have "
        "produced by each date. Where green is above amber you are ahead of "
        "the plan.")
    back = [r for r in series if r["book"] != mode]
    if not back:
        return
    other = "practice" if mode == "real" else "real-money"
    peak_back = max(abs(r["cumulative"]) for r in back)
    fore_rows = [r for r in series if r["book"] == mode]
    peak_fore = max((abs(r["cumulative"]) for r in fore_rows), default=0.0)

    if peak_fore <= 0 or peak_back <= peak_fore * charts.BACKDROP_MAX_RATIO:
        theme.legend_note(
            f"The faded grey line is your {other} book. It is never added into "
            "any total on this page.")
    else:
        theme.legend_note(
            f"Your {other} book is left off this chart - it has banked "
            f"{theme.money(peak_back)} against {theme.money(peak_fore)} here, "
            f"and on one shared scale that presses this line flat along the "
            f"bottom. Switch accounts at the top of the tab to see it.")


def _calendar(all_pos, live_from, mode: str, today) -> None:
    """Which days actually paid, this month.

    Out of the expander it used to hide in. At the Day zoom this is the whole
    point of the page - the bars say how much, and the grid says whether it
    lands in the same week every month, which is worth knowing before a bad
    week lands on all of it at once.
    """
    from src.engine import month_report as mr

    days = mr.days(all_pos, mr.month_key(today), live_from=live_from, mode=mode)
    if not days:
        return
    st.write("")
    theme.section(f"Which days paid in {today:%B}", "The month as a grid")
    st.altair_chart(charts.day_calendar(days).properties(height=250)
                    .configure_view(strokeWidth=0), width="stretch")
    theme.explain(
        "How to read this",
        "Green is a day money settled, red is a day one cost you, and the "
        "depth of the colour is the size. Most of your income landing in the "
        "same week of the month usually means your expirations are bunched.")


def _drawdown(every_pos, settings, live_from, mode: str, today) -> None:
    """How far below her best day she has been.

    Its own picture rather than a line on the equity chart, and only on the
    all-time view: how deep the dips got is a question about a whole history.
    The dashboard states the number as a card; this is what is behind it.
    """
    from src.engine import goals

    series = goals.cumulative_series(every_pos, settings, live_from, today)
    if not [r for r in series if r["book"] == mode]:
        return
    st.write("")
    theme.section("How far below your best day you have been", "Drawdown")
    income_report._render(charts.drawdown(series, mode), height=200)
    theme.explain(
        "How to read this",
        "Every working system has dips - this is how deep yours have got. The "
        "line sits at zero whenever you are at a new high, and dips below it "
        "when a loss pulls you back from your best day. What matters is not "
        "that they happen but whether they keep getting deeper.")


# -------------------------------------------------------------- the table
def _table(rows: list[dict[str, Any]], grain: str, choice: str) -> None:
    """One row per period. The chart shows the shape, this shows the numbers.

    Newest first: the question is almost always about the period she is in or
    the one just gone, and those should not be at the bottom of a scroll.
    """
    one, many = _NOUN[grain]
    theme.section(f"Every {one}, in numbers", "The ledger")

    df = pd.DataFrame([{
        "Period": r["label"],
        "Opened": r["opened"],
        "Closed": r["closed"],
        "Premium sold $": r["premium"],
        "Banked $": r["banked"],
        "Target $": r["target"],
        "vs target $": round(r["banked"] - r["target"], 2) if r["target"] else None,
        "Running total $": r["cumulative"],
    } for r in reversed(rows)])

    # A period before she funded the account has no target, so no "vs target"
    # either. Mixed floats and None make an object column, and Streamlit prints
    # the literal word "None" in the gaps - as NaN they render as blank cells.
    df["vs target $"] = pd.to_numeric(df["vs target $"], errors="coerce")

    st.dataframe(df, width="stretch", hide_index=True, column_config={
        "Period": st.column_config.TextColumn(
            "Period", help=f"One {one} of trading"),
        "Opened": st.column_config.NumberColumn(
            "Opened", help=f"Trades you opened in this {one}", format="%d"),
        "Closed": st.column_config.NumberColumn(
            "Closed", help=f"Trades that ended in this {one}", format="%d"),
        "Premium sold $": st.column_config.NumberColumn(
            "Premium sold", format="$%,.0f",
            help="What you sold - opening credits plus what rolls sold for"),
        "Banked $": st.column_config.NumberColumn(
            "Banked", format="$%,.0f",
            help="Money that actually settled: closes, rolls and leg closes"),
        "Target $": st.column_config.NumberColumn(
            "Target", format="$%,.0f",
            help=f"What a steady plan says this {one} is worth"),
        "vs target $": st.column_config.NumberColumn(
            "vs target", format="$%,.0f",
            help="Banked minus target. Blank where there is no target."),
        "Running total $": st.column_config.NumberColumn(
            "Running total", format="$%,.0f",
            help="Every dollar banked up to and including this period"),
    })
    theme.explain(
        "Premium sold and banked do not subtract",
        "**Premium sold** is what you collected for opening trades in this "
        f"{one}. **Banked** is money that finished settling in it. They are "
        "about different trades: a spread you opened in one month and closed "
        "in the next puts its premium in the first and its result in the "
        "second. Subtracting one from the other gives a number about nothing.")
