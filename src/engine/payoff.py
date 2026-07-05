"""Profit/loss at expiration for a proposed trade - the "payoff picture".

Pure math (no data fetch, no Streamlit) so it is fully unit-tested. All dollar
values are for the WHOLE position (x100, x contracts). The picture shows what
you make or lose at each underlying price if held to expiration; your actual
exits (50% target, 21 DTE) normally happen earlier.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.engine.models import Action, OptionType, Trade


class PayoffProfile(BaseModel):
    prices: list[float] = Field(default_factory=list)   # underlying prices (x-axis)
    values: list[float] = Field(default_factory=list)   # P&L in dollars (y-axis)
    breakevens: list[float] = Field(default_factory=list)
    max_profit: float = 0.0        # best case on the chart
    max_loss: float = 0.0          # worst case on the chart (negative number)
    loss_grows_below: bool = False  # losses keep growing below the chart's left edge
    loss_grows_above: bool = False  # ... above the right edge
    includes_shares: bool = False   # covered call: 100 shares are part of the math
    approximate: bool = False       # PMCC: the far-dated LEAPS is estimated


def _leg_value_at(leg, price: float) -> float:
    """This leg's per-share P&L if the underlying finishes at `price`."""
    if leg.option_type == OptionType.CALL:
        intrinsic = max(price - leg.strike, 0.0)
    else:
        intrinsic = max(leg.strike - price, 0.0)
    if leg.action == Action.BUY:
        return (intrinsic - leg.premium) * leg.quantity
    return (leg.premium - intrinsic) * leg.quantity


def value_at(trade: Trade, price: float, include_shares: bool = False) -> float:
    """Whole-position P&L in dollars if the underlying finishes at `price`."""
    per_share = sum(_leg_value_at(leg, price) for leg in trade.legs)
    if include_shares and trade.underlying_price:
        per_share += price - trade.underlying_price
    return per_share * 100 * trade.contracts


def _grid(trade: Trade, points: int) -> list[float]:
    strikes = [leg.strike for leg in trade.legs if leg.strike > 0]
    anchor = [s for s in strikes]
    if trade.underlying_price:
        anchor.append(trade.underlying_price)
    if not anchor:
        return []
    lo_a, hi_a = min(anchor), max(anchor)
    pad = max(hi_a - lo_a, hi_a * 0.06)
    lo = max(lo_a - pad, 0.0)
    hi = hi_a + pad
    step = (hi - lo) / (points - 1)
    return [round(lo + i * step, 4) for i in range(points)]


def _breakevens(prices: list[float], values: list[float]) -> list[float]:
    """Where the P&L line crosses zero (straight-line interpolation)."""
    out = []
    for (x1, y1), (x2, y2) in zip(zip(prices, values), zip(prices[1:], values[1:])):
        if y1 == 0.0:
            out.append(round(x1, 2))
        elif (y1 < 0 < y2) or (y1 > 0 > y2):
            out.append(round(x1 + (0 - y1) * (x2 - x1) / (y2 - y1), 2))
    if values and values[-1] == 0.0:
        out.append(round(prices[-1], 2))
    return sorted(set(out))


def profile(trade: Trade, strategy: dict, points: int = 121) -> Optional[PayoffProfile]:
    """The payoff picture for a trade, or None if it can't be drawn
    (no strikes yet, or a covered call without the share price)."""
    include_shares = bool(strategy.get("requires_shares"))
    if include_shares and not trade.underlying_price:
        return None
    prices = _grid(trade, points)
    if not prices:
        return None

    values = [round(value_at(trade, p, include_shares), 2) for p in prices]
    return PayoffProfile(
        prices=prices,
        values=values,
        breakevens=_breakevens(prices, values),
        max_profit=max(values),
        max_loss=min(values),
        loss_grows_below=(values[0] < 0 and values[0] < values[1]),
        loss_grows_above=(values[-1] < 0 and values[-1] < values[-2]),
        includes_shares=include_shares,
        approximate=(strategy.get("family") == "diagonal"),
    )
