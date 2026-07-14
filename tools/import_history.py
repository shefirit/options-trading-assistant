"""One-time import of Rita's hand-tracked trade history into the trade log.

How to use:
  1. Fill TRADES below - one dict per historical trade (see the example).
  2. Run:  .venv\\Scripts\\python.exe tools\\import_history.py
  3. Press "↻ Refresh" in the app's 📒 Trades tab and walk the month picker
     to confirm each month's total matches her old sheet.

Every entry writes a normal "open" event row (backdated), and - when the
trade is already closed - a matching "close" event row, through the same
logger the app uses. Rows go to the Google Sheet machine tab when the
webhook is configured, otherwise to the local Excel backup. The retired
teacher-format mirror tab is never touched.

Safe to re-run ONLY after deleting previously imported rows (each run mints
new trade ids), so fill the list once and run it once.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine import quick_log
from src.engine.config_loader import load_strategies
from src.engine.models import Trade
from src.logging_tools.trade_logger import close_trade, log_trade

# ---------------------------------------------------------------- fill me in
# strategy_key: one of put_credit_spread / call_credit_spread / iron_condor /
#               cash_secured_put / poor_mans_covered_call /
#               covered_call_model_1 / covered_call_model_2 / covered_call_model_3
# strikes:      role -> strike, roles exactly as in config/strategies.yaml
# credit_total: dollars collected for the WHOLE position (all contracts)
# closed:       None if the trade is still open
# exit_cost:    dollars paid to close (0.0 if it expired worthless)
# reason:       "Profit target (50%) hit" / "21 DTE time exit" / "Stop loss hit"
#               / "Expired worthless" / "Rolled to a new position" / "Other"
TRADES: list[dict] = [
    # {
    #     "strategy_key": "put_credit_spread",
    #     "underlying": "SPX",
    #     "strikes": {"short_put": 6000, "long_put": 5975},
    #     "contracts": 1,
    #     "credit_total": 300.0,
    #     "opened": date(2026, 6, 5),
    #     "expiration": date(2026, 7, 17),
    #     "closed": date(2026, 6, 24),          # or None if still open
    #     "exit_cost": 150.0,
    #     "realized_pl": 150.0,
    #     "reason": "Profit target (50%) hit",
    #     "note": "",
    #     "followed_sop": True,
    #     # PMCC / covered-call extras (omit for spreads and CSPs):
    #     # "leaps_expiration": date(2027, 6, 18),
    #     # "leaps_cost_total": 6000.0,
    #     # "share_price": 150.0,
    # },
]


def run() -> None:
    if not TRADES:
        print("TRADES is empty - fill in the list at the top of this file first.")
        return
    strategies = load_strategies()
    imported = 0
    for t in TRADES:
        strat = strategies[t["strategy_key"]]
        opened: date = t["opened"]
        expiration: date = t["expiration"]
        dte = max((expiration - opened).days, 0)
        leaps_exp = t.get("leaps_expiration")
        leaps_dte = max((leaps_exp - opened).days, 0) if leaps_exp else None

        legs = quick_log.legs_from_strategy(strat, t["strikes"], dte,
                                            leaps_dte=leaps_dte)
        trade = Trade(strategy_key=t["strategy_key"], underlying=t["underlying"],
                      contracts=int(t.get("contracts", 1)), legs=legs,
                      underlying_price=t.get("share_price"))
        sizing = quick_log.sizing_from_fill(
            trade, strat, float(t["credit_total"]),
            leaps_cost_total=t.get("leaps_cost_total"),
            share_price=t.get("share_price"))

        note = ("imported from her sheet"
                + (f" - {t['note']}" if t.get("note") else ""))
        dest, live, trade_id = log_trade(
            trade, strat["name"], sizing, bool(t.get("followed_sop", True)),
            note, opened_on=opened, expiration_on=expiration)
        line = f"open  {trade_id}  {t['underlying']:<5} {opened}  -> {dest}"

        if t.get("closed"):
            close_trade(trade_id, t["underlying"], strat["name"],
                        float(t.get("exit_cost", 0.0)),
                        float(t.get("realized_pl", 0.0)),
                        str(t.get("reason", "Other")), t.get("lesson", ""),
                        closed_on=t["closed"])
            line += f" | closed {t['closed']} P&L {t.get('realized_pl', 0.0):+.0f}"
        print(line)
        imported += 1
    print(f"\nDone - {imported} trade(s) imported. Press Refresh in the "
          "app's Trades tab and check the month picker.")


if __name__ == "__main__":
    run()
