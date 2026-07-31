"""Reading and writing the four numbers that define her plan.

  capital        what the account holds
  monthly goal   the income target the report measures against
  weekly goal    the same target at the rhythm she actually trades in
  BP budget      how much buying power she will commit in a month

They live in config/settings.yaml with the rest of her rules. That file is
heavily commented - every number in it explains why it is what it is - so this
edits the specific VALUES in place with a line-level replacement rather than
loading and re-dumping the YAML, which would throw every comment away.

Pure: no Streamlit, no network. The caller clears the config cache.
"""

from __future__ import annotations

import re
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


def read(settings: dict[str, Any]) -> dict[str, float]:
    """The four numbers as they stand."""
    acct = settings.get("account", {}) or {}
    tgt = settings.get("targets", {}) or {}
    risk = settings.get("risk_limits", {}) or {}
    return {
        "capital": float(acct.get("starting_capital", 0) or 0),
        "monthly": float(tgt.get("monthly", 0) or 0),
        "weekly": float(tgt.get("weekly", 0) or 0),
        "bp_limit": float(risk.get("monthly_bp_limit", 0) or 0),
    }


def weekly_from_monthly(monthly: float) -> float:
    """The weekly target implied by a monthly one.

    A month is 52/12 weeks, not 4 - using 4 would quietly set a weekly target
    8% higher than the monthly goal actually needs, and she would spend every
    year feeling behind for no reason. Her own $3,500 and $808 sit on exactly
    this ratio (3500 x 12 / 52 = 807.7), which is the check that it is right.
    """
    return round(monthly * 12.0 / 52.0)


def validate(values: dict[str, float]) -> list[str]:
    """Everything wrong with these numbers, in plain English. Empty = fine."""
    problems: list[str] = []
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


def _replace_scalar(text: str, key: str, value: float) -> str:
    """Swap one YAML scalar for a new one, leaving its line comment alone.

    Matched with the key anchored at a line start (allowing indentation) so
    `monthly:` cannot match inside `monthly_bp_limit:`, and only when the key
    appears exactly once - anything else means the file is not shaped the way
    this function assumes, and guessing would corrupt her config.
    """
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:(?P<gap>[ \t]*)"
        rf"(?P<value>[^#\r\n]*?)(?P<trail>[ \t]*(?:#[^\r\n]*)?)$",
        re.MULTILINE)
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one '{key}:' line in settings.yaml, found "
            f"{len(matches)}. Nothing was written.")
    number = f"{value:g}"
    return pattern.sub(
        lambda m: f"{m.group('indent')}{key}:{m.group('gap')}{number}"
                  f"{m.group('trail')}", text, count=1)


def save(values: dict[str, float], path: Optional[Path] = None) -> Path:
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
    path.write_text(text, encoding="utf-8")
    return path
