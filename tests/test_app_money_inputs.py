"""Every money box that comes off a TOS fill takes a PRICE, not a total.

Her fill says @1.50. Until now each of these forms demanded 150, so she had to
work out the x100 and the contract count in her head before she could type
anything - and getting it wrong by a factor of 100 is silent, arriving weeks
later as a wrong month report. The boxes take the price and the app does the
arithmetic.

The seeded PMCC and the offline app come from tests/conftest.py. Every number
here is invented; this repo is public.
"""


def _labels(at):
    return [n.label for n in at.number_input]


# --------------------------------------------------------------- closing a trade
def test_the_close_form_takes_the_price_and_works_out_the_result(app_with_one_pmcc):
    """Closing this PMCC at 60.00 a share on 1 contract is $6,000 back. The
    result the form quotes has to follow the price she typed, not 60 dollars."""
    at = app_with_one_pmcc.run()
    box = next(n for n in at.number_input if "RECEIVED when you closed it" in n.label)
    at = box.set_value(60.00).run()

    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "6,000" in body, "60.00 on 1 contract is $6,000"
    # Opened at -$9,500 of cash out, so $6,000 back is a $3,500 loss.
    assert "3,500" in body


def test_the_close_form_shows_what_the_price_comes_to(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    box = next(n for n in at.number_input if "RECEIVED when you closed it" in n.label)
    at = box.set_value(60.00).run()
    body = " ".join(str(m.value) for m in at.markdown)
    assert "on 1 contract" in body


def test_no_typed_a_total_warning_where_a_big_price_is_normal(app_with_one_pmcc):
    """Selling a LEAPS back at 120.00 a share is an ordinary PMCC close. The
    guard that catches a dollar total must not cry wolf on it."""
    at = app_with_one_pmcc.run()
    box = next(n for n in at.number_input if "RECEIVED when you closed it" in n.label)
    at = box.set_value(120.00).run()

    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert "looks like a dollar total" not in warnings


# ------------------------------------------------------------ selling a new call
def test_the_sell_a_call_form_takes_a_price(app_with_uncovered_pmcc):
    """Writing a fresh call against the LEAPS is the other place a fill price
    gets typed, and it had the same '$ total' label."""
    at = app_with_uncovered_pmcc.run()
    assert not at.exception
    labels = _labels(at)
    assert any("Credit price on your fill" == l for l in labels), \
        f"boxes were {labels}"

    box = next(n for n in at.number_input
               if n.label == "Credit price on your fill")
    at = box.set_value(3.00).run()
    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "300" in body, "3.00 on 1 contract is $300"


# -------------------------------------------------------------------- quick log
def test_quick_log_asks_for_contracts_before_any_price(app_with_one_pmcc):
    """Every money box in Quick Log is a price now, and a price only becomes
    dollars once the app knows the contract count - so it has to be asked
    first. This asserts the ORDER of the boxes, which is the whole point."""
    at = app_with_one_pmcc.run()
    labels = _labels(at)
    assert "Contracts" in labels, f"boxes were {labels}"
    money_boxes = [i for i, l in enumerate(labels)
                   if "price" in l.lower() or "Credit price" in l]
    assert money_boxes, f"no price boxes found in {labels}"
    assert labels.index("Contracts") < max(money_boxes)


def test_quick_log_money_boxes_are_priced_not_totalled(app_with_one_pmcc):
    """The labels themselves carry the change - '($ total)' is what sent her
    reaching for a calculator."""
    at = app_with_one_pmcc.run()
    labels = _labels(at)
    assert not any("$ total" in l for l in labels), \
        f"a box still asks for a dollar total: {[l for l in labels if '$ total' in l]}"
    assert any("Credit price" in l for l in labels), f"boxes were {labels}"


# ------------------------------------------------------------- european dates
def test_no_iso_dates_are_shown_to_her(app_with_one_pmcc):
    """2026-09-30 is right for the log file and wrong in a sentence. Nothing
    she reads should be in ISO."""
    import re

    at = app_with_one_pmcc.run()
    assert not at.exception
    iso = re.compile(r"\b20\d\d-\d\d-\d\d\b")
    offenders = [str(m.value)[:120] for m in at.markdown if iso.search(str(m.value))]
    assert not offenders, f"ISO dates still on screen: {offenders}"


def test_every_date_picker_is_set_to_day_first(app_with_one_pmcc):
    from ui.components import DATE_FMT

    at = app_with_one_pmcc.run()
    for box in at.date_input:
        assert box.format == DATE_FMT, f"{box.label} shows {box.format}"


# ------------------------------------------------------- the restructured tab
def test_each_open_trade_appears_once_with_its_own_buttons(app_with_one_pmcc):
    """The tab used to show the same trade up to three times: a Today block
    with forms, a table of all of them, and a dropdown opening ONE in full.
    One trade is now one card."""
    at = app_with_one_pmcc.run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    assert labels.count("🔄 Roll or close the short call") == 1
    assert sum("Close this trade" in l for l in labels) == 1
    assert any("Show the numbers" in l for l in labels)


def test_the_numbers_are_folded_away_behind_the_summary(app_with_one_pmcc):
    """Eleven metrics per trade was most of the scrolling. They are still
    there, one click away."""
    at = app_with_one_pmcc.run()
    assert any("Show the numbers" in e.label for e in at.expander)
    assert any("See them all in one table" in e.label for e in at.expander)


def test_the_card_leads_with_a_sentence_about_the_money(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    body = " ".join(str(m.value) for m in at.markdown)
    # The seeded PMCC sold its call for $500 and cannot be priced offline, so
    # the honest version of the sentence is the credit and the days left.
    assert "credit collected" in body
    assert "days left" in body


def test_the_old_separate_today_block_is_gone(app_with_one_pmcc):
    """It duplicated whichever trades needed action, forms and all. The cards
    are ordered by urgency instead, with one banner at the top."""
    at = app_with_one_pmcc.run()
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Anything to do today?" not in body
