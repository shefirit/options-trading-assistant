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


def test_all_six_tabs_render_without_a_snag(demo_app):
    at = demo_app.run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    for expected in ("📊 Market", "💡 Picks", "🔬 Analyze", "🎯 Find a trade",
                     "📒 My trades", "⚙️ Settings"):
        assert expected in labels, f"{expected} is missing from the tab bar"
    # _guard turns a tab crash into this error text - none may appear.
    snags = [e for e in at.error if "unexpected snag" in str(e.value)]
    assert not snags, f"a tab crashed: {[str(e.value) for e in snags]}"


def test_the_look_alike_tabs_are_gone(demo_app):
    """Picks, Premium, Analyze and Research all read as "look at names" and gave
    no clue which to open. Premium is a mode inside Picks now, and the six
    research tools are sub-tabs of Analyze - neither may return to the top row."""
    at = demo_app.run()
    labels = [t.label for t in at.tabs]
    assert "🔎 Premium" not in labels
    assert "🔭 Research" not in labels
    # All six tools survive one level down inside Analyze. Short labels, because
    # the full names overflowed the tab row on a 1280-wide laptop.
    for tool in ("🔭 LEAPS", "📅 Seasons", "🎯 Analysts",
                 "✅ Screener", "🧮 Fair price", "⛓️ Options"):
        assert tool in labels, f"{tool} was lost in the merge"


ANALYZE_SUB_TABS = ("📋 Overview", "🔭 LEAPS", "📅 Seasons", "🎯 Analysts",
                    "✅ Screener", "🧮 Fair price", "⛓️ Options")


def test_the_analyze_sub_tabs_stay_short_enough_to_fit(demo_app):
    """Seven sub-tabs share one row. The full names needed about 1320px against
    the ~1183px a 1280-wide laptop gives, which pushed "Options data" off-screen
    behind a scroll arrow. Keep the row inside a budget so it cannot creep back."""
    labels = [t.label for t in demo_app.run().tabs]
    for lbl in ANALYZE_SUB_TABS:
        assert lbl in labels
    assert sum(len(lbl) for lbl in ANALYZE_SUB_TABS) <= 75, "the tab row got long again"


def test_the_research_tools_stay_offline_in_demo_mode(demo_app):
    """Every research tool needs real data. In demo mode each must say so and
    stop, never reaching for the network."""
    at = demo_app.run()
    infos = " ".join(str(i.value) for i in at.info)
    for what in ("Seasonality", "Analyst coverage", "The price calculator",
                 "The options read"):
        assert f"{what} needs real market data" in infos, f"{what} did not guard itself"


def test_market_tab_new_sections_render_in_demo(demo_app):
    """The brief, strategy fit board, economic radar, sector pulse, and news must
    all render real demo content - not their soft-fail notes - with no network."""
    at = demo_app.run()
    assert not at.exception
    all_md = " ".join(str(m.value) for m in at.markdown)
    assert "Today's brief" in all_md
    # Renamed from "Strategy fit today": the ranking is a multi-week read, so
    # "today" made a correct, unchanged answer look stuck.
    assert "Which strategy fits the market now" in all_md
    # And it must show the reasoning behind the order, not just the order.
    # (Demo has no price history, so it states the read without the numbers -
    # the live path prints the measured gap.)
    assert "20-day" in all_md and "50-day" in all_md
    assert "multi-week" in all_md
    assert "What's coming" in all_md
    assert "Sector pulse" in all_md
    assert "Market news" in all_md
    # The retired fear-gauge SECTION must be gone - match its heading, not the
    # phrase. "Fear gauge" is still the plain-English handle for VIX (the tile
    # says "VIX (fear)", and the glossary explains it that way), so a bare
    # substring check fails on prose that has nothing to do with the old chart.
    assert "The fear gauge (VIX)" not in all_md
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
    assert "Your goals and budget" in all_md


def test_the_plan_numbers_can_be_edited_in_the_app(demo_app):
    """Capital, the goals and the buying-power budget drive every progress bar
    in the app, and used to be changeable only by editing a YAML file."""
    at = demo_app.run()
    labels = [i.label for i in at.number_input]
    assert "Capital in the account ($)" in labels
    assert "Monthly income goal ($)" in labels
    assert "Weekly income goal ($)" in labels
    assert "Monthly buying-power budget ($)" in labels


def test_glossary_is_reachable_from_every_tab_and_filters(demo_app):
    """It sits above the tab bar on purpose: an unknown word can appear in a
    table on any tab, so the glossary cannot live inside one of them."""
    at = demo_app.run()
    assert not at.exception

    def glossary_text(app):
        return " ".join(str(m.value) for m in app.markdown)

    full = glossary_text(at)
    assert "Implied volatility (IV)" in full, "the glossary body should render"
    assert "Buying power" in full

    # Typing a word narrows it to the entries that mention that word.
    at.text_input(key="glossary_search").set_value("gamma").run()
    assert not at.exception
    filtered = glossary_text(at)
    assert "Gamma" in filtered
    assert "Implied volatility (IV)" not in filtered, "unrelated entries should drop out"

    # A word that is in no entry says so rather than showing a blank panel.
    at.text_input(key="glossary_search").set_value("zzzzz").run()
    assert "Nothing here matches" in glossary_text(at)
