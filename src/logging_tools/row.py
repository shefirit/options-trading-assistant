"""The one row format used by every logger (local Excel and Google Sheets),
so your record looks the same wherever it lands.

Since the "My trades" tracker was added, the log is an EVENT log:
  - an "open" row when you log a trade
  - a "roll" row (same Trade ID) each time the short call is rolled
  - a "close" row (same Trade ID) when it is closed in the app
Open positions = open rows that have no matching close row yet.

Every event carries SIGNED cash (+ collected, - paid) so the debit strategies
add up: a PMCC pays money out at open and takes it back in at close, which the
old credit-only fields could not express. The signed numbers live in the Details
JSON cell rather than in new columns, so an existing Google Sheet keeps working
with no Apps Script redeploy and rows written by older versions still parse.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.engine.models import Trade

# Columns in order. The first 12 match the original log (Ticker, Strategy,
# Strikes, Premium, Contracts, BP...); the rest power the "My trades" tracker.
# Old rows without the extra columns still show up as history - they just
# can't be tracked live.
COLUMNS = [
    "Date", "Underlying", "Strategy", "Legs (strikes)", "Short Delta",
    "DTE", "Contracts", "Credit $", "Max Loss $", "Buying Power $",
    "Passed SOP", "Notes",
    # --- tracker columns ---
    "Trade ID", "Event", "Expiration", "Exit Cost $", "Realized P&L $",
    "Details JSON",
]


def new_trade_id(underlying: str, when: Optional[datetime] = None) -> str:
    """A simple unique id: 20260705-143002-SPX. Readable in the sheet."""
    when = when or datetime.now()
    return f"{when:%Y%m%d-%H%M%S}-{underlying.upper()}"


def _details_json(trade: Trade, sizing: Optional[dict[str, float]] = None) -> str:
    """Everything needed to re-price the position later, in one compact cell."""
    sizing = sizing or {}
    data: dict[str, Any] = {
        "key": trade.strategy_key,
        "underlying_price": trade.underlying_price,
        "legs": [
            {
                "role": leg.role,
                "action": leg.action.value,
                "type": leg.option_type.value,
                "strike": leg.strike,
                "delta": leg.delta,
                "premium": leg.premium,
                "qty": leg.quantity,
                "dte": leg.dte,
            }
            for leg in trade.legs
        ],
    }
    # Signed net cash at open. Written only when the caller computed it, because
    # positions.parse_rows tells "no ledger, fall back to the Credit $ column"
    # from the key being ABSENT - a stored 0.0 would be a real, wrong answer.
    if "open_cash" in sizing:
        data["open_cash"] = round(float(sizing["open_cash"]), 2)
    if sizing.get("shares_cost"):
        data["shares_cost"] = round(float(sizing["shares_cost"]), 2)
    return json.dumps(data, separators=(",", ":"))


def build_row(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str,
    trade_id: str = "",
    opened_on: Optional[date] = None,
    expiration_on: Optional[date] = None,
) -> list[Any]:
    """The "open" event row - written when you press Log this trade.

    opened_on / expiration_on default to today's behavior; pass them to record
    a trade placed on an earlier date (Quick Log backdating, history import).
    """
    opened_on = opened_on or date.today()
    strikes = " / ".join(f"{leg.strike:g}" for leg in trade.legs)
    short_delta = max((leg.abs_delta for leg in trade.short_legs), default=0.0)
    expiration = ""
    if expiration_on is not None:
        expiration = expiration_on.isoformat()
    elif trade.dte is not None:
        expiration = (opened_on + timedelta(days=int(trade.dte))).isoformat()
    return [
        opened_on.isoformat(),
        trade.underlying,
        strategy_name,
        strikes,
        round(short_delta, 3),
        trade.dte,
        trade.contracts,
        round(sizing.get("credit", 0.0), 2),
        round(sizing.get("max_loss", 0.0), 2),
        round(sizing.get("buying_power", 0.0), 2),
        "yes" if passed_sop else "NO",
        note,
        trade_id or new_trade_id(trade.underlying),
        "open",
        expiration,
        "",   # Exit Cost $ - filled on the close row
        "",   # Realized P&L $ - filled on the close row
        _details_json(trade, sizing),
    ]


def build_roll_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    cash: float,
    new_strike: Optional[float] = None,
    new_expiration: Optional[date] = None,
    new_credit: float = 0.0,
    note: str = "",
    rolled_on: Optional[date] = None,
) -> list[Any]:
    """The "roll" event row - the short call changed and cash moved, on the
    SAME Trade ID.

    This is what keeps one PMCC one position from LEAPS purchase to LEAPS sale
    instead of a chain of unrelated rows with the cost basis re-entered each
    time. Every field lands in a column she can read in the sheet:

      cash        the net on the TOS fill, banked on this date. Negative when
                  she only bought the call back and wrote nothing in its place.
      new_credit  what the NEW short call sold for on its own - the basis the
                  50% profit target measures against from here

    new_strike / new_expiration are left empty when she bought the call back
    and has not written the next one yet: the position is then uncovered until
    a later row gives it a new call.
    """
    if new_strike is None:
        text = note or "Bought the short call back - none written yet"
    else:
        text = note or f"Rolled the short call to {new_strike:g}"
    return [
        (rolled_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        f"{new_strike:g}" if new_strike is not None else "",
        "", "", "",                   # delta/dte/contracts - on the open row
        round(new_credit, 2) if new_credit else "",   # Credit $
        "", "", "",                   # max loss/BP/passed SOP - on the open row
        text,
        trade_id,
        "roll",
        new_expiration.isoformat() if new_expiration is not None else "",
        "",                           # Exit Cost $ - not a close
        round(cash, 2),               # Realized P&L $: the cash banked today
        "",
    ]


def build_close_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    exit_cost: float,
    realized_pl: float,
    reason: str,
    note: str = "",
    closed_on: Optional[date] = None,
    close_cash: Optional[float] = None,
) -> list[Any]:
    """The "close" event row - written when you close the trade in My trades.

    exit_cost is what buying the position back COST (the credit shapes, where it
    is always money out). close_cash is the same event as signed cash and is the
    one that generalises: closing a PMCC PAYS her, because she sells the LEAPS
    back. Defaults to -exit_cost, which is exactly what a close used to mean.

    closed_on defaults to today; pass it when importing an old trade so the
    profit lands in the month it was actually banked.
    """
    if close_cash is None:
        close_cash = -float(exit_cost)
    text = reason if not note else f"{reason} - {note}"
    return [
        (closed_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        "", "", "", "", "", "", "",   # strikes/delta/dte/contracts/money - on the open row
        "",
        text,
        trade_id,
        "close",
        "",
        round(exit_cost, 2),
        round(realized_pl, 2),
        json.dumps({"close_cash": round(float(close_cash), 2)},
                   separators=(",", ":")),
    ]
