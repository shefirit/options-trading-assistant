"""Table-formatting helpers in the UI layer (pure, no Streamlit runtime needed)."""

from ui.components import short_strategy


def test_short_strategy_prefers_a_short_uppercase_acronym():
    assert short_strategy("Poor Man's Covered Call (PMCC)") == "PMCC"
    assert short_strategy("Cash Secured Put (CSP)") == "CSP"


def test_short_strategy_drops_a_long_parenthetical():
    assert short_strategy("Call Credit Spread (Bear Call Spread)") == "Call Credit Spread"
    assert short_strategy("Put Credit Spread (Bull Put Spread)") == "Put Credit Spread"


def test_short_strategy_compresses_the_covered_call_models():
    assert short_strategy(
        "Covered Call - Model 3: Zero Cost Ratio") == "Covered Call M3"
    assert short_strategy(
        "Covered Call - Model 1: Absolute Protection") == "Covered Call M1"


def test_short_strategy_leaves_a_plain_name_alone():
    assert short_strategy("Iron Condor") == "Iron Condor"
    assert short_strategy("") == ""


def test_short_strategy_stays_inside_the_column_width():
    """The Strategy column is 160px - roughly 22 characters at this font size."""
    names = [
        "Poor Man's Covered Call (PMCC)",
        "Cash Secured Put (CSP)",
        "Call Credit Spread (Bear Call Spread)",
        "Put Credit Spread (Bull Put Spread)",
        "Covered Call - Model 1: Absolute Protection",
        "Covered Call - Model 2: Classic Spread",
        "Covered Call - Model 3: Zero Cost Ratio",
        "Iron Condor",
    ]
    for n in names:
        assert len(short_strategy(n)) <= 22, n
