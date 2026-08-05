"""Her plan, and where she actually is against it.

The whole "goals vs reality" half of the My trades tab is computed here, and
the reason it is one module rather than a formula copied into four places is
`elapsed_target`.

ONE DEFINITION OF "WHAT SHOULD I HAVE BY NOW"
---------------------------------------------
A monthly goal of $3,500 has to answer four different questions on one screen:

  * the pace marker on the month's bullet row   - what should today have?
  * the dashed ramp on the cumulative chart     - what should each day have?
  * the marker on the year-one track            - where should the account be?
  * the target for an "all time" view           - what was the span aiming at?

They are the same question at four scales, so they are one function. Before
this, the month version lived in month_report.pace() and the all-time version
did not exist at all - which is why the all-time view used to show a whole
account's history as a percentage of a ONE MONTH goal.

Days, not months, is the unit. She went live on 31 July, so July owes one day
of goal and not the whole $3,500. Counting whole months would have said she
was $3,500 behind before she placed a trade.

TWO BOOKS, NEVER ADDED
----------------------
Every function here takes the same `live_from` the rest of the app uses and
splits the log through month_report.split_by_mode first. A practice dollar
cannot reach a real total, because the real total is computed from a list that
never contained it.

year_one() is the one function with no `mode` parameter, on purpose: an account
balance goal is about money that exists. PaperMoney has no balance to grow.

Pure functions: no network, no Streamlit, fully unit-tested.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Optional

from src.engine import month_report as mr
from src.engine.positions import Position, cash_events


def targets_from(settings: dict) -> dict[str, float]:
    """The plan's numbers, read from config so a rule change never means a code
    change. Missing entries come back as 0.0 rather than raising - a half-filled
    settings file should make the goal panel quiet, not crash the tab."""
    t = settings.get("targets") or {}
    acct = settings.get("account") or {}
    risk = settings.get("risk_limits") or {}
    return {
        "weekly": float(t.get("weekly", 0) or 0),
        "monthly": float(t.get("monthly", 0) or 0),
        "capital": float(acct.get("starting_capital", 0) or 0),
        "year_one": float(t.get("year_one_end_balance", 0) or 0),
        "bp_limit": float(risk.get("monthly_bp_limit", 0) or 0),
    }


def _days_in(d: date) -> int:
    return monthrange(d.year, d.month)[1]


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(year=d.year + 1, month=1, day=1) if d.month == 12
            else d.replace(month=d.month + 1, day=1))


def elapsed_target(monthly_goal: float, start: Optional[date],
                   today: Optional[date] = None) -> float:
    """What a steady plan would have produced between `start` and `today`.

    A month lived end to end contributes the whole goal. A partial month at
    either end contributes its share of days. `today` counts as lived, so the
    first day live is worth one day of goal rather than nothing.

    Returns 0.0 for a start in the future, a missing start, or no goal - the
    three cases where "you should have X by now" has no meaning.
    """
    today = today or date.today()
    if start is None or monthly_goal <= 0 or start > today:
        return 0.0

    total = 0.0
    cursor = _month_start(start)
    while cursor <= today:
        month_end = _next_month(cursor) - timedelta(days=1)
        first = max(start, cursor)
        last = min(today, month_end)
        # Inclusive of both ends: the day she went live is a day of the plan.
        lived = (last - first).days + 1
        total += monthly_goal * (lived / _days_in(cursor))
        cursor = _next_month(cursor)
    return round(total, 2)


def month_target(monthly_goal: float, month: str,
                 live_from: Optional[date] = None,
                 today: Optional[date] = None) -> float:
    """One month's target.

    The full goal for a month that ran end to end, a day-share for the month in
    progress and for the month she went live in. mr.ALL_TIME asks a different
    question and belongs to span_report, so it is refused here.
    """
    today = today or date.today()
    if month == mr.ALL_TIME or monthly_goal <= 0:
        return 0.0
    year, mon = (int(x) for x in month.split("-"))
    first = date(year, mon, 1)
    last = _next_month(first) - timedelta(days=1)

    start = max(first, live_from) if live_from else first
    end = min(last, today)
    if start > end:
        return 0.0
    return round(monthly_goal * (((end - start).days + 1) / _days_in(first)), 2)


def _banked_between(positions: list[Position], start: Optional[date],
                    end: Optional[date] = None) -> float:
    """Money that settled inside a window, from the same cash_events the rest of
    the app banks on - so a roll credit counts on the day it was rolled."""
    out = 0.0
    for e in cash_events(positions):
        if start is not None and e["date"] < start:
            continue
        if end is not None and e["date"] > end:
            continue
        out += e["amount"]
    return round(out, 2)


def _tone(actual: float, target: float, pace: float) -> str:
    """good once the goal is met, watch while it is still ahead of pace,
    behind when it is not. Never a colour - the UI maps it."""
    if target > 0 and actual >= target:
        return "good"
    return "watch" if actual >= pace else "behind"


def bullet_rows(positions: list[Position], settings: dict,
                live_from: Optional[date] = None,
                today: Optional[date] = None,
                mode: str = "real") -> list[dict[str, Any]]:
    """The three target-vs-actual rows, in this order: this week, this month,
    year one.

    Three horizons on one picture is the point. A good week inside a bad month
    and a good month inside a year that is behind are both worth seeing, and
    each one on its own progress bar tells her none of that.

    Each row carries `pace` as well as `target`, which is what a bullet chart
    has that a progress bar does not: not just "35% of the way there" but
    "35%, and a steady plan would be at 16% today".
    """
    today = today or date.today()
    t = targets_from(settings)
    scoped = mr.split_by_mode(positions, live_from)[mode]

    week_start = today - timedelta(days=today.weekday())          # Monday
    month_start = _month_start(today)
    # Year one runs from the day the money went in. Before that there is no
    # year one to be in, so the span starts at the first thing that happened.
    events = cash_events(scoped)
    year_start = live_from or (events[0]["date"] if events else today)

    monthly = t["monthly"]
    weekly = t["weekly"]
    year_goal = t["year_one"] - t["capital"]     # the INCOME half of $142,000

    week_actual = _banked_between(scoped, week_start, today)
    month_actual = _banked_between(scoped, month_start, today)
    year_actual = _banked_between(scoped, year_start, today)

    # A week's pace is per day of the week; a month's is per day of the month;
    # year one's is the same elapsed_target the ramp and the track use.
    week_pace = weekly * ((today.weekday() + 1) / 7) if weekly else 0.0
    month_pace = elapsed_target(monthly, month_start, today)
    year_pace = elapsed_target(monthly, year_start, today)

    def row(label, sub, actual, target, pace, period):
        return {
            "label": label,
            "sub": sub,
            "actual": round(actual, 2),
            "target": round(target, 2),
            "pace": round(pace, 2),
            "pct": (actual / target) if target > 0 else 0.0,
            "pace_pct": (pace / target) if target > 0 else 0.0,
            "text": f"${actual:,.0f} of ${target:,.0f}",
            "tone": _tone(actual, target, pace),
            "period": period,
        }

    return [
        row("THIS WEEK", "Monday to today", week_actual, weekly, week_pace, "week"),
        row("THIS MONTH", today.strftime("%B"), month_actual, monthly,
            month_pace, "month"),
        row("YEAR ONE", "income since you went live", year_actual, year_goal,
            year_pace, "year"),
    ]


def cumulative_series(positions: list[Position], settings: dict,
                      live_from: Optional[date] = None,
                      today: Optional[date] = None) -> list[dict[str, Any]]:
    """The equity-vs-target backbone: a running total of money banked, beside
    the ramp a steady plan would have drawn, for BOTH books.

    Every row carries `book`. There is deliberately no row and no field that
    holds real plus practice - the chart cannot draw a total that does not
    exist in its data.

    Rows land on every day something happened, plus each month boundary and
    today, so the ramp has somewhere to bend and a quiet fortnight still draws
    a straight line rather than vanishing.
    """
    today = today or date.today()
    monthly = targets_from(settings)["monthly"]
    books = mr.split_by_mode(positions, live_from)

    out: list[dict[str, Any]] = []
    for book, scoped in books.items():
        events = cash_events(scoped)
        if not events:
            continue
        start = events[0]["date"]
        # The real book's plan starts the day she funded it. The practice book
        # has no plan, so its ramp is measured from its own first trade and is
        # only ever drawn as context.
        plan_start = live_from if (book == "real" and live_from) else start

        marks = {e["date"] for e in events} | {today}
        cursor = _month_start(start)
        while cursor <= today:
            if cursor >= start:
                marks.add(cursor)
            cursor = _next_month(cursor)

        running = 0.0
        idx = 0
        for d in sorted(marks):
            while idx < len(events) and events[idx]["date"] <= d:
                running += events[idx]["amount"]
                idx += 1
            out.append({
                "date": d,
                "banked": round(running, 2),
                "cumulative": round(running, 2),
                "target": elapsed_target(monthly, plan_start, d),
                "book": book,
            })
    return sorted(out, key=lambda r: (r["book"], r["date"]))


def year_one(positions: list[Position], settings: dict,
             live_from: Optional[date] = None,
             today: Optional[date] = None) -> dict[str, Any]:
    """Progress toward the $142,000 year-one balance.

    Real money only. An account-balance goal is about money that exists, and
    showing a PaperMoney total against it would be the one confusion this app
    must never create.

    pct is measured against the INCOME half - the $42,000 she has to earn - not
    against the $142,000 headline. Otherwise an account that has earned nothing
    would open at 70% and the bar would be a decoration.
    """
    today = today or date.today()
    t = targets_from(settings)
    real = mr.split_by_mode(positions, live_from)["real"]

    capital, goal, monthly = t["capital"], t["year_one"], t["monthly"]
    to_earn = max(goal - capital, 0.0)

    events = cash_events(real)
    start = live_from or (events[0]["date"] if events else today)
    banked = _banked_between(real, start, today)
    balance = capital + banked

    pace_income = elapsed_target(monthly, start, today)
    # Twelve months from the day she funded, which is what "year one" means.
    year_end = date(start.year + 1, start.month, 1) - timedelta(days=1) \
        if start.day == 1 else date(start.year + 1, start.month, start.day)
    months_left = max((year_end.year - today.year) * 12
                      + (year_end.month - today.month), 0)
    to_go = max(to_earn - banked, 0.0)

    return {
        "capital": capital,
        "goal": goal,
        "to_earn": to_earn,
        "banked": banked,
        "balance": balance,
        "to_go": to_go,
        "pct": (banked / to_earn) if to_earn > 0 else 0.0,
        "start": start,
        "year_end": year_end,
        "months_live": max((today.year - start.year) * 12
                           + (today.month - start.month), 0) + 1,
        "months_left": months_left,
        "pace_balance": capital + pace_income,
        "pace_income": pace_income,
        "ahead_by": round(banked - pace_income, 2),
        "on_pace": banked >= pace_income,
        "monthly_needed": round(to_go / months_left, 2) if months_left else to_go,
    }


def month_table(positions: list[Position], settings: dict,
                live_from: Optional[date] = None,
                today: Optional[date] = None) -> list[dict[str, Any]]:
    """One row per calendar month, oldest first, with both books side by side.

    `real` and `practice` are separate keys and there is no key that adds them.
    That is the whole safety mechanism: a chart built from these rows cannot
    plot a combined bar because the number is not in the data.
    """
    today = today or date.today()
    monthly = targets_from(settings)["monthly"]
    books = mr.split_by_mode(positions, live_from)

    banked: dict[str, dict[str, float]] = {}
    for book, scoped in books.items():
        for e in cash_events(scoped):
            key = mr.month_key(e["date"])
            banked.setdefault(key, {"real": 0.0, "practice": 0.0})[book] += e["amount"]

    keys = set(banked) | {mr.month_key(today)}
    for p in positions:
        for d in (p.opened, p.closed_on):
            if d is not None:
                keys.add(mr.month_key(d))

    summaries = {m["month"]: m for m in
                 mr_monthly_summary_by_key(books["real"], today)}

    out = []
    for key in sorted(keys):
        year, mon = (int(x) for x in key.split("-"))
        cell = banked.get(key, {"real": 0.0, "practice": 0.0})
        target = month_target(monthly, key, live_from, today)
        s = summaries.get(key, {})
        out.append({
            "month": key,
            "label": date(year, mon, 1).strftime("%B %Y"),
            "short": date(year, mon, 1).strftime("%b"),
            "real": round(cell["real"], 2),
            "practice": round(cell["practice"], 2),
            "target": target,
            "pct": (cell["real"] / target) if target > 0 else 0.0,
            "closed": s.get("closed_count", 0),
            "win_rate": s.get("win_rate"),
            "rules_followed": s.get("rules_followed", 0),
            "rules_total": s.get("closed_count", 0),
            "bp_opened": s.get("bp_opened", 0.0),
        })
    return out


def mr_monthly_summary_by_key(positions: list[Position],
                              today: Optional[date] = None) -> list[dict[str, Any]]:
    """monthly_summary, imported lazily so this module stays cheap to import."""
    from src.engine.positions import monthly_summary
    return monthly_summary(positions, today)


def span_report(positions: list[Position], settings: dict,
                live_from: Optional[date] = None,
                today: Optional[date] = None,
                mode: str = "real") -> dict[str, Any]:
    """Everything since the first trade, with a goal that means something.

    month_report.build(month=ALL_TIME) already totals the whole log correctly.
    What it cannot know is what the whole log was AIMING at, so the all-time
    view used to hand its total to a band that divided by the monthly $3,500 -
    printing a six-day-old account as 40% of a goal it was never measured
    against.

    This wraps that report and adds the target the span actually had:
    elapsed_target from the first activity (or the day she funded) to today.
    """
    today = today or date.today()
    monthly = targets_from(settings)["monthly"]
    report = mr.build(positions, month=mr.ALL_TIME, live_from=live_from,
                      today=today, mode=mode)

    scoped = mr.split_by_mode(positions, live_from)[mode]
    # The real book's span starts the day she FUNDED, not the day of her first
    # trade. A week spent waiting for a setup is still a week of the plan, and
    # a span that began at the first trade would quietly hide it.
    if mode == "real" and live_from:
        first = live_from
    else:
        dates = [d for p in scoped for d in (p.opened, p.closed_on) if d is not None]
        dates += [r.rolled_on for p in scoped for r in p.rolls if r.rolled_on]
        first = min(dates) if dates else None

    span_target = elapsed_target(monthly, first, today)
    months = mr.available_months(scoped, today)

    report.update({
        "span_target": span_target,
        "span_pct": (report["banked"] / span_target) if span_target > 0 else 0.0,
        "first_activity": first,
        "months": len(months),
        "months_elapsed": (((today.year - first.year) * 12
                            + (today.month - first.month)) + 1) if first else 0,
        "days_elapsed": ((today - first).days + 1) if first else 0,
    })
    return report
