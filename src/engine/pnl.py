"""Money banked, bucketed by day, by week or by month.

THE PROBLEM THIS SOLVES
-----------------------
The day/week/month picture already existed in pieces, and every piece was
somewhere she could not get to it. `month_report.days()` built a full day
series that only ever rendered inside a collapsed expander, inside one month,
and returned nothing at all for all-time. `month_report._weeks()` only ran
inside a single month's income report. Nothing anywhere let her change the
zoom.

One function now answers "what did I make in this period" at every grain, so
Day, Week and Month are the same code path and cannot drift apart.

THE RULE THAT RUNS THROUGH IT
-----------------------------
`banked` is the book the account switch selected. `back` is the other one.
There is deliberately no field that holds their sum - the same invariant the
charts are built on (see ui/trades/charts.py). A chart cannot draw a combined
bar because the number is not in its data.

TWO TARGETS, NOT ONE
--------------------
`target` is what the whole period is worth against her plan - a finished month
is the full monthly goal. `pace_target` is that same target prorated to today,
so the month in progress is measured against where a steady plan would be NOW
rather than against a number the month has not had time to earn. A finished
period has the two equal; the current one does not, and that gap is the point.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from src.engine import goals
from src.engine import month_report as mr
from src.engine.positions import Position, cash_events

GRAINS = ("day", "week", "month")

# How many buckets each grain shows by default before it stops being readable.
# Ninety daily bars on a 375px phone is a barcode, not a chart. Months are
# uncapped: a year of months is twelve bars and the whole point is the trend.
DEFAULT_LIMIT = {"day": 45, "week": 26, "month": None}

_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _dm(d: date) -> str:
    """Day before month, the way she reads dates in Israel - 29/6, not 6/29."""
    return f"{d.day}/{d.month}"


def _bucket_start(d: date, grain: str) -> date:
    if grain == "day":
        return d
    if grain == "week":
        return mr.week_start(d)
    return d.replace(day=1)


def _bucket_end(start: date, grain: str) -> date:
    if grain == "day":
        return start
    if grain == "week":
        return start + timedelta(days=6)
    return goals._next_month(start) - timedelta(days=1)


def _advance(start: date, grain: str) -> date:
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    return goals._next_month(start)


def _label(start: date, end: date, grain: str) -> str:
    if grain == "day":
        return f"{_DOW[start.weekday()]} {_dm(start)}"
    if grain == "week":
        return f"{_dm(start)} - {_dm(end)}"
    return start.strftime("%B %Y")


def _short(start: date, grain: str) -> str:
    """The axis label, which has far less room than the tooltip."""
    if grain == "month":
        return start.strftime("%b")
    return _dm(start)


def buckets(positions: list[Position], grain: str = "month",
            settings: Optional[dict] = None,
            live_from: Optional[date] = None,
            today: Optional[date] = None,
            mode: str = "real",
            limit: Optional[int] = -1) -> list[dict[str, Any]]:
    """Every period in the span, the empty ones included, oldest first.

    Empty periods have to be in the list rather than missing from it. A month
    drawn only on the days that earned is a scatter of green with no shape, and
    the shape is the question - does her income cluster near expiry, or is it
    spread out?

    limit trims to the most recent N buckets. -1 means "use the sensible
    default for this grain"; None means every bucket. The running total is
    computed over the WHOLE span before trimming, so a trimmed view still shows
    a true cumulative rather than one that restarts at the left edge.
    """
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}, got {grain!r}")
    today = today or date.today()
    monthly = goals.targets_from(settings)["monthly"] if settings else 0.0
    if limit == -1:
        limit = DEFAULT_LIMIT[grain]

    books = mr.split_by_mode(positions, live_from)
    other = "practice" if mode == "real" else "real"
    fore, back_pos = books[mode], books[other]

    fore_cash = cash_events(fore)
    back_cash = cash_events(back_pos)
    premium = mr.premium_events(fore, mr.ALL_TIME)

    dated = ([e["date"] for e in fore_cash] + [e["date"] for e in back_cash]
             + [e["date"] for e in premium]
             + [p.opened for p in fore + back_pos if p.opened])
    if not dated:
        return []

    # The span covers BOTH books so the faded backdrop is drawn against the
    # same axis as the foreground - a backdrop on a shorter axis would read as
    # the other book having stopped.
    first = _bucket_start(min(dated), grain)
    last = _bucket_start(max(max(dated), today), grain)

    # The plan starts the day she funded the real book. The practice book has
    # no plan, so its ramp is measured from its own first trade - the same
    # convention goals.cumulative_series uses, and the two must not disagree.
    plan_start = live_from if (mode == "real" and live_from) else min(dated)

    rows: dict[date, dict[str, Any]] = {}
    cursor = first
    while cursor <= last:
        end = _bucket_end(cursor, grain)
        rows[cursor] = {
            "key": cursor.isoformat(), "start": cursor, "end": end,
            "label": _label(cursor, end, grain), "short": _short(cursor, grain),
            "grain": grain,
            "banked": 0.0, "back": 0.0, "premium": 0.0,
            "opened": 0, "closed": 0,
            "is_current": cursor <= today <= end,
            "is_future": cursor > today,
        }
        cursor = _advance(cursor, grain)

    def slot(d: Optional[date]) -> Optional[dict[str, Any]]:
        return rows.get(_bucket_start(d, grain)) if d else None

    for e in fore_cash:
        r = slot(e["date"])
        if r is not None:
            r["banked"] += e["amount"]
    for e in back_cash:
        r = slot(e["date"])
        if r is not None:
            r["back"] += e["amount"]
    for e in premium:
        r = slot(e["date"])
        if r is not None:
            r["premium"] += e["amount"]
    for p in fore:
        r = slot(p.opened)
        if r is not None:
            r["opened"] += 1
        if p.status == "closed":
            r = slot(p.closed_on)
            if r is not None:
                r["closed"] += 1

    out, running = [], 0.0
    for start in sorted(rows):
        r = rows[start]
        r["banked"] = round(r["banked"], 2)
        r["back"] = round(r["back"], 2)
        r["premium"] = round(r["premium"], 2)
        running += r["banked"]
        r["cumulative"] = round(running, 2)
        r["target"] = _target(monthly, plan_start, r["start"], r["end"])
        r["pace_target"] = _target(monthly, plan_start, r["start"],
                                   min(r["end"], today))
        out.append(r)

    return out[-limit:] if limit else out


def _target(monthly: float, plan_start: date, start: date, end: date) -> float:
    """What a steady plan would produce between two dates.

    Built by difference over goals.elapsed_target rather than by a fresh
    day-count, so a week straddling two months of different lengths prorates
    exactly the way every other target in the app does. There is one definition
    of "what should I have by now" and this is not a second one.
    """
    if monthly <= 0 or end < start or end < plan_start:
        return 0.0
    lo = max(start, plan_start)
    before = goals.elapsed_target(monthly, plan_start, lo - timedelta(days=1))
    through = goals.elapsed_target(monthly, plan_start, end)
    return round(max(through - before, 0.0), 2)


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The handful of numbers worth printing above the chart.

    Averages are per ACTIVE bucket, not per bucket. "You average $260 a day"
    counted over every calendar day including the weekends and the quiet weeks
    is a smaller, sadder and less useful number than "on the days money
    settled, you averaged $260" - and only the second one says anything about
    her trading.
    """
    past = [r for r in rows if not r["is_future"]]
    active = [r for r in past if r["banked"]]
    banked = round(sum(r["banked"] for r in past), 2)

    best = max(active, key=lambda r: r["banked"], default=None)
    worst = min(active, key=lambda r: r["banked"], default=None)

    # Counted backwards from the most recent bucket that actually settled
    # money. Counting from the last bucket would break every streak on a quiet
    # Sunday, which is not information about anything.
    streak = 0
    for r in reversed(active):
        if r["banked"] > 0:
            streak += 1
        else:
            break

    return {
        "grain": rows[0]["grain"] if rows else "month",
        "banked": banked,
        "premium": round(sum(r["premium"] for r in past), 2),
        "target": round(sum(r["target"] for r in past), 2),
        "pace_target": round(sum(r["pace_target"] for r in past), 2),
        "opened": sum(r["opened"] for r in past),
        "closed": sum(r["closed"] for r in past),
        "buckets": len(past),
        "active": len(active),
        "average": round(banked / len(active), 2) if active else None,
        "best": best,
        "worst": worst,
        "win_streak": streak,
        "cumulative": past[-1]["cumulative"] if past else 0.0,
    }


def current(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The period she is living in right now - the hero number's row."""
    for r in rows:
        if r["is_current"]:
            return r
    past = [r for r in rows if not r["is_future"]]
    return past[-1] if past else None
