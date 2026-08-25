"""Quick Log - recording a trade she already placed in thinkorswim.

The form collects the fill, then stages a draft in session state and renders a
preview OUTSIDE the expander so she reads back what she typed before it is
written to the log. Saving is the second click, never the first.
"""

from __future__ import annotations

import streamlit as st

from src.engine.config_loader import (
    allowed_underlyings_for,
    default_strategy_key,
)
from src.engine.models import Action, CheckStatus, OptionType, Trade
from src.engine.validator import validate_trade
from ui import components, theme
from ui.trades.account import _account_choice
from ui.trades.widgets import _fill_price_input, _signed, money


def _quick_log_form(settings, strategies, provider) -> None:
    """Record a trade she ALREADY placed in thinkorswim, in under a minute:
    strategy, strikes, expiration, contracts, and the credit on her fill.
    The chain fills in deltas when it can; the SOP check informs, never blocks."""
    import datetime as dt

    from src.engine import quick_log

    # Keyed, so it holds its own state through a rerun. It used to be forced
    # open whenever a draft was waiting, because the rerun after "Check it"
    # would otherwise collapse the expander and hide the preview she was meant
    # to read. `expanded` still wins on that one case - a draft she cannot see
    # is worse than an expander that opens itself.
    with st.expander("➕ Quick Log - a trade you already placed in thinkorswim",
                     key="ql_wrap",
                     expanded=bool(st.session_state.get("ql_draft"))):
        theme.note("Place the trade in TOS first, then write it down here. Type only "
                   "what is on your fill - the app fills in the market details and "
                   "starts watching your exit rules for it.")

        keys = list(strategies.keys())
        # Opens on the strategy she actually trades most, from
        # config/settings.yaml `defaults.strategy`, without reordering the list.
        top = st.columns([3, 2])
        strategy_key = top[0].selectbox(
            "Strategy", keys, key="ql_strategy",
            index=keys.index(default_strategy_key(settings, keys)),
            format_func=lambda k: strategies[k]["name"])
        strat = strategies[strategy_key]
        if st.session_state.get("_prev_ql_strategy") != strategy_key:
            st.session_state["_prev_ql_strategy"] = strategy_key
            st.session_state.pop("ql_draft", None)   # a draft for another strategy

        allowed = allowed_underlyings_for(strategy_key)
        default_i = allowed.index("SPX") if "SPX" in allowed else 0
        # accept_new_options because this records a trade you ALREADY placed in
        # thinkorswim. The list only covers the S&P 500 and Nasdaq-100, so
        # without it a real fill on any other name simply could not be logged.
        underlying = top[1].selectbox("Underlying", allowed, index=default_i,
                                      key=f"ql_u_{strategy_key}",
                                      accept_new_options=True,
                                      help="Type to search, or type any other ticker to add it.")
        if underlying:
            underlying = underlying.strip().upper()

        basis = str(strat.get("sizing", {}).get("max_loss_basis", "vertical_width"))
        has_far_leg = basis in ("debit", "shares_plus_protection", "ratio_risk")
        # The LEAPS long call is the one strategy here she BUYS outright, so it
        # gets its own shape of form: a price PAID rather than a credit
        # collected, and room for the put(s) sold to part-pay for it. Without
        # this the only box on offer was "credit price on your fill", and a
        # trade that cost her money could only be logged as one that paid her.
        is_bought = basis == "long_premium"
        # How far out this strategy's expiration normally sits, so the date box
        # opens near the right place. A LEAPS put is a year or more out like the
        # bought call is, and defaulting it to 45 days just means retyping.
        far_dated = int(strat.get("entry", {}).get("dte_min", 0)) >= 300
        default_dte = (int(strat["entry"].get("dte_target", 400)) if far_dated
                       else 400 if is_bought else 45)
        today = dt.date.today()

        with st.form("ql_form"):
            # Contracts sits up here rather than down beside the credit because
            # every money box below is now a PRICE, and a price only becomes
            # dollars once the app knows how many contracts it applies to.
            d1, d2, d3 = st.columns([2, 2, 1])
            expiration = d1.date_input(
                "Expiration date (from your TOS fill)"
                if not has_far_leg else "Short call expiration (the near one)",
                # A LEAPS is a year or more out by definition, so opening its
                # form on a 45-day default just means retyping the date.
                value=today + dt.timedelta(days=default_dte),
                min_value=today,
                key=f"ql_exp_{strategy_key}", format=components.DATE_FMT)
            opened_on = d2.date_input(
                "Opened on", value=today, max_value=today,
                help="Change this only if you placed the trade on an earlier day.",
                key=f"ql_opened_{strategy_key}", format=components.DATE_FMT)
            contracts = d3.number_input("Contracts", min_value=1, max_value=50,
                                        value=1, step=1,
                                        key=f"ql_contracts_{strategy_key}")

            far_exp = None
            leaps_cost = None
            share_price = None
            protection_cost = None
            if basis == "debit":
                f1, f2 = st.columns(2)
                far_exp = f1.date_input(
                    "LEAPS expiration (the far-dated call you BOUGHT)",
                    value=today + dt.timedelta(days=365), min_value=today,
                    key=f"ql_farexp_{strategy_key}", format=components.DATE_FMT)
                with f2:
                    leaps_cost = _fill_price_input(
                        "Price you paid for the LEAPS",
                        f"ql_leaps_{strategy_key}", contracts, live_echo=False,
                        # A LEAPS deep in the money genuinely trades above 100 a
                        # share, so the typed-a-total guard would cry wolf here.
                        total_hint_above=None,
                        help="The fill price per share - a 40.00 fill on 1 "
                             "contract is $4,000. This is your real money at "
                             "risk, so the app needs it to tell you what the "
                             "trade actually made.")
            elif has_far_leg:
                f1, f2 = st.columns(2)
                far_exp = f1.date_input(
                    "Protective put expiration (the far-dated one)",
                    value=today + dt.timedelta(days=365), min_value=today,
                    key=f"ql_farexp_{strategy_key}", format=components.DATE_FMT)
                share_price = f2.number_input(
                    "Share price when you bought the 100 shares ($)",
                    min_value=0.0, step=1.0, key=f"ql_shares_{strategy_key}")
                protection_cost = _fill_price_input(
                    "Price the put side cost you (net)",
                    f"ql_prot_{strategy_key}", contracts, live_echo=False,
                    allow_negative=True, total_hint_above=None,
                    help="Model 1: what the long put cost. Model 2: the net "
                         "debit of the put spread. Model 3: often near zero - "
                         "and if the ratio paid you a credit, type a minus in "
                         "front. Leave at 0 only if it really was free.")

            strikes: dict[str, float] = {}
            credit_total = 0.0
            call_cost = 0.0
            put_credit = 0.0
            n_puts = 0

            if is_bought:
                theme.note("You BOUGHT this one, so there is no credit to type - "
                           "the boxes below ask what it cost you. If you also sold "
                           "put(s) at the same expiration to help pay for the call, "
                           "put them in too; leave the count at 0 if you did not.")
                b1, b2 = st.columns(2)
                strikes["long_call_leaps"] = b1.number_input(
                    "Call strike (the call you BOUGHT)", min_value=0.0, step=1.0,
                    key=f"ql_strike_{strategy_key}_long_call_leaps")
                with b2:
                    call_cost = _fill_price_input(
                        "Price you PAID for the call", f"ql_callcost_{strategy_key}",
                        contracts, live_echo=False,
                        # A LEAPS deep in the money genuinely trades above 100 a
                        # share, so the typed-a-total guard would cry wolf here.
                        total_hint_above=None,
                        help="The fill price per share - a 21.15 fill on 1 "
                             "contract is $2,115. This is the money at risk, so "
                             "every number the app shows you afterwards depends "
                             "on it.")
                # All three boxes are always drawn, never revealed by the count.
                # A form holds its values until submit, so a box that appears
                # only once the count is above zero would not appear until after
                # she pressed "Check it" - by which point the app is already
                # telling her something is missing.
                p1, p2, p3 = st.columns([1, 1, 1])
                n_puts = int(p1.number_input(
                    "Puts you SOLD (0 if none)", min_value=0, max_value=20,
                    value=0, step=1, key=f"ql_fp_n_{strategy_key}",
                    help="Per contract of the whole trade. Your SOP allows one "
                         "put per call bought, two at a push - it warns above "
                         "one and fails above two, but a trade you have already "
                         "placed still gets logged either way."))
                put_strike = p2.number_input(
                    "Put strike (the puts you SOLD)", min_value=0.0, step=1.0,
                    key=f"ql_fp_k_{strategy_key}")
                with p3:
                    put_credit = _fill_price_input(
                        "Price you GOT for each put", f"ql_fp_credit_{strategy_key}",
                        contracts * max(n_puts, 1), live_echo=False,
                        total_hint_above=None,
                        help="Per share, for ONE put - the app multiplies by 100 "
                             "and by how many you sold.")
                if n_puts:
                    strikes["financing_put"] = put_strike
                else:
                    put_credit = 0.0
            else:
                leg_defs = strat.get("legs", [])
                cols = st.columns(min(len(leg_defs), 4) or 1)
                for i, leg_def in enumerate(leg_defs):
                    role = str(leg_def["role"])
                    verb = "SOLD" if leg_def["action"] == "sell" else "BOUGHT"
                    label = (f"{role.replace('_', ' ').capitalize()} strike "
                             f"(you {verb} this {leg_def['option_type']})")
                    strikes[role] = cols[i % len(cols)].number_input(
                        label, min_value=0.0, step=1.0,
                        key=f"ql_strike_{strategy_key}_{role}")

                credit_label = ("Credit price on your fill"
                                if basis not in ("debit", "shares_plus_protection",
                                                 "ratio_risk")
                                else "Credit price for the call you SOLD")
                credit_total = _fill_price_input(
                    credit_label, f"ql_credit_{strategy_key}", contracts,
                    live_echo=False,
                    help="The price on your TOS fill, per share. On a spread that "
                         "is the one net price for the whole order.")
            note = st.text_input("Note (optional)", key=f"ql_note_{strategy_key}")

            submitted = st.form_submit_button("Check it", type="primary")

    # Everything below renders OUTSIDE the expander, so the result of
    # "Check it" (a warning or the preview card) is visible even after
    # Streamlit collapses the expander on the rerun.
    if submitted:
        if any(v <= 0 for v in strikes.values()):
            st.warning("Almost - type every strike first, one of them is still 0. "
                       "Open ➕ Quick Log above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif is_bought and call_cost <= 0:
            st.warning("Almost - type what you PAID for the call (it is on your TOS "
                       "fill). That is the whole cost of this trade, and without it "
                       "the app cannot tell you what it made. Open ➕ Quick Log "
                       "above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif is_bought and n_puts and put_credit <= 0:
            st.warning(f"Almost - you said you sold {n_puts} put(s), so type what "
                       "they paid you. Set the count back to 0 if you did not sell "
                       "any. Open ➕ Quick Log above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif not is_bought and credit_total <= 0:
            st.warning("Almost - type the credit you collected (it is on your TOS "
                       "fill). Open ➕ Quick Log above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif basis == "debit" and not leaps_cost:
            # Without it the position looks like a tiny credit trade and every
            # number downstream - result, return, buying power - comes out wrong.
            st.warning("Almost - type what you paid for the LEAPS. That is the "
                       "money actually at risk in a PMCC, and without it the app "
                       "cannot tell you what the trade made. Open ➕ Quick Log "
                       "above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif has_far_leg and basis != "debit" and not share_price:
            st.warning("Almost - type the share price you paid. That is most of "
                       "the money in a covered call, and the app needs it to "
                       "track the trade's result. Open ➕ Quick Log above to "
                       "fill it in.")
            st.session_state.pop("ql_draft", None)
        else:
            dte = max((expiration - opened_on).days, 0)
            leaps_dte = (max((far_exp - opened_on).days, 0)
                         if far_exp is not None else None)
            legs = quick_log.legs_from_strategy(strat, strikes, dte,
                                                leaps_dte=leaps_dte,
                                                financing_puts=n_puts)
            notes: list[str] = []
            underlying_price = None
            try:
                chain = provider.get_chain(underlying,
                                           dte_min=max(dte - 4, 0),
                                           dte_max=dte + 4)
                underlying_price = chain.underlying_price
                legs, fill_notes = quick_log.fill_from_chain(
                    legs, chain, expiration.isoformat(),
                    leaps_expiration_iso=(far_exp.isoformat()
                                          if far_exp else None))
                notes.extend(fill_notes)
            except Exception:
                notes.append("Live option prices were not available just now - "
                             "saved without deltas. Tracking still works from "
                             "your credit and strikes.")
            if is_bought:
                # Her own fills beat the chain's mids on this one: she typed the
                # two sides separately, so they are exact. The SOP checks read
                # these premiums to say how much of the call the puts actually
                # funded and what the puts commit her to.
                per_share = {"long_call_leaps": call_cost / (100 * int(contracts))}
                if n_puts:
                    per_share["financing_put"] = (
                        put_credit / (100 * int(contracts) * n_puts))
                quick_log.apply_fill_prices(legs, per_share)
            trade = Trade(strategy_key=strategy_key, underlying=underlying,
                          contracts=int(contracts), legs=legs,
                          underlying_price=underlying_price or share_price)
            sizing = quick_log.sizing_from_fill(
                trade, strat,
                put_credit if is_bought else float(credit_total),
                leaps_cost_total=(call_cost if is_bought else leaps_cost),
                share_price=share_price,
                protection_cost_total=protection_cost)
            passed = True
            broke: list[str] = []
            try:
                report = validate_trade(
                    trade,
                    existing_month_bp=st.session_state.get("month_bp_used", 0.0))
                passed = report.passed
                # Keep WHICH rules, not just whether. This is a trade she has
                # already placed, so the checklist cannot stop her - but "you
                # broke a rule" with no name teaches nothing, and learning
                # which rule is the entire point of logging it here.
                broke = [f"{r.name} - {r.message}" for r in report.results
                         if r.status in (CheckStatus.FAIL, CheckStatus.WARN)]
            except Exception:
                notes.append("The SOP check could not run just now - the trade "
                             "still gets logged and tracked.")
            # What the prices she typed came to in dollars. The boxes in the
            # form cannot show this as she types (a form holds its values until
            # submit), so the confirmation belongs here, on the card she reads
            # before saving.
            # (name, dollars, how many contracts that price was multiplied by) -
            # the put leg is priced per PUT, so a 3-put fill divides by three
            # times the contract count and not by the contract count alone.
            n_con = int(contracts)
            put_strike_total = sum(l.strike * l.quantity for l in trade.legs
                                   if l.action == Action.SELL
                                   and l.option_type == OptionType.PUT)
            if is_bought:
                typed = [("The call", -float(call_cost), n_con)]
                if n_puts:
                    typed.append((f"{n_puts} put(s)", float(put_credit),
                                  n_con * n_puts))
            else:
                typed = [("Credit", float(credit_total), n_con)]
                if leaps_cost:
                    typed.append(("LEAPS", -float(leaps_cost), n_con))
                if protection_cost:
                    typed.append(("Put side", -float(protection_cost), n_con))
            st.session_state["ql_draft"] = {
                "trade": trade, "strat_name": strat["name"], "sizing": sizing,
                "passed": passed, "broke": broke, "notes": notes, "note": note,
                "opened_on": opened_on, "expiration": expiration, "dte": dte,
                "typed": typed, "contracts": n_con, "bought": is_bought,
                "call_cost": float(call_cost), "put_credit": float(put_credit),
                "collateral": round(put_strike_total * 100 * n_con, 2),
                "put_shares": 100 * n_puts * n_con,
            }

    draft = st.session_state.get("ql_draft")
    if draft:
        with st.container(border=True):
            p_trade, p_size = draft["trade"], draft["sizing"]
            theme.note(f"**Ready to save: {p_trade.underlying} · "
                       f"{draft['strat_name']}** · {p_trade.contracts} "
                       f"contract(s) · opened {components.fmt_date(draft['opened_on'])} · "
                       f"expires {components.fmt_date(draft['expiration'])} "
                       f"({draft['dte']} days)")
            open_cash = float(p_size.get("open_cash", p_size["credit"]))
            collateral = float(draft.get("collateral") or 0.0)
            if draft.get("bought"):
                # Nothing here is a credit: the call is the position and the
                # puts, if any, are a discount on it that comes with an
                # obligation. Labelling either of them "credit" is what the
                # generic card below would have done.
                m = st.columns(4 if collateral else 3)
                m[0].metric("The call cost you", money(draft.get("call_cost", 0.0)),
                            help="What you paid for the LEAPS. This is the money "
                                 "at risk and what every return is measured on.")
                if collateral:
                    m[1].metric("The puts paid you", money(draft.get("put_credit", 0.0)),
                                help="Off the price of the call - not income. You "
                                     "are still net out of pocket on the trade.")
                    m[2].metric("Cash out today", money(-open_cash),
                                help="The call, less what the puts paid.")
                    m[3].metric("Most you can lose", money(p_size["max_loss"]),
                                help="The call goes to zero AND the puts land you "
                                     "the shares at their strike. That is what "
                                     "the collateral below is standing behind.")
                else:
                    m[1].metric("Most you can lose", money(p_size["max_loss"]),
                                help="A bought call can expire worthless, and "
                                     "that is the whole of it - no more, no less.")
                    m[2].metric("Buying power", money(p_size["buying_power"]),
                                help="Zero: a bought option is paid for in cash, "
                                     "so the broker holds nothing against it.")
                if collateral:
                    theme.note(
                        f"Those put(s) freeze **\\${collateral:,.0f}** of cash "
                        f"until expiration - the promise to buy "
                        f"{int(draft.get('put_shares') or 0):,} shares. That, not "
                        f"the \\${-open_cash:,.0f} that left today, is what this "
                        "trade actually ties up.")
            elif open_cash < 0:
                # A PMCC or covered call takes money OUT to open. Showing only
                # the call credit here is what made a multi-thousand-dollar
                # position look like a trade worth a couple hundred.
                m = st.columns(4)
                m[0].metric("Call credit", money(p_size["credit"]),
                            help="What the short call paid you. Your 50% profit "
                                 "target measures against this - not against the "
                                 "whole position.")
                m[1].metric("Cash out today", money(-open_cash),
                            help="What actually left your account: the long side "
                                 "you bought, minus the call credit. Closing the "
                                 "trade pays this back, plus or minus your result.")
                m[2].metric("Max loss", money(p_size["max_loss"]))
                m[3].metric("Buying power", money(p_size["buying_power"]))
            else:
                m = st.columns(3)
                m[0].metric("Credit", money(p_size["credit"]))
                m[1].metric("Max loss", money(p_size["max_loss"]))
                m[2].metric("Buying power", money(p_size["buying_power"]))
            # Read back the prices as dollars, so a price typed where she used
            # to type a total (or the other way round) is caught here rather
            # than discovered weeks later in a month report.
            typed = draft.get("typed") or []
            if typed:
                bits = [f"{name} **{_signed(amt)}** ({abs(amt) / (100 * max(units, 1)):,.2f} "
                        f"x 100" + (f" x {units}" if units > 1 else "") + ")"
                        for name, amt, units in typed]
                theme.note("What you typed comes to: " + " · ".join(bits)
                           + ". Wrong by a factor of 100? Reopen Quick Log and "
                             "type the price, not the dollar total.")
            broke = draft.get("broke") or []
            if draft["passed"] and not broke:
                st.markdown(theme.chip("SOP check: passed", "green"),
                            unsafe_allow_html=True)
            else:
                tone = "amber" if draft["passed"] else "red"
                headline = ("Worth noting for next time - logged anyway, since it is "
                            "already placed" if draft["passed"] else
                            "Outside your SOP rules - logged anyway, since it is "
                            "already placed")
                st.markdown(theme.chip(headline, tone), unsafe_allow_html=True)
                theme.note("**What your own rules say about this one:**")
                for line in broke:
                    theme.note("• " + line)
                theme.note("Nothing to do about it now - the trade is placed. This is "
                           "here so the next one starts cleaner.")
            for n in draft["notes"]:
                theme.note(n)
            # She is logging a trade that is already on her TOS screen, so the
            # real BP Effect is right there to copy.
            components.bp_effect_input(draft["sizing"], "ql")
            # Defaulted from the date the trade was PLACED, not today, so
            # back-logging an older paper trade does not land it in the real book.
            account = _account_choice(settings, "ql", draft["opened_on"])
            c1, c2 = st.columns([1, 1])
            if c1.button("✅ Save to my log", type="primary", key="ql_save"):
                from src.logging_tools.trade_logger import log_trade
                dest, live, trade_id = log_trade(
                    draft["trade"], draft["strat_name"], draft["sizing"],
                    draft["passed"], draft["note"],
                    opened_on=draft["opened_on"],
                    expiration_on=draft["expiration"],
                    account=account)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state.pop("ql_draft", None)
                st.session_state["ql_flash"] = (
                    "Saved. It now shows in your open trades below"
                    + (" and in your Google Sheet." if live
                       else " (saved on this device - connect your Google "
                            "Sheet in ⚙️ Settings to sync it everywhere)."))
                st.rerun()
            if c2.button("Never mind - discard this draft", key="ql_discard"):
                st.session_state.pop("ql_draft", None)
                st.rerun()
