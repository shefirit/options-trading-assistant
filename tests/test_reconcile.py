"""Her log against what thinkorswim actually holds.

Built after reconciling her paperMoney book by hand on 2026-09-18 turned up
three disagreements in nine positions: a short SMH call the log had no row for,
a RUT wing logged at half its size, and an AAPL spread that had never been
opened. Her account of the call: "i sold it manually and forgot to log it. or
maybe it was mistake."

The text in these tests is the real shape of her thinkorswim Positions pane,
strikes and all - it is the only way to know the parser works, and it is her
own paperMoney screen, so nothing here is private.

Two failure modes matter more than the features:

  * a FALSE alarm. "This is open in your log and not at your broker" is the
    most alarming thing the screen can say, and it fired on her AVGO LEAPS
    simply because a bought call ties up no buying power and 0.00 is falsy.
  * a SILENT miss. A line that cannot be read has to come back and be shown,
    because a check that quietly skips half the account is worse than none.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engine import reconcile
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import parse_rows
from src.logging_tools.row import COLUMNS, build_row

# Straight off her paperMoney screen.
PAPER = """XSP — 762.03 -1.75 -0.23%\t—\t12\t11.88\t115.50$\t48.13%\t762.03\t(240.00$)\t10.78\t-0.92\t—\t(3,000.00$)\t—
+3 Sep 30 (12d) 725 P\t+3\t12\t-17.84\t(498.00$)\t-72.17%\t0.64\t690.00$\t-34.36\t1.40\t93.76%\t—\t2.30
-3 Sep 30 (12d) 735 P\t-3\t12\t29.72\t613.50$\t65.97%\t1.055\t(930.00$)\t45.14\t-2.33\t89.64%\t—\t3.10
SMH — 563.55 2.94 +0.52%\tITM\t—\t28\t45.49\t(4,397.50$)\t-24.76%\t563.55\t17,760.00$\t33.16\t-0.92\t—\t(6,563.50$)\t—
-2 Oct 16 (28d) 615 C\t-2\t28\t-34.91\t55.00$\t5.91%\t4.375\t(930.00$)\t46.45\t-1.06\t84.65%\t—\t4.65
+1 Jun 17 (272d) 460 C\t+1\t272\t80.41\t(4,452.50$)\t-23.82%\t142.375\t18,690.00$\t-13.29\t0.14\t30.11%\t—\t186.90
Cash\t—\t—\t—\t—\t—\t—\t108,738.46$\t—\t—\t—\t—\t—
Totals:\t—\t12\t39.98\t(2,897.50$)\t-2.40%\t—\t120,792.46$\t270.50\t-2.96\t—\t(37,563.50$)\t—"""

# Her real account, positions collapsed - headers only, no legs.
AVGO_LINE = ("AVGO — 354.615 7.315 +2.11%\tITM\t—\t14\t61.50\t(341.00$)\t-3.17%"
             "\t354.615\t10,755.00$\t10.26\t-0.68\t—\t0.00$\t—")
FCX_LINE = ("FCX — 70.955 0.105 +0.15%\t—\t28\t26.44\t6.34$\t3.66%\t70.955"
            "\t(173.34$)\t4.06\t-3.07\t—\t(1,000.00$)\t—")


def _spread(sym, side, short, long_, contracts=2, account="real",
            trade_id="T1", dte=28):
    kind = OptionType.CALL if side == "call" else OptionType.PUT
    exp = date.today() + timedelta(days=dte)
    legs = [
        Leg(role=f"short_{side}", action=Action.SELL, option_type=kind,
            strike=short, premium=1.5, delta=0.2, dte=dte, expiration=exp),
        Leg(role=f"long_{side}", action=Action.BUY, option_type=kind,
            strike=long_, premium=0.6, dte=dte, expiration=exp),
    ]
    t = Trade(strategy_key=f"{side}_credit_spread", underlying=sym, legs=legs,
              contracts=contracts, underlying_price=100.0)
    risk = abs(short - long_) * 100 * contracts
    return build_row(t, "Put Credit Spread", {"credit": 176.0, "account": account,
                                              "max_loss": risk, "buying_power": risk},
                     True, "", trade_id=trade_id)


# ------------------------------------------------------------- the parser
def test_her_real_screen_parses_whole():
    pos, unreadable = reconcile.parse_tos(PAPER)
    assert unreadable == []
    assert [p.symbol for p in pos] == ["XSP", "SMH"]
    assert pos[0].bp_effect == 3000.0
    assert pos[1].bp_effect == 6563.50


def test_the_legs_come_out_with_their_sides_and_sizes():
    pos, _ = reconcile.parse_tos(PAPER)
    xsp = pos[0]
    assert [(l.action, l.option_type, l.strike, l.quantity) for l in xsp.legs] == [
        (Action.BUY, OptionType.PUT, 725.0, 3),
        (Action.SELL, OptionType.PUT, 735.0, 3),
    ]


def test_a_leaps_leg_keeps_its_own_far_dte():
    pos, _ = reconcile.parse_tos(PAPER)
    smh = pos[1]
    assert {l.strike: l.dte for l in smh.legs} == {615.0: 28, 460.0: 272}


def test_the_cash_and_totals_rows_are_not_positions():
    """Both paste with the rest. Reading Totals as a holding would invent a
    position worth the whole account."""
    pos, unreadable = reconcile.parse_tos(PAPER)
    assert not any(p.symbol.upper() in ("CASH", "TOTALS") for p in pos)
    assert not unreadable


def test_a_zero_buying_power_position_is_still_a_position():
    """THE false alarm. A bought call ties up nothing, so her AVGO LEAPS reads
    0.00 - and a falsy test dropped it, after which the comparison reported it
    as open in her log and missing at her broker."""
    pos, _ = reconcile.parse_tos(AVGO_LINE)
    assert [p.symbol for p in pos] == ["AVGO"]
    assert pos[0].bp_effect == 0.0


def test_a_line_it_cannot_read_comes_back_rather_than_vanishing():
    pos, unreadable = reconcile.parse_tos(FCX_LINE + "\nsomething else entirely")
    assert [p.symbol for p in pos] == ["FCX"]
    assert unreadable == ["something else entirely"]


def test_nothing_useful_in_the_paste_is_not_a_crash():
    pos, unreadable = reconcile.parse_tos("hello\n\n   \n")
    assert pos == [] and unreadable == ["hello"]


# ---------------------------------------------------------- the comparison
def test_a_book_that_agrees_reports_nothing():
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 60, contracts=2)])
    broker, _ = reconcile.parse_tos(FCX_LINE)
    assert reconcile.compare(app, broker) == []


def test_a_position_at_the_broker_and_not_in_the_log_is_the_first_finding():
    """The dangerous direction, and the one that found her SMH call."""
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 60)])
    broker, _ = reconcile.parse_tos(FCX_LINE + "\n" + AVGO_LINE)
    out = reconcile.compare(app, broker)
    assert out[0].kind == "missing_in_log" and out[0].symbol == "AVGO"


def test_a_position_in_the_log_and_not_at_the_broker_is_flagged():
    app = parse_rows(COLUMNS, [
        _spread("FCX", "put", 65, 60, trade_id="T1"),
        _spread("NDX", "call", 30625, 30675, trade_id="T2")])
    broker, _ = reconcile.parse_tos(FCX_LINE)
    out = [f for f in reconcile.compare(app, broker) if f.kind == "missing_at_broker"]
    assert len(out) == 1 and out[0].symbol == "NDX" and out[0].trade_id == "T2"


def test_a_size_difference_is_caught():
    """Her SMH: two short calls at the broker, one in the log."""
    smh = build_row(
        Trade(strategy_key="poor_mans_covered_call", underlying="SMH", contracts=1,
              legs=[Leg(role="long_call_leaps", action=Action.BUY,
                        option_type=OptionType.CALL, strike=460.0, dte=272),
                    Leg(role="short_call", action=Action.SELL,
                        option_type=OptionType.CALL, strike=615.0, dte=28)]),
        "PMCC", {"credit": 460.0, "account": "real"}, True, "", trade_id="S1")
    app = parse_rows(COLUMNS, [smh])
    broker, _ = reconcile.parse_tos(PAPER)
    sizes = [f for f in reconcile.compare(app, broker) if f.kind == "size"]
    assert len(sizes) == 1
    assert "615" in sizes[0].headline and "your log says 1" in sizes[0].headline


def test_contracts_multiply_the_leg_size_before_comparing():
    """The log stores leg quantity PER UNIT of the position; the broker prints
    the total. A 3-contract spread whose legs say qty 1 is 3 at the broker, and
    comparing the raw numbers would flag every multi-contract trade she owns."""
    app = parse_rows(COLUMNS, [_spread("XSP", "put", 735, 725, contracts=3)])
    broker, _ = reconcile.parse_tos(PAPER)
    xsp = [f for f in reconcile.compare(app, broker)
           if f.symbol == "XSP" and f.kind in ("size", "legs")]
    assert xsp == []


def test_a_buying_power_difference_is_information_not_an_alarm():
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 55, contracts=2)])
    broker, _ = reconcile.parse_tos(FCX_LINE)     # broker says 1,000
    bp = [f for f in reconcile.compare(app, broker) if f.kind == "bp"]
    assert len(bp) == 1 and bp[0].severity == "info"


def test_small_buying_power_differences_are_ignored():
    """Her CRWD condor reads 3,005 at the broker against a 3,000 width. Chasing
    five dollars would bury the findings that matter."""
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 60, contracts=2)])
    broker, _ = reconcile.parse_tos(
        FCX_LINE.replace("(1,000.00$)", "(1,005.00$)"))
    assert [f for f in reconcile.compare(app, broker) if f.kind == "bp"] == []


def test_findings_lead_with_what_she_is_actually_holding():
    """Buying power last: a number being off is worth knowing, a contract she
    does not know she holds is worth knowing FIRST."""
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 55, contracts=2)])
    broker, _ = reconcile.parse_tos(FCX_LINE + "\n" + AVGO_LINE)
    kinds = [f.kind for f in reconcile.compare(app, broker)]
    assert kinds.index("missing_in_log") < kinds.index("bp")


def test_the_summary_totals_both_sides():
    app = parse_rows(COLUMNS, [_spread("FCX", "put", 65, 60, contracts=2)])
    broker, _ = reconcile.parse_tos(FCX_LINE)
    s = reconcile.summary(app, broker, [])
    assert (s["app_count"], s["broker_count"]) == (1, 1)
    assert s["broker_bp"] == 1000.0 and s["clean"] is True


# ------------------------------------------------- the panel, end to end
FCX_ROW_TEXT = FCX_LINE


def test_the_panel_reports_a_clean_book(app_with_rows):
    """Driven through the real screen, not the functions underneath it: the
    value of this feature is entirely in being trusted, and a parser that works
    behind a form that does not is worth nothing."""
    at = app_with_rows([_spread("FCX", "put", 65, 60, contracts=2,
                                account="real", trade_id="T1")]).run()
    assert not at.exception
    at.text_area(key="recon_text").set_value(FCX_ROW_TEXT).run()
    at = at.button(key="recon_go").click().run()
    assert not at.exception
    page = " ".join(str(e.value) for e in at.success)
    assert "Everything matches" in page


def test_the_panel_names_what_is_missing(app_with_rows):
    at = app_with_rows([_spread("FCX", "put", 65, 60, contracts=2,
                                account="real", trade_id="T1")]).run()
    at.text_area(key="recon_text").set_value(FCX_ROW_TEXT + "\n" + AVGO_LINE).run()
    at = at.button(key="recon_go").click().run()
    assert not at.exception
    body = at.get("markdown")
    text = " ".join(str(e.value) for e in body)
    assert "AVGO" in text and "not in your log" in text


def test_the_panel_refuses_an_empty_paste_without_blowing_up(app_with_rows):
    at = app_with_rows([_spread("FCX", "put", 65, 60, account="real")]).run()
    at = at.button(key="recon_go").click().run()
    assert not at.exception
    assert any("Paste your positions" in str(w.value) for w in at.warning)


def test_a_paste_with_no_positions_says_so(app_with_rows):
    at = app_with_rows([_spread("FCX", "put", 65, 60, account="real")]).run()
    at.text_area(key="recon_text").set_value("just some words").run()
    at = at.button(key="recon_go").click().run()
    assert not at.exception
    assert any("looked like a position" in str(e.value) for e in at.error)
