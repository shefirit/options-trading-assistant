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


# ---------- the setup picker ----------
def _cand(underlying, dte, credit, fits=True):
    from src.engine.models import Action, Candidate, Leg, OptionType, Trade
    trade = Trade(
        strategy_key="put_credit_spread", underlying=underlying, contracts=1,
        underlying_price=7400.0,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=7200, delta=-0.24, premium=8.0, dte=dte),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=7175, delta=-0.18, premium=4.0, dte=dte),
        ])
    return Candidate(trade=trade, credit=credit, max_loss=2100, buying_power=2100,
                     return_on_risk=credit / 2100, short_delta=0.24, dte=dte,
                     fits_sop=fits, note="" if fits else "Delta is a touch above.")


def test_candidate_labels_carry_enough_to_choose_on():
    from ui.components import candidate_labels

    labels = candidate_labels([_cand("SPX", 41, 400)])
    assert labels[0].startswith("#1"), "the number must match the table's # column"
    assert "SPX 7200/7175" in labels[0]
    assert "41 days" in labels[0]
    assert "$400 credit" in labels[0]


def test_candidate_labels_mark_the_best_timed_one_per_underlying():
    from ui.components import candidate_labels

    # Sorted the way the scanner hands them over: best fit first, per underlying.
    labels = candidate_labels([_cand("SPX", 41, 400), _cand("SPX", 23, 415),
                               _cand("NDX", 38, 900), _cand("NDX", 24, 950)])
    assert "⭐ best timed" in labels[0]
    assert "⭐ best timed" not in labels[1]
    assert "⭐ best timed" in labels[2], "each underlying gets its own best"
    assert "⭐ best timed" not in labels[3]


def test_candidate_labels_flag_a_delta_near_miss():
    from ui.components import candidate_labels

    labels = candidate_labels([_cand("SPX", 41, 400), _cand("SPX", 35, 420, fits=False)])
    assert "⚠️ delta over" not in labels[0]
    assert "⚠️ delta over" in labels[1]


def test_candidate_labels_are_unique_so_the_picker_cannot_confuse_two():
    from ui.components import candidate_labels

    labels = candidate_labels([_cand("SPX", 41, 400), _cand("SPX", 41, 400)])
    assert len(set(labels)) == len(labels)


# ---------- the open-trades picker ----------
def _item(underlying, strikes, strategy, opened, dte, action):
    from src.engine.exit_rules import ExitSignal
    from src.engine.models import Action, Leg, OptionType

    class _Pos:
        def __init__(self):
            self.underlying = underlying
            self.strategy_name = strategy
            self.opened = opened
            self.legs = [Leg(role="short_put", action=Action.SELL,
                             option_type=OptionType.PUT, strike=s, delta=-0.2,
                             premium=5.0, dte=dte) for s in strikes]

        def dte_left(self):
            return dte

    sig = ExitSignal(action=action, headline="", reason="", tone="neutral")
    return {"position": _Pos(), "live": {}, "signal": sig}


def test_position_labels_keep_same_day_twins_apart():
    # The bug: labels were dictionary keys of underlying + strategy + open date,
    # so a second SPX spread opened the same day overwrote the first and became
    # impossible to select, close or roll.
    from ui.components import position_labels

    items = [_item("SPX", [7200, 7175], "Put Credit Spread", "2026-07-25", 40, "hold"),
             _item("SPX", [7100, 7075], "Put Credit Spread", "2026-07-25", 40, "hold")]
    labels = position_labels(items)
    assert len(set(labels)) == 2, "two real trades must be two distinct choices"
    assert "7200/7175" in labels[0]
    assert "7100/7075" in labels[1]


def test_position_labels_are_unique_even_when_everything_matches():
    from ui.components import position_labels

    items = [_item("SPX", [7200, 7175], "Put Credit Spread", "2026-07-25", 40, "hold")] * 2
    assert len(set(position_labels(items))) == 2


def test_position_labels_flag_the_ones_needing_action():
    from ui.components import position_labels

    items = [_item("SPX", [7200], "CSP", "2026-07-01", 13, "time"),
             _item("QQQ", [500], "CSP", "2026-07-02", 40, "hold"),
             _item("PLTR", [128], "CSP", "2026-06-23", 13, "stop")]
    labels = position_labels(items)
    assert "⏰ Decide today" in labels[0]
    assert "🛑" in labels[2]
    # A quiet position must not be dressed up as urgent.
    assert "Decide today" not in labels[1] and "🛑" not in labels[1]


def test_the_star_never_lands_on_a_rule_breaker():
    # A star reads as a recommendation, so it may not sit on the setup that
    # breaks the delta rule while the compliant one below goes unmarked.
    from ui.components import candidate_labels

    labels = candidate_labels([_cand("SPX", 45, 400, fits=False),
                               _cand("SPX", 30, 380, fits=True)])
    assert "⭐ best timed" not in labels[0]
    assert "⚠️ delta over" in labels[0]
    assert "⭐ best timed" in labels[1]
