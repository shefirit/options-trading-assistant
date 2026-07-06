"""Build the thinkorswim order line for a scanned setup.

When you build an order in thinkorswim, its Order Entry row shows one line like

    SELL -1 VERTICAL SPX 17 APR 26 6300/6250 PUT @2.50 LMT

This module produces that same line for a setup the scanner found, so you can
hold the app next to TOS and check the order strike by strike before you send
it. The expiration DATE is estimated from days-to-expiration, so always confirm
it matches the expiration you picked in TOS.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from src.engine.models import Action, OptionType, Trade


def _exp_text(dte: Optional[int], today: Optional[dt.date] = None) -> str:
    if dte is None:
        return "?"
    d = (today or dt.date.today()) + dt.timedelta(days=int(dte))
    return f"{d.day} {d.strftime('%b').upper()} {d.strftime('%y')}"


def _k(strike: float) -> str:
    """Strikes the TOS way: 6300 not 6300.0, but 622.5 keeps its half point."""
    return f"{strike:g}"


def ticket_line(trade: Trade, today: Optional[dt.date] = None) -> Optional[str]:
    """The TOS Order Entry line for this trade, or None if the shape is one the
    builder does not recognize (then the leg table above is the guide)."""
    legs = trade.legs
    qty = max(int(trade.contracts), 1)
    sym = trade.underlying.upper()
    net = trade.net_credit_per_share   # + = you collect, - = you pay

    # ---- one leg: cash secured put, or the short call of a covered call ----
    if len(legs) == 1:
        leg = legs[0]
        sell = leg.action == Action.SELL
        verb, sign = ("SELL", "-") if sell else ("BUY", "+")
        return (f"{verb} {sign}{qty * leg.quantity} {sym} {_exp_text(leg.dte, today)} "
                f"{_k(leg.strike)} {leg.option_type.value.upper()} @{leg.premium:.2f} LMT")

    # ---- two legs, same type ----
    if len(legs) == 2 and legs[0].option_type == legs[1].option_type:
        opt = legs[0].option_type.value.upper()
        shorts = [l for l in legs if l.action == Action.SELL]
        longs = [l for l in legs if l.action == Action.BUY]
        if len(shorts) != 1 or len(longs) != 1:
            return None
        short, long_ = shorts[0], longs[0]

        # Same expiration = a vertical (your credit spreads).
        if short.dte == long_.dte:
            return (f"SELL -{qty} VERTICAL {sym} {_exp_text(short.dte, today)} "
                    f"{_k(short.strike)}/{_k(long_.strike)} {opt} @{net:.2f} LMT")

        # Different expirations = a diagonal (your PMCC: buy the far LEAPS,
        # sell the near call). TOS quotes it as one order at the net debit.
        return (f"BUY +{qty} DIAGONAL {sym} "
                f"{_exp_text(long_.dte, today)}/{_exp_text(short.dte, today)} "
                f"{_k(long_.strike)}/{_k(short.strike)} {opt} @{abs(net):.2f} LMT")

    # ---- four legs: iron condor (short call/long call/short put/long put) ----
    if len(legs) == 4:
        def pick(action: Action, otype: OptionType):
            found = [l for l in legs if l.action == action and l.option_type == otype]
            return found[0] if len(found) == 1 else None
        sc = pick(Action.SELL, OptionType.CALL)
        lc = pick(Action.BUY, OptionType.CALL)
        sp = pick(Action.SELL, OptionType.PUT)
        lp = pick(Action.BUY, OptionType.PUT)
        if None in (sc, lc, sp, lp):
            return None
        return (f"SELL -{qty} IRON CONDOR {sym} {_exp_text(trade.dte, today)} "
                f"{_k(sc.strike)}/{_k(lc.strike)}/{_k(sp.strike)}/{_k(lp.strike)} "
                f"CALL/PUT @{net:.2f} LMT")

    return None
