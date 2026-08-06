"""The bookkeeping: log a trade, correct a fill, look back, delete a mistake.

These are the same kind of job - occasional, and none of them is urgent - so
they sit together at the bottom of the tab rather than above the alert saying a
trade needs closing today.
"""

from __future__ import annotations

import streamlit as st

from ui import components, theme
from ui.trades.actions import _delete_control
from ui.trades.quick_log import _quick_log_form
from ui.trades.widgets import _fill_price_input, money


def _fix_close_form(closed: list, labels: list[str]) -> None:
    """Correct a close recorded with the wrong fill price.

    She typed $2,300 for an NDX condor that actually filled at $2,260 - an easy
    slip when two similar orders go through seconds apart. She cannot edit the
    sheet by hand, and deleting the trade to re-log it risks the whole record
    over a $40 typo.

    So this appends a fresh close event instead. The log is an event log read in
    order and the LAST close for a trade wins, so a correction supersedes the
    mistake without deleting anything, and the wrong row stays as history.

    Top level, never nested inside another expander: this used to live inside
    "All closed trades", and an expander two deep collapses on every rerun.

    Keyed, which is what keeps it open. Every widget in here used to call a
    _keep_fix_open callback that set a session flag, because Streamlit re-drew
    an expander closed on every rerun - so the panel snapped shut the moment
    she picked a trade or typed a number. A keyed expander tracks its own state
    now, and the whole workaround is gone.
    """
    if not closed:
        return
    with st.expander("✏️ Fix a close I typed wrong", key="fix_close"):
        theme.note("Recorded a close with the wrong fill price? Put the right number "
                   "in here. Nothing is deleted - the app writes a correction that "
                   "replaces the old figure, and you can correct it again if needed.")
        i = st.selectbox("Which close", range(len(closed)),
                         format_func=lambda n: labels[n], key="fix_close_pick")
        p = closed[int(i)]
        was_result = float(p.realized_pl or 0.0)
        pays_to_close = p.is_debit
        # What she actually typed last time, whichever shape the trade is.
        was_cash = abs(float(p.close_cash if p.close_cash is not None
                             else -(p.exit_cost or 0.0)))

        c1, c2 = st.columns(2)
        c1.metric("Recorded now", money(was_result),
                  help="The result currently in your log for this trade.")
        # Keyed per trade on purpose. With one shared key, switching trades kept
        # the previous one's number in the box and offered to "correct" the new
        # trade to a figure that had nothing to do with it.
        with c2:
            cost = _fill_price_input(
                "Price you RECEIVED closing it" if pays_to_close
                else "Price you PAID to close it",
                f"fix_cost_{p.trade_id}", int(p.contracts or 1),
                default_total=was_cash,
                # Closing a PMCC means selling the LEAPS back, which is a large
                # per-share price by nature - no typed-a-total guard here.
                total_hint_above=None if pays_to_close else 100.0,
                help="The real fill from thinkorswim, per share. Prefilled with "
                     "what is recorded now.")

        close_cash = float(cost) if pays_to_close else -float(cost)
        realized = p.open_cash + close_cash
        delta = realized - was_result
        if abs(delta) < 0.005:
            theme.note("That matches what is already recorded - change the number to "
                       "correct it.")
        else:
            theme.note(f"New result would be **\\${realized:,.0f}** - a change of "
                       f"**{'+' if delta >= 0 else '-'}\\${abs(delta):,.0f}**.")
        why = st.text_input("What was wrong (optional)",
                            key=f"fix_why_{p.trade_id}",
                            placeholder="e.g. typed the wrong fill price")

        if st.button("Save the correction", type="primary", key="fix_go",
                     disabled=abs(delta) < 0.005):
            from src.logging_tools.trade_logger import close_trade
            try:
                dest, live = close_trade(
                    p.trade_id, p.underlying, p.strategy_name,
                    exit_cost=(0.0 if pays_to_close else float(cost)),
                    realized_pl=round(realized, 2),
                    # Keep the original exit reason so the rules-followed score
                    # is untouched by a bookkeeping fix.
                    reason=(p.exit_reason or "").split(" - ")[0] or "Corrected",
                    note=why.strip() or "Corrected fill price",
                    closed_on=p.closed_on, close_cash=close_cash,
                    account=p.account)
            except Exception as e:
                st.error(f"Could not save the correction: {e}")
                return
            st.session_state.pop("trades_rows", None)
            # "fix_close" closes the panel now the correction has landed; the
            # other two clear the boxes so a second correction starts blank.
            for k in ("fix_close", f"fix_cost_{p.trade_id}", f"fix_why_{p.trade_id}"):
                st.session_state.pop(k, None)
            st.session_state["ql_flash"] = (
                f"✏️ {p.underlying} corrected: result is now ${realized:,.0f} "
                f"(was ${was_result:,.0f}). Saved to "
                f"{'your Google Sheet' if live else 'the local log'}.")
            st.rerun()


def _records_section(settings, strategies, provider, closed, legacy, bp_used,
                     open_pos=()) -> None:
    """The bookkeeping, in one place instead of scattered up and down the tab.

    Logging a trade, correcting a fill and deleting a mistake are the same kind
    of job, done occasionally. They used to sit ABOVE the alert saying a trade
    needs closing today, which put the rarest task first.

    Deleting an OPEN trade lives here too now. It used to be an expander on
    every card, which cost a row per trade and put the one irreversible button
    in the app on the screen she looks at daily. It is a rare, careful job, and
    this is where the rare, careful jobs are.

    Quick Log is NOT here any more. Recording a trade she just placed is the
    most frequent thing she does on this tab, and it sat five screens down
    behind everything else - see ui/trades/__init__.py, which now renders it at
    the top. What is left here is genuinely occasional: correcting a fill,
    reading back the closed trades, deleting a mistake.
    """
    theme.section("Correct and look back", "Records")

    fixable = [p for p in closed if p.trade_id]
    if fixable:
        _fix_close_form(fixable, [
            f"{p.underlying}  ·  {p.strategy_name}  ·  "
            f"closed {components.fmt_date(p.closed_on)}"
            f"  ·  result ${(p.realized_pl or 0):,.0f}" for p in fixable])

    if closed:
        with st.expander(f"📓 All closed trades ({len(closed)})",
                         key="closed_table"):
            st.dataframe(components.closed_dataframe(closed), width="stretch",
                         hide_index=True)
            if fixable:
                st.divider()
                theme.note("Delete a closed trade you only entered as a test:")
                # Two closes matching on every field would have shared one
                # dictionary key, and deleting the visible one would have
                # removed the wrong row.
                labels = [f"{i + 1}.  {p.underlying}  ·  {p.strategy_name}  ·  "
                          f"closed {components.fmt_date(p.closed_on)}  ·  "
                          f"result ${(p.realized_pl or 0):,.0f}"
                          for i, p in enumerate(fixable)]
                idx = st.selectbox("Closed trade to delete", range(len(fixable)),
                                   format_func=lambda i: labels[i], key="del_closed_pick")
                cp = fixable[int(idx)]
                _delete_control(cp.trade_id, labels[int(idx)], key=f"closed_{cp.trade_id}")

    deletable = [p for p in open_pos if p.trade_id]
    if deletable:
        with st.expander("🗑️ Delete an open trade (logged by mistake / just testing)",
                         key="del_open_wrap"):
            theme.note("This is for a row that should never have been logged. "
                       "If you actually placed the trade and it is finished, "
                       "**close** it on its card instead, so your results stay "
                       "honest.")
            labels = [f"{i + 1}.  {p.underlying}  ·  {p.strategy_name}  ·  "
                      f"opened {components.fmt_date(p.opened)}"
                      for i, p in enumerate(deletable)]
            idx = st.selectbox("Open trade to delete", range(len(deletable)),
                               format_func=lambda i: labels[i],
                               key="del_open_pick")
            op = deletable[int(idx)]
            _delete_control(op.trade_id, labels[int(idx)],
                            key=f"open_{op.trade_id}")

    if legacy:
        with st.expander(f"🗄️ Trades logged before tracking existed ({len(legacy)})"):
            theme.note("These were logged with an older version of the app, so they "
                       "can't be tracked live - shown for your records only.")
            import pandas as pd
            st.dataframe(pd.DataFrame([{
                "Date": p.opened, "Symbol": p.underlying, "Strategy": p.strategy_name,
                "Credit $": p.credit, "Notes": p.note} for p in legacy]),
                width="stretch", hide_index=True)

    if not closed and bp_used:
        limit = float(settings["risk_limits"]["monthly_bp_limit"])
        theme.note("No closed trades yet - your results build from the first close. "
                   f"Meanwhile you have committed **\\${bp_used:,.0f}** of your "
                   f"**\\${limit:,.0f}** monthly buying-power budget.")
