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
        p = _pick(closed, labels, "fix_close_pick", "Which close")
        if p is None:
            return
        was_result = float(p.realized_pl or 0.0)
        # The stored figure is the open-and-close round trip only. Every number
        # she READS here adds the roll income back on, because that is the
        # trade's result everywhere else in the app - printing the smaller
        # figure here for a trade the table above calls something larger is how
        # a perfectly correct log comes to look broken.
        rolls = float(p.roll_income or 0.0)
        pays_to_close = p.is_debit
        # What she actually typed last time, whichever shape the trade is.
        was_cash = abs(float(p.close_cash if p.close_cash is not None
                             else -(p.exit_cost or 0.0)))

        c1, c2 = st.columns(2)
        c1.metric("Recorded now", money(was_result + rolls),
                  help="The result currently in your log for this trade, "
                       "including every roll credit you banked along the way.")
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
            theme.note(f"New result would be **\\${realized + rolls:,.0f}** - a "
                       f"change of **{'+' if delta >= 0 else '-'}"
                       f"\\${abs(delta):,.0f}**.")
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


def _pick(positions: list, labels: list[str], key: str, prompt: str):
    """A trade picker keyed on Trade ID rather than on list position.

    THIS IS NOT COSMETIC. Every picker here used `range(len(...))` as its
    options, so Streamlit saw the identical option list - 0, 1, 2 - whether it
    was showing the real book or the practice one. Switching accounts changed
    the trades behind those numbers and left the widget certain nothing had
    happened: it kept showing the label it had already drawn while the panel
    below rendered a completely different trade. Rita caught it as a SOFI
    heading over an iron condor's numbers.

    Trade IDs differ between books, so the options genuinely change and
    Streamlit resets the selection instead of carrying a stale one across.
    Returns the chosen Position, or None if the log changed underneath it.
    """
    by_id = {p.trade_id: p for p in positions}
    text = dict(zip(by_id, labels))
    chosen = st.selectbox(prompt, list(by_id),
                          format_func=lambda t: text.get(t, t), key=key)
    return by_id.get(chosen)


def _open_label(p) -> str:
    """Date first, because these lists are read as a diary.

    Leading with the symbol meant scanning a column of tickers to find "the one
    from the end of July"; leading with the date puts the lists in the order
    she thinks in, and the picker is already sorted that way.
    """
    return (f"{components.fmt_date(p.opened)}  ·  {p.underlying}  ·  "
            f"{components.short_strategy(p.strategy_name)}"
            + (f"  ·  banked ${p.realized_total:,.0f} so far"
               if p.realized_total else ""))


def _closed_label(p) -> str:
    return (f"{components.fmt_date(p.closed_on)}  ·  {p.underlying}  ·  "
            f"{components.short_strategy(p.strategy_name)}"
            f"  ·  {'+' if (p.realized_total or 0) >= 0 else '-'}"
            f"${abs(p.realized_total or 0):,.0f}")


def _story_panel(open_pos: list, closed: list) -> None:
    """One trade, told move by move, from the opening fill to the closing one.

    "All closed trades" gives one line per trade, and on anything she rolled
    that line is a single number covering weeks of activity. A rolled PMCC hid
    three fills that had never been logged and a fourth logged at half its
    size, and the one-line view could not have shown any of them.

    Open and closed are picked separately. In one mixed list the two kinds
    answer different questions - "how is this going" against "how did this end"
    - and a trade opened in June sorted in among trades closed in August, which
    is not an order anything reads in. Each list is now newest first on its own
    date: opened for the open book, closed for the closed one.

    Top level, not nested inside the closed-trades expander: an expander two
    deep collapses on every rerun. Keyed for the same reason.
    """
    open_pos = [p for p in components.by_opened_date(open_pos) if p.trade_id]
    closed = [p for p in components.by_closed_date(closed) if p.trade_id]
    if not open_pos and not closed:
        return

    with st.expander("📖 See one trade from start to finish", key="trade_story"):
        theme.note("Pick a trade and see every move you made on it, in order, "
                   "with what each one paid you or cost you. Newest first.")

        choices = []
        if open_pos:
            choices.append(f"Still open ({len(open_pos)})")
        if closed:
            choices.append(f"Closed ({len(closed)})")
        which = (st.radio("Which book", choices, horizontal=True,
                          key="story_side", label_visibility="collapsed")
                 if len(choices) > 1 else choices[0])

        if which.startswith("Still open"):
            p = _pick(open_pos, [_open_label(x) for x in open_pos],
                      "story_pick_open", "Which open trade")
        else:
            p = _pick(closed, [_closed_label(x) for x in closed],
                      "story_pick_closed", "Which closed trade")

        if p is not None:
            from src.engine import positions as pos_mod
            components.render_story(p, pos_mod.story(p))


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

    fixable = components.by_closed_date([p for p in closed if p.trade_id])
    _story_panel(open_pos, closed)

    if fixable:
        _fix_close_form(fixable, [_closed_label(p) for p in fixable])

    if open_pos:
        with st.expander(f"📂 All open trades ({len(open_pos)})",
                         key="open_table"):
            theme.note("Everything still on the books, most recently opened "
                       "first. What each one is doing right now, and what needs "
                       "a decision today, is in **Your open trades** further up "
                       "the tab - this is the record of what you hold and since "
                       "when.")
            st.dataframe(components.open_dataframe(open_pos), width="stretch",
                         hide_index=True,
                         column_config=components.open_column_config())

    if closed:
        with st.expander(f"📓 All closed trades ({len(closed)})",
                         key="closed_table"):
            theme.note("Every finished trade, most recently closed first.")
            st.dataframe(components.closed_dataframe(closed), width="stretch",
                         hide_index=True,
                         column_config=components.closed_column_config())
            if fixable:
                st.divider()
                theme.note("Delete a closed trade you only entered as a test:")
                # Two closes matching on every field would have shared one
                # dictionary key, and deleting the visible one would have
                # removed the wrong row.
                labels = [_closed_label(p) for p in fixable]
                cp = _pick(fixable, labels, "del_closed_pick",
                           "Closed trade to delete")
                if cp is not None:
                    _delete_control(cp.trade_id,
                                    labels[fixable.index(cp)],
                                    key=f"closed_{cp.trade_id}")

    deletable = components.by_opened_date([p for p in open_pos if p.trade_id])
    if deletable:
        with st.expander("🗑️ Delete an open trade (logged by mistake / just testing)",
                         key="del_open_wrap"):
            theme.note("This is for a row that should never have been logged. "
                       "If you actually placed the trade and it is finished, "
                       "**close** it on its card instead, so your results stay "
                       "honest.")
            labels = [_open_label(p) for p in deletable]
            op = _pick(deletable, labels, "del_open_pick", "Open trade to delete")
            if op is not None:
                _delete_control(op.trade_id, labels[deletable.index(op)],
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
