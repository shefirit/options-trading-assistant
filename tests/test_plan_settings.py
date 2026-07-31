"""Setting the plan numbers from inside the app.

settings.yaml is the file every other rule reads from, and every number in it
carries a comment explaining why it is what it is. These tests exist because a
careless write would either lose those comments or corrupt the file.
"""

from __future__ import annotations

import pytest
import yaml

from src.engine import plan_settings as ps

SAMPLE = """\
# ============================================================
#  Global settings - your account, targets, and risk limits.
# ============================================================

account:
  starting_capital: 100000      # dollars you began with
  platform: "thinkorswim"
  live_from: "2026-07-31"

# Your income goals (from your Notion hub).
targets:
  weekly: 808
  monthly: 3500
  year_one_end_balance: 142000

# Hard risk limits the app will warn you about.
risk_limits:
  monthly_bp_limit: 50000       # do not use more than this per month
  position_delta_red_flag: 90   # a position's net delta red flag
"""


@pytest.fixture()
def config(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_reads_the_four_numbers_off_the_settings():
    values = ps.read(yaml.safe_load(SAMPLE))
    assert values == {"capital": 100_000.0, "monthly": 3500.0,
                      "weekly": 808.0, "bp_limit": 50_000.0}


def test_saving_keeps_every_comment_in_the_file(config):
    ps.save({"capital": 120_000, "monthly": 4000, "weekly": 923,
             "bp_limit": 60_000}, path=config)
    text = config.read_text(encoding="utf-8")
    assert "# dollars you began with" in text
    assert "# do not use more than this per month" in text
    assert "# Your income goals (from your Notion hub)." in text
    assert "Global settings" in text


def test_saving_writes_the_new_numbers(config):
    ps.save({"capital": 120_000, "monthly": 4000, "weekly": 923,
             "bp_limit": 60_000}, path=config)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["account"]["starting_capital"] == 120_000
    assert data["targets"]["monthly"] == 4000
    assert data["targets"]["weekly"] == 923
    assert data["risk_limits"]["monthly_bp_limit"] == 60_000


def test_everything_else_in_the_file_is_left_alone(config):
    ps.save({"capital": 120_000, "monthly": 4000, "weekly": 923,
             "bp_limit": 60_000}, path=config)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["account"]["platform"] == "thinkorswim"
    assert data["account"]["live_from"] == "2026-07-31"
    assert data["risk_limits"]["position_delta_red_flag"] == 90


def test_monthly_does_not_match_inside_monthly_bp_limit(config):
    """The bug this guards: a loose search for "monthly:" also finds
    "monthly_bp_limit:" and writes the goal into the risk limit."""
    ps.save({"capital": 100_000, "monthly": 4000, "weekly": 923,
             "bp_limit": 50_000}, path=config)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["targets"]["monthly"] == 4000
    assert data["risk_limits"]["monthly_bp_limit"] == 50_000


def test_the_one_year_figure_follows_the_goal(config):
    ps.save({"capital": 120_000, "monthly": 4000, "weekly": 923,
             "bp_limit": 60_000}, path=config)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["targets"]["year_one_end_balance"] == 120_000 + 4000 * 12


def test_the_weekly_target_a_monthly_goal_implies():
    """Her own numbers sit on this ratio - 3500 x 12 / 52 = 808 - which is the
    check that a month is 52/12 weeks and not 4."""
    assert ps.weekly_from_monthly(3500) == 808
    assert ps.weekly_from_monthly(7000) == 1615


def test_a_zero_or_negative_number_is_refused(config):
    for bad in ({"capital": 0, "monthly": 3500, "weekly": 808, "bp_limit": 50_000},
                {"capital": 100_000, "monthly": -1, "weekly": 808, "bp_limit": 50_000}):
        with pytest.raises(ValueError):
            ps.save(bad, path=config)
    # The file is untouched when the save is refused.
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["targets"]["monthly"] == 3500


def test_a_budget_bigger_than_the_account_is_refused(config):
    with pytest.raises(ValueError, match="larger than your whole account"):
        ps.save({"capital": 100_000, "monthly": 3500, "weekly": 808,
                 "bp_limit": 150_000}, path=config)


def test_an_implausible_monthly_goal_is_refused(config):
    """25% of the account per month is not a target, it is a reason to take
    bad trades. Refusing it is the whole point of validating at all."""
    problems = ps.validate({"capital": 100_000, "monthly": 30_000,
                            "weekly": 6923, "bp_limit": 50_000})
    assert any("not a target" in p for p in problems)


def test_a_file_missing_a_key_refuses_rather_than_half_writing(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("targets:\n  monthly: 3500\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected exactly one"):
        ps.save({"capital": 100_000, "monthly": 3500, "weekly": 808,
                 "bp_limit": 50_000}, path=path)


def test_the_real_settings_file_has_the_shape_this_module_assumes():
    """A guard on the actual config, so a future edit that renames or
    duplicates one of these keys fails here rather than in her hands."""
    text = ps.SETTINGS_PATH.read_text(encoding="utf-8")
    for _field, (key, _label) in ps.FIELDS.items():
        assert ps._replace_scalar(text, key, 1) != text
