"""LEAPS long call - the only strategy in the book that BUYS premium.

Every other strategy here collects a credit and profits as it decays. This one
pays a debit and profits only if the stock actually moves, which inverts the
arithmetic in sizing, in the exit rules, and in the payoff chart. These tests
pin the places that inversion could silently flip the wrong way.

All synthetic - invented strikes and premiums, never Rita's real positions
(this repo is public).
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.engine import exit_rules, payoff, rules, scanner, sizing, tos_ticket, validator
from src.engine.config_loader import get_strategy
from src.engine.models import Action, CheckStatus, Leg, OptionType, Trade
from src.engine.positions import Position

TODAY = dt.date(2026, 8, 7)
EXPIRY = dt.date(2027, 9, 17)


def _leg(strike=185.0, premium=39.0, delta=0.71, dte=400, open_interest=430):
    return Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
               strike=strike, premium=premium, quantity=1, dte=dte, delta=delta,
               open_interest=open_interest)


def _trade(contracts=1, **kw):
    return Trade(strategy_key="long_call_leaps", underlying="AMZN", contracts=contracts,
                 underlying_price=200.0, legs=[_leg(**kw)])


def _position(days_held=7, paid=3900.0):
    return Position(trade_id="T1", strategy_key="long_call_leaps", underlying="AMZN",
                    contracts=1, legs=[_leg()], open_cash=-paid,
                    opened=TODAY - dt.timedelta(days=days_held), expiration=EXPIRY)


@pytest.fixture(scope="module")
def strategy():
    return get_strategy("long_call_leaps")


# ------------------------------------------------------------------ the shape
def test_it_has_one_bought_leg_and_nothing_sold():
    """Rita's ruling: the covered-call overlay from the video is NOT part of
    this strategy. Selling calls against a LEAPS is the PMCC, kept separate."""
    legs = get_strategy("long_call_leaps")["legs"]
    assert len(legs) == 1
    assert legs[0]["action"] == "buy"
    assert legs[0]["option_type"] == "call"


def test_it_is_not_the_pmcc(strategy):
    """Both buy a LEAPS; they are different trades and must not converge. The
    giveaway is that this one never sells anything against it - that overlay is
    the PMCC and Rita ruled it out of scope here."""
    pmcc = get_strategy("poor_mans_covered_call")
    assert strategy["family"] != pmcc["family"]
    assert "short_call_delta" not in strategy["entry"]
    assert "short_call_dte_target" not in strategy["entry"]


def test_it_has_no_stop_loss_and_that_is_deliberate(strategy):
    """Risk is controlled by SIZE (10% across ~3 names) and TIME (400+ days),
    not a stop. A long call gaps through stops, and cutting at a fixed loss
    guarantees missing the recovery the extra time was bought for. If a stop
    ever appears here it is a mistake, not an improvement."""
    assert "stop_loss_pct" not in strategy["exit"]
    assert "stop_loss_multiple" not in strategy["exit"]
    assert strategy["sizing"]["max_pct_of_account"] == 10
    assert strategy["entry"]["dte_target"] >= 400


# ------------------------------------------------------------------ the money
def test_what_you_paid_is_exactly_what_you_can_lose(strategy):
    """No credit to net off and no assignment to fall back on. Capital, max
    loss and the debit are all the same number, and buying power is zero
    because a bought option costs cash, not margin."""
    size = sizing.estimate(_trade(), strategy)
    assert size["credit"] == 0.0
    assert size["capital"] == 3900.0
    assert size["max_loss"] == 3900.0
    assert size["buying_power"] == 0.0


def test_the_debit_scales_with_contracts(strategy):
    assert sizing.estimate(_trade(contracts=3), strategy)["max_loss"] == 11700.0


# ------------------------------------------------------------------ the rules
def _check(report, fragment):
    return next(c for c in report.results if fragment in c.name)


def test_the_delta_is_a_floor_and_deeper_is_fine():
    """"70 delta or higher", and out-of-the-money is banned outright. Deeper is
    never the error - it is more like the stock, which is the point."""
    too_shallow = validator.validate_trade(_trade(strike=230.0, premium=12.0, delta=0.35))
    deeper = validator.validate_trade(_trade(strike=120.0, premium=82.0, delta=0.95))

    assert _check(too_shallow, "Bought call delta").status == CheckStatus.FAIL
    assert _check(deeper, "Bought call delta").status == CheckStatus.PASS
    assert _check(validator.validate_trade(_trade()), "Bought call delta").status \
        == CheckStatus.PASS


def test_thin_contracts_are_flagged():
    """A LEAPS is held for months and exited in one sale, so the bid-ask gap on
    a thin strike eats the move you were right about."""
    thin = validator.validate_trade(_trade(open_interest=43))
    assert _check(thin, "Open interest").status == CheckStatus.WARN
    assert _check(validator.validate_trade(_trade()), "Open interest").status \
        == CheckStatus.PASS


def test_the_cap_counts_every_open_leaps_not_just_this_one():
    """Three separate 8% positions is a 24% bet on one idea wearing three
    tickers. The monthly buying-power check cannot catch any of it - a bought
    call reports zero buying power and sails through."""
    alone = validator.validate_trade(_trade())
    assert _check(alone, "% of the account").status == CheckStatus.PASS

    oversized = validator.validate_trade(_trade(contracts=4))       # $15.6k on $100k
    assert _check(oversized, "% of the account").status == CheckStatus.FAIL

    # Fine on its own, over the line once existing LEAPS are counted.
    with_others = validator.validate_trade(_trade(contracts=2), open_leaps_capital=8000.0)
    assert _check(with_others, "% of the account").status == CheckStatus.FAIL


def test_it_is_never_asked_to_produce_a_credit():
    """Ryan gets this wrong in the video and a commenter corrects him: you are
    not assigned on an option you BOUGHT. Nothing here should demand a credit
    or warn about assignment."""
    names = " ".join(c.name for c in validator.validate_trade(_trade()).results)
    assert "credit" not in names.lower()
    assert "assign" not in names.lower()


# ------------------------------------------------------------- the payoff
def test_the_breakeven_is_strike_plus_premium(strategy):
    """The grid used to be drawn from the strikes and spot alone, so a $185
    call costing $39 (breakeven $224) charted a position that never turned
    green and reported no breakeven at all."""
    profile = payoff.profile(_trade(), strategy)
    assert profile.breakevens == [224.0]
    assert profile.max_loss == -3900.0
    assert profile.prices[-1] > 224.0     # the window actually reaches it


def test_the_ticket_says_buy(strategy):
    line = tos_ticket.ticket_line(_trade(), today=TODAY)
    assert line.startswith("BUY +1 AMZN")
    assert "CALL" in line


# -------------------------------------------------------------- the exits
@pytest.fixture(scope="module")
def exit_cfg():
    return get_strategy("long_call_leaps")["exit"]


def _signal(cfg, value, days_held=7):
    """value = what the call is worth today. cost_to_close is its negative,
    because closing a position you only bought PAYS you."""
    return exit_rules.evaluate(_position(days_held), cfg,
                               current_cost=-value, today=TODAY)


def test_a_call_that_doubled_is_not_reported_as_a_loss(exit_cfg):
    """The sign trap. Every other strategy profits when cost-to-close FALLS;
    this one profits when its value RISES. Wire it into the credit path and a
    winner reads as a total loss."""
    signal = _signal(exit_cfg, 7800.0)
    assert signal.tone == "green"
    assert signal.action == "close"
    assert "100%" in signal.headline


def test_the_seven_day_lightning_exit(exit_cfg):
    """10-20% inside a week is a take-it, however small it looks."""
    assert _signal(exit_cfg, 4368.0, days_held=5).action == "close"      # +12% in 5 days
    # The same 12% a month later is not a lightning exit and not yet 20%.
    assert _signal(exit_cfg, 4368.0, days_held=40).action == "hold"


def test_the_four_week_exit(exit_cfg):
    assert _signal(exit_cfg, 4875.0, days_held=21).action == "close"     # +25% in 3 weeks
    assert _signal(exit_cfg, 4875.0, days_held=200).action == "hold"


def test_the_trade_that_taught_the_rule(exit_cfg):
    """Up 30% in two weeks, held out for more, spent four months at -50% and
    scraped out at +20%. Both ends of that must read correctly."""
    assert _signal(exit_cfg, 5070.0, days_held=14).action == "close"     # +30%, take it
    underwater = _signal(exit_cfg, 1950.0, days_held=120)                # -50%
    assert underwater.action == "watch"
    assert underwater.tone == "amber"


def test_a_big_loss_is_held_not_stopped(exit_cfg):
    """There is no stop by design, so a deep loss must never say 'close'. It
    should explain WHY holding is the plan and what would change it."""
    signal = _signal(exit_cfg, 1560.0, days_held=90)        # -60%
    assert signal.action != "close"
    assert "no stop" in signal.reason.lower()
    assert "earnings" in signal.reason.lower()              # what would change the call
    assert _signal(exit_cfg, 3510.0).action == "hold"       # -10%, nothing to do


def test_a_missing_price_holds_rather_than_guessing(exit_cfg):
    signal = exit_rules.evaluate(_position(), exit_cfg, current_cost=None, today=TODAY)
    assert signal.action == "hold"


def test_the_six_month_theta_warning_appears(exit_cfg):
    near = Position(trade_id="T2", strategy_key="long_call_leaps", underlying="AMZN",
                    contracts=1, legs=[_leg()], open_cash=-3900.0,
                    opened=dt.date(2026, 1, 5), expiration=TODAY + dt.timedelta(days=120))
    signal = exit_rules.evaluate(near, exit_cfg, current_cost=-4000.0, today=TODAY)
    assert any("six months" in n for n in signal.notes)


def test_it_is_not_mistaken_for_a_pmcc_between_short_calls(exit_cfg):
    """Both are debit positions with no short call. The PMCC's message tells
    her to write the next call, which would be nonsense here."""
    position = _position()
    assert position.is_long_premium is True
    assert position.is_uncovered is False
    assert exit_rules.evaluate(position, exit_cfg, current_cost=-4000.0,
                               today=TODAY).action != "uncovered"


# -------------------------------------------------------------- the scan
# ------------------------------------- the Analyze tab must obey the same SOP
# The LEAPS Finder predates the strategy and carried its OWN numbers: a 300-day
# floor, 0.75 delta, 100 open interest, and an entry score that rewarded RSI
# 45-70 while calling oversold "catching a falling knife". Her SOP buys exactly
# what that penalised, so the Analyze tab was talking her out of the entry Find
# a trade was built to check.
def test_the_finder_reads_its_numbers_from_the_sop(strategy):
    from src.research import leaps as finder

    entry = strategy["entry"]
    assert finder.min_leap_dte() == entry["dte_min"] == 365
    assert finder.target_delta() == entry["long_leg_delta_min"] == 0.70
    assert finder.min_open_interest() == entry["min_open_interest"] == 250
    assert finder.vix_min() == entry["vix_min"]
    assert finder.rsi_max() == entry["rsi_max"]
    assert finder.Filters().min_open_interest == 250


def test_the_finder_scores_the_sop_entry_higher_than_the_opposite():
    """A stock at the LOWER band with soft RSI must beat one pressed against the
    upper band. This is the assertion that would have caught the old scoring."""
    from src.research import leaps as finder

    base = [50.0 + i * 0.25 for i in range(400)]
    pulled_back = base + [base[-1] * (1 - 0.012 * i) for i in range(1, 13)]
    stretched = base + [base[-1] * (1 + 0.012 * i) for i in range(1, 13)]

    assert finder.score_entry(pulled_back).score > finder.score_entry(stretched).score
    assert finder.band_position(pulled_back) < finder.band_position(stretched)


def test_a_collapse_is_not_scored_as_a_bargain():
    """SOP criterion 4: the drop must be the MARKET falling, never a broken
    story. A name 45% off its high is pinned to its lower band too, and would
    otherwise read as a textbook entry."""
    from src.research import leaps as finder

    base = [50.0 + i * 0.25 for i in range(400)]
    dip = base + [base[-1] * 0.94]
    collapse = base + [base[-1] * 0.55]
    assert finder.score_entry(dip).score > finder.score_entry(collapse).score
    assert any("broken chart" in f for f in finder.score_entry(collapse).factors)


def test_the_market_board_treats_fear_as_the_buy_signal(strategy):
    """The sign most likely to get "corrected" back. This strategy will not
    enter below VIX 15 at all - fear means the stock is on sale - so a calm
    market must score it LOWER, not higher, unlike every option-buying
    strategy's usual vega logic."""
    from src.data.market_context import build_context

    calm = {s.strategy_key: s.score for s in
            build_context("SPX", 5100, vix=12.0, trend="up", trend_spread=0.02).board}
    fearful = {s.strategy_key: s.score for s in
               build_context("SPX", 5100, vix=26.0, trend="up", trend_spread=0.02).board}
    assert fearful["long_call_leaps"] > calm["long_call_leaps"]
    # ...and it moves the OPPOSITE way to the PMCC, which buys its LEAPS to hold.
    assert fearful["poor_mans_covered_call"] < calm["poor_mans_covered_call"]


def test_the_scanner_respects_a_floor_not_a_band():
    """Deeper than target is valid - it just behaves more like the shares. A
    band would have silently skipped a 0.90 contract. Below the floor there is
    no candidate at all: skipping an expiration beats handing back the delta the
    whole strategy rests on."""
    from src.data.chain import OptionContract

    def contract(strike, delta, mid):
        return OptionContract(option_type=OptionType.CALL, strike=strike,
                              expiration="2027-09-17", dte=400, bid=mid - 0.5,
                              ask=mid + 0.5, delta=delta, iv=0.30, open_interest=500)

    strategy = get_strategy("long_call_leaps")

    # Nearest the 0.72 target wins.
    mixed = [contract(230, 0.35, 12.0), contract(185, 0.74, 39.0), contract(120, 0.93, 82.0)]
    assert scanner._pick_long_call(mixed, strategy).strike == 185

    # Deeper than target is still eligible when it is all that is on offer.
    deep_only = [contract(230, 0.35, 12.0), contract(120, 0.93, 82.0)]
    assert scanner._pick_long_call(deep_only, strategy).strike == 120

    # Everything below the floor -> no candidate.
    assert scanner._pick_long_call([contract(230, 0.35, 12.0)], strategy) is None
