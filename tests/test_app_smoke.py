"""The app must render all six tabs offline (demo data) with no tab erroring.

Every tab body runs on every rerun, so one broken tab would take the whole
app down for Rita on her phone - this catches that before a deploy. Network
paths are patched out: the provider is forced to demo mode and the trade log
read returns empty (her real webhook URL lives on this machine)."""

import pytest

from src.data.provider import DataProvider


@pytest.fixture
def demo_app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(DataProvider, "create", classmethod(lambda cls: cls("demo")))
    from src.logging_tools import trade_logger
    monkeypatch.setattr(trade_logger, "fetch_all_rows", lambda: ([], [], "local"))

    at = AppTest.from_file("app.py", default_timeout=30)
    return at


def test_all_six_tabs_render_without_a_snag(demo_app):
    at = demo_app.run()
    assert not at.exception
    assert len(at.tabs) == 6
    # _guard turns a tab crash into this error text - none may appear.
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"


def test_settings_tab_shows_connections_and_plan(demo_app):
    at = demo_app.run()
    all_md = " ".join(str(m.value) for m in at.markdown)
    assert "Where your trades log" in all_md
    assert "Your plan" in all_md
