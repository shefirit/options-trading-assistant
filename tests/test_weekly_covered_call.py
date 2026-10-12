"""The weekly covered call, followed from buying the shares to being called away.

Rita's strategy (2026-10-12): buy 100 shares in a bullish setup, then sell a
call against them every week. Often the call goes on a day or two AFTER the
shares, so the shares are logged on their own first and every call - the first
one included - is recorded against that same trade.

She set no rules for it: no delta limit, no profit target, no stop, no time
exit. A falling stock gets a warning, never an instruction to sell.

Invented numbers throughout (this repo is public).
"""

from datetime import date, timedelta

import pytest

from src.engine import exit_rules, positions, quick_log, wheel
from src.engine.config_loader import entry_dte_window, get_strategy
from src.engine.models import Action, CheckStatus, Leg, OptionType, Trade
from src.engine.validator import validate_trade
from src.logging_tools.row import COLUMNS, build_roll_row, build_row

KEY = "weekly_covered_call"
STRAT = get_strategy(KEY)
NAME = STRAT["name"]
TRADE_ID = "20261012-100000-XYZ"


def _shares_row(price: float = 50.0, contracts: int = 1, opened=None):
    """The open row Quick Log writes for shares bought with no call yet."""
    trade = Trade(strategy_key=KEY, underlying="XYZ", contracts=contracts,
                  legs=[], underlying_price=price)
    sizing = quick_log.sizing_from_fill(trade, STRAT, 0.0, share_price=price)
    return build_row(trade, NAME, sizing, True, "", trade_id=TRADE_ID,
                     opened_on=opened or date.today() - timedelta(days=3))


def _call(strike: float, credit: float, days: int = 7, cash=None,
          on=None):
    """A call written against the shares, the way ➕ Sell a call records it."""
    return build_roll_row(TRADE_ID, "XYZ", NAME,
                          credit if cash is None else cash,
                          new_strike=strike,
                          new_expiration=date.today() + timedelta(days=days),
                          new_credit=credit, rolled_on=on or date.today())


def _expired(on=None):
    """The roll form's 'It expired worthless' - nothing paid, call gone."""
    return build_roll_row(TRADE_ID, "XYZ", NAME, 0.0,
                          note="The call expired worthless",
                          rolled_on=on or date.today())


def _parse(*rows):
    return positions.parse_rows(COLUMNS, list(rows))[0]


def _signal(p, price, cost=None, delta=None, today=None):
    return exit_rules.evaluate(p, STRAT["exit"], current_cost=cost,
                               underlying_price=price, short_delta=delta,
                               today=today)


# ------------------------------------------------------------------ config
def test_she_set_no_rules_so_none_are_configured():
    entry, ex = STRAT["entry"], STRAT["exit"]
    assert "short_leg_delta_max" not in entry
    assert "short_call_delta" not in entry
    for rule in ("profit_target_pct", "stop_loss_multiple", "time_exit_dte"):
        assert rule not in ex, rule
    assert STRAT["bullish_only"] is True
    assert ex["share_drop_warn_pct"] == 10


def test_the_window_catches_this_friday_and_next_from_any_weekday():
    lo, hi = entry_dte_window(STRAT, "XYZ")
    # Monday: Fridays at 4 and 11 days. Wednesday: 2 and 9.
    assert lo <= 2 and hi >= 11
    assert hi < 14                       # but not the Friday after that


# ------------------------------------------------------------------ logging
def test_shares_alone_cost_what_the_shares_cost():
    trade = Trade(strategy_key=KEY, underlying="XYZ", contracts=2, legs=[])
    s = quick_log.sizing_from_fill(trade, STRAT, 0.0, share_price=50.0)
    assert s["shares_cost"] == 10_000.0
    assert s["open_cash"] == -10_000.0
    assert s["credit"] == 0.0
    assert s["buying_power"] == 10_000.0
    assert s["max_loss"] == 10_000.0     # no put underneath


def test_shares_and_a_call_on_the_same_day_still_work():
    leg = Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
              strike=52.0, dte=7)
    trade = Trade(strategy_key=KEY, underlying="XYZ", contracts=1, legs=[leg])
    s = quick_log.sizing_from_fill(trade, STRAT, 80.0, share_price=50.0)
    assert s["open_cash"] == -4_920.0
    assert s["max_loss"] == 4_920.0      # the shares, less the call


# ------------------------------------------------------------------ shares only
def test_shares_logged_alone_wait_for_their_first_call():
    p = _parse(_shares_row())
    assert p.status == "open"
    assert p.legs == [] and p.expiration is None
    assert p.is_uncovered and p.can_write_call
    sig = _signal(p, 51.0)
    assert sig.action == "uncovered"
    assert "Sell a call against it" in sig.reason


def test_bought_shares_get_the_wheel_numbers():
    state = wheel.state_from(_parse(_shares_row()), 51.0)
    assert state is not None and state.bought
    assert state.paid_per_share == 50.0
    assert state.cost_basis == 50.0
    assert state.unrealised == 100.0
    assert state.call_strike is None


def test_other_covered_calls_are_not_read_as_bought_shares():
    """Models 1-3 own shares too, but their puts change the story."""
    p = _parse(_shares_row())
    p.strategy_key = "covered_call_model_2"
    assert wheel.state_from(p, 51.0) is None


# ------------------------------------------------------------------ the weekly rhythm
def test_the_first_call_two_days_later_joins_the_same_trade():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    assert p.has_short_call and not p.is_uncovered
    state = wheel.state_from(p, 51.0)
    assert state.premium_collected == 80.0
    assert state.cost_basis == 49.20
    assert state.call_strike == 52.0
    assert state.called_away_profit == 280.0     # (52 - 49.20) x 100


def test_an_expired_call_comes_off_and_keeps_its_premium():
    week1 = date.today() - timedelta(days=8)
    p = _parse(_shares_row(opened=week1 - timedelta(days=2)),
               _call(52.0, 80.0, days=-1, on=week1),
               _expired(on=date.today() - timedelta(days=1)))
    assert not p.has_short_call and p.is_uncovered
    assert wheel.state_from(p, 51.0).premium_collected == 80.0


def test_every_week_pulls_the_cost_down_further():
    p = _parse(_shares_row(opened=date.today() - timedelta(days=20)),
               _call(52.0, 80.0, days=-8, on=date.today() - timedelta(days=15)),
               _expired(on=date.today() - timedelta(days=8)),
               _call(52.0, 70.0, days=-1, on=date.today() - timedelta(days=8)),
               _expired(on=date.today() - timedelta(days=1)),
               _call(53.0, 60.0, on=date.today()))
    state = wheel.state_from(p, 51.0)
    assert state.premium_collected == 210.0
    assert state.cost_basis == 47.90
    assert state.call_strike == 53.0


def test_a_call_past_its_date_says_record_what_happened():
    p = _parse(_shares_row(), _call(52.0, 80.0, days=-1,
                                    on=date.today() - timedelta(days=8)))
    sig = _signal(p, 51.0)
    assert sig.action == "expired"
    assert "expired worthless" in sig.reason.lower()


# ------------------------------------------------------------------ no rules
def test_a_live_call_never_triggers_a_target_or_stop():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    # Kept 90% of the credit: a credit spread would say "take the win".
    assert _signal(p, 50.0, cost=8.0).action == "hold"
    # Costs 4x the credit to close: a credit spread would say "stop".
    assert _signal(p, 51.0, cost=320.0).action == "hold"


def test_no_delta_red_flag_on_calls_sold_right_at_it():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    sig = _signal(p, 50.5, cost=60.0, delta=0.35)
    assert not any("red flag" in n for n in [sig.reason, *sig.notes])


def test_above_the_strike_reads_as_called_away_not_trouble():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    sig = _signal(p, 53.0, cost=110.0)
    text = " ".join([sig.reason, *sig.notes])
    assert "called away" in text
    assert "in trouble" not in text


# ------------------------------------------------------------------ falling stock
def test_a_small_dip_is_not_a_warning():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    sig = _signal(p, 46.0, cost=5.0)     # 6.5% under a 49.20 cost
    assert not any("no stop" in n for n in [sig.reason, *sig.notes])


def test_ten_percent_under_cost_warns_and_never_says_sell():
    p = _parse(_shares_row(), _call(52.0, 80.0))
    sig = _signal(p, 44.0, cost=2.0)     # 10.6% under a 49.20 cost
    assert sig.action == "watch"
    text = " ".join([sig.reason, *sig.notes])
    assert "10.6%" in text and "no stop" in text
    assert sig.action not in ("stop", "time")


def test_the_warning_shows_with_no_call_written_too():
    p = _parse(_shares_row())
    sig = _signal(p, 44.0)               # 12% under a 50.00 cost
    assert sig.action == "uncovered"
    assert any("no stop" in n for n in sig.notes)


# ------------------------------------------------------------------ checklist
def test_the_checklist_reminds_her_of_the_bullish_condition():
    leg = Leg(role="short_call", action=Action.SELL, option_type=OptionType.CALL,
              strike=52.0, dte=7, delta=0.30, premium=0.80)
    report = validate_trade(Trade(strategy_key=KEY, underlying="AAPL",
                                  contracts=1, legs=[leg], underlying_price=50.0))
    bullish = [r for r in report.results if r.name == "Bullish setups only"]
    assert bullish and bullish[0].status == CheckStatus.INFO
    assert report.passed


# ------------------------------------------------------------------ called away
def test_called_away_counts_as_a_planned_ending():
    from src.engine import month_report
    assert any(prefix == "shares called away" and by_rules
               for prefix, _, _, by_rules in month_report._EXIT_BUCKETS)
    assert "shares called away" in positions._SOP_EXIT_PREFIXES


# ------------------------------------------------------------------ ROI on the card
def test_the_card_roi_splits_this_call_the_premium_and_called_away():
    """$5,000 of shares bought 3 days ago; a 52 call sold today for $80,
    expiring in 7 days."""
    from src.engine import roi

    p = _parse(_shares_row(), _call(52.0, 80.0))
    r = roi.covered_call_roi(p, wheel.state_from(p, 51.0))
    assert r.base == 5_000.0
    # This week's call: $80 on $5,000 over its own 7 days.
    assert r.call_pct == 1.6 and r.call_days == 7
    assert r.call_annual_pct == pytest.approx(1.6 * 365 / 7, abs=0.1)
    # Premium so far: the same $80, but over the 3 days since the shares.
    assert r.premium_pct == 1.6 and r.premium_days == 3
    # Called away at 52: (52 - 49.20) x 100 = $280, over 10 days in all.
    assert r.away_result == 280.0 and r.away_pct == 5.6 and r.away_days == 10


def test_premium_so_far_keeps_counting_after_a_call_expires():
    from src.engine import roi

    p = _parse(_shares_row(opened=date.today() - timedelta(days=9)),
               _call(52.0, 80.0, days=-1, on=date.today() - timedelta(days=8)),
               _expired(on=date.today() - timedelta(days=1)))
    r = roi.covered_call_roi(p, wheel.state_from(p, 51.0))
    assert r.premium == 80.0 and r.premium_pct == 1.6
    assert r.call_pct is None and r.away_pct is None      # no call on now


def test_the_call_roi_uses_the_day_the_current_call_was_sold():
    from src.engine import roi

    sold = date.today() - timedelta(days=2)
    p = _parse(_shares_row(opened=date.today() - timedelta(days=12)),
               _call(52.0, 80.0, days=-3, on=date.today() - timedelta(days=10)),
               _expired(on=date.today() - timedelta(days=3)),
               _call(53.0, 60.0, days=5, on=sold))
    r = roi.covered_call_roi(p, wheel.state_from(p, 51.0))
    assert r.call_credit == 60.0 and r.call_days == 7
