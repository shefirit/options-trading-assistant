"""Is the U.S. stock market open today?

The market is closed on weekends and on the ~10 NYSE holidays each year. When a
fixed-date holiday lands on a Saturday it is observed the Friday before; on a
Sunday, the Monday after. This module computes those dates so the app can say
"market closed today" instead of quietly showing yesterday's prices as if they
were live - which is exactly what confused things on a holiday.

No network needed - it is all date math, so it works offline and on the cloud.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Optional


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The nth given weekday of a month (weekday: Mon=0 ... Sun=6)."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """The last given weekday of a month."""
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    """Easter Sunday (anonymous Gregorian algorithm) - Good Friday is 2 days before."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _observed(d: dt.date) -> dt.date:
    """NYSE rule: a Saturday holiday is observed Friday; a Sunday one, Monday."""
    if d.weekday() == 5:      # Saturday -> Friday
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:      # Sunday -> Monday
        return d + dt.timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> dict:
    """{date: name} of the days the U.S. stock market is fully closed that year."""
    good_friday = _easter(year) - dt.timedelta(days=2)
    return {
        _observed(dt.date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        good_friday: "Good Friday",
        _last_weekday(year, 5, 0): "Memorial Day",
        _observed(dt.date(year, 6, 19)): "Juneteenth",
        _observed(dt.date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        _observed(dt.date(year, 12, 25)): "Christmas",
    }


def _today(d: Optional[dt.date]) -> dt.date:
    return d or dt.date.today()


def holiday_name(d: Optional[dt.date] = None) -> Optional[str]:
    d = _today(d)
    return holidays(d.year).get(d)


def is_weekend(d: Optional[dt.date] = None) -> bool:
    return _today(d).weekday() >= 5


def is_market_open(d: Optional[dt.date] = None) -> bool:
    """True on a normal trading day (not a weekend, not a full-close holiday)."""
    d = _today(d)
    return not is_weekend(d) and d not in holidays(d.year)


def closed_reason(d: Optional[dt.date] = None) -> Optional[str]:
    """Plain-English why the market is closed, or None if it's a trading day."""
    d = _today(d)
    name = holidays(d.year).get(d)
    if name:
        return name
    if d.weekday() == 5:
        return "the weekend"
    if d.weekday() == 6:
        return "the weekend"
    return None


def next_market_open(d: Optional[dt.date] = None) -> dt.date:
    """The next trading day strictly after d (skips weekends + holidays)."""
    nd = _today(d)
    for _ in range(15):
        nd = nd + dt.timedelta(days=1)
        if is_market_open(nd):
            return nd
    return nd


def last_market_open(d: Optional[dt.date] = None) -> dt.date:
    """The most recent trading day on or before d."""
    nd = _today(d)
    for _ in range(15):
        if is_market_open(nd):
            return nd
        nd = nd - dt.timedelta(days=1)
    return nd


def adjust_back_if_closed(d: dt.date) -> dt.date:
    """If a date lands on a closed day, move it to the trading day just before.

    Used for events that shift earlier when their usual day is a holiday - e.g.
    monthly options expiration moves to the Thursday, and the jobs report is
    released the day before a holiday Friday.
    """
    nd = d
    for _ in range(15):
        if is_market_open(nd):
            return nd
        nd = nd - dt.timedelta(days=1)
    return d
