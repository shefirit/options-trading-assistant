"""The top of the My trades tab: where she stands, in one screen.

Three rows, in the order a trading dashboard is read:

  1. HEALTH CHECK  - six cards. If those six cannot tell the story on their own,
     no chart underneath is going to fix it.
  2. GOALS AGAINST REALITY - the bullet chart, the year-one track, and the
     cumulative curve against the ramp a steady plan would draw.
  3. PROCESS QUALITY - did she trade the way her rules say, is she opening a
     sensible number of trades, and how much buying power that used.

Above all three sits the band: banked this month, this week, and whether
anything needs a decision today.

WHY THERE IS EXACTLY ONE GOAL VISUAL
------------------------------------
This page used to draw three progress bars of the same number - one in the
headline strip, one in the report's goals panel, one in the pace note. Three
answers to one question reads as an app that has not decided what it thinks.
Now there is one, the bullet chart, and it says more than all three did: not
"35% of the way there" but "35%, and a steady plan would be at 16% today".

NOTHING IN THESE THREE ROWS IS BEHIND A CLICK
---------------------------------------------
Progressive disclosure belongs on detail, not on decisions. Her rule: a power
dashboard, not a gated wizard.

Dollar signs: &#36; inside HTML, \\$ inside theme.note(). A raw pair turns
Streamlit's markdown into LaTeX and garbles the line.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from typing import Any, Optional

import streamlit as st

from ui import income_report, theme
from ui.trades import charts


def _d(x: float, decimals: int = 0) -> str:
    """Dollars with an HTML-entity sign, for HTML this module writes ITSELF.

    Do not hand these to theme.kpi_card or theme.track - those escape their
    input, so an entity arriving there comes out as a literal "&#36;0" on the
    page. Use _m() for anything going through a theme helper.
    """
    return f"&#36;{x:,.{decimals}f}"


def _m(x: float, decimals: int = 0) -> str:
    """Plain dollars, for values passed to a theme helper that escapes."""
    return f"${x:,.{decimals}f}"


def _signed(x: float) -> str:
    """The minus in front of the sign - "-$640", not "$-640"."""
    return f"-{_m(abs(x))}" if x < 0 else _m(x)


def _pct(x: Optional[float], nd: int = 0) -> str:
    return f"{x * 100:.{nd}f}%" if x is not None else "-"


def _esc(text: Any) -> str:
    return _html.escape(str(text), quote=True)


# ------------------------------------------------------------------ the band
def band(report: dict, pace: Optional[dict], goal: float, weekly_goal: float,
         week_banked: float, needs: int, open_count: int,
         priced_at: Optional[str], mode: str) -> None:
    """Banked this month, banked this week, and whether anything needs doing.

    The two most urgent facts on the tab used to live in two separate objects -
    a headline strip at the top and a red alert further down, with the open
    trades between them. They belong together: "you have banked $1,240 and two
    trades need a decision today" is one thought.
    """
    banked = report["banked"]
    pct = (banked / goal) if goal else 0.0
    real = mode == "real"
    tag = "REAL MONEY" if real else "PRACTICE (PaperMoney)"
    tag_bg = theme.GREEN if real else theme.AMBER

    if pace and pace["still_needed"] > 0:
        month_sub = (f"{_pct(pct)} of your {_d(goal)} goal &middot; "
                     f"{_d(pace['still_needed'])} to go, "
                     f"{pace['days_left']} days left")
    elif pace:
        month_sub = f"Goal met &middot; {_d(banked - goal)} past it"
    else:
        month_sub = f"{_pct(pct)} of your {_d(goal)} goal"

    if needs:
        word = "trade needs" if needs == 1 else "trades need"
        todo_label, todo_value = "NEEDS YOU TODAY", f"{needs} {word} a decision"
        todo_colour, todo_sub = "#FFC9C0", "Do it in thinkorswim, then record it below"
    elif open_count:
        todo_label, todo_value = "NOTHING TO DO TODAY", f"{open_count} open"
        todo_colour = theme.BAND_HERO
        todo_sub = "every open trade is inside your rules"
    else:
        todo_label, todo_value = "NOTHING OPEN", "0 open"
        todo_colour, todo_sub = "#FFFFFF", "nothing at risk right now"
    if priced_at:
        todo_sub += f" &middot; priced {_esc(priced_at)}"

    week_sub = f"of your {_d(weekly_goal)} weekly goal" if weekly_goal else "banked"

    st.markdown(
        f'<div class="ota-band">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:baseline;flex-wrap:wrap;gap:10px;">'
        f'<div class="ota-band-title">{_esc(report["label"])}</div>'
        f'<span style="background:{tag_bg};color:#FFFFFF;font-size:.78rem;'
        f'font-weight:800;letter-spacing:.06em;padding:5px 12px;'
        f'border-radius:999px;">{tag}</span></div>'
        f'<div class="ota-band-zones">'
        f'<div class="ota-band-zone" style="min-width:230px;">'
        f'<div class="ota-band-label">Banked this month</div>'
        f'<div class="ota-band-hero">{_d(banked)}</div>'
        f'<div class="ota-band-sub">{month_sub}</div></div>'
        f'<div class="ota-band-zone">'
        f'<div class="ota-band-label">This week</div>'
        f'<div class="ota-band-value">{_d(week_banked)}</div>'
        f'<div class="ota-band-sub">{week_sub}</div></div>'
        f'<div class="ota-band-zone" style="min-width:250px;">'
        f'<div class="ota-band-label">{todo_label}</div>'
        f'<div class="ota-band-value" style="color:{todo_colour};">{todo_value}</div>'
        f'<div class="ota-band-sub">{todo_sub}</div></div>'
        f'</div></div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------- row 1: KPIs
def health_row(report: dict, quality: dict, pace: Optional[dict],
               goal: float) -> None:
    """The six numbers worth reading every time she opens the tab.

    Five of them are the measures that actually predict whether a system keeps
    working - profit factor, average trade, drawdown, win rate, rule adherence -
    rather than five different ways of saying "profit". The sixth is the month
    against her goal, because that is the question she opens the tab with.

    Two of them refuse to make a number up. Profit factor is blank until
    something has lost, because a first month of pure winners divided by zero
    losses is infinity and not a fact. And under five closed trades the whole
    row says so underneath, rather than printing 3.4 in bold off two trades.
    """
    banked = report["banked"]
    pct = (banked / goal) if goal else 0.0
    if pace is None:
        month_sub = f"of your {_m(goal)} goal"
        month_tone = "good" if goal and banked >= goal else "neutral"
    elif pace["still_needed"] <= 0:
        month_sub = f"goal met, {_m(banked - goal)} past it"
        month_tone = "good"
    elif pace["on_track"]:
        month_sub = f"{_m(pace['ahead_by'])} ahead of a steady pace"
        month_tone = "good"
    else:
        month_sub = f"{_m(abs(pace['ahead_by']))} behind a steady pace"
        month_tone = "behind"

    win = report["win_rate"]
    pf = quality["profit_factor"]
    exp = quality["expectancy"]
    dd = quality["max_drawdown"]
    followed, total = report["rules_followed"], report["rules_total"]
    rules_tone = ("neutral" if not total else "good" if followed == total
                  else "watch" if followed >= total / 2 else "bad")

    cards = [
        theme.kpi_card("This month", _m(banked), month_sub, month_tone, "💰"),
        theme.kpi_card(
            "Win rate", _pct(win) if win is not None else "-",
            f"{report['wins']} won, {report['losses']} lost" if win is not None
            else "nothing closed yet", "neutral", "🏅"),
        theme.kpi_card(
            "Profit factor", f"{pf:.2f}" if pf is not None else "-",
            (f"you have earned {_m(pf, 2)} for every $1.00 you lost"
             if pf is not None else "nothing has lost yet - no ratio to show"),
            "good" if pf is not None and pf >= 1.5 else
            "watch" if pf is not None and pf >= 1 else
            "bad" if pf is not None else "neutral", "⚖️"),
        theme.kpi_card(
            "Average trade", _signed(exp) if exp is not None else "-",
            "what one closed trade has been worth, wins and losses together",
            "good" if exp is not None and exp > 0 else
            "bad" if exp is not None else "neutral", "📊"),
        # Neutral on purpose. A drawdown is information, not a verdict - every
        # working system has them, and colouring this red would teach her to
        # fear a normal part of the job.
        theme.kpi_card(
            "Worst dip", _signed(dd) if dd else _m(0),
            ("the most you were ever down from your best day"
             if dd else "you have never been below your best day"),
            "neutral", "📉"),
        theme.kpi_card(
            "By the rules", f"{followed} of {total}" if total else "-",
            ("closes that used one of your four SOP exits" if total
             else "no closes this month yet"), rules_tone, "🎯"),
    ]
    theme.kpi_row(cards)

    if quality["confidence"] == "thin":
        n = quality["closed_count"]
        theme.note(
            f"**These fill in as you close more trades.** With {n} close"
            f"{'' if n == 1 else 's'} behind you, the win rate and the profit "
            "factor are still mostly noise - a run of three good ones would "
            "swing them either way. They start meaning something around "
            "twenty closes. The one worth watching from trade one is **By the "
            "rules**, because that is the only number you control directly.")


# --------------------------------------------------- row 2: goals vs reality
def goals_block(positions: list, settings: dict, live_from, mode: str,
                today=None) -> None:
    """Her plan, and where she actually is against it - total and by month.

    Three horizons on one bullet chart, because a good week inside a bad month
    and a good month inside a year that is behind are both worth seeing, and
    three separate progress bars tell her none of that.
    """
    from src.engine import goals

    today = today or _dt.date.today()
    t = goals.targets_from(settings)

    theme.section("Where you actually are against the plan", "Goals vs reality")

    rows = goals.bullet_rows(positions, settings, live_from, today, mode)
    income_report._render(charts.goal_bullet(rows), height=170,
                          labels=[r["label"] for r in rows])
    theme.note(
        "Each bar is how far along that goal you are. The **black line** is the "
        "goal itself, and the **amber line** is where a steady plan would be "
        "**today** - so a bar past the amber line is ahead of pace even when it "
        "is nowhere near the black one. The pale green bands behind are just "
        "halfway and nearly-there markers.")

    # ---- the year-one balance
    st.write("")
    if mode == "real" and t["year_one"] > 0:
        y = goals.year_one(positions, settings, live_from, today)
        theme.track(
            y["balance"], y["capital"], y["goal"],
            marker=y["pace_balance"], marker_label="a steady plan is here today",
            value_label=f"${y['balance']:,.0f}",
            start_label=f"${y['capital']:,.0f} start",
            goal_label=f"${y['goal']:,.0f} year one")
        pace_word = ("**ahead of** a steady plan" if y["on_pace"]
                     else "**behind** a steady plan")
        theme.note(
            f"Year one ends at **\\${y['goal']:,.0f}**. That is your "
            f"**\\${y['capital']:,.0f}** plus **\\${y['to_earn']:,.0f}** of income, "
            f"which is the **\\${t['monthly']:,.0f}** monthly goal twelve times "
            f"over. You have banked **\\${y['banked']:,.0f}** so far, which is "
            f"\\${abs(y['ahead_by']):,.0f} {pace_word}. With {y['months_left']} "
            f"month(s) left that works out at **\\${y['monthly_needed']:,.0f} a "
            f"month** from here.")
    elif mode != "real":
        # theme.chip does not escape, so the dollar sign has to arrive as an
        # entity or Streamlit reads it as the start of a LaTeX span.
        st.markdown(theme.chip(
            f"📝 The {_d(t['year_one'])} year-one track is about your real "
            "account, so it is hidden on the practice book", "amber"),
            unsafe_allow_html=True)

    # ---- the running total against the ramp
    series = goals.cumulative_series(positions, settings, live_from, today)
    if series:
        st.write("")
        theme.section("Every dollar you have banked, against the plan", "Total")
        income_report._render(charts.cumulative_vs_target(series, mode), height=260)
        theme.note(
            "The solid green line is your money, running total. The **dashed "
            "amber line** is what a steady "
            f"**\\${t['monthly']:,.0f} a month** would have produced by each "
            "date. Where green is above amber you are ahead of the plan.")
        if any(r["book"] != mode for r in series):
            other = "practice" if mode == "real" else "real-money"
            theme.legend_note(
                f"The faded grey line is your {other} book. It is never added "
                "into any total on this page - it is here so you can see how "
                "far this one has come.")

    # ---- the plan itself, once
    st.write("")
    _plan_strip(t)


def _plan_strip(t: dict) -> None:
    """The four numbers everything above is measured against.

    Rendered once, here. They used to be printed inside every month's report,
    so scrolling through three months meant reading her own goals three times.
    """
    tiles = [
        income_report._tile("CAPITAL", _d(t["capital"]),
                            "what the account holds", theme.INK, "🏦"),
        income_report._tile("MONTHLY GOAL", _d(t["monthly"]),
                            "the number this page is scored against",
                            theme.INK, "🎯"),
        income_report._tile("WEEKLY GOAL", _d(t["weekly"]),
                            "the pace that gets you there", theme.INK, "📅"),
        income_report._tile("BUYING-POWER BUDGET", _d(t["bp_limit"]),
                            "the most you put to work in a month",
                            theme.INK, "🧮"),
    ]
    st.markdown(f"<div style='display:flex;gap:12px;flex-wrap:wrap;'>"
                f"{''.join(tiles)}</div>", unsafe_allow_html=True)
    theme.note("Change any of these four in **⚙️ Settings → Your goals and "
               "budget**. They drive this whole page, so it follows the moment "
               "you save.")


# ------------------------------------------------ row 3: how well she traded
def process_row(report: dict, quality: dict, bp_used: float,
                bp_limit: float, median_bp: float) -> None:
    """The half of a dashboard that is about the trading rather than the money.

    While she is learning this matters more than the P&L: a disciplined month
    with a small profit is a better month than a lucky one with a big profit,
    because only the first one repeats.
    """
    theme.section("How well you traded, not how much you made", "Process")
    left, middle, right = st.columns(3)

    with left:
        followed, total = report["rules_followed"], report["rules_total"]
        st.markdown("**Did you follow your own exit rules?**")
        if not total:
            theme.note("Nothing closed this month yet, so there is nothing to "
                       "score. This fills in from your first close.")
        else:
            share = followed / total
            tone = "green" if share == 1 else "amber" if share >= 0.5 else "red"
            st.markdown(theme.chip(f"{followed} of {total} closes by the rules",
                                   tone), unsafe_allow_html=True)
            for row in report["exits"]:
                theme.note(f"• **{row['count']}** {row['label'].lower()}"
                           + ("" if row["by_rules"] else "  (outside your four exits)"))

    with middle:
        st.markdown("**Are you opening a sensible number?**")
        opened = report["trades_opened"]
        if median_bp > 0 and bp_limit > 0:
            fits = max(int(bp_limit // median_bp), 1)
            tone = "green" if opened <= fits else "amber"
            st.markdown(theme.chip(f"{opened} opened this month", tone),
                        unsafe_allow_html=True)
            theme.note(
                f"At your usual size (about **\\${median_bp:,.0f}** of buying "
                f"power a trade) your **\\${bp_limit:,.0f}** budget fits about "
                f"**{fits}** trades in a month. This is not an SOP rule - it is "
                "just what your own budget and your own position sizes work out "
                "to.")
        else:
            st.markdown(theme.chip(f"{opened} opened this month", "green"),
                        unsafe_allow_html=True)
            theme.note("Once you have a few trades logged this will also say "
                       "roughly how many your buying-power budget fits.")

    with right:
        st.markdown("**How much of your budget is committed?**")
        used = (bp_used / bp_limit) if bp_limit else 0.0
        tone = "red" if used > 1 else "amber" if used > 0.8 else "green"
        st.markdown(theme.chip(
            f"{_d(bp_used)} of {_d(bp_limit)} committed ({used * 100:.0f}%)",
            tone), unsafe_allow_html=True)
        theme.note(
            "Buying power is the cash your broker sets aside while a trade is "
            "open. This counts **every trade you opened this month**, closed "
            "ones included - closing early frees the risk, not the month's "
            f"budget. **\\${max(bp_limit - bp_used, 0):,.0f}** left.")
