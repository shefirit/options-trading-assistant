"""The number the roll form fills in for her, and the two ways it gets there.

"What did the new spread sell for by itself?" is the one figure a one-order
roll never prints, so the app answers it for her. It used to answer with
today's chain price divided by her contract count - right on one contract,
a third of the truth on three - and then refuse the roll because the number it
had invented said the buy-back paid her money.

Every figure here is INVENTED; this repo is public.
"""

import datetime as dt

from src.data.chain import OptionChain, OptionContract
from src.engine import roll_math
from src.engine.models import OptionType
from ui.trades import actions

EXP = dt.date.today() + dt.timedelta(days=50)


class _Chain:
    """A provider that knows exactly two strikes - the legs of one spread."""

    def __init__(self, prices: dict[float, tuple[float, float]]):
        self.prices = prices

    def get_chain(self, underlying, dte_min=0, dte_max=0):
        return OptionChain(
            underlying=underlying, underlying_price=300.0,
            contracts=[
                OptionContract(option_type=OptionType.CALL, strike=strike,
                               expiration=EXP.isoformat(), dte=50,
                               bid=bid, ask=ask)
                for strike, (bid, ask) in self.prices.items()])


# ------------------------------------------------------- what a leg is worth
def test_a_leg_is_priced_for_every_contract_she_holds():
    """The prefill feeds a box that wants the position's TOTAL. Priced for one
    contract, a three-contract roll arrived at a third of its real size."""
    provider = _Chain({375.0: (1.90, 2.10)})     # mid 2.00 a share

    assert actions._live_leg_mid(provider, "GOOG", 375.0, EXP,
                                 OptionType.CALL, 1) == 200.0
    assert actions._live_leg_mid(provider, "GOOG", 375.0, EXP,
                                 OptionType.CALL, 3) == 600.0


def test_one_contract_is_unchanged():
    """The old behaviour on the size most of her trades are - a guard against
    fixing this by scaling it twice."""
    provider = _Chain({375.0: (1.90, 2.10)})
    assert actions._live_leg_mid(provider, "GOOG", 375.0, EXP,
                                 OptionType.CALL) == 200.0


def test_a_spread_is_the_difference_between_its_legs_at_that_size():
    """2.00 sold less 0.77 bought is 1.23 a share, which is $369 on three."""
    provider = _Chain({375.0: (1.90, 2.10), 385.0: (0.72, 0.82)})
    sold = actions._live_leg_mid(provider, "GOOG", 375.0, EXP, OptionType.CALL, 3)
    bought = actions._live_leg_mid(provider, "GOOG", 385.0, EXP, OptionType.CALL, 3)
    assert round(sold - bought, 2) == 369.0


# ------------------------------------------------- which answer the form uses
def test_todays_chain_is_used_when_it_agrees_with_her_fill():
    pre = roll_math.new_credit_prefill(chain_price=369.0, cost_to_close=84.0,
                                       net_credit=249.0)
    assert pre.from_chain and pre.total == 369.0


def test_a_chain_price_below_the_net_credit_is_worked_forward_instead():
    """The bug she hit: a chain price under the roll's own credit says buying
    back the old spread PAID her, which cannot happen. Her fill can settle it -
    what getting out costs today, plus the cash the order put in."""
    pre = roll_math.new_credit_prefill(chain_price=123.0, cost_to_close=84.0,
                                       net_credit=249.0)
    assert not pre.from_chain
    assert pre.total == 333.0
    # And that number is one the form will accept, which the other was not.
    assert roll_math.from_net(261.0, 249.0, pre.total).paid_to_close == 84.0


def test_a_leg_the_chain_cannot_price_is_worked_forward_too():
    pre = roll_math.new_credit_prefill(chain_price=None, cost_to_close=84.0,
                                       net_credit=249.0)
    assert not pre.from_chain and pre.total == 333.0


def test_before_she_types_a_fill_price_the_chain_stands_alone():
    """Nothing to work forward from until the credit box has something in it."""
    pre = roll_math.new_credit_prefill(chain_price=123.0, cost_to_close=84.0,
                                       net_credit=0.0)
    assert pre.from_chain and pre.total == 123.0


def test_nothing_is_invented_when_there_is_nothing_to_work_from():
    """No buy-back price means no second opinion. The impossible figure is
    handed back so the form says so out loud instead of guessing."""
    pre = roll_math.new_credit_prefill(chain_price=123.0, cost_to_close=None,
                                       net_credit=249.0)
    assert pre.from_chain and pre.total == 123.0
    assert roll_math.from_net(261.0, 249.0, pre.total).impossible

    assert roll_math.new_credit_prefill(None, None, 249.0).total is None


def test_a_debit_roll_never_works_forward_to_a_free_leg():
    """Paying more to get out than the new leg sold for is an ordinary debit
    roll; a worked-forward zero or less is not a price, so leave the chain."""
    pre = roll_math.new_credit_prefill(chain_price=40.0, cost_to_close=50.0,
                                       net_credit=-60.0)
    assert pre.from_chain and pre.total == 40.0


# ------------------------------------------------------- and on the real form
def _priced_app(monkeypatch, app_with_rows, rows, new_leg_mid: float):
    """The app over one position, with a chain that prices both expirations.

    The near one is what buying back costs today; the far one is what the roll
    form offers for the leg she sold in its place.
    """
    from src.data import cache
    from src.data.provider import DataProvider
    from src.engine.models import OptionType as OT

    near, far = dt.date.today() + dt.timedelta(days=30), \
        dt.date.today() + dt.timedelta(days=60)
    chain = OptionChain(
        underlying="SOFI", underlying_price=108.0,
        contracts=[
            # 0.28 a share to buy back: $84 on her three contracts.
            OptionContract(option_type=OT.PUT, strike=100.0,
                           expiration=near.isoformat(), dte=30,
                           bid=0.26, ask=0.30, delta=-0.28),
            OptionContract(option_type=OT.PUT, strike=100.0,
                           expiration=far.isoformat(), dte=60,
                           bid=new_leg_mid - 0.02, ask=new_leg_mid + 0.02,
                           delta=-0.30),
        ])
    cache.clear()   # the position chain is cached per symbol and expiration
    monkeypatch.setattr(DataProvider, "_expiration_chain",
                        lambda self, sym, dte: chain)
    monkeypatch.setattr(DataProvider, "get_chain",
                        lambda self, sym, **kw: chain)
    return app_with_rows(rows).run()


def _new_leg_box(at):
    return next(n for n in at.number_input
                if "sold for by itself" in (n.label or ""))


def test_the_prefill_is_the_whole_positions_money_not_one_contracts(
        monkeypatch, app_with_rows, open_csp_row):
    """Three contracts at 1.23 a share is $369. The box asks for the price, so
    1.23 is what belongs in it - it used to arrive as 0.41, a third of the
    truth, and the form then argued with a fill that was perfectly good."""
    at = _priced_app(monkeypatch, app_with_rows,
                     [open_csp_row(credit=261.0, contracts=3)],
                     new_leg_mid=1.23)
    assert not at.exception

    assert _new_leg_box(at).value == 1.23
    body = " ".join(str(m.value) for m in at.markdown)
    assert "on 3 contracts" in body


def test_a_chain_price_that_argues_with_her_fill_gives_way_to_it(
        monkeypatch, app_with_rows, open_csp_row):
    """A stale 0.30 on the new put against a roll that paid 0.83 says the
    buy-back paid her. The form used to fill that in and then refuse to record
    the roll over it; now it works forward from the buy-back instead."""
    at = _priced_app(monkeypatch, app_with_rows,
                     [open_csp_row(credit=261.0, contracts=3)],
                     new_leg_mid=0.30)
    at = next(n for n in at.number_input
              if (n.key or "").startswith("roll_cash_")).set_value(0.83).run()

    assert not at.exception
    # $84 to buy back plus the $249 the roll paid = $333, or 1.11 a share.
    assert _new_leg_box(at).value == 1.11
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Worked out from your own fill" in body
    assert "cannot happen" not in " ".join(str(w.value) for w in at.warning)
