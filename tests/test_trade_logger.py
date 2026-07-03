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
    monkeypatch.setattr(trade_logger.excel_logger, "append_row",
                        lambda *a, **k: tmp_path / "log.xlsx")
    dest, went_to_sheet = trade_logger.log_trade(_trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is False
    assert "log.xlsx" in dest


def test_webhook_used_first_when_connected(monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    monkeypatch.setattr(webhook_logger, "append",
                        lambda row, header: "https://docs.google.com/spreadsheets/d/XYZ")
    dest, went_to_sheet = trade_logger.log_trade(_trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is True
    assert dest.endswith("/XYZ")


def test_falls_back_when_webhook_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "is_configured", lambda: True)
    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(webhook_logger, "append", boom)
    monkeypatch.setattr(sheets_logger, "is_configured", lambda: False)
    monkeypatch.setattr(trade_logger.excel_logger, "append_row",
                        lambda *a, **k: tmp_path / "log.xlsx")
    dest, went_to_sheet = trade_logger.log_trade(_trade(), "Put Credit Spread", SIZE, True, "n")
    assert went_to_sheet is False   # gracefully used the backup
    assert "log.xlsx" in dest
