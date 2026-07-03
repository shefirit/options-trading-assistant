"""Tests for the Apps Script web-app logger (no real network calls)."""

from __future__ import annotations

import io

from src.logging_tools import webhook_logger


def test_save_and_read_url(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "WEBHOOK_FILE", tmp_path / "hook.txt")
    assert webhook_logger.is_configured() is False
    webhook_logger.set_url("  https://script.google.com/macros/s/abc/exec  ")
    assert webhook_logger.get_url() == "https://script.google.com/macros/s/abc/exec"
    assert webhook_logger.is_configured() is True


def test_non_https_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "WEBHOOK_FILE", tmp_path / "hook.txt")
    webhook_logger.set_url("notaurl")
    assert webhook_logger.is_configured() is False


def test_append_posts_json(tmp_path, monkeypatch):
    monkeypatch.setattr(webhook_logger, "WEBHOOK_FILE", tmp_path / "hook.txt")
    webhook_logger.set_url("https://script.google.com/macros/s/abc/exec")

    captured = {}

    class FakeResp:
        status = 200
        def read(self): return b'{"ok":true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["method"] = req.get_method()
        return FakeResp()

    monkeypatch.setattr(webhook_logger.urllib.request, "urlopen", fake_urlopen)

    result = webhook_logger.append(["2026-07-02", "SPX"], ["Date", "Underlying"])
    assert captured["method"] == "POST"
    assert b"SPX" in captured["body"]
    assert result.startswith("https://docs.google.com/spreadsheets/")
