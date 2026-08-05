"""Every open trade, one card each, the ones needing a decision first.

One trade is one card: what it is doing, a sentence saying where it stands in
dollars, and its own buttons. The full read-out folds away behind them.
"""

from __future__ import annotations

import streamlit as st

from ui import components, theme
from ui.trades.actions import (
    _assign_form,
    _close_form,
    _delete_control,
    _roll_form,
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
        st.success("No open trades right now. Record one with **Quick Log** in Records "
                   "below, or build one in 🎯 Find a trade.")
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

    for it in ordered:
        _trade_card(it, strategies, provider)

    # The table is still the best way to compare six trades side by side, so it
    # stays - folded away, because it is no longer how she reads any ONE of
    # them, and open it was most of the scrolling she complained about.
    with st.expander("📋 See them all in one table"):
        st.dataframe(components.positions_dataframe(items), width="stretch",
                     hide_index=True,
                     column_config=components.positions_column_config())


def _trade_card(it: dict, strategies, provider) -> None:
    """One open trade: what it is doing, then what she can do about it."""
    from src.engine import glance
    from src.engine import positions as pos_mod
    from src.engine import wheel

    p, live, sig = it["position"], it["live"], it["signal"]
    px = live.get("underlying_price")
    wheel_state = wheel.state_from(p, px)

    with st.container(border=True):
        dte = p.dte_left()
        head = (f"{p.underlying} · {components.short_strategy(p.strategy_name)}"
                + (f" · {dte} day{'s' if dte != 1 else ''} left"
                   if dte is not None else ""))
        tone = {"red": theme.RED, "amber": theme.AMBER,
                "green": theme.GREEN}.get(sig.tone, theme.INK)
        st.markdown(
            f"<div style='font-size:1.05rem;font-weight:800;color:{theme.INK};'>"
            f"{_h_esc(head)}</div>"
            f"<div style='font-size:1.2rem;font-weight:800;color:{tone};"
            f"margin:2px 0 4px;'>"
            f"{components._SIGNAL_WORD.get(sig.action, sig.action)}</div>",
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

        with st.expander("🔢 Show the numbers"):
            _trade_numbers(p, live, sig, strategies, px)

        if p.is_uncovered:
            _write_call_form(p, provider)
        elif p.is_debit:
            _roll_form(p, live, provider)
        _assign_form(p)
        _close_form(p, live)

        with st.expander("🗑️ Delete this trade (logged by mistake / just testing)"):
            _delete_control(p.trade_id,
                            f"{p.underlying} {p.strategy_name} opened "
                            f"{components.fmt_date(p.opened)}",
                            key=f"open_{p.trade_id}")


def _trade_numbers(p, live: dict, sig, strategies, px) -> None:
    """The full read-out, folded away behind the card's summary."""
    from src.engine import positions as pos_mod

    cols = st.columns(5)
    cols[0].metric(f"{p.underlying} now",
                   f"${px:,.2f}" if px else "n/a",
                   help="The underlying's price right now, about 15 minutes "
                        "delayed. This is what decides whether your strikes "
                        "are safe.")
    cols[1].metric("Credit received", money(p.credit),
                   help="What the short call paid you - the basis for your "
                        "50% target." if p.is_debit else None)
    cols[2].metric("Costs to close now",
                   money(live["cost_to_close"]) if live.get("cost_to_close")
                   is not None else "n/a",
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
