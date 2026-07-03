"""Upcoming market events that move prices and volatility.

Three kinds:
  - Figured out automatically: monthly options expiration (3rd Friday),
    quarterly "triple witching", and the monthly jobs report (1st Friday).
  - From an editable file (config/economic_calendar.yaml): Fed rate decisions.
  - Real, per-stock (from Yahoo): the next earnings date and ex-dividend date.

The app flags any event that lands inside your trade window, because selling
premium right into a big event is risky (a surprise can blow through your strikes,
and earnings usually crush option prices right after).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAL_FILE = PROJECT_ROOT / "config" / "economic_calendar.yaml"


class Event(BaseModel):
    date: dt.date
    days_away: int
    label: str
    kind: str            # "opex" | "fomc" | "jobs" | "earnings" | "dividend" | "custom"
    note: str = ""
    in_window: bool = False   # does it land inside your trade window?

    @property
    def icon(self) -> str:
        return {"opex": "🗓️", "fomc": "🏦", "jobs": "💼",
                "earnings": "📊", "dividend": "💵", "custom": "📌"}.get(self.kind, "•")


# ---------- deterministic dates ----------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth given weekday of a month (weekday: Mon=0 ... Sun=6)."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def third_friday(year: int, month: int) -> dt.date:
    return _nth_weekday(year, month, 4, 3)   # Friday = 4


def first_friday(year: int, month: int) -> dt.date:
    return _nth_weekday(year, month, 4, 1)


def next_opex(from_date: dt.date) -> dt.date:
    """The next monthly options expiration (3rd Friday) on or after from_date."""
    tf = third_friday(from_date.year, from_date.month)
    if tf >= from_date:
        return tf
    nxt = from_date.replace(day=1) + dt.timedelta(days=32)
    return third_friday(nxt.year, nxt.month)


def next_jobs_report(from_date: dt.date) -> dt.date:
    ff = first_friday(from_date.year, from_date.month)
    if ff >= from_date:
        return ff
    nxt = from_date.replace(day=1) + dt.timedelta(days=32)
    return first_friday(nxt.year, nxt.month)


def is_triple_witching(opex: dt.date) -> bool:
    return opex.month in (3, 6, 9, 12)


# ---------- editable economic calendar ----------
def _load_calendar() -> dict:
    if not CAL_FILE.exists():
        return {}
    with CAL_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _fomc_dates() -> list[dt.date]:
    out = []
    for d in _load_calendar().get("fomc_decisions", []) or []:
        out.append(d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)))
    return out


def _custom_events() -> list[tuple[dt.date, str]]:
    out = []
    for e in _load_calendar().get("custom_events", []) or []:
        try:
            d = e["date"]
            out.append((d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)),
                        str(e.get("label", "Event"))))
        except Exception:
            continue
    return out


# ---------- assemble ----------
def upcoming_events(
    from_date: Optional[dt.date] = None,
    horizon_days: int = 45,
    trade_dte: Optional[int] = None,
    earnings_date: Optional[dt.date] = None,
    ex_div_date: Optional[dt.date] = None,
) -> list[Event]:
    """All notable events between today and `horizon_days` out, soonest first.

    If trade_dte is given, events landing on or before that many days are flagged
    as "in your trade window".
    """
    today = from_date or dt.date.today()
    horizon = today + dt.timedelta(days=horizon_days)
    window_days = trade_dte if trade_dte is not None else horizon_days
    raw: list[Event] = []

    def add(date: dt.date, label: str, kind: str, note: str = "") -> None:
        if date is None or not (today <= date <= horizon):
            return
        days = (date - today).days
        raw.append(Event(date=date, days_away=days, label=label, kind=kind, note=note,
                         in_window=days <= window_days))

    opex = next_opex(today)
    add(opex, "Monthly options expiration" + (" (triple witching)" if is_triple_witching(opex) else ""),
        "opex", "Pinning and bigger moves are common around expiration.")
    add(next_jobs_report(today), "Monthly jobs report", "jobs",
        "Can jolt the market at 8:30am that morning.")
    for d in _fomc_dates():
        add(d, "Fed interest-rate decision (FOMC)", "fomc",
            "Big volatility event - be cautious selling premium right into it.")
    for d, label in _custom_events():
        add(d, label, "custom")
    if earnings_date:
        add(earnings_date, "Earnings report", "earnings",
            "Options usually get crushed right after earnings - avoid selling premium through it.")
    if ex_div_date:
        add(ex_div_date, "Ex-dividend date", "dividend",
            "Assignment risk rises on short calls around the ex-dividend date.")

    raw.sort(key=lambda e: e.date)
    return raw
