"""One place the app calls to log, close, and read back trades. It tries your
Google Sheet first, and if that is not set up (or fails), it quietly uses the
local Excel backup so you never lose a record.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.engine.models import Trade
from src.logging_tools import excel_logger, sheets_logger, webhook_logger
from src.logging_tools.row import (COLUMNS, build_assign_row, build_close_row,
                                   build_edit_row, build_leg_close_row,
                                   build_reopen_row, build_roll_row, build_row,
                                   new_trade_id)


def _append(row: list[Any], mirror: Optional[dict] = None) -> tuple[str, bool]:
    """Send one event row wherever it can land: sheet webhook, then service
    account, then the local Excel backup. Returns (destination, went_to_sheet).

    mirror stays None since the teacher-format "App Trades" tab was retired
    (2026-07): the deployed Apps Script only writes that tab when the payload
    carries a mirror field, so omitting it freezes the tab with no redeploy."""
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
    opened_on: Optional[date] = None,
    expiration_on: Optional[date] = None,
    account: str = "",
) -> tuple[str, bool, str]:
    """Log a new trade (an "open" event). Returns (destination, went_to_sheet,
    trade_id). The trade_id is what the My trades tab tracks the position by.

    opened_on / expiration_on default to today's behavior; pass them when the
    trade was placed on an earlier date (Quick Log backdating, history import).

    account is "real" or "paper" and is stamped onto the row, so the two books
    stay separate for good rather than being re-guessed from dates later."""
    trade_id = new_trade_id(trade.underlying)
    sizing = {**sizing, "account": account}
    row = build_row(trade, strategy_name, sizing, passed_sop, note,
                    trade_id=trade_id, opened_on=opened_on,
                    expiration_on=expiration_on)
    dest, live = _append(row)
    return dest, live, trade_id


def roll_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    cash: float,
    new_strike: Optional[float] = None,
    new_expiration: Optional[date] = None,
    new_credit: float = 0.0,
    note: str = "",
    rolled_on: Optional[date] = None,
    account: str = "",
    option_type: str = "call",
    new_long_strike: Optional[float] = None,
) -> tuple[str, bool]:
    """Record that the leg she is short changed (a "roll" event on the trade).

    Returns (destination, went_to_sheet). cash is the net from the TOS fill and
    is banked on rolled_on, which defaults to today.

    option_type is which side she rolled - "call" for a PMCC or covered call,
    "put" for a cash secured put or the short leg of a put credit spread. It
    defaults to "call" because that is the only thing rolls used to be, and
    every row already in her log was one.

    new_long_strike is where the protection ended up when a whole vertical
    rolled as one order; leave it out on a single-leg roll.

    Omit new_strike/new_expiration to record a buy-back with nothing written in
    its place - the position is uncovered until a later leg gives it one.
    """
    row = build_roll_row(trade_id, underlying, strategy_name, cash,
                         new_strike, new_expiration, new_credit, note,
                         rolled_on=rolled_on, account=account,
                         option_type=option_type,
                         new_long_strike=new_long_strike)
    return _append(row)


def close_leg(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    cash: float,
    strike: Optional[float] = None,
    option_type: str = "put",
    side: str = "buy",
    for_assignment: bool = False,
    note: str = "",
    closed_on: Optional[date] = None,
    account: str = "",
) -> tuple[str, bool]:
    """Record that ONE leg came off while the rest of the trade stayed open (a
    "legclose" event on the same Trade ID).

    The fill this is written for: a credit spread where she sells the long put
    back and leaves the short put alone so it can be assigned. Returns
    (destination, went_to_sheet). cash is signed - positive when the fill paid
    her, which is the usual case here - and is banked on closed_on, which
    defaults to today.
    """
    row = build_leg_close_row(trade_id, underlying, strategy_name, cash,
                              strike=strike, option_type=option_type, side=side,
                              for_assignment=for_assignment, note=note,
                              closed_on=closed_on, account=account)
    return _append(row)


def assign_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    strike: float,
    contracts: int,
    note: str = "",
    assigned_on: Optional[date] = None,
    account: str = "",
) -> tuple[str, bool]:
    """Record that a short put was assigned into shares (an "assign" event).

    Same Trade ID, so the wheel stays one position from the first put to the
    day the shares are called away. Returns (destination, went_to_sheet).
    """
    row = build_assign_row(trade_id, underlying, strategy_name, strike,
                           contracts, note, assigned_on=assigned_on,
                           account=account)
    return _append(row)


def close_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    exit_cost: float,
    realized_pl: float,
    reason: str,
    note: str = "",
    closed_on: Optional[date] = None,
    close_cash: Optional[float] = None,
    account: str = "",
) -> tuple[str, bool]:
    """Record that a trade was closed (a "close" event). Returns (destination,
    went_to_sheet). closed_on defaults to today; pass it for imported history.

    close_cash is the close as signed cash (positive when closing PAID her, as
    it does on a PMCC); it defaults to -exit_cost, the old credit-shape meaning.
    """
    row = build_close_row(trade_id, underlying, strategy_name,
                          exit_cost, realized_pl, reason, note,
                          closed_on=closed_on, close_cash=close_cash,
                          account=account)
    return _append(row)


def edit_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    changes: dict[str, Any],
    summary: str = "",
    edited_on: Optional[date] = None,
    target: str = "open",
    roll_index: Optional[int] = None,
    account: str = "",
) -> tuple[str, bool]:
    """Correct details typed wrong when the trade was logged.

    Appends rather than rewrites, for the same reason the close correction does:
    the sheet is append-only, so a fix that had to change an old row would need
    a delete and a re-write, and a failure between the two loses the trade.
    Here the worst case is a correction that never lands, leaving the original
    exactly as it was. Returns (destination, went_to_sheet).
    """
    if not trade_id or not changes:
        return "", False
    row = build_edit_row(trade_id, underlying, strategy_name, changes,
                         summary=summary, edited_on=edited_on,
                         target=target, roll_index=roll_index, account=account)
    return _append(row)


def reopen_trade(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    note: str = "",
    reopened_on: Optional[date] = None,
    account: str = "",
) -> tuple[str, bool]:
    """Take back a close that never happened (a "reopen" event on the same
    Trade ID). The trade goes back into the open trades exactly as it stood,
    and its result stops counting. Returns (destination, went_to_sheet).

    Appended, not deleted, like every other correction here: the close row
    stays in the sheet as history and the replay simply stops reading it.
    """
    if not trade_id:
        return "", False
    row = build_reopen_row(trade_id, underlying, strategy_name, note=note,
                           reopened_on=reopened_on, account=account)
    return _append(row)


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
