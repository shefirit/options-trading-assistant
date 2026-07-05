"""One place the app calls to log, close, and read back trades. It tries your
Google Sheet first, and if that is not set up (or fails), it quietly uses the
local Excel backup so you never lose a record.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from src.engine.models import Trade
from src.logging_tools import app_trades, excel_logger, sheets_logger, webhook_logger
from src.logging_tools.row import COLUMNS, build_close_row, build_row, new_trade_id


def _append(row: list[Any], mirror: Optional[dict] = None) -> tuple[str, bool]:
    """Send one event row wherever it can land: sheet webhook, then service
    account, then the local Excel backup. Returns (destination, went_to_sheet).

    mirror (webhook only) is the extra data the Apps Script writes into the
    human "App Trades" tab in Rita's format."""
    # 1. Apps Script web app (Rita's chosen method).
    if webhook_logger.is_configured():
        try:
            return webhook_logger.append(row, COLUMNS, mirror=mirror), True
        except Exception:
            pass
    # 2. Service-account connection (if a JSON key is ever added instead).
    if sheets_logger.is_configured():
        try:
            return sheets_logger.append(row, COLUMNS), True
        except Exception:
            pass
    # 3. Safe local backup.
    return str(excel_logger.append_values(row)), False


def log_trade(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str = "",
) -> tuple[str, bool, str]:
    """Log a new trade (an "open" event). Returns (destination, went_to_sheet,
    trade_id). The trade_id is what the My trades tab tracks the position by."""
    trade_id = new_trade_id(trade.underlying)
    row = build_row(trade, strategy_name, sizing, passed_sop, note, trade_id=trade_id)
    expiration_iso = ""
    if trade.dte is not None:
        expiration_iso = (date.today() + timedelta(days=int(trade.dte))).isoformat()
    mirror = app_trades.mirror_fields(trade, sizing, trade_id, expiration_iso)
    dest, live = _append(row, mirror=mirror)
    return dest, live, trade_id


def close_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    exit_cost: float,
    realized_pl: float,
    reason: str,
    note: str = "",
) -> tuple[str, bool]:
    """Record that a trade was closed (a "close" event). Returns (destination,
    went_to_sheet)."""
    row = build_close_row(trade_id, underlying, strategy_name,
                          exit_cost, realized_pl, reason, note)
    # Tell the script to also update the App Trades mirror row (set its Profit%
    # so her Profit$ formula shows the realized result, and mark CLOSE).
    mirror = {"close": True, "trade_id": trade_id, "realized_pl": realized_pl}
    return _append(row, mirror=mirror)


def delete_trade(trade_id: str) -> tuple[int, str]:
    """Remove a logged trade (all its rows) wherever it lives. Returns
    (rows_removed, source). Deletes from the Google Sheet when connected (that
    is where fetch reads from), otherwise from the local backup. Raises if the
    sheet is connected but its script is too old to support delete."""
    if not trade_id:
        return 0, "local"
    if webhook_logger.is_configured():
        return webhook_logger.delete_trade(trade_id), "sheet"
    return excel_logger.delete_trade(trade_id), "local"


def fetch_all_rows() -> tuple[list[str], list[list[Any]], str]:
    """Read the whole trade log back: (header, rows, source).

    source is "sheet" or "local". Reading tries the Google Sheet first (needs
    the v2 Apps Script), then the local Excel backup. Raises only if BOTH the
    sheet read fails AND there is no local file - callers show a friendly note.
    """
    if webhook_logger.is_configured():
        try:
            header, rows = webhook_logger.fetch_rows()
            return header, rows, "sheet"
        except Exception:
            pass
    header, rows = excel_logger.read_rows()
    return header, rows, "local"
