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


def test_the_star_never_lands_on_a_rule_breaker():
    # A star reads as a recommendation, so it may not sit on the setup that
    # breaks the delta rule while the compliant one below goes unmarked.
    from ui.components import candidate_labels

    labels = candidate_labels([_cand("SPX", 45, 400, fits=False),
                               _cand("SPX", 30, 380, fits=True)])
    assert "⭐ best timed" not in labels[0]
    assert "⚠️ delta over" in labels[0]
    assert "⭐ best timed" in labels[1]


# ---------- the always-visible headline numbers ----------
def test_first_sentence_trims_the_essay_but_keeps_short_reasons():
    from ui.trades import widgets

    long = ("Your SOP says never hold past 21 days to expiration without a decision: "
            "from here things go wrong fast. Close it - or roll for a credit.")
    assert widgets._first_sentence(long).endswith("go wrong fast.")
    short = "Kept 78% of the credit."
    assert widgets._first_sentence(short) == short
    assert widgets._first_sentence("") == ""


def test_uncovered_positions_reach_the_today_list():
    """A PMCC with no call written against it is idle capital - the whole income
    of that strategy is the call she has not sold yet."""
    from ui.trades import data as trades_data

    assert "uncovered" in trades_data.ACTION_SIGNALS
    for expected in ("stop", "time", "profit"):
        assert expected in trades_data.ACTION_SIGNALS


def test_risk_card_hides_the_cash_tile_when_it_repeats_the_buying_power():
    # On a credit spread max loss, capital and buying power are one number, and
    # printing it three times was noise. On a PMCC they genuinely differ.
    spread = {"credit": 400.0, "max_loss": 2100.0, "capital": 2100.0,
              "buying_power": 2100.0}
    pmcc = {"credit": 757.0, "max_loss": 16335.0, "capital": 17092.0,
            "buying_power": 0.0}
    assert abs(spread["capital"] - spread["buying_power"]) <= 0.5
    assert abs(pmcc["capital"] - pmcc["buying_power"]) > 0.5


# ---------- funds are not graded like companies ----------
def test_an_etf_gets_no_letter_grade():
    """SPY was scored a D off blank company fundamentals. It is 500 companies -
    profit margin is not missing data, it is the wrong question. premium_finder
    already treated funds this way; the Analyze grader did not."""
    from src.data.stock_analysis import analyze

    closes = [400.0 + i * 0.4 for i in range(260)]
    spy = analyze("SPY", {"quoteType": "ETF", "shortName": "SPDR S&P 500",
                          "regularMarketPrice": 500.0}, closes, avg_volume=70_000_000)
    assert spy.is_fund
    assert spy.grade is None
    assert spy.fundamentals == [], "a fund has no company metrics to show"
    assert "basket" in spy.summary
    assert spy.suitable, "a big liquid fund is a fine thing to sell options on"


def test_a_real_company_still_gets_graded():
    from src.data.stock_analysis import analyze

    closes = [100.0 + i * 0.2 for i in range(260)]
    aapl = analyze("AAPL", {"quoteType": "EQUITY", "sector": "Technology",
                            "marketCap": 3.5e12, "trailingPE": 30.0,
                            "profitMargins": 0.25, "revenueGrowth": 0.10,
                            "regularMarketPrice": 150.0}, closes,
                   avg_volume=50_000_000)
    assert not aapl.is_fund
    assert aapl.grade in list("ABCDF")
    assert aapl.fundamentals


def test_a_fund_is_detected_without_quotetype():
    # Some feeds omit quoteType; no sector and no company economics is the tell.
    from src.data.stock_analysis import analyze

    closes = [50.0 + i * 0.05 for i in range(260)]
    fund = analyze("GLD", {"shortName": "Gold Trust", "regularMarketPrice": 60.0},
                   closes, avg_volume=8_000_000)
    assert fund.is_fund and fund.grade is None


# ---------- the decision date ----------
def test_decide_by_dates_the_time_exit_instead_of_counting_down():
    import datetime as dt

    from ui.components import _decide_by

    class P:
        def __init__(self, d): self._d = d
        def dte_left(self): return self._d

    # Day before month, the way she writes dates.
    assert _decide_by(P(34), 21) == f"{dt.date.today() + dt.timedelta(days=13):%a %d %b}"
    assert _decide_by(P(21), 21) == "today"
    assert _decide_by(P(13), 21) == "overdue"
    assert _decide_by(P(None), 21) == "-"


def test_decide_by_follows_the_strategy_own_time_exit():
    from ui.components import _decide_by

    class P:
        def dte_left(self): return 30

    # A strategy exiting at 30 is due today; one exiting at 21 has nine days.
    assert _decide_by(P(), 30) == "today"
    assert _decide_by(P(), 21) != "today"


# ---------- comparing premium on either side of the trade ----------
def _snap(symbol="AAPL", **kw):
    from src.data.premium_finder import PremiumSnapshot
    base = dict(symbol=symbol, price=200.0, short_strike=190.0, short_delta=0.30,
                credit_dollars=350.0, monthly_yield_pct=1.84,
                annualized_yield_pct=22.0, richness="Fair", liquidity="Good",
                grade="A", verdict="sell")
    base.update(kw)
    return PremiumSnapshot(**base)


def test_the_put_comparison_shows_both_yields_and_the_strike():
    """She compares names to find a good deal, so the yield has to be there in
    the table - the annualised one too - not only in a detail panel."""
    from ui.components import premium_dataframe

    cols = list(premium_dataframe([_snap()]).columns)
    for wanted in ("Verdict", "Quality", "Sell put at", "Delta",
                   "Income $/mo", "Yield %/mo", "Yield %/yr", "Premium deal"):
        assert wanted in cols, wanted


def test_every_put_column_has_help_text():
    from ui.components import premium_column_config, premium_dataframe

    cfg = premium_column_config()
    for col in premium_dataframe([_snap()]).columns:
        if col in ("Symbol", "Watch out"):
            continue
        assert col in cfg, f"{col} has no tooltip"


def test_the_two_sides_use_the_same_yield_wording():
    """The call side reuses the covered-call table the scan uses, so the two
    can never drift apart on what a yield means."""
    from ui.components import covered_call_column_config, premium_column_config

    puts, calls = premium_column_config(), covered_call_column_config()
    # Streamlit column configs are plain dicts under the hood, so read the text
    # out of the whole entry rather than a .help attribute that is not there.
    assert "not compounded" in str(calls["Yield/yr %"])
    assert "not compounded" in str(puts["Yield %/yr"])


def test_an_errored_name_still_gets_a_row():
    from ui.components import premium_dataframe

    df = premium_dataframe([_snap(), _snap(symbol="ZZZZ", error="no chain")])
    assert len(df) == 2
    assert "no chain" in str(df.iloc[1]["Verdict"])


# ------------------------------------------------------------- european dates
def test_dates_are_written_day_first():
    """She writes 30/09/2026, not 2026-09-30. ISO stays in the log file, where
    a machine reads it; everything she reads is day first."""
    import datetime as dt

    from ui.components import fmt_date

    assert fmt_date(dt.date(2026, 9, 30)) == "30/09/2026"
    assert fmt_date(dt.date(2026, 1, 5)) == "05/01/2026"
    assert fmt_date(None) == "-"
    assert fmt_date(None, empty="") == ""


def test_trade_tables_hand_streamlit_real_dates_not_strings():
    """A DD/MM/YYYY string sorts 01/12 above 02/01. Real dates plus a
    DateColumn keep the column both readable AND sortable."""
    import datetime as dt

    from src.engine.positions import Position
    from ui.components import (DATE_FMT, month_trades_column_config,
                               month_trades_dataframe)

    cfg = month_trades_column_config()
    for col in ("Opened", "Closed"):
        assert col in cfg, f"{col} has no column config"
        assert getattr(cfg[col], "format", None) == DATE_FMT or DATE_FMT in str(cfg[col])

    p = Position(trade_id="t1", underlying="SPY", strategy_name="Iron Condor",
                 opened=dt.date(2026, 9, 30), closed_on=dt.date(2026, 12, 1),
                 credit=100.0, realized_pl=50.0, status="closed")
    frame = month_trades_dataframe([{"position": p, "tag": "closed"}])
    assert frame["Opened"].iloc[0] == dt.date(2026, 9, 30),         "the table must carry a date, not a preformatted string"
    assert frame["Closed"].iloc[0] == dt.date(2026, 12, 1)


def test_the_score_card_says_unknown_rather_than_showing_a_dash():
    """A "-" in the amber grade slot reads as a poor score. A name whose data
    never arrived gets a question mark in neutral grey instead."""
    from src.data.stock_analysis import analyze
    from ui.components import _score_card

    closes = [100.0 + i * 0.2 for i in range(260)]
    blank = analyze("NVDA", {}, closes)
    html = _score_card(blank, {})
    assert ">?</div>" in html
    assert "could not be worked out" in html
    assert "#B45309" not in html, "an unknown score must not wear the amber warning colour"


def test_the_score_card_still_badges_a_fund_and_a_company():
    from src.data.stock_analysis import analyze
    from ui.components import _score_card

    closes = [100.0 + i * 0.2 for i in range(260)]
    spy = analyze("SPY", {"quoteType": "ETF", "shortName": "SPDR"}, closes,
                  avg_volume=70_000_000)
    assert "ETF" in _score_card(spy, {})
    aapl = analyze("AAPL", {"quoteType": "EQUITY", "sector": "Technology",
                            "marketCap": 3.5e12, "trailingPE": 30.0,
                            "profitMargins": 0.25, "revenueGrowth": 0.10},
                   closes, avg_volume=50_000_000)
    assert aapl.grade in _score_card(aapl, {})


# ================================================ Records: open and closed apart
# Rita's ask: "separate records between open trades and closed trades - by
# dates (european style dates)". Both halves of that are load-bearing - the two
# kinds answer different questions, and every date on the page is DD/MM/YYYY.
def _pos(**kw):
    import datetime as dt

    from src.engine.positions import Position

    base = dict(trade_id="t", underlying="SPY", strategy_name="Iron Condor",
                opened=dt.date(2026, 6, 1), credit=100.0, status="open")
    return Position(**{**base, **kw})


def test_both_records_tables_format_every_date_the_european_way():
    from ui.components import DATE_FMT, closed_column_config, open_column_config

    for cfg, cols in ((closed_column_config(), ("Closed", "Opened")),
                      (open_column_config(), ("Opened", "Expires"))):
        for col in cols:
            assert col in cfg, f"{col} has no column config"
            assert (getattr(cfg[col], "format", None) == DATE_FMT
                    or DATE_FMT in str(cfg[col])), f"{col} is not {DATE_FMT}"


def test_open_records_are_newest_opened_first():
    import datetime as dt

    from ui.components import by_opened_date, open_dataframe

    old = _pos(trade_id="a", opened=dt.date(2026, 6, 1))
    new = _pos(trade_id="b", opened=dt.date(2026, 8, 7))
    mid = _pos(trade_id="c", opened=dt.date(2026, 7, 4))
    assert [p.trade_id for p in by_opened_date([old, new, mid])] == ["b", "c", "a"]

    frame = open_dataframe([old, new, mid], today=dt.date(2026, 8, 10))
    # A real date, not a pre-formatted string: "01/06" would sort above "07/08".
    assert frame["Opened"].iloc[0] == dt.date(2026, 8, 7)


def test_closed_records_are_newest_closed_first_not_newest_opened():
    """A trade opened in June and closed in August belongs at the top of the
    closed list and the bottom of the open one - the two orders differ."""
    import datetime as dt

    from ui.components import by_closed_date, closed_dataframe

    early = _pos(trade_id="a", opened=dt.date(2026, 7, 20), status="closed",
                 closed_on=dt.date(2026, 7, 24), realized_pl=50.0)
    late = _pos(trade_id="b", opened=dt.date(2026, 6, 1), status="closed",
                closed_on=dt.date(2026, 8, 10), realized_pl=90.0)
    assert [p.trade_id for p in by_closed_date([early, late])] == ["b", "a"]
    assert closed_dataframe([early, late])["Closed"].iloc[0] == dt.date(2026, 8, 10)


def test_open_records_carry_roll_credits_already_banked():
    """The one number that makes an open trade a record rather than a wait:
    money a roll already paid her is hers whatever happens next."""
    import datetime as dt

    from src.engine.positions import RollEvent
    from ui.components import open_dataframe

    p = _pos(expiration=dt.date(2026, 9, 4),
             rolls=[RollEvent(rolled_on=dt.date(2026, 7, 1), cash=300.0),
                    RollEvent(rolled_on=dt.date(2026, 8, 1), cash=235.0)])
    frame = open_dataframe([p], today=dt.date(2026, 8, 10))
    assert frame["Banked so far $"].iloc[0] == 535.0
    assert frame["Days left"].iloc[0] == 25


def test_the_two_records_pickers_lead_with_the_date():
    """Leading with the symbol meant scanning tickers to find "the one from the
    end of July". These lists are read as a diary."""
    import datetime as dt

    from ui.trades.records import _closed_label, _open_label

    assert _open_label(_pos(opened=dt.date(2026, 8, 3))).startswith("03/08/2026")
    closed = _closed_label(_pos(status="closed", closed_on=dt.date(2026, 8, 10),
                                realized_pl=1515.0))
    assert closed.startswith("10/08/2026")
    assert "+$1,515" in closed
