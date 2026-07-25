"""Tests for the plain-English glossary.

The glossary's whole job is to catch a word she does not know, so the tests
mostly guard against it quietly falling out of step with the app: a column
header that has no entry, a duplicate, or a definition drifting into the jargon
it is supposed to be translating.
"""

from __future__ import annotations

from ui import glossary


def _defs() -> list[tuple[str, str]]:
    return [row for _title, rows in glossary.SECTIONS for row in rows]


def test_every_term_is_defined_once():
    terms = [t.lower() for t in glossary.all_terms()]
    assert terms, "the glossary must not be empty"
    dupes = {t for t in terms if terms.count(t) > 1}
    assert not dupes, f"defined more than once: {sorted(dupes)}"


def test_definitions_are_written_out():
    for term, definition in _defs():
        assert len(definition) > 30, f"{term} has a stub definition"
        assert definition.endswith("."), f"{term} does not end in a full stop"


def test_no_em_dashes_anywhere():
    # Her house style: hyphens, never em or en dashes.
    for term, definition in _defs():
        for bad in ("—", "–"):
            assert bad not in term and bad not in definition, f"dash in: {term}"


def test_the_words_on_the_scan_table_are_all_covered():
    # The columns of the candidates table in Find a trade, which is where she
    # picks a trade and therefore where an unexplained word costs the most.
    terms = " ".join(glossary.all_terms()).lower()
    for word in ("delta", "dte", "credit", "max loss", "buying power",
                 "return on risk", "mid price", "open interest"):
        assert word in terms, f"no glossary entry covers '{word}'"


def test_her_three_exit_rules_are_covered():
    terms = " ".join(glossary.all_terms()).lower()
    for word in ("profit target", "stop loss", "time exit", "rolling"):
        assert word in terms, f"no glossary entry covers '{word}'"


def test_search_matches_the_term():
    hits = [t for t, d in _defs() if glossary._term_matches(t, d, "gamma")]
    assert any(t.lower() == "gamma" for t in hits)


def test_search_matches_inside_a_definition():
    # Typing a word she read on screen should find the entry that explains it,
    # even when that word is not the entry's own title.
    hits = [t for t, d in _defs() if glossary._term_matches(t, d, "thinkorswim")]
    assert hits, "searching a word used in the definitions should find something"


def test_search_for_nonsense_finds_nothing():
    hits = [t for t, d in _defs() if glossary._term_matches(t, d, "zzzzz")]
    assert hits == []
