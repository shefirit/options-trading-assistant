"""Which expiration the premium read is built on.

The CBOE feed mixes thin weeklies in with the real expirations: a couple of
dozen strikes hugging the money and no deltas at all. Picking purely by "closest
to the target DTE" landed on one of those for SPY at 45 DTE - 29 puts, a 735-763
band against a 742.97 spot, every delta 0.0 - and the premium panel came back
"No sellable put found" for one of the most liquid names on the board.

So the trade-picking path searches a window around the target and only accepts
an expiration it can actually work with. Position pricing is deliberately NOT
part of that: an open trade sits on one specific expiration and must be priced
on that one, thin or not.

All fixtures are hand-built - no network.
"""

from __future__ import annotations

from src.data import cache
from src.data.chain import OptionChain, OptionContract
from src.data.provider import DataProvider
from src.engine.models import OptionType

SPOT = 742.97


def _thin_weekly(dte: int, exp: str) -> list[OptionContract]:
    """The SPY 2026-09-04 shape: a narrow band around spot, no deltas at all."""
    return [OptionContract(option_type=OptionType.PUT, strike=float(s), expiration=exp,
                           dte=dte, delta=0.0, bid=1.0, ask=1.2)
            for s in range(735, 764)]        # 29 puts, none below 735


def _deep_ladder(dte: int, exp: str) -> list[OptionContract]:
    """A real monthly: strikes well below spot, deltas that mean something."""
    out = []
    for s in range(620, 781, 5):
        moneyness = (SPOT - s) / SPOT
        delta = max(0.02, 0.50 - moneyness * 2.2)
        out.append(OptionContract(option_type=OptionType.PUT, strike=float(s),
                                  expiration=exp, dte=dte, delta=-delta,
                                  iv=0.18, bid=2.0, ask=2.2, open_interest=4000))
        out.append(OptionContract(option_type=OptionType.CALL, strike=float(s),
                                  expiration=exp, dte=dte, delta=delta,
                                  iv=0.18, bid=2.0, ask=2.2, open_interest=4000))
    return out


def _chain(*groups) -> OptionChain:
    contracts = [c for g in groups for c in g]
    return OptionChain(underlying="SPY", underlying_price=SPOT, contracts=contracts)


# ---------- the picker itself ----------
def test_a_thin_weekly_sitting_on_the_target_is_passed_over():
    chain = _chain(_thin_weekly(45, "2026-09-04"), _deep_ladder(52, "2026-09-11"))
    picked = DataProvider._tradable_expiration(chain, 45)
    assert picked.expirations() == ["2026-09-11"]


def test_the_nearest_usable_expiration_wins_among_several():
    chain = _chain(_thin_weekly(45, "2026-09-04"),
                   _deep_ladder(38, "2026-08-28"),
                   _deep_ladder(52, "2026-09-11"))
    # 38 and 52 are both usable and both 7 days off - ties resolve by date, so
    # the pick is stable rather than dependent on dict ordering.
    picked = DataProvider._tradable_expiration(chain, 45)
    assert picked.expirations() == ["2026-08-28"]

    chain = _chain(_thin_weekly(45, "2026-09-04"),
                   _deep_ladder(35, "2026-08-25"),
                   _deep_ladder(48, "2026-09-07"))
    assert DataProvider._tradable_expiration(chain, 45).expirations() == ["2026-09-07"]


def test_usable_expirations_outside_the_window_are_not_reached_for():
    """A fat chain 40 days from the target is the wrong trade, not a rescue."""
    chain = _chain(_thin_weekly(45, "2026-09-04"), _deep_ladder(85, "2026-10-16"))
    picked = DataProvider._tradable_expiration(chain, 45, window=15)
    assert picked.expirations() == ["2026-09-04"]      # falls back to nearest


def test_a_chain_with_nothing_usable_still_returns_the_nearest():
    """Better a thin chain the caller can judge than an empty one."""
    chain = _chain(_thin_weekly(45, "2026-09-04"), _thin_weekly(52, "2026-09-11"))
    picked = DataProvider._tradable_expiration(chain, 45)
    assert picked.expirations() == ["2026-09-04"]
    assert picked.underlying_price == SPOT            # metadata survives the slice


def test_an_empty_chain_is_handed_back_untouched():
    empty = OptionChain(underlying="SPY", underlying_price=0.0, contracts=[])
    assert DataProvider._tradable_expiration(empty, 45).contracts == []


def test_what_makes_an_expiration_unusable():
    """Each rejection reason on its own, so a future tweak can't quietly drop one."""
    thin = _thin_weekly(45, "2026-09-04")
    assert DataProvider._is_tradable(thin, SPOT) is False          # no deltas, narrow band
    assert DataProvider._is_tradable(_deep_ladder(45, "x"), SPOT) is True

    # too few strikes, even with good deltas and reach
    stub = _deep_ladder(45, "x")[:10]
    assert DataProvider._is_tradable(stub, SPOT) is False

    # a full ladder with real reach but every delta zeroed out by the feed
    no_deltas = [c.model_copy(update={"delta": 0.0}) for c in _deep_ladder(45, "x")]
    assert DataProvider._is_tradable(no_deltas, SPOT) is False

    # deltas fine, but the strikes stop short of where the sold put sits
    high_only = [c for c in _deep_ladder(45, "x") if c.strike > SPOT * 0.97]
    assert DataProvider._is_tradable(high_only, SPOT) is False


# ---------- the premium read end to end ----------
def _cboe_returns(monkeypatch, chain: OptionChain) -> None:
    cache.clear()
    monkeypatch.setattr("src.data.cboe_client.get_option_chain", lambda *a, **k: chain)


def test_premium_snapshot_finds_a_put_where_it_used_to_come_up_empty(monkeypatch):
    """The reported failure, end to end: SPY at 45 DTE returned 'No sellable put
    found' because the pick landed on the thin weekly."""
    from src.data import yfinance_client as yc

    _cboe_returns(monkeypatch, _chain(_thin_weekly(45, "2026-09-04"),
                                      _deep_ladder(52, "2026-09-11")))
    monkeypatch.setattr(yc, "get_history_closes", lambda *a, **k: [700.0 + i for i in range(130)])
    monkeypatch.setattr(yc, "get_calendar_dates", lambda *a, **k: (None, None))

    snap = DataProvider("yahoo").get_premium_snapshot("SPY", target_dte=45)

    assert snap.error == ""
    assert snap.short_strike and snap.credit_dollars
    assert snap.dte == 52                       # the real monthly, not the weekly


def test_the_old_nearest_pick_is_what_broke_it(monkeypatch):
    """Pinning the bug itself: the plain nearest-expiration pick still produces
    the empty snapshot, which is why the trade-picking path no longer uses it."""
    from src.data import premium_finder

    chain = _chain(_thin_weekly(45, "2026-09-04"), _deep_ladder(52, "2026-09-11"))
    thin = DataProvider._nearest_expiration(chain, 45)
    assert premium_finder.snapshot("SPY", thin, hv=0.15).error == "No sellable put found."


# ---------- open positions must NOT be re-pointed ----------
def test_pricing_an_open_position_stays_on_its_own_expiration(monkeypatch):
    """Her trade is on the thin weekly. Swapping in a neighbouring expiration
    would price a contract she does not hold."""
    _cboe_returns(monkeypatch, _chain(_thin_weekly(45, "2026-09-04"),
                                      _deep_ladder(52, "2026-09-11")))
    chain = DataProvider("yahoo")._expiration_chain("SPY", 45)
    assert chain.expirations() == ["2026-09-04"]


def test_the_trade_picking_path_asks_for_a_tradable_one(monkeypatch):
    _cboe_returns(monkeypatch, _chain(_thin_weekly(45, "2026-09-04"),
                                      _deep_ladder(52, "2026-09-11")))
    chain = DataProvider("yahoo")._expiration_chain("SPY", 45, tradable=True)
    assert chain.expirations() == ["2026-09-11"]


def test_leaps_lookup_skips_a_thin_far_dated_week(monkeypatch):
    _cboe_returns(monkeypatch, _chain(_thin_weekly(210, "2027-02-19"),
                                      _deep_ladder(217, "2027-02-26")))
    leaps = DataProvider("yahoo").get_leaps_chain("SPY", target_dte=210)
    assert leaps.expirations() == ["2027-02-26"]
