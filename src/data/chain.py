"""A normalized option chain the whole app understands.

Schwab's raw API response gets converted into these simple objects
(see schwab_client.py), so the scanner never has to know Schwab's format.
The same objects load from a saved JSON fixture, which is how the scanner is
tested without any live connection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from src.engine.models import OptionType


class OptionContract(BaseModel):
    """One tradable option (one strike, one expiration)."""

    option_type: OptionType
    strike: float
    expiration: str                  # "YYYY-MM-DD"
    dte: int                         # days to expiration
    delta: float = 0.0               # per share, signed (puts negative)
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0                  # implied volatility, e.g. 0.15 = 15%
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0                  # contracts traded today (liquidity)
    open_interest: int = 0           # contracts currently open (liquidity)

    @property
    def mid(self) -> float:
        """Mid price - the fair estimate between what buyers bid and sellers ask."""
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 2)
        return round(self.ask or self.bid, 2)

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)


class OptionChain(BaseModel):
    """All the contracts for one underlying, plus its current price."""

    underlying: str
    underlying_price: float
    fetched_at: Optional[str] = None
    contracts: list[OptionContract] = Field(default_factory=list)

    # ---- filtering helpers the scanner uses ----
    def expirations(self) -> list[str]:
        return sorted({c.expiration for c in self.contracts})

    def dtes(self) -> list[int]:
        return sorted({c.dte for c in self.contracts})

    def nearest_dte(self, target: int) -> Optional[int]:
        dtes = self.dtes()
        return min(dtes, key=lambda d: abs(d - target)) if dtes else None

    def by(self, option_type: OptionType, dte: int) -> list[OptionContract]:
        rows = [c for c in self.contracts if c.option_type == option_type and c.dte == dte]
        return sorted(rows, key=lambda c: c.strike)

    def find(self, option_type: OptionType, dte: int, strike: float) -> Optional[OptionContract]:
        for c in self.contracts:
            if c.option_type == option_type and c.dte == dte and abs(c.strike - strike) < 1e-6:
                return c
        return None

    # ---- persistence (for fixtures / caching) ----
    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "OptionChain":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
