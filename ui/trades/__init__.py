"""The My trades tab.

Sections in the order the questions come up: what needs doing today, how the
open trades are doing, whether she is on pace, and the bookkeeping.

Import direction is one way - nothing in this package imports app.py. Helpers
the other tabs share live in ui/components.py instead.
"""

from __future__ import annotations

import streamlit as st

from ui import theme
from ui.trades import dashboard
from ui.trades.account import _account_switch, live_from as account_live_from, mr_split
from ui.trades.data import ACTION_SIGNALS, _load_trade_log, _price_positions
from ui.trades.history import _results_section
from ui.trades.open_trades import _open_section
from ui.trades.quick_log import _quick_log_form
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

    # Recording a trade she has just placed is the most frequent thing she does
    # on this tab, and it used to sit five screens down inside Records, behind
    # the dashboard, the open trades, the goals and the history. Collapsed it
    # costs one line here and is always in reach.
    #
    # Above the band on purpose: the band is where she looks to see how the
    # month is going, which is a thing she READS. This is a thing she DOES, and
    # the doing should not be further away than the reading.
    _quick_log_form(settings, strategies, provider)

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
                   "**➕ Quick Log** at the top of this tab for one you already placed in "
                   "thinkorswim, or "
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

    # Prices first: the band says whether anything needs a decision today, and
    # that answer comes out of the same pricing pass the cards below use.
    items, priced_at = ([], None)
    if open_pos:
        items, priced_at = _price_positions(open_pos, provider, strategies)
    needs = sum(1 for it in items if it["signal"].action in ACTION_SIGNALS)

    import datetime as _dt

    from src.engine import goals
    from src.engine import month_report as mr

    today = _dt.date.today()
    live_from = account_live_from(settings)
    targets = goals.targets_from(settings)

    # all_pos is already one book; passing it back through the same split with
    # the same live_from is idempotent, so build() gets the right totals and
    # keeps its own guarantee that the two books never mix.
    this_month = mr.build(all_pos, month=mr.month_key(today),
                          live_from=live_from, today=today, mode=mode)
    pace = mr.pace(this_month, targets["monthly"], today)
    quality = pos_mod.quality(all_pos, today)
    perf = pos_mod.performance(all_pos, today)

    dashboard.band(this_month, pace, targets["monthly"], targets["weekly"],
                   perf["week_pl"], needs, len(items), priced_at, mode)
    dashboard.health_row(this_month, quality, pace, targets["monthly"])

    st.divider()
    _open_section(items, strategies, provider, priced_at)
    st.divider()
    # every_pos, not all_pos: the goals block draws the OTHER book faded behind
    # this one, so it needs both. It splits them itself and never adds them.
    dashboard.goals_block(every_pos, settings, live_from, mode, today)
    st.divider()
    dashboard.process_row(this_month, quality, bp_used, targets["bp_limit"],
                          _median_bp(all_pos))
    st.divider()
    _results_section(all_pos, settings, bp_used, mode, every_pos)
    st.divider()
    _records_section(settings, strategies, provider, closed, legacy, bp_used,
                     open_pos)


def _median_bp(positions) -> float:
    """Her usual position size, in buying power.

    Used to say roughly how many trades her monthly budget fits. The median
    rather than the mean because one PMCC with a $12,000 LEAPS in it would drag
    an average far away from what she actually does most weeks.
    """
    sizes = sorted(p.bp_effect for p in positions if p.bp_effect > 0)
    if not sizes:
        return 0.0
    mid = len(sizes) // 2
    return (sizes[mid] if len(sizes) % 2
            else (sizes[mid - 1] + sizes[mid]) / 2)
