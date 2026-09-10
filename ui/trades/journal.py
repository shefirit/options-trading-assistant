"""The Journal: every trade in one table, and the whole story of any one.

WHAT THIS REPLACES
------------------
The record of her trading was spread across four tables that overlapped and
disagreed about what a row was - the open table, the closed table, the
month table, and a legacy frame - plus nine stacked expanders holding the
forms. The full story of a trade, which is the thing that actually explains
what happened, was the FIRST of those nine, behind a click, behind a radio, and
behind a second dropdown to choose the trade.

One table now holds every trade she has ever logged. Pick a row and the whole
life of that trade opens underneath it: every fill in order, what each leg
contributed, and how it ended.

WHY IT IS STILL A TABLE
-----------------------
Rita's ruling, and the reason the open trades are a table too: "I want all
trades organised nicely in table, not one by one analysis." A card each means
scrolling past four trades to compare two, and comparing is what a table is
for. The story is the detail BELOW the table, not a replacement for it.

Dollar signs: &#36; inside HTML, \\$ inside theme.note().
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

import pandas as pd
import streamlit as st

from ui import components, theme

STATUS_WORD = {
    "open": "⏳ Open",
    "closed": "✅ Won",
    "lost": "❌ Lost",
    "even": "➖ Break even",
    "legacy": "📜 Before tracking",
}

ALL = "All"


def render(all_pos, settings, strategies, provider, mode: str) -> None:
    """The ledger, the filters above it, and the trade page below it."""
    theme.section("Every trade you have logged, and the story of any one",
                  "Journal")

    trades = [p for p in all_pos]
    if not trades:
        theme.note("**Nothing logged yet.** Use **➕ Quick Log** at the top of "
                   "this tab for a trade you already placed, or **Log this "
                   "trade** in 🎯 Find a trade.")
        return

    rows = _rows(trades)
    shown = _filters(rows)
    if not shown:
        theme.note("**No trade matches those filters.** Widen one of them "
                   "above - the trades are all still there.")
        return

    st.dataframe(
        _dataframe(shown), width="stretch", hide_index=True,
        column_config=_column_config(), key="journal_table",
        on_select="rerun", selection_mode="single-row-required")

    picked = _picked(shown)
    if picked is not None:
        st.divider()
        _trade_page(picked, settings, strategies, provider)


# ---------------------------------------------------------------- the rows
def _rows(trades: list) -> list[dict[str, Any]]:
    """One dict per trade, newest activity first.

    Sorted on the date something last HAPPENED - the close for a closed trade,
    the open for one still running - rather than on the open date for
    everything. A trade opened in June and closed last week is recent news, and
    sorting it under June buries it.
    """
    out = []
    for p in trades:
        pl = p.realized_total
        if p.status == "legacy":
            word = STATUS_WORD["legacy"]
        elif p.status == "open":
            word = STATUS_WORD["open"]
        elif pl is None:
            word = "✔️ Closed"
        elif pl > 0:
            word = STATUS_WORD["closed"]
        elif pl < 0:
            word = STATUS_WORD["lost"]
        else:
            word = STATUS_WORD["even"]

        out.append({
            "position": p,
            "result": word,
            "status": p.status,
            "symbol": p.underlying or "-",
            "strategy": components.short_strategy(p.strategy_name),
            "opened": p.opened,
            "closed": p.closed_on,
            "sort": p.closed_on or p.opened or _dt.date.min,
            "contracts": p.contracts,
            "credit": p.credit,
            "banked": p.banked_income,
            "result_amount": pl,
            "exit": p.exit_reason or "",
            "rolls": len(p.rolls),
        })
    return sorted(out, key=lambda r: r["sort"], reverse=True)


def _filters(rows: list[dict]) -> list[dict]:
    """Four filters on one line, each defaulting to everything.

    Defaulting to All matters: a filter that starts narrowed is a filter that
    silently hides trades she has not been told about, and the first question
    this page answers is "show me everything".
    """
    left, mid, right, far = st.columns([1.1, 1.2, 1.1, 1.2])

    with left:
        status = st.selectbox(
            "Show", [ALL, "Open", "Closed", "Won", "Lost"], key="journal_status")
    with mid:
        names = [ALL] + sorted({r["strategy"] for r in rows})
        strategy = st.selectbox("Strategy", names, key="journal_strategy")
    with right:
        syms = [ALL] + sorted({r["symbol"] for r in rows})
        symbol = st.selectbox("Symbol", syms, key="journal_symbol")
    with far:
        spans = [ALL, "This month", "Last 3 months", "This year"]
        span = st.selectbox("When", spans, key="journal_span")

    today = _dt.date.today()
    out = []
    for r in rows:
        if status == "Open" and r["status"] != "open":
            continue
        if status == "Closed" and r["status"] != "closed":
            continue
        if status == "Won" and not (r["result_amount"] or 0) > 0:
            continue
        if status == "Lost" and not (r["result_amount"] or 0) < 0:
            continue
        if strategy != ALL and r["strategy"] != strategy:
            continue
        if symbol != ALL and r["symbol"] != symbol:
            continue
        if span != ALL and not _in_span(r["sort"], span, today):
            continue
        out.append(r)
    return out


def _in_span(d, span: str, today) -> bool:
    if d is None or d == _dt.date.min:
        return False
    if span == "This month":
        return (d.year, d.month) == (today.year, today.month)
    if span == "Last 3 months":
        return d >= today - _dt.timedelta(days=92)
    if span == "This year":
        return d.year == today.year
    return True


def _dataframe(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame([{
        "Result": r["result"],
        "Symbol": r["symbol"],
        "Strategy": r["strategy"],
        "Opened": components.fmt_date(r["opened"]),
        "Closed": components.fmt_date(r["closed"]),
        "Qty": r["contracts"],
        "Credit $": r["credit"],
        "Banked $": r["banked"] or None,
        "Result $": r["result_amount"],
        "Why closed": r["exit"],
    } for r in rows])

    # An open trade has no result and often nothing banked yet, and a column
    # holding both floats and None comes out of pandas as dtype object - which
    # Streamlit renders as the literal word "None" in every empty cell, down a
    # column of real money. Coerced to a float column the blanks are NaN, and
    # NumberColumn draws NaN as an empty cell, which is what "not yet" looks
    # like.
    for col in ("Credit $", "Banked $", "Result $", "Qty"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _column_config() -> dict:
    return {
        "Result": st.column_config.TextColumn(
            "Result", help="How this trade ended, or that it is still running"),
        "Symbol": st.column_config.TextColumn("Symbol"),
        "Strategy": st.column_config.TextColumn("Strategy"),
        "Opened": st.column_config.TextColumn("Opened"),
        "Closed": st.column_config.TextColumn(
            "Closed", help="Blank while the trade is still open"),
        "Qty": st.column_config.NumberColumn(
            "Qty", format="%d", help="Contracts"),
        "Credit $": st.column_config.NumberColumn(
            "Credit", format="$%,.0f",
            help="What you collected when you opened it"),
        "Banked $": st.column_config.NumberColumn(
            "Banked so far", format="$%,.0f",
            help="Money already settled from rolls and legs sold, before the "
                 "trade ends"),
        "Result $": st.column_config.NumberColumn(
            "Result", format="$%,.0f",
            help="The whole life of the trade - the close plus every roll"),
        "Why closed": st.column_config.TextColumn(
            "Why closed", help="Which of your exit rules ended it"),
    }


def _picked(rows: list[dict]):
    """The trade the selected row is about.

    Follows the TRADE, not the row number: a rerun that reorders or filters the
    table would otherwise open a different trade than the one she clicked. The
    same rule the open-trades card follows.
    """
    state = st.session_state.get("journal_table")
    idx = 0
    if state is not None:
        chosen = getattr(state, "selection", None) or state.get("selection", {})
        picked_rows = (chosen.get("rows") if isinstance(chosen, dict)
                       else getattr(chosen, "rows", None)) or []
        if picked_rows:
            idx = picked_rows[0]
    if idx >= len(rows):
        idx = 0
    return rows[idx]["position"] if rows else None


# --------------------------------------------------------- one trade, whole
def _trade_page(position, settings, strategies, provider) -> None:
    """Everything about one trade, on one page instead of behind three clicks.

    Order is the order the questions come: what is this, how did it go, what
    each leg contributed, then every fill in sequence, then the corrections.
    """
    from src.engine import positions as pos_mod

    p = position
    theme.section(_headline(p), "This trade")
    _summary_stats(p)

    steps = pos_mod.story(p)
    if steps:
        components.render_story(p, steps)

    _corrections(p, settings, strategies, provider)


def _headline(p) -> str:
    bits = [p.underlying or "-", components.short_strategy(p.strategy_name)]
    if p.contracts:
        bits.append(f"{p.contracts} contract{'' if p.contracts == 1 else 's'}")
    return "  ·  ".join(bits)


def _summary_stats(p) -> None:
    """The five numbers that answer "how did this go" without scrolling."""
    # dte_left and days_held are METHODS on Position, not properties. Read as
    # attributes they are truthy bound methods, so the card printed
    # "<bound method Position.dte_left of Position(...)>" where the days should
    # have been.
    pl = p.realized_total
    held = p.days_held()
    left = p.dte_left()

    cards = [
        theme.stat("OPENED", components.fmt_date(p.opened),
                   f"expires {components.fmt_date(p.expiration)}"),
        theme.stat("CREDIT TAKEN", theme.money(p.credit),
                   "what you collected to open it"),
    ]
    if p.rolls:
        cards.append(theme.stat(
            "ROLLED", f"{len(p.rolls)}x", theme.signed(p.roll_income)
            + " collected on the rolls",
            "up" if p.roll_income >= 0 else "down"))
    if p.status == "closed":
        # The exit reason is the sub-line of the result rather than a card of
        # its own: a card with a long sentence as its VALUE renders that
        # sentence at 1.5rem and unbalances the whole strip.
        cards.append(theme.stat(
            "RESULT", theme.signed(pl) if pl is not None else "-",
            p.exit_reason or "closed", "up" if (pl or 0) >= 0 else "down"))
        cards.append(theme.stat(
            "CLOSED", components.fmt_date(p.closed_on),
            f"held {held} days" if held else "same day"))
    else:
        cards.append(theme.stat(
            "STILL OPEN", f"{left} days left" if left is not None else "-",
            "priced on the Now tab"))
        if p.banked_income:
            cards.append(theme.stat(
                "BANKED SO FAR", theme.signed(p.banked_income),
                "from rolls and legs already sold",
                "up" if p.banked_income >= 0 else "down"))
    theme.stat_row(cards)

    if p.note:
        theme.note(f"**Your note:** {p.note}")


def _corrections(p, settings, strategies, provider) -> None:
    """The forms, scoped to the trade she is looking at.

    They used to be a stack of top-level expanders, each re-asking which trade
    it was about. Here the trade is already chosen, so a correction is one
    click instead of three.

    Rendered as SIBLINGS, not inside a wrapper expander: each form brings its
    own keyed expander, and an expander two deep collapses on every rerun.
    """
    from ui.trades import records

    if not p.trade_id:
        return
    theme.note("Correcting something rewrites what the app thinks happened. It "
               "never touches thinkorswim - fix it there first, then make this "
               "page match.")
    records.render_corrections_for(p, settings, strategies, provider)
