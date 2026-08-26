"""Every open trade, one card each, the ones needing a decision first.

One trade is one card: what it is doing, a sentence saying where it stands in
dollars, and its own buttons. The full read-out folds away behind them.
"""

from __future__ import annotations

import streamlit as st

from ui import components, theme
from ui.trades.actions import (
    _assign_form,
    _assignment_plan_panel,
    _close_form,
    _roll_form,
    _sell_long_leg_form,
    _wheel_panel,
    _write_call_form,
)
from ui.trades.data import ACTION_SIGNALS, _exit_cfg_for
from ui.trades.widgets import _first_sentence, _h_esc, money


def _open_section(items, strategies, provider, priced_at) -> None:
    """Every open trade, each as one card, the ones needing a decision first.

    This replaced a three-part arrangement that showed the same trade up to
    three times: a red "2 of 5 need action" block with its own forms, a table
    of all five, and a dropdown that opened ONE of them in full. Acting on two
    trades meant scrolling between all three, and no single place answered
    "how is this one going?" - the table gave numbers, the card gave an
    instruction, and joining them up was left to her.

    One trade is now one card: the instruction, a sentence saying where it
    stands in dollars, and its own buttons. The numbers fold away behind them.
    """
    from src.engine import glance

    theme.section("Your open trades", "Open trades")
    if not items:
        st.success("No open trades right now. Record one with **➕ Quick Log** at the "
                   "top of this tab, or build one in 🎯 Find a trade.")
        return

    ordered = sorted(items, key=lambda it: glance.priority(it["signal"],
                                                           it["position"]))
    needs = [it for it in ordered if it["signal"].action in ACTION_SIGNALS]
    if needs:
        word = "trade needs" if len(needs) == 1 else "trades need"
        st.error(f"🔔 {len(needs)} of {len(items)} open {word} a decision today, "
                 f"and {'it is' if len(needs) == 1 else 'they are'} first below. "
                 "Do it in thinkorswim, then record it on the card.")
    else:
        st.success(f"✅ Nothing to do today - all {len(items)} open trades are "
                   "inside your rules.")
    theme.note(f"Prices checked at **{priced_at}** - they refresh on their own every "
               "few minutes, or press ↻ Refresh at the top.")

    # One table, every trade, urgency first - and clicking a row opens that one
    # underneath. Rita: "I want all trades organised nicely in table, not one
    # by one analysis." A card each meant scrolling past four trades to compare
    # two, and comparing is what a table is for.
    #
    # "single-row-required" rather than "single-row": exactly one row is always
    # selected, radio-style, so the panel below is never empty and the trade
    # that needs a decision today is the one already open.
    event = st.dataframe(
        components.positions_dataframe(ordered), width="stretch",
        hide_index=True, key="open_trades_table",
        on_select="rerun", selection_mode="single-row-required",
        column_config=components.positions_column_config())

    picked, moved = _card_index(ordered, event)

    theme.note(
        "**Click any row** to open that trade below - what it is doing, and the "
        "buttons to roll, close or record an assignment. The list is sorted so "
        "whatever needs a decision today is at the top and already open.")
    if moved:
        st.info(f"Showing **{ordered[picked]['position'].underlying}** - the one "
                "you had open. It has moved in the list above (the order follows "
                "what needs doing first), so the highlighted row is somewhere "
                "else now. Click any row to switch.")
    st.write("")
    _trade_card(ordered[picked], strategies, provider)


def _card_index(ordered, event) -> tuple[int, bool]:
    """Which trade the card below belongs to - by TRADE, not by row number.

    Streamlit remembers a table selection as a row NUMBER, and this table is
    sorted by what needs doing first and then by days left. So anything that
    re-sorts it moves a trade out from under that number while the number stays
    put: correcting an expiry (which is exactly how Rita lost an NDX trade she
    had just fixed), a three-minute price refresh changing a signal, or another
    trade being closed. The card then silently showed a different trade - and
    the roll, assign and CLOSE buttons on it belonged to that one.

    So the trade she picked is remembered by Trade ID. A row number that has not
    changed since the last run means she has not clicked anything, and the ID
    wins; a row number that HAS changed is a fresh choice, and it wins instead.

    Returns (index into `ordered`, whether the highlighted row and the card have
    come apart) - the caller says so on screen rather than leaving the table
    pointing one way and the card another.
    """
    rows = getattr(getattr(event, "selection", None), "rows", None) or []
    raw = rows[0] if rows and rows[0] < len(ordered) else 0
    ids = [it["position"].trade_id for it in ordered]
    picked = _follow_trade(ids, raw,
                           st.session_state.get("open_card_row"),
                           st.session_state.get("open_card_id"))
    st.session_state["open_card_row"] = raw
    st.session_state["open_card_id"] = ids[picked] if ids else None
    return picked, picked != raw


def _follow_trade(ids: list, raw: int, was_row, was_id) -> int:
    """The row the card should show: the remembered TRADE where the row number
    is stale, the clicked row where it is not.

    Kept apart from session state so the rule itself can be tested - it is the
    whole of the fix, and it is three lines that are easy to get backwards.
    """
    if raw == was_row and was_id in ids:
        return ids.index(was_id)
    return raw


def _trade_card(it: dict, strategies, provider) -> None:
    """One open trade: what it is doing, then what she can do about it."""
    from src.engine import glance
    from src.engine import wheel

    p, live, sig = it["position"], it["live"], it["signal"]
    px = live.get("underlying_price")
    wheel_state = wheel.state_from(p, px)

    with st.container(border=True):
        dte = p.dte_left()
        head = (f"{p.underlying} · {components.short_strategy(p.strategy_name)}"
                + (f" · {dte} day{'s' if dte != 1 else ''} left"
                   if dte is not None else ""))
        # The instruction rides on the same line as the name now. Stacked, the
        # signal word pushed the money sentence below the fold on a phone.
        chip_tone = {"red": "red", "amber": "amber", "green": "green"}.get(
            sig.tone, "neutral")
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:12px;"
            f"flex-wrap:wrap;margin-bottom:6px;'>"
            f"<span style='font-size:1.12rem;font-weight:800;color:{theme.INK};'>"
            f"{_h_esc(head)}</span>"
            + theme.chip(components._SIGNAL_WORD.get(sig.action, sig.action),
                         chip_tone)
            + "</div>",
            unsafe_allow_html=True)

        # The line she was previously left to assemble herself.
        st.markdown(components._esc(
            glance.summary_line(p, live, sig, wheel_state=wheel_state)))
        whole = glance.whole_trade_line(p, live)
        if whole:
            st.markdown(components._esc(whole))
        theme.note(_first_sentence(sig.reason))
        for n in sig.notes:
            st.warning(components._esc(n))

        if wheel_state is not None:
            _wheel_panel(p, px)
        elif p.awaiting_assignment:
            # The spread's numbers stopped applying the day she sold the long
            # put. This is the position she is actually holding now.
            _assignment_plan_panel(p, px)

        # Keyed, so it survives a rerun. Without a key, recording anything on
        # this card snapped every expander shut and she lost her place.
        with st.expander("🔢 Show the numbers", key=f"num_{p.trade_id}"):
            _trade_numbers(p, live, sig, strategies, px)

        if p.is_uncovered:
            _write_call_form(p, provider)
        else:
            # Anything with a leg SOLD can be rolled, and the form works out
            # which side from the position. It used to be offered on the debit
            # shapes alone - a PMCC or a covered call - so the trades her exit
            # rules most often say to roll, a threatened credit spread and a
            # cash secured put, had no way to record one. Closing and re-logging
            # was the only route, and it turned one put rolled four times into
            # five separate trades with four losses between them.
            _roll_form(p, live, provider)
        # Her third way out of a credit spread: sell the long put and let the
        # short one assign you. Offered before the close button, because it is
        # the decision the close button used to swallow.
        _sell_long_leg_form(p, provider)
        _assign_form(p)
        _close_form(p, live)


def _trade_numbers(p, live: dict, sig, strategies, px) -> None:
    """The full read-out, folded away behind the card's summary."""
    from src.engine import positions as pos_mod

    cols = st.columns(5)
    cols[0].metric(f"{p.underlying} now",
                   f"${px:,.2f}" if px else "n/a",
                   help="The underlying's price right now, about 15 minutes "
                        "delayed. This is what decides whether your strikes "
                        "are safe.")
    if p.is_long_premium:
        cols[1].metric("Credit received", money(p.credit),
                       help="Nothing, and that is right: you BOUGHT this one. "
                            "What the put(s) you sold paid you came off the "
                            "price of the call rather than counting as income.")
    else:
        cols[1].metric("Credit received", money(p.credit),
                       help="What the short call paid you - the basis for your "
                            "50% target." if p.is_debit else None)

    # Closing a position you only ever bought PAYS her, and the chain math
    # returns that as a negative cost. Printed raw it read "$-243", which is
    # the sign convention leaking onto the screen - a minus sign where the
    # good news was.
    ctc = live.get("cost_to_close")
    if ctc is not None and ctc < 0:
        cols[2].metric("Closing pays you", money(-ctc),
                       help="You bought this position, so unwinding it puts "
                            "money back in rather than costing you.")
    else:
        cols[2].metric("Costs to close now",
                       money(ctc) if ctc is not None else "n/a",
                       help="Buying back the short call alone." if p.is_debit
                            else None)
    dte_now = p.dte_left()
    cols[3].metric("Days left", dte_now if dte_now is not None else "n/a")

    # On the covered call models "max loss" was never the max loss - it
    # was the cash she laid out for shares she still owns. What she
    # actually needs is how far the put side protects her.
    protection = (pos_mod.protection_read(p, px)
                  if p.shares_cost > 0 else None)
    if protection and protection["flat_to"] is not None:
        cols[4].metric("Flat down to", f"${protection['flat_to']:,.0f}",
                       help="Your shares are protected this far down - "
                            "the P&L barely moves until here. See the "
                            "line below for what happens past it.")
    elif protection:
        cols[4].metric("Most you can lose",
                       money(abs(protection["worst_case"])),
                       help="The real worst case from the payoff, not "
                            "what you paid - your protective put caps "
                            "it." if protection["capped"] else
                            "The worst case if the stock went to zero.")
    else:
        cols[4].metric("Max loss", money(p.max_loss))

    if p.is_debit:
        components.render_debit_position_card(p, live)
    if protection:
        components.render_protection_read(p, protection)

    # The single most useful read for a beginner: where is price, versus
    # the option she SOLD, and how much room is between them.
    cushion = pos_mod.strike_cushion(p, px)
    if cushion:
        side = "call" if cushion["option_type"] == "call" else "put"
        direction = "rise" if side == "call" else "fall"
        if cushion["breached"]:
            theme.note(
                f"**{p.underlying} is at \\${px:,.2f}, past the {cushion['strike']:g} "
                f"{side} you sold.** That strike is breached - your SOP says roll "
                f"{'up' if side == 'call' else 'down'} and out for a credit, or close.")
        else:
            theme.note(
                f"**{p.underlying} is at \\${px:,.2f}.** The closest option you sold "
                f"is the **{cushion['strike']:g} {side}** - price would have to "
                f"{direction} **{abs(cushion['room_pct']) * 100:.1f}%** to reach it. "
                f"Your SOP says think about rolling once that room drops under 1.5%.")

    target_pct = float(_exit_cfg_for(p, strategies).get("profit_target_pct", 50) or 50)
    if sig.profit_pct is not None and p.credit > 0:
        if sig.profit_pct >= 0:
            st.progress(min(sig.profit_pct / target_pct, 1.0))
            theme.note(f"You've kept **{sig.profit_pct:.0f}%** of the credit so far - "
                       f"your SOP takes the win at **{target_pct:.0f}%**.")
        else:
            stop_mult = float(_exit_cfg_for(p, strategies).get("stop_loss_multiple", 2) or 2)
            st.progress(0.0)
            theme.note(f"Right now closing costs **more** than you collected "
                       f"({sig.profit_pct:.0f}% of the credit). Your stop-loss rule "
                       f"says close if that reaches **-{stop_mult * 100:.0f}%**.")
    if p.legs:
        strikes = " / ".join(f"{leg.strike:g}" for leg in p.legs)
        theme.note(f"Legs: **{strikes}** · {p.contracts} contract(s)"
                   + (f" · expires {components.fmt_date(p.expiration)}"
                      if p.expiration else ""))
