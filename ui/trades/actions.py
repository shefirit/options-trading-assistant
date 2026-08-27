"""What she can do to an open trade: write a call, roll it, sell one leg off,
record an assignment, close it, or delete a mistake.

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


def _live_leg_mid(provider, underlying: str, strike: float,
                  expiration: dt.date,
                  option_type: OptionType = OptionType.CALL,
                  contracts: int = 1) -> Optional[float]:
    """Today's mid for that leg in dollars for the WHOLE position, or None.

    Used to prefill what a leg is worth so she does not have to dig per-leg
    prices out of thinkorswim: what a freshly sold call went for, and what the
    long put she is about to sell back is fetching today.

    Every money box it feeds asks for the position's total, so the contract
    count belongs here rather than at each call. Left out, the prefill on three
    contracts arrived a third of its real size - right on one contract, which
    is why it went unnoticed - and on a roll that turned a perfectly good chain
    price into one the form's own arithmetic then rejected.
    """
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
         if c.option_type == option_type and c.expiration == exp
         and abs(c.strike - strike) < 1e-6), None)
    if contract is None or contract.mid <= 0:
        return None
    return round(contract.mid * 100 * max(int(contracts), 1), 2)


def _live_call_mid(provider, underlying: str, strike: float,
                   expiration: dt.date, contracts: int = 1) -> Optional[float]:
    """Today's mid for the calls she holds - the call side of _live_leg_mid."""
    return _live_leg_mid(provider, underlying, strike, expiration,
                         OptionType.CALL, contracts)


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
        suggested = _live_call_mid(provider, p.underlying, strike, exp,
                                   int(p.contracts or 1))
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


def _short_leg(p, kind: OptionType):
    """The option of this type she is short RIGHT NOW - the near-dated one.

    Filtered on type as well as side because most of the shapes here carry both:
    models 2 and 3 are short a call and long a put, an iron condor is short
    both sides at once, and a call roll must never pick a put up. Near-dated
    because a PMCC's short call sits months in front of its LEAPS.

    None means nothing of that kind is sold at the moment.
    """
    legs = [l for l in p.legs
            if l.action == Action.SELL and l.option_type == kind]
    if not legs:
        return None
    return min(legs, key=lambda l: (l.dte if l.dte is not None else 10**6))


def _short_call_leg(p):
    """The call she is short right now - the call side of _short_leg()."""
    return _short_leg(p, OptionType.CALL)


def _spread_long_leg(p, short_leg, kind: OptionType):
    """The bought leg sitting under that short one, when this is a vertical.

    A vertical's two legs share an expiration, and that is what tells this
    apart from the other bought legs in the book: a PMCC's LEAPS is a long call
    too, but it expires a year past the call written against it and is nobody's
    protection. Matching on the short leg's own dte keeps a call roll on a PMCC
    from dragging the LEAPS along with it - which would rewrite the trade.

    None on a naked short leg: a cash secured put, a covered call's call.
    """
    if short_leg is None or short_leg.dte is None:
        return None
    return next((l for l in p.legs
                 if l.action == Action.BUY and l.option_type == kind
                 and l.dte == short_leg.dte), None)


def _rollable_sides(p) -> list[OptionType]:
    """Which sides of this trade have something SOLD that could be rolled.

    Usually one. An iron condor is short both, and so is a covered call written
    against shares that also carry a protective put she has sold against - so
    the form asks which side she rolled rather than guessing, because guessing
    wrong records the roll against the wrong leg.

    The debit shapes lead with the call: on a PMCC or covered call the call IS
    the income leg, and it is the only thing that has ever been rolled here.
    """
    order = ((OptionType.CALL, OptionType.PUT) if p.is_debit
             else (OptionType.PUT, OptionType.CALL))
    return [kind for kind in order if _short_leg(p, kind) is not None]


def _leg_label(strike: Optional[float], expiration: Optional[dt.date],
               word: str = "call") -> str:
    """"the 500 call expiring 2027-01-15" - how she says it out loud.

    Used where the sentence has room for it. In a tight row of the money panel
    the date is noise, so _leg_short() gives the same contract in three words.
    """
    if not strike:
        return f"the {word}"
    text = f"the {strike:g} {word}"
    return (f"{text} expiring {components.fmt_date(expiration)}"
            if expiration else text)


def _leg_short(strike: Optional[float], word: str = "call") -> str:
    """"500 call" - the same contract where the line has no room for a date."""
    return f"{strike:g} {word}" if strike else word


def _roll_money_panel(figs, old_short: str, new_short: str,
                      banked_before: float, tail: str = "") -> None:
    """The money side of a roll, spelled out before she commits to it.

    Closing a trade has always ended with "Result: $X (profit)", and that one
    line is why closing feels clear. A roll had nothing of the sort: two dollar
    boxes, no running total, and no answer to the question she actually asks -
    did the leg I just finished make money or lose it? A roll hides that,
    because the loss on the option being bought back and the credit on the new
    one arrive netted into a single number that is nearly always positive.
    """
    word = figs.leg_word
    parts = [
        _money_line(
            f"The {old_short} you bought back",
            f"sold for ${figs.old_credit:,.0f}, cost ${figs.paid_to_close:,.0f} "
            "to buy back",
            _signed(figs.old_leg_result),
            theme.GREEN if figs.old_leg_result >= 0 else theme.RED),
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

    # The two numbers above look like they disagree whenever the finished leg
    # lost money and the order still paid her - which is the normal case on a
    # roll away from trouble. Saying why, in the same breath, is the whole point.
    if figs.old_leg_result < 0 <= figs.net_credit:
        theme.note(
            f"Both are true: the {old_short} finished "
            f"\\${abs(figs.old_leg_result):,.0f} down, and the order still "
            f"paid you \\${figs.net_credit:,.0f}, because the new {word}'s "
            f"\\${figs.new_credit:,.0f} came in at the same moment and more "
            f"than covered it. Rolling turns a losing {word} into more time, "
            "not into a loss you have to take today.")
    after = banked_before + figs.net_credit
    theme.note(f"Premium banked on this trade so far: **\\${after:,.0f}** "
               f"(\\${banked_before:,.0f} before this roll)." + (f" {tail}" if tail else ""))


def _roll_side_choice(p, sides: list, kp: str) -> OptionType:
    """Which side she rolled, asked only when the trade is short both.

    An iron condor is short a put AND a call, and so is a covered call sold
    against a put she has written. Rolling is always one side at a time - and
    recording it against the wrong one moves a leg she never touched, so this
    asks rather than guesses. Everything else has one answer and no question.
    """
    if len(sides) == 1:
        return sides[0]
    labels = {OptionType.PUT: "The put side", OptionType.CALL: "The call side"}
    picked = st.radio(
        "Which side did you roll?", [labels[k] for k in sides], horizontal=True,
        key=f"roll_side_{kp}_{p.trade_id}",
        help="One side at a time - that is how the order fills, and how it is "
             "recorded. Roll the other side as a second roll if you moved both.")
    return next(k for k in sides if labels[k] == picked)


def _roll_form(p, live: dict, provider, kp: str = "detail") -> None:
    """Record what happened to the leg she is short: rolled in one order, or
    just bought back with the next one still to come.

    Either way this keeps ONE position from the day it was opened to the day it
    is closed. Closing and re-logging instead would re-enter a PMCC's LEAPS as
    a fresh several-thousand dollar purchase every month, and would turn a cash
    secured put rolled four times into five unrelated trades with four losses
    and no sign that they were the same position all along.

    The form began as a call-only thing, because a PMCC's short call was the
    only leg the app could roll. Her SOP has always said to roll a threatened
    put down and out for a credit as well, and until now the only way to record
    that was to close the trade and log a new one. So the side is decided here
    and every sentence below follows it.
    """
    import datetime as dt

    from src.engine import roll_math

    sides = _rollable_sides(p)
    if not sides:
        return          # nothing sold, so there is nothing to roll

    # Keyed so it stays open through a rerun. Recording anything on the card
    # used to snap every expander shut and lose her place mid-form.
    with st.expander("🔄 Roll it (or buy the short leg back)",
                     key=f"roll_{kp}_{p.trade_id}"):
        kind = _roll_side_choice(p, sides, kp)
        word = kind.value                       # "call" or "put"
        old_leg = _short_leg(p, kind)
        old_strike = old_leg.strike if old_leg is not None else None
        old_long = _spread_long_leg(p, old_leg, kind)
        spread = old_long is not None
        # On a spread every price is the SPREAD's price - that is what one
        # order fills at and what her statement prints - so the word the money
        # panel uses has to be "spread" and not the short leg's own name.
        money_word = "spread" if spread else word
        width = abs((old_strike or 0.0) - old_long.strike) if spread else 0.0

        old_label = _leg_label(old_strike, p.expiration, word)
        old_short = (f"{old_strike:g}/{old_long.strike:g} {word} spread"
                     if spread else _leg_short(old_strike, word))
        old_credit = float(p.credit or 0.0)

        # What closing costs today, as a prefill. Only where it means the leg
        # being rolled: the priced figure covers every near-dated leg, so on an
        # iron condor it is BOTH sides at once and would prefill roughly double
        # the true buy-back of the one side she rolled.
        cost_now = live.get("cost_to_close") if len(sides) == 1 else None
        contracts = max(int(p.contracts or 1), 1)

        # Where this leg stands BEFORE she types anything. The first version
        # only worked out the finished leg's result once every box was filled,
        # which put the explanation after the confusing part instead of before
        # it - she opened the form, saw a wall of zeros, and was no wiser.
        if old_strike:
            standing = ""
            if cost_now is not None:
                so_far = old_credit - float(cost_now)
                standing = (
                    f" Buying it back costs about ${float(cost_now):,.0f} today, "
                    f"so as things stand it is "
                    f"{'up' if so_far >= 0 else 'down'} ${abs(so_far):,.0f}.")
            holding = (f"the {old_strike:g}/{old_long.strike:g} {word} spread"
                       if spread else old_label)
            st.markdown(components._esc(
                f"**You are short {holding}**, sold for ${old_credit:,.0f}."
                + standing))

        # Her rule, in her words: roll it when the roll pays her a credit; when
        # it would cost a debit, close it and sell the next one separately. The
        # buy-back path only exists where something is LEFT afterwards - the
        # LEAPS of a PMCC, the shares of a covered call. Buying back a cash
        # secured put leaves nothing behind, so there it is simply a close, and
        # offering it here would record a live trade that no longer exists.
        rolling = True
        if p.is_debit and kind == OptionType.CALL:
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
                done = roll_math.buy_back_only(old_credit, float(paid), word)
                won = done.old_leg_result >= 0
                st.markdown(components._esc(
                    f"**The {old_short} finishes "
                    f"{'up' if won else 'down'} "
                    f"${abs(done.old_leg_result):,.0f}** - sold for "
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
                               account=p.account, option_type=word)
                    st.session_state.pop("trades_rows", None)
                    st.session_state.pop("_priced_positions", None)
                    st.session_state["ql_flash"] = (
                        f"Recorded: ${paid:,.0f} paid to close the call. This "
                        "trade has no call sold against it now - use ➕ Sell a "
                        "call against it when you write the next one.")
                    st.rerun()
            return

        # ---- step 1: the new leg ----------------------------------------
        # Defaults that are USABLE on arrival. The first version opened with
        # strike 0.00 and an expiration a month from today - which, on a leg
        # expiring further out than that, is a date the form's own validation
        # then rejects. Roll OUT from the contract she holds, not from today.
        base = p.expiration or dt.date.today()
        floor = max(base, dt.date.today())
        away = "up and out raises it" if kind == OptionType.CALL else \
               "down and out lowers it"
        _step(1, f"The new {money_word} you sold",
              f"The further-out {money_word} - the one you SOLD in the roll. "
              f"Your SOP rolls out in time and away from the money, so it "
              f"should expire after the one you bought back.")
        cols = st.columns(3 if spread else 2)
        new_exp = cols[0].date_input(
            "Expires", value=max(base + dt.timedelta(days=30), floor),
            min_value=floor, key=f"roll_exp_{kp}_{p.trade_id}",
            format=components.DATE_FMT)
        new_strike = cols[1].number_input(
            "Strike" if not spread else f"Strike you SOLD",
            min_value=0.0, step=1.0,
            value=float(old_strike or 0.0),
            key=f"roll_strike_{kp}_{p.trade_id}",
            help=f"Rolling straight out keeps the same strike; rolling {away}. "
                 f"Prefilled with the strike you hold now.")
        new_long_strike = None
        if spread:
            # The protection moves with it. A spread rolls as ONE order with
            # four legs, and a new short strike with the old long left behind
            # would be a position she does not hold and a risk that is not hers.
            drop = -width if kind == OptionType.PUT else width
            new_long_strike = cols[2].number_input(
                "Strike you BOUGHT", min_value=0.0, step=1.0,
                value=max(float(new_strike) + drop, 0.0),
                key=f"roll_long_{kp}_{p.trade_id}_{new_strike:g}",
                help=f"The protection under it. Prefilled to keep your current "
                     f"{width:g}-wide spread; change it if you rolled to a "
                     f"different width.")

        new_short = (f"{new_strike:g}/{new_long_strike:g} {word} spread"
                     if spread and new_strike and new_long_strike
                     else _leg_short(new_strike, word))
        suggested = _live_leg_mid(provider, p.underlying, new_strike, new_exp,
                                  kind, contracts)
        if spread and new_long_strike:
            bought = _live_leg_mid(provider, p.underlying, new_long_strike,
                                   new_exp, kind, contracts)
            # The spread's own credit: what the short leg pays less what the
            # protection costs. Either leg missing from the chain makes the
            # pair meaningless, so offer nothing rather than half of it.
            suggested = (round(suggested - bought, 2)
                         if suggested is not None and bought is not None else None)

        # ---- step 2: the money ------------------------------------------
        # A roll ORDER fills at one net price and never prints its legs, so
        # asking outright for a leg price sent her hunting for a figure that
        # was not on her statement. One price is what she will have; the
        # two-price way round is folded away for the times she has both.
        _step(2, "What your fill said",
              f"It is the price your order filled at - one line, ending in "
              f"one number, like ...{p.underlying} {new_strike:g}/"
              f"{old_strike:g} {word.upper()} @1.50. In thinkorswim web it is "
              f"under Activity, with your other filled orders."
              if old_strike and new_strike else
              "It is the price your order filled at. In thinkorswim web your "
              "filled orders are under Activity.")
        cash = _fill_price_input(
            "Credit price on your fill", f"roll_cash_{kp}_{p.trade_id}",
            contracts, allow_negative=True,
            help="The single price at the end of your roll line - per share, "
                 "not the dollar total. The app does the x100. If the roll cost "
                 "you money instead of paying you, type a minus in front.")

        # Prefilled from the chain, so the common case is "leave it alone" -
        # unless the chain's answer argues with her fill, in which case her
        # fill wins and the number is worked forward from the buy-back. The app
        # used to fill in an impossible figure and then refuse the roll over
        # it, which left her fixing a number she had never typed.
        pre = roll_math.new_credit_prefill(suggested, cost_now, cash)
        # The key carries the strike, the date and which way this was worked
        # out, because Streamlit ignores value= once a key has been seen and
        # the worked-forward default moves with the fill price above it.
        stamp = "chain" if pre.from_chain else f"net{pre.total:g}"
        new_credit = _fill_price_input(
            f"What the new {new_short} sold for by itself"
            if new_strike else f"What the new {money_word} sold for by itself",
            f"roll_credit_{kp}_{p.trade_id}_{new_strike:g}_{new_exp}_{stamp}",
            contracts, default_total=pre.total,
            help="Not on your fill - a one-order roll only prints the net. The "
                 "app fills it in for you, which is close enough. Change it "
                 "only if your order history happens to list the legs "
                 "separately.")
        if pre.total and not pre.from_chain:
            missing = suggested is None
            theme.note(
                f"**Worked out from your own fill.** "
                + (f"The app could not price the new {money_word} on today's "
                   f"chain, so it used your order instead. "
                   if missing else
                   f"Today's chain priced the new {money_word} BELOW the "
                   f"{_signed(cash)} this roll paid you, which would mean "
                   f"buying back the {old_short} paid you money. Your fill "
                   f"cannot argue with itself, so the app used that instead. ")
                + f"Buying back the {old_short} costs about "
                  f"\\${float(cost_now):,.0f} today, and the roll paid you "
                  f"\\${abs(cash):,.0f} on top. Change it if your order "
                  f"history lists the legs separately.")
        elif pre.total:
            theme.note("**Already filled in for you.** Your fill does not carry "
                       "this number, so the app priced it off today's chain. "
                       "Leave it as it is unless you know better.")
        elif new_strike:
            theme.note("The app could not price that just now, so type what it "
                       "sold for. If you only have the roll's net price, open "
                       "**My fill shows two prices** below instead.")
        figs = roll_math.from_net(old_credit, cash, new_credit, money_word)

        with st.expander("My fill shows two prices, not one",
                         key=f"rolltwo_{kp}_{p.trade_id}"):
            theme.note(f"Use this if you closed the old {money_word} and sold "
                       "the new one as two separate orders - then you have two "
                       "prices rather than one. Some order-history screens also "
                       "list an order's legs one per line. Type both and the app "
                       "works out the net for you.")
            paid_back = _fill_price_input(
                f"Price you paid to buy back the {old_short}",
                f"roll_paid_{kp}_{p.trade_id}", contracts,
                default_total=cost_now,
                help="Prefilled with today's price - change it to your fill.")
            got = _fill_price_input(
                f"Price you got for the new {money_word}",
                f"roll_got_{kp}_{p.trade_id}_{new_strike:g}_{new_exp}",
                contracts, default_total=suggested)
            use_legs = st.checkbox(
                "Use these two prices instead of the one above",
                key=f"roll_uselegs_{kp}_{p.trade_id}")
            if paid_back and got:
                legs = roll_math.from_legs(old_credit, paid_back, got, money_word)
                theme.note(f"Those two make a net of **{_signed(legs.net_credit)}** "
                           "on the order. It should match the price on your fill.")
            if use_legs:
                figs = roll_math.from_legs(old_credit, paid_back, got, money_word)
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
            escape = ("**Closed the call** above does that."
                      if p.is_debit and kind == OptionType.CALL else
                      "Closing it outright is the ✔️ Close button below.")
            theme.note(f"That is a **debit roll** - it cost you "
                       f"\\${abs(cash):,.0f} rather than paying you. You said "
                       f"you would rather close it and start again when the "
                       f"roll will not pay. {escape} Recording it as a roll is "
                       "fine too if that is really what you did.")

        if new_credit and cash:
            _roll_money_panel(
                figs, old_short, new_short, p.roll_income,
                tail=("Your LEAPS is not touched and does not count until you "
                      "sell it." if p.is_debit else
                      "The cash behind this trade stays tied up until it is "
                      "closed or assigned."))
            # The derived buy-back is the sanity check she CAN judge: she knows
            # roughly what getting out cost, even when the new leg's own price
            # came from the app rather than her fill.
            theme.note(f"Read the other way: buying back the {old_short} cost "
                       f"you about **\\${figs.paid_to_close:,.0f}**. If that "
                       f"looks wrong, the new {money_word}'s price in step 2 is "
                       "the one to fix.")
        else:
            theme.note("Fill in the price from your fill above and this is where "
                       "the app shows what the roll did to your money, before "
                       "you save anything.")

        f1, f2 = st.columns([1, 2])
        rolled_on = f1.date_input("Rolled on", value=dt.date.today(),
                                  max_value=dt.date.today(),
                                  key=f"roll_when_{kp}_{p.trade_id}", format=components.DATE_FMT)
        note = f2.text_input("Note (optional)", key=f"roll_note_{kp}_{p.trade_id}")

        current_exp = p.expiration or dt.date.today()
        unchanged = (new_exp == current_exp
                     and float(new_strike or 0) == float(old_strike or 0)
                     and (not spread
                          or float(new_long_strike or 0) == float(old_long.strike)))

        if st.button("Record the roll", type="primary", key=f"rollbtn_{kp}_{p.trade_id}"):
            if not new_strike:
                st.warning(f"Step 1 needs the strike of the {word} you sold.")
            elif spread and not new_long_strike:
                st.warning("Step 1 needs the strike you BOUGHT as well - a "
                           "spread rolls both legs, and without it the app "
                           "would leave your old protection behind.")
            elif not cash:
                st.warning("Step 2 needs the price from your fill - it is the "
                           "money this roll actually made you.")
            elif not new_credit:
                st.warning(f"Step 2 needs what the new {money_word} sold for by "
                           "itself. The app usually fills this in; if it could "
                           "not price it, type it from your Account Trade "
                           "History.")
            elif figs.impossible:
                st.warning(figs.impossible)
            elif new_exp < current_exp:
                st.warning(f"A roll moves the {word} OUT in time, but "
                           f"{components.fmt_date(new_exp)} is BEFORE this "
                           f"position's current expiration "
                           f"({components.fmt_date(current_exp)}). Check the date.")
            elif unchanged:
                # Same strikes, same date: nothing moved, so there is nothing
                # to record. Saved as it stands it would bank the cash against
                # a roll that never happened and leave the position identical.
                st.warning("Nothing has changed - same strike, same expiration. "
                           "Roll it out in time, away from the money, or both.")
            else:
                from src.logging_tools.trade_logger import roll_trade
                roll_trade(p.trade_id, p.underlying, p.strategy_name,
                           float(cash), float(new_strike), new_exp,
                           float(new_credit), note, rolled_on=rolled_on,
                           account=p.account, option_type=word,
                           new_long_strike=(float(new_long_strike)
                                            if new_long_strike else None))
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Roll recorded: {_signed(cash)} banked "
                    f"(${p.roll_income + cash:,.0f} from rolls on this trade so "
                    f"far), now tracking the {new_short} expiring "
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


def _sellable_long_puts(p) -> list:
    """The long puts she could sell off while keeping the short put open.

    Only on a credit shape (a spread or a condor), and only while a short put
    is still there to be left behind - selling the protection off a position
    with nothing short under it is just closing it, which the close form
    already does properly.
    """
    if p.status != "open" or p.assigned_strike or p.is_debit:
        return []
    if not p.short_puts:
        return []
    return [leg for leg in p.legs
            if leg.action == Action.BUY and leg.option_type == OptionType.PUT]


def _after_selling_the_long_put(p, proceeds: float) -> dict:
    """The three numbers that change the moment that fill goes through.

    Worked out here rather than after the fact because they are what makes the
    decision real: the spread's capped loss becomes an obligation to buy the
    shares, and she should see the size of it before she records anything.
    """
    contracts = max(int(p.contracts or 1), 1)
    shares = 100 * contracts
    strike = p.assignment_strike or 0.0
    collected = round(float(p.open_credit or 0.0) + p.banked_income + proceeds, 2)
    return {
        "shares": shares,
        "strike": strike,
        "collected": collected,
        "cash_needed": round(strike * shares, 2),
        "basis": round(strike - collected / shares, 2) if shares else 0.0,
        "was_max_loss": float(p.max_loss or 0.0),
    }


def _assignment_plan_panel(p, price: Optional[float]) -> None:
    """A trade waiting to be assigned on purpose: what it now costs and owes.

    The spread's numbers are gone - there is no width to lose and no 50% to
    take - so the card would otherwise be showing her the arithmetic of a
    position she no longer holds. These are the three she actually needs
    between now and expiration.
    """
    strike = p.assignment_strike
    if not strike:
        return
    shares = 100 * max(int(p.contracts or 1), 1)
    collected = round(float(p.open_credit or 0.0) + p.banked_income, 2)
    basis = p.assignment_basis or 0.0

    rows = [
        _money_line(
            f"If assigned you buy {shares} shares at {strike:g}",
            "the cash (or the margin for it) has to be in the account that day",
            f"-${p.assignment_cash_needed:,.0f}", theme.INK),
        _money_line(
            "Collected on this trade so far",
            "the opening credit, the long put you sold back, and any roll",
            f"+${collected:,.0f}", theme.GREEN),
        _money_line(
            "So the shares would cost you",
            f"{basis:,.2f} a share - premium has already taken "
            f"${strike - basis:,.2f} off the {strike:g} strike",
            f"${basis:,.2f}", theme.INK, strong=True),
    ]
    st.markdown(
        f"<div style='background:{theme.TILE};border:1px solid {theme.BORDER};"
        f"border-radius:12px;padding:12px 16px;margin:6px 0 10px;'>"
        f"<div style='font-weight:800;color:{theme.INK};font-size:1.02rem;"
        f"margin-bottom:4px;'>🎯 Waiting to be assigned on "
        f"{_h_esc(p.underlying)}</div>" + "".join(rows) + "</div>",
        unsafe_allow_html=True)

    if price is not None and strike:
        if price <= strike:
            theme.note(
                f"**{p.underlying} is at \\${price:,.2f}, below your {strike:g} "
                "put**, so as things stand the shares are coming to you. That is "
                "the plan - just make sure the cash is there, and record it with "
                "**🎡 I was assigned** as soon as it happens (usually the weekend "
                "after expiration) so every dollar you have collected keeps "
                "counting towards what the shares cost.")
        else:
            gap = (price - strike) / price * 100
            theme.note(
                f"**{p.underlying} is at \\${price:,.2f}, {gap:.1f}% above your "
                f"{strike:g} put.** If it stays here the put expires, no shares "
                f"arrive and you simply keep the \\${collected:,.0f} you have "
                "collected. That is a win too - the shares were the plan, not "
                "the requirement.")

    if not p.has_long_put:
        theme.note(
            f"**Nothing is bought underneath this any more.** Your loss no "
            f"longer stops at the width of the spread: below \\${basis:,.2f} a "
            f"share it keeps going, \\${shares:,.0f} for every further dollar "
            f"{p.underlying} falls. That is the trade you chose when you sold "
            "the long put - it is worth knowing the shape of it.")


def _sell_long_leg_form(p, provider, kp: str = "detail") -> None:
    """Record selling the long put off a credit spread, short put left open.

    Her third way out, and the one the app had no room for. It offered close
    (both legs) or roll (both legs), so a decision to keep the short put and
    take the shares had to be logged as a lie - close the spread and open a
    fresh cash-secured put - which threw away the credit already collected and
    with it the real cost basis of the shares.

    This keeps it ONE trade: the spread, the leg she sold, the assignment, and
    every covered call afterwards, so the wheel that follows knows what those
    shares actually cost.
    """
    import datetime as dt

    longs = _sellable_long_puts(p)
    if not longs:
        return

    with st.expander("✂️ Sell the long put, keep the short one (for assignment)",
                     key=f"legwrap_{kp}_{p.trade_id}"):
        theme.note(
            "For when you decide not to close and not to roll: you sell the "
            "long put back, bank what it is worth, and leave the short put "
            "alone so it can assign you the shares. Do it in thinkorswim "
            "first, then record the fill here.")

        if len(longs) > 1:
            labels = [f"the {leg.strike:g} put" for leg in longs]
            choice = st.selectbox("Which long put did you sell?", labels,
                                  key=f"legpick_{kp}_{p.trade_id}")
            leg = longs[labels.index(choice)]
        else:
            leg = longs[0]
            short = (f"{p.assignment_strike:g} short put"
                     if p.assignment_strike else "short put")
            st.markdown(components._esc(
                f"Selling back **the {leg.strike:g} put** - the protection "
                f"under your {short}."))

        c1, c2 = st.columns(2)
        sold_on = c1.date_input("Sold on", value=dt.date.today(),
                                max_value=dt.date.today(),
                                key=f"legwhen_{kp}_{p.trade_id}",
                                format=components.DATE_FMT)
        keep_for_assignment = c2.radio(
            "And the short put?",
            ["Leave it - I want to be assigned", "Keep it, I still mean to close it"],
            key=f"legwhy_{kp}_{p.trade_id}",
            help="This is the bit nothing else in the log records, and it "
                 "decides every piece of advice from here. Left for "
                 "assignment, the app stops running the 50% target and the "
                 "21-day clock at you and starts tracking what the shares "
                 "will cost.") == "Leave it - I want to be assigned"

        suggested = _live_leg_mid(provider, p.underlying, leg.strike,
                                  p.leg_expiration(leg) or p.expiration,
                                  OptionType.PUT, int(p.contracts or 1))
        proceeds = _fill_price_input(
            "Price you SOLD the long put for",
            f"legcash_{kp}_{p.trade_id}", int(p.contracts or 1),
            default_total=suggested,
            # A long put bought for protection is routinely worth 15.00+ a
            # share by the time she is doing this, so the typed-a-total guard
            # would cry wolf on exactly the fills this form is for.
            total_hint_above=None,
            help="What the sale paid you, per share - the app does the x100. "
                 "Prefilled from today's chain when it could be priced.")
        if suggested:
            theme.note("**Prefilled from today's chain** for that contract. "
                       "Change it if your fill said otherwise.")

        after = _after_selling_the_long_put(p, float(proceeds))
        if proceeds and after["strike"]:
            st.markdown(components._esc(
                f"Recording this banks ${proceeds:,.0f} today. If the "
                f"{after['strike']:g} put then assigns you, you buy "
                f"{after['shares']} shares for ${after['cash_needed']:,.0f} - "
                f"and with the ${after['collected']:,.0f} this trade has "
                f"collected, they cost you ${after['basis']:,.2f} a share."))
            if keep_for_assignment:
                st.warning(components._esc(
                    f"Have ${after['cash_needed']:,.0f} ready. Without the long "
                    f"put your risk is no longer the ${after['was_max_loss']:,.0f} "
                    f"this spread could lose - below ${after['basis']:,.2f} a "
                    f"share the loss keeps going."))
        note = st.text_input("Note (optional)", key=f"legnote_{kp}_{p.trade_id}")

        if st.button("Record the long put I sold", type="primary",
                     key=f"legbtn_{kp}_{p.trade_id}"):
            if not proceeds:
                st.warning("Type what the sale paid you - it is on your TOS fill.")
            else:
                from src.logging_tools.trade_logger import close_leg
                close_leg(p.trade_id, p.underlying, p.strategy_name,
                          cash=float(proceeds), strike=float(leg.strike),
                          option_type="put", side="buy",
                          for_assignment=keep_for_assignment,
                          note=note, closed_on=sold_on, account=p.account)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Recorded: the {leg.strike:g} put sold for "
                    f"${proceeds:,.0f}. "
                    + (f"Your {after['strike']:g} put is on its own now - the "
                       f"app is watching for assignment and will want "
                       f"${after['cash_needed']:,.0f} of cash ready."
                       if keep_for_assignment else
                       "The short put is still tracked against your exit rules.")
                )
                st.rerun()


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

    collected = round(float(p.open_credit or 0.0) + p.banked_income, 2)
    with st.expander("🎡 I was assigned - I own the shares now",
                     key=f"asg_{kp}_{p.trade_id}"):
        theme.note(
            f"On a wheel this is the plan, not a mistake. Recording it here "
            f"keeps everything on ONE trade, so the \\${collected:,.0f} you "
            f"already collected still counts towards what the shares cost you. "
            f"The app then asks you to sell calls against them."
            + (" That includes what the long put sold for when you took it off "
               "this spread - it came off the price of these shares too."
               if p.leg_closes else ""))
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
            basis = at_strike - (collected / shares)
            st.markdown(components._esc(
                f"You will own **{shares} shares** at ${at_strike:g}, costing "
                f"**${at_strike * shares:,.0f}**. With the ${collected:,.0f} "
                f"already collected on this trade, your cost basis starts at "
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
        # The close banks the capital result. Roll credits - and anything a
        # leg sold off already banked - landed on their own days, so they are
        # not counted again here.
        realized = p.open_cash + close_cash
        total = realized + p.banked_income
        if p.is_debit:
            st.markdown(components._esc(
                f"Result: **${total:,.0f}** "
                f"({'profit' if total >= 0 else 'loss'}) - "
                f"${-p.open_cash:,.0f} out, ${p.roll_income:,.0f} banked "
                f"from rolls, ${close_cash:,.0f} back today."))
        elif p.banked_income:
            st.markdown(components._esc(
                f"Result: **${total:,.0f}** "
                f"({'profit' if total >= 0 else 'loss'}) - "
                f"${realized:,.0f} on this close, plus the "
                f"${p.banked_income:,.0f} already banked on this trade "
                "(rolls, and any leg you sold off)."))
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
