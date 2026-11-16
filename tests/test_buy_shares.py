"""Buy shares - a trade that is only shares, no option.

Rita, 2026-11-16: "in my trades i want to add a option just to buy shares".
It is logged with ➕ Log a trade, tracked at today's share price, and closed
when she sells them. If she later sells a call against them it stays the same
trade, run the way a Buy / Write is.

Invented numbers throughout (this repo is public).
"""

from datetime import date, timedelta

from src.engine import exit_rules, month_report, positions, quick_log, wheel
from src.engine.config_loader import get_strategy
from src.engine.models import Trade
from src.engine.positions import Position, RollEvent
from src.logging_tools import twa_tracker
from src.logging_tools.row import COLUMNS, build_roll_row, build_row

KEY = "shares"
STRAT = get_strategy(KEY)
NAME = STRAT["name"]
TRADE_ID = "20261116-100000-XYZ"


def _shares_row(price: float = 50.0, contracts: int = 1):
    """The open row Quick Log writes for a Buy shares trade."""
    trade = Trade(strategy_key=KEY, underlying="XYZ", contracts=contracts,
                  legs=[], underlying_price=price)
    sizing = quick_log.sizing_from_fill(trade, STRAT, 0.0, share_price=price)
    return build_row(trade, NAME, sizing, True, "", trade_id=TRADE_ID,
                     opened_on=date.today() - timedelta(days=3))


def _call(strike: float, credit: float, days: int = 30):
    return build_roll_row(TRADE_ID, "XYZ", NAME, credit, new_strike=strike,
                          new_expiration=date.today() + timedelta(days=days),
                          new_credit=credit, rolled_on=date.today())


def _parse(*rows):
    return positions.parse_rows(COLUMNS, list(rows))[0]


# ------------------------------------------------------------------ config
def test_it_is_called_buy_shares_and_has_no_option():
    assert NAME == "Buy shares"
    assert STRAT["legs"] == []
    assert STRAT["log_only"] is True
    # No rules to break: no target, no stop, no time exit.
    for rule in ("profit_target_pct", "stop_loss_multiple", "time_exit_dte"):
        assert rule not in STRAT["exit"]


def test_it_is_not_offered_as_something_to_scan_for():
    from src.engine.scanner import SETUP_FAMILIES
    assert STRAT["family"] not in SETUP_FAMILIES


# ------------------------------------------------------------------ the money
def test_the_shares_cost_what_she_paid():
    trade = Trade(strategy_key=KEY, underlying="XYZ", contracts=2, legs=[])
    s = quick_log.sizing_from_fill(trade, STRAT, 0.0, share_price=50.0)
    assert s["shares_cost"] == 10_000.0
    assert s["open_cash"] == -10_000.0
    assert s["credit"] == 0.0
    assert s["max_loss"] == 10_000.0


def test_they_are_valued_at_todays_price():
    p = _parse(_shares_row())
    v = positions.shares_only_value(p, 55.0)
    assert v["value"] == 5_500.0
    assert v["open_pl"] == 500.0
    assert v["shares_pl"] == 500.0


def test_they_get_the_bought_share_numbers():
    state = wheel.state_from(_parse(_shares_row()), 51.0)
    assert state is not None and state.bought
    assert state.paid_per_share == 50.0
    assert state.unrealised == 100.0


# ------------------------------------------------------------------ the card
def test_no_call_is_not_a_problem_on_plain_shares():
    p = _parse(_shares_row())
    assert p.legs == [] and p.expiration is None
    assert p.can_write_call
    sig = exit_rules.evaluate(p, STRAT["exit"], current_cost=None,
                              underlying_price=51.0)
    assert sig.action == "hold" and sig.tone == "neutral"
    assert "nothing is earning" not in sig.headline.lower()


def test_a_big_drop_warns_and_never_says_sell():
    p = _parse(_shares_row())
    sig = exit_rules.evaluate(p, STRAT["exit"], current_cost=None,
                              underlying_price=44.0)
    assert sig.notes and "nothing here says sell" in sig.notes[0]


def test_a_call_sold_later_joins_the_same_trade():
    p = _parse(_shares_row(), _call(55.0, 120.0))
    assert p.has_short_call and not p.is_uncovered
    state = wheel.state_from(p, 51.0)
    assert state.premium_collected == 120.0
    assert state.cost_basis == 48.8
    assert state.call_strike == 55.0


def test_selling_the_shares_files_as_neutral_not_by_the_rules():
    label, tone, by_rules = month_report._exit_bucket("Sold the shares")
    assert (label, tone, by_rules) == ("Sold the shares", "neutral", False)


# ------------------------------------------------------------------ the tracker
def test_the_tracker_gets_a_buy_stock_row():
    p = Position(trade_id="T1", underlying="abc", account="real", status="open",
                 opened=date(2026, 11, 2), contracts=1, strategy_key=KEY,
                 shares_cost=5000.0, open_cash=-5000.0, legs=[], open_legs=[])
    rows, why = twa_tracker.rows_for(p, date(2026, 10, 1))
    assert why == ""
    (shares,) = [r["cells"] for r in rows]
    assert (shares["Type"], shares["Strategy"]) == ("Buy", "STOCK")
    assert shares["Premium $"] == 50.0


def test_the_tracker_adds_a_call_written_later():
    p = Position(trade_id="T1", underlying="abc", account="real", status="open",
                 opened=date(2026, 11, 2), contracts=1, strategy_key=KEY,
                 shares_cost=5000.0, open_cash=-5000.0, legs=[], open_legs=[],
                 rolls=[RollEvent(rolled_on=date(2026, 11, 4), cash=120.0,
                                  new_strike=55.0,
                                  new_expiration=date(2026, 12, 4),
                                  new_credit=120.0, option_type="call")])
    rows, why = twa_tracker.rows_for(p, date(2026, 10, 1))
    assert why == ""
    kinds = [(r["cells"]["Type"], r["cells"]["Strategy"]) for r in rows]
    assert kinds == [("Buy", "STOCK"), ("Sell", "CC")]


# ------------------------------------------------------------------ the app
def _page(at) -> str:
    return " ".join(str(m.value) for m in [*at.markdown, *at.warning, *at.info])


def _no_snags(at):
    assert not at.exception
    snags = [str(e.value) for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, snags


def test_log_a_trade_asks_only_for_the_shares(app_with_rows):
    at = app_with_rows([]).run()
    at = next(b for b in at.button if b.key == "ql_open").click().run()
    assert NAME in at.selectbox(key="ql_strategy").options
    at = at.selectbox(key="ql_strategy").set_value(KEY).run()
    _no_snags(at)
    keys = [n.key or "" for n in at.number_input]
    assert any(k.startswith("ql_sharepx_") for k in keys)
    # No option on this one: no strike, no credit, no expiration.
    assert not any(k.startswith(("ql_strike_", "ql_credit_")) for k in keys)
    assert not any((d.key or "").startswith("ql_exp_") for d in at.date_input)

    next(n for n in at.number_input
         if (n.key or "").startswith("ql_sharepx_")).set_value(50.0)
    at = next(b for b in at.button if b.label == "Check it").click().run()
    _no_snags(at)
    page = _page(at)
    assert "Buy shares" in page and "shares only" in page
    assert "no call sold yet" not in page
    assert "100 shares" in page and "contract(s)" not in page
    assert "SOP check" not in page
    labels = [m.label for m in at.metric]
    assert "The shares cost you" in labels and "Call credit" not in labels
    assert any("Save to my log" in (b.label or "") for b in at.button)


def test_find_a_trade_does_not_list_it(app_with_rows):
    at = app_with_rows([]).run()
    options = at.selectbox(key="build_strategy").options
    assert "Buy / Write" in options      # the list is the formatted names
    assert NAME not in options


def test_the_card_offers_a_call_and_a_sold_the_shares_close(app_with_rows):
    at = app_with_rows([_shares_row()]).run()
    _no_snags(at)
    assert "Your shares in XYZ" in _page(at)
    assert any((n.key or "").startswith("write_credit_") for n in at.number_input)
    reasons = next(s for s in at.selectbox
                   if (s.key or "").startswith("exit_reason_"))
    assert reasons.options[0] == "Sold the shares"
