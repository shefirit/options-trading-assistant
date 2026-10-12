"""The weekly covered call, driven through the real app (offline, demo data).

tests/test_weekly_covered_call.py pins the arithmetic. This file presses the
buttons: logging the shares with no call yet (Rita often sells the call a day
or two after buying), the card that shows them, and the two things a weekly
call needs every Friday - recording that it expired, and recording being
called away.
"""

import json
from datetime import date, timedelta

from src.engine.config_loader import get_strategy

KEY = "weekly_covered_call"
NAME = get_strategy(KEY)["name"]
TRADE_ID = "20261012-100000-XYZ"


def _shares_row(price: float = 50.0):
    opened = date.today() - timedelta(days=2)
    details = {"key": KEY, "underlying_price": price, "legs": [],
               "open_cash": -price * 100, "shares_cost": price * 100}
    return [opened.isoformat(), "XYZ", NAME, "", 0.0, "", 1, 0.0, price * 100,
            price * 100, "yes", "", TRADE_ID, "open", "", "", "",
            json.dumps(details), "real"]


def _call_row(strike: float = 52.0, credit: float = 80.0):
    from src.logging_tools.row import build_roll_row
    return build_roll_row(TRADE_ID, "XYZ", NAME, credit, new_strike=strike,
                          new_expiration=date.today() + timedelta(days=5),
                          new_credit=credit, account="real")


def _page(at) -> str:
    return " ".join(str(m.value) for m in [*at.markdown, *at.warning, *at.info])


def _no_snags(at):
    assert not at.exception
    snags = [str(e.value) for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, snags


def _quick_log_weekly(at):
    at = next(b for b in at.button if b.key == "ql_open").click().run()
    at = at.selectbox(key="ql_strategy").set_value(KEY).run()
    _no_snags(at)
    return at


def _box(at, prefix):
    return next(n for n in at.number_input if (n.key or "").startswith(prefix))


# ------------------------------------------------------------------ quick log
def test_quick_log_asks_for_the_share_price(app_with_rows):
    at = _quick_log_weekly(app_with_rows([]).run())
    assert _box(at, "ql_sharepx_").label == "Price you paid per share ($)"
    # No protective-put boxes: this strategy has no put.
    assert not any((n.key or "").startswith("ql_prot_") for n in at.number_input)


def test_shares_can_be_logged_before_the_call(app_with_rows):
    at = _quick_log_weekly(app_with_rows([]).run())
    _box(at, "ql_sharepx_").set_value(50.0)
    at = next(b for b in at.button if b.label == "Check it").click().run()
    _no_snags(at)
    page = _page(at)
    assert "shares only - no call sold yet" in page
    assert "Sell a call against it" in page
    assert any("Save to my log" in (b.label or "") for b in at.button)


def test_a_half_filled_call_is_caught(app_with_rows):
    at = _quick_log_weekly(app_with_rows([]).run())
    _box(at, "ql_sharepx_").set_value(50.0)
    _box(at, "ql_strike_").set_value(52.0)          # strike but no price
    at = next(b for b in at.button if b.label == "Check it").click().run()
    _no_snags(at)
    assert "half filled in" in _page(at)
    assert not any("Save to my log" in (b.label or "") for b in at.button)


# ------------------------------------------------------------------ the card
def test_shares_alone_show_on_their_card_with_the_sell_a_call_form(app_with_rows):
    at = app_with_rows([_shares_row()]).run()
    _no_snags(at)
    page = _page(at)
    assert "Your shares in XYZ" in page
    assert "assigned" not in page.split("Your shares in XYZ")[1][:400]
    assert any((n.key or "").startswith("write_credit_") for n in at.number_input)


def test_a_weekly_call_can_be_recorded_as_expired(app_with_rows):
    at = app_with_rows([_shares_row(), _call_row()]).run()
    _no_snags(at)
    modes = next(r for r in at.radio if (r.key or "").startswith("roll_mode_"))
    assert "It expired worthless (I keep the shares)" in modes.options
    at = modes.set_value("It expired worthless (I keep the shares)").run()
    _no_snags(at)
    assert "expired with nothing to pay" in _page(at) or \
           "ran out with nothing to pay" in _page(at)


def test_called_away_is_a_close_reason(app_with_rows):
    at = app_with_rows([_shares_row(), _call_row()]).run()
    _no_snags(at)
    reasons = next(s for s in at.selectbox
                   if (s.key or "").startswith("exit_reason_"))
    assert "Shares called away" in reasons.options


def test_the_card_shows_the_covered_call_roi(app_with_rows):
    at = app_with_rows([_shares_row(), _call_row()]).run()
    _no_snags(at)
    page = _page(at)
    for label in ("THIS WEEK&#x27;S CALL", "PREMIUM SO FAR", "IF CALLED AWAY"):
        assert label in page or label.replace("&#x27;", "'") in page, label
