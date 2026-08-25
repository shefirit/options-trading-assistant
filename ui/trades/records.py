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


def _reopen_form(closed: list, labels: list[str]) -> None:
    """Take back a close that never happened.

    Closing is one primary button on a trade's card, and the card has not always
    been showing the trade she thought it was: the open-trades table remembers a
    row NUMBER, the list re-sorts whenever a correction or a price change moves
    a trade up it, and the buttons underneath then belonged to whatever had
    taken that row. She corrected an NDX expiry and the trade left her open
    trades - the close had been recorded against it.

    Until this existed the only way back was DELETE and re-log, which throws
    away the trade's rolls, its history and its Trade ID to undo one click. This
    appends instead, like every other correction here: the close row stays in
    the sheet, the replay stops reading it, and the trade goes back on the books
    exactly as it stood.

    Top level, never nested inside another expander - two deep and it collapses
    on every rerun - and keyed, so it stays open while she picks and ticks.
    """
    if not closed:
        return
    with st.expander("↩️ Put a trade back on the books (I never closed it)",
                     key="reopen"):
        theme.note("Closed the wrong trade, or recorded a close for one you have "
                   "not actually closed yet? This puts it back in your open "
                   "trades exactly as it was - same rolls, same credit, same "
                   "history. Nothing is deleted: the app writes a row saying "
                   "that close did not happen and stops counting it.")
        p = _pick(closed, labels, "reopen_pick", "Which trade")
        if p is None:
            return

        result = float(p.realized_total or 0.0)
        st.caption(f"{p.underlying} · {p.strategy_name} · closed "
                   f"{components.fmt_date(p.closed_on)}"
                   + (f" · {p.exit_reason}" if p.exit_reason else ""))
        theme.note(f"Putting it back removes **{money(result)}** from your "
                   f"results and returns it to your open trades, where the app "
                   f"will price it and watch your exit rules again.")
        why = st.text_input("What happened (optional)", key=f"reopen_why_{p.trade_id}",
                            placeholder="e.g. recorded the close on the wrong trade")
        sure = st.checkbox(f"Yes - {p.underlying} is still open in thinkorswim",
                           key=f"reopen_sure_{p.trade_id}")

        if st.button("Put it back on the books", type="primary", key="reopen_go",
                     disabled=not sure):
            from src.logging_tools.trade_logger import reopen_trade
            try:
                _dest, live = reopen_trade(
                    p.trade_id, p.underlying, p.strategy_name,
                    note=why.strip(), account=p.account)
            except Exception as e:
                st.error(f"Could not put it back: {e}")
                return
            st.session_state.pop("trades_rows", None)
            # The open set has changed, so the pricing pass has to run again -
            # without this the trade would be back in the list with no price
            # and no signal until the next three-minute refresh.
            st.session_state.pop("_priced_positions", None)
            for k in ("reopen", f"reopen_why_{p.trade_id}",
                      f"reopen_sure_{p.trade_id}"):
                st.session_state.pop(k, None)
            # It is the trade she cares about right now, so it is the one the
            # card up the page opens on.
            st.session_state["open_card_id"] = p.trade_id
            st.session_state["ql_flash"] = (
                f"↩️ {p.underlying} is back in your open trades - the close is "
                f"no longer counted. Saved to "
                f"{'your Google Sheet' if live else 'the local log'}.")
            st.rerun()


def _is_sold_put(leg) -> bool:
    return leg.action.value == "sell" and leg.option_type.value == "put"


def _financing_put_block(position, call_legs: list, put_legs: list,
                         contracts: int, bits: list[str]) -> list:
    """The put(s) sold against a bought call - add them, change them, drop them.

    This is the one place in the correction panel that can give a trade a leg
    it never had. It has to be: the LEAPS long call's financing put lives
    outside the strategy's `legs` block, so every one logged before Quick Log
    could take it went into the log as a bare call, with the puts - and the
    collateral behind them - recorded nowhere. Deleting and re-logging would
    work, but only by throwing away the trade's history to fix a missing leg.

    Returns the put legs as they should now stand (empty when she sets the
    count to zero, which is how a put entered by mistake comes off again).
    """
    from src.engine.models import Action, Leg, OptionType

    st.markdown("**The put(s) you sold against it**")
    theme.note("Sold puts at the same expiration to help pay for the call? Put "
               "them in here - the app will work out what the trade really cost "
               "and what the collateral behind them ties up. Leave the count at "
               "0 if there were none.")
    was_n = int(sum(l.quantity for l in put_legs))
    was_k = float(put_legs[0].strike) if put_legs else 0.0
    was_px = float(put_legs[0].premium) if put_legs else 0.0

    q1, q2, q3 = st.columns(3)
    n_puts = int(q1.number_input(
        "How many you SOLD", min_value=0, max_value=20, value=was_n, step=1,
        key=f"ed_fpn_{position.trade_id}",
        help="Per contract of the whole trade. Your SOP allows one per call "
             "bought and warns at two; past that it fails the check, but a "
             "trade you have already placed is still recorded as it is."))
    strike = float(q2.number_input(
        "Put strike", min_value=0.0, value=was_k, step=1.0, format="%.2f",
        key=f"ed_fpk_{position.trade_id}"))
    price = float(q3.number_input(
        "Price you got for each put", min_value=0.0, value=was_px, step=0.05,
        format="%.2f", key=f"ed_fppx_{position.trade_id}",
        help="Per share, for ONE put, the way thinkorswim prints it - 6.25, "
             "not 625."))

    if n_puts and price:
        total = price * 100 * n_puts * max(contracts, 1)
        collateral = strike * 100 * n_puts * max(contracts, 1)
        theme.note(f"That is **\\${total:,.0f}** collected ({price:.2f} x 100 x "
                   f"{n_puts * max(contracts, 1)}), against **\\${collateral:,.0f}** "
                   f"of collateral held until they expire.")

    if n_puts != was_n:
        bits.append(f"puts sold {was_n} → {n_puts}")
    elif n_puts and (abs(strike - was_k) > 1e-9 or abs(price - was_px) > 1e-9):
        bits.append(f"sold put {was_k:g} @ {was_px:.2f} → {strike:g} @ {price:.2f}")

    if not n_puts:
        return []
    # Same expiration as the call, which is what her SOP requires and how the
    # position was priced going in. The stored dte is measured from the day it
    # was opened, so copying the call's keeps both legs on one end date.
    dte = next((l.dte for l in call_legs if l.dte is not None), position.dte_at_entry)
    return [Leg(role="financing_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=strike, premium=price, quantity=n_puts,
                dte=int(dte) if dte is not None else None,
                delta=float(put_legs[0].delta) if put_legs else 0.0)]


def _legs_differ(new: list, old: list) -> bool:
    """True when the legs are not the same trade any more - a strike moved, a
    price was corrected, or a leg was added or taken away."""
    def shape(legs):
        return [(l.role, l.action.value, l.option_type.value, round(l.strike, 4),
                 int(l.quantity), round(l.premium, 4)) for l in legs]

    return shape(new) != shape(old)


def _edit_details_form(editable: list, labels: list[str], strategies: dict) -> None:
    """Fix details typed wrong when the trade was logged.

    The close price already had a correction path; everything typed at the
    OPENING did not, so a mistyped strike or contract count meant deleting the
    whole trade and rebuilding it - losing the roll history with it.

    Same append-only shape as the close fix: nothing is deleted, a correction
    row records what changed, and the app replays the log in order so the later
    value wins. She can correct a correction, and a fix that fails to send
    leaves the original untouched rather than half-applied.

    Strategy and ticker are deliberately NOT editable. Changing either makes it
    a different trade - the strategy re-bases the whole risk calculation and the
    ticker is baked into the Trade ID - and delete-and-relog is the honest move.
    """
    if not editable:
        return
    from src.engine.config_loader import get_strategy
    from src.engine.models import Trade
    from src.engine.quick_log import resize_after_edit, resize_bought_call

    with st.expander("✏️ Fix trade details I typed wrong", key="fix_details"):
        theme.note("Wrong strike, wrong number of contracts, wrong date? Correct it "
                   "here. Nothing is deleted - the app records the correction and "
                   "reads the new values from now on, and your rolls and close stay "
                   "exactly as they are.")
        p = _pick(editable, labels, "fix_details_pick", "Which trade")
        if p is None:
            return

        st.caption(f"{p.underlying} · {p.strategy_name} · logged "
                   f"{components.fmt_date(p.opened)}")
        changes: dict = {}
        bits: list[str] = []

        c1, c2, c3 = st.columns(3)
        opened = c1.date_input("Opened on", value=p.opened,
                               key=f"ed_open_{p.trade_id}", format="DD/MM/YYYY")
        expiry = c2.date_input("Expires", value=p.expiration,
                               key=f"ed_exp_{p.trade_id}", format="DD/MM/YYYY")
        contracts = int(c3.number_input(
            "Contracts", min_value=1, max_value=100,
            value=int(p.contracts or 1), step=1, key=f"ed_qty_{p.trade_id}"))

        if opened and opened != p.opened:
            changes["opened_on"] = opened.isoformat()
            bits.append(f"opened {components.fmt_date(p.opened)} → "
                        f"{components.fmt_date(opened)}")
        if expiry and expiry != p.expiration:
            changes["expiration"] = expiry.isoformat()
            bits.append(f"expiry {components.fmt_date(p.expiration)} → "
                        f"{components.fmt_date(expiry)}")
        if contracts != int(p.contracts or 1):
            changes["contracts"] = contracts
            bits.append(f"contracts {p.contracts} → {contracts}")

        strat_cfg = get_strategy(p.strategy_key) if p.strategy_key else {}
        basis = str(strat_cfg.get("sizing", {}).get("max_loss_basis", ""))
        # A bought call has no credit column and can gain a leg here - the
        # financing put she sold against it, which older rows could not record
        # at all. Both need knowing before the strike boxes are drawn.
        is_bought = basis == "long_premium"

        # One box per leg, labelled by what the leg IS. A bare list of numbers
        # is unreadable on a four-leg condor.
        original = [leg.model_copy(deep=True) for leg in (p.open_legs or p.legs)]
        legs = [leg.model_copy(deep=True) for leg in original]
        # On a bought call the sold puts come out of this row and get their own
        # block below, where the COUNT and the price can be changed too. Two
        # boxes for one strike is how a correction panel starts contradicting
        # itself.
        put_legs = [l for l in legs if _is_sold_put(l)] if is_bought else []
        if is_bought:
            legs = [l for l in legs if not _is_sold_put(l)]
        if legs:
            st.markdown("**Strikes**")
            cols = st.columns(min(len(legs), 4))
            for i, leg in enumerate(legs):
                label = (f"{'Bought' if leg.action.value == 'buy' else 'Sold'} "
                         f"{leg.option_type.value}")
                new = cols[i % len(cols)].number_input(
                    label, value=float(leg.strike), step=1.0, format="%.2f",
                    key=f"ed_k{i}_{p.trade_id}")
                if abs(float(new) - float(leg.strike)) > 1e-9:
                    bits.append(f"{label.lower()} {leg.strike:g} → {new:g}")
                    leg.strike = float(new)

        if is_bought:
            legs = legs + _financing_put_block(p, legs, put_legs, contracts, bits)

        # open_credit, NOT credit. Every roll overwrites `credit` with whatever
        # the CURRENT short call sold for, because that is what the 50% profit
        # target measures against - so on a rolled PMCC the two are different
        # numbers. This panel corrects the OPENING, so it has to use the opening
        # figure; reading `credit` here derived a cost basis off the wrong
        # number entirely and would have written a confidently wrong correction.
        day_one_credit = abs(float(p.open_credit if p.open_credit is not None
                                   else (p.credit or 0.0)))

        # A bought LEAPS collects nothing, so a "credit collected" box would sit
        # there at zero inviting a number that has no meaning on this shape. The
        # money that IS hers to correct is what the call cost, below - and what
        # the financing put paid, which comes off the put block above.
        c4, c5 = st.columns(2)
        credit = day_one_credit
        if not is_bought:
            credit = float(c4.number_input(
                "Credit collected when you opened it $",
                value=day_one_credit, step=1.0, format="%.2f",
                key=f"ed_credit_{p.trade_id}",
                help="What you collected on the DAY YOU OPENED it, for the whole "
                     "position - not per contract, and not what a later roll paid. "
                     "Correct a roll in the panel below this one."))

        # What she PAID, on the shapes where the credit is only half the story.
        # A PMCC's whole cost basis is the LEAPS, and without a box for it the
        # panel could correct everything about the trade except the biggest
        # number in it - which is exactly the $10 typo that sent her here.
        old_paid = None
        paid = None
        # What the sold put(s) handed back on the day, off their stored fill
        # prices. Zero on every shape except a financed LEAPS, where it is the
        # difference between "the call cost $2,115" and "$240 left the account".
        put_credit = round(p.short_put_credit, 2) if is_bought else 0.0
        if basis in ("debit", "long_premium"):
            base = put_credit if is_bought else day_one_credit
            # abs(), which is what makes this box right on a LEAPS logged
            # before the form knew it was a purchase: those rows recorded the
            # call's cost as a CREDIT, so the ledger has the size of it with
            # the sign the other way round. Saving rebuilds the money from this
            # figure and the put's fill, which repairs the direction too.
            old_paid = round(base - float(p.open_cash or 0.0), 2)
            paid = float(st.number_input(
                "What the call you BOUGHT cost you $" if is_bought
                else "What the long LEAPS cost you $",
                value=abs(old_paid), step=1.0,
                format="%.2f", key=f"ed_paid_{p.trade_id}",
                help="The debit on the LEAPS fill, for the whole position - your "
                     "cost basis for this trade."
                     + (f" The put(s) you sold against it paid ${put_credit:,.0f}, "
                        "which the app already has from the legs."
                        if is_bought and put_credit else "")))
            if abs(paid - abs(old_paid)) > 0.005:
                bits.append(f"LEAPS cost ${abs(old_paid):,.0f} → ${paid:,.0f}")
        book = c5.selectbox(
            "Which book", ["real", "paper"],
            index=0 if (p.account or "real") == "real" else 1,
            key=f"ed_book_{p.trade_id}",
            format_func=lambda v: "Real money" if v == "real" else "Practice")

        note = st.text_input("Note on the trade", value=p.note or "",
                             key=f"ed_note_{p.trade_id}")
        why = st.text_input("What was wrong (optional)", key=f"ed_why_{p.trade_id}",
                            placeholder="e.g. typed 2 contracts, only placed 1")

        if _legs_differ(legs, original):
            changes["legs"] = [
                {"role": l.role, "action": l.action.value, "type": l.option_type.value,
                 "strike": l.strike, "delta": l.delta, "premium": l.premium,
                 "qty": l.quantity, "dte": l.dte} for l in legs]
            changes["strikes"] = " / ".join(f"{l.strike:g}" for l in legs)
        if abs(credit - day_one_credit) > 0.005:
            bits.append(f"credit ${day_one_credit:,.0f} → ${credit:,.0f}")
        if book != (p.account or "real"):
            changes["account"] = book
            bits.append(f"book {p.account or 'real'} → {book}")
        if (note or "") != (p.note or ""):
            changes["note"] = note

        # Anything that moves the money or the size re-prices the risk. Leaving
        # the old max-loss and buying-power figures would misreport the two
        # numbers the dashboard and the monthly limit are built on.
        paid_changed = paid is not None and abs(paid - abs(old_paid or 0.0)) > 0.005
        if changes.get("legs") or "contracts" in changes or paid_changed or \
                abs(credit - day_one_credit) > 0.005:
            try:
                trade = Trade(strategy_key=p.strategy_key, underlying=p.underlying,
                              contracts=contracts, legs=legs,
                              underlying_price=p.underlying_price_at_entry or 0.0)
                if is_bought:
                    # Nothing is reversed out of the stored ledger here, on
                    # purpose: what the call cost is in the box above and the
                    # put's fill price is on its leg, so the money is rebuilt
                    # from the two fills. That is also what repairs a LEAPS
                    # logged before the form could take one, whose ledger says
                    # the call PAID her.
                    fresh = resize_bought_call(strat_cfg, trade, paid or 0.0)
                else:
                    # A corrected LEAPS cost is fed in as the ledger it implies,
                    # so resize_after_edit rebuilds the cost from the corrected
                    # figure rather than the stored one it would reverse out.
                    old_cash = (round(day_one_credit - paid, 2) if paid_changed
                                else float(p.open_cash or 0.0))
                    fresh = resize_after_edit(
                        strat_cfg, trade, credit,
                        old_credit=float(p.open_credit or p.credit or 0),
                        old_open_cash=old_cash,
                        old_shares_cost=float(p.shares_cost or 0.0),
                        old_contracts=int(p.contracts or 1))
                changes.update({
                    "credit": fresh["credit"], "open_cash": fresh["open_cash"],
                    "max_loss": fresh["max_loss"],
                    "buying_power": fresh["buying_power"],
                })
                if abs(fresh["max_loss"] - float(p.max_loss or 0)) > 0.5:
                    bits.append(f"most you can lose ${p.max_loss:,.0f} → "
                                f"${fresh['max_loss']:,.0f}")
            except Exception as exc:
                st.warning(f"Could not re-check the risk numbers for this one ({exc}). "
                           "The details will be corrected, but check the max loss "
                           "and buying power yourself.")
                changes["credit"] = round(credit, 2)

        if not changes:
            theme.note("Nothing changed yet - edit a field above and the correction "
                       "will appear here.")
            return

        # Caught while testing the panel: correcting 1 contract to 2 left the
        # credit at the one-contract figure, so the max loss came out right for
        # the wrong reason. The app must not guess that the credit doubled -
        # only her fill knows - but it must not let the mismatch pass unsaid.
        if "contracts" in changes and is_bought and not paid_changed:
            # Same trap, other shape: on a bought call the cost basis is the
            # paid box, and the app scales it per contract rather than guessing
            # at a fill only she can read.
            st.warning(
                f"You changed the contracts but left the call cost at "
                f"**\\${abs(old_paid or 0.0):,.0f}**. The app has scaled that per "
                f"contract - if {contracts} contracts actually cost something "
                "else, correct the box above too.")
        elif "contracts" in changes and abs(credit - day_one_credit) <= 0.005:
            st.warning(
                f"You changed the contracts but left the credit at "
                f"**\\${credit:,.0f}**. That box is the total for the whole "
                f"position, so if {contracts} contracts actually collected more, "
                "correct it here too - otherwise the result and the max loss will "
                "be measured against the old number.")

        theme.note("**This will record:** " + "; ".join(bits or ["a note change"]))

        if st.button("Save the correction", type="primary", key="ed_go"):
            from src.logging_tools.trade_logger import edit_trade
            try:
                dest, live = edit_trade(
                    p.trade_id, p.underlying, p.strategy_name, changes,
                    summary=(why.strip() or "; ".join(bits))[:250],
                    account=p.account)
            except Exception as e:
                st.error(f"Could not save the correction: {e}")
                return
            st.session_state.pop("trades_rows", None)
            st.session_state.pop("_priced_positions", None)
            for k in list(st.session_state):
                if k.startswith("ed_") and k.endswith(p.trade_id):
                    st.session_state.pop(k, None)
            st.session_state.pop("fix_details", None)
            # Correcting an expiry or a credit re-sorts the open-trades table
            # (urgency first, then days left), and the card up the page follows
            # the TRADE rather than the row number - so name the trade she just
            # corrected and it is still the one on screen when she scrolls up.
            st.session_state["open_card_id"] = p.trade_id
            st.session_state["ql_flash"] = (
                f"✏️ {p.underlying} corrected: {'; '.join(bits) or 'note updated'}. "
                f"Saved to {'your Google Sheet' if live else 'the local log'}.")
            st.rerun()


def _edit_roll_form(rollable: list, labels: list[str]) -> None:
    """Fix a roll fill typed wrong.

    Every roll is a number typed by hand, so the same slip is as likely here as
    at the opening - her DIA log had four roll fills that needed correcting and
    the only remedy was rebuilding the whole trade.

    A roll is addressed by the order it was WRITTEN (`RollEvent.seq`), never by
    where it sits in the list on screen. `Position.rolls` is ordered by DATE,
    and correcting a roll's date would reshuffle it - so the number handed to
    the correction has to come off the event itself.
    """
    if not rollable:
        return
    with st.expander("✏️ Fix a roll I typed wrong", key="fix_roll"):
        theme.note("Wrong credit on a roll, or rolled to the wrong strike? Correct "
                   "it here. The rest of the trade is untouched.")
        p = _pick(rollable, labels, "fix_roll_pick", "Which trade")
        if p is None or not p.rolls:
            return

        def roll_label(i: int, r) -> str:
            where = f" → {r.new_strike:g}" if r.new_strike is not None else " (bought back)"
            return (f"{i + 1}. {components.fmt_date(r.rolled_on)}{where}  ·  "
                    f"{'+' if r.cash >= 0 else '-'}${abs(r.cash):,.0f}")

        options = list(range(len(p.rolls)))
        which = st.selectbox(
            "Which roll", options,
            format_func=lambda i: roll_label(i, p.rolls[i]),
            key=f"rl_which_{p.trade_id}")
        r = p.rolls[which]

        c1, c2 = st.columns(2)
        cash = float(c1.number_input(
            "Cash it actually banked $", value=float(r.cash), step=1.0,
            format="%.2f", key=f"rl_cash_{p.trade_id}_{r.seq}",
            help="Signed: positive when the roll paid you, negative when buying "
                 "the call back cost you. Straight off the thinkorswim fill."))
        new_credit = float(c2.number_input(
            "What the NEW short call sold for $", value=float(r.new_credit or 0.0),
            step=1.0, format="%.2f", key=f"rl_credit_{p.trade_id}_{r.seq}",
            help="Its own premium, not the net of the roll. This is what your "
                 "50% profit target measures against from here."))

        c3, c4 = st.columns(2)
        strike = float(c3.number_input(
            "Rolled to strike", value=float(r.new_strike or 0.0), step=1.0,
            format="%.2f", key=f"rl_k_{p.trade_id}_{r.seq}",
            help="Leave at 0 if you only bought the call back and wrote nothing."))
        when = c4.date_input("Rolled on", value=r.rolled_on,
                             key=f"rl_when_{p.trade_id}_{r.seq}", format="DD/MM/YYYY")

        changes: dict = {}
        bits: list[str] = []
        if abs(cash - float(r.cash)) > 0.005:
            changes["open_cash"] = round(cash, 2)
            bits.append(f"cash ${r.cash:,.0f} → ${cash:,.0f}")
        if abs(new_credit - float(r.new_credit or 0.0)) > 0.005:
            changes["credit"] = round(new_credit, 2)
            bits.append(f"new call's premium ${r.new_credit or 0:,.0f} → ${new_credit:,.0f}")
        if abs(strike - float(r.new_strike or 0.0)) > 0.005 and strike > 0:
            changes["strikes"] = strike
            bits.append(f"strike {r.new_strike or 0:g} → {strike:g}")
        if when and when != r.rolled_on:
            changes["opened_on"] = when.isoformat()
            bits.append(f"date {components.fmt_date(r.rolled_on)} → "
                        f"{components.fmt_date(when)}")

        if not changes:
            theme.note("Nothing changed yet - edit a field above and the correction "
                       "will appear here.")
            return

        banked = p.roll_income + (changes.get("open_cash", r.cash) - r.cash)
        theme.note("**This will record:** " + "; ".join(bits)
                   + f". Roll income banked becomes **\\${banked:,.0f}**.")

        if st.button("Save the correction", type="primary", key="rl_go"):
            from src.logging_tools.trade_logger import edit_trade
            try:
                _dest, live = edit_trade(
                    p.trade_id, p.underlying, p.strategy_name, changes,
                    summary=f"Roll {which + 1}: " + "; ".join(bits),
                    target="roll", roll_index=r.seq, account=p.account)
            except Exception as e:
                st.error(f"Could not save the correction: {e}")
                return
            st.session_state.pop("trades_rows", None)
            for k in list(st.session_state):
                if k.startswith("rl_") and p.trade_id in k:
                    st.session_state.pop(k, None)
            st.session_state.pop("fix_roll", None)
            st.session_state["ql_flash"] = (
                f"✏️ {p.underlying} roll {which + 1} corrected: {'; '.join(bits)}. "
                f"Saved to {'your Google Sheet' if live else 'the local log'}.")
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
        closed_labels = [_closed_label(p) for p in fixable]
        _fix_close_form(fixable, closed_labels)
        _reopen_form(fixable, closed_labels)

    # Open trades first: a mistyped detail is usually caught while the trade is
    # still on the books, and correcting it there fixes what the open-trade
    # cards and the buying-power figure are reading right now.
    correctable = (components.by_opened_date([p for p in open_pos if p.trade_id])
                   + list(fixable))
    labels = [(_open_label(p) if p.status == "open" else _closed_label(p))
              for p in correctable]
    _edit_details_form(correctable, labels, strategies)

    rollable = [p for p in correctable if p.rolls]
    _edit_roll_form(rollable, [labels[correctable.index(p)] for p in rollable])

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
