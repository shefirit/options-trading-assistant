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


def _signed(x: float) -> str:
    """Dollars with the minus in front of the sign, not after it - "-$4,826"
    rather than "$-4,826", which reads as a typo."""
    return f"-{_d(abs(x))}" if x < 0 else _d(x)


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _pct(x: Optional[float], nd: int = 0) -> str:
    return f"{x * 100:.{nd}f}%" if x is not None else "-"


# Roughly how wide one character of a 13px label is, and the slack on top.
# Used to reserve real space for the y-axis labels rather than trusting Vega's
# own estimate, which came up about a character short: "13/7 - 19/7" rendered
# as "3/7 - 19/7" while the shorter labels beside it were fine.
_CHAR_PX = 7.6
_LABEL_SLACK_PX = 18


def _label_pad(labels: list[str]) -> int:
    """Room to reserve on the left for the longest axis label."""
    longest = max((len(str(x)) for x in labels), default=0)
    return int(longest * _CHAR_PX + _LABEL_SLACK_PX)


def _render(chart, height: int, labels: Optional[list[str]] = None) -> None:
    """Draw a chart so nothing at its edges gets cut off.

    Vega sizes the plotting area first and lets axis labels and value text hang
    outside it. autosize fit-x asks it to make them fit, but on a LAYERED chart
    (bars plus a goal line) Vega-Lite does not honour fit against a container
    width, so the longest label still lost its first character. Reserving the
    space explicitly from the labels themselves is what actually holds.
    """
    st.altair_chart(
        chart.properties(
            height=height,
            padding={"left": _label_pad(labels or []), "right": 46,
                     "top": 6, "bottom": 6},
            # Set through properties, not configure_autosize: a layered chart
            # has no configure_autosize at all.
            autosize=alt.AutoSizeParams(type="fit-x", contains="padding"),
        ).configure_view(strokeWidth=0),
        width="stretch")


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
def _tile(label: str, value: str, sub: str, tone: str = theme.INK,
          icon: str = "") -> str:
    head = (f"<span style='font-size:1.05rem;margin-right:6px;'>{icon}</span>"
            if icon else "")
    return (
        f"<div style='flex:1 1 165px;min-width:165px;background:{theme.CARD};"
        f"border:1px solid {theme.BORDER_STRONG};border-radius:14px;"
        f"padding:14px 16px;'>"
        f"<div style='font-size:.76rem;font-weight:800;color:{theme.SECONDARY};"
        f"letter-spacing:.05em;'>{head}{label}</div>"
        f"<div style='font-size:1.7rem;font-weight:800;color:{tone};"
        f"line-height:1.2;margin:2px 0;'>{value}</div>"
        f"<div style='font-size:.85rem;font-weight:600;color:{theme.SECONDARY};"
        f"line-height:1.45;'>{sub}</div></div>")


def render_goals(report: dict, settings: dict) -> None:
    """Her plan on the page: the goal, the budget, and where this month sits
    against both. The report is meaningless without the numbers it is being
    measured against, and those numbers used to live only in a config file."""
    goal = float(settings["targets"]["monthly"])
    weekly_goal = float(settings["targets"]["weekly"])
    capital = float(settings["account"]["starting_capital"])
    bp_limit = float(settings["risk_limits"]["monthly_bp_limit"])

    banked, bp = report["banked"], report["bp_opened"]
    to_go = max(goal - banked, 0.0)
    bp_share = (bp / bp_limit) if bp_limit else 0.0
    bp_tone = (theme.RED if bp_share >= 1 else theme.AMBER
               if bp_share >= 0.8 else theme.GREEN)
    goal_tone = theme.GREEN if banked >= goal else theme.INK

    tiles = [
        _tile("CAPITAL", _d(capital), "what the account holds",
              theme.INK, "🏦"),
        _tile("MONTHLY GOAL", _d(goal),
              (f"met - {_d(banked - goal)} past it" if banked >= goal
               else f"{_d(to_go)} still to go"), goal_tone, "🎯"),
        _tile("WEEKLY GOAL", _d(weekly_goal),
              "the pace that gets you there", theme.INK, "📅"),
        _tile("BUYING-POWER BUDGET", _d(bp_limit),
              f"{_d(bp)} committed &middot; {_pct(bp_share)} used",
              bp_tone, "🧮"),
    ]
    st.markdown(
        f"<div style='display:flex;gap:12px;flex-wrap:wrap;'>{''.join(tiles)}</div>",
        unsafe_allow_html=True)
    st.write("")
    st.progress(min(max(banked / goal, 0.0), 1.0) if goal else 0.0)
    theme.note("Change any of these four in **⚙️ Settings → Your goals and budget**. "
               "They drive this whole page, so the report follows the moment you "
               "save.")


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
              "and banked", theme.INK, "💸"),
        _tile("PREMIUM CAPTURED", _pct(cap) if cap is not None else "-",
              (f"of the credit kept on {report['capture_trades']} credit "
               f"trades" if cap is not None else "no credit trades closed yet"),
              cap_tone, "🎯"),
        _tile("WIN RATE", _pct(win) if win is not None else "-",
              f"{report['wins']} won &middot; {report['losses']} lost",
              theme.INK, "🏅"),
        _tile("PER CLOSED TRADE", _d(per_close) if per_close is not None else "-",
              "average result, wins and losses together", theme.INK, "📊"),
        _tile("ACTIVE DAYS", str(report["active_days"]),
              (f"{_d(avg_day)} a day you traded"
               if avg_day is not None else "days anything happened"),
              theme.INK, "📆"),
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
            bp_tone, "🧮"))
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


# ------------------------------------------------------- chart builders
# Separated from the rendering so the shape of each chart can be asserted in a
# test. These two were rebuilt after they came out unreadable: fixed bar sizes
# made five weeks render as one solid block, and with nothing reserved for the
# labels the y-axis text lost its first characters and the value at the end of
# the longest bar ran off the right edge.
def weeks_chart(weeks: list[dict], weekly_goal: float):
    """Money banked per week, with the weekly target as a dashed line."""
    df = pd.DataFrame([{"Week": w["label"], "Banked": w["banked"],
                        "Premium sold": w["premium"], "order": str(w["start"])}
                       for w in weeks])
    order = list(df.sort_values("order")["Week"])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("Week:N", sort=order, title=None,
                # Band padding, NOT a fixed bar size: with size= the bars kept
                # their height while the rows scaled with the number of weeks,
                # so five weeks rendered as one solid block of colour.
                scale=alt.Scale(paddingInner=0.35, paddingOuter=0.2),
                axis=alt.Axis(labelLimit=200, labelPadding=8,
                              labelFontSize=13, labelColor=theme.INK)),
        # A tick every $100 turns the axis into a wall of numbers on a month
        # that banked a few thousand. Six labels is enough to read a bar.
        x=alt.X("Banked:Q", title="Banked ($)",
                axis=alt.Axis(tickCount=6, format="$,.0f")),
        color=alt.condition("datum.Banked >= 0",
                            alt.value(theme.GREEN), alt.value(theme.RED)),
        tooltip=[alt.Tooltip("Week:N"),
                 alt.Tooltip("Banked:Q", format="$,.0f"),
                 alt.Tooltip("Premium sold:Q", format="$,.0f")])
    if not weekly_goal:
        return bars
    rule = alt.Chart(pd.DataFrame({"goal": [weekly_goal]})).mark_rule(
        color=theme.AMBER, strokeDash=[6, 4], strokeWidth=2).encode(x="goal:Q")
    return bars + rule


def producers_chart(rows: list[dict]):
    """Premium sold per underlying, with its value printed at the bar's end."""
    df = pd.DataFrame([{"Name": r["name"], "Premium": r["premium"],
                        "Banked": r["banked"]} for r in rows])
    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y("Name:N", sort=list(df["Name"]), title=None,
                scale=alt.Scale(paddingInner=0.3, paddingOuter=0.2),
                axis=alt.Axis(labelLimit=120, labelPadding=8,
                              labelFontSize=13, labelColor=theme.INK)),
        # Headroom on the right so the value label printed at the end of the
        # longest bar has somewhere to sit instead of being clipped.
        x=alt.X("Premium:Q", title="Premium sold ($)",
                scale=alt.Scale(domainMin=0, nice=True,
                                domainMax=float(df["Premium"].max()) * 1.16),
                axis=alt.Axis(tickCount=6, format="$,.0f")),
        color=alt.value(SLOTS[0]),
        tooltip=[alt.Tooltip("Name:N", title="Underlying"),
                 alt.Tooltip("Premium:Q", format="$,.0f"),
                 alt.Tooltip("Banked:Q", format="$,.0f")])
    labels = bars.mark_text(align="left", dx=7, fontSize=13,
                            fontWeight="bold", color=theme.INK).encode(
        text=alt.Text("Premium:Q", format="$,.0f"))
    return bars + labels


# ------------------------------------------------------------------- by week
def render_weeks(report: dict, weekly_goal: float) -> None:
    """Money banked per week against the $808 target - the picture that shows
    whether a month was steady or one lucky Tuesday."""
    weeks = report["weeks"]
    if not weeks:
        return
    theme.section("Was it steady, or was it one good week?", "By week")

    _render(weeks_chart(weeks, weekly_goal), height=max(160, 52 * len(weeks)),
            labels=[w["label"] for w in weeks])

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
def strategy_donut(rows: list[dict], total: float):
    """A readable ring: thick enough to see, its share printed on every slice
    big enough to hold it, and the total sitting in the middle.

    The first version was a hairline circle with a grey legend underneath and
    not one number on it - it showed that four things existed and nothing about
    their sizes, which is the only reason to draw a pie at all.
    """
    df = pd.DataFrame([{"Name": r["name"], "Premium": r["premium"],
                        "Share": r["share"]} for r in rows if r["premium"] > 0])
    names = list(df["Name"])
    base = alt.Chart(df).encode(
        theta=alt.Theta("Premium:Q", stack=True),
        color=alt.Color("Name:N", sort=names,
                        scale=alt.Scale(domain=names, range=SLOTS[:len(names)]),
                        # No Vega legend: it rendered in a grey well under her
                        # contrast floor and could not carry the dollar amounts.
                        # The legend beside the chart is built in HTML instead.
                        legend=None),
        tooltip=[alt.Tooltip("Name:N", title="Strategy"),
                 alt.Tooltip("Premium:Q", format="$,.0f"),
                 alt.Tooltip("Share:Q", format=".0%")])
    arcs = base.mark_arc(innerRadius=64, outerRadius=108,
                         stroke="#FFFFFF", strokeWidth=2)
    # Only on slices with room for it - a 3% sliver cannot hold "3%" without
    # colliding with its neighbours.
    shares = base.mark_text(radius=130, fontSize=13, fontWeight="bold",
                            fill=theme.INK).encode(
        text=alt.condition(alt.datum.Share >= 0.08,
                           alt.Text("Share:Q", format=".0%"), alt.value("")))
    middle = pd.DataFrame({"v": [f"${total:,.0f}"], "c": ["premium sold"]})
    total_text = alt.Chart(middle).mark_text(
        fontSize=21, fontWeight="bold", fill=theme.INK, dy=-8).encode(text="v:N")
    caption = alt.Chart(middle).mark_text(
        fontSize=12, fontWeight="bold", fill=theme.SECONDARY, dy=14).encode(
        text="c:N")
    return arcs + shares + total_text + caption


def _legend_rows(rows: list[dict], name_col: str) -> None:
    """The breakdown as readable rows: a colour chip that ties each line to its
    slice, the name at full contrast, what it sold, and what it banked.

    This replaces a st.dataframe that sat beside the chart. The table was
    sortable but it could not show which colour was which, so reading the ring
    meant hovering it slice by slice.
    """
    out = []
    for i, r in enumerate(rows):
        banked = r["banked"]
        banked_tone = theme.GREEN if banked >= 0 else theme.RED
        # A strategy can bank money this month having sold nothing in it - a
        # trade opened in June and closed in July does exactly that. It has no
        # slice in the ring, so it gets a hollow chip and says why, instead of
        # reading as "$0, 0%, 0 sale(s)".
        sold_earlier = r["premium"] <= 0 and banked != 0
        chip = (f"border:2px solid {theme.BORDER_STRONG};background:transparent;"
                if sold_earlier else f"background:{SLOTS[i % len(SLOTS)]};")
        n = r["trades"]
        detail = ("sold in an earlier month" if sold_earlier
                  else f"{n} sale" if n == 1 else f"{n} sales")
        right = ("&mdash;" if sold_earlier else _d(r["premium"]))
        share = "" if sold_earlier else _pct(r["share"])
        out.append(
            f"<div style='display:flex;gap:10px;align-items:flex-start;"
            f"padding:9px 0;border-bottom:1px solid {theme.BORDER};'>"
            f"<span style='flex:0 0 12px;width:12px;height:12px;margin-top:5px;"
            f"border-radius:3px;{chip}'></span>"
            f"<div style='flex:1 1 auto;min-width:0;'>"
            f"<div style='font-size:1rem;font-weight:800;color:{theme.INK};"
            f"line-height:1.35;'>{_esc(r['name'])}</div>"
            f"<div style='font-size:.9rem;font-weight:600;"
            f"color:{theme.SECONDARY};'>"
            f"banked <b style='color:{banked_tone};'>{_signed(banked)}</b>"
            f" &middot; {detail}</div></div>"
            f"<div style='flex:0 0 auto;text-align:right;'>"
            f"<div style='font-size:1.05rem;font-weight:800;color:{theme.INK};'>"
            f"{right}</div>"
            f"<div style='font-size:.85rem;font-weight:700;"
            f"color:{theme.SECONDARY};'>{share}</div></div></div>")
    st.markdown(
        f"<div style='font-size:.76rem;font-weight:800;color:{theme.SECONDARY};"
        f"letter-spacing:.05em;margin-bottom:2px;'>{_esc(name_col.upper())} "
        f"&middot; PREMIUM SOLD</div>{''.join(out)}",
        unsafe_allow_html=True)


def render_money_math(report: dict) -> None:
    """Premium sold, minus what closing it cost, equals what was banked - laid
    out as a small statement rather than hidden behind a link.

    The gap between the big premium number and the money in the account is
    where an income report can fool the person reading it, so the subtraction
    is done in the open, on the page.
    """
    lines = [
        ("Premium sold this month", report["premium_sold"], theme.GREEN),
        ("Less what you paid to close positions", -report["cost_to_close"],
         theme.RED),
    ]
    body = "".join(
        f"<tr><td style='padding:7px 14px 7px 0;color:{theme.INK};"
        f"font-size:1rem;'>{_esc(label)}</td>"
        f"<td style='padding:7px 0;text-align:right;font-weight:800;"
        f"color:{color};font-size:1.05rem;'>{_signed(value)}</td></tr>"
        for label, value, color in lines)
    st.markdown(
        f"<div style='background:{theme.TILE};border:1px solid "
        f"{theme.BORDER_STRONG};border-radius:14px;padding:16px 18px;'>"
        f"<div style='font-size:.78rem;font-weight:800;color:{theme.SECONDARY};"
        f"letter-spacing:.05em;margin-bottom:6px;'>HOW PREMIUM SOLD BECAME "
        f"MONEY BANKED</div>"
        f"<table style='width:100%;border-collapse:collapse;'>{body}"
        f"<tr><td style='padding:10px 14px 0 0;border-top:2px solid "
        f"{theme.BORDER_STRONG};font-weight:800;color:{theme.INK};"
        f"font-size:1.05rem;'>Banked</td>"
        f"<td style='padding:10px 0 0;border-top:2px solid "
        f"{theme.BORDER_STRONG};text-align:right;font-weight:800;"
        f"color:{theme.GREEN};font-size:1.3rem;'>"
        f"{_d(report['banked'])}</td></tr></table></div>",
        unsafe_allow_html=True)
    theme.note(
        "These two lines do not always subtract exactly to the third, and that "
        "is correct rather than a rounding bug. Premium sold counts what you "
        "sold **this month**; banked counts what settled **this month**. A trade "
        "opened in June and closed in July has its premium in June and its "
        "result in July.")
    if report["roll_income"]:
        theme.note(
            f"**\\${report['roll_income']:,.0f}** of the banked total came from "
            "rolling short calls. That credit is yours the day you roll, even "
            "on a trade that is still open.")


def render_strategy(report: dict) -> None:
    rows = report["by_strategy"]
    if not rows:
        return
    theme.section("Which of your eight strategies paid you?", "By strategy")
    left, right = st.columns([1, 1])
    with left:
        if any(r["premium"] > 0 for r in rows):
            st.altair_chart(
                strategy_donut(rows, report["premium_sold"])
                .properties(height=320)
                .configure_view(strokeWidth=0),
                width="stretch")
    with right:
        _legend_rows(rows, "Strategy")
    st.write("")
    render_money_math(report)
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
    _render(producers_chart(rows), height=max(140, 46 * len(rows)),
            labels=[r["name"] for r in rows])


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

    st.divider()
    theme.section("What you are aiming at", "Goals and budget")
    render_goals(report, settings)
    st.divider()
    render_weeks(report, weekly_goal)
    st.divider()
    render_strategy(report)
    st.divider()
    render_underlyings(report)
    st.divider()
    render_management(report)
    st.divider()
    render_at_a_glance(report, settings)


def render_at_a_glance(report: dict, settings: dict) -> None:
    """The month in one strip, at the end - the same few numbers the report
    opened with, for when she has scrolled all the way down and wants the
    summary again without going back up."""
    goal = float(settings["targets"]["monthly"])
    cap = report["capture_pct"]
    cells = [
        ("💰", _d(report["premium_sold"]), "PREMIUM SOLD"),
        ("🏦", _d(report["banked"]), "BANKED"),
        ("🎯", _pct((report["banked"] / goal) if goal else 0), "OF GOAL"),
        ("🤝", str(report["trades_opened"]), "TRADES OPENED"),
        ("✅", str(report["trades_closed"]), "TRADES CLOSED"),
        ("📆", str(report["active_days"]), "ACTIVE DAYS"),
        ("📈", _pct(cap) if cap is not None else "-", "PREMIUM KEPT"),
    ]
    body = "".join(
        f"<div style='flex:1 1 130px;min-width:130px;text-align:center;'>"
        f"<div style='font-size:1.3rem;'>{icon}</div>"
        f"<div style='font-size:1.35rem;font-weight:800;color:#FFFFFF;"
        f"line-height:1.2;'>{value}</div>"
        f"<div style='font-size:.72rem;font-weight:800;color:{BAND_SUB};"
        f"letter-spacing:.05em;'>{label}</div></div>"
        for icon, value, label in cells)
    st.markdown(
        f"<div style='background:{BAND};border-radius:16px;padding:18px 20px;'>"
        f"<div style='color:{BAND_SUB};font-size:.78rem;font-weight:800;"
        f"letter-spacing:.06em;margin-bottom:12px;'>"
        f"{_esc(report['label'].upper())} AT A GLANCE</div>"
        f"<div style='display:flex;gap:14px;flex-wrap:wrap;'>{body}</div></div>",
        unsafe_allow_html=True)
