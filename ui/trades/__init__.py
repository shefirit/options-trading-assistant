"""The My trades tab.

FOUR PAGES, NOT ONE SCROLL
--------------------------
This tab used to stack ten sections onto one page: the band, six KPI cards, the
open trades, the goal charts, the process row, an entire month income report,
then nine record expanders. That was about twenty screens of scrolling, and
finding anything meant remembering how far down it lived. Rita's verdict was
that it felt "not effective and not professional - hard to follow and
understand".

It is now four sub-tabs, each answering one question:

  📍 Now      what needs a decision today, and how are the open trades doing
  💰 Profit   what did I make - by day, by week, by month, or all of it
  🎯 Plan     am I on pace, and am I trading by my own rules
  📜 Journal  every trade in one table, and the whole story of any one

This is lateral navigation, not a wizard. Everything is one tap away and
nothing is gated behind a step, which is the rule this tab has always had: a
power dashboard, not a gated wizard.

WHAT LIVES IN THE SIDEBAR
-------------------------
The three things that are chrome rather than content: the account switch, which
governs every number on all four pages; Refresh; and the button that opens
Quick Log. They used to sit above the tabs, costing every page its top three
inches, which is space that should be showing numbers.

Nothing she READS goes in there, so the sidebar and the page never hold two
copies of the same thing.

Import direction is one way - nothing in this package imports app.py. Helpers
the other tabs share live in ui/components.py instead.
"""

from __future__ import annotations

import streamlit as st

from ui import theme
from ui.trades import dashboard, journal, pnl
from ui.trades.account import _account_switch, live_from as account_live_from, mr_split
from ui.trades.data import ACTION_SIGNALS, _load_trade_log, _price_positions
from ui.trades.history import _results_section
from ui.trades.open_trades import _open_section
from ui.trades.quick_log import _quick_log_form


def render(settings, strategies, provider) -> None:
    """The tab: one shared header, then four pages."""
    from src.engine import positions as pos_mod

    theme.section("Every logged trade, tracked against your own exit rules",
                  "My trades")

    flash = st.session_state.pop("ql_flash", None)
    if flash:
        st.success(flash)

    header, rows, source = _load_trade_log()
    every_pos = pos_mod.parse_rows(header, rows)

    # ---- the sidebar: the controls, off the page
    #
    # These three used to sit above the tabs, where they cost every page its
    # top three inches. They are chrome, not content: the switch decides which
    # book the whole tab is about, Refresh re-reads the log, and Quick Log is a
    # thing she DOES rather than reads. The sidebar is theirs now.
    #
    # It is a real sidebar again for the first time - the app was built without
    # one because it was assumed she used it on a phone, where the sidebar
    # cannot be opened. She uses it on desktop.
    with st.sidebar:
        theme.section("Your trades", "Controls")

    # THE ACCOUNT SWITCH STAYS ON THE PAGE, NOT IN THE SIDEBAR.
    #
    # It went in the sidebar with the other controls and had to come straight
    # back out. Streamlit collapses the sidebar on a narrower window and then
    # draws the button that reopens it at 0x0, so it cannot be recovered - and
    # a hidden account switch does not fail loudly, it silently pins the whole
    # tab to the real book. Rita saw her 35 practice trades vanish with no
    # control anywhere to bring them back.
    #
    # Everything else in the sidebar fails visibly if it disappears: she would
    # notice she cannot log a trade. This one lies about her money instead, so
    # it lives where nothing can collapse it.
    mode = _account_switch(settings, every_pos)
    all_pos = mr_split(every_pos, settings)[mode]

    # Recording a trade she just placed is the thing she does most. From the
    # sidebar it is one click from ANY tab, not just from this one.
    _quick_log_form(settings, strategies, provider)

    with st.sidebar:
        if st.button("↻ Refresh", key="trades_refresh", width="stretch"):
            st.session_state.pop("trades_rows", None)
            st.session_state.pop("_priced_positions", None)

    open_pos = pos_mod.open_positions(all_pos)
    closed = pos_mod.closed_positions(all_pos)
    legacy = [p for p in all_pos if p.status == "legacy"]
    bp_used = pos_mod.bp_committed_this_month(all_pos)
    # Only the real book's buying power constrains real trades, so this is what
    # the Find-a-trade checklist reads. Practice trades tie up nothing.
    st.session_state["month_bp_used"] = (
        pos_mod.bp_committed_this_month(mr_split(every_pos, settings)["real"]))

    if not all_pos:
        _empty(settings, every_pos, mode, source, rows)
        return

    if source == "local":
        theme.note("Reading the **local backup log** on this device. To track "
                   "trades everywhere, connect your Google Sheet in the "
                   "**⚙️ Settings** tab (one-time, ~2 minutes).")

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

    t_now, t_profit, t_plan, t_journal = st.tabs(
        ["📍 Now", "💰 Profit", "🎯 Plan", "📜 Journal"])

    with t_now:
        dashboard.band(this_month, pace, targets["monthly"], targets["weekly"],
                       perf["week_pl"], needs, len(items), priced_at, mode)
        _open_section(items, strategies, provider, priced_at)

    with t_profit:
        pnl.render(all_pos, settings, live_from, mode, every_pos, today)
        st.divider()
        # The month report in full, still reachable. Everything it does that
        # the grain view does not - the discipline tiles, the money math, the
        # month at a glance - lives in here and is worth keeping.
        _results_section(all_pos, settings, bp_used, mode, every_pos)

    with t_plan:
        dashboard.health_row(this_month, quality, pace, targets["monthly"])
        st.divider()
        # every_pos, not all_pos: the goals block draws the OTHER book faded
        # behind this one, so it needs both. It splits them itself.
        dashboard.goals_block(every_pos, settings, live_from, mode, today)
        st.divider()
        dashboard.process_row(this_month, quality, bp_used, targets["bp_limit"],
                              _median_bp(all_pos))

    with t_journal:
        journal.render(all_pos, settings, strategies, provider, mode)


def _empty(settings, every_pos, mode: str, source: str, rows) -> None:
    """The first-run page. No tabs: four empty pages is not a welcome."""
    book = "real-money book" if mode == "real" else "practice book"
    theme.note(f"Nothing in your **{book}** yet. Two ways to log a trade: "
               "**➕ Log a trade** in the sidebar for one you already "
               "placed in thinkorswim, or **Log this trade** in 🎯 Find a trade "
               "when the app finds the setup for you. Both ask which account "
               "the trade is in. Either way it lands here and the app starts "
               "watching your exit rules: take the win at 50% of the credit, "
               "at 21 days to expiration close or roll for a credit, stop the "
               "loss at 2x.")
    if mode == "real" and mr_split(every_pos, settings)["practice"]:
        theme.note("Your practice trades are still here - switch accounts "
                   "above to see them. They are kept completely apart from "
                   "this book.")
    if source == "local" and not rows:
        from src.logging_tools import webhook_logger
        if webhook_logger.is_configured():
            st.info("Your Google Sheet link is saved, but the log could not be "
                    "read back. That usually means the sheet still runs the "
                    "older script - paste the updated **LogTrade.gs** (in the "
                    "google_apps_script folder) into Apps Script, then Deploy "
                    "→ Manage deployments → Edit → New version → Deploy.")


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
