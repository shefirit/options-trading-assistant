"""ROI and annualized ROI - her rulings (2026-10-12): measured on money at risk,
annualized simply (ROI x 365 / days held). Invented numbers."""

from datetime import date, timedelta

import pytest

from src.engine import roi
from src.engine.positions import Position, RollEvent

OPEN = date(2026, 9, 1)


def _closed(result: float, days: int, **kw) -> Position:
    base = dict(trade_id="T", underlying="XYZ", status="closed", opened=OPEN,
                closed_on=OPEN + timedelta(days=days), realized_pl=result)
    base.update(kw)
    return Position(**base)


# ------------------------------------------------------------------ one trade
def test_a_credit_spread_is_measured_on_its_max_loss():
    """$5 wide, $150 collected: $350 at risk. $75 kept = 21.4%."""
    p = _closed(75.0, 30, open_cash=150.0, credit=150.0, max_loss=350.0,
                buying_power=350.0)
    r = roi.trade_roi(p)
    assert r.base == 350.0
    assert r.pct == pytest.approx(21.43, abs=0.01)
    assert r.annual_pct == pytest.approx(21.43 * 365 / 30, abs=0.1)


def test_a_loss_is_a_negative_roi():
    p = _closed(-300.0, 20, open_cash=150.0, max_loss=350.0)
    assert roi.trade_roi(p).pct == pytest.approx(-85.71, abs=0.01)


def test_rolls_count_in_the_result():
    """realized_total: the close plus every roll's cash."""
    p = _closed(-50.0, 40, open_cash=150.0, max_loss=350.0,
                rolls=[RollEvent(cash=120.0)])
    assert roi.trade_roi(p).result == 70.0


def test_a_debit_trade_is_measured_on_the_cash_laid_out_not_its_capped_loss():
    """A collar's max_loss is the small capped figure. Dividing by it would
    turn an ordinary month into a four-digit percentage."""
    p = _closed(250.0, 35, open_cash=-5_000.0, shares_cost=5_000.0,
                max_loss=300.0)
    r = roi.trade_roi(p)
    assert r.base == 5_000.0
    assert r.pct == 5.0


def test_a_weekly_call_annualizes_simply():
    """2% in a week is ~104% a year simple - not the ~181% compounding gives."""
    p = _closed(100.0, 7, open_cash=-5_000.0, shares_cost=5_000.0)
    assert roi.trade_roi(p).annual_pct == pytest.approx(104.3, abs=0.1)


def test_same_day_counts_as_one_day():
    p = _closed(30.0, 0, open_cash=100.0, max_loss=400.0)
    r = roi.trade_roi(p)
    assert r.days == 1
    assert r.annual_pct == pytest.approx(7.5 * 365, abs=0.1)


def test_open_trades_have_no_roi_yet():
    p = Position(trade_id="T", status="open", opened=OPEN, max_loss=350.0,
                 open_cash=150.0)
    assert roi.trade_roi(p) is None
    assert roi.days_held(p, today=OPEN + timedelta(days=9)) == 9


def test_no_recorded_risk_means_no_roi_rather_than_a_guess():
    p = _closed(75.0, 30, open_cash=150.0, max_loss=0.0)
    assert roi.trade_roi(p) is None


# ------------------------------------------------------------------ the book
def test_the_book_is_weighted_by_dollar_days_not_averaged():
    """A tiny two-day win must not outvote a big two-month trade."""
    small = _closed(40.0, 2, open_cash=60.0, max_loss=440.0)       # 9.1%, 2 d
    big = _closed(900.0, 60, open_cash=-10_000.0, shares_cost=10_000.0)  # 9%, 60 d
    book = roi.book_roi([small, big])
    assert book.trades == 2
    assert book.result == 940.0 and book.base == 10_440.0
    assert book.pct == pytest.approx(9.0, abs=0.01)
    dollar_days = 440 * 2 + 10_000 * 60
    assert book.annual_pct == pytest.approx(940 / dollar_days * 365 * 100, abs=0.1)
    naive = (roi.trade_roi(small).annual_pct + roi.trade_roi(big).annual_pct) / 2
    assert book.annual_pct < naive / 5


def test_the_book_skips_open_trades_and_returns_none_when_empty():
    open_one = Position(trade_id="T", status="open", opened=OPEN, max_loss=350.0)
    assert roi.book_roi([open_one]) is None
    assert roi.book_roi([]) is None


# ------------------------------------------------------------------ the Journal
def test_the_journal_shows_roi_on_closed_trades(app_with_rows):
    """A cash secured put: $200 collected on a 100 strike, $9,800 at risk,
    closed five days later for $150 kept - 1.5%, about 112% a year."""
    from conftest import csp_row
    from src.logging_tools.row import build_close_row

    row = csp_row()
    close = build_close_row(row[12], "SOFI", row[2], 50.0, 150.0,
                            "Profit target (50%) hit", account="real")
    at = app_with_rows([row, close]).run()
    assert not at.exception
    table = next(d.value for d in at.dataframe
                 if "ROI / year" in list(d.value.columns))
    assert table["ROI"].iloc[0] == "+1.5%"
    assert table["ROI / year"].iloc[0] == "+111.7%"
    page = " ".join(str(m.value) for m in at.markdown)
    assert "ROI PER YEAR" in page          # the strip above the table
    assert "at risk" in page               # the card on the trade's page


# ------------------------------------------------------------------ open trades
def test_roi_so_far_uses_todays_pl_and_days_held_so_far():
    p = Position(trade_id="T", status="open", opened=OPEN, max_loss=350.0,
                 open_cash=150.0)
    r = roi.open_roi(p, 70.0, today=OPEN + timedelta(days=10))
    assert r.pct == 20.0
    assert r.days == 10
    assert r.annual_pct == pytest.approx(730.0)


def test_roi_so_far_on_shares_uses_the_cash_laid_out():
    p = Position(trade_id="T", status="open", opened=OPEN, open_cash=-5_000.0,
                 shares_cost=5_000.0, max_loss=4_920.0)
    assert roi.open_roi(p, -250.0, today=OPEN + timedelta(days=5)).pct == -5.0


def test_no_price_means_no_roi_so_far():
    p = Position(trade_id="T", status="open", opened=OPEN, max_loss=350.0)
    assert roi.open_roi(p, None) is None


def test_a_closed_trade_has_no_roi_so_far():
    assert roi.open_roi(_closed(75.0, 30, max_loss=350.0), 75.0) is None


def test_the_open_trades_table_and_card_show_roi_so_far(app_with_one_csp,
                                                       monkeypatch):
    """The demo CSP: $200 collected on a 100 strike, $9,800 at risk. Priced at
    $100 to close, the trade is $100 up - 1.0% so far, five days in."""
    from src.data.provider import DataProvider

    monkeypatch.setattr(DataProvider, "price_position", lambda self, p: {
        "priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
        "short_delta": 0.2, "position_value": None, "open_pl": None,
        "options_pl": None, "shares_pl": None})
    at = app_with_one_csp.run()
    assert not at.exception
    table = next(d.value for d in at.dataframe
                 if "P&L %" in list(d.value.columns))
    assert table["P&L $"].iloc[0] == 100.0
    assert table["P&L %"].iloc[0] == pytest.approx(1.02, abs=0.01)
    # The money it is measured on sits beside it, and the per-year figure
    # stays off this table - a few days in it is not a real number.
    assert table["Money in"].iloc[0] == pytest.approx(9800.0)
    assert "ROI / year" not in list(table.columns)
    page = " ".join(str(m.value) for m in at.markdown)
    assert "ROI so far: **+1.0%**" in page


# ------------------------------------------------------------------ the monthly report
def test_the_monthly_report_gives_each_strategy_its_own_roi():
    from src.engine import month_report

    spread = _closed(75.0, 30, strategy_name="Put Credit Spread", open_cash=150.0,
                     max_loss=350.0, account="real")
    spread2 = _closed(-25.0, 10, strategy_name="Put Credit Spread", open_cash=150.0,
                      max_loss=350.0, account="real")
    csp = _closed(200.0, 30, strategy_name="Cash Secured Put", open_cash=200.0,
                  max_loss=9_800.0, account="real")
    rows = month_report.roi_by_strategy([spread, spread2, csp])
    assert [r["name"] for r in rows] == ["Put Credit Spread", "Cash Secured Put"]
    pcs = rows[0]
    assert pcs["trades"] == 2 and pcs["result"] == 50.0 and pcs["base"] == 700.0
    assert pcs["pct"] == pytest.approx(7.14, abs=0.01)
    assert rows[1]["pct"] == pytest.approx(2.04, abs=0.01)

    report = month_report.build([spread, spread2, csp], month=month_report.ALL_TIME,
                                live_from=date(2026, 1, 1), mode="real")
    assert len(report["roi_by_strategy"]) == 2
    assert report["roi_total"]["trades"] == 3
    assert report["roi_total"]["result"] == 250.0


def test_the_report_draws_the_roi_table_with_a_bottom_line(monkeypatch):
    from ui import income_report

    shown: list[str] = []
    monkeypatch.setattr(income_report.st, "markdown",
                        lambda body, **kw: shown.append(str(body)))
    monkeypatch.setattr(income_report.theme, "note", lambda text: None)
    report = {"month": "2026-09", "label": "September 2026",
              "roi_by_strategy": [
                  {"name": "Put Credit Spread", "trades": 2, "result": 50.0,
                   "base": 700.0, "pct": 7.14, "annual_pct": 130.4, "avg_days": 20},
                  {"name": "Cash <Secured> Put", "trades": 1, "result": -20.0,
                   "base": 9_800.0, "pct": -0.2, "annual_pct": -2.5, "avg_days": 30}],
              "roi_total": {"trades": 3, "result": 30.0, "base": 10_500.0,
                            "pct": 0.29, "annual_pct": 4.1, "avg_days": 23}}
    income_report.render_roi_by_strategy(report)
    html = "".join(shown)
    assert "TRADES CLOSED IN SEPTEMBER 2026" in html
    assert "+7.1%" in html and "+130%" in html
    assert "-&#36;20" in html                 # minus before the sign, entity-safe
    assert "Cash &lt;Secured&gt; Put" in html  # names are escaped
    assert "All strategies" in html


def test_no_closed_trades_means_no_roi_table(monkeypatch):
    from ui import income_report

    shown: list[str] = []
    monkeypatch.setattr(income_report.st, "markdown",
                        lambda body, **kw: shown.append(str(body)))
    income_report.render_roi_by_strategy({"roi_by_strategy": [], "roi_total": None})
    assert shown == []


def test_the_monthly_report_gives_each_symbol_its_own_roi():
    """Every strategy on one name counts together under that name."""
    from src.engine import month_report

    spread = _closed(75.0, 30, underlying="SOFI", open_cash=150.0,
                     max_loss=350.0, strategy_name="Put Credit Spread")
    csp = _closed(200.0, 30, underlying="sofi", open_cash=200.0,
                  max_loss=9_800.0, strategy_name="Cash Secured Put")
    other = _closed(-50.0, 10, underlying="BE", open_cash=100.0, max_loss=400.0)
    rows = month_report.roi_by_underlying([spread, csp, other])
    assert [r["name"] for r in rows] == ["SOFI", "BE"]
    assert rows[0]["trades"] == 2
    assert rows[0]["result"] == 275.0 and rows[0]["base"] == 10_150.0
    assert rows[1]["pct"] == -12.5


def test_the_symbol_table_shows_under_top_producers(monkeypatch):
    from ui import income_report

    shown: list[str] = []
    sections: list[str] = []
    monkeypatch.setattr(income_report.st, "markdown",
                        lambda body, **kw: shown.append(str(body)))
    monkeypatch.setattr(income_report.theme, "note", lambda text: None)
    monkeypatch.setattr(income_report.theme, "section",
                        lambda title, eyebrow="": sections.append(title))
    # A month that sold nothing new still has closed trades to measure.
    report = {"month": "all", "label": "All time", "by_underlying": [],
              "roi_by_underlying": [
                  {"name": "SOFI", "trades": 2, "result": 275.0, "base": 10_150.0,
                   "pct": 2.71, "annual_pct": 33.0, "avg_days": 30}],
              "roi_total": {"trades": 2, "result": 275.0, "base": 10_150.0,
                            "pct": 2.71, "annual_pct": 33.0, "avg_days": 30}}
    income_report.render_underlyings(report)
    html = "".join(shown)
    assert sections == ["Which names did the earning?"]
    assert ">SYMBOL<" in html and "SOFI" in html and "+2.7%" in html
    assert "TRADES CLOSED ALL TIME" in html
    assert "All symbols" not in html      # one row needs no bottom line


# ------------------------------------------------------------------ the PMCC card
def _pmcc(row):
    from src.engine import positions
    from src.logging_tools.row import COLUMNS
    return positions.parse_rows(COLUMNS, [row])[0]


def test_pmcc_roi_this_call_premium_and_the_strike_floor(open_pmcc_row):
    """$10,000 LEAPS at 400, a 500 call sold for $500: $9,500 in the trade.
    Opened 7 days ago; the call expires in 32."""
    p = _pmcc(open_pmcc_row())
    r = roi.pmcc_roi(p)
    assert r.base == 9_500.0
    assert r.call_credit == 500.0 and r.call_days == 39
    assert r.premium == 500.0 and r.premium_days == 7
    # At 500 or above at expiry the pair is worth at least (500 - 400) x 100
    # = $10,000, against the $9,500 that went in: at least $500 up.
    assert r.away_result == 500.0
    assert r.away_pct == pytest.approx(5.26, abs=0.01)


def test_no_floor_when_more_calls_are_sold_than_leaps_owned(open_pmcc_row):
    """Her SMH: two calls against one LEAPS. The second call is naked, so
    "at least" would be a promise the trade cannot keep."""
    import json
    row = open_pmcc_row()
    details = json.loads(row[17])
    details["legs"][1]["qty"] = 2
    row[17] = json.dumps(details)
    r = roi.pmcc_roi(_pmcc(row))
    assert r.call_pct is not None
    assert r.away_pct is None


def test_an_uncovered_pmcc_shows_only_the_premium_so_far(open_pmcc_row):
    r = roi.pmcc_roi(_pmcc(open_pmcc_row(covered=False)))
    assert r.call_pct is None and r.away_pct is None
    assert r.premium_pct is not None


def test_the_pmcc_card_shows_the_roi_cards(app_with_one_pmcc):
    at = app_with_one_pmcc.run()
    assert not at.exception
    page = " ".join(str(m.value) for m in at.markdown)
    assert "THIS CALL" in page and "PREMIUM SO FAR" in page
    assert "AT THE STRIKE, AT LEAST" in page
    assert "CALLED AWAY" not in page          # a PMCC owns no shares


def test_a_financed_leaps_counts_only_its_calls_as_premium(open_financed_leaps_row):
    """Her BAC shape: a LEAPS call with puts sold to part-pay for it, then a
    call written against it. The puts' $1,875 is a discount on the LEAPS, not
    income (her ruling, 2026-08-13) - only the call's $46 is premium."""
    from src.engine import positions
    from src.logging_tools.row import COLUMNS, build_roll_row

    row = open_financed_leaps_row()
    call = build_roll_row(row[12], "WFC", row[2], 46.0, new_strike=90.0,
                          new_expiration=date.today() + timedelta(days=11),
                          new_credit=46.0, account="real")
    p = positions.parse_rows(COLUMNS, [row, call])[0]
    assert p.effective_strategy_key == "poor_mans_covered_call"
    r = roi.pmcc_roi(p)
    assert r.premium == 46.0
    assert r.call_credit == 46.0
    assert r.away_pct is None          # the sold puts are not in the floor


# ------------------------------------------------------------------ covered call models
def _model_row(key="covered_call_model_2", ratio=False):
    """$10,000 of shares at 100, a put side costing $1,200 (bought 100 put,
    sold 80 - twice on Model 3), and a 105 call sold for $200. Opened 10 days
    ago; the call expires in 30."""
    import json
    opened = date.today() - timedelta(days=10)
    legs = [
        {"role": "long_put_protection", "action": "buy", "type": "put",
         "strike": 100.0, "delta": -0.45, "premium": 15.0, "qty": 1, "dte": 730},
        {"role": "short_put_ratio" if ratio else "short_put_offset",
         "action": "sell", "type": "put", "strike": 80.0, "delta": -0.15,
         "premium": 3.0, "qty": 2 if ratio else 1, "dte": 730},
        {"role": "short_call", "action": "sell", "type": "call", "strike": 105.0,
         "delta": 0.30, "premium": 2.0, "qty": 1, "dte": 40},
    ]
    details = {"key": key, "underlying_price": 100.0, "legs": legs,
               "open_cash": 200.0 - 10_000.0 - 1_200.0, "shares_cost": 10_000.0}
    return [opened.isoformat(), "SPY", key, "100 / 80 / 105", 0.30, 40, 1,
            200.0, 11_000.0, 11_000.0, "yes", "", "20261002-100000-SPY", "open",
            (opened + timedelta(days=40)).isoformat(), "", "",
            json.dumps(details), "real"]


def _model(*rows):
    from src.engine import positions
    from src.logging_tools.row import COLUMNS
    return positions.parse_rows(COLUMNS, list(rows))[0]


def test_a_covered_call_model_gets_the_three_numbers():
    r = roi.covered_model_roi(_model(_model_row()))
    assert r.base == 11_000.0                       # shares + put side - call
    assert r.call_credit == 200.0 and r.call_days == 40
    assert r.premium == 200.0 and r.premium_days == 10
    # Called away at 105: $500 on the shares + the $200 call = $700, on the
    # $10,000 the shares cost. The put side is left out - she still owns it.
    assert r.away_result == 700.0 and r.away_base == 10_000.0
    assert r.away_pct == 7.0 and r.away_days == 40


def test_put_side_rolls_are_protection_not_premium():
    from src.logging_tools.row import build_roll_row
    row = _model_row()
    put_roll = build_roll_row(row[12], "SPY", row[2], -300.0, new_strike=100.0,
                              new_expiration=date.today() + timedelta(days=900),
                              new_credit=0.0, option_type="put")
    call_roll = build_roll_row(row[12], "SPY", row[2], 150.0, new_strike=106.0,
                               new_expiration=date.today() + timedelta(days=60),
                               new_credit=350.0)
    r = roi.covered_model_roi(_model(row, put_roll, call_roll))
    assert r.premium == 350.0            # the opening call + the call roll only


def test_model_3_gets_the_called_away_card_too():
    """The puts are left out of it, so the ratio's extra sold put does not
    break it."""
    r = roi.covered_model_roi(_model(_model_row("covered_call_model_3", ratio=True)))
    assert r.away_pct == 7.0


def test_the_model_card_shows_the_roi_cards(app_with_rows):
    at = app_with_rows([_model_row()]).run()
    assert not at.exception
    page = " ".join(str(m.value) for m in at.markdown)
    assert "THIS CALL" in page and "PREMIUM SO FAR" in page
    assert "IF CALLED AWAY" in page and "of shares" in page
    assert "put side is left out" in page


# ------------------------------------------------------------------ open trades in the Journal
def _journal_table(at):
    return next(d.value for d in at.dataframe
                if "ROI / year" in list(d.value.columns)
                and "Why closed" in list(d.value.columns))


def test_the_journal_shows_roi_so_far_on_open_trades(app_with_one_csp, monkeypatch):
    """The same demo CSP, priced at $100 to close: $100 up on $9,800 at
    risk, five days in. Marked "so far" - it is live, not a result."""
    from src.data.provider import DataProvider

    monkeypatch.setattr(DataProvider, "price_position", lambda self, p: {
        "priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
        "short_delta": 0.2, "position_value": None, "open_pl": None,
        "options_pl": None, "shares_pl": None})
    at = app_with_one_csp.run()
    assert not at.exception
    table = _journal_table(at)
    assert table["ROI"].iloc[0] == "+1.0% so far"
    assert table["ROI / year"].iloc[0] == "+74.5% so far"
    page = " ".join(str(m.value) for m in at.markdown)
    assert "ROI SO FAR" in page              # the card on the trade's page


def test_an_open_trade_that_cannot_be_priced_still_reads_open(app_with_one_csp):
    """Demo data cannot price SOFI - no number is better than a made-up one."""
    at = app_with_one_csp.run()
    assert not at.exception
    assert _journal_table(at)["ROI"].iloc[0] == "open"


# ------------------------------------------------------------------ open trades in the report
def _open_pair(underlying, strategy, pl, base, days):
    p = Position(trade_id=underlying, status="open", underlying=underlying,
                 strategy_name=strategy, opened=OPEN, max_loss=base,
                 open_cash=100.0)
    return p, roi.open_roi(p, pl, today=OPEN + timedelta(days=days))


def test_the_report_groups_open_trades_roi_so_far():
    from src.engine import month_report

    pairs = [_open_pair("SOFI", "Put Credit Spread", 70.0, 350.0, 10),
             _open_pair("BE", "Put Credit Spread", -35.0, 350.0, 10),
             _open_pair("SOFI", "Cash Secured Put", 98.0, 9_800.0, 10)]
    report = month_report.add_open_roi({"month": "all"}, pairs)
    by_s = {r["name"]: r for r in report["open_roi_by_strategy"]}
    assert by_s["Put Credit Spread"]["trades"] == 2
    assert by_s["Put Credit Spread"]["pct"] == 5.0          # $35 on $700
    by_u = {r["name"]: r for r in report["open_roi_by_underlying"]}
    assert by_u["SOFI"]["result"] == 168.0
    assert report["open_roi_total"]["trades"] == 3


def test_a_past_month_does_not_show_open_trades():
    from src.engine import month_report

    pairs = [_open_pair("SOFI", "Put Credit Spread", 70.0, 350.0, 10)]
    report = month_report.add_open_roi(
        {"month": "2026-08", "is_current": False}, pairs)
    assert report["open_roi_by_strategy"] == []
    assert report["open_roi_total"] is None


def test_the_profit_tab_report_shows_the_open_table(app_with_one_csp, monkeypatch):
    from src.data.provider import DataProvider

    monkeypatch.setattr(DataProvider, "price_position", lambda self, p: {
        "priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
        "short_delta": 0.2, "position_value": None, "open_pl": None,
        "options_pl": None, "shares_pl": None})
    at = app_with_one_csp.run()
    assert not at.exception
    page = " ".join(str(m.value) for m in at.markdown)
    assert "STILL OPEN &middot; ROI SO FAR AT LIVE PRICES" in page
    assert "ROI SO FAR" in page and "+1.0%" in page


# ------------------------------------------------------------------ the dashboard band
def _band_html(monkeypatch, **kw) -> str:
    from ui.trades import dashboard

    shown: list[str] = []
    monkeypatch.setattr(dashboard.st, "markdown",
                        lambda body, **k: shown.append(str(body)))
    report = {"banked": 1_000.0, "label": "October 2026"}
    dashboard.band(report, None, 4_500.0, 1_038.0, 200.0, 0, 2, "14:30",
                   "real", **kw)
    return "".join(shown)


def test_the_band_shows_open_trades_roi_so_far(monkeypatch):
    pairs = [_open_pair("SOFI", "Put Credit Spread", 70.0, 350.0, 10),
             _open_pair("BE", "Put Credit Spread", -35.0, 350.0, 10)]
    book = roi.book_of([r for _, r in pairs])
    html = _band_html(monkeypatch, open_book=book)
    assert "ROI so far" in html and "+5.0%" in html
    assert "&#36;35 on &#36;700 at risk" in html.replace("$", "&#36;")
    assert "ota-band-zones-4" in html          # four columns, not a wrapped row


def test_the_band_says_when_not_every_open_trade_was_priced(monkeypatch):
    book = roi.book_of([_open_pair("SOFI", "Put Credit Spread", 70.0, 350.0, 10)[1]])
    assert "1 of 2 priced" in _band_html(monkeypatch, open_book=book)


def test_no_priced_trades_means_no_roi_zone(monkeypatch):
    html = _band_html(monkeypatch, open_book=None)
    assert "ROI so far" not in html and "ota-band-zones-4" not in html


def test_the_now_tab_band_shows_roi_so_far(app_with_one_csp, monkeypatch):
    from src.data.provider import DataProvider

    monkeypatch.setattr(DataProvider, "price_position", lambda self, p: {
        "priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
        "short_delta": 0.2, "position_value": None, "open_pl": None,
        "options_pl": None, "shares_pl": None})
    at = app_with_one_csp.run()
    assert not at.exception
    band = next(str(m.value) for m in at.markdown if "<div class=\"ota-band\">" in str(m.value))
    assert "Open trades &middot; ROI so far" in band and "+1.0%" in band


# ------------------------------------------------------------------ the Plan tab
def _return_row_html(monkeypatch, report, open_book, all_time):
    from ui.trades import dashboard

    shown: list[str] = []
    monkeypatch.setattr(dashboard.st, "markdown",
                        lambda body, **k: shown.append(str(body)))
    monkeypatch.setattr(dashboard.theme, "note", lambda text: None)
    dashboard.return_row(report, open_book, all_time)
    return "".join(shown)


def test_the_plan_row_shows_live_month_and_all_time_roi(monkeypatch):
    open_book = roi.book_of([_open_pair("SOFI", "Put Credit Spread", 70.0, 350.0, 10)[1]])
    month = {"trades": 3, "result": 250.0, "base": 10_500.0, "pct": 2.38,
             "annual_pct": 40.0, "avg_days": 20.0}
    all_time = roi.book_roi([_closed(75.0, 30, max_loss=350.0, open_cash=150.0)])
    html = _return_row_html(monkeypatch, {"label": "October 2026",
                                          "roi_total": month}, open_book, all_time)
    assert "ROI so far" in html and "+20.0%" in html and "1 open trade" in html
    assert "ROI in October 2026" in html and "+2.4%" in html
    assert "ROI all time" in html and "+21.4%" in html
    assert "&amp;middot;" not in html                 # no escaped entity
    assert 'class="ota-kpi"' in html                    # spans the row


def test_the_plan_row_says_why_a_card_is_empty(monkeypatch):
    html = _return_row_html(monkeypatch, {"label": "October 2026",
                                          "roi_total": None}, None, None)
    assert "nothing open could be priced right now" in html
    assert "nothing closed this month yet" in html


def test_the_plan_tab_shows_the_roi_row(app_with_one_csp, monkeypatch):
    from src.data.provider import DataProvider

    monkeypatch.setattr(DataProvider, "price_position", lambda self, p: {
        "priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
        "short_delta": 0.2, "position_value": None, "open_pl": None,
        "options_pl": None, "shares_pl": None})
    at = app_with_one_csp.run()
    assert not at.exception
    row = next(str(m.value) for m in at.markdown
               if "ROI all time" in str(m.value))
    assert "ROI so far" in row and "+1.0%" in row


# ------------------------------------------------------------------ the Analyze tab
class _FixedPrices:
    """A stand-in provider: every open trade costs $100 to close."""
    is_real = True

    def price_position(self, p):
        return {"priced": True, "cost_to_close": 100.0, "underlying_price": 108.0,
                "short_delta": 0.2, "position_value": None, "open_pl": None,
                "options_pl": None, "shares_pl": None}


def _symbol_strip(monkeypatch, rows, sym="SOFI"):
    from src.engine.config_loader import load_settings, load_strategies
    from src.logging_tools.row import COLUMNS
    from ui import theme
    from ui.trades import data, symbol

    shown: list[str] = []
    monkeypatch.setattr(data, "_load_trade_log", lambda: (COLUMNS, rows, "test"))
    monkeypatch.setattr(symbol.st, "markdown",
                        lambda body, **k: shown.append(str(body)))
    monkeypatch.setattr(theme.st, "markdown",
                        lambda body, **k: shown.append(str(body)))
    monkeypatch.setattr(theme, "note", lambda text: shown.append(str(text)))
    symbol.render(sym, load_settings(), load_strategies(), _FixedPrices())
    return " ".join(shown)


def test_the_analyze_tab_shows_your_roi_on_that_symbol(monkeypatch):
    """One open CSP on SOFI ($100 up on $9,800, 5 days in) and one closed
    ($150 kept on the same $9,800)."""
    from conftest import csp_row
    from src.logging_tools.row import build_close_row

    still_open = csp_row()
    done = csp_row()
    done[12] = "20260101-101500-SOFI"
    done[0] = (date.today() - timedelta(days=30)).isoformat()
    close = build_close_row(done[12], "SOFI", done[2], 50.0, 150.0,
                            "Profit target (50%) hit",
                            closed_on=date.today() - timedelta(days=10),
                            account="real")
    page = _symbol_strip(monkeypatch, [still_open, done, close])
    assert "Your trades on SOFI" in page and "real money" in page
    assert "OPEN · ROI SO FAR" in page and "+1.0%" in page
    assert "CLOSED · ROI" in page and "+1.5%" in page
    assert "so far, 5 days in" in page                   # the per-trade line


def test_a_symbol_never_traded_shows_nothing(monkeypatch):
    from conftest import csp_row
    assert _symbol_strip(monkeypatch, [csp_row()], sym="AAPL") == ""
