"""The one row format used by every logger (local Excel and Google Sheets),
so your record looks the same wherever it lands.

Since the "My trades" tracker was added, the log is an EVENT log:
  - an "open" row when you log a trade
  - a "roll" row (same Trade ID) each time the short call is rolled
  - a "legclose" row (same Trade ID) when ONE leg comes off and the rest of the
    trade stays open - selling the long put of a credit spread and leaving the
    short put to be assigned
  - a "close" row (same Trade ID) when it is closed in the app
  - a "reopen" row (same Trade ID) when a close is taken back - it was recorded
    on the wrong trade, or on one she has not actually closed yet
Open positions = open rows whose last close-or-reopen row is not a close.

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
    # Which book this row belongs to: "real" or "paper". A plain column rather
    # than a field inside Details JSON, because the whole point is that she can
    # SEE it - open the sheet, sort or filter on this one cell, and know which
    # trades were real money. The Apps Script auto-extends the header when a
    # new column appears, so adding this needed no redeploy.
    "Account",
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
    # The BP Effect read off thinkorswim, when she supplied it. Absent means
    # "not told", which is different from a real zero - so only write it when a
    # number was actually given. It lives here rather than in a new column
    # because adding a column means she has to redeploy the Apps Script.
    if sizing.get("bp_effect") is not None:
        data["bp_effect"] = round(float(sizing["bp_effect"]), 2)
    return json.dumps(data, separators=(",", ":"))


def _account(value: Any) -> str:
    """The account cell: "real", "paper", or blank when nobody said.

    Stamped on the row when the trade is logged rather than worked out later
    from the date, because a date rule cannot tell a PRACTICE trade placed
    after going live from a real one - and she may well keep paper-testing a
    new strategy alongside real money.
    """
    return str(value) if value in ("real", "paper") else ""


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
        _account(sizing.get("account")),
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
    account: str = "",
    option_type: str = "call",
    new_long_strike: Optional[float] = None,
) -> list[Any]:
    """The "roll" event row - the leg she is short changed and cash moved, on
    the SAME Trade ID.

    This is what keeps one PMCC one position from LEAPS purchase to LEAPS sale
    instead of a chain of unrelated rows with the cost basis re-entered each
    time - and the same for a cash secured put rolled down and out month after
    month, which is one trade with one story, not a new trade every roll. Every
    field lands in a column she can read in the sheet:

      cash            the net on the TOS fill, banked on this date. Negative
                      when she only bought the leg back and wrote nothing in
                      its place.
      new_credit      what the NEW short leg sold for on its own - or, on a
                      spread, what the new spread's own net credit was. The
                      basis the 50% profit target measures against from here.
      option_type     which side rolled, "call" or "put". Rows written before
                      puts could be rolled carry nothing and mean "call".
      new_long_strike where the protection went when a whole vertical rolled.

    new_strike / new_expiration are left empty when she bought the leg back and
    has not written the next one yet: the position is then uncovered until a
    later row gives it a new one.

    Only the SHORT strike goes in the Legs column, even on a spread roll. The
    replay reads that cell as one number, and a "90 / 95" there would parse as
    nothing at all - which the replay would then take for "bought it back and
    wrote nothing", quietly deleting a leg she still holds. The pair is spelled
    out in the note instead, which is what she reads in the sheet anyway.
    """
    option_type = "put" if str(option_type).lower() == "put" else "call"
    if new_strike is None:
        text = note or f"Bought the short {option_type} back - none written yet"
    elif new_long_strike is not None:
        text = note or (f"Rolled the {option_type} spread to "
                        f"{new_strike:g}/{new_long_strike:g}")
    else:
        text = note or f"Rolled the short {option_type} to {new_strike:g}"
    details: dict[str, Any] = {"type": option_type}
    if new_long_strike is not None:
        details["long_strike"] = round(float(new_long_strike), 4)
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
        # Which side rolled, and where its protection went. The trade's own
        # details stay on the open row - this cell was empty until puts could
        # be rolled, and using it beats adding a column she would have to
        # redeploy the Apps Script for.
        json.dumps(details, separators=(",", ":")),
        _account(account),
    ]


# Fields an edit row may carry. Anything not listed here is left alone, and
# `strategy` / `underlying` are absent on purpose - changing either makes it a
# different trade, and the honest fix for that is delete and re-log.
EDITABLE = ("opened_on", "expiration", "contracts", "credit", "open_cash",
            "legs", "strikes", "max_loss", "buying_power", "account", "note")


def build_edit_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    changes: dict[str, Any],
    summary: str = "",
    edited_on: Optional[date] = None,
    target: str = "open",
    roll_index: Optional[int] = None,
    account: str = "",
) -> list[Any]:
    """The "edit" event row - a correction to details typed wrong earlier.

    The log is append-only and the sheet cannot be edited in place, so a fix is
    a new row rather than a change to an old one. Same shape as the existing
    close correction: nothing is deleted, the app replays the log in order, and
    a later edit wins. She can correct a correction.

    Only the fields she actually changed are written, because absent means
    "leave it alone" - a blanket rewrite would silently reset anything the form
    did not know about.

    `target` says WHAT is being corrected: "open" for the trade's own details,
    or "roll" plus `roll_index` for one of its roll events. The index counts
    rolls in the order they were WRITTEN, not by date - append order never
    changes, whereas correcting a roll's date would renumber a date-sorted list
    underneath the very edit doing the correcting.

    `summary` is the human-readable "contracts 2 -> 1" that lands in Notes, so
    the sheet reads as a story rather than as a second mystery row.
    """
    payload: dict[str, Any] = {
        "target": target,
        "changes": {k: v for k, v in changes.items() if k in EDITABLE},
    }
    if target == "roll" and roll_index is not None:
        payload["roll_index"] = int(roll_index)
    changed = payload["changes"]
    return [
        (edited_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        # The corrected strikes, when that is what changed - so the column
        # still reads correctly at a glance in the sheet.
        str(changed.get("strikes", "")),
        "", "", "",                   # delta/dte/contracts - see Details JSON
        "",                           # Credit $ - the open row keeps the original
        "", "", "",                   # max loss/BP/passed SOP - unchanged here
        summary or "Corrected trade details",
        trade_id,
        "edit",
        str(changed.get("expiration", "")),
        "",                           # Exit Cost $ - not a close
        "",                           # Realized P&L $ - no money moved
        json.dumps(payload, separators=(",", ":")),
        # The trade's own book. This cell is what the Apps Script routes on, so
        # a blank one sent every correction to the Practice tab - including
        # corrections to real-money trades. The app read them anyway (an edit
        # finds its trade by Trade ID, whichever tab it sits in), but her sheet
        # said a real trade had been corrected in the practice book.
        _account(account),
    ]


def build_reopen_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    note: str = "",
    reopened_on: Optional[date] = None,
    account: str = "",
) -> list[Any]:
    """The "reopen" event row - that close never happened.

    Closing is one primary button on a card, and the card can be showing a
    different trade from the one she means (see ui/trades/open_trades.py). Until
    this existed the only way back from a close recorded on the wrong trade was
    to DELETE the trade and rebuild it by hand - throwing away its rolls and its
    history to undo a single click.

    So a close can be taken back the same way everything else here is corrected:
    by appending, never by rewriting. The replay reads closes and reopens in the
    order they were written and the last word wins, so this puts the trade back
    exactly as it stood - same Trade ID, same legs, same rolls - and its result
    stops counting in the month. Close it again later and the close wins again.
    """
    return [
        (reopened_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        "", "", "", "", "", "", "",   # strikes/delta/dte/contracts/money - unchanged
        "",
        note or "Reopened - that close was recorded by mistake",
        trade_id,
        "reopen",
        "",                           # Expiration - the trade keeps its own
        "",                           # Exit Cost $ - nothing was paid
        "",                           # Realized P&L $ - and nothing was banked
        "",                           # Details JSON - nothing to re-price
        _account(account),
    ]


def build_leg_close_row(
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
) -> list[Any]:
    """The "legclose" event row - one leg came off, the trade carries on.

    Written when she takes a credit spread apart on purpose: sell the long put
    back, leave the short put open so it can assign her the shares. A "close"
    row would have ended a trade that is still very much running, and a "roll"
    row would have claimed the short leg moved when it did not - so this is its
    own event, on the same Trade ID, and the position stays one story from the
    spread through the assignment to the covered calls afterwards.

      cash            what the fill PAID her (positive) - selling a long put
                      back - or cost her (negative), banked on this date
      for_assignment  she is leaving the short leg to be assigned. Her
                      intention, which nothing else in the log records and
                      which decides every piece of advice the app gives from
                      here: no 50% target, no 21-day roll, have the cash ready.

    The extra facts live in Details JSON rather than in new columns, for the
    same reason the signed cash does: a new column means redeploying the Apps
    Script.
    """
    side = "sell" if str(side).lower() == "sell" else "buy"
    option_type = "call" if str(option_type).lower() == "call" else "put"
    what = f"{strike:g} {option_type}" if strike else f"long {option_type}"
    if note:
        text = note
    elif side == "buy":
        text = f"Sold the {what} back" + (
            " and left the short put open to be assigned" if for_assignment else "")
    else:
        text = f"Bought the {what} back"
    return [
        (closed_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        f"{strike:g}" if strike else "",
        "", "", "",                   # delta/dte/contracts - on the open row
        "",                           # Credit $ - this is not premium sold
        "", "", "",                   # max loss/BP/passed SOP - recomputed on replay
        text,
        trade_id,
        "legclose",
        "",                           # Expiration - the rest of the trade keeps its own
        "",                           # Exit Cost $ - not a close
        round(float(cash), 2),        # Realized P&L $: the cash banked today
        json.dumps({"type": option_type, "side": side,
                    "for_assignment": bool(for_assignment)},
                   separators=(",", ":")),
        _account(account),
    ]


def build_assign_row(
    trade_id: str,
    underlying: str,
    strategy_name: str,
    strike: float,
    contracts: int,
    note: str = "",
    assigned_on: Optional[date] = None,
    account: str = "",
) -> list[Any]:
    """The "assign" event row - a short put became 100 shares per contract.

    This is what lets the wheel stay ONE trade. Before it existed, assignment
    ended the position and the shares reappeared as an unrelated covered call,
    which threw away the premium already collected - and with it the only
    number the wheel is really about, what those shares cost after premium.

    The cash is negative and large (she pays strike x 100 per contract for the
    shares), and it goes in the same Realized P&L column every other event
    uses, so the month reports need no special case. The strike lands in the
    Legs column so the shares can be priced later.
    """
    from src.engine.wheel import assignment_cash

    cash = assignment_cash(strike, contracts)
    return [
        (assigned_on or date.today()).isoformat(),
        underlying,
        strategy_name,
        f"{strike:g}",
        "", "", contracts,
        "",                           # Credit $ - assignment collects nothing
        "", "", "",
        note or f"Assigned {100 * max(int(contracts or 1), 1)} shares at {strike:g}",
        trade_id,
        "assign",
        "",                           # Expiration - the put is gone
        "",
        cash,                         # Realized P&L $: the cash that left today
        "",
        _account(account),
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
    account: str = "",
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
        _account(account),
    ]
