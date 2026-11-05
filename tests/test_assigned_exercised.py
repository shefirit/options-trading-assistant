"""Closing a credit spread that was assigned and had its long leg exercised.

Her AMAT 450/430 put spread (2026-11-05): the short put was assigned the day
before expiry and she exercised the long put to sell the shares straight back.
The close form had no reason for that, so she could not record it herself.
"""

from src.engine import month_report as mr
from src.engine import positions as P
from ui.trades.actions import ASSIGNED_EXERCISED, spread_width

from conftest import put_spread_row


def _spread():
    from src.logging_tools.row import COLUMNS
    return P.parse_rows(COLUMNS, [put_spread_row()])[0]


def test_the_width_of_a_vertical():
    assert spread_width(_spread()) == 5.0


def test_assigned_is_named_but_not_by_the_rules():
    label, tone, by_rules = mr._exit_bucket(
        f"{ASSIGNED_EXERCISED} - held it past 21 DTE")
    assert label == "Assigned, long option exercised"
    assert tone == "amber"
    assert by_rules is False


def test_assigned_is_not_counted_as_an_sop_exit():
    assert not ASSIGNED_EXERCISED.lower().startswith(P._SOP_EXIT_PREFIXES)


def _reason_box(at):
    return next(s for s in at.selectbox
                if (s.key or "").startswith("exit_reason_"))


def test_the_close_form_offers_it_and_says_what_to_type(app_with_one_put_spread):
    at = app_with_one_put_spread.run()
    assert not at.exception
    box = _reason_box(at)
    assert ASSIGNED_EXERCISED in box.options
    at = box.set_value(ASSIGNED_EXERCISED).run()
    assert not at.exception
    page = " ".join(str(m.value) for m in [*at.markdown, *at.warning])
    assert "5.00" in page and "500" in page
