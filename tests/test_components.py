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


# ---------- the price-change header ----------
def test_period_change_reads_a_clean_frame():
    import pandas as pd
    from ui.components import period_change

    frame = pd.DataFrame({"Close": [100.0, 110.0, 120.0]})
    diff, pct = period_change(frame)
    assert diff == 20.0
    assert pct == 20.0


def test_period_change_ignores_nan_at_the_edges():
    # The real bug: Yahoo hands back frames with a NaN close on the boundary,
    # .iloc[0] took it, and the header rendered "$nan (+nan%)" in red because
    # NaN >= 0 is False.
    import pandas as pd
    from ui.components import period_change

    frame = pd.DataFrame({"Close": [float("nan"), 100.0, 120.0, float("nan")]})
    diff, pct = period_change(frame)
    assert diff == 20.0
    assert pct == 20.0


def test_period_change_gives_up_rather_than_showing_a_non_number():
    import pandas as pd
    from ui.components import period_change

    assert period_change(None) is None
    assert period_change(pd.DataFrame({"Close": []})) is None
    assert period_change(pd.DataFrame({"Close": [100.0]})) is None
    assert period_change(pd.DataFrame({"Close": [float("nan")] * 4})) is None
    # A zero first price would divide by zero.
    assert period_change(pd.DataFrame({"Close": [0.0, 50.0]})) is None
    # A frame with no Close column at all.
    assert period_change(pd.DataFrame({"Open": [1.0, 2.0]})) is None


def test_period_change_reports_a_fall_as_negative():
    import pandas as pd
    from ui.components import period_change

    diff, pct = period_change(pd.DataFrame({"Close": [200.0, 150.0]}))
    assert diff == -50.0
    assert pct == -25.0
