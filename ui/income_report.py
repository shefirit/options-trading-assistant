"""The month's income report, drawn the way a report should be drawn.

One month on one screen: a headline band, tiles for the numbers she checks,
then three "where did it come from" panels - by week, by strategy, by name -
and a discipline scorecard.

Design notes, all deliberate:

* The BANKED number is the hero, not the premium sold. Income reports that lead
  with gross premium flatter the trader: premium sold says nothing about what
  buying the position back cost. The gross number is here, one size down, where
  it belongs.
* Every tile is a flex item with a min-width, so the same markup is a five-wide
  strip on a laptop and a clean stack on a phone. Nothing is behind the sidebar.
* Text sits at or above her 9:1 contrast floor - INK and SECONDARY only, no
  Streamlit caption grey anywhere.
* Dollar signs go through _d() / _esc(), because a raw pair of them turns
  Streamlit's markdown into LaTeX and garbles the line.
"""

from __future__ import annotations

import html as _html
from typing import Any, Optional

import altair as alt
import pandas as pd
import streamlit as st

from ui import theme

# The chart palette, in fixed slot order - blue, orange, green, then the
# supporting tones. Fixed rather than cycled so the same strategy keeps the
# same colour between the pie and the table beside it.
SLOTS = ["#2a78d6", "#eb6834", "#0B7A54", "#7B4FBF", "#B45309", "#0E7490",
         "#9D174D", "#4E625A"]

# The dark band at the top, lifted from the report style she liked. Deep navy
# rather than the app's green so the report reads as its own object - a page
# torn out of a monthly statement, not another panel of the tab.
BAND = "#12294A"
BAND_SUB = "#D8E6F7"


def _d(x: float, decimals: int = 0) -> str:
    """Dollars with an HTML-entity sign, safe inside st.markdown."""
    return f"&#36;{x:,.{decimals}f}"


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _pct(x: Optional[float], nd: int = 0) -> str:
    return f"{x * 100:.{nd}f}%" if x is not None else "-"


# ------------------------------------------------------------------- the band
def render_band(report: dict, goal: float) -> None:
    """The headline: which month, real or practice, and the three numbers that
    describe it in one line."""
    real = report["mode"] == "real"
    tag = "REAL MONEY" if real else "PRACTICE (PaperMoney)"
    tag_bg = "#0B7A54" if real else "#B45309"
    banked = report["banked"]
    pct = (banked / goal) if goal else 0.0
    closed = report["trades_closed"]
    opened = report["trades_opened"]

    st.markdown(
        f"""
        <div style="background:{BAND};border-radius:16px;padding:22px 24px;
                    margin:6px 0 14px;">
          <div style="display:flex;justify-content:space-between;
                      align-items:baseline;flex-wrap:wrap;gap:10px;">
            <div style="color:#FFFFFF;font-size:1.45rem;font-weight:800;
                        letter-spacing:.01em;">
              {_esc(report['label'])} Income Report
            </div>
            <span style="background:{tag_bg};color:#FFFFFF;font-size:.78rem;
                         font-weight:800;letter-spacing:.06em;padding:5px 12px;
                         border-radius:999px;">{tag}</span>
          </div>
          <div style="display:flex;gap:34px;flex-wrap:wrap;margin-top:16px;">
            <div style="min-width:190px;">
              <div style="color:{BAND_SUB};font-size:.8rem;font-weight:700;
                          letter-spacing:.06em;">BANKED THIS MONTH</div>
              <div style="color:#7DE8B0;font-size:2.6rem;font-weight:800;
                          line-height:1.1;">{_d(banked)}</div>
              <div style="color:{BAND_SUB};font-size:.9rem;font-weight:600;">
                {_pct(pct)} of your {_d(goal)} goal</div>
            </div>
            <div style="min-width:150px;">
              <div style="color:{BAND_SUB};font-size:.8rem;font-weight:700;
                          letter-spacing:.06em;">PREMIUM SOLD</div>
              <div style="color:#FFFFFF;font-size:1.9rem;font-weight:800;
                          line-height:1.15;">{_d(report['premium_sold'])}</div>
              <div style="color:{BAND_SUB};font-size:.9rem;font-weight:600;">
                before the cost of closing</div>
            </div>
            <div style="min-width:150px;">
              <div style="color:{BAND_SUB};font-size:.8rem;font-weight:700;
                          letter-spacing:.06em;">TRADES</div>
              <div style="color:#FFFFFF;font-size:1.9rem;font-weight:800;
                          line-height:1.15;">{opened} opened</div>
              <div style="color:{BAND_SUB};font-size:.9rem;font-weight:600;">
                {closed} closed &middot; {report['still_open']} still running</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True)


# ------------------------------------------------------------------ the tiles
def _tile(label: str, value: str, sub: str, tone: str = theme.INK) -> str:
    return (
        f"<div style='flex:1 1 165px;min-width:165px;background:{theme.CARD};"
        f"border:1px solid {theme.BORDER_STRONG};border-radius:14px;"
        f"padding:14px 16px;'>"
        f"<div style='font-size:.76rem;font-weight:800;color:{theme.SECONDARY};"
        f"letter-spacing:.05em;'>{label}</div>"
        f"<div style='font-size:1.7rem;font-weight:800;color:{tone};"
        f"line-height:1.2;margin:2px 0;'>{value}</div>"
        f"<div style='font-size:.85rem;font-weight:600;color:{theme.SECONDARY};"
        f"line-height:1.45;'>{sub}</div></div>")


def render_tiles(report: dict, goal: float, bp_limit: float) -> None:
    """The six numbers worth reading every time, in a row that becomes a
    stack on a phone."""
    cap = report["capture_pct"]
    # Above her 50% target is the healthy band. Below it means she is closing
    # into losses more often than the rule intends; far above it usually means
    # she is holding past 21 days, which the SOP does not allow.
    cap_tone = (theme.INK if cap is None
                else theme.GREEN if cap >= 0.5 else theme.AMBER)
    avg_day = report["avg_per_active_day"]
    per_close = report["avg_per_close"]
    win = report["win_rate"]

    tiles = [
        _tile("COST TO CLOSE", _d(report["cost_to_close"]),
              "spent buying positions back - the gap between premium sold "
              "and banked"),
        _tile("PREMIUM CAPTURED", _pct(cap) if cap is not None else "-",
              (f"of the credit kept on {report['capture_trades']} credit "
               f"trades" if cap is not None else "no credit trades closed yet"),
              cap_tone),
        _tile("WIN RATE", _pct(win) if win is not None else "-",
              f"{report['wins']} won &middot; {report['losses']} lost"),
        _tile("PER CLOSED TRADE", _d(per_close) if per_close is not None else "-",
              "average result, wins and losses together"),
        _tile("ACTIVE DAYS", str(report["active_days"]),
              (f"{_d(avg_day)} a day you traded"
               if avg_day is not None else "days anything happened")),
    ]
    # Buying power is the one number here that is about RISK rather than
    # reward, so it gets a tile of its own and turns amber before the limit,
    # not at it.
    bp = report["bp_opened"]
    if bp_limit > 0:
        bp_share = bp / bp_limit
        bp_tone = (theme.RED if bp_share >= 1 else theme.AMBER
                   if bp_share >= 0.8 else theme.INK)
        tiles.append(_tile(
            "BUYING POWER USED", _d(bp),
            f"of your {_d(bp_limit)} monthly budget &middot; {_pct(bp_share)}",
            bp_tone))
    st.markdown(
        f"<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px;'>"
        f"{''.join(tiles)}</div>", unsafe_allow_html=True)


def render_pace(pace: Optional[dict], goal: float) -> None:
    """Only for the month in progress: on pace, or behind, judged on days gone
    rather than on the calendar flipping over."""
    if pace is None:
        return
    st.write("")
    st.progress(min(max(pace["pct_of_goal"], 0.0), 1.0))
    if pace["still_needed"] <= 0:
        # The goal is already met. "$0 left with 0 days to do it" is technically
        # true and reads like a warning, which is the wrong feeling entirely.
        theme.note(
            f"**Goal met with {pace['days_left']} day(s) of the month left.** "
            f"You are \\${pace['ahead_by']:,.0f} past the pace a steady month "
            "would set. Your SOP has no rule that says stop, and no rule that "
            "says push - the entries that fit your rules still fit, and the "
            "ones that do not still do not.")
    elif pace["on_track"]:
        theme.note(
            f"**On pace.** Day {pace['days_elapsed']} of {pace['days_total']} - "
            f"a steady month would have banked about \\${pace['expected_by_now']:,.0f} "
            f"by now, and you are **\\${pace['ahead_by']:,.0f} ahead** of that. "
            f"\\${pace['still_needed']:,.0f} left to reach the goal, with "
            f"{pace['days_left']} days to do it.")
    else:
        theme.note(
            f"**Behind pace, and that is information, not a verdict.** Day "
            f"{pace['days_elapsed']} of {pace['days_total']} - a steady month "
            f"would sit near \\${pace['expected_by_now']:,.0f} by now, so you are "
            f"**\\${abs(pace['ahead_by']):,.0f} short** of that line. "
            f"\\${pace['still_needed']:,.0f} left with {pace['days_left']} days. "
            f"Your SOP does not have a rule for catching up, and there is a "
            f"reason: forcing trades to hit a monthly number is how the number "
            f"gets much worse.")


# ------------------------------------------------------------------- by week
def render_weeks(report: dict, weekly_goal: float) -> None:
    """Money banked per week against the $808 target - the picture that shows
    whether a month was steady or one lucky Tuesday."""
    weeks = report["weeks"]
    if not weeks:
        return
    theme.section("Was it steady, or was it one good week?", "By week")

    df = pd.DataFrame([{"Week": w["label"], "Banked": w["banked"],
                        "Premium sold": w["premium"], "order": str(w["start"])}
                       for w in weeks])
    order = list(df.sort_values("order")["Week"])
    bars = alt.Chart(df).mark_bar(size=34, cornerRadiusEnd=4).encode(
        y=alt.Y("Week:N", sort=order, title=None),
        # A tick every $100 turns the axis into a wall of numbers on a month
        # that banked a few thousand. Six labels is enough to read a bar.
        x=alt.X("Banked:Q", title="Banked ($)",
                axis=alt.Axis(tickCount=6, format="$,.0f")),
        color=alt.condition("datum.Banked >= 0",
                            alt.value(theme.GREEN), alt.value(theme.RED)),
        tooltip=[alt.Tooltip("Week:N"),
                 alt.Tooltip("Banked:Q", format="$,.0f"),
                 alt.Tooltip("Premium sold:Q", format="$,.0f")])
    chart = bars
    if weekly_goal:
        rule = alt.Chart(pd.DataFrame({"goal": [weekly_goal]})).mark_rule(
            color=theme.AMBER, strokeDash=[6, 4], strokeWidth=2).encode(x="goal:Q")
        chart = bars + rule
    st.altair_chart(chart.properties(height=max(150, 46 * len(weeks))),
                    width="stretch")

    best = report["best_week"]
    if best and best["banked"] > 0:
        st.markdown(
            f"<div style='background:{theme.TILE};border:1px solid {theme.BORDER_STRONG};"
            f"border-radius:12px;padding:12px 16px;'>"
            f"<span style='font-size:1.05rem;font-weight:800;color:{theme.GREEN};'>"
            f"🏆 Best week: {_esc(best['label'])} &middot; {_d(best['banked'])}</span>"
            f"<div style='color:{theme.CAPTION};font-size:.95rem;margin-top:4px;'>"
            f"{_d(best['premium'])} of premium sold across {best['trades']} "
            f"sale(s) that week.</div></div>",
            unsafe_allow_html=True)
    if weekly_goal:
        theme.note(f"The dashed line is your **\\${weekly_goal:,.0f}** weekly target. "
                   "Weeks run Monday to Sunday, and a week with nothing in it is a "
                   "week your rules told you to sit still - that is not a failure.")


# --------------------------------------------------------------- by strategy
def _donut(rows: list[dict], title: str) -> None:
    df = pd.DataFrame([{"Name": r["name"], "Premium": r["premium"],
                        "Share": r["share"]} for r in rows if r["premium"] > 0])
    if df.empty:
        return
    names = list(df["Name"])
    chart = alt.Chart(df).mark_arc(innerRadius=58, stroke="#FFFFFF",
                                   strokeWidth=2).encode(
        theta=alt.Theta("Premium:Q", stack=True),
        color=alt.Color("Name:N", sort=names, title=title,
                        scale=alt.Scale(domain=names,
                                        range=SLOTS[:len(names)]),
                        legend=alt.Legend(orient="bottom", columns=1,
                                          labelLimit=260, labelFontSize=13,
                                          titleFontSize=13)),
        tooltip=[alt.Tooltip("Name:N", title=title),
                 alt.Tooltip("Premium:Q", format="$,.0f"),
                 alt.Tooltip("Share:Q", format=".0%")])
    st.altair_chart(chart.properties(height=300), width="stretch")


def _breakdown_table(rows: list[dict], name_col: str) -> None:
    df = pd.DataFrame([{
        name_col: r["name"],
        "Premium sold": r["premium"],
        "Share": r["share"],
        "Banked": r["banked"],
        "Sales": r["trades"],
    } for r in rows])
    st.dataframe(df, width="stretch", hide_index=True, column_config={
        "Premium sold": st.column_config.NumberColumn(
            format="$%d", help="Premium you sold - before the cost of closing."),
        "Share": st.column_config.ProgressColumn(
            format="%.0f%%", min_value=0.0, max_value=1.0,
            help="This slice of the month's premium."),
        "Banked": st.column_config.NumberColumn(
            format="$%d",
            help="Money actually banked here - closes and roll credits. This "
                 "can be negative even when premium sold is large, and that "
                 "gap is the whole point of showing both."),
        "Sales": st.column_config.NumberColumn(
            help="Times you sold premium: opening a trade, or rolling a short "
                 "call out to a later one."),
    })


def render_strategy(report: dict) -> None:
    rows = report["by_strategy"]
    if not rows:
        return
    theme.section("Which of your eight strategies paid you?", "By strategy")
    left, right = st.columns([1, 1])
    with left:
        _donut(rows, "Strategy")
    with right:
        _breakdown_table(rows, "Strategy")
    top = rows[0]
    if top["premium"] > 0:
        theme.note(
            f"**{top['name']}** brought in the most premium this month - "
            f"\\${top['premium']:,.0f}, or {top['share'] * 100:.0f}% of the total. "
            "One strategy carrying most of a month is normal while you are "
            "learning. It is worth knowing which one, so a change in its "
            "behaviour does not surprise you.")


def render_underlyings(report: dict) -> None:
    rows = [r for r in report["by_underlying"] if r["premium"] > 0][:10]
    if not rows:
        return
    theme.section("Which names did the earning?", "Top producers")
    df = pd.DataFrame([{"Name": r["name"], "Premium": r["premium"],
                        "Banked": r["banked"]} for r in rows])
    bars = alt.Chart(df).mark_bar(size=30, cornerRadiusEnd=4).encode(
        y=alt.Y("Name:N", sort=list(df["Name"]), title=None),
        x=alt.X("Premium:Q", title="Premium sold ($)",
                axis=alt.Axis(tickCount=6, format="$,.0f")),
        color=alt.value(SLOTS[0]),
        tooltip=[alt.Tooltip("Name:N", title="Underlying"),
                 alt.Tooltip("Premium:Q", format="$,.0f"),
                 alt.Tooltip("Banked:Q", format="$,.0f")])
    labels = bars.mark_text(align="left", dx=6, fontSize=13,
                            fontWeight="bold", color=theme.INK).encode(
        text=alt.Text("Premium:Q", format="$,.0f"))
    st.altair_chart((bars + labels).properties(height=max(120, 40 * len(rows))),
                    width="stretch")


# ---------------------------------------------------------- trade management
def render_management(report: dict) -> None:
    """The part that matters more than the P&L while she is learning: did she
    exit the way her own rules say to exit?"""
    if not report["trades_closed"]:
        return
    theme.section("Did you trade the way your rules say?", "Discipline")

    followed, total = report["rules_followed"], report["rules_total"]
    share = followed / total if total else 0.0
    tone = theme.GREEN if share == 1 else theme.AMBER if share >= 0.5 else theme.RED

    cards = [_tile("BY THE RULES", f"{followed} of {total}",
                   "closes that used one of your four SOP exits", tone)]
    for row in report["exits"]:
        color = {"green": theme.GREEN, "amber": theme.AMBER,
                 "red": theme.RED}.get(row["tone"], theme.INK)
        cards.append(_tile(row["label"].upper(), str(row["count"]),
                           "by the rules" if row["by_rules"]
                           else "outside your four exits", color))
    st.markdown(
        f"<div style='display:flex;gap:12px;flex-wrap:wrap;'>{''.join(cards)}</div>",
        unsafe_allow_html=True)

    st.write("")
    if followed == total:
        theme.note(
            "**Every close this month followed your SOP.** That is the number "
            "to protect. A disciplined month with a small profit is a better "
            "month than a lucky one with a big profit, because only the first "
            "one repeats.")
    else:
        theme.note(
            f"**{total - followed} close(s) did not use one of your four SOP "
            "exits** - the 50% profit target, the 21-day time exit, the 2x stop "
            "loss, or letting it expire worthless. That is worth a minute of "
            "thought: an exit made for a reason outside the rules is the most "
            "common way a working system stops working.")

    if report["lessons"]:
        st.markdown("**What you wrote down this month:**")
        for lesson in report["lessons"]:
            theme.note(f"• {lesson}")


# ------------------------------------------------------------------ the page
def render(report: dict, settings: dict, pace: Optional[dict] = None,
           empty_note: str = "") -> None:
    """The whole report, top to bottom.

    empty_note replaces the generic "nothing logged yet" line when the caller
    knows something more useful - most importantly that the month DOES have
    trades, they are just practice ones sitting behind the account switch.
    """
    goal = float(settings["targets"]["monthly"])
    weekly_goal = float(settings["targets"]["weekly"])

    render_band(report, goal)

    if not report["has_activity"]:
        theme.note(empty_note or (
            f"**Nothing logged in {report['label']} yet.** Once you log your "
            "first trade this page fills in on its own - there is nothing "
            "extra to type. Log a trade you already placed with **Quick Log** "
            "in Records below, or build one in 🎯 Find a trade."))
        return

    render_tiles(report, goal, float(settings["risk_limits"]["monthly_bp_limit"]))
    render_pace(pace, goal)

    # The honest arithmetic, spelled out. The gap between the big premium
    # number and the money in the account is where beginners get fooled by
    # their own reports, so the report does that subtraction out loud.
    st.write("")
    with st.expander("How premium sold becomes money banked"):
        rolls = report["roll_income"]
        lines = [
            ("Premium sold this month", report["premium_sold"], theme.GREEN),
            ("Less what you paid to close positions", -report["cost_to_close"],
             theme.RED),
        ]
        body = "".join(
            f"<tr><td style='padding:6px 14px 6px 0;color:{theme.INK};"
            f"font-size:1rem;'>{_esc(label)}</td>"
            f"<td style='padding:6px 0;text-align:right;font-weight:800;"
            f"color:{color};font-size:1rem;'>{_d(value)}</td></tr>"
            for label, value, color in lines)
        st.markdown(
            f"<table style='width:100%;max-width:520px;border-collapse:collapse;'>"
            f"{body}"
            f"<tr><td style='padding:10px 14px 0 0;border-top:2px solid "
            f"{theme.BORDER_STRONG};font-weight:800;color:{theme.INK};"
            f"font-size:1.05rem;'>Banked</td>"
            f"<td style='padding:10px 0 0;border-top:2px solid "
            f"{theme.BORDER_STRONG};text-align:right;font-weight:800;"
            f"color:{theme.GREEN};font-size:1.15rem;'>"
            f"{_d(report['banked'])}</td></tr></table>",
            unsafe_allow_html=True)
        theme.note(
            "These two lines do not always subtract exactly to the third, and "
            "that is correct rather than a rounding bug. Premium sold counts "
            "what you sold **this month**; banked counts what settled **this "
            "month**. A trade opened in June and closed in July has its premium "
            "in June and its result in July.")
        if rolls:
            theme.note(
                f"**\\${rolls:,.0f}** of the banked total came from rolling "
                "short calls. That credit is yours the day you roll, even on a "
                "trade that is still open.")

    st.divider()
    render_weeks(report, weekly_goal)
    st.divider()
    render_strategy(report)
    st.divider()
    render_underlyings(report)
    st.divider()
    render_management(report)
