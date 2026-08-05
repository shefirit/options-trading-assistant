"""Real money or practice - the switch that scopes the whole trades tab.

Two completely separate books. The one thing this package must never do is add
a PaperMoney dollar to real income, so every total downstream is computed from
one side only and the choice is made here, once.
"""

from __future__ import annotations

import streamlit as st

from ui import theme


REAL_LABEL = "💵 Real money"


PAPER_LABEL = "📝 Practice (PaperMoney)"


def _live_from(settings):
    """The day real money started, from config/settings.yaml (account.live_from).

    Empty or missing means she is still practising, and the whole log reads as
    practice - which is the safe default: a paper trade counted as income is a
    worse mistake than real income shown as practice.
    """
    import datetime as _dt

    raw = str((settings.get("account") or {}).get("live_from") or "").strip()
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw)
    except ValueError:
        return None


def mr_split(positions, settings) -> dict:
    """The log cut into its two books, real and practice."""
    from src.engine import month_report as mr
    return mr.split_by_mode(positions, _live_from(settings))


def _account_switch(settings, every_pos) -> str:
    """The switch that scopes the whole My trades tab to one book.

    It appears as soon as the log holds anything from the other book, and it
    stays visible rather than hiding once she is fully live: a trader who
    cannot see which account she is looking at is one bad glance away from
    reading practice results as income.
    """
    split = mr_split(every_pos, settings)
    live_from = _live_from(settings)
    if live_from is None:
        # Not funded yet. Everything is practice, and saying so once is
        # clearer than a switch with one position.
        if every_pos:
            st.markdown(theme.chip("📝 Practice account (PaperMoney)", "amber"),
                        unsafe_allow_html=True)
        return "practice"
    if not split["practice"]:
        st.markdown(theme.chip("💵 Real money account", "green"),
                    unsafe_allow_html=True)
        return "real"

    counts = {"real": len(split["real"]), "practice": len(split["practice"])}
    labels = [f"{REAL_LABEL}  ({counts['real']})",
              f"{PAPER_LABEL}  ({counts['practice']})"]
    picked = st.radio(
        "Which account are you looking at?", labels, index=0, horizontal=True,
        key="trades_account",
        help="Two completely separate books. Open trades, today's decisions, "
             "results, records and your goal progress all follow this switch - "
             "nothing from one account ever counts in the other.")
    mode = "real" if picked.startswith(REAL_LABEL) else "practice"
    if mode == "practice":
        st.markdown(theme.chip("📝 Viewing your practice book - none of this is "
                               "real money", "amber"), unsafe_allow_html=True)
    return mode


def _default_account(settings, opened_on=None) -> str:
    """Which book a trade goes in unless she says otherwise.

    Real once she has funded, practice before that. Backdated trades follow the
    date they were PLACED, so importing history does not retroactively turn old
    paper trades into real ones.
    """
    import datetime as _dt

    live = _live_from(settings)
    if live is None:
        return "paper"
    return "real" if (opened_on or _dt.date.today()) >= live else "paper"


def _account_choice(settings, key: str, opened_on=None) -> str:
    """The one control that decides which book a trade is written to.

    It is a deliberate, visible choice on every log form rather than a hidden
    global, because the cost of getting it wrong is asymmetric: a practice
    trade counted as real income quietly inflates the record she uses to judge
    whether this is working.
    """
    default = _default_account(settings, opened_on)
    labels = [REAL_LABEL, PAPER_LABEL]
    picked = st.radio(
        "Which account is this trade in?", labels,
        index=0 if default == "real" else 1, horizontal=True,
        key=f"acct_{key}",
        help="Real money and practice trades are kept completely apart - "
             "separate open positions, separate results, separate records. "
             "Nothing from one ever counts in the other.")
    return "real" if picked == REAL_LABEL else "paper"


# Public names for callers outside this package. app.py's Find-a-trade tab asks
# which account a trade is going into, and Settings needs the go-live date.
choice = _account_choice
live_from = _live_from
switch = _account_switch
split = mr_split
