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


# ---------- the Analyze symbol box remembering hand-typed tickers ----------
def test_a_hand_typed_ticker_survives_the_next_rerun():
    """Rita: "i tried to enter 2 tickers one after another... tickers dissapear."

    The option list was rebuilt from the static universe every rerun, so a
    ticker added with the "Add:" line existed for exactly one script run - it
    vanished from the dropdown, and the box blanked because `index` was then
    computed as "not found".
    """
    from app import _remembered_symbols

    base = ["SPX", "SPY", "AAPL"]
    extras = []

    # She adds one that is not in the universe.
    opts = _remembered_symbols(base, extras, "TQQQ")
    assert "TQQQ" in opts
    # ...and it is still there on the next run, without being re-typed.
    assert "TQQQ" in _remembered_symbols(base, extras, "SPY")


def test_a_second_hand_typed_ticker_does_not_evict_the_first():
    from app import _remembered_symbols

    base = ["SPX", "SPY"]
    extras = []
    _remembered_symbols(base, extras, "TQQQ")
    opts = _remembered_symbols(base, extras, "SQQQ")
    assert opts[:2] == ["TQQQ", "SQQQ"], "her own tickers come first"
    assert set(base).issubset(opts)


def test_the_remembered_list_never_duplicates():
    from app import _remembered_symbols

    base = ["SPX", "SPY"]
    extras = []
    for _ in range(3):
        _remembered_symbols(base, extras, "TQQQ")
    opts = _remembered_symbols(base, extras, "SPY")
    assert opts.count("TQQQ") == 1
    assert opts.count("SPY") == 1


def test_a_universe_symbol_needs_no_extra_slot_in_the_dropdown():
    """_remembered_symbols only front-loads names the universe does not carry.

    The list she switches between DOES include universe names - that is
    _remember_symbol's job, and the switcher row is built from it - but a name
    already in `base` needs no second entry to be selectable.
    """
    from app import _remembered_symbols

    extras = []
    _remembered_symbols(["SPX", "SPY"], extras, "SPY")
    assert extras == []


def test_a_handoff_symbol_becomes_selectable():
    """Picks and Premium write straight into the widget key; if that name is not
    an option the box cannot display it."""
    from app import _remembered_symbols

    assert "HOOD" in _remembered_symbols(["SPX"], [], "HOOD")
