"""Reading the trade log, and pricing what is open.

Both are cached in session state: the log is fetched once per session, and live
prices are memoised for three minutes. Streamlit re-runs every tab body on
every interaction, so without these the tab would re-fetch the sheet and
re-price every position each time she ticks a checkbox.
"""

from __future__ import annotations

import streamlit as st


_DEFAULT_EXIT = {"profit_target_pct": 50, "stop_loss_multiple": 2.0, "time_exit_dte": 21}


def _load_trade_log() -> tuple[list, list, str]:
    """The trade log rows, fetched once per session (Refresh re-reads)."""
    if "trades_rows" not in st.session_state:
        with st.spinner("Reading your trade log..."):
            try:
                from src.logging_tools.trade_logger import fetch_all_rows
                st.session_state["trades_rows"] = fetch_all_rows()
            except Exception:
                st.session_state["trades_rows"] = ([], [], "local")
    return st.session_state["trades_rows"]


def _exit_cfg_for(pos, strategies) -> dict:
    # effective_strategy_key, not strategy_key: a bought LEAPS call with a call
    # written against it is being run as a PMCC now, and the LEAPS page has no
    # 50% target and no 21-day exit to manage that call with.
    strat = strategies.get(pos.effective_strategy_key)
    if strat is None:   # older row - find the strategy by its display name
        strat = next((s for s in strategies.values()
                      if s.get("name") == pos.strategy_name), None)
    return (strat or {}).get("exit", _DEFAULT_EXIT) or _DEFAULT_EXIT


def _price_positions(open_pos, provider, strategies) -> tuple[list, str]:
    """Price every open position and run the exit rules - memoized for a few
    minutes in the session. Every tap anywhere in the app reruns the whole
    script (all tabs), so without this the pricing loop would replay on each
    interaction; with it, only the first look and every ~3 minutes do work.
    Returns (items, as-of time)."""
    import datetime as dt
    import time

    from src.engine import exit_rules

    sig = (tuple(sorted(p.trade_id or f"{p.underlying}|{p.opened}" for p in open_pos)),
           int(time.time() // 180))
    cached = st.session_state.get("_priced_positions")
    if cached and cached["sig"] == sig:
        return cached["items"], cached["at"]

    items = []
    bar = st.progress(0.0, text="Pricing your open trades...")
    for i, p in enumerate(open_pos):
        live = provider.price_position(p)
        cfg = _exit_cfg_for(p, strategies)
        s = exit_rules.evaluate(
            p, cfg,
            current_cost=live.get("cost_to_close"),
            underlying_price=live.get("underlying_price"),
            short_delta=live.get("short_delta"))
        # Carried so the table can date the time exit from THIS strategy's rule
        # rather than assuming 21 for everything.
        items.append({"position": p, "live": live, "signal": s,
                      "time_exit_dte": int(cfg.get("time_exit_dte", 21) or 21)})
        bar.progress((i + 1) / len(open_pos),
                     text=f"Priced {p.underlying} ({i + 1}/{len(open_pos)})")
    bar.empty()
    # Not sorted here on purpose: the open-trades section sorts by
    # glance.priority, which holds the same urgency table. Sorting twice meant
    # two copies of one rule, and only the second one was ever visible.
    at = dt.datetime.now().strftime("%H:%M")
    st.session_state["_priced_positions"] = {"sig": sig, "items": items, "at": at}
    return items, at


# "uncovered" is here because a PMCC with no call written against it is idle
# capital - the whole income of that strategy is the call she has not sold. Her
# SOP allows sitting uncovered for a while after taking a win at 50%, so it is a
# nudge rather than an alarm, but it is still something to do today.
ACTION_SIGNALS = ("stop", "time", "profit", "uncovered")
