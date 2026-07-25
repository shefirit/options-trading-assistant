"""Tests for the upcoming-events engine (deterministic dates, no network)."""

from __future__ import annotations

import datetime as dt

from src.data import market_events as me


def test_third_friday():
    # July 2026: 3rd Friday is the 17th.
    assert me.third_friday(2026, 7) == dt.date(2026, 7, 17)


def test_first_friday():
    assert me.first_friday(2026, 7) == dt.date(2026, 7, 3)


def test_next_opex_rolls_to_next_month():
    # After the 3rd Friday, next opex is next month's.
    after = dt.date(2026, 7, 18)
    assert me.next_opex(after) == me.third_friday(2026, 8)


def test_triple_witching_quarters_only():
    assert me.is_triple_witching(dt.date(2026, 9, 18)) is True
    assert me.is_triple_witching(dt.date(2026, 7, 17)) is False


def test_upcoming_includes_opex_and_flags_window():
    today = dt.date(2026, 7, 1)
    events = me.upcoming_events(from_date=today, horizon_days=45, trade_dte=30)
    kinds = {e.kind for e in events}
    assert "opex" in kinds
    # Every event is within the horizon and sorted by date.
    assert events == sorted(events, key=lambda e: e.date)
    assert all(0 <= e.days_away <= 45 for e in events)


def test_earnings_in_window_is_flagged():
    today = dt.date(2026, 7, 1)
    earnings = dt.date(2026, 7, 20)   # 19 days out, inside a 30-day window
    events = me.upcoming_events(from_date=today, trade_dte=30, earnings_date=earnings)
    earn = [e for e in events if e.kind == "earnings"]
    assert earn and earn[0].in_window is True


def test_far_earnings_not_in_window():
    today = dt.date(2026, 7, 1)
    earnings = dt.date(2026, 8, 10)   # ~40 days out, beyond a 30-day window
    events = me.upcoming_events(from_date=today, trade_dte=30, earnings_date=earnings, horizon_days=60)
    earn = [e for e in events if e.kind == "earnings"]
    assert earn and earn[0].in_window is False


def test_fomc_dates_loaded_from_config():
    # The seeded 2026 calendar should surface a Fed decision within a wide horizon.
    events = me.upcoming_events(from_date=dt.date(2026, 7, 1), horizon_days=120)
    assert any(e.kind == "fomc" for e in events)


def test_economic_releases_loaded_and_flagged():
    # The seeded 2026 CPI/PCE/GDP releases should surface with why-it-matters notes.
    events = me.upcoming_events(from_date=dt.date(2026, 7, 10), horizon_days=60, trade_dte=35)
    kinds = {e.kind for e in events}
    assert {"cpi", "pce", "gdp"} <= kinds
    # July 14 CPI is 4 days out - inside a 35-day window.
    cpi = [e for e in events if e.kind == "cpi"]
    assert cpi and cpi[0].in_window is True
    # Every release carries a plain-English note.
    assert all(e.note for e in events if e.kind in ("cpi", "pce", "gdp"))


# ---------- which events actually earn a warning ----------
def _ev(kind: str, days_away: int = 5, in_window: bool = True):
    import datetime as dt

    from src.data.market_events import Event
    return Event(date=dt.date(2026, 8, 1), days_away=days_away, label=kind.upper(),
                 kind=kind, in_window=in_window)


def test_only_the_real_movers_warn_inside_the_window():
    # Macro events land roughly weekly, so "inside your trade window" was true
    # for every row - a warning on everything is a warning on nothing.
    for kind in ("fomc", "cpi", "jobs", "earnings"):
        assert _ev(kind).is_warning, f"{kind} should warn inside the window"
    for kind in ("opex", "pce", "gdp", "dividend"):
        assert not _ev(kind).is_warning, f"{kind} should not shout"


def test_a_big_mover_outside_the_window_does_not_warn():
    assert not _ev("fomc", days_away=60, in_window=False).is_warning


def test_every_event_kind_has_an_impact_level():
    for kind in ("opex", "fomc", "jobs", "cpi", "pce", "gdp", "earnings",
                 "dividend", "custom"):
        assert _ev(kind).impact in ("high", "medium", "low")
