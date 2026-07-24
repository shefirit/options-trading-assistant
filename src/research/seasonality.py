"""Seasonality - does this stock have months it reliably likes or dislikes?

We take up to 20 years of daily closes, turn them into month-by-month returns,
and then answer three plain questions for each calendar month:

  * On average, what did it do?          (average return)
  * How often was it green?              (hit rate)
  * How wide was the swing?              (best and worst year)

This is history, not a promise. A month that was green 16 of 20 years is
worth knowing about; it is still not a guarantee about this year. The wording
throughout keeps that honest.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from pydantic import BaseModel, Field

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# A month needs this many years behind it before we say anything confident.
MIN_YEARS = 5


class MonthStat(BaseModel):
    month: int                          # 1-12
    name: str
    short: str
    years: int = 0                      # how many years we actually measured
    avg_pct: Optional[float] = None      # average return, in percent
    median_pct: Optional[float] = None
    hit_rate: Optional[float] = None     # percent of years that finished green
    best_pct: Optional[float] = None
    worst_pct: Optional[float] = None
    rank: Optional[int] = None           # 1 = strongest month of the twelve
    status: str = "ok"                   # "good" | "ok" | "watch"
    read: str = ""


class YearRow(BaseModel):
    """One row of the heatmap: a year, and its 12 monthly returns (None where
    we have no data - the first and last year are usually part-years)."""
    year: int
    returns: list[Optional[float]] = Field(default_factory=lambda: [None] * 12)
    full_year_pct: Optional[float] = None


class Seasonality(BaseModel):
    symbol: str
    years_covered: int = 0
    first_year: Optional[int] = None
    last_year: Optional[int] = None
    months: list[MonthStat] = Field(default_factory=list)
    rows: list[YearRow] = Field(default_factory=list)
    best_month: Optional[MonthStat] = None
    worst_month: Optional[MonthStat] = None
    this_month: Optional[MonthStat] = None
    next_month: Optional[MonthStat] = None
    summary: str = ""
    enough_history: bool = False


# ---------- turning prices into monthly returns ----------
def month_end_closes(points: Iterable[tuple[dt.date, float]]) -> dict[tuple[int, int], float]:
    """Last close of every calendar month, keyed (year, month).

    `points` is (date, close) oldest-first - exactly what a daily price frame
    gives you once you strip it down.
    """
    out: dict[tuple[int, int], float] = {}
    for when, close in points:
        if close is None or close <= 0:
            continue
        out[(when.year, when.month)] = float(close)   # later rows overwrite earlier
    return out


def monthly_returns(points: Iterable[tuple[dt.date, float]]) -> dict[tuple[int, int], float]:
    """Percent change from one month's last close to the next month's last close.

    Only consecutive months count. If a month is missing from the data (a gap
    in the history) we skip that pair rather than inventing a two-month return.
    """
    closes = month_end_closes(points)
    out: dict[tuple[int, int], float] = {}
    for (year, month), close in closes.items():
        prev_key = (year - 1, 12) if month == 1 else (year, month - 1)
        prev = closes.get(prev_key)
        if prev:
            out[(year, month)] = (close / prev - 1.0) * 100.0
    return out


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _month_read(stat: MonthStat) -> tuple[str, str]:
    """(status, plain-English read) for one calendar month."""
    if stat.years < MIN_YEARS or stat.avg_pct is None or stat.hit_rate is None:
        return "ok", f"Only {stat.years} year(s) of history - too thin to read anything into."

    avg, hit = stat.avg_pct, stat.hit_rate
    strong_up = avg >= 1.0 and hit >= 65
    strong_down = avg <= -1.0 and hit <= 40

    if strong_up:
        return "good", (f"Historically a strong month - up {hit:.0f}% of years, "
                        f"averaging {avg:+.1f}%.")
    if strong_down:
        return "watch", (f"Historically a weak month - green only {hit:.0f}% of years, "
                         f"averaging {avg:+.1f}%.")
    if avg > 0 and hit >= 55:
        return "good", f"Leans positive - up {hit:.0f}% of years, averaging {avg:+.1f}%."
    if avg < 0 and hit < 50:
        return "watch", f"Leans negative - up only {hit:.0f}% of years, averaging {avg:+.1f}%."
    return "ok", f"No real pattern - up {hit:.0f}% of years, averaging {avg:+.1f}%."


def build(symbol: str, points: Iterable[tuple[dt.date, float]],
          today: Optional[dt.date] = None, max_years: int = 20) -> Seasonality:
    """The whole seasonality picture for one symbol.

    `points` is (date, close) oldest-first. `max_years` trims very long
    histories so the heatmap stays readable and the 1990s do not drown out
    how the stock behaves now.
    """
    today = today or dt.date.today()
    points = list(points)
    returns = monthly_returns(points)

    result = Seasonality(symbol=symbol.upper())
    if not returns:
        result.summary = (f"Not enough price history for {result.symbol} to look at "
                          "seasonality yet.")
        return result

    years = sorted({y for (y, _m) in returns})
    if len(years) > max_years:
        years = years[-max_years:]
    keep = set(years)
    returns = {k: v for k, v in returns.items() if k[0] in keep}

    result.first_year, result.last_year = years[0], years[-1]
    result.years_covered = len(years)
    result.enough_history = len(years) >= MIN_YEARS

    # Heatmap rows, newest year first - that is the order she reads them in.
    for year in reversed(years):
        row = YearRow(year=year)
        for month in range(1, 13):
            row.returns[month - 1] = returns.get((year, month))
        present = [r for r in row.returns if r is not None]
        if len(present) == 12:
            compounded = 1.0
            for r in present:
                compounded *= (1 + r / 100)
            row.full_year_pct = (compounded - 1) * 100
        result.rows.append(row)

    # Per-month statistics.
    for month in range(1, 13):
        values = [returns[(y, month)] for y in years if (y, month) in returns]
        stat = MonthStat(month=month, name=MONTH_NAMES[month - 1],
                         short=MONTH_SHORT[month - 1], years=len(values))
        if values:
            stat.avg_pct = sum(values) / len(values)
            stat.median_pct = _median(values)
            stat.hit_rate = 100.0 * sum(1 for v in values if v > 0) / len(values)
            stat.best_pct = max(values)
            stat.worst_pct = min(values)
        stat.status, stat.read = _month_read(stat)
        result.months.append(stat)

    # Rank the months strongest to weakest, so a card can say "2nd of 12".
    measured = [m for m in result.months if m.avg_pct is not None]
    for position, stat in enumerate(sorted(measured, key=lambda m: m.avg_pct, reverse=True),
                                    start=1):
        stat.rank = position

    ranked = [m for m in result.months if m.avg_pct is not None and m.years >= MIN_YEARS]
    if ranked:
        result.best_month = max(ranked, key=lambda m: m.avg_pct)
        result.worst_month = min(ranked, key=lambda m: m.avg_pct)

    result.this_month = result.months[today.month - 1]
    result.next_month = result.months[today.month % 12]

    result.summary = _summary(result)
    return result


def _summary(s: Seasonality) -> str:
    if not s.enough_history:
        return (f"{s.symbol} has only {s.years_covered} year(s) of history here. "
                "That is too short to read a seasonal pattern - treat the grid as "
                "background colour, nothing more.")

    parts = [f"{s.years_covered} years of history ({s.first_year}-{s.last_year})."]
    if s.best_month and s.worst_month:
        parts.append(f"{s.best_month.name} has been the strongest month "
                     f"({s.best_month.avg_pct:+.1f}% average, green "
                     f"{s.best_month.hit_rate:.0f}% of years) and {s.worst_month.name} "
                     f"the weakest ({s.worst_month.avg_pct:+.1f}%).")
    now = s.this_month
    if now and now.avg_pct is not None:
        parts.append(f"We are in {now.name}: {now.read}")
    parts.append("Seasonality is a tiebreaker, never a reason to trade on its own.")
    return " ".join(parts)


def frame_to_points(frame) -> list[tuple[dt.date, float]]:
    """Adapter: a pandas price frame with a Close column and a date index ->
    the (date, close) pairs this module works in. Kept here so the rest of the
    module never has to know pandas exists."""
    if frame is None or len(frame) == 0 or "Close" not in getattr(frame, "columns", []):
        return []
    points: list[tuple[dt.date, float]] = []
    for when, close in zip(frame.index, frame["Close"]):
        try:
            day = when.date() if hasattr(when, "date") else when
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            points.append((day, value))
    return points
