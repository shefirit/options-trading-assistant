"""The money maths behind a roll.

All figures here are INVENTED (this repo is public - never put real positions
in source control). They keep the shape that makes a roll confusing, though: a
short call sold for $500, bought back for $700, replaced by one selling for
$900. That order pays $200 into the account while the call it closed finished
$200 down, and both being true at once is what the form has to explain.
"""

from src.engine import roll_math


# ---------------------------------------------------------------- from the net
def test_net_price_gives_back_what_the_buy_back_cost():
    # $200 net with the new call worth $900 leaves $700 as the buy-back.
    figs = roll_math.from_net(old_credit=500.0, net_credit=200.0, new_credit=900.0)
    assert figs.paid_to_close == 700.0
    assert figs.net_credit == 200.0
    assert figs.new_credit == 900.0


def test_the_finished_call_can_lose_while_the_order_pays():
    """The whole reason the panel exists: money into the account, and a loss on
    the call that just closed. Netting them into one number hid the loss."""
    figs = roll_math.from_net(old_credit=500.0, net_credit=200.0, new_credit=900.0)
    assert figs.old_leg_result == -200.0
    assert figs.net_credit > 0
    assert not figs.is_debit


def test_a_roll_that_costs_money_is_a_debit():
    figs = roll_math.from_net(old_credit=500.0, net_credit=-100.0, new_credit=600.0)
    assert figs.is_debit
    assert figs.paid_to_close == 700.0


def test_a_call_bought_back_cheaper_than_it_sold_made_money():
    figs = roll_math.from_net(old_credit=500.0, net_credit=600.0, new_credit=800.0)
    assert figs.paid_to_close == 200.0
    assert figs.old_leg_result == 300.0


# --------------------------------------------------------------- from the legs
def test_two_leg_prices_derive_the_net():
    figs = roll_math.from_legs(old_credit=500.0, paid_to_close=700.0,
                               new_credit=900.0)
    assert figs.net_credit == 200.0
    assert figs.old_leg_result == -200.0


def test_both_ways_round_agree():
    """Whichever two figures she has, the third must come out the same - the
    two paths in the form are one calculation seen from either end."""
    from_net = roll_math.from_net(500.0, 200.0, 900.0)
    from_legs = roll_math.from_legs(500.0, 700.0, 900.0)
    assert from_net == from_legs


def test_legs_that_lose_money_produce_a_debit_net():
    figs = roll_math.from_legs(old_credit=500.0, paid_to_close=700.0,
                               new_credit=580.0)
    assert figs.net_credit == -120.0
    assert figs.is_debit


# ------------------------------------------------------- catching a typed slip
def test_a_buy_back_that_pays_her_is_impossible():
    """If the new call's figure is typed too low, the maths says closing the
    old call PAID her. It never does, so say so instead of logging it."""
    figs = roll_math.from_net(old_credit=500.0, net_credit=200.0, new_credit=100.0)
    assert figs.paid_to_close < 0
    assert "cannot happen" in figs.impossible


def test_sane_figures_raise_no_objection():
    assert roll_math.from_net(500.0, 200.0, 900.0).impossible == ""
    assert roll_math.from_legs(500.0, 700.0, 900.0).impossible == ""


def test_a_free_buy_back_is_allowed():
    """Paying nothing is possible - a call that expired worthless costs $0 to
    be rid of. Only a NEGATIVE cost is the impossible one."""
    figs = roll_math.from_legs(old_credit=500.0, paid_to_close=0.0,
                               new_credit=900.0)
    assert figs.impossible == ""
    assert figs.old_leg_result == 500.0


# ------------------------------------------------------ bought back, none sold
def test_buy_back_only_finishes_the_call_with_nothing_written():
    figs = roll_math.buy_back_only(old_credit=500.0, paid_to_close=700.0)
    assert figs.new_credit == 0.0
    assert figs.net_credit == -700.0
    assert figs.old_leg_result == -200.0


def test_buy_back_only_on_a_winner():
    figs = roll_math.buy_back_only(old_credit=500.0, paid_to_close=200.0)
    assert figs.old_leg_result == 300.0
    assert figs.net_credit == -200.0


# --------------------------------------------------------------------- pennies
def test_money_is_rounded_to_the_cent():
    figs = roll_math.from_net(old_credit=500.0, net_credit=200.567,
                              new_credit=900.333)
    assert figs.net_credit == 200.57
    assert figs.new_credit == 900.33
    assert figs.paid_to_close == 699.77
