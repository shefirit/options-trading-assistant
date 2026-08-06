"""What she can do to an open trade: write a call, roll it, record an
assignment, close it, or delete a mistake.

Each one is a form that records something that already happened in
thinkorswim. Nothing here places a trade.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import streamlit as st

from src.engine.models import Action, OptionType
from ui import components, theme
from ui.trades.widgets import (
    _fill_price_input,
    _h_esc,
    _money_line,
    _signed,
    _step,
)


def _delete_control(trade_id, what: str, key: str) -> None:
    """A guarded delete: tick a box, then the button removes the trade's rows
    from the log (sheet or local backup). For trades logged by mistake / tests."""
    theme.note(f"This permanently removes **{what}** from your log. Use it only for a "
               "trade you logged by mistake or while testing - not one you actually "
               "traded (close that instead, so your results stay honest).")
    sure = st.checkbox("Yes, I logged this by mistake - delete it", key=f"delsure_{key}")
    if st.button("🗑️ Delete this trade", key=f"del_{key}", disabled=not sure):
        from src.logging_tools.trade_logger import delete_trade
        try:
            removed, source = delete_trade(trade_id)
        except Exception as e:
            st.error(f"Could not delete it: {e}")
            return
        st.session_state.pop("trades_rows", None)
        if removed:
            st.success(f"Deleted ({removed} row(s) removed from your "
                       f"{'Google Sheet' if source == 'sheet' else 'local log'}).")
            st.rerun()
        else:
            st.warning("Nothing was deleted - it may already be gone. Press ↻ Refresh.")


def _live_call_mid(provider, underlying: str, strike: float,
                   expiration: dt.date) -> Optional[float]:
    """Today's mid for one call, or None. Used to suggest what a freshly sold
    call was worth, so she doesn't have to dig per-leg prices out of TOS."""
    import datetime as dt

    if not strike or expiration is None:
        return None
    try:
        dte = max((expiration - dt.date.today()).days, 0)
        chain = provider.get_chain(underlying, dte_min=max(dte - 4, 0),
                                   dte_max=dte + 4)
    except Exception:
        return None
    if chain is None:
        return None
    exp = expiration.isoformat()
    contract = next(
        (c for c in chain.contracts
         if c.option_type == OptionType.CALL and c.expiration == exp
         and abs(c.strike - strike) < 1e-6), None)
    if contract is None or contract.mid <= 0:
        return None
    return round(contract.mid * 100, 2)


def _write_call_form(p, provider, kp: str = "detail") -> None:
    """She is uncovered: record the new call she has just written."""
    import datetime as dt

    with st.expander("➕ Sell a call against it (records the credit)",
                     key=f"writewrap_{kp}_{p.trade_id}", expanded=True):
        theme.note("Sell it in thinkorswim first, then write the fill down "
                   "here. Your SOP's PMCC sells about 30 days out at delta "
                   "0.30. The credit is banked in this month's profit and the "
                   "app starts watching the new call.")
        w1, w2, w3 = st.columns(3)
        sold_on = w1.date_input("Sold on", value=dt.date.today(),
                                max_value=dt.date.today(),
                                key=f"write_when_{kp}_{p.trade_id}", format=components.DATE_FMT)
        strike = w2.number_input("Strike you SOLD", min_value=0.0, step=1.0,
                                 key=f"write_strike_{kp}_{p.trade_id}")
        exp = w3.date_input("Expiration",
                            value=dt.date.today() + dt.timedelta(days=30),
                            min_value=dt.date.today(),
                            key=f"write_exp_{kp}_{p.trade_id}", format=components.DATE_FMT)
        suggested = _live_call_mid(provider, p.underlying, strike, exp)
        credit = _fill_price_input(
            "Credit price on your fill",
            f"write_credit_{kp}_{p.trade_id}_{strike:g}_{exp}",
            int(p.contracts or 1), default_total=suggested,
            help="The price you sold it at, per share - the app does the x100. "
                 "This is what your 50% profit target measures against from "
                 "now on.")
        if suggested:
            theme.note("**Prefilled from today's chain** for that contract. "
                       "Change it if your fill said otherwise.")
        note = st.text_input("Note (optional)", key=f"write_note_{kp}_{p.trade_id}")

        if st.button("Record the call I sold", type="primary",
                     key=f"writebtn_{kp}_{p.trade_id}"):
            if not strike:
                st.warning("Type the strike you sold first.")
            elif not credit:
                st.warning("Type the credit you collected - it is on your TOS "
                           "fill.")
            else:
                from src.logging_tools.trade_logger import roll_trade
                roll_trade(p.trade_id, p.underlying, p.strategy_name,
                           cash=float(credit), new_strike=float(strike),
                           new_expiration=exp, new_credit=float(credit),
                           note=note or f"Sold the {strike:g} call against it",
                           rolled_on=sold_on, account=p.account)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Recorded: ${credit:,.0f} collected, now tracking the "
                    f"{strike:g} call expiring {components.fmt_date(exp)}.")
                st.rerun()


def _short_call_leg(p):
    """The call she is short RIGHT NOW - the near-dated short CALL.

    Models 2 and 3 also carry short PUTs, which a call roll must never pick up,
    so this filters on type as well as side. None means nothing is written
    against the long side at the moment.
    """
    calls = [l for l in p.legs
             if l.action == Action.SELL and l.option_type == OptionType.CALL]
    if not calls:
        return None
    return min(calls, key=lambda l: (l.dte if l.dte is not None else 10**6))


def _call_label(strike: Optional[float], expiration: Optional[dt.date]) -> str:
    """"the 500 call expiring 2027-01-15" - how she says it out loud.

    Used where the sentence has room for it. In a tight row of the money panel
    the date is noise, so _call_short() gives the same call in three words.
    """
    if not strike:
        return "the call"
    text = f"the {strike:g} call"
    return (f"{text} expiring {components.fmt_date(expiration)}"
            if expiration else text)


def _call_short(strike: Optional[float]) -> str:
    """"500 call" - the same contract where the line has no room for a date."""
    return f"{strike:g} call" if strike else "call"


def _roll_money_panel(figs, old_short: str, new_short: str,
                      banked_before: float) -> None:
    """The money side of a roll, spelled out before she commits to it.

    Closing a trade has always ended with "Result: $X (profit)", and that one
    line is why closing feels clear. A roll had nothing of the sort: two dollar
    boxes, no running total, and no answer to the question she actually asks -
    did the call I just finished make money or lose it? A roll hides that,
    because the loss on the call being bought back and the credit on the new
    one arrive netted into a single number that is nearly always positive.
    """
    parts = [
        _money_line(
            f"The {old_short} you bought back",
            f"sold for ${figs.old_credit:,.0f}, cost ${figs.paid_to_close:,.0f} "
            "to buy back",
            _signed(figs.old_call_result),
            theme.GREEN if figs.old_call_result >= 0 else theme.RED),
    ]
    if figs.new_credit:
        parts.append(_money_line(
            f"The {new_short} you sold",
            "not profit yet - it is open, and buying it back later will cost "
            "some of this",
            f"+${figs.new_credit:,.0f}", theme.INK))
    parts.append(_money_line(
        "Cash from this one order",
        "the net on your TOS fill - this is what lands in this month's profit",
        _signed(figs.net_credit),
        theme.GREEN if figs.net_credit >= 0 else theme.RED, strong=True))

    st.markdown(
        f"<div style='background:{theme.TILE};border:1px solid {theme.BORDER};"
        f"border-radius:12px;padding:12px 16px;margin:6px 0 10px;'>"
        f"<div style='font-weight:800;color:{theme.INK};font-size:1.02rem;"
        f"margin-bottom:4px;'>💵 What this roll does to your money</div>"
        + "".join(parts) + "</div>", unsafe_allow_html=True)

    # The two numbers above look like they disagree whenever the finished call
    # lost money and the order still paid her - which is the normal case on a
    # roll up and out. Saying why, in the same breath, is the whole point.
    if figs.old_call_result < 0 <= figs.net_credit:
        theme.note(
            f"Both are true: the {old_short} finished "
            f"\\${abs(figs.old_call_result):,.0f} down, and the order still "
            f"paid you \\${figs.net_credit:,.0f}, because the new call's "
            f"\\${figs.new_credit:,.0f} came in at the same moment and more "
            "than covered it. Rolling turns a losing call into more time, not "
            "into a loss you have to take today.")
    after = banked_before + figs.net_credit
    theme.note(f"Premium banked on this trade so far: **\\${after:,.0f}** "
               f"(\\${banked_before:,.0f} before this roll). Your LEAPS is not "
               "touched and does not count until you sell it.")


def _roll_form(p, live: dict, provider, kp: str = "detail") -> None:
    """Record what happened to the short call: rolled in one order, or just
    bought back with the next one still to come.

    Either way this keeps ONE position from the LEAPS purchase to the LEAPS
    sale. Closing and re-logging instead would re-enter the LEAPS as a fresh
    several-thousand dollar purchase every month and make the results
    meaningless.
    """
    import datetime as dt

    from src.engine import roll_math

    old_leg = _short_call_leg(p)
    old_strike = old_leg.strike if old_leg is not None else None
    old_label = _call_label(old_strike, p.expiration)
    old_short = _call_short(old_strike)
    old_credit = float(p.credit or 0.0)

    cost_now = live.get("cost_to_close")
    contracts = max(int(p.contracts or 1), 1)

    # Keyed so it stays open through a rerun. Recording anything on the card
    # used to snap every expander shut and lose her place mid-form.
    with st.expander("🔄 Roll or close the short call", key=f"roll_{kp}_{p.trade_id}"):
        # Where this call stands BEFORE she types anything. The first version
        # only worked out the finished call's result once every box was filled,
        # which put the explanation after the confusing part instead of before
        # it - she opened the form, saw a wall of zeros, and was no wiser.
        if old_strike:
            standing = ""
            if cost_now is not None:
                so_far = old_credit - float(cost_now)
                standing = (
                    f" Buying it back costs about ${float(cost_now):,.0f} today, "
                    f"so as things stand that call is "
                    f"{'up' if so_far >= 0 else 'down'} ${abs(so_far):,.0f}.")
            st.markdown(components._esc(
                f"**You are short {old_label}**, sold for ${old_credit:,.0f}."
                + standing))
        # Her rule, in her words: roll it when the roll pays her a credit; when
        # it would cost a debit, close the call instead and sell the next one
        # separately. The two paths are named for that decision, not for the
        # mechanics.
        mode = st.radio(
            "What did you do?",
            ["Rolled it for a credit (one order)",
             "Closed the call (I'll sell a new one)"],
            key=f"roll_mode_{kp}_{p.trade_id}")
        rolling = mode.startswith("Rolled")

        if not rolling:
            theme.note("This records only the call you bought back. Nothing is "
                       "earning until you sell the next one, and the long leg "
                       "rides the stock both ways meanwhile - the card will say "
                       "so. Selling a new one straight away is fine: the form "
                       "for it opens right after this, and both land in the "
                       "same day's profit either way.")
            back_on = st.date_input("Closed it on", value=dt.date.today(),
                                    max_value=dt.date.today(),
                                    key=f"back_when_{kp}_{p.trade_id}", format=components.DATE_FMT)
            paid = _fill_price_input(
                "Price you paid to close it (as thinkorswim shows it)",
                f"back_paid_{kp}_{p.trade_id}", contracts,
                default_total=cost_now,
                help="The price on your fill, per share - the app multiplies by "
                     "100 and by your contracts. Prefilled with today's price.")
            if paid:
                # The same "did that call make money?" answer the roll path
                # gives. Bought back on its own it is simple arithmetic, but it
                # was still nowhere on the screen before she pressed Record.
                done = roll_math.buy_back_only(old_credit, float(paid))
                won = done.old_call_result >= 0
                st.markdown(components._esc(
                    f"**The {old_short} finishes "
                    f"{'up' if won else 'down'} "
                    f"${abs(done.old_call_result):,.0f}** - sold for "
                    f"${old_credit:,.0f}, cost ${paid:,.0f} to buy back. "
                    f"${paid:,.0f} comes out of this month's profit."))
            note = st.text_input("Note (optional)", key=f"back_note_{kp}_{p.trade_id}")
            if st.button("Record it", type="primary",
                         key=f"backbtn_{kp}_{p.trade_id}"):
                if not paid:
                    st.warning("Type what you paid to close the call.")
                else:
                    from src.logging_tools.trade_logger import roll_trade
                    roll_trade(p.trade_id, p.underlying, p.strategy_name,
                               cash=-float(paid), note=note, rolled_on=back_on,
                               account=p.account)
                    st.session_state.pop("trades_rows", None)
                    st.session_state.pop("_priced_positions", None)
                    st.session_state["ql_flash"] = (
                        f"Recorded: ${paid:,.0f} paid to close the call. This "
                        "trade has no call sold against it now - use ➕ Sell a "
                        "call against it when you write the next one.")
                    st.rerun()
            return

        # ---- step 1: the new call ---------------------------------------
        # Defaults that are USABLE on arrival. The first version opened with
        # strike 0.00 and an expiration a month from today - which, on a call
        # expiring further out than that, is a date the form's own validation
        # then rejects. Roll OUT from the call she holds, not from today.
        base = p.expiration or dt.date.today()
        floor = max(base + dt.timedelta(days=1), dt.date.today())
        _step(1, "The new call you sold",
              "The further-out call - the one you SOLD in the roll. It has to "
              "expire after the one you bought back.")
        r1, r2 = st.columns(2)
        new_strike = r2.number_input(
            "Strike", min_value=0.0, step=1.0,
            value=float(old_strike or 0.0),
            key=f"roll_strike_{kp}_{p.trade_id}",
            help="Rolling straight out keeps the same strike; rolling up and "
                 "out raises it. Prefilled with the strike you hold now.")
        new_exp = r1.date_input(
            "Expires", value=max(base + dt.timedelta(days=30), floor),
            min_value=floor, key=f"roll_exp_{kp}_{p.trade_id}",
            format=components.DATE_FMT)

        new_label = _call_label(new_strike, new_exp)
        new_short = _call_short(new_strike)
        suggested = _live_call_mid(provider, p.underlying, new_strike, new_exp)

        # ---- step 2: the money ------------------------------------------
        # A roll ORDER fills at one net price and never prints its two legs, so
        # asking outright for a leg price sent her hunting for a figure that
        # was not on her statement. One price is what she will have; the
        # two-price way round is folded away for the times she has both.
        _step(2, "What your fill said",
              f"It is the price your order filled at - one line, ending in "
              f"one number, like ...{p.underlying} {new_strike:g}/"
              f"{old_strike:g} CALL @1.50. In thinkorswim web it is under "
              f"Activity, with your other filled orders."
              if old_strike and new_strike else
              "It is the price your order filled at. In thinkorswim web your "
              "filled orders are under Activity.")
        cash = _fill_price_input(
            "Credit price on your fill", f"roll_cash_{kp}_{p.trade_id}",
            contracts, allow_negative=True,
            help="The single price at the end of your roll line - per share, "
                 "not the dollar total. The app does the x100. If the roll cost "
                 "you money instead of paying you, type a minus in front.")

        # Prefilled from the chain, so the common case is "leave it alone". The
        # key carries the strike and date because Streamlit ignores value=
        # once a key has been seen, and this default must follow those.
        new_credit = _fill_price_input(
            f"What the new {new_strike:g} call sold for by itself"
            if new_strike else "What the new call sold for by itself",
            f"roll_credit_{kp}_{p.trade_id}_{new_strike:g}_{new_exp}",
            contracts, default_total=suggested,
            help="Not on your fill - a one-order roll only prints the net. The "
                 "app fills in today's price for that contract, which is close "
                 "enough. Change it only if your order history happens to list "
                 "the two legs separately.")
        if suggested:
            theme.note("**Already filled in for you.** Your fill does not carry "
                       "this number, so the app priced the contract off today's "
                       "chain. Leave it as it is unless you know better.")
        elif new_strike:
            theme.note("The app could not price that contract just now, so type "
                       "what it sold for. If you only have the roll's net price, "
                       "open **My fill shows two prices** below instead.")
        figs = roll_math.from_net(old_credit, cash, new_credit)

        with st.expander("My fill shows two prices, not one",
                         key=f"rolltwo_{kp}_{p.trade_id}"):
            theme.note("Use this if you closed the old call and sold the new "
                       "one as two separate orders - then you have two prices "
                       "rather than one. Some order-history screens also list a "
                       "spread's legs one per line. Type both and the app works "
                       "out the net for you.")
            paid_back = _fill_price_input(
                f"Price you paid to buy back the {old_short}",
                f"roll_paid_{kp}_{p.trade_id}", contracts,
                default_total=cost_now,
                help="Prefilled with today's price - change it to your fill.")
            got = _fill_price_input(
                "Price you got for the new call",
                f"roll_got_{kp}_{p.trade_id}_{new_strike:g}_{new_exp}",
                contracts, default_total=suggested)
            use_legs = st.checkbox(
                "Use these two prices instead of the one above",
                key=f"roll_uselegs_{kp}_{p.trade_id}")
            if paid_back and got:
                legs = roll_math.from_legs(old_credit, paid_back, got)
                theme.note(f"Those two make a net of **{_signed(legs.net_credit)}** "
                           "on the order. It should match the price on your fill.")
            if use_legs:
                figs = roll_math.from_legs(old_credit, paid_back, got)
                cash, new_credit = figs.net_credit, figs.new_credit

        # ---- step 3: what it did, then record ---------------------------
        _step(3, "Check it, then record it",
              "Everything below is worked out from what you typed. Nothing is "
              "saved until you press the button.")

        if figs.impossible:
            st.warning(figs.impossible)
        elif cash < 0:
            # Her own rule: roll when it pays a credit, close when it would be
            # a debit. Recorded either way - it is her call, not the app's.
            theme.note(f"That is a **debit roll** - it cost you "
                       f"\\${abs(cash):,.0f} rather than paying you. You said "
                       "you would rather close the call and sell a new one when "
                       "the roll will not pay. **Closed the call** above does "
                       "that. Recording it as a roll is fine too if that is "
                       "really what you did.")

        if new_credit and cash:
            _roll_money_panel(figs, old_short, new_short, p.roll_income)
            # The derived buy-back is the sanity check she CAN judge: she knows
            # roughly what getting out of the old call cost, even when the new
            # call's own price came from the app rather than her fill.
            theme.note(f"Read the other way: buying back {old_label} cost you "
                       f"about **\\${figs.paid_to_close:,.0f}**. If that looks "
                       "wrong, the new call's price in step 2 is the one to fix.")
        else:
            theme.note("Fill in the price from your fill above and this is where "
                       "the app shows what the roll did to your money, before "
                       "you save anything.")

        f1, f2 = st.columns([1, 2])
        rolled_on = f1.date_input("Rolled on", value=dt.date.today(),
                                  max_value=dt.date.today(),
                                  key=f"roll_when_{kp}_{p.trade_id}", format=components.DATE_FMT)
        note = f2.text_input("Note (optional)", key=f"roll_note_{kp}_{p.trade_id}")

        if st.button("Record the roll", type="primary", key=f"rollbtn_{kp}_{p.trade_id}"):
            if not new_strike:
                st.warning("Step 1 needs the strike of the call you sold.")
            elif not cash:
                st.warning("Step 2 needs the price from your fill - it is the "
                           "money this roll actually made you.")
            elif not new_credit:
                st.warning("Step 2 needs what the new call sold for by itself. "
                           "The app usually fills this in; if it could not price "
                           "the contract, type it from your Account Trade "
                           "History.")
            elif figs.impossible:
                st.warning(figs.impossible)
            elif new_exp <= (p.expiration or dt.date.today()):
                st.warning(f"A roll moves the call OUT in time, but "
                           f"{components.fmt_date(new_exp)} is not after this "
                           f"position's current expiration "
                           f"({components.fmt_date(p.expiration)}). Check the date.")
            else:
                from src.logging_tools.trade_logger import roll_trade
                roll_trade(p.trade_id, p.underlying, p.strategy_name,
                           float(cash), float(new_strike), new_exp,
                           float(new_credit), note, rolled_on=rolled_on,
                           account=p.account)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Roll recorded: {_signed(cash)} banked "
                    f"(${p.roll_income + cash:,.0f} from rolls on this trade so "
                    f"far), now tracking the {new_strike:g} call expiring "
                    f"{components.fmt_date(new_exp)}.")
                st.rerun()


def _wheel_panel(p, price: Optional[float]) -> None:
    """What the shares she was assigned actually cost her.

    The one number the wheel turns on. Every premium she has collected on this
    trade - the put that got her assigned, and every call written since - comes
    off the price she paid, so the basis falls month after month. Nowhere in
    the app said so before: assignment used to end the trade, and the shares
    turned up as an unrelated covered call with the history thrown away.
    """
    from src.engine import wheel

    state = wheel.state_from(p, price)
    if state is None:
        return

    rows = [
        _money_line(
            f"{state.shares} shares, bought at {state.paid_per_share:g}",
            f"assigned on {components.fmt_date(p.assigned_on)}",
            f"-${state.paid_per_share * state.shares:,.0f}", theme.INK),
        _money_line(
            "Premium collected on this trade",
            "the put that assigned you, plus every call written since",
            f"+${state.premium_collected:,.0f}", theme.GREEN),
        _money_line(
            "So the shares really cost you",
            f"{state.cost_basis:g} a share - premium has taken "
            f"${state.premium_per_share:,.2f} off the {state.paid_per_share:g} "
            "you paid",
            f"${state.cost_basis:,.2f}", theme.INK, strong=True),
    ]
    st.markdown(
        f"<div style='background:{theme.TILE};border:1px solid {theme.BORDER};"
        f"border-radius:12px;padding:12px 16px;margin:6px 0 10px;'>"
        f"<div style='font-weight:800;color:{theme.INK};font-size:1.02rem;"
        f"margin-bottom:4px;'>🎡 Your wheel on {_h_esc(p.underlying)}</div>"
        + "".join(rows) + "</div>", unsafe_allow_html=True)

    if state.market_price is not None:
        if state.below_basis:
            theme.note(
                f"**{p.underlying} is at \\${state.market_price:,.2f}, under "
                f"your \\${state.cost_basis:,.2f} basis.** That is not a loss "
                "until you sell, and it is the normal middle of a wheel: every "
                "call you write against these shares pulls the basis down "
                "again. Keep writing calls at or above the basis so being "
                "called away is still a win.")
        else:
            theme.note(
                f"**{p.underlying} is at \\${state.market_price:,.2f}, above "
                f"your \\${state.cost_basis:,.2f} basis.** Selling here would "
                f"bank \\${state.unrealised:,.0f} on the shares on top of the "
                "premium you have already kept.")

    if state.call_strike:
        won = (state.called_away_profit or 0) >= 0
        theme.note(
            f"**If the {state.call_strike:g} call finishes in the money**, your "
            f"shares are called away at \\${state.call_strike:g} and the whole "
            f"wheel ends {'up' if won else 'DOWN'} "
            f"**\\${abs(state.called_away_profit or 0):,.0f}** - that is the "
            f"\\${state.call_strike:g} you would be paid against your "
            f"\\${state.cost_basis:,.2f} basis, on {state.shares} shares. "
            + ("A good outcome: take it and start the wheel again."
               if won else
               "That strike is BELOW your basis, so being called away locks in "
               "a loss. Your SOP says never write a call below your cost "
               "basis - roll it up and out instead."))
    else:
        theme.note(
            f"**Nothing is earning on these shares right now.** Sell a call "
            f"against them to start the premium going again - at "
            f"\\${state.cost_basis:,.2f} or higher, so being called away is "
            "still a win.")


def _assign_form(p, kp: str = "detail") -> None:
    """Record that a short put was assigned into shares.

    Her SOP treats assignment on a wheel or a cash-secured put as the plan, not
    an accident - so this is a normal thing to record, not an error path. It
    keeps the SAME trade, which is the whole point: the premium already
    collected has to keep counting towards what the shares cost.
    """
    import datetime as dt

    from src.engine import wheel

    if not wheel.is_wheelable(p):
        return

    strikes = [leg.strike for leg in p.legs
               if leg.action == Action.SELL and leg.option_type == OptionType.PUT]
    strike = strikes[0] if strikes else 0.0
    shares = 100 * max(int(p.contracts or 1), 1)

    with st.expander("🎡 I was assigned - I own the shares now",
                     key=f"asg_{kp}_{p.trade_id}"):
        theme.note(
            f"On a wheel this is the plan, not a mistake. Recording it here "
            f"keeps everything on ONE trade, so the \\${p.credit:,.0f} you "
            f"already collected still counts towards what the shares cost you. "
            f"The app then asks you to sell calls against them.")
        a1, a2 = st.columns(2)
        when = a1.date_input("Assigned on", value=dt.date.today(),
                             max_value=dt.date.today(),
                             key=f"asg_when_{kp}_{p.trade_id}",
                             format=components.DATE_FMT)
        at_strike = a2.number_input(
            "Strike you were assigned at", min_value=0.0, step=1.0,
            value=float(strike), key=f"asg_strike_{kp}_{p.trade_id}",
            help="The put you sold. Assignment always happens at its strike, "
                 "whatever the shares are worth that morning.")
        if at_strike:
            basis = at_strike - (float(p.credit or 0.0) / shares)
            st.markdown(components._esc(
                f"You will own **{shares} shares** at ${at_strike:g}, costing "
                f"**${at_strike * shares:,.0f}**. With the ${p.credit:,.0f} "
                f"premium already collected, your cost basis starts at "
                f"**${basis:,.2f} a share** - and every call you write from "
                "here lowers it."))
        note = st.text_input("Note (optional)", key=f"asg_note_{kp}_{p.trade_id}")
        if st.button("Record the assignment", type="primary",
                     key=f"asgbtn_{kp}_{p.trade_id}"):
            if not at_strike:
                st.warning("Type the strike you were assigned at.")
            else:
                from src.logging_tools.trade_logger import assign_trade
                assign_trade(p.trade_id, p.underlying, p.strategy_name,
                             float(at_strike), int(p.contracts or 1), note,
                             assigned_on=when, account=p.account)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Assignment recorded: you own {shares} {p.underlying} "
                    f"shares at {at_strike:g}. Sell a call against them with "
                    "➕ Sell a call against it.")
                st.rerun()


def _close_form(p, live: dict, label: str = "✔️ Close this trade (records the result)",
                kp: str = "detail") -> None:
    """Record the close of an open trade.

    Pulled out of the one-trade detail card so the Today block can offer it too:
    a trade that needs closing today should be closeable where she reads that,
    not only after finding it again in a dropdown further down.
    """
    # "closewrap_", not "close_" - the Record button below already owns that
    # prefix, and two elements sharing a key is a hard Streamlit error.
    with st.expander(label, key=f"closewrap_{kp}_{p.trade_id}"):
        theme.note("Close it in thinkorswim first, then record the fill here so "
                   "your results stay accurate.")
        default_cost = float(live["cost_to_close"]) if live.get("cost_to_close") \
            is not None else 0.0
        if p.is_debit:
            # Closing a PMCC or covered call PAYS her - she sells the
            # long side back. The old "what you paid" box could not go
            # below zero, so a close that paid had nowhere to be typed.
            default_in = live.get("position_value")
            proceeds = _fill_price_input(
                "Credit price you RECEIVED when you closed it",
                f"exit_in_{kp}_{p.trade_id}", int(p.contracts or 1),
                default_total=max(float(default_in or 0.0), 0.0),
                # Selling a LEAPS back is routinely 50.00+ a share, so the
                # typed-a-total guard has no business firing here.
                total_hint_above=None,
                help="Selling the LEAPS back, minus buying back the short "
                     "call - the one net price on your fill, per share. A "
                     "50.00 credit on 1 contract = $5,000. If closing "
                     "somehow cost you money, type 0 and note it below.")
            close_cash = float(proceeds)
            exit_cost = 0.0
        else:
            exit_cost = _fill_price_input(
                "Price you paid to close it",
                f"exit_cost_{kp}_{p.trade_id}", int(p.contracts or 1),
                default_total=max(default_cost, 0.0),
                help="The price on your TOS fill, per share - the app does the "
                     "x100. Prefilled with what it costs to close right now.")
            close_cash = -float(exit_cost)
        reason = st.selectbox(
            "Why you closed it",
            ["Profit target (50%) hit", "21 DTE time exit",
             "21 DTE credit roll (opened a new spread)", "Stop loss hit",
             "Rolled to a new position", "Expired worthless", "Other"],
            key=f"exit_reason_{kp}_{p.trade_id}")
        note = st.text_input("Lesson learned (optional - future you says thanks)",
                             key=f"exit_note_{kp}_{p.trade_id}")
        # The close banks the capital result. Roll credits were banked on
        # the days they landed, so they are not counted again here.
        realized = p.open_cash + close_cash
        total = realized + p.roll_income
        if p.is_debit:
            st.markdown(components._esc(
                f"Result: **${total:,.0f}** "
                f"({'profit' if total >= 0 else 'loss'}) - "
                f"${-p.open_cash:,.0f} out, ${p.roll_income:,.0f} banked "
                f"from rolls, ${close_cash:,.0f} back today."))
        else:
            st.markdown(components._esc(
                f"Result: **${realized:,.0f}** "
                f"({'profit' if realized >= 0 else 'loss'})"))
        if st.button("Record the close", type="primary",
                     key=f"close_{kp}_{p.trade_id}"):
            from src.logging_tools.trade_logger import close_trade
            dest, live_log = close_trade(p.trade_id, p.underlying, p.strategy_name,
                                         exit_cost, realized, reason, note,
                                         close_cash=close_cash,
                                         account=p.account)
            st.session_state.pop("trades_rows", None)
            st.session_state.pop("_priced_positions", None)
            st.rerun()
