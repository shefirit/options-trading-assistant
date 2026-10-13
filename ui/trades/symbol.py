"""Your own trades on one symbol - for the Analyze tab.

Rita's ask (2026-10-13): ROI so far on the Analyze tab. That tab is about one
name, so this is her record ON that name: what the trades still open are doing
at live prices, and what the closed ones made - the same measure as the
Journal, the month report and the Now band (src/engine/roi.py).

Which book follows the My trades account switch, so the Analyze tab never
shows practice money where she has been reading real (or the other way round),
and the strip says which one it is.
"""

from __future__ import annotations

import streamlit as st

from ui import components, theme


def _book(settings) -> str:
    """The account the My trades switch is on - real money unless she last
    picked practice there, or has not funded yet."""
    from ui.trades.account import PAPER_LABEL, _live_from

    if _live_from(settings) is None:
        return "practice"
    picked = str(st.session_state.get("trades_account") or "")
    return "practice" if picked.startswith(PAPER_LABEL) else "real"


def render(sym: str, settings, strategies, provider) -> None:
    """A short strip under the symbol's picture. Draws nothing at all when
    she has never traded the name - the Overview is for the name first."""
    from src.engine import exit_rules
    from src.engine import positions as pos_mod
    from src.engine import roi
    from ui.trades.account import mr_split
    from ui.trades.data import _exit_cfg_for, _load_trade_log

    if not sym:
        return
    header, rows, _source = _load_trade_log()
    mode = _book(settings)
    book = mr_split(pos_mod.parse_rows(header, rows), settings)[mode]
    mine = [p for p in book if p.status != "merged"
            and (p.underlying or "").upper() == sym.upper()]
    if not mine:
        return

    open_now = [p for p in mine if p.status == "open"]
    closed = [p for p in mine if p.status == "closed"]

    # Priced here rather than borrowed from My trades: that tab prices every
    # open trade, and asking it for one symbol would throw its cache away.
    # price_position caches the quotes it fetches, so this is cheap.
    live_rois = []
    for p in open_now:
        live = provider.price_position(p)
        sig = exit_rules.evaluate(
            p, _exit_cfg_for(p, strategies),
            current_cost=live.get("cost_to_close"),
            underlying_price=live.get("underlying_price"),
            short_delta=live.get("short_delta"))
        r = components.open_roi(p, components.open_pl(p, live, sig))
        live_rois.append((p, r))

    open_book = roi.book_of([r for _, r in live_rois if r is not None])
    closed_book = roi.book_roi(closed)
    which = "real money" if mode == "real" else "practice (PaperMoney)"

    st.markdown(f"**Your trades on {sym}** · {which}")
    cards = []
    if open_now:
        if open_book is not None:
            priced = len([r for _, r in live_rois if r is not None])
            note = ("" if priced == len(open_now)
                    else f" · {priced} of {len(open_now)} priced")
            cards.append(theme.stat(
                "OPEN · ROI SO FAR", f"{open_book.pct:+.1f}%",
                f"{theme.signed(open_book.result)} on "
                f"{theme.money(open_book.base)} at risk · "
                f"{open_book.annual_pct:+.0f}% a year{note}",
                "up" if open_book.pct >= 0 else "down"))
        else:
            cards.append(theme.stat(
                "OPEN · ROI SO FAR", "-",
                f"{len(open_now)} open - could not be priced right now"))
    if closed_book is not None:
        cards.append(theme.stat(
            "CLOSED · ROI", f"{closed_book.pct:+.1f}%",
            f"{theme.signed(closed_book.result)} on "
            f"{theme.money(closed_book.base)} at risk · "
            f"{closed_book.annual_pct:+.0f}% a year · {closed_book.trades} "
            f"trade{'' if closed_book.trades == 1 else 's'}",
            "up" if closed_book.pct >= 0 else "down"))
    if cards:
        theme.stat_row(cards)

    # One line per open trade: with two or three on one name, the total alone
    # hides which of them is carrying it.
    for p, r in live_rois:
        name = components.short_strategy(p.shown_strategy_name)
        if r is None:
            theme.note(f"• **{name}**, opened {components.fmt_date(p.opened)} - "
                       "could not be priced right now.")
        else:
            theme.note(
                f"• **{name}**, opened {components.fmt_date(p.opened)} - "
                f"**{r.pct:+.1f}%** so far, {r.days} day"
                f"{'' if r.days == 1 else 's'} in "
                f"({components._esc(theme.signed(r.result))} on "
                f"{components._esc(theme.money(r.base))} at risk).")
