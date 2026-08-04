"""The wheel, told as one story instead of three unrelated trades.

The wheel is: sell a put on a stock you want to own; if it expires worthless
keep the cash and sell another; if you are ASSIGNED you own 100 shares per
contract and start selling calls against them, until one day the shares get
called away.

The app could not follow that. Assignment ended the trade, the shares appeared
from nowhere, and every premium collected before that moment stopped counting -
so the one number the whole strategy turns on, what those shares REALLY cost
after all the premium, was never on screen.

That number is the point of these functions. Everything here is arithmetic on a
Position that has already been assigned; nothing fetches or stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WheelState:
    """What the shares cost, what they are worth, and what gets her out."""

    shares: int                      # 100 per contract
    paid_per_share: float            # the put strike she was assigned at
    premium_collected: float         # every credit on this trade, put and calls
    cost_basis: float                # per share, after the premium is taken off
    market_price: Optional[float]    # today, when the app could price it
    unrealised: Optional[float]      # shares only, against the basis
    call_strike: Optional[float]     # the call written against them right now
    called_away_profit: Optional[float]   # what that call finishing ITM pays her

    @property
    def break_even(self) -> float:
        """The share price where she walks away level. Same number as the cost
        basis - named the way she asks the question."""
        return self.cost_basis

    @property
    def below_basis(self) -> bool:
        """Shares worth less than they cost her. Normal on a wheel and not a
        loss until she sells: the calls she keeps writing lower the basis
        further every month."""
        return (self.market_price is not None
                and self.market_price < self.cost_basis)

    @property
    def premium_per_share(self) -> float:
        """How far the premium has pulled the basis down, per share."""
        return round(self.paid_per_share - self.cost_basis, 2)


def state_from(position, market_price: Optional[float] = None) -> Optional[WheelState]:
    """The wheel's numbers for an assigned position, or None if not assigned.

    premium_collected deliberately counts BOTH sides of the story: the put that
    got her assigned (open_credit) and every call written since, including the
    credits from rolling them (roll_income). That is what makes the cost basis
    fall month after month, and it is the whole reason the strategy is worth
    running.
    """
    strike = getattr(position, "assigned_strike", None)
    if not strike:
        return None

    contracts = max(int(position.contracts or 1), 1)
    shares = 100 * contracts
    premium = round(float(position.open_credit or 0.0)
                    + float(position.roll_income or 0.0), 2)
    basis = round(float(strike) - premium / shares, 2)

    unrealised = (round((float(market_price) - basis) * shares, 2)
                  if market_price else None)

    # The call she is short right now, if any - a wheel spends most of its life
    # with one written against the shares.
    call_strike = next(
        (leg.strike for leg in position.legs
         if getattr(leg.action, "value", leg.action) == "sell"
         and getattr(leg.option_type, "value", leg.option_type) == "call"),
        None)
    called_away = (round((float(call_strike) - basis) * shares, 2)
                   if call_strike else None)

    return WheelState(
        shares=shares,
        paid_per_share=round(float(strike), 2),
        premium_collected=premium,
        cost_basis=basis,
        market_price=round(float(market_price), 2) if market_price else None,
        unrealised=unrealised,
        call_strike=float(call_strike) if call_strike else None,
        called_away_profit=called_away,
    )


def assignment_cash(strike: float, contracts: int) -> float:
    """What leaves her account when a short put is assigned: she buys the
    shares at the strike, whatever they are worth that morning."""
    return round(-abs(float(strike)) * 100 * max(int(contracts or 1), 1), 2)


def is_wheelable(position) -> bool:
    """Can this position BE assigned into shares?

    Any short put she is prepared to be assigned on - the wheel by name, and a
    cash-secured put, which is the same trade with a different intention. Both
    of her SOP entries for these accept assignment rather than defending
    against it, so both get the button.
    """
    if getattr(position, "assigned_strike", None):
        return False
    if position.status != "open":
        return False
    if position.strategy_key in ("wheel", "cash_secured_put"):
        return True
    # Older rows carry no strategy key, so fall back to the shape: exactly one
    # short put and nothing else.
    sells = [l for l in position.legs
             if getattr(l.action, "value", l.action) == "sell"]
    return (len(position.legs) == 1 and len(sells) == 1
            and getattr(sells[0].option_type, "value",
                        sells[0].option_type) == "put")
