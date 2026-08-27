"""The arithmetic behind rolling a short option, kept out of the form.

A roll is ONE order with two legs: the option she buys back, and the
further-out one she sells in its place. thinkorswim prints only the NET price
for that order - so a form that asks "what did the new one sell for on its
own?" is asking for a number that appears nowhere on her fill, which is exactly
where the confusion was.

These functions take whichever two of the three figures she actually has and
work out the third, plus the plain result of the leg that just finished. That
lets the form show her what the roll did to her money instead of only banking
it quietly.

None of this cares which side she rolled. A cash secured put rolled down and
out and a PMCC's call rolled up and out are the same three numbers in the same
places - the only difference is the word the form uses when it says them back
to her, which is what `leg_word` carries.

Every figure is TOTAL DOLLARS (fill price x 100 x contracts), the way the rest
of the app talks about money - never per-share option prices.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RollFigures:
    """One roll, with every money figure filled in.

    old_credit is what the leg she just bought back had sold for when she wrote
    it. It is the only figure that comes from history rather than from today's
    fill, and it is what makes "did that one actually make money?" answerable.
    """

    net_credit: float        # cash this one order put in her account (negative = it cost her)
    paid_to_close: float     # what buying the old leg back cost
    new_credit: float        # what the new leg sold for on its own
    old_credit: float        # what the old leg had sold for when she wrote it
    old_leg_result: float    # old_credit - paid_to_close: that leg, start to finish
    # "call", "put", or "spread" - what to call the contract in a sentence.
    # Money-wise it changes nothing; it exists so the warnings below read like
    # the trade she is actually looking at.
    leg_word: str = "call"

    @property
    def is_debit(self) -> bool:
        """True when the roll cost her money instead of paying her. Her SOP
        says close it instead when this happens, but it is recorded either way
        - it is her decision, not the app's."""
        return self.net_credit < 0

    @property
    def impossible(self) -> str:
        """Why these numbers cannot all be true, in plain English, or "".

        Buying back something she SOLD always costs money. If the maths says
        the buy-back paid her, one of the two figures she typed is wrong - and
        saying so beats silently logging a roll that never happened.
        """
        if self.paid_to_close < 0:
            return (
                f"These numbers say buying the old {self.leg_word} back PAID "
                f"you ${abs(self.paid_to_close):,.0f}, and that cannot happen "
                f"- closing something you sold always costs money. One of the "
                f"two figures is off, most likely what the new "
                f"{self.leg_word} sold for."
            )
        return ""


@dataclass(frozen=True)
class Prefill:
    """A suggested "what did the new leg sell for on its own?", and where it
    came from - the two answers read differently enough on screen to be worth
    telling apart.
    """

    total: float | None      # dollars for the whole position, or None
    from_chain: bool         # True = today's chain, False = worked from her fill


def new_credit_prefill(chain_price: float | None, cost_to_close: float | None,
                       net_credit: float) -> Prefill:
    """What to put in that box before she types anything.

    Today's chain is the obvious answer and usually the right one. But it
    prices the leg NOW, knows nothing about the order she actually filled, and
    on a thin or stale quote it can come back lower than the credit the roll
    paid her - which says the buy-back paid her too, and the form then refuses
    the roll over a number it filled in itself.

    Her own fill answers the same question and cannot contradict itself: the
    new leg's premium is what getting out of the old one costs plus the cash
    the order put in her account. So that way round is used whenever the
    chain's answer cannot be squared with her fill.

    Both figures are TOTAL dollars for the whole position, like everything else
    here. A chain price that is impossible with nothing to replace it is handed
    back unchanged - better that she sees the warning and fixes it than that
    the app invents a number.
    """
    chain = round(float(chain_price), 2) if chain_price is not None else None
    if chain is not None and (not net_credit or chain >= float(net_credit)):
        return Prefill(chain, True)
    worked = (round(float(cost_to_close) + float(net_credit), 2)
              if cost_to_close is not None and net_credit else None)
    if worked is not None and worked > 0:
        return Prefill(worked, False)
    return Prefill(chain, True)


def from_net(old_credit: float, net_credit: float, new_credit: float,
             leg_word: str = "call") -> RollFigures:
    """Build the figures from the NET price on her fill (the usual case).

    A roll order fills at one net price - 1.50 credit on 1 contract is
    net_credit=150. What she paid to close the old leg is then whatever is left
    once the new one's own premium is taken off.
    """
    paid = float(new_credit) - float(net_credit)
    return RollFigures(
        net_credit=round(float(net_credit), 2),
        paid_to_close=round(paid, 2),
        new_credit=round(float(new_credit), 2),
        old_credit=round(float(old_credit), 2),
        old_leg_result=round(float(old_credit) - paid, 2),
        leg_word=leg_word,
    )


def from_legs(old_credit: float, paid_to_close: float, new_credit: float,
              leg_word: str = "call") -> RollFigures:
    """Build the figures from the two prices, when she has them.

    Closing and then re-selling as two separate orders gives her both prices
    directly, and some order-history screens list a spread's legs one per line.
    Either way the net is derived rather than typed, so it always agrees with
    the legs.
    """
    net = float(new_credit) - float(paid_to_close)
    return RollFigures(
        net_credit=round(net, 2),
        paid_to_close=round(float(paid_to_close), 2),
        new_credit=round(float(new_credit), 2),
        old_credit=round(float(old_credit), 2),
        old_leg_result=round(float(old_credit) - float(paid_to_close), 2),
        leg_word=leg_word,
    )


def buy_back_only(old_credit: float, paid_to_close: float,
                  leg_word: str = "call") -> RollFigures:
    """She bought it back and wrote nothing in its place.

    Same figures with the new leg at zero, so the form can show the finished
    leg's result the same way it does for a roll.
    """
    return from_legs(old_credit, paid_to_close, 0.0, leg_word)
