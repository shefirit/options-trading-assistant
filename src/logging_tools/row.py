"""The one row format used by every logger (local Excel and Google Sheets),
so your record looks the same wherever it lands.

Since the "My trades" tracker was added, the log is an EVENT log:
  - an "open" row when you log a trade
  - a "close" row (same Trade ID) when you close it in the app
Open positions = open rows that have no matching close row yet.
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


def _details_json(trade: Trade) -> str:
    """Everything needed to re-price the position later, in one compact cell."""
    return json.dumps({
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
    }, separators=(",", ":"))


def build_row(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str,
    trade_id: str = "",
) -> list[Any]:
    """The "open" event row - written when you press Log this trade."""
    strikes = " / ".join(f"{leg.strike:g}" for leg in trade.legs)
    short_delta = max((leg.abs_delta for leg in trade.short_legs), default=0.0)
    expiration = ""
    if trade.dte is not None:
        expiration = (date.today() + timedelta(days=int(trade.dte))).isoformat()
    return [
        date.today().isoformat(),
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
        _details_json(trade),
    ]


def build_close_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    exit_cost: float,
    realized_pl: float,
    reason: str,
    note: str = "",
) -> list[Any]:
    """The "close" event row - written when you close the trade in My trades."""
    text = reason if not note else f"{reason} - {note}"
    return [
        date.today().isoformat(),
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
        "",
    ]
