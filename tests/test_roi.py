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
                 if "ROI so far" in list(d.value.columns))
    assert table["P&L $"].iloc[0] == 100.0
    assert table["ROI so far"].iloc[0] == pytest.approx(1.02, abs=0.01)
    assert table["ROI / year"].iloc[0] == pytest.approx(1.02 * 365 / 5, abs=0.5)
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
