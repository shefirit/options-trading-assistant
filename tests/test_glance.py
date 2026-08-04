"""The one sentence that says how a trade is going.

The My trades tab could show the instruction ("Hold") and it could show eleven
columns of numbers, but joining the two into "how is this actually going?" was
left to her, for every position, every day.

All figures invented - this repo is public.
"""

from datetime import date, timedelta

from src.engine import glance
from src.engine.exit_rules import ExitSignal
from src.engine.models import Action, Leg, OptionType
from src.engine.positions import Position


def _spread(credit: float = 500.0, dte: int = 12, strike: float = 90.0) -> Position:
    today = date.today()
    return Position(
        trade_id="T1", underlying="XYZ", strategy_key="put_credit_spread",
        strategy_name="Put Credit Spread (Bull Put Spread)",
        credit=credit, open_credit=credit, open_cash=credit, contracts=1,
        opened=today - timedelta(days=20),
        expiration=today + timedelta(days=dte),
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=strike, delta=-0.25, premium=credit / 100, dte=dte),
              Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                  strike=strike - 5, delta=-0.15, premium=1.0, dte=dte)])


def _sig(action: str = "hold") -> ExitSignal:
    return ExitSignal(action=action, headline="", reason="", tone="neutral")


# ------------------------------------------------------------- the money first
def test_it_says_the_dollars_kept_not_only_a_percentage():
    """"62%" is the thing she has to translate. Translating it is the app's
    job, so the sentence leads with the money."""
    line = glance.summary_line(_spread(credit=500.0), {"cost_to_close": 190.0},
                               _sig())
    assert "kept $310 of the $500 credit" in line.lower()
    assert "62%" in line


def test_a_losing_trade_says_what_it_costs_rather_than_a_negative_percentage():
    """A negative percentage of a credit reads like a discount. What she needs
    is the dollars: closing costs more than she was paid."""
    line = glance.summary_line(_spread(credit=500.0), {"cost_to_close": 800.0},
                               _sig("stop"))
    assert "closing it costs $800 against the $500 you collected" in line.lower()
    assert "$300 down" in line


def test_days_left_is_part_of_the_same_sentence():
    line = glance.summary_line(_spread(dte=12), {"cost_to_close": 100.0}, _sig())
    assert "12 days left" in line


def test_one_day_left_is_not_written_as_1_days():
    line = glance.summary_line(_spread(dte=1), {"cost_to_close": 100.0}, _sig())
    assert "1 day left" in line and "1 days" not in line


def test_expiring_today_says_so():
    line = glance.summary_line(_spread(dte=0), {"cost_to_close": 100.0}, _sig())
    assert "expires today" in line


# ------------------------------------------------------------ room to a strike
def test_it_says_how_far_price_is_from_the_option_she_sold():
    line = glance.summary_line(_spread(strike=90.0),
                               {"cost_to_close": 100.0,
                                "underlying_price": 100.0}, _sig())
    assert "clear of your 90 put" in line


def test_a_breached_strike_is_stated_plainly():
    line = glance.summary_line(_spread(strike=90.0),
                               {"cost_to_close": 900.0,
                                "underlying_price": 85.0}, _sig("stop"))
    assert "price is PAST your 90 put" in line


def test_it_admits_when_there_is_nothing_to_say():
    """No price data means no sentence - better than a confident-looking line
    built from nothing."""
    bare = Position(trade_id="T", underlying="XYZ", credit=0.0)
    assert "Not enough price data" in glance.summary_line(bare, {}, _sig())


def test_the_sentence_reads_like_a_sentence():
    line = glance.summary_line(_spread(), {"cost_to_close": 190.0}, _sig())
    assert line[0].isupper() and line.endswith(".")


# -------------------------------------------------- the money the credit hides
def test_a_pmcc_also_reports_the_whole_position():
    """The short call's credit is a small slice of a PMCC. Telling only that
    story is true and misleading - the LEAPS holds most of the money."""
    p = Position(trade_id="P1", underlying="XYZ", credit=500.0,
                 open_cash=-9500.0, contracts=1)
    line = glance.whole_trade_line(p, {"open_pl": 1240.0})
    assert "up $1,240" in line
    assert "$9,500 you laid out" in line


def test_a_credit_spread_has_no_whole_position_line():
    """Nothing is hidden on a spread - the credit IS the trade."""
    assert glance.whole_trade_line(_spread(), {"open_pl": 100.0}) is None


def test_no_whole_position_line_when_the_long_leg_could_not_be_priced():
    p = Position(trade_id="P1", underlying="XYZ", credit=500.0, open_cash=-9500.0)
    assert glance.whole_trade_line(p, {}) is None


# --------------------------------------------------------------------- sorting
def test_the_trades_needing_a_decision_sort_first():
    """The order she scrolls has to be the order she should act in."""
    actions = ["hold", "stop", "watch", "profit", "time", "unpriced"]
    ordered = sorted(actions,
                     key=lambda a: glance.priority(_sig(a), _spread()))
    assert ordered[:3] == ["stop", "time", "profit"]
    assert ordered[-1] == "hold"


def test_within_the_same_urgency_the_soonest_expiry_comes_first():
    a = (_sig("watch"), _spread(dte=30))
    b = (_sig("watch"), _spread(dte=3))
    assert glance.priority(*b) < glance.priority(*a)
