"""The roll form has to actually render, and be fillable, for a PMCC.

Everything it shows is built from the position: which call she is short, what
it sold for, what the roll did to her money. The seeded PMCC and the offline
app both come from tests/conftest.py.
"""

from datetime import date, timedelta

from src.engine import positions
from src.logging_tools.row import COLUMNS

# ------------------------------------------------------------ the position itself
def test_the_seeded_row_is_a_trackable_debit_position(open_pmcc_row):
    """Guards the fixture: if this stops parsing as an open PMCC, the render
    test below would pass by simply drawing nothing."""
    parsed = positions.parse_rows(COLUMNS, [open_pmcc_row()])
    assert len(parsed) == 1
    p = parsed[0]
    assert p.is_debit and not p.is_uncovered
    assert p.credit == 500.0


def test_the_short_call_leg_is_the_call_not_the_leaps(open_pmcc_row):
    """A PMCC holds two long-dated calls' worth of confusion: the form must
    name the call she sold, never the LEAPS she bought."""
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_pmcc_row()])[0]
    leg = actions._short_call_leg(p)
    assert leg is not None
    assert leg.strike == 500.0
    assert actions._leg_label(leg.strike, p.expiration, "call").startswith("the 500 call")


def test_the_short_call_leg_is_none_when_nothing_is_written(open_pmcc_row):
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_pmcc_row()])[0]
    p.legs = [l for l in p.legs if l.role != "short_call"]
    assert actions._short_call_leg(p) is None


def test_signed_money_shows_its_sign():
    from ui.trades import widgets

    assert widgets._signed(200.0) == "+$200"
    assert widgets._signed(-200.0) == "-$200"
    assert widgets._signed(0.0) == "+$0"


# ------------------------------------------------------------------ the rendering
def _fill_the_net_way(at, net_price: float, sold_price: float):
    """Type a roll the way a one-order fill reads.

    Prices go in the way thinkorswim prints them (per share); the form does the
    x100. On one contract 2.00 means $200.
    """
    # Selected by KEY, not by label. Quick Log carries a box labelled "Credit
    # price on your fill" too, and once it moved to the top of the tab a
    # label match found ITS box instead of the roll form's.
    def box(prefix):
        return next(n for n in at.number_input if (n.key or "").startswith(prefix))

    box("roll_strike_").set_value(560.0).run()
    box("roll_cash_").set_value(net_price).run()
    return next(n for n in at.number_input
                if "sold for by itself" in (n.label or "")).set_value(sold_price).run()


def test_my_trades_renders_the_roll_form_for_an_open_pmcc(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    assert not at.exception
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"
    labels = [x.label for x in at.expander]
    assert any("Roll it (or buy the short leg back)" in l for l in labels), \
        f"the roll form is missing - expanders were {labels}"


def test_the_defaults_are_usable_the_moment_the_form_opens(app_with_one_pmcc):
    """The first version opened with strike 0.00 and an expiration a month from
    TODAY - which, on a call expiring further out than that, its own validation
    then rejected. A roll moves out from the call she holds, not from today."""
    at = app_with_one_pmcc.run()
    assert not at.exception
    current_expiry = date.today() + timedelta(days=32)

    strike = next(n for n in at.number_input if n.label == "Strike")
    assert strike.value == 500.0, "strike should start at the one she holds"
    expires = next(d for d in at.date_input if d.label == "Expires")
    assert expires.value > current_expiry, \
        f"{expires.value} is not after the call being rolled ({current_expiry})"


def test_prices_are_typed_the_way_thinkorswim_prints_them(app_with_one_pmcc):
    """She reads @1.50 off a fill; every box used to demand 150. Typing the
    price must record the total, so the x100 is the app's job not hers."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=9.00)
    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "&#36;200" in body or "$200" in body, "2.00 on 1 contract is $200"


def test_a_dollar_total_typed_into_a_price_box_is_flagged(app_with_one_pmcc):
    """Her habit from every other form in the app is to type totals. Here that
    would record 100x the real money, so it has to be caught out loud."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=200.0, sold_price=9.00)
    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "looks like a dollar total" in warnings


def test_typing_a_roll_shows_what_it_did_to_her_money(app_with_one_pmcc):
    """The whole point of the rebuild: a roll that pays $200 while the call it
    closed finished $200 down must say BOTH, not just bank the credit."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=9.00)

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "What this roll does to your money" in body
    assert "700" in body      # the buy-back derived from the net
    assert "200" in body      # both the credit banked and the call's result


def test_the_two_price_way_round_derives_the_net_instead_of_asking(app_with_one_pmcc):
    """When she has both leg prices, the net is worked out rather than typed -
    so what the app banks can never disagree with the prices she read."""
    at = app_with_one_pmcc.run()
    next(n for n in at.number_input if n.label == "Strike").set_value(560.0).run()
    next(n for n in at.number_input
         if "paid to buy back" in n.label).set_value(7.00).run()
    next(n for n in at.number_input
         if "got for the new call" in n.label).set_value(9.00).run()
    at = next(c for c in at.checkbox
              if "Use these two prices" in c.label).set_value(True).run()

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "net of" in body
    assert "200" in body      # 900 - 700, the same fill read the other way


def test_an_impossible_pair_of_figures_is_refused(app_with_one_pmcc):
    """Typing the new call's premium too low implies the buy-back PAID her.
    That cannot happen, and logging it would corrupt the trade's history."""
    at = _fill_the_net_way(app_with_one_pmcc.run(), net_price=2.00, sold_price=1.00)

    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "cannot happen" in warnings


# ===========================================================================
# THE PREFILL THAT TOOK THE TAB DOWN.
#
# My trades crashed outright on her real WFC position - a bought LEAPS call
# part-paid for by three sold puts:
#
#   StreamlitValueBelowMinError: The value -10.16 is less than the min_value 0.0
#
# The roll form prefills "what did it cost to buy the leg back" with the live
# cost to close. On every other shape that figure is the short leg. Here the
# bought call and the sold puts share ONE expiration, so it prices the whole
# position - and since the deep call is worth more than the puts, closing PAYS
# her and the number comes back negative. Streamlit raises on a value under a
# box's minimum, and the whole section went down with it, which is why she
# could not reach any of her trades rather than just that one field.
def test_the_priced_figure_is_not_this_rolls_buy_back_on_a_financed_leaps(
        open_financed_leaps_row):
    """The root cause, named: the near expiration holds a leg she BOUGHT, of a
    different type from the puts she would be rolling."""
    from src.engine.models import OptionType
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_financed_leaps_row()])[0]
    assert actions._priced_legs_beyond_this_roll(p, OptionType.PUT) is True


def test_a_spread_still_prefills_from_its_own_priced_legs(open_put_spread_row):
    """The guard must not switch the prefill off where it was right. A credit
    spread's two legs share an expiration too - and there the priced figure IS
    what one roll order fills at."""
    from src.engine.models import OptionType
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_put_spread_row()])[0]
    assert actions._priced_legs_beyond_this_roll(p, OptionType.PUT) is False


def test_a_pmcc_still_prefills_from_its_short_call(open_pmcc_row):
    from src.engine.models import OptionType
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_pmcc_row()])[0]
    assert actions._priced_legs_beyond_this_roll(p, OptionType.CALL) is False


def test_a_csp_still_prefills_from_its_short_put(open_csp_row):
    from src.engine.models import OptionType
    from ui.trades import actions

    p = positions.parse_rows(COLUMNS, [open_csp_row()])[0]
    assert actions._priced_legs_beyond_this_roll(p, OptionType.PUT) is False


def test_my_trades_survives_a_negative_cost_to_close(
        app_with_one_financed_leaps, monkeypatch):
    """The crash itself, end to end. Priced at her real numbers - $1,016 back
    in her pocket to unwind it - the tab has to draw rather than fall over."""
    from src.data.provider import DataProvider

    monkeypatch.setattr(
        DataProvider, "price_position",
        lambda self, position: {"underlying_price": 89.56,
                                "cost_to_close": -1016.0,
                                "short_delta": 0.32})
    at = app_with_one_financed_leaps.run()
    assert not at.exception
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"


def test_no_money_box_can_be_prefilled_below_its_own_minimum(
        app_with_one_financed_leaps, monkeypatch):
    """Belt and braces under the fix above: whatever a caller hands the price
    box, a prefill it cannot hold becomes an empty box rather than a raise."""
    from src.data.provider import DataProvider

    monkeypatch.setattr(
        DataProvider, "price_position",
        lambda self, position: {"underlying_price": 89.56,
                                "cost_to_close": -1016.0,
                                "short_delta": 0.32})
    at = app_with_one_financed_leaps.run()
    assert not at.exception
    assert all(n.value >= 0 for n in at.number_input
               if (n.key or "").startswith(("roll_paid_", "back_paid_")))
