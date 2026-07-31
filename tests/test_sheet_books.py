"""The two separate books in the Google Sheet.

The Apps Script (google_apps_script/LogTrade.gs v8) keeps real money and
practice money in two different TABS, and merges them on read-back so the app
still sees one table. The script itself runs inside her spreadsheet and cannot
be executed here, so its merge is reproduced below and the result is fed to the
real parser. That is the part with actual risk: three tabs of different widths
being stitched into one table the app has to read correctly.
"""

from __future__ import annotations

from datetime import date

from src.engine import month_report as mr
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.positions import parse_rows
from src.logging_tools.row import COLUMNS, build_close_row, build_row

ACCOUNT = "Account"

REAL_TAB, PAPER_TAB, LEGACY_TAB = "Real Money Log", "Practice Log", "Options Assistant Log"


def _merge_books(books: list[tuple[str, list, list[list]]]) -> tuple[list, list[list]]:
    """A faithful port of doGet(mode=rows) in LogTrade.gs v8.

    books: [(account, header, rows)] in tab order. The widest header wins, rows
    are mapped onto it BY COLUMN NAME, and every row is stamped with the account
    of the tab it came from - the tab is the record, not the cell.
    """
    header: list = []
    for _account, head, _rows in books:
        if len(head) > len(header):
            header = list(head)
    if ACCOUNT not in header:
        header = header + [ACCOUNT]
    acct_at = header.index(ACCOUNT)

    out_rows: list[list] = []
    for account, head, rows in books:
        for src in rows:
            if not str("".join(str(c) for c in src)).strip():
                continue
            row = []
            for name in header:
                at = head.index(name) if name in head else -1
                row.append(src[at] if 0 <= at < len(src) else "")
            row[acct_at] = account
            out_rows.append(row)
    return header, out_rows


def _trade(underlying="SPX") -> Trade:
    return Trade(
        strategy_key="put_credit_spread", underlying=underlying, contracts=1,
        underlying_price=7535.0,
        legs=[Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                  strike=7200, delta=-0.25, premium=12.0, dte=42),
              Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                  strike=7150, delta=-0.18, premium=7.8, dte=42)])


SIZE = {"credit": 420.0, "max_loss": 4580.0, "buying_power": 4580.0}


def _open(trade_id, account, underlying="SPX", opened=date(2026, 8, 3)):
    return build_row(_trade(underlying), "Put Credit Spread",
                     {**SIZE, "account": account}, True, "",
                     trade_id=trade_id, opened_on=opened,
                     expiration_on=date(2026, 9, 14))


def test_the_three_tabs_merge_into_one_readable_table():
    real = [_open("R1", "real")]
    paper = [_open("P1", "paper", "QQQ")]
    # Her original tab is one column narrower - it predates the Account column.
    legacy = [_open("L1", "", "IWM", opened=date(2026, 6, 10))[:len(COLUMNS) - 1]]

    header, rows = _merge_books([
        ("real", COLUMNS, real),
        ("paper", COLUMNS, paper),
        ("paper", COLUMNS[:-1], legacy),
    ])
    assert header == COLUMNS
    positions = {p.trade_id: p for p in parse_rows(header, rows)}
    assert positions["R1"].account == "real"
    assert positions["P1"].account == "paper"
    assert positions["L1"].account == "paper"
    # The narrower legacy row still parsed into a real position, not a stub.
    assert positions["L1"].underlying == "IWM"
    assert positions["L1"].credit == 420.0


def test_the_tab_decides_the_book_even_if_the_cell_says_otherwise():
    """Someone typing "real" into a cell in the practice tab must not move that
    trade into the real book. The tab is the record."""
    tampered = _open("P2", "real", "QQQ")          # cell says real...
    header, rows = _merge_books([("paper", COLUMNS, [tampered])])   # ...tab says paper
    p = parse_rows(header, rows)[0]
    assert p.account == "paper"
    assert not mr.is_real(p, date(2026, 7, 31))


def test_a_full_trade_keeps_all_its_rows_in_one_book():
    opened = _open("R2", "real")
    closed = build_close_row("R2", "SPX", "Put Credit Spread", 210.0, 210.0,
                             "Profit target (50%) hit",
                             closed_on=date(2026, 8, 21), account="real")
    header, rows = _merge_books([("real", COLUMNS, [opened, closed])])
    p = parse_rows(header, rows)[0]
    assert p.status == "closed"
    assert p.account == "real"
    assert p.realized_pl == 210.0


def test_the_two_books_never_mix_in_the_month_report():
    real = [_open("R3", "real"),
            build_close_row("R3", "SPX", "Put Credit Spread", 210.0, 210.0,
                            "Profit target (50%) hit",
                            closed_on=date(2026, 8, 20), account="real")]
    paper = [_open("P3", "paper", "QQQ"),
             build_close_row("P3", "QQQ", "Put Credit Spread", 100.0, 320.0,
                             "Profit target (50%) hit",
                             closed_on=date(2026, 8, 20), account="paper")]
    header, rows = _merge_books([("real", COLUMNS, real), ("paper", COLUMNS, paper)])
    positions = parse_rows(header, rows)

    live = mr.build(positions, month="2026-08", live_from=date(2026, 7, 31),
                    mode="real")
    practice = mr.build(positions, month="2026-08", live_from=date(2026, 7, 31),
                        mode="practice")
    assert live["banked"] == 210.0
    assert live["trades_closed"] == 1
    assert practice["banked"] == 320.0
    assert practice["trades_closed"] == 1


def test_blank_rows_in_a_tab_are_skipped():
    header, rows = _merge_books([
        ("real", COLUMNS, [_open("R4", "real"), [""] * len(COLUMNS)]),
    ])
    assert len(rows) == 1


def test_an_empty_practice_tab_does_not_break_the_merge():
    header, rows = _merge_books([("real", COLUMNS, [_open("R5", "real")]),
                                 ("paper", COLUMNS, [])])
    assert header == COLUMNS
    assert len(parse_rows(header, rows)) == 1
