"""The unified logger tries Google Sheets, then falls back to local Excel."""

from __future__ import annotations

from src.engine.models import Action, Leg, OptionType, Trade
from src.logging_tools import sheets_logger, trade_logger, webhook_logger


def _trade() -> Trade:
    return Trade(
        strategy_key="put_credit_spread", underlying="SPX", contracts=1,
        legs=[
            Leg(role="short_put", action=Action.SELL, option_type=OptionType.PUT,
                strike=5000, delta=-0.08, premium=8.0, dte=30),
            Leg(role="long_put", action=Action.BUY, option_type=OptionType.PUT,
                strike=4975, delta=-0.05, premium=5.0, dte=30),
        ],
    )


SIZE = {"credit": 300.0, "max_loss": 2200.0, "buying_power": 2200.0}


def test_falls_back_to_excel_when_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: False)
    monkeypatch.setattr(sheets_logger, "is_configured", lambda: False)
    monkeypatch.setattr(trade_logger.excel_logger, "append_values",
                        lambda row: tmp_path / "log.xlsx")
    dest, went_to_sheet, trade_id = trade_logger.log_trade(
        _trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is False
    assert "log.xlsx" in dest
    assert trade_id.endswith("-SPX")


def test_webhook_used_first_when_connected(monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    monkeypatch.setattr(webhook_logger, "append",
                        lambda row, header: "https://docs.google.com/spreadsheets/d/XYZ")
    dest, went_to_sheet, trade_id = trade_logger.log_trade(
        _trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is True
    assert dest.endswith("/XYZ")
    assert trade_id


def test_falls_back_when_webhook_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(webhook_logger, "append", boom)
    monkeypatch.setattr(sheets_logger, "is_configured", lambda: False)
    monkeypatch.setattr(trade_logger.excel_logger, "append_values",
                        lambda row: tmp_path / "log.xlsx")
    dest, went_to_sheet, _ = trade_logger.log_trade(
        _trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is False   # gracefully used the backup
    assert "log.xlsx" in dest


def test_close_trade_writes_a_close_event(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: False)
    monkeypatch.setattr(sheets_logger, "is_configured", lambda: False)

    def fake_append(row):
        captured["row"] = row
        return tmp_path / "log.xlsx"
    monkeypatch.setattr(trade_logger.excel_logger, "append_values", fake_append)

    dest, went_to_sheet = trade_logger.close_trade(
        "20260705-120000-SPX", "SPX", "Put Credit Spread",
        exit_cost=150.0, realized_pl=150.0, reason="Profit target (50%) hit")
    assert went_to_sheet is False
    row = captured["row"]
    assert "20260705-120000-SPX" in row
    assert "close" in row
    assert 150.0 in row


def test_delete_trade_uses_sheet_when_connected(monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    monkeypatch.setattr(webhook_logger, "delete_trade", lambda tid: 2)
    removed, source = trade_logger.delete_trade("T1")
    assert removed == 2 and source == "sheet"


def test_delete_trade_uses_local_when_no_sheet(monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: False)
    monkeypatch.setattr(trade_logger.excel_logger, "delete_trade", lambda tid: 1)
    removed, source = trade_logger.delete_trade("T1")
    assert removed == 1 and source == "local"


def test_delete_trade_empty_id_is_noop(monkeypatch):
    removed, source = trade_logger.delete_trade("")
    assert removed == 0


def test_fetch_all_rows_prefers_sheet_then_local(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    monkeypatch.setattr(webhook_logger, "fetch_rows",
                        lambda: (["Date"], [["2026-07-05"]]))
    header, rows, source = trade_logger.fetch_all_rows()
    assert source == "sheet"
    assert rows == [["2026-07-05"]]

    def boom():
        raise RuntimeError("old script")
    monkeypatch.setattr(webhook_logger, "fetch_rows", boom)
    monkeypatch.setattr(trade_logger.excel_logger, "read_rows",
                        lambda: (["Date"], [["2026-07-04"]]))
    header, rows, source = trade_logger.fetch_all_rows()
    assert source == "local"
    assert rows == [["2026-07-04"]]
