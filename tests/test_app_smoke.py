"""The app must render all eight tabs offline (demo data) with no tab erroring.

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


def test_all_eight_tabs_render_without_a_snag(demo_app):
    at = demo_app.run()
    assert not at.exception
    # Eight top-level tabs; the Research tab adds six of its own inside itself,
    # which only build once she is on real data.
    labels = [t.label for t in at.tabs]
    assert "🔭 Research" in labels
    assert len(labels) >= 8
    # _guard turns a tab crash into this error text - none may appear.
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"


def test_research_tab_stays_offline_in_demo_mode(demo_app):
    """Every research tool needs real data. In demo mode the tab must say so
    and stop, never reaching for the network."""
    at = demo_app.run()
    infos = " ".join(str(i.value) for i in at.info)
    assert "research tools need real market data" in infos


def test_market_tab_new_sections_render_in_demo(demo_app):
    """The brief, strategy fit board, economic radar, sector pulse, and news must
    all render real demo content - not their soft-fail notes - with no network."""
    at = demo_app.run()
    assert not at.exception
    all_md = " ".join(str(m.value) for m in at.markdown)
    assert "Today's brief" in all_md
    assert "Strategy fit today" in all_md
    assert "What's coming" in all_md
    assert "Sector pulse" in all_md
    assert "Market news" in all_md
    # The retired fear gauge must be gone.
    assert "fear gauge" not in all_md
    # The _soft wrapper prints this only when a section crashed.
    assert "could not load right now" not in all_md
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags


def test_picks_tab_stays_offline_in_demo_mode(demo_app):
    """In demo mode the Picks tab must show its needs-real-data note and stop -
    it must never try to scan (the smoke suite runs with no network)."""
    at = demo_app.run()
    infos = " ".join(str(i.value) for i in at.info)
    assert "Today's picks need real market data" in infos


def test_demo_mode_shouts_that_the_numbers_are_fake(demo_app):
    """Sample prices look exactly like real ones on screen. When the live feed
    can't be reached the app has to say so loudly, above the tabs, on every
    screen - a quiet amber badge is not enough to trade safely around."""
    at = demo_app.run()
    errors = " ".join(str(e.value) for e in at.error)
    assert "Demo mode" in errors and "FAKE" in errors
    assert "Do not place a trade" in errors
    # And the badge in the hero has to match, not read like business as usual.
    all_md = " ".join(str(m.value) for m in at.markdown)
    assert "DEMO · FAKE numbers · do not trade" in all_md
    assert "ota-chip-red" in all_md


def test_settings_tab_shows_connections_and_plan(demo_app):
    at = demo_app.run()
    all_md = " ".join(str(m.value) for m in at.markdown)
    assert "Where your trades log" in all_md
    assert "Your plan" in all_md
