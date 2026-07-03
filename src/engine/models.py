"""Core data models shared by the scanner, validator, and UI.

Plain-English reminders used throughout:
  - "long" leg  = an option you BUY  (shows as +qty in thinkorswim)
  - "short" leg = an option you SELL (shows as -qty in thinkorswim)
  - delta is roughly the chance an option finishes in the money, and also
    how much the option moves when the stock moves $1. Calls have positive
    delta, puts have negative delta.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class CheckStatus(str, Enum):
    PASS = "pass"    # rule satisfied
    FAIL = "fail"    # rule broken - do not enter
    WARN = "warn"    # allowed, but pay attention
    INFO = "info"    # reminder, not a pass/fail test


class Leg(BaseModel):
    """One option in a trade (a covered call also has a shares leg handled separately)."""

    role: str                         # e.g. "short_put", "long_call_leaps"
    action: Action
    option_type: OptionType
    strike: float
    # Per-share delta straight from the option chain: puts negative, calls positive.
    delta: float = 0.0
    # Mid price per share (what one contract costs / pays, before x100).
    premium: float = 0.0
    quantity: int = 1                 # contracts of THIS leg per 1 unit of the position
    dte: Optional[int] = None         # days to expiration for this leg

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)

    @property
    def signed_cashflow(self) -> float:
        """Per share: positive = you receive money (sell), negative = you pay (buy)."""
        sign = 1.0 if self.action == Action.SELL else -1.0
        return sign * self.premium * self.quantity

    @property
    def position_delta_contribution(self) -> float:
        """Per-share delta this leg adds to the whole position.

        Selling flips the sign: a short call (positive-delta option you sold)
        makes your position delta negative.
        """
        sign = 1.0 if self.action == Action.BUY else -1.0
        return sign * self.delta * self.quantity


class Trade(BaseModel):
    """A proposed trade: which strategy, which underlying, the legs, and size."""

    strategy_key: str
    underlying: str
    legs: list[Leg] = Field(default_factory=list)
    contracts: int = 1                # how many copies of the whole position
    underlying_price: Optional[float] = None

    # ---- money math (per share unless noted) ----
    @property
    def net_credit_per_share(self) -> float:
        """Positive = you collect a credit. Negative = you pay a debit."""
        return sum(leg.signed_cashflow for leg in self.legs)

    @property
    def net_credit_total(self) -> float:
        """Total dollars collected (or paid, if negative) for the whole position."""
        return self.net_credit_per_share * 100 * self.contracts

    @property
    def is_credit(self) -> bool:
        return self.net_credit_per_share > 0

    @property
    def dte(self) -> Optional[int]:
        """The nearest expiration in the trade - what your exit rules count down to."""
        dtes = [leg.dte for leg in self.legs if leg.dte is not None]
        return min(dtes) if dtes else None

    @property
    def short_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.action == Action.SELL]

    @property
    def long_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.action == Action.BUY]

    @property
    def net_position_delta(self) -> float:
        """Whole-position delta in share-equivalents (x100 x contracts)."""
        per_share = sum(leg.position_delta_contribution for leg in self.legs)
        return per_share * 100 * self.contracts

    def vertical_width(self, option_type: OptionType) -> Optional[float]:
        """Distance between the short and long strike on one side (put side or call side)."""
        legs = [leg for leg in self.legs if leg.option_type == option_type]
        if len(legs) < 2:
            return None
        strikes = [leg.strike for leg in legs]
        return abs(max(strikes) - min(strikes))


class CheckResult(BaseModel):
    """One line in the SOP checklist the user sees (green / red / etc.)."""

    name: str
    status: CheckStatus
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None

    @property
    def icon(self) -> str:
        return {
            CheckStatus.PASS: "✅",   # green check
            CheckStatus.FAIL: "❌",   # red x
            CheckStatus.WARN: "⚠️",  # warning
            CheckStatus.INFO: "ℹ️",  # info
        }[self.status]


class ValidationReport(BaseModel):
    """The full result of checking a trade against its strategy SOP."""

    strategy_key: str
    strategy_name: str
    underlying: str
    results: list[CheckResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only if nothing FAILED (warnings are allowed)."""
        return not any(r.status == CheckStatus.FAIL for r in self.results)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.FAIL)

    @property
    def n_warned(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.WARN)


class Candidate(BaseModel):
    """A trade the scanner found, plus the numbers that help rank it."""

    trade: Trade
    credit: float                 # net credit for the whole position (dollars)
    max_loss: float               # worst-case dollars at risk
    buying_power: float           # buying power the position ties up
    return_on_risk: float         # credit / max_loss (higher is richer premium)
    short_delta: float            # abs delta of the short leg(s), for a quick read
    dte: Optional[int] = None
    # True = obeys every SOP rule. False = shown for context (e.g. delta is a
    # touch over your limit, like 0.12 when your rule says 0.10).
    fits_sop: bool = True
    note: str = ""
