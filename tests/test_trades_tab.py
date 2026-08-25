"""The My trades page as a whole - the contracts a rebuild must not break.

These render the real app through AppTest, so they catch the faults that live
between correct functions: a number formatted for the wrong renderer, a goal
drawn three times, an all-time view measured against a monthly target.

Two of them are named regression tests for defects this rebuild set out to fix:

  * exactly ONE progress bar on the page. There used to be three, all showing
    the month against the same $3,500.
  * the all-time scope never says "of your $3,500 goal". It used to hand a
    whole account's total to a band that divided by the monthly number.

Every number in the fixtures is invented. This repo is public.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest

from src.logging_tools.row import COLUMNS

PCS = "Put Credit Spread"


def _closed_row(trade_id="20260801-101500-SPX", opened=None, closed=None,
                credit=300.0, realized=150.0, account="real",
                reason="Profit target (50%) hit"):
    """One opened-and-closed spread, as the two rows the log actually holds."""
    opened = opened or (date.today() - timedelta(days=4))
    closed = closed or (date.today() - timedelta(days=1))
    details = {"key": "put_credit_spread", "underlying_price": 6400.0,
               "legs": [{"role": "short_put", "action": "sell", "type": "put",
                         "strike": 6300.0, "delta": 0.25,
                         "premium": credit / 100, "qty": 1, "dte": 45},
                        {"role": "long_put", "action": "buy", "type": "put",
                         "strike": 6250.0, "delta": 0.18, "premium": 1.0,
                         "qty": 1, "dte": 45}],
               "open_cash": credit}
    expiry = (opened + timedelta(days=45)).isoformat()
    open_row = [opened.isoformat(), "SPX", PCS, "6300 / 6250", 0.25, 45, 1,
                credit, 5000.0, 5000.0, "yes", "", trade_id, "open", expiry,
                "", "", json.dumps(details), account]
    close_row = [closed.isoformat(), "SPX", PCS, "6300 / 6250", 0.25, 45, 1,
                 credit, 5000.0, 5000.0, "yes", "", trade_id, "close", expiry,
                 credit - realized, realized,
                 json.dumps({"close_cash": -(credit - realized)}), account]
    open_row[11] = reason
    close_row[11] = reason
    return [open_row, close_row]


def _both_books():
    """A real close and a practice one, so the two-book behaviour has something
    to be right or wrong about."""
    rows = _closed_row("20260801-101500-SPX", realized=600.0, account="real")
    rows += _closed_row("20260615-101500-SPY",
                        opened=date(2026, 6, 15), closed=date(2026, 6, 28),
                        realized=400.0, account="paper")
    return rows


def _markdown(at) -> list[str]:
    return [m.value for m in at.markdown]


def _page(at) -> str:
    return "\n".join(_markdown(at))


# ------------------------------------------------- the three-progress-bar fix
def test_the_monthly_goal_is_never_drawn_as_a_progress_bar(app_with_rows):
    """THE regression test. This page used to carry three progress bars of the
    same number - one in the headline strip, one in the report's goals panel,
    one in the pace note. Three answers to one question reads as an app that
    has not decided what it thinks.

    The fixture has no OPEN trades on purpose, so any bar on the page would
    have to be a goal bar. The goal is now drawn once, as the bullet chart,
    which is not a progress element at all.

    What was removed is three drawings of the MONTHLY GOAL, not the idea of a
    progress bar. One trade's progress toward its own 50% profit target is a
    different number about a different thing and it stays, inside that trade's
    "Show the numbers". It cannot be asserted here because the seeded PMCC has
    no live price offline, so the bar has nothing to draw.
    """
    at = app_with_rows(_closed_row()).run()
    assert len(at.get("progress")) == 0


def test_the_goal_appears_once_as_a_chart_not_as_a_row_of_bars(app_with_rows):
    at = app_with_rows(_closed_row()).run()
    # The bullet chart's own caption, which only the one goal visual prints.
    # theme.note() turns **bold** into <b> tags, so match around them.
    assert _page(at).count("is the goal itself") == 1


# ------------------------------------------------ the broken all-time view
def test_all_time_is_never_measured_against_a_one_month_goal(app_with_rows):
    """THE other regression test. The all-time view used to build a whole
    account's report and hand it to a band computing banked / $3,500, so a
    six-day-old account read as a percentage of a target it had never been
    measured against."""
    at = app_with_rows(_closed_row(realized=600.0)).run()
    at = at.selectbox(key="trades_month_pick").set_value("All time").run()
    page = _page(at)
    # No hardcoded day count - this asserted "in 6 days" and broke the moment
    # the date rolled over. The regression is the SHAPE of the sentence: a span
    # target measured over the days elapsed, not a month's goal.
    assert re.search(r"a steady\s+plan would have produced in \d+ day", page)
    assert "Since you started you have banked" in page
    # The dashboard's own band says it once, about THIS MONTH, which is right.
    # A second one would be the all-time total wearing a monthly target.
    assert page.count("of your &#36;3,500 goal") == 1


def test_the_month_in_progress_does_not_print_its_band_twice(app_with_rows):
    """The dashboard opens with this month's banked against this month's goal.
    The report a screen below used to open with exactly the same band."""
    at = app_with_rows(_closed_row()).run()
    # The band's label is uppercased by CSS, not by the markup, so match
    # case-insensitively or the two spellings look like two different labels.
    page = _page(at).lower()
    assert page.count("banked this month") == 1
    assert "the breakdown behind them" in page


# ------------------------------------------------------------ the KPI row
def test_the_six_headline_cards_are_all_there(app_with_rows):
    at = app_with_rows(_closed_row()).run()
    page = _page(at)
    for label in ("This month", "Win rate", "Profit factor", "Average trade",
                  "Worst dip", "By the rules"):
        assert label in page, f"missing KPI card: {label}"


def test_the_cards_are_visible_without_opening_anything(app_with_rows):
    """A power dashboard, not a gated wizard. Nothing in the top three rows is
    behind a click."""
    at = app_with_rows(_closed_row()).run()
    labels = [e.label for e in at.expander]
    assert not any("Profit factor" in (l or "") for l in labels)


def test_a_thin_record_says_so_instead_of_stating_a_ratio_as_fact(app_with_rows):
    at = app_with_rows(_closed_row()).run()
    assert "still mostly noise" in _page(at)


# --------------------------------------------------------- the LaTeX trap
def test_no_dollar_entity_leaks_onto_the_page_as_text(app_with_rows):
    """The bug this test was written for: theme.kpi_card escapes its input, so
    an &#36; handed to it has its ampersand escaped too and comes out as the
    literal text "&#36;0" on the card.

    A bare &#36; in a raw-HTML block is correct and everywhere - it is the
    double-escaped &amp;#36; that is always a mistake.
    """
    at = app_with_rows(_closed_row()).run()
    assert "&amp;#36;" not in _page(at)


def test_no_raw_dollar_pair_survives_into_markdown(app_with_rows):
    """A pair of raw dollar signs in one markdown block turns the text between
    them into LaTeX and garbles the line."""
    at = app_with_rows(_closed_row()).run()
    for block in _markdown(at):
        if "<" in block:          # raw HTML blocks are not parsed as markdown
            continue
        assert block.count("$") < 2, f"LaTeX risk in: {block[:120]}"


def test_no_iso_dates_reach_the_page(app_with_rows):
    """She reads dates day-first. An ISO date on screen is always a formatting
    step that was skipped."""
    at = app_with_rows(_closed_row()).run()
    for block in _markdown(at):
        assert not re.search(r"\b20\d\d-\d\d-\d\d\b", block), block[:120]


# -------------------------------------------------------- the two books
def test_the_practice_legend_appears_when_the_log_holds_both_books(app_with_rows):
    at = app_with_rows(_both_books()).run()
    assert "never added into any total" in _page(at)


def test_the_year_one_track_is_on_the_real_book(app_with_rows):
    at = app_with_rows(_closed_row()).run()
    page = _page(at)
    assert "142,000" in page and "100,000" in page
    assert "year one" in page.lower()


def test_switching_to_practice_hides_the_year_one_track(app_with_rows):
    """An account-balance goal is about money that exists. Showing $142,000
    against PaperMoney is the one confusion this app must never create."""
    at = app_with_rows(_both_books()).run()
    at.radio(key="trades_account").set_value(
        [o for o in at.radio(key="trades_account").options
         if o.startswith("📝")][0]).run()
    page = _page(at)
    assert "hidden on the practice book" in page
    assert "Year one ends at" not in page


def test_a_practice_close_never_lands_in_a_real_total(app_with_rows):
    """The real book banked 600 and the practice book 400. 1,000 must appear
    nowhere on the real view."""
    at = app_with_rows(_both_books()).run()
    assert "$1,000" not in _page(at)


# ---------------------------------------------------------- the trade card
def test_the_open_trades_are_a_table_not_a_stack_of_cards():
    """Rita: "I want all trades organised nicely in table, not one by one
    analysis." A card each meant scrolling past four trades to compare two,
    and comparing is what a table is for."""
    import ast
    from pathlib import Path

    src = (Path(__file__).parent.parent / "ui" / "trades"
           / "open_trades.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "dataframe"]
    assert calls, "the open trades must render as a table"
    kw = {k.arg: k for c in calls for k in c.keywords}
    # Radio-style: exactly one row is always selected, so the detail panel
    # below is never empty and the urgent trade is the one already open.
    assert kw["selection_mode"].value.value == "single-row-required"
    assert kw["on_select"].value.value == "rerun"


def test_the_table_answers_is_this_winning_without_a_click(app_with_one_pmcc):
    """Whether a trade is winning has to be readable off the list itself -
    that was the point of moving the numbers out of a per-card strip."""
    at = app_with_one_pmcc.run()
    # Every tab body renders, so the page carries several tables. Find ours by
    # its columns rather than by position.
    tables = [d.value for d in at.dataframe if "What to do" in list(d.value.columns)]
    assert tables, "expected the open trades table"
    cols = list(tables[0].columns)
    for col in ("What to do", "Symbol", "Days left", "Decide by", "% kept", "P&L $"):
        assert col in cols, f"missing from the table: {col}"


def test_the_full_read_out_is_still_one_click_away(app_with_one_pmcc):
    """Four numbers on the card, seven behind it. Detail belongs behind a
    click; deciding does not."""
    at = app_with_one_pmcc.run()
    assert any("Show the numbers" in e.label for e in at.expander)


def test_every_expander_holding_a_form_is_keyed():
    """Recording anything on a card used to snap every expander shut and lose
    her place mid-form. Streamlit 1.58 keyed expanders track their own state,
    which is what replaced the _keep_fix_open callback.

    Checked against the SOURCE, not the rendered page: AppTest exposes neither
    an expander's key nor its open state, and an unkeyed expander only reveals
    itself on the second interaction, which is exactly the bug. A read-only
    expander may go unkeyed - collapsing a table costs nothing. One with an
    input in it may not.
    """
    import ast
    from pathlib import Path

    INPUTS = {"text_input", "number_input", "selectbox", "date_input",
              "checkbox", "button", "form", "radio", "text_area"}
    offenders = []
    for path in sorted((Path(__file__).parent.parent / "ui" / "trades").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            for item in node.items:
                call = item.context_expr
                if not (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "expander"):
                    continue
                has_key = any(k.arg == "key" for k in call.keywords)
                has_input = any(
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in INPUTS for n in ast.walk(node))
                if has_input and not has_key:
                    offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, ("these expanders hold inputs but have no key, so "
                           f"they collapse on every rerun: {offenders}")


def test_the_delete_button_is_off_the_daily_screen(app_with_one_pmcc):
    """The one irreversible button in the app used to sit on every card. It is
    a rare, careful job, so it lives with the other rare, careful jobs."""
    at = app_with_one_pmcc.run()
    labels = [e.label or "" for e in at.expander]
    assert not any("Delete this trade" in l for l in labels)
    assert any("Delete an open trade" in l for l in labels)


# --------------------------------------------------------- it always renders
@pytest.mark.parametrize("rows,label", [
    ([], "an empty log"),
    (_closed_row(account="paper"), "practice rows only"),
    (_closed_row(), "one real close"),
])
def test_the_tab_renders_without_an_error_box(app_with_rows, rows, label):
    at = app_with_rows(rows).run()
    assert not at.exception, label
    # _guard turns a crash inside a tab into this message rather than a blank
    # page, so its absence is the real assertion.
    assert not any("unexpected snag" in e.value for e in at.error), label


def test_quick_log_is_at_the_top_not_buried_in_records():
    """Rita: "entering new trades will be not hidden down. it should be
    accessible." Recording a trade she just placed is the most frequent thing
    she does on this tab, and it sat five screens down inside Records."""
    import ast
    from pathlib import Path

    src = (Path(__file__).parent.parent / "ui" / "trades"
           / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    render = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "render")
    order = [n.func.id if isinstance(n.func, ast.Name) else n.func.attr
             for n in ast.walk(render) if isinstance(n, ast.Call)
             and isinstance(n.func, (ast.Name, ast.Attribute))]
    called = [c for c in order if c in
              ("_quick_log_form", "band", "_open_section", "_records_section")]
    assert called.index("_quick_log_form") < called.index("band")
    assert called.index("_quick_log_form") < called.index("_open_section")


def test_two_forms_never_share_a_money_box_label_without_distinct_keys():
    """Quick Log and the roll form both label a box "Credit price on your fill".
    That is fine on screen - they are screens apart - but only because their
    KEYS differ. Tests that match on label find whichever renders first, which
    is what moving Quick Log to the top quietly broke."""
    import ast
    from pathlib import Path

    keys = []
    for name in ("quick_log.py", "actions.py"):
        src = (Path(__file__).parent.parent / "ui" / "trades"
               / name).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", getattr(node.func, "id", ""))
                    in ("number_input", "_fill_price_input")):
                for kw in node.keywords:
                    if kw.arg == "key":
                        keys.append(ast.dump(kw.value))
                if len(node.args) >= 2 and node.func.__class__ is ast.Name:
                    keys.append(ast.dump(node.args[1]))
    assert len(keys) == len(set(keys)), "two money boxes share a key template"


# ===========================================================================
# The Quick Log DRAFT PREVIEW - the branch that shipped a NameError.
#
# Pressing "Check it" stages a draft and renders a preview card outside the
# form. Nothing tested that branch, so when the ui/trades extraction rewrote
# _bp_effect_input -> components.bp_effect_input in app.py but not in the
# modules it had just moved out, the crash only appeared on Rita's screen:
#
#   NameError: name '_bp_effect_input' is not defined
#
# One test that presses the button would have caught it.
# ===========================================================================
def _check_it(at):
    """Fill the minimum Quick Log needs and press Check it.

    EVERY strike box, not just the first: the default strategy is a put credit
    spread, and with a leg missing the form shows a warning instead of staging
    a draft - so a one-strike version of this silently tested nothing.
    """
    for n in at.number_input:
        if (n.key or "").startswith("ql_strike_"):
            n.set_value(6250.0 if "long" in n.key else 6300.0)
    next(n for n in at.number_input
         if (n.key or "").startswith("ql_credit_")).set_value(2.80)
    return next(b for b in at.button if b.label == "Check it").click().run()


def test_pressing_check_it_renders_the_preview_without_crashing(app_with_rows):
    at = app_with_rows([]).run()
    at = _check_it(at)
    assert not at.exception
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"the draft preview crashed: {[str(e.value) for e in snags]}"


def test_the_preview_offers_the_thinkorswim_buying_power_box(app_with_rows):
    """The box the crash was in: she is logging a trade that is already on her
    TOS screen, so the real BP Effect is right there to copy."""
    at = _check_it(app_with_rows([]).run())
    assert any("Buying power effect from thinkorswim" in (n.label or "")
               for n in at.number_input)


def test_the_preview_reads_back_what_she_typed_before_saving(app_with_rows):
    """Saving is the second click, never the first."""
    at = _check_it(app_with_rows([]).run())
    page = _page(at)
    assert "Ready to save" in page
    assert any("Save to my log" in (b.label or "") for b in at.button)


# ===========================================================================
# Repairing a LEAPS logged before the form could take its financing puts.
# Those rows hold one leg and a "credit" that is really the call's cost, so the
# correction panel has to be able to give a trade a leg it never had - the one
# thing it could not do - and rebuild the money from the fills while it is at
# it. Driven through the real app, because the panel's own arithmetic (what
# the call cost, reversed out of a ledger that is wrong) is where this breaks.
LEAPS = "LEAPS Call (Long Call)"


def _bare_leaps_row(trade_id="20260821-142826-WFC"):
    opened = date.today()
    expiry = opened + timedelta(days=518)
    details = {"key": "long_call_leaps", "underlying_price": 84.0,
               "legs": [{"role": "long_call_leaps", "action": "buy",
                         "type": "call", "strike": 70.0, "delta": 0.73,
                         "premium": 21.25, "qty": 1, "dte": 518}],
               # The old sizing had no long_premium branch: the debit went in
               # as a credit and the risk came out as zero.
               "open_cash": 2125.0}
    return [opened.isoformat(), "WFC", LEAPS, "70", 0.0, 518, 1, 2125.0, 0.0,
            0.0, "NO", "", trade_id, "open", expiry.isoformat(), "", "",
            json.dumps(details), "real"]


def _put_boxes(at):
    return (next(n for n in at.number_input if (n.key or "").startswith("ed_fpn_")),
            next(n for n in at.number_input if (n.key or "").startswith("ed_fpk_")),
            next(n for n in at.number_input if (n.key or "").startswith("ed_fppx_")))


def test_the_correction_panel_offers_a_place_for_the_puts(app_with_rows):
    at = app_with_rows([_bare_leaps_row()]).run()
    assert not at.exception
    n, k, px = _put_boxes(at)
    assert n.value == 0 and k.value == 0.0 and px.value == 0.0
    assert "The put(s) you sold against it" in _page(at)
    # ...and it asks what the CALL cost, never what credit it collected.
    assert any("What the call you BOUGHT cost you" in (b.label or "")
               for b in at.number_input)
    assert not any("Credit collected" in (b.label or "") for b in at.number_input)


def test_adding_the_puts_rebuilds_the_money_from_the_fills(app_with_rows):
    """$2,115 for the call against three puts at $6.25 is $240 out of the
    account and $22,740 at risk - and the row said the trade PAID her $2,125."""
    at = app_with_rows([_bare_leaps_row()]).run()
    n, k, px = _put_boxes(at)
    n.set_value(3)
    k.set_value(75.0)
    px.set_value(6.25)
    paid = next(b for b in at.number_input if (b.key or "").startswith("ed_paid_"))
    paid.set_value(2115.0)
    at = at.run()
    assert not at.exception
    page = _page(at)
    assert "puts sold 0 → 3" in page
    assert "$1,875" in page and "$22,500" in page       # collected, and held
    assert "22,740" in page                             # the new worst case


def test_the_correction_it_would_write_carries_both_legs(app_with_rows, monkeypatch):
    """What actually lands in the sheet: the same trade id, both legs, and a
    ledger that finally reads as money out."""
    sent: dict = {}

    from src.logging_tools import trade_logger
    monkeypatch.setattr(trade_logger, "edit_trade",
                        lambda *a, **kw: (sent.update(
                            {"id": a[0], "changes": a[3], "summary": kw.get("summary")}),
                            ("local", False))[1])

    at = app_with_rows([_bare_leaps_row()]).run()
    n, k, px = _put_boxes(at)
    n.set_value(3)
    k.set_value(75.0)
    px.set_value(6.25)
    next(b for b in at.number_input
         if (b.key or "").startswith("ed_paid_")).set_value(2115.0)
    at = at.run()
    next(b for b in at.button if b.key == "ed_go").click().run()

    assert sent["id"] == "20260821-142826-WFC"
    legs = sent["changes"]["legs"]
    assert [l["role"] for l in legs] == ["long_call_leaps", "financing_put"]
    assert legs[1]["qty"] == 3 and legs[1]["strike"] == 75.0
    assert legs[1]["dte"] == legs[0]["dte"]            # one trade, one end date
    assert sent["changes"]["open_cash"] == -240.0
    assert sent["changes"]["credit"] == 0.0            # never premium sold
    assert sent["changes"]["max_loss"] == 22740.0
    assert sent["changes"]["buying_power"] == 20625.0


def _financed_leaps_row(trade_id="20260821-142826-WFC"):
    """The corrected shape: a bought call with three puts sold against it."""
    opened = date.today()
    expiry = opened + timedelta(days=518)
    details = {"key": "long_call_leaps", "underlying_price": 84.0,
               "legs": [{"role": "long_call_leaps", "action": "buy",
                         "type": "call", "strike": 70.0, "delta": 0.73,
                         "premium": 21.15, "qty": 1, "dte": 518},
                        {"role": "financing_put", "action": "sell",
                         "type": "put", "strike": 75.0, "delta": -0.28,
                         "premium": 6.25, "qty": 3, "dte": 518}],
               "open_cash": -240.0}
    return [opened.isoformat(), "WFC", LEAPS, "70 / 75", 0.28, 518, 1, 0.0,
            22740.0, 20625.0, "NO", "", trade_id, "open", expiry.isoformat(),
            "", "", json.dumps(details), "real"]


# ---------- the card follows the TRADE, not the row number ----------
# Streamlit remembers a table selection as a row number, and the open-trades
# table re-sorts whenever something changes what needs doing first: a corrected
# expiry, a price refresh, another trade closed. Rita corrected an NDX expiry,
# the row moved, and the card - with its roll and CLOSE buttons - was showing a
# different trade.
def test_a_stale_row_number_still_shows_the_trade_she_was_on():
    from ui.trades.open_trades import _follow_trade

    # She was on B (row 1). A correction moved it to the top of the list, but
    # the selection is still sitting on row 1, now C.
    assert _follow_trade(["B", "A", "C"], raw=1, was_row=1, was_id="B") == 0


def test_clicking_a_different_row_picks_that_trade():
    from ui.trades.open_trades import _follow_trade

    assert _follow_trade(["A", "B", "C"], raw=2, was_row=1, was_id="B") == 2


def test_a_trade_that_has_left_the_list_falls_back_to_the_selected_row():
    from ui.trades.open_trades import _follow_trade

    assert _follow_trade(["A", "C"], raw=1, was_row=1, was_id="B") == 1


def test_the_first_look_at_the_page_takes_the_selected_row():
    from ui.trades.open_trades import _follow_trade

    assert _follow_trade(["A", "B"], raw=0, was_row=None, was_id=None) == 0


# ---------- taking back a close that never happened ----------
def test_a_closed_trade_can_be_put_back_on_the_books(app_with_rows, monkeypatch):
    """Rita closed a trade she had not closed - the card was showing a different
    one - and her only way back was deleting the record and rebuilding it.

    The panel appends a reopen row; nothing is deleted. This drives the real
    widgets, because the guard (tick the box first) is half the feature.
    """
    written = {}

    def fake_append(row, mirror=None):
        written["row"] = row
        return "local", False

    from src.logging_tools import trade_logger
    monkeypatch.setattr(trade_logger, "_append", fake_append)

    at = app_with_rows(_closed_row()).run()
    assert at.button(key="reopen_go").disabled, "the guard has to be ticked first"

    at = at.checkbox(key="reopen_sure_20260801-101500-SPX").set_value(True).run()
    at = at.button(key="reopen_go").click().run()

    row = written["row"]
    assert row[COLUMNS.index("Event")] == "reopen"
    assert row[COLUMNS.index("Trade ID")] == "20260801-101500-SPX"
    assert row[COLUMNS.index("Account")] == "real"
    said = [m.value for m in at.success] + [m.value for m in at.markdown]
    assert any("back in your open trades" in t for t in said)

