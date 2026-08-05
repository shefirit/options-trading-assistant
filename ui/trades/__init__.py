"""The My trades tab.

Sections in the order the questions come up: what needs doing today, how the
open trades are doing, whether she is on pace, and the bookkeeping.

Import direction is one way - nothing in this package imports app.py. Helpers
the other tabs share live in ui/components.py instead.
"""

from __future__ import annotations

import streamlit as st

from ui import components, theme
from ui.trades.account import _account_switch, mr_split
from ui.trades.data import _load_trade_log, _price_positions
from ui.trades.history import _results_section
from ui.trades.open_trades import _open_section
from ui.trades.records import _records_section


def render(settings, strategies, provider) -> None:
    """Four sections, in the order the questions come up: what needs doing
    today, how the open trades are doing, whether she is on pace, and the
    bookkeeping. It used to open with the bookkeeping and carry two overlapping
    results blocks that printed the same four numbers twice.
    """
    from src.engine import positions as pos_mod

    theme.section("Every logged trade, tracked against your own exit rules", "My trades")

    top = st.columns([1, 6])
    if top[0].button("↻ Refresh", key="trades_refresh"):
        st.session_state.pop("trades_rows", None)
        st.session_state.pop("_priced_positions", None)

    flash = st.session_state.pop("ql_flash", None)
    if flash:
        st.success(flash)

    header, rows, source = _load_trade_log()
    every_pos = pos_mod.parse_rows(header, rows)

    # The two books are kept completely apart, and the switch below decides
    # which one this whole tab is about - the headline numbers, what needs doing
    # today, the open trades, the results and the records. Scoping only the
    # report would leave the biggest numbers on the page mixing practice money
    # with real, which is the one thing this must never do.
    mode = _account_switch(settings, every_pos)
    all_pos = mr_split(every_pos, settings)[mode]

    open_pos = pos_mod.open_positions(all_pos)
    closed = pos_mod.closed_positions(all_pos)
    legacy = [p for p in all_pos if p.status == "legacy"]
    bp_used = pos_mod.bp_committed_this_month(all_pos)
    # Only the real book's buying power constrains real trades, so this is what
    # the Find-a-trade checklist reads. Practice trades tie up nothing.
    st.session_state["month_bp_used"] = (
        pos_mod.bp_committed_this_month(mr_split(every_pos, settings)["real"]))

    if not all_pos:
        book = ("real-money book" if mode == "real" else "practice book")
        theme.note(f"Nothing in your **{book}** yet. Two ways to log a trade: "
                   "**Quick Log** below for one you already placed in thinkorswim, or "
                   "**Log this trade** in 🎯 Find a trade when the app finds the setup "
                   "for you. Both ask which account the trade is in. Either way it "
                   "lands here and the app starts watching your exit rules: take the "
                   "win at 50% of the credit, at 21 days to expiration close or roll "
                   "for a credit, stop the loss at 2x.")
        if mode == "real" and mr_split(every_pos, settings)["practice"]:
            theme.note("Your practice trades are still here - switch accounts above "
                       "to see them. They are kept completely apart from this book.")
        if source == "local" and not rows:
            from src.logging_tools import webhook_logger
            if webhook_logger.is_configured():
                st.info("Your Google Sheet link is saved, but the log could not be read "
                        "back. That usually means the sheet still runs the older script - "
                        "paste the updated **LogTrade.gs** (in the google_apps_script "
                        "folder) into Apps Script, then Deploy → Manage deployments "
                        "→ Edit → New version → Deploy.")
        st.divider()
        _records_section(settings, strategies, provider, closed, legacy, bp_used)
        return

    if source == "local":
        theme.note("Reading the **local backup log** on this device. To track trades "
                   "everywhere, connect your Google Sheet in the **⚙️ Settings** "
                   "tab (one-time, ~2 minutes).")

    # Her numbers first, always on screen. They used to live only inside the
    # Results block - below the open trades and behind a month picker - so "how
    # am I doing" took three scrolls to answer.
    import datetime as _dt

    perf = pos_mod.performance(all_pos)
    # Match on the actual month rather than taking the newest entry: a trade
    # mistyped with a future date would otherwise become "this month".
    key_now = f"{_dt.date.today():%Y-%m}"
    this_month = next((m for m in pos_mod.monthly_summary(all_pos)
                       if m["month"] == key_now), None)
    rules = (f"{this_month['rules_followed']} of {this_month['closed_count']}"
             if this_month and this_month["closed_count"] else "")
    components.render_headline_stats(perf, settings["targets"], rules)

    items, priced_at = ([], None)
    if open_pos:
        items, priced_at = _price_positions(open_pos, provider, strategies)

    st.divider()
    _open_section(items, strategies, provider, priced_at)
    st.divider()
    _results_section(all_pos, settings, bp_used, mode)
    st.divider()
    _records_section(settings, strategies, provider, closed, legacy, bp_used)
