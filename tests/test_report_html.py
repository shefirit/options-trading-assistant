"""The standalone research report is a real HTML document.

It used to be a FRAGMENT. render() started straight at <title> with no doctype,
no <html>, no <head> and - the part that actually bit - no viewport meta. A
browser handed that renders it in quirks mode at desktop width, so the report's
own `@media (max-width: 640px)` rules never fired and the page came out
zoomed-out and unreadable on anything narrow.

Nothing in tests/ touched this file before. These are the cheapest assertions
that would have caught it: a document either declares itself or it does not.
"""

from __future__ import annotations

import re

import pytest

from tools import report_html

# symbol and kind are the only keys render() requires; everything else falls
# back to a .get() default, which is what makes it testable with no network.
MINIMAL = {"symbol": "NVDA", "kind": "stock"}


@pytest.fixture(scope="module")
def page() -> str:
    return report_html.render(MINIMAL)


def test_it_is_a_standards_mode_document(page):
    """No doctype means quirks mode, and quirks mode means the CSS is being
    interpreted by rules nobody writing it had in mind."""
    assert page.lstrip().startswith("<!DOCTYPE html>")


def test_it_declares_a_viewport(page):
    """THE bug. Without this the report's own phone breakpoints never fire -
    the browser assumes a desktop-width canvas and scales the whole page down.
    """
    assert re.search(r'<meta\s+name="viewport"[^>]*width=device-width', page)


def test_it_declares_its_encoding(page):
    """The report prints company names and · separators; without a charset the
    browser guesses, and guesses wrong on the non-ASCII ones."""
    assert re.search(r'<meta\s+charset="utf-8"', page, re.I)


def test_it_names_a_language(page):
    assert re.search(r'<html\s+lang="[a-z-]+"', page)


def test_the_document_opens_and_closes_once(page):
    """A fragment that grew a doctype but kept its old tail would pass every
    test above and still be malformed.

    Matched on a word boundary: the report's masthead is a <header>, and a
    plain "<head" substring counts that too.
    """
    for tag in ("html", "head", "body"):
        opens = len(re.findall(rf"<{tag}\b", page))
        closes = len(re.findall(rf"</{tag}\s*>", page))
        assert opens == 1, f"expected exactly one <{tag}>, found {opens}"
        assert closes == 1, f"expected exactly one </{tag}>, found {closes}"
    assert page.rstrip().endswith("</html>")


def test_the_head_closes_before_the_body_opens(page):
    """Ordering, not just presence - style and script blocks land in whichever
    of the two is open when they are written."""
    assert page.index("<head>") < page.index("</head>") < page.index("<body>")


def test_the_title_is_inside_the_head(page):
    assert page.index("<head>") < page.index("<title>") < page.index("</head>")


def test_it_still_renders_the_report_itself(page):
    """The wrapper must not have swallowed the content it wraps."""
    assert "NVDA" in page
    assert "research note" in page
    # The two markers analyst_report.py replaces with prose afterwards.
    assert "BOTTOM_LINE" in page
