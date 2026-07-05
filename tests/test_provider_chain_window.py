"""How the provider sources option chains: CBOE (free, no key) is the default on
the hosted app, filtered to the DTE window in-memory; Yahoo is the fallback and
only then is the narrow fetch window forwarded to it."""

from __future__ import annotations

from src.data import cache
from src.data.chain import OptionChain, OptionContract
from src.data.provider import DataProvider
from src.engine.models import OptionType


def _contract(dte: int, strike: float = 100.0):
    exp = f"2026-{(dte % 12) + 1:02d}-15"
    return OptionContract(option_type=OptionType.PUT, strike=strike, expiration=exp,
                          dte=dte, delta=-0.2, bid=1.0, ask=1.2)


def _full_chain():
    return OptionChain(underlying="SPX", underlying_price=5000.0,
                       contracts=[_contract(d) for d in (10, 25, 40, 60)])


def test_cboe_is_the_default_source_and_gets_filtered(monkeypatch):
    cache.clear()
    calls = {"cboe": 0, "yahoo": 0}

    def cboe_chain(underlying, from_dte=0, to_dte=3650):
        calls["cboe"] += 1
        return _full_chain()
    monkeypatch.setattr("src.data.cboe_client.get_option_chain", cboe_chain)
    monkeypatch.setattr("src.data.yfinance_client.get_option_chain",
                        lambda *a, **k: calls.__setitem__("yahoo", calls["yahoo"] + 1) or _full_chain())

    provider = DataProvider("yahoo")
    chain = provider.get_chain("SPX", dte_min=21, dte_max=45)

    assert calls["cboe"] == 1 and calls["yahoo"] == 0     # CBOE preferred, Yahoo untouched
    assert sorted(c.dte for c in chain.contracts) == [25, 40]   # window applied in-memory


def test_falls_back_to_yahoo_when_cboe_empty(monkeypatch):
    cache.clear()
    forwarded = []
    monkeypatch.setattr("src.data.cboe_client.get_option_chain",
                        lambda *a, **k: OptionChain(underlying="SPX", underlying_price=0.0, contracts=[]))
    monkeypatch.setattr(
        "src.data.yfinance_client.get_option_chain",
        lambda underlying, from_dte=15, to_dte=70: forwarded.append((from_dte, to_dte)) or _full_chain())

    provider = DataProvider("yahoo")
    provider.get_chain("SPX", dte_min=14, dte_max=52)
    assert forwarded == [(14, 52)]      # the narrow window is forwarded to the Yahoo fallback
