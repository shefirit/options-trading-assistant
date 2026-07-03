"""Logs a trade to a local Excel file (the safe fallback / backup).

This writes to its OWN file (trade_log.xlsx in the project folder), never your
teacher's Hebrew tracker, so that file stays safe.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.engine.models import Trade
from src.logging_tools.row import COLUMNS, build_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = PROJECT_ROOT / "trade_log.xlsx"

__all__ = ["COLUMNS", "append_row", "DEFAULT_LOG"]


def append_row(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str = "",
    path: str | Path = DEFAULT_LOG,
) -> Path:
    """Append one trade to the log workbook, creating it with headers if needed."""
    path = Path(path)
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Trades"
        ws.append(COLUMNS)

    ws.append(build_row(trade, strategy_name, sizing, passed_sop, note))
    wb.save(path)
    return path
