"""The one row format used by every logger (local Excel and Google Sheets),
so your record looks the same wherever it lands.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.engine.models import Trade

# Columns in order. Matches your tracker's fields (Ticker, Strategy, Strikes,
# Premium, Contracts, BP) plus a few helpful extras.
COLUMNS = [
    "Date", "Underlying", "Strategy", "Legs (strikes)", "Short Delta",
    "DTE", "Contracts", "Credit $", "Max Loss $", "Buying Power $",
    "Passed SOP", "Notes",
]


def build_row(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str,
) -> list[Any]:
    strikes = " / ".join(f"{leg.strike:g}" for leg in trade.legs)
    short_delta = max((leg.abs_delta for leg in trade.short_legs), default=0.0)
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
    ]
