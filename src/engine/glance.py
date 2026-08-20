"""One sentence per open trade: what it is doing, in her own money.

The My trades tab could tell her the instruction ("Hold", "Take the win") and
it could show her eleven columns of numbers, but nothing joined the two up. To
answer "how is this trade actually going?" she had to read a table row, find
the same trade in a dropdown, open the card, and assemble the answer herself -
for every position, every day.

These functions build that answer once, in words, from figures the app has
already worked out. Pure: nothing here fetches, prices or stores.

Every dollar figure is a total, never a per-share price.
"""

from __future__ import annotations

from typing import Optional


def _pct(value: float) -> str:
    return f"{value:.0f}%"


def _money(value: float) -> str:
    """Whole dollars, for sums of money."""
    return f"${abs(value):,.0f}"


def _price(value: float) -> str:
    """Cents kept, for anything quoted PER SHARE - a cost basis rounded to the
    dollar is the wrong number to write a call against."""
    return f"${abs(value):,.2f}"


def _shares_line(position, state, price: Optional[float]) -> str:
    """A wheel that has been assigned: the shares are the trade now."""
    bits = [f"{state.shares} shares at a {_price(state.cost_basis)} basis"]
    if price is not None:
        ahead = state.unrealised or 0.0
        bits.append(f"{position.underlying} at {_price(price)}, "
                    f"{_money(ahead)} {'ahead' if ahead >= 0 else 'behind'}")
    if state.call_strike:
        bits.append(f"the {state.call_strike:g} call is written against them")
    else:
        bits.append("no call written against them yet")
    return ", ".join(bits) + "."


def _awaiting_line(position, price: Optional[float]) -> str:
    """A spread stripped down to its short put, on purpose, waiting to assign.

    The credit line above would be nonsense here: it measures a spread that no
    longer exists against a cost to close she has no intention of paying. What
    she is holding is an obligation to buy shares at a price, and that is what
    the sentence has to say.
    """
    strike = position.assignment_strike
    basis = position.assignment_basis
    bits = [f"short the {strike:g} put on its own"] if strike else ["waiting to assign"]
    if price is not None and strike:
        where = "below" if price <= strike else "above"
        bits.append(f"{position.underlying} at {_price(price)}, {where} it")
    if basis is not None:
        bits.append(f"assignment would leave you {position.contracts * 100} shares "
                    f"at a {_price(basis)} basis, for "
                    f"{_money(position.assignment_cash_needed)} of cash")
    dte = position.dte_left()
    if dte is not None:
        bits.append("expires today" if dte == 0
                    else f"{dte} day{'s' if dte != 1 else ''} to go")
    return _sentence(bits)


def summary_line(position, live: dict, signal, target_pct: float = 50.0,
                 wheel_state=None) -> str:
    """The one line that answers "how is this trade going?".

    Written so the numbers carry their own meaning: not "62%", but "kept $356
    of the $575 credit". A percentage on its own is the thing she has to
    translate, and translating it is the app's job.
    """
    if wheel_state is not None:
        return _shares_line(position, wheel_state, live.get("underlying_price"))
    if getattr(position, "awaiting_assignment", False):
        return _awaiting_line(position, live.get("underlying_price"))

    bits: list[str] = []
    credit = float(position.credit or 0.0)
    cost = live.get("cost_to_close")

    if cost is not None and credit > 0:
        kept = credit - float(cost)
        if kept >= 0:
            bits.append(f"kept {_money(kept)} of the {_money(credit)} credit "
                        f"so far ({_pct(kept / credit * 100)})")
        else:
            # Underwater: say the real number rather than a negative percentage,
            # which reads as a discount rather than a loss.
            bits.append(f"closing it costs {_money(cost)} against the "
                        f"{_money(credit)} you collected, so it is "
                        f"{_money(kept)} down")
    elif credit > 0:
        bits.append(f"{_money(credit)} credit collected")

    dte = position.dte_left()
    if dte is not None:
        bits.append("expires today" if dte == 0
                    else f"{dte} day{'s' if dte != 1 else ''} left")

    # Where price sits relative to the option she sold - the fact that decides
    # whether any of the above is about to change.
    from src.engine.positions import strike_cushion

    cushion = strike_cushion(position, live.get("underlying_price"))
    if cushion:
        side = "call" if cushion.get("option_type") == "call" else "put"
        if cushion.get("breached"):
            bits.append(f"price is PAST your {cushion['strike']:g} {side}")
        else:
            room = abs(float(cushion.get("room_pct") or 0.0)) * 100
            bits.append(f"price is {room:.1f}% clear of your "
                        f"{cushion['strike']:g} {side}")

    if not bits:
        return "Not enough price data to say how this one is doing today."
    return _sentence(bits)


def _sentence(bits: list[str]) -> str:
    """Join the parts into one readable line."""
    text = ", ".join(bits)
    return text[0].upper() + text[1:] + "."


def whole_trade_line(position, live: dict) -> Optional[str]:
    """The money the short-leg view leaves out.

    On a PMCC or covered call the credit is a small slice of the position - the
    LEAPS or the shares hold most of the money, and can be up thousands while
    the short call reads as a small loss. Saying only the short call's story
    there is technically true and practically misleading.
    """
    if not position.is_debit:
        return None
    pl = live.get("open_pl")
    if pl is None:
        return None
    laid_out = abs(float(position.open_cash or 0.0))
    if not laid_out:
        return None
    word = "up" if pl >= 0 else "down"
    return (f"The whole trade, long side included, is {word} {_money(pl)} on "
            f"the {_money(laid_out)} you laid out.")


def priority(signal, position) -> tuple:
    """Sort key: the trades that need a decision, first.

    Same order the exit rules rank urgency in, so the list she scrolls matches
    the list she should act on. Within a band, whatever expires soonest.
    """
    order = {"stop": 0, "time": 1, "profit": 2, "watch": 3, "uncovered": 4,
             # Below the trades needing a decision, above a plain hold: she has
             # already decided what happens here, she just has to be ready for it.
             "awaiting": 5, "unpriced": 6, "hold": 7}
    dte = position.dte_left()
    return (order.get(signal.action, 9), dte if dte is not None else 10**6)
