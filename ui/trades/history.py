"""Looking back: one month at a time, or everything since she started.

The picker decides the scope and everything below follows it.
"""

from __future__ import annotations

import streamlit as st

from ui import components, income_report, theme
from ui.trades.account import _live_from


ALL_TIME = "All time"


def _results_section(all_pos, settings, bp_used: float, mode: str = "real") -> None:
    """One results block, scoped by a single picker.

    There used to be two: "Monthly tracking" and "Your results". They answered
    the same question at different scopes and printed the same four numbers -
    closed trades, win rate, profit against goal, a chart - one above the other.
    With every trade in one month they were literally identical on screen, which
    is what made the tab look broken.

    Now the picker decides the scope and everything below follows it, and the
    scope leads with the month's income report rather than four bare metrics.
    """
    from src.engine import month_report as mr
    from src.engine import positions as pos_mod

    theme.section("Are you on pace for your goals?", "Results")
    summaries = pos_mod.monthly_summary(all_pos)
    names = [ALL_TIME] + [m["label"] for m in summaries]
    if st.session_state.get("trades_month_pick") not in names:
        st.session_state.pop("trades_month_pick", None)
    # Default to this month: the question she opens the tab with is usually
    # "how is THIS month going", not "how has it all gone".
    idx = 1 if len(names) > 1 else 0

    live_from = _live_from(settings)
    # The account is already chosen at the top of the tab and scopes everything
    # here - all_pos arrives pre-filtered, so this picker only chooses a month.
    pick = st.selectbox("Show me", names, index=idx, key="trades_month_pick",
                        help="One month at a time, or everything since you started.")

    monthly_goal = float(settings["targets"]["monthly"])
    bp_limit = float(settings["risk_limits"]["monthly_bp_limit"])

    # The income report is the headline view for a single month - it is the
    # question "how did this month go" answered in full. All time keeps the
    # cumulative dashboard, which is a different question.
    month_key = (mr.ALL_TIME if pick == ALL_TIME
                 else next(m["month"] for m in summaries if m["label"] == pick))
    report = mr.build(all_pos, month=month_key, live_from=live_from, mode=mode)

    # An empty REAL report in a month she knows she traded is the one moment
    # this design can confuse her, so it explains itself rather than saying
    # "nothing logged" about a month full of practice trades.
    empty_note = ""
    if mode == "real" and not report["has_activity"] and live_from:
        empty_note = (
            f"**No real-money trades in {report['label']} yet.** You funded on "
            f"{live_from.day} {live_from:%B}, and this book holds only real money. "
            "Any trades you are thinking of are in your practice book - switch "
            "accounts at the top of this tab to see them. Your first real trade "
            "starts this page off at zero, which is exactly where a real-money "
            "record should start.")

    income_report.render(report, settings, pace=mr.pace(report, monthly_goal),
                         empty_note=empty_note)

    st.divider()
    import datetime as _dt
    covered = pick == ALL_TIME or pick == f"{_dt.date.today():%B %Y}"

    if pick == ALL_TIME:
        perf = pos_mod.performance(all_pos)
        components.render_results_dashboard(perf, settings["targets"], bp_used, bp_limit,
                                            compact=covered)
    else:
        # Just the trade list. The report above now carries every number
        # render_month_summary used to print - profit against goal, counts, BP
        # against the limit, the discipline score and the lessons - so calling
        # it here would print the whole thing a second time.
        entry = next(m for m in summaries if m["label"] == pick)
        if entry["rows"]:
            st.markdown("**Every trade this month:**")
            st.dataframe(components.month_trades_dataframe(entry["rows"]),
                         width="stretch", hide_index=True,
                         column_config=components.month_trades_column_config())

    # The month-by-month bars sit under both views: they are the one picture
    # that only makes sense across months, so scoping them to one would be odd.
    components.render_month_bars(summaries, monthly_goal)
