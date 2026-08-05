"""The process measures - how well she trades, not how much she made.

performance() answers "how much". quality() answers "how repeatably", which is
the half a dashboard is for while she is learning.

The two behaviours worth protecting are both refusals to invent a number:
profit factor is None when nothing has lost yet, and `confidence` says out loud
when there is too little history to read anything into.

Every number here is invented. This repo is public.
"""

from __future__ import annotations

from datetime import date

from src.engine.positions import Position, RollEvent, quality


def _closed(trade_id, closed_on, pl, opened=date(2026, 7, 1)) -> Position:
    return Position(
        trade_id=trade_id, underlying="SPX", strategy_name="Put Credit Spread",
        opened=opened, credit=300.0, open_credit=300.0, open_cash=300.0,
        status="closed", closed_on=closed_on, realized_pl=pl,
        exit_reason="Profit target (50%) hit")


# ------------------------------------------------------------- profit factor
def test_profit_factor_is_none_when_nothing_has_lost_yet():
    """A beginner's first months are routinely all winners. Dividing by zero
    losses gives infinity, which is not a number to put on a card."""
    wins = [_closed("a", date(2026, 8, 1), 300.0),
            _closed("b", date(2026, 8, 2), 200.0)]
    assert quality(wins)["profit_factor"] is None


def test_profit_factor_is_gross_wins_over_gross_losses():
    trades = [_closed("a", date(2026, 8, 1), 600.0),
              _closed("b", date(2026, 8, 2), 300.0),
              _closed("c", date(2026, 8, 3), -300.0)]
    q = quality(trades)
    assert q["profit_factor"] == 3.0          # 900 won for 300 lost


def test_an_empty_log_answers_none_everywhere_rather_than_zero():
    q = quality([])
    assert q["closed_count"] == 0
    assert q["profit_factor"] is None
    assert q["expectancy"] is None
    assert q["max_drawdown"] == 0.0


# ---------------------------------------------------------------- expectancy
def test_expectancy_is_what_one_closed_trade_has_been_worth():
    trades = [_closed("a", date(2026, 8, 1), 600.0),
              _closed("b", date(2026, 8, 2), -200.0)]
    assert quality(trades)["expectancy"] == 200.0


def test_the_average_winner_and_loser_are_kept_apart():
    trades = [_closed("a", date(2026, 8, 1), 600.0),
              _closed("b", date(2026, 8, 2), 400.0),
              _closed("c", date(2026, 8, 3), -250.0)]
    q = quality(trades)
    assert q["avg_win"] == 500.0
    assert q["avg_loss"] == -250.0
    assert q["payoff_ratio"] == 2.0


# ------------------------------------------------------------------ drawdown
def test_drawdown_measures_the_dip_below_the_best_day_and_recovers():
    """Up to 1,000, down to 400, back to 900. The worst dip was 600, and the
    fact that it recovered does not erase it."""
    trades = [_closed("a", date(2026, 8, 1), 1000.0),
              _closed("b", date(2026, 8, 2), -600.0),
              _closed("c", date(2026, 8, 3), 500.0)]
    q = quality(trades)
    assert q["max_drawdown"] == -600.0
    assert q["max_drawdown_pct"] == 0.6       # 600 off a 1,000 peak
    assert q["current_drawdown"] == -100.0    # 900 against the 1,000 peak


def test_a_curve_that_only_rises_has_no_drawdown():
    trades = [_closed("a", date(2026, 8, 1), 300.0),
              _closed("b", date(2026, 8, 2), 400.0)]
    q = quality(trades)
    assert q["max_drawdown"] == 0.0
    assert q["current_drawdown"] == 0.0


def test_the_first_losing_trade_of_an_account_is_a_real_drawdown():
    """The peak starts at zero, so an account that opens with a loss is down -
    not dividing by nothing."""
    q = quality([_closed("a", date(2026, 8, 1), -400.0)])
    assert q["max_drawdown"] == -400.0
    assert q["max_drawdown_pct"] is None      # no positive peak to measure against


def test_a_roll_credit_counts_in_the_curve_on_the_day_it_was_rolled():
    """Same contract as cash_events: money from a roll is hers that day, even
    on a trade that is still open."""
    p = Position(trade_id="r", underlying="SPY", strategy_name="PMCC",
                 opened=date(2026, 8, 1), credit=0.0, open_credit=0.0,
                 open_cash=-5000.0, status="open",
                 rolls=[RollEvent(rolled_on=date(2026, 8, 3), cash=250.0)])
    q = quality([p])
    assert q["closed_count"] == 0
    assert q["max_drawdown"] == 0.0           # the curve only went up


# -------------------------------------------------------------------- streak
def test_a_streak_counts_the_most_recent_run_not_any_run():
    trades = [_closed("a", date(2026, 8, 1), 300.0),
              _closed("b", date(2026, 8, 2), 300.0),
              _closed("c", date(2026, 8, 3), 300.0),
              _closed("d", date(2026, 8, 4), -100.0)]
    assert quality(trades)["streak"] == -1


def test_wins_running_are_positive_and_losses_running_are_negative():
    wins = [_closed("a", date(2026, 8, 1), 300.0),
            _closed("b", date(2026, 8, 2), 300.0)]
    assert quality(wins)["streak"] == 2
    losses = [_closed("a", date(2026, 8, 1), -300.0),
              _closed("b", date(2026, 8, 2), -300.0),
              _closed("c", date(2026, 8, 3), -300.0)]
    assert quality(losses)["streak"] == -3


# ---------------------------------------------------------------- confidence
def test_confidence_says_thin_before_five_closes():
    """This is what stops the dashboard printing a profit factor off two trades
    as though it meant something."""
    trades = [_closed(str(i), date(2026, 8, i + 1), 300.0) for i in range(4)]
    assert quality(trades)["confidence"] == "thin"


def test_confidence_builds_and_then_settles():
    at_five = [_closed(str(i), date(2026, 8, 1), 300.0) for i in range(5)]
    assert quality(at_five)["confidence"] == "building"
    at_twenty = [_closed(str(i), date(2026, 8, 1), 300.0) for i in range(20)]
    assert quality(at_twenty)["confidence"] == "ok"


def test_one_book_only_is_the_callers_job_and_the_maths_respects_it():
    """quality() never splits real from practice itself - it answers about the
    list it was given, exactly as performance() does."""
    real = [_closed("r", date(2026, 8, 4), 600.0)]
    assert quality(real)["expectancy"] == 600.0
