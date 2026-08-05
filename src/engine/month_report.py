"""The month's income report - a whole month on one screen.

The rest of the "My trades" tab answers "what do I do today" and "am I on
pace". This answers the question that only makes sense once a month is behind
you: where did the money actually come from, and how well did I trade?

Everything is derived from the same event log the tracker already reads, so
there is nothing extra to type.

TWO NUMBERS, AND THEY ARE NOT THE SAME
--------------------------------------
  premium sold   every dollar of premium sold during the month. It is the
                 number the trading world puts in its headline, and on its own
                 it is a vanity number - it says nothing about what buying the
                 positions back cost, and nothing about the losers.
  banked         premium sold, minus what closing cost, minus the losses. This
                 is the number the $3,500 monthly goal is measured in, so this
                 is the one the report shows biggest.

Capture rate is the bridge between them: of the premium sold on the trades she
closed, what share did she actually keep. Her SOP takes the win at 50% of the
credit, so a healthy capture rate sits somewhere above that and below 100% -
100% would mean letting everything expire, which the 21-day exit forbids.

PRACTICE vs REAL MONEY
----------------------
Trades opened before `live_from` were placed in thinkorswim PaperMoney. They
stay in the log as history but must never be added to real income, so every
month carries a `mode` and the totals are computed from one side only.

Pure functions: no network, no Streamlit, fully unit-tested.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from src.engine.positions import Position, cash_events

ALL_TIME = "all"

# How each close reason reads on the discipline scorecard. Matched as a
# lowercase prefix against the reason the close form stored, and kept in step
# with _SOP_EXIT_PREFIXES in positions.py - the reasons that count as "by the
# rules" are exactly the ones tagged good here.
_EXIT_BUCKETS: list[tuple[str, str, str, bool]] = [
    # prefix,          label,                          tone,     by the rules
    ("profit target", "Took the win at 50%", "green", True),
    ("21 dte", "Closed or rolled at 21 days", "green", True),
    ("expired", "Expired worthless", "green", True),
    ("stop loss", "Stopped out at 2x", "amber", True),
    ("rolled", "Rolled to a new position", "neutral", False),
]
_OTHER_BUCKET = ("Closed for another reason", "red", False)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _in_month(d: Optional[date], month: str) -> bool:
    return d is not None and (month == ALL_TIME or month_key(d) == month)


def _week_start(d: date) -> date:
    """The Monday of d's week - how her weekly $808 target is counted."""
    return d - timedelta(days=d.weekday())


def is_real(position: Position, live_from: Optional[date]) -> bool:
    """Real money, or practice?

    The stamp on the trade wins. It is written when the trade is logged, so it
    knows things a date never can - most importantly that a practice trade
    placed AFTER going live is still practice.

    Only rows written before the two accounts were split carry no stamp, and
    those fall back to the date rule: a trade belongs to the account it was
    OPENED in, so a paper trade closed after going live is still paper and its
    result must never land in the real-money income.
    """
    if position.account:
        return position.account == "real"
    if live_from is None or position.opened is None:
        return False
    return position.opened >= live_from


def split_by_mode(positions: list[Position],
                  live_from: Optional[date]) -> dict[str, list[Position]]:
    """The log, cut into the two accounts it actually spans."""
    real, practice = [], []
    for p in positions:
        (real if is_real(p, live_from) else practice).append(p)
    return {"real": real, "practice": practice}


def available_months(positions: list[Position],
                     today: Optional[date] = None) -> list[dict[str, str]]:
    """Every month with activity, newest first, plus the current one.

    Activity means a trade was opened, rolled or closed in it - the same
    definition the month picker has always used, so the two agree.
    """
    today = today or date.today()
    keys: set[str] = {month_key(today)}
    for p in positions:
        for d in (p.opened, p.closed_on):
            if d is not None:
                keys.add(month_key(d))
        for r in p.rolls:
            if r.rolled_on is not None:
                keys.add(month_key(r.rolled_on))
    out = []
    for key in sorted(keys, reverse=True):
        year, month = key.split("-")
        out.append({"key": key,
                    "label": date(int(year), int(month), 1).strftime("%B %Y")})
    return out


def _premium_events(positions: list[Position], month: str) -> list[dict[str, Any]]:
    """Every dollar of premium SOLD inside the month, as dated events.

    Two kinds, because there are two ways to sell premium: opening a trade, and
    rolling a short call out to a later one. A roll's premium is what the NEW
    call sold for - not the net cash of the roll, which is already net of
    buying the old one back.
    """
    events: list[dict[str, Any]] = []
    for p in positions:
        if _in_month(p.opened, month) and p.open_credit > 0:
            events.append({"date": p.opened, "amount": round(p.open_credit, 2),
                           "kind": "open", "position": p})
        for r in p.rolls:
            if not _in_month(r.rolled_on, month):
                continue
            # new_credit is what the replacement call sold for. Older roll rows
            # were written without it, so fall back to the net cash - which is
            # the only premium figure those rows carry.
            sold = r.new_credit if r.new_credit > 0 else max(r.cash, 0.0)
            if sold > 0:
                events.append({"date": r.rolled_on, "amount": round(sold, 2),
                               "kind": "roll", "position": p})
    return sorted(events, key=lambda e: e["date"])


def _exit_bucket(exit_reason: str) -> tuple[str, str, bool]:
    reason = (exit_reason or "").split(" - ", 1)[0].strip().lower()
    for prefix, label, tone, by_rules in _EXIT_BUCKETS:
        if reason.startswith(prefix):
            return label, tone, by_rules
    return _OTHER_BUCKET


def _weeks(premium: list[dict], banked: list[dict]) -> list[dict[str, Any]]:
    """Money by week, Monday to Sunday, oldest first.

    Weeks are the rhythm her $808 target is set in, and they are what shows
    whether a month was steady or one lucky Tuesday.
    """
    buckets: dict[date, dict[str, Any]] = {}

    def bucket(d: date) -> dict[str, Any]:
        start = _week_start(d)
        if start not in buckets:
            end = start + timedelta(days=6)
            buckets[start] = {
                "start": start, "end": end,
                # "6/8 - 6/12" reads faster than any date format with a year in
                # it, and the year is already on the report's title.
                # Day before month, the way she reads dates in Israel and
                # Europe - 29/6, not 6/29.
                "label": f"{start.day}/{start.month} - {end.day}/{end.month}",
                "premium": 0.0, "banked": 0.0, "trades": 0,
            }
        return buckets[start]

    for e in premium:
        b = bucket(e["date"])
        b["premium"] += e["amount"]
        b["trades"] += 1
    for e in banked:
        bucket(e["date"])["banked"] += e["amount"]

    out = []
    for start in sorted(buckets):
        b = buckets[start]
        b["premium"] = round(b["premium"], 2)
        b["banked"] = round(b["banked"], 2)
        out.append(b)
    return out


def _group(premium: list[dict], banked: list[dict],
           key) -> list[dict[str, Any]]:
    """Premium and banked money split by strategy or by underlying, biggest
    premium first - the two "where did it come from" breakdowns."""
    rows: dict[str, dict[str, Any]] = {}

    def row(name: str) -> dict[str, Any]:
        if name not in rows:
            rows[name] = {"name": name or "(unknown)", "premium": 0.0,
                          "banked": 0.0, "trades": 0}
        return rows[name]

    for e in premium:
        r = row(key(e["position"]))
        r["premium"] += e["amount"]
        r["trades"] += 1
    for e in banked:
        row(key(e["position"]))["banked"] += e["amount"]

    total = sum(r["premium"] for r in rows.values())
    out = []
    for r in rows.values():
        r["premium"] = round(r["premium"], 2)
        r["banked"] = round(r["banked"], 2)
        r["share"] = (r["premium"] / total) if total > 0 else 0.0
        out.append(r)
    return sorted(out, key=lambda r: r["premium"], reverse=True)


def build(positions: list[Position], month: str = ALL_TIME,
          live_from: Optional[date] = None,
          today: Optional[date] = None,
          mode: str = "real") -> dict[str, Any]:
    """One month's income report (or all time, with month=ALL_TIME).

    mode picks which account to report on: "real" for money that was actually
    at stake, "practice" for the PaperMoney history. Mixing them would be the
    one thing that makes every number here a lie, so they never mix.
    """
    today = today or date.today()
    scoped = split_by_mode(positions, live_from)[mode]

    premium = _premium_events(scoped, month)
    banked_ev = [e for e in cash_events(scoped) if _in_month(e["date"], month)]

    opened = [p for p in scoped if _in_month(p.opened, month)]
    closed = [p for p in scoped
              if p.status == "closed" and _in_month(p.closed_on, month)]

    premium_sold = round(sum(e["amount"] for e in premium), 2)
    banked = round(sum(e["amount"] for e in banked_ev), 2)
    # Buying power committed by everything opened this month, closed ones
    # included - her monthly limit is a cumulative budget, so closing a trade
    # early does not hand its room back.
    bp_opened = round(sum(p.bp_effect for p in opened), 2)
    cost_to_close = round(sum(p.exit_cost or 0.0 for p in closed
                              if (p.exit_cost or 0.0) > 0), 2)
    roll_income = round(sum(e["amount"] for e in banked_ev
                            if e["kind"] == "roll"), 2)

    # Capture measures the credit shapes only. On a PMCC the closing figure
    # includes selling a LEAPS back, so "what share of the premium did I keep"
    # has no meaning there - it would read in the hundreds of percent and make
    # the whole tile untrustworthy.
    cap_trades = [p for p in closed
                  if not p.is_debit and p.credit > 0 and p.realized_pl is not None]
    cap_sold = sum(p.credit for p in cap_trades)
    capture_pct = ((sum(p.realized_pl or 0.0 for p in cap_trades) / cap_sold)
                   if cap_sold > 0 else None)

    results = [p.realized_total for p in closed if p.realized_total is not None]
    wins = [r for r in results if r > 0]

    # A day "had activity" if anything at all happened on it - opened, rolled
    # or closed. It is what makes "average per active day" honest: dividing by
    # every trading day in the month would punish her for the days her SOP
    # tells her to sit still.
    active_days = {e["date"] for e in premium} | {e["date"] for e in banked_ev}

    exits: dict[str, dict[str, Any]] = {}
    rules_followed = 0
    lessons: list[str] = []
    for p in closed:
        label, tone, by_rules = _exit_bucket(p.exit_reason)
        row = exits.setdefault(label, {"label": label, "tone": tone, "count": 0,
                                       "by_rules": by_rules})
        row["count"] += 1
        rules_followed += 1 if by_rules else 0
        parts = (p.exit_reason or "").split(" - ", 1)
        if len(parts) > 1 and parts[1].strip():
            lessons.append(parts[1].strip())

    weeks = _weeks(premium, banked_ev)
    best_week = max(weeks, key=lambda w: w["banked"], default=None)

    if month == ALL_TIME:
        label = "All time"
    else:
        year, mon = month.split("-")
        label = date(int(year), int(mon), 1).strftime("%B %Y")

    return {
        "month": month,
        "label": label,
        "mode": mode,
        "is_current": month == month_key(today),
        # headline money
        "premium_sold": premium_sold,
        "banked": banked,
        "cost_to_close": cost_to_close,
        "roll_income": roll_income,
        "capture_pct": capture_pct,
        "capture_trades": len(cap_trades),
        # counts
        "bp_opened": bp_opened,
        "trades_opened": len(opened),
        "trades_closed": len(closed),
        "still_open": sum(1 for p in opened if p.status == "open"),
        "premium_events": len(premium),
        "active_days": len(active_days),
        # averages, None rather than a divide-by-zero-shaped 0
        "avg_premium_per_sale": (round(premium_sold / len(premium), 2)
                                 if premium else None),
        "avg_per_active_day": (round(banked / len(active_days), 2)
                               if active_days else None),
        "avg_per_close": (round(banked / len(closed), 2) if closed else None),
        # quality
        "win_rate": (len(wins) / len(results)) if results else None,
        "wins": len(wins),
        "losses": len(results) - len(wins),
        "rules_followed": rules_followed,
        "rules_total": len(closed),
        "exits": sorted(exits.values(), key=lambda r: r["count"], reverse=True),
        "lessons": lessons,
        # breakdowns
        "weeks": weeks,
        "best_week": best_week,
        "by_strategy": _group(premium, banked_ev, lambda p: p.strategy_name),
        "by_underlying": _group(premium, banked_ev, lambda p: p.underlying),
        "has_activity": bool(premium or banked_ev or opened),
    }


def series(positions: list[Position], live_from: Optional[date] = None,
           today: Optional[date] = None, mode: str = "real") -> list[dict[str, Any]]:
    """One full report per month with activity, oldest first.

    The by-month backbone. Everything the month view can show for one month, it
    can now show for all of them, without the caller looping over build() and
    guessing which months exist.
    """
    today = today or date.today()
    scoped = split_by_mode(positions, live_from)[mode]
    keys = [m["key"] for m in available_months(scoped, today)]
    return [build(positions, month=k, live_from=live_from, today=today, mode=mode)
            for k in sorted(keys)]


def days(positions: list[Position], month: str,
         live_from: Optional[date] = None, today: Optional[date] = None,
         mode: str = "real") -> list[dict[str, Any]]:
    """Every calendar day of one month, the empty ones included - the calendar
    heatmap's data.

    Empty days have to be in the list rather than missing from it: a month
    drawn only on the days that earned is a scatter of green with no shape,
    and the shape is the point. Whether her income clusters near expiry is a
    question only a full grid can answer.

    weekday is 0 for Monday, matching how her weekly target is counted.
    week_index counts from the Monday of the week the 1st falls in, so the grid
    has whole rows.
    """
    today = today or date.today()
    if month == ALL_TIME:
        return []
    year, mon = (int(x) for x in month.split("-"))
    first = date(year, mon, 1)
    nxt = (date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1))
    grid_start = _week_start(first)

    scoped = split_by_mode(positions, live_from)[mode]
    banked: dict[date, float] = {}
    for e in cash_events(scoped):
        if _in_month(e["date"], month):
            banked[e["date"]] = banked.get(e["date"], 0.0) + e["amount"]
    premium: dict[date, list[float]] = {}
    for e in _premium_events(scoped, month):
        premium.setdefault(e["date"], []).append(e["amount"])

    out = []
    d = first
    while d < nxt:
        sold = premium.get(d, [])
        out.append({
            "date": d,
            "day": d.day,
            "banked": round(banked.get(d, 0.0), 2),
            "premium": round(sum(sold), 2),
            "trades": len(sold),
            "weekday": d.weekday(),
            "week_index": (_week_start(d) - grid_start).days // 7,
            "is_future": d > today,
        })
        d += timedelta(days=1)
    return out


def pace(report: dict[str, Any], monthly_goal: float,
         today: Optional[date] = None) -> Optional[dict[str, Any]]:
    """Is this month on track, judged on the days gone rather than the calendar?

    Only meaningful for the month in progress. A month that is 40% gone with
    30% of the goal banked is behind - saying so on day 10 is useful, saying it
    on the last day of a finished month is just noise.
    """
    if not report["is_current"] or monthly_goal <= 0:
        return None
    today = today or date.today()
    # Imported here rather than at module scope: goals.py builds on this
    # module, so a top-level import would be a cycle.
    from src.engine.goals import elapsed_target

    first = today.replace(day=1)
    nxt = (first.replace(year=first.year + 1, month=1) if first.month == 12
           else first.replace(month=first.month + 1))
    days_in_month = (nxt - first).days
    elapsed = today.day
    # One definition of "what should I have by now", shared with the pace
    # marker on the bullet chart, the ramp on the cumulative chart and the
    # all-time target. It used to be written out longhand here and nowhere
    # else, which is why the all-time view had no target at all.
    expected = elapsed_target(monthly_goal, first, today)
    banked = report["banked"]
    return {
        "days_elapsed": elapsed,
        "days_total": days_in_month,
        "days_left": days_in_month - elapsed,
        "expected_by_now": round(expected, 2),
        "ahead_by": round(banked - expected, 2),
        "on_track": banked >= expected,
        "still_needed": round(max(monthly_goal - banked, 0.0), 2),
        "pct_of_goal": (banked / monthly_goal) if monthly_goal else 0.0,
    }
