"""The arithmetic behind rolling a short call, kept out of the form.

A roll is ONE order with two legs: the call she buys back, and the further-out
one she sells in its place. thinkorswim prints only the NET price for that
order - so a form that asks "what did the new call sell for on its own?" is
asking for a number that appears nowhere on her fill, which is exactly where
the confusion was.

These functions take whichever two of the three figures she actually has and
work out the third, plus the plain result of the call that just finished. That
lets the form show her what the roll did to her money instead of only banking
it quietly.

Every figure is TOTAL DOLLARS (fill price x 100 x contracts), the way the rest
of the app talks about money - never per-share option prices.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RollFigures:
    """One roll, with every money figure filled in.

    old_credit is what the call she just bought back had sold for when she
    wrote it. It is the only figure that comes from history rather than from
    today's fill, and it is what makes "did that call actually make money?"
    answerable.
    """

    net_credit: float        # cash this one order put in her account (negative = it cost her)
    paid_to_close: float     # what buying the old call back cost
    new_credit: float        # what the new call sold for on its own
    old_credit: float        # what the old call had sold for when she wrote it
    old_call_result: float   # old_credit - paid_to_close: that call, start to finish

    @property
    def is_debit(self) -> bool:
        """True when the roll cost her money instead of paying her. Her SOP
        says close the call instead when this happens, but it is recorded
        either way - it is her decision, not the app's."""
        return self.net_credit < 0

    @property
    def impossible(self) -> str:
        """Why these numbers cannot all be true, in plain English, or "".

        Buying back a short call ALWAYS costs money. If the maths says the
        buy-back paid her, one of the two figures she typed is wrong - and
        saying so beats silently logging a roll that never happened.
        """
        if self.paid_to_close < 0:
            return (
                f"These numbers say buying the old call back PAID you "
                f"${abs(self.paid_to_close):,.0f}, and that cannot happen - "
                f"closing a call you sold always costs money. One of the two "
                f"figures is off, most likely what the new call sold for."
            )
        return ""


def from_net(old_credit: float, net_credit: float, new_credit: float) -> RollFigures:
    """Build the figures from the NET price on her fill (the usual case).

    A diagonal roll order fills at one net price - 1.50 credit on 1 contract is
    net_credit=150. What she paid to close the old call is then whatever is
    left once the new call's own premium is taken off.
    """
    paid = float(new_credit) - float(net_credit)
    return RollFigures(
        net_credit=round(float(net_credit), 2),
        paid_to_close=round(paid, 2),
        new_credit=round(float(new_credit), 2),
        old_credit=round(float(old_credit), 2),
        old_call_result=round(float(old_credit) - paid, 2),
    )


def from_legs(old_credit: float, paid_to_close: float, new_credit: float) -> RollFigures:
    """Build the figures from the two leg prices, when she has them.

    thinkorswim lists a spread order's legs separately under Monitor > Account
    Statement > Account Trade History, and closing then re-selling as two
    orders gives her both prices directly. Either way the net is derived rather
    than typed, so it always agrees with the legs.
    """
    net = float(new_credit) - float(paid_to_close)
    return RollFigures(
        net_credit=round(net, 2),
        paid_to_close=round(float(paid_to_close), 2),
        new_credit=round(float(new_credit), 2),
        old_credit=round(float(old_credit), 2),
        old_call_result=round(float(old_credit) - float(paid_to_close), 2),
    )


def buy_back_only(old_credit: float, paid_to_close: float) -> RollFigures:
    """She bought the call back and wrote nothing in its place.

    Same figures with the new call at zero, so the form can show the finished
    call's result the same way it does for a roll.
    """
    return from_legs(old_credit, paid_to_close, 0.0)
