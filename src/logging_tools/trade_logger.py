"""One place the app calls to log a trade. It tries your Google Sheet first, and
if that is not set up (or fails), it quietly saves to the local Excel backup so
you never lose a record.
"""

from __future__ import annotations

from src.engine.models import Trade
from src.logging_tools import excel_logger, sheets_logger, webhook_logger
from src.logging_tools.row import COLUMNS, build_row


def log_trade(
    trade: Trade,
    strategy_name: str,
    sizing: dict[str, float],
    passed_sop: bool,
    note: str = "",
) -> tuple[str, bool]:
    """Log the trade. Returns (destination, went_to_google_sheet).

    Tries, in order: the Apps Script web app (the easy "paste one link" method),
    then a service-account connection, then the local Excel backup so a record
    is never lost. destination is a URL for Google Sheets, else the file path.
    """
    row = build_row(trade, strategy_name, sizing, passed_sop, note)

    # 1. Apps Script web app (Rita's chosen method).
    if webhook_logger.is_configured():
        try:
            return webhook_logger.append(row, COLUMNS), True
        except Exception:
            pass

    # 2. Service-account connection (if a JSON key is ever added instead).
    if sheets_logger.is_configured():
        try:
            return sheets_logger.append(row, COLUMNS), True
        except Exception:
            pass

    # 3. Safe local backup.
    path = excel_logger.append_row(trade, strategy_name, sizing, passed_sop, note)
    return str(path), False
