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

# Strategies that start by BUYING the shares outright rather than being assigned
# them. From the first call onwards they are the same trade as a wheel after
# assignment - shares, calls written against them, a basis the premium keeps
# pulling down - so they get the same numbers. Models 1-3 own shares too but
# are deliberately left out: their protective puts change what the shares can
# lose, and they have their own protection read on the card.
BOUGHT_SHARE_STRATEGIES = ("weekly_covered_call",)


@dataclass(frozen=True)
class WheelState:
    """What the shares cost, what they are worth, and what gets her out."""

    shares: int                      # 100 per contract
    paid_per_share: float            # the put strike she was assigned at, or
                                     # the price she bought the shares at
    premium_collected: float         # every credit on this trade, put and calls
    cost_basis: float                # per share, after the premium is taken off
    market_price: Optional[float]    # today, when the app could price it
    unrealised: Optional[float]      # shares only, against the basis
    call_strike: Optional[float]     # the call written against them right now
    called_away_profit: Optional[float]   # what that call finishing ITM pays her
    bought: bool = False             # bought outright, not assigned from a put

    @property
    def drop_pct(self) -> Optional[float]:
        """How far the shares sit UNDER what they really cost her, as a
        percentage of that cost. None when they are at or above it, or when
        there is no price to judge by."""
        if self.market_price is None or self.cost_basis <= 0:
            return None
        gap = (self.cost_basis - self.market_price) / self.cost_basis * 100
        return round(gap, 1) if gap > 0 else None

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


def bought_share_price(position) -> Optional[float]:
    """What she paid a share, on a strategy that bought them outright.

    Worked back from shares_cost, which the log stores as the dollar total.
    None on everything else - including a wheel, whose price is the strike.
    """
    if getattr(position, "strategy_key", "") not in BOUGHT_SHARE_STRATEGIES:
        return None
    cost = float(getattr(position, "shares_cost", 0.0) or 0.0)
    if cost <= 0:
        return None
    shares = 100 * max(int(position.contracts or 1), 1)
    return round(cost / shares, 4)


def state_from(position, market_price: Optional[float] = None) -> Optional[WheelState]:
    """The wheel's numbers for an assigned position, or None if not assigned.

    Also for shares bought outright to write calls against (the weekly covered
    call): the price she paid stands in for the strike she was assigned at,
    and everything after that is the same arithmetic.

    premium_collected deliberately counts EVERY side of the story: the put that
    got her assigned (open_credit), every call written since including the
    credits from rolling them, and - on a credit spread she stripped down on
    purpose - what the long put sold for on its way out. All of it is cash this
    trade has banked, so all of it pulls the cost basis down. That is what makes
    the basis fall month after month, and it is the whole reason the strategy is
    worth running.
    """
    strike = getattr(position, "assigned_strike", None)
    bought = False
    if not strike:
        strike = bought_share_price(position)
        bought = strike is not None
    if not strike:
        return None

    contracts = max(int(position.contracts or 1), 1)
    shares = 100 * contracts
    # Lots folded in from other trades each came at their own price, so what a
    # share cost is the average - the same figure thinkorswim shows.
    merges = getattr(position, "share_merges", None) or []
    cost = float(getattr(position, "shares_cost", 0.0) or 0.0)
    if merges and cost > 0:
        strike = round(cost / shares, 4)
    # banked_income, not roll_income: it is roll credits PLUS any leg she sold
    # off on the way here, and both are cash this trade collected.
    banked = getattr(position, "banked_income", None)
    if banked is None:
        banked = position.roll_income
    premium = round(float(position.open_credit or 0.0) + float(banked or 0.0)
                    + sum(m.premium for m in merges), 2)
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
        bought=bought,
    )


def share_lot_candidates(position, others: list) -> list:
    """Other open trades holding the same shares, that could join this one.

    One call can only be written against one trade, so 100 shares from an
    assigned put and 100 bought outright could never carry the 2-contract call
    she actually sold. Same underlying, same book, both holding shares and no
    option open on either - see positions._apply_share_merge for why.
    """
    from src.engine.positions import holds_shares

    if position.status != "open" or position.legs or not holds_shares(position):
        return []
    return [o for o in others
            if o is not position and o.trade_id != position.trade_id
            and o.status == "open" and not o.legs and holds_shares(o)
            and o.underlying.upper() == position.underlying.upper()
            and (o.account or "") == (position.account or "")]


def assignment_cash(strike: float, contracts: int) -> float:
    """What leaves her account when a short put is assigned: she buys the
    shares at the strike, whatever they are worth that morning."""
    return round(-abs(float(strike)) * 100 * max(int(contracts or 1), 1), 2)


def is_wheelable(position) -> bool:
    """Can this position BE assigned into shares?

    Any short put she is prepared to be assigned on - the wheel by name, a
    cash-secured put, which is the same trade with a different intention, and a
    LEAPS put, which is the same trade again with a year on the clock. All three
    of her SOP entries accept assignment rather than defending against it, so
    all three get the button. The LEAPS put page says so explicitly: if assigned,
    hold the shares or move into a covered call, "the same CSP -> Wheel pattern".
    """
    if getattr(position, "assigned_strike", None):
        return False
    if position.status != "open":
        return False
    if position.strategy_key in ("wheel", "cash_secured_put", "leaps_put"):
        return True
    # A credit spread she has stripped the long put off, on purpose, so the
    # short put can assign her. It is not a wheel by name and never will be by
    # shape while the call side of a condor is still open - she said what she
    # was doing when she recorded the fill, and that is what counts.
    if getattr(position, "awaiting_assignment", False):
        return True
    # Older rows carry no strategy key, so fall back to the shape: exactly one
    # short put and nothing else.
    sells = [l for l in position.legs
             if getattr(l.action, "value", l.action) == "sell"]
    return (len(position.legs) == 1 and len(sells) == 1
            and getattr(sells[0].option_type, "value",
                        sells[0].option_type) == "put")
