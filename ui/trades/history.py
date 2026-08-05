"""Looking back: one month at a time, or everything since she started.

The picker decides the scope and everything below follows it.

THE ALL-TIME VIEW USED TO BE WRONG
----------------------------------
It built an all-time report and handed it to a band that computes
`banked / monthly_goal`, so a whole account's history was shown as a percentage
of a ONE MONTH $3,500 target. Six days of real trading read as 17% of a goal it
had never been measured against.

It now goes through goals.span_report, which carries the target the span
actually had - $3,500 a month, prorated by days, from the day she funded. Six
days is about $677, and being at $600 of that is a sentence worth reading.

The month view keeps the income report, which was already a good report. What
it lost is its goals panel: her capital, targets and budget are the same four
numbers every month, so printing them inside each one meant reading her own
plan three times to scroll through three months. They live on the dashboard now.
"""

from __future__ import annotations

import datetime as _dt

import streamlit as st

from ui import components, income_report, theme
from ui.trades import charts
from ui.trades.account import _live_from

ALL_TIME = "All time"


def _results_section(all_pos, settings, bp_used: float, mode: str = "real",
                     every_pos=None) -> None:
    """One results block, scoped by a single picker.

    There used to be two: "Monthly tracking" and "Your results". They answered
    the same question at different scopes and printed the same four numbers -
    closed trades, win rate, profit against goal, a chart - one above the other.
    With every trade in one month they were literally identical on screen, which
    is what made the tab look broken.

    every_pos is the UNSPLIT log. The month bars draw the other book faded
    behind this one, so they need both; every total on this page still comes
    from all_pos, which is one book only.
    """
    from src.engine import goals
    from src.engine import month_report as mr
    from src.engine import positions as pos_mod

    every_pos = all_pos if every_pos is None else every_pos
    today = _dt.date.today()
    live_from = _live_from(settings)
    monthly_goal = float(settings["targets"]["monthly"])

    theme.section("How each month actually went", "History")

    summaries = pos_mod.monthly_summary(all_pos)
    names = [ALL_TIME] + [m["label"] for m in summaries]
    if st.session_state.get("trades_month_pick") not in names:
        st.session_state.pop("trades_month_pick", None)
    # Default to this month: the question she opens the tab with is usually
    # "how is THIS month going", not "how has it all gone".
    idx = 1 if len(names) > 1 else 0
    pick = st.selectbox("Show me", names, index=idx, key="trades_month_pick",
                        help="One month at a time, or everything since you started.")

    if pick == ALL_TIME:
        _span_view(all_pos, settings, live_from, mode, today)
    else:
        _month_view(all_pos, summaries, settings, live_from, mode, pick)

    # The month-by-month bars sit under both views: they are the one picture
    # that only makes sense across months, so scoping them to one would be odd.
    st.divider()
    _month_bars(every_pos, settings, live_from, mode, today, monthly_goal)


def _span_view(all_pos, settings, live_from, mode: str, today) -> None:
    """Everything since she started, against the target the span actually had.

    The band's goal label is the whole fix. "of your $3,500 goal" under an
    all-time total is a category error - it compares a span of months to one
    month's target and gets a percentage that means nothing.
    """
    from src.engine import goals

    report = goals.span_report(all_pos, settings, live_from, today, mode)
    days = report["days_elapsed"]
    if report["span_target"] > 0:
        label = (f"of the {income_report._d(report['span_target'])} a steady "
                 f"plan would have produced in {days} day"
                 f"{'' if days == 1 else 's'}")
    else:
        label = "banked since you started"

    income_report.render_band(report, report["span_target"], goal_label=label,
                              title=f"All time &middot; {days} day"
                                    f"{'' if days == 1 else 's'}")

    if not report["has_activity"]:
        theme.note("**Nothing logged yet.** This page fills in on its own from "
                   "your first trade - there is nothing extra to type.")
        return

    ahead = report["banked"] - report["span_target"]
    if report["span_target"] > 0:
        word = "ahead of" if ahead >= 0 else "behind"
        theme.note(
            f"Since you started you have banked **\\${report['banked']:,.0f}**. "
            f"A steady **\\${float(settings['targets']['monthly']):,.0f} a month** "
            f"over the same {days} days would have produced "
            f"**\\${report['span_target']:,.0f}**, so you are "
            f"**\\${abs(ahead):,.0f} {word}** the plan. This is the number the "
            "monthly goal adds up to - it is not a second, harder target.")

    st.write("")
    income_report.render_tiles(report, report["span_target"],
                               float(settings["risk_limits"]["monthly_bp_limit"]))
    st.divider()
    income_report.render_strategy(report)
    st.divider()
    income_report.render_underlyings(report)
    st.divider()
    income_report.render_management(report)


def _month_view(all_pos, summaries, settings, live_from, mode: str,
                pick: str) -> None:
    """One month, in full, through the income report."""
    from src.engine import month_report as mr

    month_key = next(m["month"] for m in summaries if m["label"] == pick)
    report = mr.build(all_pos, month=month_key, live_from=live_from, mode=mode)
    monthly_goal = float(settings["targets"]["monthly"])

    # An empty REAL report in a month she knows she traded is the one moment
    # this design can confuse her, so it explains itself rather than saying
    # "nothing logged" about a month full of practice trades.
    empty_note = ""
    if mode == "real" and not report["has_activity"] and live_from:
        empty_note = (
            f"**No real-money trades in {report['label']} yet.** You funded on "
            f"{live_from.day} {live_from:%B}, and this book holds only real money. "
            "Any trades you are thinking of are in your practice book - switch "
            "accounts at the top of this tab to see them. Your first real trade "
            "starts this page off at zero, which is exactly where a real-money "
            "record should start.")

    # The dashboard already opened with this month's banked against its goal,
    # so repeating the band here would be the same number twice on one page.
    # Everything under the band is new, so only the band is dropped.
    if report["is_current"]:
        theme.note(f"**Where {report['label']}'s money came from.** The totals "
                   "for this month are at the top of the tab - this is the "
                   "breakdown behind them.")
    income_report.render(report, settings, pace=mr.pace(report, monthly_goal),
                         empty_note=empty_note,
                         show_band=not report["is_current"])

    entry = next(m for m in summaries if m["label"] == pick)
    if entry["rows"]:
        st.divider()
        st.markdown("**Every trade this month:**")
        st.dataframe(components.month_trades_dataframe(entry["rows"]),
                     width="stretch", hide_index=True,
                     column_config=components.month_trades_column_config())

    _calendar(all_pos, month_key, live_from, mode, report["label"])


def _calendar(all_pos, month_key: str, live_from, mode: str, label: str) -> None:
    """Which days actually paid.

    Behind a keyed expander: it is the one thing on this page that is a
    curiosity rather than a decision, and a keyed expander in Streamlit 1.58
    stays open through a rerun instead of snapping shut.
    """
    from src.engine import month_report as mr

    days = mr.days(all_pos, month_key, live_from=live_from, mode=mode)
    if not days or not any(d["banked"] for d in days):
        return
    with st.expander(f"📅 Which days actually paid in {label}",
                     key=f"cal_{month_key}"):
        st.altair_chart(charts.day_calendar(days).properties(height=260)
                        .configure_view(strokeWidth=0), width="stretch")
        theme.note("Green is a day money settled, red is a day one cost you, "
                   "and the depth of the colour is the size. Most of your "
                   "income landing in the same week of the month usually means "
                   "your expirations are bunched - which is worth knowing "
                   "before a bad week lands on all of them at once.")


def _month_bars(every_pos, settings, live_from, mode: str, today,
                monthly_goal: float) -> None:
    """Profit per month, with the other book faded behind and the goal dashed.

    The one picture that only makes sense across months. It is also where the
    practice history earns its place: a real book five days old has one bar,
    and one bar is not a trend.
    """
    from src.engine import goals

    rows = goals.month_table(every_pos, settings, live_from, today)
    other = "practice" if mode == "real" else "real"
    if not any(r["real"] or r["practice"] for r in rows):
        return

    theme.section("Month by month", "The shape of it")
    income_report._render(charts.month_bars(rows, monthly_goal, mode),
                          height=280, labels=[])
    theme.note(f"The dashed line is your **\\${monthly_goal:,.0f}** monthly goal. "
               "A month below it is not a failure - your rules do not let you "
               "force trades to hit a number, and the months that follow the "
               "rules are the ones that repeat.")
    if any(r[other] for r in rows):
        book = "practice" if other == "practice" else "real-money"
        theme.legend_note(
            f"The wider faded bars are your {book} book. They are never added "
            "into any total on this page - they are here so a book with one "
            "month in it still has something to be read against.")
