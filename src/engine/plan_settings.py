"""Reading and writing the numbers that define her plan.

  capital        what the account holds
  monthly goal   the income target the report measures against
  weekly goal    the same target at the rhythm she actually trades in
  BP budget      how much buying power she will commit in a month
  plan start     the day this plan came into force, which year one runs from

They live in config/settings.yaml with the rest of her rules. That file is
heavily commented - every number in it explains why it is what it is - so this
edits the specific VALUES in place with a line-level replacement rather than
loading and re-dumping the YAML, which would throw every comment away.

Pure: no Streamlit, no network. The caller clears the config cache.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src.engine.config_loader import CONFIG_DIR

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"

# field -> (the YAML key, how it is labelled when something is wrong)
FIELDS: dict[str, tuple[str, str]] = {
    "capital": ("starting_capital", "Capital"),
    "monthly": ("monthly", "Monthly goal"),
    "weekly": ("weekly", "Weekly goal"),
    "bp_limit": ("monthly_bp_limit", "Monthly buying-power budget"),
}

# A year of the monthly goal on top of capital - the "double every two years"
# line from her Notion hub. Kept in step automatically so the plan cannot end
# up describing two different futures.
YEAR_ONE_KEY = "year_one_end_balance"


PLAN_FROM_KEY = "plan_from"


def read(settings: dict[str, Any]) -> dict[str, Any]:
    """The plan as it stands. `plan_from` is a date or None; the rest are floats."""
    acct = settings.get("account", {}) or {}
    tgt = settings.get("targets", {}) or {}
    risk = settings.get("risk_limits", {}) or {}
    raw = acct.get(PLAN_FROM_KEY)
    if isinstance(raw, date):
        plan_from: Optional[date] = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            plan_from = date.fromisoformat(raw.strip())
        except ValueError:
            plan_from = None
    else:
        plan_from = None
    return {
        "capital": float(acct.get("starting_capital", 0) or 0),
        "monthly": float(tgt.get("monthly", 0) or 0),
        "weekly": float(tgt.get("weekly", 0) or 0),
        "bp_limit": float(risk.get("monthly_bp_limit", 0) or 0),
        "plan_from": plan_from,
    }


def weekly_from_monthly(monthly: float) -> float:
    """The weekly target implied by a monthly one.

    A month is 52/12 weeks, not 4 - using 4 would quietly set a weekly target
    8% higher than the monthly goal actually needs, and she would spend every
    year feeling behind for no reason. Her own $3,500 and $808 sit on exactly
    this ratio (3500 x 12 / 52 = 807.7), which is the check that it is right.
    """
    return round(monthly * 12.0 / 52.0)


def validate(values: dict[str, Any]) -> list[str]:
    """Everything wrong with these numbers, in plain English. Empty = fine."""
    problems: list[str] = []
    plan_from = values.get("plan_from")
    if plan_from is not None and not isinstance(plan_from, date):
        problems.append("The plan start has to be a date, or empty.")
    for field, (_key, label) in FIELDS.items():
        value = values.get(field)
        if value is None or value <= 0:
            problems.append(f"{label} has to be more than zero.")
    capital = values.get("capital") or 0
    bp = values.get("bp_limit") or 0
    monthly = values.get("monthly") or 0
    if capital > 0 and bp > capital:
        problems.append(
            f"The buying-power budget (${bp:,.0f}) is larger than your whole "
            f"account (${capital:,.0f}). You cannot commit more than you have.")
    if capital > 0 and monthly > 0 and monthly > capital * 0.25:
        problems.append(
            f"A monthly goal of ${monthly:,.0f} is more than 25% of ${capital:,.0f} "
            f"a month. That is not a target, it is a reason to take bad trades - "
            f"check the number.")
    return problems


def _line_for(key: str) -> "re.Pattern[str]":
    """The one line in settings.yaml that sets `key`.

    Anchored at a line start (allowing indentation) so `monthly:` cannot match
    inside `monthly_bp_limit:`, and capturing the trailing comment separately so
    a rewrite leaves it alone. Every number in that file explains why it is what
    it is; a writer that dropped those comments would cost more than it saved.
    """
    return re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:(?P<gap>[ \t]*)"
        rf"(?P<value>[^#\r\n]*?)(?P<trail>[ \t]*(?:#[^\r\n]*)?)$",
        re.MULTILINE)


def _replace_raw(text: str, key: str, rendered: str) -> str:
    """Swap one YAML value for `rendered`, leaving its line comment alone.

    Only when the key appears exactly once - anything else means the file is not
    shaped the way this function assumes, and guessing would corrupt her config.
    """
    pattern = _line_for(key)
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one '{key}:' line in settings.yaml, found "
            f"{len(matches)}. Nothing was written.")
    return pattern.sub(
        lambda m: f"{m.group('indent')}{key}:{m.group('gap')}{rendered}"
                  f"{m.group('trail')}", text, count=1)


def _replace_scalar(text: str, key: str, value: float) -> str:
    """Swap one YAML number for a new one."""
    return _replace_raw(text, key, f"{value:g}")


def _replace_quoted(text: str, key: str, value: str) -> str:
    """Swap one YAML value for a QUOTED string.

    A date has to go back quoted, or `plan_from: 2026-09-15` comes out of the
    loader as a `date` while `plan_from: ""` comes out as a `str`, and every
    reader would need to handle both. Quoting it means the value always arrives
    the same shape.
    """
    return _replace_raw(text, key, '"' + value + '"')


def save(values: dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write the four numbers back to settings.yaml, comments intact.

    Raises ValueError when the numbers do not make sense or the file is not
    shaped as expected - it is better to refuse than to half-write a config
    every other rule in the app reads from.
    """
    problems = validate(values)
    if problems:
        raise ValueError(" ".join(problems))

    path = path or SETTINGS_PATH
    text = path.read_text(encoding="utf-8")
    for field, (key, _label) in FIELDS.items():
        text = _replace_scalar(text, key, float(values[field]))
    # Keep the one-year figure consistent with the goal it is derived from,
    # rather than leaving a stale number in the file to be read later as if it
    # still meant something.
    try:
        text = _replace_scalar(
            text, YEAR_ONE_KEY,
            float(values["capital"]) + float(values["monthly"]) * 12.0)
    except ValueError:
        pass   # the key is optional; its absence is not a reason to fail

    # The plan's start date, when the caller passed one. Absent from `values`
    # means "leave it alone" - only an explicit None clears it, so a caller that
    # only wants to move the numbers cannot wipe the date by omission.
    if "plan_from" in values:
        stamp = values["plan_from"]
        try:
            text = _replace_quoted(
                text, PLAN_FROM_KEY,
                stamp.isoformat() if isinstance(stamp, date) else "")
        except ValueError:
            pass   # optional, same as above

    path.write_text(text, encoding="utf-8")
    return path
