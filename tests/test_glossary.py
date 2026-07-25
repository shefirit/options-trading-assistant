"""Tests for the plain-English glossary.

The glossary's whole job is to catch a word she does not know, so the tests
mostly guard against it quietly falling out of step with the app: a column
header that has no entry, a duplicate, or a definition drifting into the jargon
it is supposed to be translating.
"""

from __future__ import annotations

from ui import glossary


def _defs() -> list[tuple[str, str]]:
    # The filled text, not the templates - the style rules apply to what she
    # actually reads on screen.
    return [row for _title, rows in glossary.filled_sections() for row in rows]


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


# ---------- the glossary must not drift from config ----------
def test_no_placeholder_is_left_unfilled():
    for term, definition in [r for _t, rows in glossary.filled_sections() for r in rows]:
        assert "{" not in term and "}" not in term, f"unfilled placeholder in: {term}"
        assert "{" not in definition and "}" not in definition, f"unfilled in: {term}"


def test_the_glossary_quotes_the_live_config_numbers():
    """Her rules live in config and the app follows. The glossary used to spell
    them out in prose, so changing a delta in strategies.yaml would leave it
    teaching the old one with nothing to catch it."""
    from src.engine.config_loader import get_strategy, load_settings

    text = " ".join(f"{t} {d}" for _s, rows in glossary.filled_sections() for t, d in rows)
    put = get_strategy("put_credit_spread")
    call = get_strategy("call_credit_spread")
    condor = get_strategy("iron_condor")
    settings = load_settings()

    assert f"{put['entry']['short_leg_delta_max']:.2f} delta put" in text
    assert f"{call['entry']['short_leg_delta_max']:.2f} delta" in text
    assert f"{condor['entry']['short_leg_delta_max']:.2f} delta per leg" in text
    assert f"about {put['entry']['dte_target']}" in text
    assert f"past {put['exit']['time_exit_dte']}" in text
    assert f"{float(settings['risk_limits']['monthly_bp_limit']):,.0f}" in text
    assert str(settings["market_read"]["vix_zone_low"]) in text


def test_a_config_change_moves_the_glossary_with_it(monkeypatch):
    # The whole point: edit the rule, and the glossary teaches the new number.
    real = glossary.sop_numbers()
    bumped = dict(real, put_delta="0.20", put_delta_pct="20")
    monkeypatch.setattr(glossary, "sop_numbers", lambda: bumped)
    text = " ".join(d for _s, rows in glossary.filled_sections() for _t, d in rows)
    assert "0.20 delta put" in text
    assert "0.25 delta put" not in text


def test_worked_examples_agree_with_the_rule_they_illustrate():
    vals = glossary.sop_numbers()
    credit = float(vals["eg_credit"].replace(",", ""))
    keep = float(vals["eg_keep"].replace(",", ""))
    loss = float(vals["eg_loss"].replace(",", ""))
    buyback = float(vals["eg_buyback"].replace(",", ""))
    assert keep == credit * float(vals["profit_pct"]) / 100
    assert loss == credit * float(vals["stop_mult"])
    assert buyback == credit + loss
