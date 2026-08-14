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


def _leg(strike=185.0, premium=39.0, delta=0.71, dte=400, open_interest=430,
         spread=2.0):
    """`spread` is the bid-ask gap as a percentage of the mid, so a test can
    ask for a specific fill quality. The default is a tight, tradable quote;
    the bid/ask straddle `premium` so the mid stays exactly what was asked for."""
    half = premium * spread / 200
    return Leg(role="long_call_leaps", action=Action.BUY, option_type=OptionType.CALL,
               strike=strike, premium=premium, quantity=1, dte=dte, delta=delta,
               open_interest=open_interest,
               bid=round(premium - half, 2), ask=round(premium + half, 2))


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


@pytest.fixture
def market_open(monkeypatch):
    """Pin the market to OPEN for spread tests that go through the validator.

    The bid-ask rule softens a refusal to a warning while New York is shut,
    because the delayed feed quotes wide on a closed market. That is correct
    behaviour and it makes any test of the refusal time-dependent - green in
    Rita's evening, red in her morning. Caught exactly that way. The closed
    branch is tested separately by calling the rule directly.
    """
    monkeypatch.setattr(validator.market_calendar, "quotes_are_stale", lambda *a, **k: None)


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


def test_the_spread_is_reported_and_never_refuses_a_trade(market_open):
    """Her ruling, 2026-08-14: "pay attention but do not place strong
    limitations." An earlier version this same day failed a trade over it. Even
    FE's 54% - 490 contracts of open interest and half the value in the spread -
    is information here, not a refusal."""
    for pct in (4.0, 14.0, 24.0, 54.0):
        check = _check(validator.validate_trade(_trade(spread=pct)),
                       "bid-ask spread")
        assert check.status == CheckStatus.INFO, pct


def test_the_financing_put_band_is_settled_at_the_csp_delta():
    """Settled 2026-08-14: "2 puts, each at delta 0.20-0.30, if needed to cover
    the long call cost." The band does NOT widen - depth was the rejected lever,
    because a deeper put's strike sits at or above the stock while two shallow
    ones sit well below it. Her CSP sells at 0.30 and this never goes past it."""
    fp = get_strategy("long_call_leaps")["financing_put"]
    assert (fp["delta_min"], fp["delta_max"]) == (0.20, 0.30)
    assert fp["delta_max"] <= get_strategy("cash_secured_put")["entry"]["short_leg_delta_max"]
    assert fp["ratio"] == 1 and fp["max_ratio"] == 2
    assert fp["enabled"] is False


def test_two_puts_are_held_back_by_buying_power_not_by_a_new_rule():
    """Why the band needs no extra cap: two 0.26 delta puts on a $300 stock
    reserve essentially the whole $50,000 month, so the existing check stops it
    the moment anything else is open."""
    leg = _leg()
    put = Leg(role="financing_put", action=Action.SELL, option_type=OptionType.PUT,
              strike=270.0, premium=20.75, dte=leg.dte, delta=-0.26, quantity=2,
              open_interest=400, bid=20.3, ask=21.2)
    trade = Trade(strategy_key="long_call_leaps", underlying="AAPL", contracts=1,
                  underlying_price=305.5, legs=[leg, put])

    alone = validator.validate_trade(trade, existing_month_bp=0)
    crowded = validator.validate_trade(trade, existing_month_bp=5_000)

    assert _check(alone, "Puts sold per call").status == CheckStatus.WARN
    assert _check(crowded, "buying power").status == CheckStatus.FAIL
    assert not crowded.passed


def test_no_bid_ask_threshold_lives_in_config():
    """The guard on the rule she removed. If this fails, someone put a limit
    back into her SOP without her asking."""
    for key in ("long_call_leaps", "poor_mans_covered_call", "put_credit_spread",
                "call_credit_spread", "iron_condor", "cash_secured_put"):
        entry = get_strategy(key)["entry"]
        assert "max_bid_ask_pct" not in entry, key
        assert "warn_bid_ask_pct" not in entry, key


def test_the_spread_note_says_what_it_costs_in_dollars(market_open):
    """A percentage does not land; "about $780" does."""
    report = validator.validate_trade(_trade(premium=39.0, spread=20.0, contracts=1))
    assert "$780" in _check(report, "bid-ask spread").message


def test_a_wide_spread_is_still_called_wide(market_open):
    tight = _check(validator.validate_trade(_trade(spread=4.0)), "bid-ask spread")
    awful = _check(validator.validate_trade(_trade(spread=54.0)), "bid-ask spread")
    assert "Tight" in tight.message
    assert "very wide" in awful.message


def test_a_missing_quote_is_reported_not_guessed_at():
    leg = _leg()
    leg.bid, leg.ask = None, None
    trade = Trade(strategy_key="long_call_leaps", underlying="AMZN", contracts=1,
                  underlying_price=200.0, legs=[leg])
    check = _check(validator.validate_trade(trade), "bid-ask spread")
    assert check.status == CheckStatus.INFO
    assert "could not be measured" in check.message


def test_a_closed_market_is_named_so_the_number_is_not_taken_literally():
    """Her delayed feed quotes wide when New York is shut and she scans during
    her afternoon, so half the board would read as untradable."""
    shut = rules.check_bought_call_spread(_trade(spread=24.0),
                                          stale_reason="the weekend")
    assert shut.status == CheckStatus.INFO
    assert "the weekend" in shut.message


def test_the_spread_note_only_reaches_strategies_that_buy_calls():
    """It returns None when nothing in the trade is a bought call, which is what
    keeps it off her credit spreads - there a percentage spread on a cheap
    option reads high and means nothing."""
    sold_only = Trade(
        strategy_key="put_credit_spread", underlying="SPX", contracts=1,
        underlying_price=5000.0,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=4800, premium=3.0, dte=40, delta=-0.25, bid=2.0, ask=4.0)])
    assert rules.check_bought_call_spread(sold_only) is None


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


# -------------------------------------------- the optional financing put
# Rita's addition, 2026-08-13: sell a 0.20-0.30 delta put at the SAME expiration
# to part-pay for the call (a risk reversal). It is a VARIANT - off by default -
# because it breaks the sentence the rest of this strategy rests on, "the most
# you can lose is the premium paid". These tests pin that it stays optional and
# that the money maths tells the truth when it is on.
FIN_PUT_STRIKE = 150.0
FIN_PUT_PREMIUM = 12.0


def _put_leg(strike=FIN_PUT_STRIKE, premium=FIN_PUT_PREMIUM, delta=-0.25, dte=400):
    return Leg(role="financing_put", action=Action.SELL, option_type=OptionType.PUT,
               strike=strike, premium=premium, quantity=1, dte=dte, delta=delta,
               open_interest=400)


def _reversal(contracts=1, put=None, **kw):
    """The variant: the same bought call, plus the financing put."""
    return Trade(strategy_key="long_call_leaps", underlying="AMZN", contracts=contracts,
                 underlying_price=200.0,
                 legs=[_leg(**kw), put if put is not None else _put_leg()])


def test_the_financing_put_is_off_by_default(strategy):
    """It must never appear unasked. `legs` stays one bought call - the config
    block only supplies numbers for when she opts in."""
    assert len(strategy["legs"]) == 1
    assert strategy["financing_put"]["enabled"] is False
    assert strategy["financing_put"]["delta_min"] == 0.20
    assert strategy["financing_put"]["delta_max"] == 0.30


def test_the_plain_leaps_checklist_is_untouched():
    """The whole point of "variant, not default": switching this feature into
    the config must not add a single check to her ordinary one-leg trade."""
    report = validator.validate_trade(_trade())
    names = " ".join(c.name for c in report.results).lower()
    assert "financing put" not in names
    assert "same day" not in names
    # ...and it still must not ask a BOUGHT call for a credit or an assignment.
    assert "credit" not in names and "assign" not in names


def test_the_credit_pays_down_the_debit_but_not_the_collateral(strategy):
    """The arithmetic that is easiest to get wrong, and it flatters the trade
    when you do. Buying the $39 call is $3,900 out; selling the $150 put pays
    $1,200 back, so the DEBIT is $2,700 - but $15,000 still has to sit behind
    that put. Netting the credit off both makes the position look $1,200
    cheaper than it is."""
    size = sizing.estimate(_reversal(), strategy)
    assert size["debit"] == 2700.0                  # 3900 paid - 1200 collected
    assert size["capital"] == 17700.0               # 2700 + the full 15000 collateral
    assert size["buying_power"] == 13800.0          # 15000 - 1200, same as her CSP


def test_the_worst_case_is_no_longer_the_premium_paid(strategy):
    """The headline risk change. Alone, the call can lose $3,900. With the put
    attached, a stock that goes to zero costs the debit AND the assignment."""
    alone = sizing.estimate(_trade(), strategy)["max_loss"]
    with_put = sizing.estimate(_reversal(), strategy)["max_loss"]
    assert alone == 3900.0
    assert with_put == 17700.0
    assert with_put > alone * 4


def test_the_credit_is_never_reported_as_income(strategy):
    """It part-pays for a call; it is not premium she gets to keep. If this ever
    starts feeding the credit line, the dashboards will show a debit trade
    earning money."""
    assert sizing.estimate(_reversal(), strategy)["credit"] == 0.0
    assert sizing.estimate(_reversal(), strategy)["return_on_risk"] == 0.0


def test_the_ten_percent_cap_is_measured_on_the_debit_not_the_collateral():
    """Her SOP caps LEAPS at 10% of the account on premium PAID. Measure it
    against capital instead and every financing-put trade fails at 17.6% for
    the wrong reason - the collateral belongs to the buying-power limit."""
    report = validator.validate_trade(_reversal())
    check = _check(report, "% of the account")
    assert check.status == CheckStatus.PASS         # $2,600 net on $100k
    # ...and it says so, rather than repeating "every cent of that can be lost".
    assert "not your worst case" in check.message


def test_the_put_delta_is_a_band_not_a_floor():
    """The opposite of the bought call's rule, sitting inches away from it. Too
    deep is the danger on a put you SELL - it pays more precisely because it is
    likelier to hand you 100 shares."""
    ok = validator.validate_trade(_reversal())
    too_deep = validator.validate_trade(_reversal(put=_put_leg(strike=185.0, delta=-0.45)))
    too_far = validator.validate_trade(_reversal(put=_put_leg(strike=110.0, delta=-0.08)))

    assert _check(ok, "Financing put delta").status == CheckStatus.PASS
    assert _check(too_deep, "Financing put delta").status == CheckStatus.FAIL
    assert _check(too_far, "Financing put delta").status == CheckStatus.FAIL


def test_mismatched_expirations_are_caught():
    """A gap turns one trade into two, and the leftover short put cannot be
    rolled forward - there is nothing listed beyond a LEAPS expiration."""
    matched = validator.validate_trade(_reversal())
    mismatched = validator.validate_trade(_reversal(put=_put_leg(dte=200)))
    assert _check(matched, "same day").status == CheckStatus.PASS
    assert _check(mismatched, "same day").status == CheckStatus.FAIL


def _ratio(n):
    """The variant with n puts sold against the one bought call."""
    trade = _reversal()
    trade.legs[1].quantity = n
    return trade


def test_two_puts_are_allowed_but_never_silently():
    """Rita's point, 2026-08-13: one 0.25 delta put funds only about a quarter
    of a 0.70 delta call, so "sell a put to pay for it" barely does. Two is the
    honest answer - but the second one is uncovered, so it warns rather than
    passing green. Three is a different trade wearing this one's name."""
    one = _check(validator.validate_trade(_ratio(1)), "Puts sold per call")
    two = _check(validator.validate_trade(_ratio(2)), "Puts sold per call")
    three = _check(validator.validate_trade(_ratio(3)), "Puts sold per call")

    assert one.status == CheckStatus.PASS
    assert two.status == CheckStatus.WARN
    assert three.status == CheckStatus.FAIL


def test_the_second_put_is_named_as_uncovered():
    """The call balances the first put and does nothing for the second. If that
    ever reads as ordinary, she is carrying twice the downside for a cheaper
    debit without being told."""
    msg = _check(validator.validate_trade(_ratio(2)), "Puts sold per call").message
    assert "$200 per point instead of $100" in msg
    assert "200 shares" in msg
    assert "Model 3" in msg              # the same shape her SOP calls ADVANCED


def test_the_second_put_doubles_the_collateral_and_the_worst_case(strategy):
    """Where two puts actually bite. The debit halves again - which is the
    attraction - while the cash committed and the loss both climb."""
    one = sizing.estimate(_ratio(1), strategy)
    two = sizing.estimate(_ratio(2), strategy)

    assert one["debit"] == 2700.0 and two["debit"] == 1500.0      # 3900 - 2x1200
    assert two["capital"] == 31500.0                             # 1500 + 2x15000
    assert two["max_loss"] == two["capital"]
    assert two["buying_power"] == 27600.0                        # 30000 - 2400
    # The cheaper debit is the trap: it FALLS while the risk doubles.
    assert two["debit"] < one["debit"]
    assert two["max_loss"] > one["max_loss"] * 1.7


def test_two_puts_lose_at_twice_the_rate_below_the_strike(strategy):
    """The accelerating shape, drawn rather than described. Below the put strike
    the two-put version must fall away twice as steeply."""
    lo = FIN_PUT_STRIKE - 50
    one = payoff.value_at(_ratio(1), lo)
    two = payoff.value_at(_ratio(2), lo)
    one_further = payoff.value_at(_ratio(1), lo - 10)
    two_further = payoff.value_at(_ratio(2), lo - 10)

    assert two < one
    assert (two - two_further) == pytest.approx(2 * (one - one_further), rel=0.01)


def test_the_scanner_cannot_build_more_than_the_cap():
    """The cap lives in her config and the scanner must respect it even when
    asked for more - it must never produce a setup the checklist would reject."""
    from src.data.chain import OptionChain, OptionContract

    def contract(otype, strike, delta, mid):
        return OptionContract(option_type=otype, strike=strike, expiration="2027-09-17",
                              dte=400, bid=mid - 0.5, ask=mid + 0.5, delta=delta,
                              iv=0.30, open_interest=500)

    chain = OptionChain(underlying="AMZN", underlying_price=200.0, contracts=[
        contract(OptionType.CALL, 185, 0.74, 39.0),
        contract(OptionType.PUT, 150, -0.25, 12.0),
    ])

    def sold(ratio):
        found = scanner.scan_setups("long_call_leaps", chain, dte_min=365, dte_max=800,
                                    financing_put=True, put_ratio=ratio)
        return [l for l in found[0].trade.legs if l.action == Action.SELL][0].quantity

    assert sold(1) == 1
    assert sold(2) == 2
    assert sold(5) == 2          # clamped to max_ratio, not obeyed blindly


def test_the_commitment_is_spelled_out_in_dollars():
    """The number that actually decides whether the variant is worth it is the
    cash she must hold, and neither the debit nor the buying-power check shows
    it next to the credit that looks like a discount."""
    check = _check(validator.validate_trade(_reversal()),
                   "What the financing put commits you to")
    assert check.status == CheckStatus.INFO
    assert "$15,000" in check.message          # collateral, not netted down
    assert "$138.00" in check.message          # lower break-even: 150 - 12


def test_the_delta_flag_explains_the_shape_instead_of_blaming_it():
    """A bought call plus a sold put IS a ~100 delta position - that is the
    definition of the structure. The generic wording told her to reduce size on
    a trade whose whole design is to move like 100 shares, which reads as a
    fault in the setup rather than the thing she chose."""
    check = _check(validator.validate_trade(_reversal()), "Position delta")
    assert check.status == CheckStatus.WARN            # 0.71 + 0.25 = ~96 delta
    assert "owning 100 shares" in check.message
    assert "reduce size" not in check.message
    # ...and a plain long call is still told the plain thing.
    plain = _check(validator.validate_trade(_trade(delta=0.95, strike=120.0, premium=82.0)),
                   "Position delta")
    assert "reduce size" in plain.message


def test_the_payoff_knows_about_the_downside(strategy):
    """Alone, the payoff floor is flat at the premium. With the put sold it
    keeps falling - if that never got drawn, the chart would be reassuring
    about the exact risk she took on."""
    alone = payoff.profile(_trade(), strategy)
    reversal = payoff.profile(_reversal(), strategy)
    assert alone.loss_grows_below is False
    assert reversal.loss_grows_below is True
    assert reversal.max_loss < alone.max_loss


def test_the_ticket_is_one_combo_order(strategy):
    """Her SOP says both legs go in as ONE order - legging in is how she ends up
    filled on only the risky half."""
    line = tos_ticket.ticket_line(_reversal(), today=TODAY)
    assert line.startswith("BUY +1 COMBO AMZN")
    assert "CALL/-150 PUT" in line
    assert "@27.00 LMT" in line                # the net debit, per share (39 - 12)


def test_the_scanner_only_sells_a_put_when_asked():
    from src.data.chain import OptionChain, OptionContract

    def contract(otype, strike, delta, mid):
        return OptionContract(option_type=otype, strike=strike, expiration="2027-09-17",
                              dte=400, bid=mid - 0.5, ask=mid + 0.5, delta=delta,
                              iv=0.30, open_interest=500)

    chain = OptionChain(underlying="AMZN", underlying_price=200.0, contracts=[
        contract(OptionType.CALL, 185, 0.74, 39.0),
        contract(OptionType.PUT, 150, -0.25, 12.0),
        contract(OptionType.PUT, 185, -0.45, 26.0),
    ])

    off = scanner.scan_setups("long_call_leaps", chain, dte_min=365, dte_max=800)
    on = scanner.scan_setups("long_call_leaps", chain, dte_min=365, dte_max=800,
                             financing_put=True)
    assert [len(c.trade.legs) for c in off] == [1]
    assert [len(c.trade.legs) for c in on] == [2]
    # In the band, nearest 0.25 - never the richer 0.45 sitting right there.
    sold = [l for l in on[0].trade.legs if l.action == Action.SELL]
    assert sold[0].strike == 150


def test_no_put_in_the_band_leaves_the_call_on_its_own():
    """Skipping the setup entirely would hide a perfectly good default trade,
    and substituting a wrong-delta put would quietly build a bigger position
    than she asked for. Neither - the call goes out alone."""
    from src.data.chain import OptionChain, OptionContract

    def contract(otype, strike, delta, mid):
        return OptionContract(option_type=otype, strike=strike, expiration="2027-09-17",
                              dte=400, bid=mid - 0.5, ask=mid + 0.5, delta=delta,
                              iv=0.30, open_interest=500)

    chain = OptionChain(underlying="AMZN", underlying_price=200.0, contracts=[
        contract(OptionType.CALL, 185, 0.74, 39.0),
        contract(OptionType.PUT, 185, -0.45, 26.0),      # only a too-deep put
    ])
    found = scanner.scan_setups("long_call_leaps", chain, dte_min=365, dte_max=800,
                                financing_put=True)
    assert [len(c.trade.legs) for c in found] == [1]


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
