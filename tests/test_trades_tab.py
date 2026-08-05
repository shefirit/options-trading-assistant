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
def test_the_page_draws_exactly_one_progress_bar(app_with_rows):
    """THE regression test. This page used to carry three progress bars of the
    same number - one in the headline strip, one in the report's goals panel,
    one in the pace note. Three answers to one question reads as an app that
    has not decided what it thinks.

    The one that remains belongs to the open-trades pricing spinner, which is
    a different thing entirely. The goal itself is now drawn once, as the
    bullet chart, which is not a progress element at all.
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
    assert "a steady plan would have produced in 6 days" in page
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
def test_the_card_answers_is_this_winning_without_a_click(app_with_one_pmcc):
    """The card used to hide all eleven numbers behind "Show the numbers", so
    answering "is this one winning?" cost a click on every trade."""
    at = app_with_one_pmcc.run()
    page = _page(at)
    for label in ("KEPT SO FAR", "DAYS LEFT", "DECIDE BY"):
        assert label in page, f"missing from the always-visible strip: {label}"


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
