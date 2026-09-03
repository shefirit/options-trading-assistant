"""The Candidate check - is this name worth a credit spread today, and which side?

Reads top to bottom the way the decision is actually made: what the volatility
source is, then the verdict for each side, then the layers that produced it,
then the way through to the scanner. Everything is on the page - no sidebar, no
steps to unlock, because she runs this on a phone.

The layers are shown even when the verdict is obvious. A verdict she cannot
audit is a verdict she has to take on trust, and the whole point of the tool is
that she can see which measurement moved it.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from src.data import barchart
from src.engine.candidate import CALL, PUT
from src.engine.config_loader import (
    default_spread_width,
    entry_dte_window,
    preferred_entry_dte,
)
from ui import theme

# Status -> what she sees. The icon is never the only signal; the wording of
# each layer's read always says the same thing in words.
_ICON = {"good": "✅", "ok": "➖", "watch": "⚠️", "bad": "⛔", "unknown": "❔"}
_TONE = {"green": "good", "amber": "watch", "red": "bad"}

BARCHART_KEY = "barchart_iv_import"
MANUAL_KEY = "manual_iv_rank"
IV_RANK_URL = "https://www.barchart.com/options/iv-rank-percentile"


def _manual_ranks() -> dict:
    return st.session_state.setdefault(MANUAL_KEY, {})


def _import() -> Optional[barchart.BarchartImport]:
    return st.session_state.get(BARCHART_KEY)


# ------------------------------------------------------------ volatility source
def _volatility_source_panel(sym: str, vol) -> None:
    """Where the IV Rank came from, and how to give it a better one."""
    imp = _import()
    tone = "red" if not vol.known else ("amber" if vol.is_proxy else "green")
    label = vol.source or "nothing available"
    chips = [theme.chip(f"IV Rank source: {label}", tone)]
    if imp is not None and imp.ok:
        age = imp.age_days()
        stale = age is not None and age > 3
        chips.append(theme.chip(
            f"Barchart file: {len(imp.rows)} symbols"
            + (f", {age}d old" if age is not None else ""),
            "amber" if stale else "neutral"))
    st.markdown(" ".join(chips), unsafe_allow_html=True)

    if vol.note:
        theme.note(vol.note)

    with st.expander("Import IV Rank from Barchart, or type one in", expanded=False):
        theme.note(
            "Your app cannot call Barchart directly - their data API is a separate "
            "paid product, and their website needs a logged-in session. What your "
            "subscription does give you is the download button. Open "
            f"**{IV_RANK_URL}**, set the dropdown to the list you want, click "
            "**download**, then drop the file here. It fills IV Rank, IV Percentile, "
            "30-day realized volatility and the earnings date for every symbol in it."
        )
        upload = st.file_uploader("Barchart IV Rank export (.csv)", type=["csv"],
                                  key="barchart_uploader")
        if upload is not None:
            try:
                text = upload.getvalue().decode("utf-8-sig", errors="replace")
            except Exception:
                text = ""
            result = barchart.parse(text, source=upload.name)
            if result.ok:
                st.session_state[BARCHART_KEY] = result
                st.success(
                    f"Loaded {len(result.rows)} symbols from {upload.name}"
                    + (f", dated {result.as_of:%b %d}" if result.as_of else "")
                    + ". Every symbol in that file now grades on a real IV Rank.")
            else:
                st.error(result.error)

        if imp is not None and imp.ok:
            if st.button("Clear the imported file", key="barchart_clear"):
                st.session_state.pop(BARCHART_KEY, None)
                st.rerun()

        st.divider()
        theme.note(
            f"No file to hand? Read {sym}'s IV Rank off Barchart and type it here. "
            "It applies to this symbol only, and a file always beats it.")
        current = _manual_ranks().get(sym)
        typed = st.number_input(
            f"{sym} IV Rank (0-100)", min_value=0.0, max_value=100.0,
            value=float(current) if current is not None else 0.0, step=1.0,
            key=f"manual_iv_{sym}",
            help="0 to 100. Where implied volatility sits between its lowest and "
                 "highest of the past year.")
        cols = st.columns(2)
        if cols[0].button("Use this rank", key=f"manual_set_{sym}"):
            _manual_ranks()[sym] = float(typed)
            st.rerun()
        if current is not None and cols[1].button("Forget it", key=f"manual_clr_{sym}"):
            _manual_ranks().pop(sym, None)
            st.rerun()


# ------------------------------------------------------------------- verdicts
def _side_card(side) -> str:
    fit = side.fit_pct
    sub = (f"{side.score:+.1f} of a possible {side.max_score:.1f}"
           + (f"  ({fit}% fit)" if fit is not None else ""))
    if side.blocked:
        sub = "Blocked - see below"
    return theme.kpi_card(side.name, side.verdict, sub,
                          tone=_TONE.get(side.tone, "neutral"))


def _render_verdicts(report) -> None:
    theme.kpi_row([_side_card(report.put_side), _side_card(report.call_side)])
    theme.note(report.summary)

    blockers = []
    for side in (report.put_side, report.call_side):
        for b in side.blockers:
            if b not in blockers:
                blockers.append(b)
    if blockers:
        st.error("**Blockers** - these are not scores to be outweighed. Each one is "
                 "a reason on its own.")
        for b in blockers:
            st.markdown(f"- {b}")


def _render_layers(report) -> None:
    theme.section("What each layer found", "THE EVIDENCE")
    theme.note(
        "Every layer measures one thing and pushes points toward the put side, the "
        "call side, or neither. Nothing here predicts - each one describes what has "
        "already happened, or what options are charging right now.")
    for lay in report.layers:
        icon = _ICON.get(lay.status, "➖")
        with st.container(border=True):
            pts = []
            if lay.put_points:
                pts.append(f"put {lay.put_points:+.2g}")
            if lay.call_points:
                pts.append(f"call {lay.call_points:+.2g}")
            tail = f"  ·  {', '.join(pts)}" if pts else ""
            st.markdown(f"{icon}  **{lay.label}** - {lay.value}{tail}")
            theme.note(lay.read)

    if report.data_gaps:
        theme.note(
            "**Not graded:** " + ", ".join(report.data_gaps) + ". A missing "
            "measurement is not a pass and not a fail - it lowers how much of the "
            "picture the score covers, which is why the cards show what was "
            "actually gradeable rather than a flat percentage.")


# ---------------------------------------------------------------------- entry
def render(sym: str, kind: str, provider, settings, strategies) -> None:
    theme.section("Is this a credit-spread candidate?", "CANDIDATE CHECK")

    if not sym:
        theme.note("Pick an index, ETF, or stock at the top of this tab and its "
                   "candidate check appears here.")
        return
    if not provider.is_real:
        st.info("The candidate check needs real market data - connect to the "
                "internet first.")
        return

    pcs = strategies["put_credit_spread"]
    ccs = strategies["call_credit_spread"]
    entry = pcs.get("entry", {})
    dte_lo, dte_hi = entry_dte_window(pcs, sym)
    market = (settings.get("market_read") or {})

    theme.note(
        f"Graded for a **{default_spread_width(sym):g}-point** spread at "
        f"**{dte_lo}-{dte_hi} days** to expiration, selling the put at "
        f"{float(entry.get('short_leg_delta_max', 0.25)):.2f} delta and the call at "
        f"{float(ccs.get('entry', {}).get('short_leg_delta_max', 0.10)):.2f} delta - "
        "your own numbers, read from config, not typed in here.")

    with st.spinner(f"Reading {sym} across every layer..."):
        report, vol = provider.get_candidate(
            sym, kind,
            width=default_spread_width(sym),
            put_delta=float(entry.get("short_leg_delta_max", 0.25)),
            call_delta=float(ccs.get("entry", {}).get("short_leg_delta_max", 0.10)),
            dte_target=preferred_entry_dte(pcs, sym) or 45,
            dte_lo=dte_lo, dte_hi=dte_hi,
            min_credit_pct=float(entry.get("min_credit_pct_of_width", 0.06)),
            vix_stop=float(market.get("vix_stop", 28.0)),
            vix_zone=(float(market.get("vix_zone_low", 13.0)),
                      float(market.get("vix_zone_high", 25.0))),
            barchart_import=_import(),
            manual_rank=_manual_ranks().get(sym),
        )

    _volatility_source_panel(sym, vol)
    st.divider()
    _render_verdicts(report)
    _render_layers(report)

    _handoff(report, sym)


def _handoff(report, sym: str) -> None:
    """Straight into the scanner on the side that graded best - and nothing at
    all when neither side did, because a button here would be an invitation."""
    if report.best == "neither":
        theme.note("No button to the scanner from here on purpose. Nothing graded "
                   "well enough today, and the cheapest trade you will ever make is "
                   "the one you did not take.")
        return

    st.divider()
    if report.best == "both":
        theme.note("Both sides grade workable, which is the range case. An iron "
                   "condor is the shape that fits a range - it is the two spreads "
                   "at once, sized as one trade.")
        choices = [("iron_condor", "Iron Condor"),
                   ("put_credit_spread", "Put Credit Spread"),
                   ("call_credit_spread", "Call Credit Spread")]
    elif report.best == PUT:
        choices = [("put_credit_spread", "Put Credit Spread")]
    else:
        choices = [("call_credit_spread", "Call Credit Spread")]

    cols = st.columns(len(choices))
    for col, (key, name) in zip(cols, choices):
        if col.button(f"Find this: {name} on {sym} ▸", key=f"cand_to_build_{key}",
                      type="primary" if key == choices[0][0] else "secondary"):
            st.session_state["build_strategy"] = key
            st.session_state["build_underlyings"] = [sym]
            st.session_state["_prev_build_strategy"] = key
            st.success("Loaded into **🎯 Find a trade** - open that tab to scan it "
                       "and check it against your rules.")
