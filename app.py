"""Options Trading Assistant - a free-navigation trading dashboard.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

Six tabs, in the order the work happens, all open at once - use them in any
order, nothing is locked. There is no sidebar: everything lives in the tabs, so
nothing can hide behind a toggle or show up twice.

  📊 Market   - is today a good day to sell premium? (holiday-aware)
  💡 Picks    - WHO to sell on, two ways round: scan your whole universe and
                rank it, or compare a list of names you type in yourself
  🔬 Analyze  - everything about ONE name behind one symbol box: the overview
                and strategy fit, plus LEAPS, seasonality, analyst targets, the
                quality screener, the price calculator and the options read
  🎯 Find a trade    - pick a strategy, scan real setups, check your SOP rules, log it
  📒 My trades- every logged trade tracked live against your own exit rules,
                plus your results vs your weekly/monthly goals
  ⚙️ Settings - connections (Google Sheet, earnings data, Schwab) and your plan
                numbers

A glossary sits above the tabs, reachable from all of them.

It never places trades and never gives buy/sell advice. You place every trade
yourself in thinkorswim; this just helps you do it correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src.data.provider import DataProvider
from src.engine import scanner
from src.engine.config_loader import (
    allowed_underlyings_for,
    default_strategy_key,
    load_settings,
    load_strategies,
    underlying_fits_style,
)
from src.engine.strategy_advisor import advise
from src.engine.validator import validate_trade
from ui import components, glossary, research, theme, trades, tv_chart
from ui.trades import account as trades_account

st.set_page_config(page_title="Options Trading Assistant", page_icon="📈", layout="wide")
theme.inject()

MARKET_READ_SYMBOL = "SPX"


@st.cache_resource
def get_provider() -> DataProvider:
    return DataProvider.create()


def money(x: float) -> str:
    return f"${x:,.0f}"


# ------------------------------------------------------------------ small helpers
def _classify(sym: str, settings) -> str:
    if sym in settings["underlyings"]["european_style"]:
        return "index"
    if sym in settings["underlyings"]["us_style"]:
        return "etf"
    return "stock"


def _symbol_options(settings) -> list:
    from src.data import stock_universe
    european = list(settings["underlyings"]["european_style"])
    etfs = list(settings["underlyings"]["us_style"])
    return list(dict.fromkeys(
        european + etfs + stock_universe.FEATURED + stock_universe.all_stocks()))


def _remembered_symbols(base: list, extras: list, current) -> list:
    """The Analyze dropdown's options: her own additions first, then the universe.

    `extras` is MUTATED on purpose - it lives in session state and is how a
    hand-typed ticker survives past the one rerun that created it. `current`
    covers the case where a handoff button (Picks, Premium) wrote a symbol
    straight into the widget's key without it ever being typed here.
    """
    if current and current not in base and current not in extras:
        extras.append(current)
    return list(dict.fromkeys(list(extras) + list(base)))


def _analyze_symbol_box(settings) -> str:
    """The Analyze tab's one symbol box, and the two bugs it used to have.

    Rita: "i tried to enter 2 tickers one after another and it doesn't let it.
    tickers dissapear." Both halves of that were real, and they had one cause
    between them - the option list was rebuilt from the static universe on every
    single rerun, so a ticker she added with the "Add:" line existed for exactly
    one script run.

      1. It vanished from the DROPDOWN. Add TQQQ, move to something else, and
         TQQQ was gone - not merely unselected, absent from the list, so getting
         back to it meant typing the whole thing again. Confirmed live: after
         adding two, neither was in the list, not even the selected one.

      2. The box went BLANK. `index=` was passed alongside `key=` on every
         rerun, and `index` was computed as "where the stored symbol sits in the
         options, or None if it is missing" - which for a hand-typed ticker is
         always None. So the widget was handed "select nothing" on the very
         rerun that was supposed to show what she had just chosen, and the panel
         below carried on rendering the previous name.

    So the tickers she adds are remembered for the session and put at the TOP of
    the list where she can find them, and `index` is gone entirely: the widget's
    key is the single source of truth for what is selected. Seeding the key with
    None keeps the box empty on the first visit rather than pre-selecting SPX,
    which is what dropping `index` would otherwise do.
    """
    extras = st.session_state.setdefault("analyze_extra_syms", [])
    opts = _remembered_symbols(_symbol_options(settings), extras,
                               st.session_state.get("analyze_sym"))
    st.session_state.setdefault("analyze_sym", None)
    sym = st.selectbox("Symbol", opts, key="analyze_sym",
                       placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...",
                       accept_new_options=True)
    return (sym or "").strip().upper()


def _compute_advice(sym, kind, provider, settings):
    ctx = provider.get_market_context(sym)
    analysis = provider.get_stock_analysis(sym) if kind in ("stock", "etf") else None
    tv = provider.get_tradingview(sym, is_index=(kind == "index"))
    earn = provider.get_earnings_info(sym) if kind == "stock" else {}
    price = analysis.price if analysis else ctx.price
    return advise(
        symbol=sym, kind=kind, price=price, trend=ctx.trend, vix=ctx.vix, tv=tv,
        analysis=analysis, earnings_date=earn.get("earnings_date"),
        capital=float(settings["account"]["starting_capital"]),
        monthly_bp=float(settings["risk_limits"]["monthly_bp_limit"]))


from src.data.market_read import days_phrase as _days_phrase  # noqa: E402


# ------------------------------------------------------------------ main
def _mode_badge(provider) -> tuple[str, str]:
    # Demo is red, not amber, and says what the numbers actually are. Sample
    # prices look exactly like real ones on screen, so a quiet badge is the one
    # thing this must never be.
    tone = {"schwab": "green", "yahoo": "green", "demo": "red"}[provider.mode]
    text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
            "demo": "⚠ DEMO · FAKE numbers · do not trade"}[provider.mode]
    return text, tone


DEMO_WARNING = (
    "### ⚠️ Demo mode - every number below is FAKE\n"
    "Live market data could not be reached, so the app is showing bundled **sample** "
    "prices, chains and premiums. They look real and they are not. **Do not place a "
    "trade off anything on this screen.** Check your internet connection and reload "
    "the page.")


def _log_badge() -> tuple[str, str]:
    """Where trades land when you press Log - always visible, because on the
    phone the sidebar (where this used to live) can't be opened."""
    from src.logging_tools import webhook_logger
    if webhook_logger.is_configured():
        return "● Log → Google Sheet", "green"
    return "● Log: this device only", "amber"


def _guard(render, *args) -> None:
    """One tab hitting an error must not blank the whole app (every tab body
    runs on every interaction, so an unhandled exception kills all of them)."""
    try:
        render(*args)
    except Exception as e:
        st.error("This section hit an unexpected snag - the rest of the app still works. "
                 "Reload the page or try again in a minute.")
        with st.expander("Technical details"):
            st.exception(e)


def main() -> None:
    settings = load_settings()
    strategies = load_strategies()
    provider = get_provider()

    theme.hero(
        "Options Trading Assistant",
        "Read the market, get today's picks, screen premium, analyze a name, build and "
        "check the trade.",
        [_mode_badge(provider), _log_badge()])

    # Above the tabs, so it is on screen whichever tab she is reading.
    if provider.mode == "demo":
        st.error(DEMO_WARNING)

    # Same reason: an unknown word can turn up in any table on any tab, so the
    # glossary cannot live on one of them. Collapsed, it costs a single line.
    glossary.render()

    # Six tabs in the order the work actually happens. It was eight, four of
    # which (Picks, Premium, Analyze, Research) all read as "look at names" and
    # gave no clue which to open. Now: Picks finds a name, Analyze studies one.
    (t_market, t_picks, t_analyze, t_build, t_trades, t_settings) = st.tabs(
        ["📊 Market", "💡 Picks", "🔬 Analyze", "🎯 Find a trade", "📒 My trades",
         "⚙️ Settings"])
    with t_market:
        _guard(_tab_market, settings, provider, strategies)
    with t_picks:
        _guard(_tab_picks, settings, strategies, provider)
    with t_analyze:
        _guard(_tab_analyze, settings, provider, strategies)
    with t_build:
        _guard(_tab_build, settings, strategies, provider)
    with t_trades:
        _guard(trades.render, settings, strategies, provider)
    with t_settings:
        _guard(_tab_settings, settings, provider)


# ------------------------------------------------------------------ Market tab
def _trading_verdict(ctx, events):
    """The day's verdict - same thresholds as always, now read from
    config/settings.yaml (market_read:) so the rule lives with your other rules."""
    from src.data import market_read
    return market_read.trading_verdict(ctx, events, market_read.read_cfg(load_settings()))


def _tab_market(settings, provider, strategies) -> None:
    import datetime as dt

    from src.data import market_calendar as cal
    from src.data import market_read
    from src.data.market_context import daily_sentiment

    today = dt.date.today()
    market_open = cal.is_market_open(today)

    # Fetched here (not before the tabs) so the header and tab bar paint
    # immediately - on a phone connection that first second matters.
    with st.spinner("Reading the market..."):
        ctx = provider.get_market_context(MARKET_READ_SYMBOL)
        tiles = provider.get_market_tiles()

    components.render_market_tiles(tiles, market_open)
    changes = [t["change_pct"] for t in tiles if t["symbol"] != "VIX"]
    if not market_open:
        st.markdown(theme.chip("◷ Showing last close - market closed today", "amber"),
                    unsafe_allow_html=True)

    events = provider.get_macro_events(trade_dte=35)

    st.write("")
    with st.container(border=True):
        if not market_open:
            reason = cal.closed_reason(today) or "a non-trading day"
            nxt = cal.next_market_open(today)
            nxt_str = f"{nxt:%A}, {nxt.day} {nxt:%B}"
            st.markdown(theme.chip("🛑  U.S. market closed today", "red"), unsafe_allow_html=True)
            st.markdown(
                f"<div style='margin-top:10px;font-size:1.05rem'>Today is <b>{reason}</b>, so "
                f"the market is closed - no trading and no new prices (the numbers above are the "
                f"last close). Markets reopen <b>{nxt_str}</b>.</div>", unsafe_allow_html=True)
        else:
            headline, tone, why = _trading_verdict(ctx, events)
            icon = {"green": "✅", "amber": "⚠️", "red": "🛑"}[tone]
            st.markdown(theme.chip(f"{icon}  {headline}", tone), unsafe_allow_html=True)
            st.markdown(f"<div style='margin-top:10px;font-size:1.05rem'>{why}</div>",
                        unsafe_allow_html=True)

    sent_label, _sent_note = daily_sentiment(changes, ctx.vix)
    low = sent_label.lower()
    sent_tone = "green" if "positive" in low else "red" if "negative" in low else "amber"
    nxt_ev = events[0] if events else None
    bits = [theme.chip(f"Today: {sent_label}", sent_tone),
            theme.chip(f"Trend: {ctx.trend.title()}", "indigo")]
    if nxt_ev:
        bits.append(theme.chip(f"Next event: {nxt_ev.label} · {_days_phrase(nxt_ev.days_away)}",
                               "amber" if nxt_ev.in_window else "neutral"))
    st.write("")
    st.markdown(" ".join(bits), unsafe_allow_html=True)

    # The sector pulse is fetched once here and shared by the brief and the grid
    # (it is cached, so this is a single request).
    syms = list(settings["underlyings"]["us_style"])
    with st.spinner("Reading today's sector moves..."):
        try:
            pulse_rows = market_read.build_pulse_rows(provider.get_market_pulse(syms), syms)
        except Exception:
            pulse_rows = []

    st.divider()
    _soft(_market_brief_section, changes, ctx, pulse_rows, events, settings, what="market brief")
    st.divider()
    # The same cached series the trend read already used, so this is free.
    try:
        fit_closes = provider.get_history_closes(MARKET_READ_SYMBOL)
    except Exception:
        fit_closes = []
    _soft(_market_fit_section, ctx, strategies, fit_closes, what="strategy board")
    st.divider()
    _soft(_market_radar_section, events, what="economic radar")
    st.divider()
    _soft(_market_pulse_section, pulse_rows, market_open, what="sector pulse")
    st.divider()
    _soft(_market_news_section, provider, what="market news")

    if not provider.is_real:
        st.info("You are offline, so these are sample numbers. Connect to the internet for real "
                "market data (or set up Schwab for true real-time).")


def _soft(render, *args, what: str) -> None:
    """One section failing must not blank the Market tab. The data fetchers
    already return None/[] on failure; this catches anything unexpected on top."""
    try:
        render(*args)
    except Exception:
        theme.note(f"The {what} could not load right now - the rest of this tab still "
                   "works. Try again in a minute.")


def _market_fit_section(ctx, strategies, closes=None) -> None:
    """Every strategy in the SOP ranked on trend and volatility, with the numbers
    that produced the order.

    Rita asked twice why this "always shows the same". Both halves of that were
    fair. It only ever listed the THREE index strategies, so the other six in
    her book had no home on this tab. And it scored trend and volatility in
    coarse buckets - VIX 15 and VIX 24 both landed in "normal" and scored zero -
    so in practice only the up/down/sideways label moved anything, and that
    label is a multi-week read. Both inputs are continuous now, and the board
    covers the whole SOP.
    """
    st.markdown("### 🧭 Which strategy fits the market now")
    theme.note("Your whole strategy book, ranked on **trend** (which way the "
               "market is leaning, and how hard) and **volatility** (how much it "
               "is swinging). These are reasons, not instructions - you check the "
               "winner in 🎯 Find a trade and you decide.")
    _market_fit_evidence(ctx, closes)
    board = getattr(ctx, "board", None) or ctx.suggestions
    if board:
        components.render_strategy_board(board)
    st.write("")
    best_key = ctx.best_strategy_key or list(strategies.keys())[0]
    best_name = ctx.best_strategy_name or strategies[best_key]["name"]
    if st.button(f"Set up {best_name} in Find a trade ▸", type="primary", key="mkt_to_build"):
        st.session_state["build_strategy"] = best_key
        st.session_state["build_underlyings"] = ["SPX"]
        st.session_state["_prev_build_strategy"] = best_key
        st.success("Loaded into **🎯 Find a trade** - open that tab to scan it.")


def _market_fit_evidence(ctx, closes=None) -> None:
    """The two numbers behind the ranking, said out loud.

    "Sideways" with nothing behind it looks like the app gave up. "The 20-day
    average is 0.2% above the 50-day, and it takes 1% to call a direction" is
    the same answer with a reason attached, and it makes an unchanged ranking
    read as steady rather than stuck.
    """
    band = ctx.trend_band * 100
    if ctx.trend == "unknown":
        # An unknown trend used to produce the SAME order as a genuine sideways
        # read, so a failed price fetch was invisible. Now it says so.
        st.warning(f"**{ctx.underlying}'s price history could not be loaded**, so the "
                   "ranking below is on volatility alone and cannot see which way "
                   "the market is leaning. Press ↻ Refresh, or try again in a "
                   "few minutes.")
        return

    if ctx.trend_spread is None:
        # The trend is known but the gap behind it is not - the Schwab and demo
        # paths have no price history to measure. Say the read without pretending
        # to numbers that were never computed.
        read = ("its 20-day and 50-day averages are close enough together that "
                "there is no direction to lean on"
                if ctx.trend == "sideways" else
                f"its short-term average has pulled {'above' if ctx.trend == 'up' else 'below'} "
                "its 50-day average")
    else:
        gap = ctx.trend_spread * 100
        side = "above" if gap >= 0 else "below"
        if ctx.trend == "sideways":
            read = (f"its 20-day average is only **{abs(gap):.1f}%** {side} its "
                    f"50-day average, and it takes **{band:.0f}%** either way to "
                    "call a direction. That is a real no-trend reading, not a "
                    "missing number")
        else:
            read = (f"its 20-day average is **{abs(gap):.1f}%** {side} its 50-day "
                    f"average, past the **{band:.0f}%** it takes to call a direction")

    # "Market fear (VIX)", not "The fear gauge (VIX)" - that exact phrase is
    # the heading of the retired fear-gauge chart section, and a smoke test
    # guards against it coming back.
    vix_bit = (f" Market fear (VIX) is **{ctx.vix:.0f}**, which counts as "
               f"**{ctx.vol_bucket}**." if ctx.vix is not None else "")

    theme.note(f"**{ctx.underlying} is {ctx.trend}** - {read}.{vix_bit}")
    theme.note("This is a **multi-week** read, not a daily one - averages move "
               "slowly, so the same answer for a month is normal and means "
               "conditions have not changed.")
    _market_fit_track_record(ctx, closes)
    if ctx.below_200:
        theme.note("It is also trading **below its 200-day average**, which caps "
                   "the read: a market under its long-term average does not get "
                   "called an uptrend even when the short averages turn up.")


def _market_fit_track_record(ctx, closes) -> None:
    """How often this read has ACTUALLY changed, measured from her own history.

    This line used to be fixed text ("changed about seven times") that nothing
    checked. It is the one number that answers "why is this always the same",
    so it has to be real - and "it has held this read for 34 trading days" says
    far more than any explanation of why an unchanged answer is fine.
    """
    from src.data.market_context import trend_history

    if not closes:
        return
    hist = trend_history(list(closes))
    if not hist["enough"]:
        return

    weeks = hist["run"] / 5      # trading days, so a week is 5
    held = (f"about {weeks:.0f} weeks" if weeks >= 2
            else f"{hist['run']} trading day" + ("s" if hist["run"] != 1 else ""))
    theme.note(f"Measured from {ctx.underlying}'s own price history: the trend read "
               f"changed **{hist['changes']} times** over the last "
               f"**{hist['days']} trading days**, and it has been "
               f"**{hist['trend']}** for {held}.")


def _market_brief_section(changes, ctx, pulse_rows, events, settings) -> None:
    """A plain-English read of the market today, built from the numbers already
    on this tab (no extra fetch)."""
    from src.data import market_read

    st.markdown("### 📋 Today's brief")
    cfg = market_read.read_cfg(settings)
    big = market_read.next_big_event(events)
    brief = market_read.build_brief(changes, ctx.vix, ctx.trend, pulse_rows, big, cfg,
                                    underlying=MARKET_READ_SYMBOL)
    theme.note(brief)


def _market_radar_section(events) -> None:
    """The scheduled events and data that move volatility - shown openly (not in
    an expander) so the calendar is the first thing she sees."""
    st.markdown("### 🗓️ What's coming (events and data that move volatility)")
    theme.note("Selling premium right into a big event is risky - a surprise can blow "
               "through your strikes. Only the **big movers** inside your trade window "
               "get a red flag; the rest are here for awareness.")
    if events:
        components.render_events(events)
    else:
        theme.note("Nothing major on the calendar in the next several weeks.")


def _market_pulse_section(pulse_rows, market_open) -> None:
    """Where money flowed today across the big index, sector, and asset ETFs."""
    st.markdown("### 🗺️ Sector pulse")
    theme.note("Today's move for the big index, sector, and asset ETFs on your list - "
               "context for where premium lives, not a signal to trade.")
    if not pulse_rows:
        theme.note("Couldn't download today's sector moves (the free data source "
                   "sometimes throttles) - try again in a minute or two.")
        return
    components.render_pulse_grid(pulse_rows, market_open)


def _market_news_section(provider) -> None:
    """Recent market headlines from free public feeds - context, not signals."""
    st.markdown("### 📰 Market news")
    theme.note("Recent market and economy headlines, for context - not trade signals. "
               "Tap one to read it at the source.")
    with st.spinner("Loading recent headlines..."):
        items = provider.get_news(limit=6)
    if not items:
        theme.note("Couldn't load headlines right now - try again in a minute or two.")
        return
    components.render_news(items)


# ------------------------------------------------------------------ Today's picks tab
def _tab_picks(settings, strategies, provider) -> None:
    """WHO to sell on, two ways round: let the app find names, or bring your own.

    These were two tabs, "Picks" and "Premium", which read as the same promise
    and did overlap - Picks already prices the ~0.30 delta put for every name it
    scans, which was Premium's whole job. They are the same question asked from
    opposite ends, so they are one tab with two modes.
    """
    theme.section("Who looks good to sell premium on right now?", "Today's picks")

    # Named for what she came looking for. This was "Compare my own list", which
    # contains neither "premium" nor "pays" - so the premium finder was sitting
    # here in plain sight and still looked like it had been deleted.
    mode = st.radio(
        "How do you want to find a name?",
        ["⚡ Scan everything - let the app search my whole universe and rank it",
         "📝 Compare premiums on names I choose - which pays the best deal"],
        key="picks_mode")
    st.write("")
    if mode.startswith("📝"):
        _premium_compare(settings, provider)
        return
    _picks_scan(settings, strategies, provider)


def _watchlist_ui() -> None:
    """Names the Full sweep must always screen, whatever the market screen says.

    The sweep keeps only finalists that clear every bar, so a name she is
    actively researching can vanish on a day it misses one. These are always
    evaluated, so she sees the reason instead of a gap.
    """
    from src.data import stock_universe, watchlist

    saved = watchlist.read()
    with st.expander(f"⭐ My watchlist - always scanned ({len(saved)})", expanded=False):
        theme.note("Add any ticker you are following. It gets scanned every Full sweep even "
                   "if the screen would have dropped it, so you always see where it stands. "
                   "Not on the list? Type it and click the **Add:** line.")
        opts = list(dict.fromkeys(saved + stock_universe.all_stocks()))
        picked = st.multiselect("Tickers", opts, default=saved, key="watchlist_pick",
                                accept_new_options=True,
                                max_selections=watchlist.MAX_SYMBOLS)
        if st.button("Save watchlist", key="watchlist_save"):
            stored = watchlist.save(picked)
            st.success(f"Saved {len(stored)} name(s): {', '.join(stored) or 'none'}"
                       if stored else "Watchlist cleared.")
        theme.note("Saved on this device. The hosted app rebuilds its files when it "
                   "restarts, so re-add them if they ever disappear there.")


def _picks_scan(settings, strategies, provider) -> None:
    import time as _time

    from src.engine import recommender

    theme.note("One button scans your allowed universe - the 4 cash-settled indexes, the big "
               "liquid ETFs, and the whole Russell 1000 (roughly the 1,000 biggest US "
               "companies, so it reaches past the S&P 500) - and ranks who fits your SOP "
               "for a monthly premium trade today: generous premium, sane risk, and a "
               "dividend when there is one. These are **candidates with reasons, not "
               "instructions** - you check the winner in 🎯 Find a trade and you decide.")

    if not provider.is_real:
        st.info("Today's picks need real market data - connect to the internet first. "
                "(Sample data has nothing real to recommend.)")
        return

    monthly = recommender.monthly_target()
    ctx = provider.get_market_context(MARKET_READ_SYMBOL)
    events = provider.get_macro_events(trade_dte=monthly.dte)
    headline, tone, _ = _trading_verdict(ctx, events)
    icon = {"green": "✅", "amber": "⚠️", "red": "🛑"}[tone]
    bits = [theme.chip(f"{icon}  {headline}", tone)]
    if ctx.vix is not None:
        bits.append(theme.chip(f"VIX {ctx.vix:.0f}", "indigo"))
    bits.append(theme.chip(f"🗓️ Target: {monthly.label}", "neutral"))
    st.markdown(" ".join(bits), unsafe_allow_html=True)
    if tone == "red":
        theme.note("Your SOP calls today a sit-out day. The scan still works - just treat "
                   "anything it finds as homework for later, not a trade for right now.")

    scope = st.radio(
        "How wide should the scan look?",
        ["⚡ Quick look - the indexes, the biggest ETFs, and the largest, most-traded "
         "stocks (about a minute)",
         "🌐 Full market sweep - screen the whole Russell 1000 + ~45 big ETFs for hidden "
         "gems (a few minutes the first time each day)"],
        key="picks_scope")
    full = scope.startswith("🌐")

    _watchlist_ui()

    if st.button("💡 Find today's candidates", type="primary", key="picks_go"):
        st.session_state["picks_report"] = _run_picks_scan(
            provider, settings, strategies, monthly, ctx.vix, full)
        st.session_state["picks_report_at"] = _time.time()

    report = st.session_state.get("picks_report")
    if report is None:
        theme.note("Press the button and the app builds today's ranked shortlist for you.")
        return

    age_min = (_time.time() - st.session_state.get("picks_report_at", 0)) / 60
    theme.note(f"Scanned at **{report.generated_at}** - numbers are ~15 minutes delayed."
               + (" It's been a while - press the button again for fresh numbers."
                  if age_min > 15 else ""))
    if report.funnel_note:
        theme.note("🔬 " + report.funnel_note)

    _stale_quotes_note()
    _picks_best_ideas(report, strategies)

    # ---------- Section A: index plays ----------
    st.divider()
    st.markdown("### 🏛️ Index plays - credit spreads and iron condors")
    theme.note("Cash-settled indexes: no shares ever change hands and no early assignment - "
               "the cleanest home for credit spreads. XSP is the mini S&P 500: the same "
               "trade at about one tenth the size, easier on buying power.")
    if report.index_picks:
        st.dataframe(components.picks_index_dataframe(report.index_picks),
                     width="stretch", hide_index=True,
                     column_config=components.picks_index_column_config())
        chosen = st.selectbox("Look closer at one index",
                              [p.symbol for p in report.index_picks],
                              key="picks_index_detail")
        pick = next(p for p in report.index_picks if p.symbol == chosen)
        with st.container(border=True):
            _index_pick_detail(pick, strategies, settings)
    else:
        theme.note("No index looks clean enough to sell right now - any that were close are "
                   "in the 'left out' list below with the reason.")

    # ---------- Section A2: bearish plays on strong fallers (only when any) ----------
    if report.bearish_picks:
        st.divider()
        st.markdown("### 📉 Bearish plays - strong stocks heading down")
        theme.note("When a big, top-quality stock is trending **down**, selling puts on it "
                   "would be a trap (you'd be assigned a falling stock). Instead: a **Call "
                   "Credit Spread** - you sell a call above the price and keep the credit as "
                   "long as it does **not** rally back. Defined risk. Shown only for the "
                   "largest, most-established names (the biggest by market value), because a "
                   "single-stock spread can be assigned early and gaps on news - so the "
                   "underlying has to be rock-solid.")
        st.dataframe(components.picks_index_dataframe(report.bearish_picks),
                     width="stretch", hide_index=True,
                     column_config=components.picks_index_column_config())
        chosenb = st.selectbox("Look closer at one bearish play",
                               [p.symbol for p in report.bearish_picks],
                               key="picks_bearish_detail")
        pickb = next(p for p in report.bearish_picks if p.symbol == chosenb)
        with st.container(border=True):
            _index_pick_detail(pickb, strategies, settings)

    # ---------- Section B: stock & ETF income plays ----------
    st.divider()
    # Named for the put, because that is what it ranks. It used to say "puts and
    # covered calls" while showing no covered calls at all - they now have their
    # own section below, judged on the call rather than as an afterthought here.
    st.markdown("### 💰 Stock and ETF plays - puts you'd sell for income")
    theme.note("The put side: for each name, the one-month put you'd sell (~0.30 delta), the "
               "strategy it points to - a cash secured put, or a PMCC when the shares are too "
               "pricey - and the dividend as a bonus. Only names actually worth selling are "
               "shown; anything hard to trade, in a downtrend, weak, or paying thin premium "
               "is left out (listed at the bottom). Ranked by verdict, then income. "
               "**Covered calls have their own section below.**")
    valid_income = [p for p in report.income_picks if not p.snapshot.error]
    if valid_income:
        st.dataframe(components.picks_income_dataframe(report.income_picks),
                     width="stretch", hide_index=True,
                     column_config=components.picks_income_column_config())
        chosen2 = st.selectbox("See the full plan for one name",
                               [p.snapshot.symbol for p in valid_income],
                               key="picks_income_detail")
        pick2 = next(p for p in valid_income if p.snapshot.symbol == chosen2)
        with st.container(border=True):
            _income_pick_detail(pick2, strategies, settings, provider)
    else:
        theme.note("Nothing cleared the bar to sell this scan - everything scanned was hard to "
                   "trade, trending down, weak, or paying thin premium (see 'left out' below). "
                   "On a quiet day that can happen; try the Full market sweep for more names.")

    # ---------- Section C: covered calls (own the shares, sell the call) ----------
    st.divider()
    st.markdown("### 🛡️ Covered call candidates - you own the shares, the call pays you")
    theme.note("A covered call means buying (or already owning) **100 real shares** and "
               "selling a call against them. These are ranked on what the call pays "
               "against what the shares cost - the monthly yield and that same rate over "
               "a year - and they are judged on their own, on any trend. Your buying-power "
               "budget is deliberately not applied here: size it yourself.")
    if report.covered_call_picks:
        st.dataframe(components.covered_call_dataframe(report.covered_call_picks),
                     width="stretch", hide_index=True,
                     column_config=components.covered_call_column_config())
        chosen3 = st.selectbox("See the full plan for one name",
                               [p.symbol for p in report.covered_call_picks],
                               key="picks_cc_detail")
        pick3 = next(p for p in report.covered_call_picks if p.symbol == chosen3)
        with st.container(border=True):
            components.render_covered_call_detail(pick3)
            fit = ("the protective collar, for high fear and a falling market"
                   if pick3.strategy_key == "covered_call_model_1"
                   else "the classic neutral model")
            theme.note(f"Your SOP fits this one to **{pick3.strategy_name}** - {fit}.")
            _strategy_about(strategies[pick3.strategy_key])
    else:
        theme.note("Nothing clears the covered-call bar this scan - either the calls pay "
                   "too little for the money the shares would tie up, or there is no room "
                   "to the strike before the shares get called away.")

    if report.left_out:
        with st.expander(f"Left out - not among the best right now ({len(report.left_out)})"):
            theme.note("These were scanned but didn't make the cut - shown here so nothing is "
                       "hidden. If you disagree with one, you can still build it in 🎯 Find a trade.")
            for line in report.left_out:
                theme.note("• " + line)

    if report.skipped:
        with st.expander(f"No data this scan ({len(report.skipped)})"):
            theme.note("The app couldn't read option data for these right now (often a brief "
                       "data-source hiccup) - try again in a minute.")
            for line in report.skipped:
                theme.note("• " + line)

    with st.expander("🎓 How these picks are chosen (and ranked)"):
        st.markdown(components._esc(
            "**The funnel, in order:**\n"
            "1. **Universe** - the 4 cash-settled indexes, ~45 large major-issuer ETFs, and "
            "every S&P 500 stock.\n"
            "2. **The screen (stocks and ETFs)** - a name must be large (stocks: market cap "
            "over $10B), trade real dollars daily (over $200M - thin names have costly "
            "option spreads), cost over $15, move enough to pay premium but not wildly "
            "(12%-80% yearly volatility), and NOT be in a downtrend. Every threshold lives "
            "in your config file.\n"
            "3. **The deep look** - the top names by traded dollars get a real option-chain "
            "read: premium richness, the ~0.30-delta put's income, liquidity, earnings "
            "timing, and the dividend.\n"
            "4. **Ranking** - indexes: SOP-fitting setups first, then return on risk. Stocks "
            "and ETFs: the verdict first (good to sell > okay), then monthly yield; "
            "between two names within half a percent of each other, the dividend payer "
            "wins.\n"
            "5. **Only the best shown** - anything the SOP grades 'skip' (hard to trade, "
            "weak company, thin premium) is left out of the tables and listed separately "
            "with the reason, so you only scan real candidates.\n"
            "6. **Downtrends** - selling puts into a faller is a trap, so a downtrending "
            "stock is normally left out. The exception: the biggest names by market value "
            "get a defined-risk bearish Call Credit Spread instead (the 📉 Bearish plays "
            "section) - you win if they do not rally back.\n\n"
            "The app never places trades and never says 'buy this' - it shortlists what "
            "fits your own rules today, with the reasons, and you decide."))


def _covered_call_snapshot(provider, sym, monthly, monthly_bp, put_snap=None):
    """The best TRADABLE expiration for a covered call on this name.

    Her window is 30-45 days, but a window is only useful if something inside it
    can actually be traded. Right now the monthlies sit at 26 and 54 days, so
    everything between them is a weekly - and on single stocks those barely
    trade: at 40 days KO quoted a 43% bid-ask spread and AAPL had an open
    interest of 2. Paying a spread like that costs more than the extra fortnight
    of premium is worth.

    So it widens the search rather than insisting or giving up: her window
    first, nearest her 37-day target, then the liquid monthlies either side.
    First one that is genuinely tradable wins, and the pick itself says so when
    the answer landed outside 30-45. The full chain is cached from the put-side
    snapshot, so each attempt is a slice rather than a fetch.
    """
    from src.engine import recommender

    lo, target, hi = recommender.cc_dte_window()
    quote = provider.call_quote(sym, lo, target, hi)
    if quote is None:
        return None
    if put_snap is None or put_snap.error:
        # No put snapshot to borrow the context from (the Compare screen calls
        # it this way) - pay for one, once, at the expiration just chosen.
        put_snap = provider.get_premium_snapshot(sym, target_dte=quote["dte"],
                                                 monthly_bp=monthly_bp)
        if put_snap.error:
            return None
    # Copy the put-side snapshot and swap in the call. Everything the covered
    # call pick needs beyond the call itself - quality, trend, richness,
    # earnings - was computed a moment ago for the put, so fetching a second
    # snapshot at the new expiration meant redoing all of it to reach five
    # numbers. That was half a second a name, twenty seconds of a scan.
    return put_snap.model_copy(update={
        "dte": quote["dte"],
        "call_strike": quote["strike"],
        "call_delta": quote["delta"],
        "call_credit_dollars": quote["credit"],
        "liquidity": quote["liquidity"],
        "price": quote["price"] or put_snap.price,
    })


def _run_picks_scan(provider, settings, strategies, monthly, vix, full: bool):
    """Stage 1 (screen the market) + stage 2 (option-chain read on the survivors)."""
    import datetime as dt
    import time as _time

    from src.data import cache, market_screener, premium_finder, stock_universe, watchlist
    from src.data.market_context import build_context
    from src.engine import recommender

    indexes = list(settings["underlyings"]["european_style"])
    picks_cfg = settings.get("picks", {}) or {}
    rules = market_screener.rules_from_config(picks_cfg)
    monthly_bp = float(settings["risk_limits"]["monthly_bp_limit"])
    # The only names allowed a single-stock bear call spread: the biggest by
    # market cap (then grade-gated to A/B strong when scanned).
    bearish_pool = set(stock_universe.largest_stocks(int(picks_cfg.get("bearish_top_stocks", 20))))

    report = recommender.PicksReport(
        monthly=monthly, vix=vix, scope="full" if full else "quick",
        generated_at=_time.strftime("%H:%M"))

    # ---- stage 1: who earns an option-chain fetch ----
    if full:
        with st.spinner("Screening the whole market (price, size, volume, trend)..."):
            # all_stocks(), not sp500(): the S&P 500 misses large, liquid names
            # that fail its committee criteria, so the sweep could never surface
            # them. The Russell 1000 covers those, and the screen's own size,
            # volume and trend bars still decide who survives.
            screen = provider.get_screen(f"full:{dt.date.today().isoformat()}",
                                         stock_universe.all_stocks(),
                                         stock_universe.liquid_etfs(), rules)
        if screen is None:
            finalists = ([(s, "etf") for s in settings["underlyings"]["us_style"]]
                         + [(s, "stock") for s in settings["underlyings"]["stocks"]])
            report.funnel_note = ("The whole-market screen couldn't download today (the "
                                  "data source throttled it) - screening your curated "
                                  "shortlists instead. Try the Full sweep again later.")
        else:
            finalists = [(r.symbol, r.kind) for r in screen["finalists"]]
            report.funnel_note = market_screener.funnel_note(screen["results"],
                                                             screen["finalists"])
    else:
        # Quick look = a curated shortlist, no whole-market screen: the biggest ETFs
        # (by assets) and the biggest stocks (by market cap). Falls back to the
        # curated config lists if the data files are missing.
        etf_list = (stock_universe.largest_etfs(int(picks_cfg.get("quick_top_etfs", 15)))
                    or settings["underlyings"]["us_style"])
        stock_list = (stock_universe.largest_stocks(int(picks_cfg.get("quick_top_stocks", 20)))
                      or settings["underlyings"]["stocks"])
        finalists = ([(s, "etf") for s in etf_list]
                     + [(s, "stock") for s in stock_list])
        report.funnel_note = (
            f"Quick look: the {len(indexes)} cash-settled indexes, the {len(etf_list)} "
            f"largest ETFs, and the {len(stock_list)} biggest, most-traded stocks - no "
            "whole-market screen. Run the 🌐 Full market sweep to screen every S&P 500 name.")

    # Always evaluate the biggest stocks too, even if the screen dropped them for
    # trending down - a strong big-cap in a downtrend earns a bearish call spread.
    have = {s for s, _ in finalists}
    finalists += [(s, "stock") for s in bearish_pool if s not in have]

    # Her own watchlist always gets evaluated, even when the screen dropped it.
    # A name she is actively researching should not vanish from her results on
    # a day it happens to miss one bar - she wants to see the reason, not a gap.
    have = {s for s, _ in finalists}
    watching = [s for s in watchlist.read() if s not in have]
    finalists += [(s, "etf" if s in set(stock_universe.liquid_etfs()) else "stock")
                  for s in watching]
    if watching:
        report.funnel_note += (f" Plus {len(watching)} from your watchlist "
                               f"({', '.join(watching)}).")

    total = max(len(indexes) + len(finalists), 1)
    done = 0
    bar = st.progress(0.0, text="Reading option chains...")

    # ---- indexes: trend-fitting strategy + a real scanned monthly setup ----
    for sym in indexes:
        try:
            ictx = provider.get_market_context(sym)
            hv = premium_finder.annualized_vol(provider.get_history_closes(sym))
            chain = provider.get_chain(sym, dte_min=max(monthly.dte - 3, 0),
                                       dte_max=monthly.dte + 3)
            exact = recommender.chain_for_expiration(chain, monthly.expiration)
            pick = recommender.build_index_pick(sym, ictx, exact, hv, monthly)
            if pick.candidate is None and ictx.best_strategy_key in strategies:
                # The monthly sits outside this strategy's SOP window (or has no
                # fitting strike) - scan the normal SOP window and say so.
                lo, hi = scanner.strategy_dte_window(strategies[ictx.best_strategy_key], sym)
                fallback = provider.get_chain(sym, dte_min=lo, dte_max=hi)
                pick = recommender.build_index_pick(sym, ictx, exact, hv, monthly,
                                                    fallback_chain=fallback)
            report.index_picks.append(pick)
        except Exception as e:
            report.skipped.append(f"{sym} - {str(e)[:80]}")
        done += 1
        bar.progress(done / total, text=f"Checked {sym} ({done}/{total})")

    # ---- stocks & ETFs: premium snapshot + dividend + risk ----
    for sym, kind in finalists:
        try:
            snap = provider.get_premium_snapshot(sym, target_dte=monthly.dte,
                                                 monthly_bp=monthly_bp)
            if snap.error:
                report.skipped.append(f"{sym} - {snap.error}")
            elif recommender.is_strong_bearish_stock(kind, sym, snap.trend, bearish_pool):
                # A big, strong stock heading down: sell puts would be a trap, so
                # scan a defined-risk bear Call Credit Spread instead (same cached
                # chain the snapshot just used).
                down_ctx = build_context(sym, snap.price or 0.0, vix=vix, trend="down")
                chain = provider.get_chain(sym, dte_min=max(monthly.dte - 3, 0),
                                           dte_max=monthly.dte + 3)
                exact = recommender.chain_for_expiration(chain, monthly.expiration)
                lo, hi = scanner.strategy_dte_window(strategies["call_credit_spread"], sym)
                fallback = provider.get_chain(sym, dte_min=lo, dte_max=hi)
                report.bearish_picks.append(recommender.build_index_pick(
                    sym, down_ctx, exact, snap.hv, monthly, fallback_chain=fallback,
                    earnings_date=snap.earnings_date, american=True))
            else:
                info = provider.get_raw_info(sym)
                report.income_picks.append(recommender.build_income_pick(
                    snap, kind, info, monthly, monthly_bp=monthly_bp,
                    bp_limit=monthly_bp, vix=vix))
            # The covered-call side is judged separately and on ANY trend. It
            # used to be unreachable: the only path to a covered call ran
            # through a downtrend, and the put-side verdict deleted every
            # downtrending name before it could be shown.
            #
            # Its own expiration, too: the put side follows the monthly, and the
            # monthlies are 26 and 54 days out right now, so neither sits in the
            # 30-45 days she wants for a covered call. This asks for the nearest
            # tradable expiration to 37 instead. The full chain is still cached
            # from the snapshot above, so it costs a slice, not a fetch.
            if not snap.error:
                cc_snap = _covered_call_snapshot(provider, sym, monthly,
                                                 monthly_bp, put_snap=snap)
                if cc_snap is not None:
                    report.covered_call_picks.append(
                        recommender.build_covered_call_pick(
                            cc_snap, kind, provider.get_raw_info(sym), monthly, vix=vix))
        except Exception as e:
            report.skipped.append(f"{sym} - {str(e)[:80]}")
        finally:
            # A parsed full chain is big; only the indexes stay cached (for Build).
            cache.clear(f"cfull:{sym}")
        done += 1
        bar.progress(done / total, text=f"Checked {sym} ({done}/{total})")

    bar.empty()
    ranked_ix = recommender.rank_index_picks(report.index_picks)
    ranked_bear = recommender.rank_index_picks(report.bearish_picks)
    ranked_inc = recommender.rank_income_picks(report.income_picks)
    report.covered_call_picks = [
        p for p in recommender.rank_covered_call_picks(report.covered_call_picks)
        if p.verdict != "skip"]
    # Show only the best - drop the "skip" verdicts (hard to trade, downtrend,
    # weak, thin premium) into a transparent "left out" list.
    (report.index_picks, report.income_picks, report.bearish_picks,
     report.left_out) = recommender.keep_best(ranked_ix, ranked_inc, ranked_bear)
    report.generated_at = _time.strftime("%H:%M")   # stamp the END - a sweep takes minutes
    return report


def _sop_block(notes: list) -> None:
    if not notes:
        return
    st.markdown("**What your SOP says here:**")
    for n in notes:
        theme.note("• " + n)


def _liquidity_line(liquidity, spread_pct, open_interest) -> str:
    line = f"Liquidity: {liquidity}"
    if spread_pct is not None:
        line += f" - bid-ask spread {spread_pct:.0f}% of mid"
        if open_interest:
            line += f", open interest {open_interest:,}"
    return line + "."


def _to_build(strategy_key: str, symbol: str, key: str, label: str) -> None:
    """A button that loads a pick straight into Find a trade."""
    if st.button(label, key=key, type="primary"):
        st.session_state["build_strategy"] = strategy_key
        st.session_state["build_underlyings"] = [symbol]
        st.session_state["_prev_build_strategy"] = strategy_key
        st.success("Loaded into **🎯 Find a trade** - open that tab to scan and "
                   "check it against your SOP.")


def _best_idea_card(icon: str, kind: str, headline: str, why: str,
                    strategy_key: str, symbol: str, key: str) -> None:
    import html as _h

    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:.78rem;font-weight:700;color:{theme.SECONDARY};"
            f"letter-spacing:.04em;'>{icon} {_h.escape(kind).upper()}</div>"
            f"<div style='font-size:1.05rem;font-weight:800;color:{theme.INK};"
            f"margin:2px 0 4px;'>{_h.escape(headline)}</div>"
            f"<div style='color:{theme.CAPTION};line-height:1.55;'>{_h.escape(why)}</div>",
            unsafe_allow_html=True)
        _to_build(strategy_key, symbol, key, f"Set this up on {symbol} ▸")


def _stale_quotes_note() -> None:
    """Say so when the liquidity read cannot be trusted.

    The app happily prints "hard to trade" and "thin premium" off a closed
    market's last prints. Rita pointed this out on a Sunday: KO showed a 43%
    bid-ask spread and AAPL an open interest of 2, which is Friday's stale quote
    rather than an untradable option. Prices and yields survive the weekend;
    spreads do not.
    """
    from src.data import market_calendar

    why = market_calendar.quotes_are_stale()
    if not why:
        return
    st.warning(
        f"◷ **The market is closed ({why})** - these are the last prints from when it "
        "was open. Bid-ask spreads go wide once trading stops, so **“hard to trade” "
        "and “thin premium” verdicts are unreliable right now**, and names may be "
        "left out that would pass on a trading day. Prices, credits and yields are fine; "
        "the liquidity read is not. Re-run it when the market is open before acting on it.")


def _picks_best_ideas(report, strategies) -> None:
    """The one-screen answer to "just tell me the good ones".

    The scan already ranks everything, and then hands her four tables and four
    dropdowns to read - which is still a lot of choosing for someone who asked
    the app to choose. This is the top of each ranked list, one line each, with
    the reason and a way straight into Find a trade. The detailed sections stay
    below for when she wants to look properly.
    """
    best_index = report.index_picks[0] if report.index_picks else None
    best_bear = report.bearish_picks[0] if report.bearish_picks else None
    best_income = next((p for p in report.income_picks if not p.snapshot.error), None)
    best_call = report.covered_call_picks[0] if report.covered_call_picks else None

    if not any((best_index, best_bear, best_income, best_call)):
        return

    st.divider()
    st.markdown("### ⭐ Today's best ideas")
    theme.note("The top of each list below, in one place - so you do not have to read four "
               "tables to find them. Every one is a candidate with a reason, never an "
               "instruction: check it against your SOP in 🎯 Find a trade before "
               "you place anything.")

    cols = st.columns(2)
    slot = 0

    def place():
        nonlocal slot
        col = cols[slot % 2]
        slot += 1
        return col

    if best_index is not None:
        c = best_index.candidate
        money_bit = (f"{money(c.credit)} credit against {money(c.max_loss)} of risk "
                     f"({c.return_on_risk * 100:.0f}% return on risk)"
                     if c is not None else "a setup at your SOP delta")
        with place():
            _best_idea_card(
                "🏛️", "Best index play",
                f"{best_index.symbol} · {best_index.strategy_name}",
                f"{money_bit}. {best_index.why[0] if best_index.why else ''}",
                best_index.strategy_key, best_index.symbol,
                f"best_ix_{best_index.symbol}")

    if best_income is not None:
        s_ = best_income.snapshot
        name = strategies.get(best_income.strategy_key, {}).get(
            "name", best_income.strategy_key)
        yield_bit = (f"{money(s_.credit_dollars)} a month"
                     if s_.credit_dollars else "premium")
        if s_.monthly_yield_pct:
            yield_bit += f" ({s_.monthly_yield_pct:.2f}% of the cash set aside)"
        with place():
            _best_idea_card(
                "💰", "Best put to sell",
                f"{s_.symbol} · {components.short_strategy(name)}",
                f"Sell the {s_.short_strike:g} put: {yield_bit}. "
                f"Trend {s_.trend}, quality {components.quality_label(s_.symbol, s_.grade)}."
                if s_.short_strike else yield_bit,
                best_income.strategy_key, s_.symbol, f"best_inc_{s_.symbol}")

    if best_call is not None:
        with place():
            _best_idea_card(
                "🛡️", "Best covered call",
                f"{best_call.symbol} · {components.short_strategy(best_call.strategy_name)}",
                f"Sell the {best_call.call_strike:g} call {best_call.dte} days out for "
                f"{money(best_call.call_credit or 0)} - {best_call.monthly_yield_pct:.2f}% "
                f"for the month, about {best_call.annualized_yield_pct:.0f}% a year. "
                f"You need 100 shares ({money(best_call.shares_cost or 0)}).",
                best_call.strategy_key, best_call.symbol, f"best_cc_{best_call.symbol}")

    if best_bear is not None:
        cb = best_bear.candidate
        bits = (f"{money(cb.credit)} credit against {money(cb.max_loss)} of risk"
                if cb is not None else "a defined-risk bear call spread")
        with place():
            _best_idea_card(
                "📉", "Best bearish play",
                f"{best_bear.symbol} · {best_bear.strategy_name}",
                f"{bits}. It is trending down, so selling puts would be the trap - "
                "this wins if it does not rally back.",
                best_bear.strategy_key, best_bear.symbol, f"best_bear_{best_bear.symbol}")


def _picks_risk_block(max_loss, bp, settings, liquidity_line, settlement, events,
                      extra=None) -> None:
    """One candidate's risk picture: worst case, buying power vs the monthly
    limit, liquidity, settlement style, and the events inside the window."""
    bp_limit = float(settings["risk_limits"]["monthly_bp_limit"])
    loss_txt = f"&#36;{max_loss:,.0f}" if max_loss is not None else "see Find a trade"
    bp_txt = (f"&#36;{bp:,.0f} <span style='font-size:.9rem;font-weight:600;'>"
              f"({bp / bp_limit * 100:.0f}% of your &#36;{bp_limit:,.0f} monthly limit)"
              "</span>" if bp is not None else "see Find a trade")
    st.markdown(
        f"""
        <div style="border:2px solid {theme.RED};border-radius:14px;padding:12px 16px;
                    background:#FDF3F2;margin:8px 0 4px;">
          <div style="font-weight:800;color:{theme.RED};">⚠️ Risk picture (1 contract)</div>
          <div style="display:flex;gap:28px;flex-wrap:wrap;margin-top:8px;">
            <div><div style="color:#5B2320;font-weight:600;font-size:.85rem;">MOST YOU CAN LOSE</div>
                 <div style="font-size:1.35rem;font-weight:800;color:{theme.RED};">{loss_txt}</div></div>
            <div><div style="color:#213229;font-weight:600;font-size:.85rem;">BUYING POWER NEEDED</div>
                 <div style="font-size:1.35rem;font-weight:800;color:{theme.INK};">{bp_txt}</div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True)
    theme.note("• " + settlement)
    theme.note("• " + liquidity_line)
    for line in (extra or []):
        theme.note("• " + line)
    in_window = [e for e in events if e.in_window]
    st.markdown("**Events inside this trade window:**")
    components.render_events(in_window, empty_note="None - a clean window.")


def _index_pick_detail(pick, strategies, settings) -> None:
    if pick.price:
        st.markdown(components._esc(
            f"**{pick.symbol}** is trading at **${pick.price:,.2f}** right now."))
    for w in pick.why:
        theme.note("• " + w)
    theme.note("🗓️ " + pick.expiry_note)
    if pick.error and pick.candidate is None:
        st.warning(components._esc(pick.error + " Use 🎯 Find a trade to scan other expirations."))
    c = pick.candidate
    if c is not None:
        m = st.columns(4)
        m[0].metric("Credit (1 contract)", f"${c.credit:,.0f}")
        m[1].metric("Max loss", f"${c.max_loss:,.0f}")
        m[2].metric("Return on risk", f"{c.return_on_risk * 100:.0f}%",
                    help="The credit as a % of the worst case - the premium you earn per "
                         "dollar at risk.")
        m[3].metric("Short delta", f"{c.short_delta:.2f}",
                    help="Roughly the chance the short strike finishes in the money - "
                         "lower is safer.")
        st.markdown("**Leg-by-leg (how it looks in thinkorswim):**")
        st.dataframe(components.candidate_leg_detail(c), width="stretch", hide_index=True)
    _sop_block(pick.sop_notes)
    for w in pick.warnings:
        st.warning(components._esc(w))
    settlement = ("American-style stock options: the short call can be assigned early if it "
                  "goes in the money (you'd end up short 100 shares), most likely deep in the "
                  "money or right before an ex-dividend date. Your loss is still capped by the "
                  "long call above it." if pick.american else
                  "Cash-settled index: no shares ever change hands and no early assignment - "
                  "if it expires in the money you just settle the difference in cash.")
    _picks_risk_block(
        max_loss=(c.max_loss if c else None), bp=(c.buying_power if c else None),
        settings=settings,
        liquidity_line=_liquidity_line(pick.liquidity, pick.spread_pct, pick.open_interest),
        settlement=settlement, events=pick.events)
    _strategy_about(strategies[pick.strategy_key])
    if st.button(f"Set up {pick.strategy_name} on {pick.symbol} in 🎯 Find a trade ▸",
                 type="primary", key=f"picks_spread_build_{pick.symbol}"):
        st.session_state["build_strategy"] = pick.strategy_key
        st.session_state["build_underlyings"] = [pick.symbol]
        st.session_state["_prev_build_strategy"] = pick.strategy_key
        st.success("Loaded into **🎯 Find a trade** - open that tab to scan and check it.")


def _income_pick_detail(pick, strategies, settings, provider) -> None:
    import datetime as dt

    s = pick.snapshot
    components.render_premium_detail(s)

    st.markdown("**💵 Dividend:**")
    div_line = pick.dividend.note
    if pick.dividend.pays and pick.dividend.ex_div_date:
        when = pick.dividend.ex_div_date
        div_line += (f" Next ex-dividend: {when:%b %d}." if when >= dt.date.today()
                     else f" Last ex-dividend was {when:%b %d}.")
    if pick.dividend.pays:
        div_line += (" A dividend only lands in your pocket while you own the shares - "
                     "covered calls, or a put that assigned you.")
    theme.note(div_line)

    _sop_block(pick.sop_notes)
    for w in pick.warnings:
        st.warning(components._esc(w))

    extra = []
    if pick.strategy_key == "poor_mans_covered_call":
        extra.append("A PMCC's real risk is the long-dated call you buy - scan it in Find a trade "
                     "to see the actual dollars.")
    if pick.strategy_key.startswith("covered_call"):
        extra.append("Covered calls need 100 real shares per contract - the worst case is "
                     "the shares themselves falling.")
    _picks_risk_block(
        max_loss=pick.bp_required, bp=pick.bp_required, settings=settings,
        liquidity_line=_liquidity_line(s.liquidity, s.spread_pct, s.open_interest),
        settlement="American-style options: assignment before expiration is possible - "
                   "most likely deep in the money or right before an ex-dividend date.",
        events=pick.events, extra=extra)

    with st.expander(f"🔬 Full strategy read for {s.symbol} (trend + technicals + playbook)"):
        try:
            components.render_advice(_compute_advice(s.symbol, pick.kind, provider, settings))
        except Exception:
            theme.note("Couldn't load the full read right now - the 🔬 Analyze tab has it.")

    _strategy_about(strategies[pick.strategy_key])
    label = components._STRATEGY_SHORT.get(pick.strategy_key, pick.strategy_key)
    if st.button(f"Set up {label} on {s.symbol} in 🎯 Find a trade ▸",
                 type="primary", key="picks_inc_to_build"):
        st.session_state["build_strategy"] = pick.strategy_key
        st.session_state["build_underlyings"] = [s.symbol]
        st.session_state["_prev_build_strategy"] = pick.strategy_key
        st.success("Loaded into **🎯 Find a trade** - open that tab to scan and check it.")


# ------------------------------------ Picks, mode 2: compare a list you type
def _premium_compare(settings, provider) -> None:
    """Compare the premium on names SHE picks, on either side of the trade.

    This is the old Premium finder. It only ever priced the put, so "which of
    these pays a good covered call" could not be answered from the table - the
    call was buried in each name's detail panel. The side toggle fixes that, and
    the call side reuses the covered-call pick used by the scan above so the two
    can never disagree.
    """
    from src.data import stock_universe
    from src.engine import recommender

    side = st.radio(
        "Compare the premium on:",
        ["🔻 Puts - what I collect for agreeing to buy the shares (cash secured put)",
         "🔺 Calls - what I collect for renting out shares I own (covered call)"],
        key="premium_side")
    calls = side.startswith("🔺")

    if calls:
        lo, _t, hi = recommender.cc_dte_window()
        theme.note(f"For each name: the call your SOP would sell (~0.30 delta, {lo}-{hi} days "
                   "out), what it pays, and that as a yield on the cost of the 100 shares - "
                   "for the month and for a year. **Sort by any column to find the best deal**, "
                   "and read the Verdict before the yield: the fattest premium is usually the "
                   "one the market expects to move most.")
    else:
        theme.note("For each name: the put your SOP would sell (~0.30 delta, about a month "
                   "out), what it pays, and that as a yield on the cash you set aside - for "
                   "the month and for a year. **Sort by any column to find the best deal**, "
                   "and read the Verdict before the yield: the fattest premium is usually the "
                   "one the market expects to move most.")

    if not provider.is_real:
        st.info("Comparing premium needs real market data - connect to the internet first.")
        return

    _stale_quotes_note()

    etfs = settings["underlyings"]["us_style"]
    options = list(dict.fromkeys(etfs + stock_universe.FEATURED + stock_universe.all_stocks()))
    picks = st.multiselect(
        "Names to compare", options,
        default=[s for s in ["AAPL", "NVDA", "MSFT", "SPY", "QQQ"] if s in options],
        max_selections=20, key="premium_picks", accept_new_options=True,
        help="Add as many as you like - the table compares them all at once. "
             "Not on the list? Type the ticker and click the Add: line.")
    picks = [p.strip().upper() for p in picks if p and p.strip()]

    monthly_bp = float(settings["risk_limits"]["monthly_bp_limit"])
    state_key = "premium_calls" if calls else "premium_snaps"
    if st.button("Compare", type="primary", key="premium_scan"):
        if not picks:
            st.warning("Pick at least one name.")
        else:
            monthly = recommender.monthly_target()
            out = []
            bar = st.progress(0.0, text="Reading option premiums...")
            for i, sym in enumerate(picks):
                try:
                    if calls:
                        snap = _covered_call_snapshot(provider, sym, monthly, monthly_bp)
                        if snap is not None:
                            kind = "etf" if sym in etfs else "stock"
                            out.append(recommender.build_covered_call_pick(
                                snap, kind, provider.get_raw_info(sym), monthly,
                                vix=None))
                    else:
                        out.append(provider.get_premium_snapshot(
                            sym, monthly_bp=monthly_bp))
                except Exception as e:
                    if not calls:
                        from src.data.premium_finder import PremiumSnapshot
                        out.append(PremiumSnapshot(symbol=sym, error=str(e)[:40]))
                bar.progress((i + 1) / len(picks), text=f"Checked {sym} ({i+1}/{len(picks)})")
            bar.empty()
            if calls:
                st.session_state[state_key] = recommender.rank_covered_call_picks(out)
            else:
                from src.data import premium_finder
                st.session_state[state_key] = premium_finder.rank(out)

    rows = st.session_state.get(state_key)
    if not rows:
        theme.note("Press **Compare** to build the table.")
        return

    if calls:
        _premium_calls_table(rows)
    else:
        _premium_puts_table(rows)


def _premium_calls_table(picks) -> None:
    """The covered-call comparison - the same table and numbers the scan uses."""
    st.dataframe(components.covered_call_dataframe(picks), width="stretch",
                 hide_index=True, column_config=components.covered_call_column_config())
    with st.expander("🎓 How to read this and pick a good one"):
        st.markdown(components._esc(
            "- **Verdict** weighs everything else. Read it first: a big yield on a ❌ is "
            "big for a reason.\n"
            "- **Yield/mo %** is the honest way to compare names - the credit as a share of "
            "what the 100 shares cost, so a $900 stock and a $60 stock line up fairly. "
            "**Yield/yr %** is the same rate repeated for a year, for scale, not a promise.\n"
            "- **Delta** is roughly the chance the shares get called away. Around 0.30 is "
            "your SOP.\n"
            "- **Quality** matters more here than anywhere: a covered call means you OWN "
            "the shares, so you keep whatever the premium does not cover.\n"
            "- **A good deal** is a decent yield on a name you would be happy holding - not "
            "the biggest number in the column."))
    st.divider()
    chosen = st.selectbox("See the full plan for one name", [p.symbol for p in picks],
                          key="premium_call_detail")
    pick = next(p for p in picks if p.symbol == chosen)
    with st.container(border=True):
        components.render_covered_call_detail(pick)
        if st.button(f"Analyze {chosen} in depth ▸", key="prem_call_to_analyze"):
            st.session_state["analyze_sym"] = chosen
            st.success(f"Loaded {chosen} into **🔬 Analyze** - open that tab for its "
                       f"chart, fundamentals and every research tool.")


def _premium_puts_table(snaps) -> None:
    """The cash-secured-put comparison."""
    st.dataframe(components.premium_dataframe(snaps), width="stretch", hide_index=True,
                 column_config=components.premium_column_config())
    with st.expander("🎓 How to read this and pick a good one"):
        st.markdown(components._esc(
            "- **Verdict** is the bottom-line call: ✅ good to sell / ⚠️ okay / ❌ skip. It "
            "already weighs everything below, so if you read one column, read this.\n"
            "- **Quality** - the company's A-F grade (ETFs are baskets, shown as ETF). It "
            "matters because if the put is assigned you end up owning the shares.\n"
            "- **Income $/mo** and **Yield %/mo** - the cash you collect, and that as a % of "
            "the money you set aside. The yield is the fair way to compare names of "
            "different prices; **Yield %/yr** is the same rate over a year, for scale.\n"
            "- **Premium deal** - is the premium a good deal for the RISK? **Rich** = you "
            "are paid more than this stock's usual swings would justify (good for you). "
            "**Thin** = it moves a lot but pays little (bad). **Fair** = normal.\n"
            "- **Watch out** flags a landmine: earnings before expiry, or options that are "
            "hard to trade.\n"
            "- **A good deal** is a rich or fair premium on a name you would be happy to "
            "own - not simply the highest yield on the list."))

    valid = [s for s in snaps if not s.error]
    if valid:
        st.divider()
        chosen = st.selectbox("See the full plan for one name", [s.symbol for s in valid],
                              key="premium_detail_sym")
        detail = next(s for s in valid if s.symbol == chosen)
        with st.container(border=True):
            components.render_premium_detail(detail)
            if st.button(f"Analyze {chosen} in depth ▸", key="prem_to_analyze"):
                st.session_state["analyze_sym"] = chosen
                st.success(f"Loaded {chosen} into **🔬 Analyze** - open that tab for its "
                           f"chart, fundamentals and every research tool.")


# ------------------------------------------------------------------ Analyze tab
def _tab_analyze(settings, provider, strategies) -> None:
    """Everything about ONE name, behind one symbol box.

    This was two tabs, "Analyze" and "Research", and six of the eight tools made
    you type the same ticker again for each one. Type it once here and every
    tool below reads it.
    """
    theme.section("Everything about one name, in one place", "Analyze")
    sym = _analyze_symbol_box(settings)
    theme.note("Type a ticker once - every tool below reads it. The list holds the indexes, the "
               "big ETFs and the S&P 500 / Nasdaq-100 stocks; for anything else (SOFI, HOOD...) "
               "type it and click the **Add:** line that appears. Indexes (SPX, NDX) have no "
               "company behind them, so the company tools stay empty for those.")

    # Short labels on purpose: the full names needed 1320px of tab bar and a
    # 1280-wide laptop gives about 1183, so "Options data" sat off-screen behind
    # a scroll arrow. Each tab restates its own full title as a heading anyway.
    (t_over, t_leaps, t_season, t_analyst, t_screen, t_calc, t_opts) = st.tabs(
        ["📋 Overview", "🔭 LEAPS", "📅 Seasons", "🎯 Analysts",
         "✅ Screener", "🧮 Fair price", "⛓️ Options"])

    with t_over:
        _analyze_overview(sym, settings, provider, strategies)
    # Each tool guards itself rather than the whole tab gating on a symbol: the
    # LEAPS Finder's scan mode hunts for candidates and needs no symbol at all,
    # and gating would have made it unreachable until you picked one.
    with t_leaps:
        _research_leaps(settings, provider, sym)
    with t_season:
        _research_seasonality(settings, provider, sym)
    with t_analyst:
        _research_analyst(settings, provider, sym)
    with t_screen:
        _research_analyzer(settings, provider, sym)
    with t_calc:
        _research_calculator(settings, provider, sym)
    with t_opts:
        _research_options(settings, provider, sym)


def _analyze_overview(sym, settings, provider, strategies) -> None:
    if not sym:
        theme.note("Pick an index, ETF, or stock above for its full picture and the strategy "
                   "that fits it.")
        return
    if not provider.is_real and _classify(sym, settings) != "index":
        st.info("The deep dive needs real market data - connect to the internet first.")
        return

    kind = _classify(sym, settings)
    with st.container(border=True):
        _symbol_research(sym, provider, settings, key_prefix="analyze")
        advice = _compute_advice(sym, kind, provider, settings)
        st.divider()
        components.render_advice(advice)
        if advice.primary:
            if st.button(f"Find this: {advice.primary.name} on {sym} ▸", type="primary",
                         key="analyze_to_build"):
                st.session_state["build_strategy"] = advice.primary.key
                st.session_state["build_underlyings"] = [sym]
                st.session_state["_prev_build_strategy"] = advice.primary.key
                st.success("Loaded into **🎯 Find a trade** - open that tab to scan and check it.")


def _symbol_research(sym, provider, settings, key_prefix) -> None:
    kind = _classify(sym, settings)
    if kind == "index":
        price, chg = provider.get_price_change(sym) if provider.is_real else (None, None)
        c1, c2 = st.columns([1, 3])
        c1.metric(sym, f"{price:,.0f}" if price else "n/a",
                  f"{chg:+.2f}%" if chg is not None else None)
        tv = provider.get_tradingview(sym, is_index=True) if provider.is_real else {}
        with c2:
            if tv:
                components.render_tv_ratings(tv, title=f"TradingView on {sym}")
        theme.note("Indexes have no earnings or fundamentals - the Market tab is your main guide "
                   "here.")
    else:
        if not provider.is_real:
            st.info("The full name check needs real data.")
            return
        _stock_overview_block(sym, provider, key_prefix=key_prefix)

    st.divider()
    tv_chart.render(sym, provider, kind=kind, key_prefix=key_prefix)


# --------------------------------- the research tools, now inside Analyze
def _research_ready(provider, sym, what: str, settings=None,
                    company_only: bool = False) -> bool:
    """Every tool needs real data and the symbol picked at the top of Analyze.
    Says which one is missing instead of rendering an empty panel.

    company_only: this tool asks a question about a COMPANY. On an index there
    is no company to ask about, so say that rather than "no data found" - which
    reads like a broken lookup and invites her to retry something that will
    never work.
    """
    if not provider.is_real:
        st.info(f"{what} needs real market data - connect to the internet first.")
        return False
    if not sym:
        theme.note(f"Pick a symbol at the top of this tab and {what.lower()} appears here.")
        return False
    if company_only and settings is not None and _classify(sym, settings) == "index":
        st.info(f"{sym} is an index - a basket of hundreds of companies, with no single "
                f"company behind it - so {what.lower()} does not exist for it. This tool "
                f"is for stocks and ETFs. For an index, the 📊 Market tab is your read.")
        return False
    return True


# ---------- LEAPS Finder ----------
LEAPS_UNIVERSES = {
    "Featured names (fast)": "featured",
    "Nasdaq 100": "nasdaq100",
    "S&P 500 (slowest, most thorough)": "sp500",
}


def _research_leaps(settings, provider, sym) -> None:
    from src.research import leaps

    st.markdown("#### Is a long-dated call worth buying here?")
    theme.note(
        "This scores a name against your **LEAPS Call** SOP - the one strategy in your book "
        "that BUYS an option instead of selling one. Far less cash up front than the shares "
        "and you can never lose more than you paid, but you are paying for time: if the "
        "stock just sits still you lose all of it, where shares that go nowhere cost you "
        "nothing. Everything below weighs that honestly.")
    _leaps_sop_reminder(provider)

    mode = st.radio("How do you want to look?",
                    ["Check one stock", "Scan for candidates"],
                    horizontal=True, key="leaps_mode")

    if mode == "Check one stock":
        floor = leaps.target_delta()
        target_delta = st.select_slider(
            "How deep in the money?", options=[0.70, 0.75, 0.80, 0.85, 0.90],
            value=0.70, key="leaps_delta",
            format_func=lambda d: f"{d:.2f} delta")
        theme.note(
            f"Delta is how much of the stock's move the option captures. Your SOP's floor is "
            f"**{floor:.2f}** - at that level most of the price is real (intrinsic) value, so "
            "it tracks the shares closely and decays slowly. Deeper is fine. Shallower is the "
            "mistake, and out-of-the-money is banned outright: those are nearly all time "
            "value, so they swing hard and expire worthless if the stock only drifts.")
        if not _research_ready(provider, sym, "The LEAPS check"):
            return
        with st.spinner(f"Pricing {sym} LEAPS and checking its history..."):
            candidate = provider.get_leaps_candidate(sym, target_delta)
        if candidate is None:
            st.info(f"No long-dated option data came back for {sym}.")
            return
        st.divider()
        research.render_leaps_detail(candidate)
        return

    universe_label = st.selectbox("Universe", list(LEAPS_UNIVERSES), key="leaps_universe")
    universe_key = LEAPS_UNIVERSES[universe_label]

    with st.expander("Scan criteria", expanded=True):
        col1, col2, col3 = st.columns(3)
        min_cap = col1.number_input("Smallest company ($B)", 0.0, 5000.0, 10.0, 5.0,
                                    key="leaps_cap")
        min_vol = col2.number_input("Least daily volume (M shares)", 0.0, 100.0, 1.0, 0.5,
                                    key="leaps_vol")
        max_off = col3.number_input("Most it can be below its 52-week high (%)",
                                    0.0, 90.0, 25.0, 5.0, key="leaps_offhigh")
        col4, col5 = st.columns(2)
        above200 = col4.checkbox("Only stocks above their 200-day average", value=True,
                                 key="leaps_200")
        above50 = col5.checkbox("Only stocks above their 50-day average", value=False,
                                key="leaps_50")
        stoch_lo, stoch_hi = st.slider("Weekly stochastic between", 0, 100, (20, 90),
                                       key="leaps_stoch")
        theme.note(
            "That last one is the filter the paid tools lean on hardest. Left wide it "
            "barely excludes anything, which is rather the point - momentum position is "
            "one input here, not the thesis.")

    filters = leaps.Filters(
        min_market_cap_b=min_cap, min_avg_volume_m=min_vol, max_pct_off_high=max_off,
        require_above_200dma=above200, require_above_50dma=above50,
        stoch_min=float(stoch_lo), stoch_max=float(stoch_hi),
    )

    if st.button("Scan the universe ▸", type="primary", key="leaps_scan_btn"):
        st.session_state["leaps_scanned"] = universe_key
    if st.session_state.get("leaps_scanned") != universe_key:
        theme.note("Press scan to score the universe. Results cache for six hours.")
        return

    symbols = _leaps_universe(universe_key, settings)
    with st.spinner(f"Scoring {len(symbols)} names..."):
        scanned = provider.get_leaps_scan(universe_key, symbols)
    if not scanned:
        st.warning("The market data provider did not return history for that scan. "
                   "Press scan again in a moment - free data throttles from time to time.")
        return

    ranked = leaps.rank(scanned, filters)
    st.markdown(f"**{len(ranked)} of {len(scanned)} names pass your criteria**")
    theme.note(
        "This is the chart-and-quality half of the score only. Pricing an actual contract "
        "for hundreds of names would take many minutes, so pick one below and we will "
        "fetch its real chain, work out what it costs, and check the odds against its own "
        "history.")
    if not ranked:
        st.info("Nothing passed. Loosen a filter above.")
        return

    st.dataframe(research.leaps_frame(ranked[:60]), hide_index=True,
                 column_config=research.leaps_columns(), width="stretch")

    picked = st.selectbox("Price the contract for", [c.symbol for c in ranked[:60]],
                          key="leaps_pick")
    if picked and st.button(f"Score {picked} in full ▸", key="leaps_full_btn"):
        with st.spinner(f"Pricing {picked} LEAPS..."):
            candidate = provider.get_leaps_candidate(picked, leaps.target_delta())
        if candidate is None:
            st.info(f"No long-dated option data came back for {picked}.")
        else:
            st.divider()
            research.render_leaps_detail(candidate)


def _leaps_sop_reminder(provider) -> None:
    """The two SOP rules this tab cannot score from price history alone.

    The VIX gate is the one that was missing entirely. It is a hard "do not
    enter" in her SOP, it has nothing to do with the individual stock, and a
    tab that scored a name 88/100 while the market sat at VIX 12 was quietly
    contradicting the strategy page. It is checked here, once, up front.
    """
    from src.research import leaps

    floor = leaps.vix_min()
    vix = None
    try:
        vix = provider.get_market_context(MARKET_READ_SYMBOL).vix
    except Exception:
        pass

    if vix is None:
        theme.note(f"**Before you buy:** your SOP does not enter a LEAPS call while VIX is "
                   f"below **{floor:.0f}** - a calm market means nothing is on sale and any "
                   "shock lands while you hold. Today's VIX could not be read; check it on "
                   "the Market tab.")
        return

    # Enough decimals that the reading cannot look identical to the floor it
    # missed. At VIX 14.84 the rounded version read "VIX is 15 - below your 15
    # floor", which is a correct answer that looks like a bug. Same principle as
    # rules._tell_apart does for a delta sitting just over its limit.
    shown = f"{vix:.0f}" if round(vix) != round(floor) else f"{vix:.2f}"
    if vix >= floor:
        st.markdown(theme.chip(f"✅  VIX is {shown} - above your {floor:.0f} floor",
                               "green"), unsafe_allow_html=True)
    else:
        st.markdown(theme.chip(f"🛑  VIX is {shown} - below your {floor:.0f} floor",
                               "red"), unsafe_allow_html=True)
        theme.note(
            f"Your SOP does not open a LEAPS call below VIX {floor:.0f}. Under that the "
            "market is complacent, nothing has sold off, and you would be buying near the "
            "highs with any bad news still ahead of you. A name can score well below and "
            "still be a wait - the score reads the stock, this reads the market.")
    theme.note("The other rule no chart can check for you: the pullback must be the MARKET "
               "falling, **never** bad earnings or cut guidance. A market dip recovers with "
               "the market; a broken story may not come back at all.")


def _leaps_universe(key: str, settings) -> list:
    from src.data import stock_universe
    if key == "nasdaq100":
        return stock_universe.nasdaq100()
    if key == "sp500":
        return stock_universe.sp500()
    return stock_universe.FEATURED


# ---------- Seasonality ----------
def _research_seasonality(settings, provider, sym) -> None:
    st.markdown("#### Does this stock have months it likes?")
    theme.note(
        "Total returns with dividends reinvested, month by month, for as far back as the "
        "data goes. Useful as a tiebreaker on timing. Never a reason to trade on its own - "
        "a month that was green 16 years out of 20 is still not a promise about this year.")
    years = st.select_slider("How far back?", options=[5, 10, 15, 20, 25],
                             value=20, key="season_years",
                             format_func=lambda y: f"{y} years")
    if not _research_ready(provider, sym, "Seasonality"):
        return
    with st.spinner(f"Loading {sym} history..."):
        data = provider.get_seasonality(sym, years)
    if data is None or not data.months:
        st.info(f"No usable price history came back for {sym}.")
        return
    st.divider()
    research.render_seasonality(data)


# ---------- Analyst ----------
def _research_analyst(settings, provider, sym) -> None:
    st.markdown("#### What Wall Street says, and whether it has ever happened")
    theme.note(
        "Consensus ratings and price targets, plus the check almost nobody runs: how often "
        "this stock has actually gained that much in a year. Targets are twelve-month "
        "opinions that cluster optimistic - worth reading as sentiment, not forecast.")
    if not _research_ready(provider, sym, "Analyst coverage", settings,
                           company_only=True):
        return
    with st.spinner(f"Loading analyst coverage for {sym}..."):
        view = provider.get_analyst_view(sym)
    if view is None:
        st.info(f"No analyst data came back for {sym}.")
        return
    st.divider()
    research.render_analyst(view)


# ---------- Instant Analyzer ----------
def _research_analyzer(settings, provider, sym) -> None:
    import pandas as pd

    from src.research import criteria

    st.markdown("#### Your rules, applied to any stock")
    theme.note(
        "Decide what a good company looks like to you, then grade any stock against "
        "exactly that. Misses show how far off they were - failing by a hair is a very "
        "different thing from failing by a mile, and a plain red X hides that.")

    preset_name = st.selectbox("Start from", list(criteria.PRESETS), key="crit_preset")
    theme.note(criteria.PRESETS[preset_name]["note"])

    if st.session_state.get("_crit_loaded") != preset_name:
        st.session_state["_crit_loaded"] = preset_name
        st.session_state["_crit_rules"] = [
            {"Measure": criteria.FIELDS[c.field]["label"], "Test": c.op, "Value": c.value}
            for c in criteria.preset(preset_name)]

    label_to_field = {spec["label"]: key for key, spec in criteria.FIELDS.items()}
    edited = st.data_editor(
        pd.DataFrame(st.session_state["_crit_rules"]),
        num_rows="dynamic", hide_index=True, width="stretch", key="crit_editor",
        column_config={
            "Measure": st.column_config.SelectboxColumn(
                "Measure", options=list(label_to_field), required=True),
            "Test": st.column_config.SelectboxColumn(
                "Test", options=[">=", "<=", ">", "<"], required=True),
            "Value": st.column_config.NumberColumn("Value", required=True),
        })

    rules = []
    for _i, row in edited.iterrows():
        field = label_to_field.get(row.get("Measure"))
        if field and pd.notna(row.get("Value")):
            rules.append(criteria.Criterion(field=field, op=row.get("Test") or ">=",
                                            value=float(row["Value"])))
    if not rules:
        st.info("Add at least one rule above.")
        return

    with st.expander("What each measure means"):
        for field in {r.field for r in rules}:
            spec = criteria.FIELDS[field]
            st.markdown(f"- **{spec['label']}** - {spec['help']}")

    if not _research_ready(provider, sym, "The grade for a stock", settings,
                           company_only=True):
        return
    with st.spinner(f"Checking {sym} against your rules..."):
        info = provider.get_raw_info(sym)
        extras = _criteria_extras(sym, provider)
        result = criteria.evaluate(sym, rules, info, extras)
    st.divider()
    research.render_criteria_result(result)


def _criteria_extras(sym, provider) -> dict:
    """The rule fields that come from price history rather than fundamentals."""
    from src.research import leaps
    closes = provider.get_long_closes(sym)
    if not closes:
        return {}
    price = closes[-1]
    window = closes[-leaps.TRADING_DAYS_YEAR:] if len(closes) >= leaps.TRADING_DAYS_YEAR \
        else closes
    high = max(window)
    sma200 = leaps.sma(closes, 200)
    extras = {
        "pct_off_high": abs((price / high - 1) * 100) if high else None,
        "rsi": leaps.rsi(closes),
        "above_200dma": (1.0 if sma200 and price > sma200 else 0.0) if sma200 else None,
        # Not a rule field - the dividend yield needs it to divide the dollar
        # rate by, which beats guessing at Yahoo's yield units.
        "price": price,
    }
    if len(closes) > leaps.TRADING_DAYS_YEAR:
        extras["year_return"] = (price / closes[-leaps.TRADING_DAYS_YEAR] - 1) * 100
    return extras


# ---------- Price calculator ----------
def _research_calculator(settings, provider, sym) -> None:
    from src.research import fair_value

    st.markdown("#### What should I pay to earn the return I want?")
    theme.note(
        "Estimate what the company earns in a few years, decide what the market pays for "
        "those earnings, then discount it back at the return you insist on. Whatever comes "
        "out is the most you can pay. The answer rests entirely on two guesses, so the grid "
        "at the bottom shows what happens when you are wrong about them.")

    if not _research_ready(provider, sym, "The price calculator", settings,
                           company_only=True):
        return

    info = provider.get_raw_info(sym)
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    eps_default = float(info.get("trailingEps") or 0.0)
    pe_default = float(info.get("trailingPE") or 18.0)
    growth_default = float((info.get("earningsGrowth") or info.get("revenueGrowth") or 0.08)
                           * 100)

    if eps_default <= 0:
        st.warning(f"{sym} has no positive trailing earnings, so an earnings-based "
                   "calculation cannot say anything useful here. Judge it on sales or "
                   "cash flow instead.")

    col1, col2, col3 = st.columns(3)
    eps = col1.number_input("Earnings per share now ($)", value=round(eps_default, 2),
                            step=0.10, key="calc_eps")
    growth = col2.number_input("Growth a year (%)", value=round(min(growth_default, 30.0), 1),
                               step=1.0, key="calc_growth")
    years = col3.number_input("Years to hold", 1, 20, 5, key="calc_years")
    col4, col5, col6 = st.columns(3)
    exit_pe = col4.number_input("P/E at the end", value=round(min(pe_default, 40.0), 1),
                                step=1.0, key="calc_pe")
    required = col5.number_input("Return you want (%/yr)", value=12.0, step=1.0,
                                 key="calc_req")
    div = col6.number_input("Dividend yield (%)",
                            value=round(_dividend_pct(info), 2), step=0.25, key="calc_div")

    theme.note(
        "Sensible starting points: growth no higher than the company has actually managed, "
        "and an exit P/E at or below today's - assuming the market will pay MORE for it "
        "later is how these calculations flatter themselves.")

    inputs = fair_value.ValuationInputs(
        symbol=sym, eps=eps, growth_pct=growth, years=int(years), exit_pe=exit_pe,
        required_return_pct=required, dividend_yield_pct=div, current_price=price)
    result = fair_value.project(inputs)

    st.divider()
    research.render_valuation(result)

    st.markdown("**What if the two guesses are wrong?**")
    theme.note("Buy-below price at each combination of growth and exit P/E. If the answer "
               "only works in one corner of this grid, it is not much of an answer.")
    st.dataframe(research.sensitivity_frame(fair_value.sensitivity(inputs)),
                 width="stretch")


def _dividend_pct(info: dict) -> float:
    from src.research.leaps import dividend_yield_pct
    return dividend_yield_pct(info)


# ---------- Options data ----------
def _research_options(settings, provider, sym) -> None:
    st.markdown("#### What the option market is pricing")
    theme.note(
        "Implied volatility, the move options expect, and which way the money is leaning - "
        "plus the check most chain viewers skip: how often this stock has actually exceeded "
        "that expected move. That tells you whether options are dear or cheap, which "
        "matters whether you are buying or selling them.")
    dte = st.slider("Days to expiration to focus on", 7, 120, 30, key="opts_dte")
    if not _research_ready(provider, sym, "The options read"):
        return
    with st.spinner(f"Loading the {sym} option chain..."):
        view = provider.get_options_view(sym, dte)
    if view is None:
        st.info(f"No option chain came back for {sym}.")
        return
    st.divider()
    research.render_options_view(view)


# ------------------------------------------------------------------ Find a trade tab
def _strategy_about(strat) -> None:
    with st.expander(f"ℹ️ About {strat['name']}", expanded=False):
        st.markdown(f"**What it is:** {strat['plain_english']}")
        c = st.columns(2)
        c[0].markdown(f"**Market outlook:** {strat.get('market_outlook', '-')}")
        c[1].markdown(f"**Difficulty:** {strat.get('difficulty', '-')}")
        theme.note(f"👀 In thinkorswim: {strat.get('tos_hint', '')}")
        st.markdown(f"[📖 Read the full SOP in Notion]({strat['notion_url']})")
        if strat.get("warning"):
            st.warning(f"⚠️ {strat['warning']}")


def _underlying_prices(underlyings, provider) -> None:
    """Show what each selected underlying is trading at right now, so you can see
    where the price sits relative to the strikes you're about to sell."""
    if not underlyings:
        return
    if not provider.is_real:
        theme.note("Current prices need real market data (you're on sample data now).")
        return
    cols = st.columns(min(len(underlyings), 4))
    for i, u in enumerate(underlyings):
        col = cols[i % len(cols)]
        try:
            price, chg = provider.get_price_change(u)
        except Exception:
            price, chg = None, None
        col.metric(f"{u} now", f"${price:,.2f}" if price else "n/a",
                   f"{chg:+.2f}% today" if chg is not None else None)


def _width_sanity_note(names: tuple, width) -> None:
    """Say something when the width is far outside the SOP tier for these names.

    The tier is a distance in POINTS, so it means different things on indexes at
    different scales: 25 on SPX is a 0.33% spread, and the same 25 on XSP is
    3.3% and risks ten times what she expects to risk there.
    """
    if not width:
        return
    from src.engine.config_loader import default_spread_width
    for u in names:
        want = default_spread_width(u)
        if width > want * 2 + 1e-9:
            theme.note(
                f"**\\${width:,.0f} is wide for {u}.** Your SOP's range there is "
                f"about **\\${want:g} to \\${want * 2:g}** - that is what keeps "
                f"the max loss where you expect it. At \\${width:,.0f} you are "
                f"risking **\\${width * 100:,.0f}** per contract.")
            return


def _tab_build(settings, strategies, provider) -> None:
    from src.data import stock_universe
    keys = list(strategies.keys())
    # The strategy she trades most, not whichever happens to be first in
    # config/strategies.yaml. A handoff from Market, Picks or Analyze still
    # overrides it - those set build_strategy before this runs.
    st.session_state.setdefault("build_strategy",
                                default_strategy_key(settings, keys))

    top = st.columns([2, 2])
    strategy_key = top[0].selectbox("Strategy", keys, key="build_strategy",
                                    format_func=lambda k: strategies[k]["name"])
    strat = strategies[strategy_key]
    allowed = allowed_underlyings_for(strategy_key)
    # For credit spreads, list the cash-settled indexes first (safest - no assignment),
    # then ETFs and featured stocks. For US-style strategies, ETFs/stocks first.
    european = settings["underlyings"]["european_style"]
    pref = ((european if strat.get("family") == "credit_spread" else [])
            + settings["underlyings"]["us_style"] + stock_universe.FEATURED)
    priority = [u for u in pref if u in allowed]
    ordered = priority + [u for u in allowed if u not in priority]
    default_u = ["SPX"] if "SPX" in allowed else ordered[:1]
    st.session_state.setdefault("build_underlyings", default_u)
    if st.session_state.get("_prev_build_strategy") != strategy_key:
        st.session_state["_prev_build_strategy"] = strategy_key
        # A hand-typed name is not in `ordered`, so keep anything the strategy's
        # option style still allows - otherwise switching strategy would silently
        # throw away a name the Analyze tab just handed over.
        valid = [u for u in st.session_state["build_underlyings"]
                 if u in ordered or underlying_fits_style(strategy_key, u)]
        st.session_state["build_underlyings"] = valid or default_u
    underlyings = top[1].multiselect("Underlying(s)", ordered, key="build_underlyings",
                                     accept_new_options=True,
                                     help="Type to search, or type any other ticker to add it. "
                                          "Pick more than one to scan together.")

    _underlying_prices(underlyings, provider)

    if strat.get("family") == "credit_spread":
        theme.note("ℹ️ Per your SOP, credit spreads run on **any liquid stock, ETF, or index**. "
                   "**Indexes** (SPX, NDX, RUT, XSP) are cash-settled with no assignment risk - "
                   "the cleanest choice. **Stocks/ETFs** can be assigned, so the app enters them "
                   "nearer 45 DTE and warns about earnings.")
    else:
        theme.note("Pick any **stock** or **ETF** you can own shares of - the list holds the big "
                   "ones, and for anything else type the ticker and click the **Add:** line. "
                   "Want the recommended play for a name? Use **Analyze**.")

    uses_width = strat.get("family") == "credit_spread"
    row = st.columns([1, 1] if uses_width else [1, 2])
    contracts = row[0].number_input("Contracts", min_value=1, max_value=50, value=1, step=1)
    width = None
    if uses_width:
        from src.engine.config_loader import default_spread_width
        # SOP width: individual stocks $5-10; indexes and ETFs $25-50. Read per
        # name from config so a smaller-scale index (XSP is a tenth of SPX) gets
        # the rule at ITS scale, not a spread ten times too wide. The narrowest
        # of the picked names wins - never default anyone into more risk.
        picked = tuple(sorted(underlyings)) or ("SPX",)
        default_width = min(default_spread_width(u) for u in picked)
        if st.session_state.get("_prev_width_names") != picked:
            st.session_state["_prev_width_names"] = picked
            st.session_state["build_width"] = default_width
        width = row[1].number_input(
            "Spread width ($)", min_value=1.0, max_value=200.0,
            step=1.0, key="build_width",
            help="How far the strike you BUY sits from the strike you SELL - it "
                 "sets your max loss. Your SOP: indexes and ETFs $25-50, "
                 "individual stocks $5-10. XSP is a tenth of SPX, so $5 there is "
                 "your $50 SPX trade at a tenth the size.")
        _width_sanity_note(picked, width)

    sig = (strategy_key, tuple(underlyings), int(contracts), width)
    if st.session_state.get("build_sig") != sig:
        st.session_state["build_sig"] = sig
        st.session_state.pop("build_candidates", None)
        st.session_state.pop("build_chosen", None)

    _strategy_about(strat)
    st.divider()
    # Scan only. The old "check a trade I built myself" mode asked her to hand-type
    # a delta, a mid price and a DTE for every leg of a trade she had usually
    # already placed - which is what Quick Log in 📒 My trades does from her real
    # fill, in a fraction of the typing. Two forms for one job; this is the one
    # that could not know the price she actually got.
    _build_scan(strategy_key, strat, underlyings, provider, contracts, width, settings)


def _spread_event_warnings(underlyings, provider, dte_window=45) -> list:
    """Your SOP: no binary events (earnings / Fed) during a credit-spread trade."""
    import datetime as dt

    from src.engine import config_loader
    notes = []
    today = dt.date.today()
    if provider.is_real:
        for u in underlyings:
            if config_loader.underlying_kind(u) != "stock":
                continue   # ETFs/indexes have no company earnings
            earn = provider.get_earnings_info(u).get("earnings_date")
            if earn and today <= earn <= today + dt.timedelta(days=dte_window):
                notes.append(
                    f"⚠️ **{u}** reports earnings on {earn:%b %d} (in {(earn - today).days} days) "
                    "- inside your trade window. Your SOP says don't sell premium through "
                    "earnings: pick an expiration before it, or skip this name.")
    for e in provider.get_macro_events(trade_dte=dte_window):
        if e.kind == "fomc" and e.in_window:
            notes.append(
                f"⚠️ **{e.label}** is {_days_phrase(e.days_away)} - inside your trade window. "
                "Big-move event; trade after it, or keep size small.")
            break
    return notes


def _spread_width(cand) -> float:
    """The narrowest gap between adjacent strikes = the real spread width achieved."""
    strikes = sorted({leg.strike for leg in cand.trade.legs})
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b - a > 0]
    return min(gaps) if gaps else 0.0


def _build_scan(key, strat, underlyings, provider, contracts, width, settings) -> None:
    if not scanner.can_scan(key):
        # Unreachable today - every one of the 8 strategies is in a scannable
        # family. It stays as a guard for a 9th, but it must not point at the
        # "check a trade I built myself" form, which no longer exists: Quick Log
        # replaced it, and it reads the details off her real fill instead of
        # asking her to hand-type a delta and a mid price for every leg.
        st.info("The scanner does not cover this strategy yet. Place it in thinkorswim, then "
                "record it with **➕ Quick Log** in 📒 My trades - it checks the trade against "
                "your SOP and starts tracking your exit rules for it.")
        return
    if not underlyings:
        st.warning("Pick at least one underlying above.")
        return

    if strat.get("family") == "credit_spread":
        for _msg in _spread_event_warnings(underlyings, provider):
            st.warning(_msg)

    existing_bp = st.number_input(
        "Buying power already used this month ($)", min_value=0.0,
        value=float(st.session_state.get("month_bp_used", 0.0)), step=1000.0,
        help="Auto-filled from trades you opened this month in My trades - adjust if you "
             "also have positions the app doesn't know about.")
    is_pmcc = strat.get("family") == "diagonal"
    is_long_call = strat.get("family") == "long_call"
    want_financing_put = False
    put_ratio = 1
    if is_long_call:
        theme.note(f"Shows up to 10 {strat['name']} setups - one per expiration a year or "
                   "more out, each at the 0.70-0.80 delta your SOP calls for. This one BUYS "
                   "an option, so every setup is a debit: the money leaves your account and "
                   "the whole of it is at risk.")
        fp_cfg = strat.get("financing_put") or {}
        want_financing_put = st.checkbox(
            f"Also sell a {fp_cfg.get('delta_min', 0.20):.2f}-{fp_cfg.get('delta_max', 0.30):.2f} "
            "delta put to help pay for the call",
            value=bool(fp_cfg.get("enabled", False)), key="build_financing_put",
            help="Optional variant (a risk reversal). Same expiration as the call. The credit "
                 "cuts what you pay - but you must hold the strike x 100 in cash, and your "
                 "worst case stops being 'the premium I paid'.")
        if want_financing_put:
            put_ratio = int(st.radio(
                "How many puts per call?",
                options=[1, 2], horizontal=True, key="build_put_ratio",
                format_func=lambda n: ("1 put (matched)" if n == 1
                                       else "2 puts (advanced - uncovered second put)")))
            theme.note(
                f"**Financing put ON.** Each setup below now has two legs: the call you buy "
                f"(**+1**) and {'a put' if put_ratio == 1 else 'two puts'} you sell "
                f"(**-{put_ratio}**) at the same expiration. Read the capital line, not just "
                "the debit - the put's collateral is usually several times the cost of the "
                "call, so these setups tie up far more money than the same call on its own. "
                "Only worth it on a name you would be happy to own "
                f"{100 * put_ratio} shares of at the put's strike.")
            if put_ratio == 2:
                st.warning(
                    "**The second put is uncovered.** The call balances the first one and "
                    "does nothing for the second, so below the put strike this loses at "
                    "twice the rate and assignment arrives as 200 shares. It also doubles "
                    "the collateral - on a $300 stock that is over $50,000, your whole "
                    "monthly buying power, for one trade. Check the buying-power line on "
                    "every setup before you like the look of the cheaper debit.")
    else:
        theme.note(f"Shows up to 10 {strat['name']} setups - one per expiration across 21-44 days, "
                   "each at the delta your SOP calls for."
                   + (" (PMCC also picks a deep-in-the-money LEAPS.)" if is_pmcc else ""))

    if st.button("🔎 Scan the market now", type="primary"):
        from yfinance.exceptions import YFRateLimitError
        found = []
        rate_limited = []
        bar = st.progress(0.0, text="Reading option chains...")
        for i, u in enumerate(underlyings):
            try:
                # Only fetch the expirations this strategy actually needs (not a wide
                # default window) - far fewer requests to Yahoo per scan, so a scan is
                # both faster and much less likely to trip their rate limit.
                lo, hi = scanner.strategy_dte_window(strat, u)
                chain = provider.get_chain(u, dte_min=max(lo - 7, 0), dte_max=hi + 7)
                # A LEAPS long call lives entirely in the far-dated chain, so it
                # needs one aimed past a year - not PMCC's ~7-month default,
                # which would come back with nothing at 365+ DTE.
                if is_long_call:
                    leaps = provider.get_leaps_chain(u, target_dte=420)
                elif is_pmcc:
                    leaps = provider.get_leaps_chain(u)
                else:
                    leaps = None
                found.extend(scanner.scan_setups(key, chain, width=width,
                                                 contracts=int(contracts), max_setups=10,
                                                 leaps_chain=leaps,
                                                 financing_put=want_financing_put,
                                                 put_ratio=put_ratio))
            except YFRateLimitError:
                rate_limited.append(u)
            except Exception as e:
                st.error(f"{u}: {e}")
            bar.progress((i + 1) / len(underlyings), text=f"Scanned {u} ({i+1}/{len(underlyings)})")
        bar.empty()
        if rate_limited:
            st.warning(
                f"⏳ A data source was briefly rate-limited for: **{', '.join(rate_limited)}**. "
                "This usually clears in a minute or two - wait a moment and scan again. "
                "(The app normally uses CBOE's free option data, which rarely does this.)")
        # Closest to the SOP's preferred days-to-expiration first, so trade #1
        # (what the picker below opens on) is the best-timed setup, not the
        # shortest-dated one that has to be closed two days after entry.
        found = scanner.sort_by_dte_fit(found, strat)
        st.session_state["build_candidates"] = found
        st.session_state.pop("build_chosen", None)
        # A fresh scan is a fresh list, so drop the old selection - otherwise
        # the picker holds an index into results that no longer exist.
        st.session_state.pop("build_pick", None)

    candidates = st.session_state.get("build_candidates", [])
    if not candidates:
        if "build_candidates" in st.session_state:
            fam = strat.get("family")
            if fam == "covered_call":
                st.info("No setups found. A covered call needs 100 shares - for these names that "
                        "may exceed your monthly buying-power limit. Try a cheaper stock or a PMCC.")
            elif is_pmcc:
                st.info("No setups found. PMCC needs long-dated LEAPS, which some names lack "
                        "(and demo mode has none). Try a large, liquid stock on real data.")
            else:
                st.info("No setups found for these names right now.")
        else:
            theme.note("Press **Scan the market now** for a short list of the best setups.")
        return

    scanned_dtes = sorted({c.dte for c in candidates if c.dte is not None})
    st.success(f"Found {len(candidates)} setup(s) at your SOP delta, across "
               f"{', '.join(str(d) for d in scanned_dtes)} days to expiration.")

    # Warn when the strikes are too coarse to honor the requested width (e.g. NDX
    # far out of the money), so the bigger max loss is never a surprise.
    if width and strat.get("family") == "credit_spread":
        got = min((w for c in candidates if (w := _spread_width(c)) > 0), default=0.0)
        if got and got > width * 1.5:
            names = ", ".join(sorted({c.trade.underlying for c in candidates}))
            st.warning(
                f"⚠️ You asked for a **${width:.0f}-wide** spread, but these came out "
                f"**~${got:.0f} wide** - so the max loss is larger than you intended. "
                f"**{names}**'s option strikes are spaced that far apart where your short leg "
                "sits (far out of the money). For tight ${:.0f}-wide spreads use **SPX**, **XSP**, "
                "or **RUT** (fine 1-5 point strikes); NDX strikes get coarse far from the price."
                .format(width))

    st.dataframe(components.candidates_dataframe(candidates), width="stretch",
                 hide_index=True, column_config=components.candidates_column_config())

    # Why #1 is #1 - the list is no longer in date order, so say what it is in.
    from src.engine.config_loader import preferred_entry_dte
    best = candidates[0]
    target = preferred_entry_dte(strat, best.trade.underlying)
    if target and best.dte is not None:
        theme.note(f"Sorted by **how close each expiration is to your {target}-day target**, "
                   f"not by date - so **trade #1** ({best.dte} days on "
                   f"{best.trade.underlying}) is the best-timed one. Anything near the bottom "
                   f"of your window leaves almost no room before the 21-day time exit.")

    labels = components.candidate_labels(candidates)
    pick = st.selectbox("Look at one setup", range(len(candidates)),
                        format_func=lambda i: labels[i], key="build_pick")
    chosen = candidates[int(pick)]
    with st.container(border=True):
        if not chosen.fits_sop:
            st.warning(f"⚠️ {chosen.note}")
        st.markdown("**Leg-by-leg (build it this way in thinkorswim):**")
        st.dataframe(components.candidate_leg_detail(chosen), width="stretch", hide_index=True)
        _tos_ticket_block(chosen.trade, strat)
        st.markdown("**Your SOP checklist:**")
        report = validate_trade(chosen.trade, existing_month_bp=existing_bp)
        components.render_checklist(report)
        size = {"credit": chosen.credit, "max_loss": chosen.max_loss,
                "buying_power": chosen.buying_power}
        _risk_and_payoff(chosen.trade, strat, size, settings)
        _log_button(chosen.trade, strat["name"], size, report.passed, key="scan",
                    settings=settings)


def _tos_ticket_block(trade, strat) -> None:
    """The one-line order exactly as thinkorswim's Order Entry row shows it,
    in a copyable box - hold the phone next to TOS and check strike by strike."""
    from src.engine import tos_ticket
    line = tos_ticket.ticket_line(trade)
    if not line:
        return
    st.markdown("**The order line you should see in thinkorswim:**")
    st.code(line, language=None)
    extra = (" A covered call also needs your 100 shares per contract - this line is "
             "just the call you sell." if strat.get("family") == "covered_call" else "")
    theme.note("When you build the order in TOS, its Order Entry row should read like "
               "this. Check the strikes and the price against it before you send. "
               "The **date is estimated** from days-to-expiration - confirm it matches "
               "the expiration you picked, and expect to adjust the @ price a few cents "
               "to get filled." + extra)


# ------------------------------------------------------------------ shared pieces
def _stock_overview_block(sym, provider, key_prefix="setup"):
    with st.spinner(f"Analyzing {sym}..."):
        analysis = provider.get_stock_analysis(sym)
        info = provider.get_raw_info(sym)
        _, change_pct = provider.get_price_change(sym)
        analysts = provider.get_analyst_ratings(sym)
        eps_history = provider.get_eps_history(sym)
        earn_info = provider.get_earnings_info(sym)
        tv = provider.get_tradingview(sym)
    if analysis is None:
        st.info(f"Could not analyze {sym} right now - try again in a moment.")
        return None, {}

    components.render_stock_overview(
        analysis, info,
        frame_loader=lambda period: provider.get_price_frame(sym, period),
        change_pct=change_pct, analysts=analysts, eps_history=eps_history,
        key_prefix=key_prefix)

    if analysis.suitable:
        st.success(f"👍 {analysis.summary}")
    elif not analysis.liquid:
        st.error(f"👎 {analysis.summary}")
    else:
        st.warning(f"🤔 {analysis.summary}")

    earnings = earn_info.get("earnings_date")
    ex_div = earn_info.get("ex_div_date")
    from src.data import market_events
    evs = [e for e in market_events.upcoming_events(
               horizon_days=120, trade_dte=35,
               earnings_date=earnings, ex_div_date=ex_div)
           if e.kind in ("earnings", "dividend")]
    if evs:
        st.markdown(f"**📅 {sym} dates to know:**")
        components.render_events(evs)
        eps = earn_info.get("eps_avg")
        if eps:
            theme.note(f"Analysts expect about \\${eps:.2f} earnings per share next report "
                       f"(range \\${earn_info.get('eps_low', eps):.2f}"
                       f" to \\${earn_info.get('eps_high', eps):.2f}).")

    with st.expander("🔬 Full checks: fundamentals, technicals, TradingView"):
        components.render_stock_analysis(analysis)
        if tv:
            st.divider()
            components.render_tv_ratings(tv)
    return analysis, earn_info


def _risk_and_payoff(trade, strat, size, settings) -> None:
    """The stop-and-look risk card + profit-zone picture, shown before logging."""
    from src.engine import payoff
    prof = payoff.profile(trade, strat)
    components.render_risk_card(
        trade, strat, size, payoff_profile=prof,
        bp_limit=float(settings["risk_limits"]["monthly_bp_limit"]))
    if prof is not None:
        st.markdown("**Your profit zone at expiration:**")
        components.render_payoff_chart(prof, current_price=trade.underlying_price)


def _log_button(trade, strategy_name, size, passed, key: str, settings=None) -> None:
    components.bp_effect_input(size, key)
    account = trades_account.choice(settings, key) if settings is not None else ""
    note = st.text_input("Note (optional)", key=f"note_{key}",
                         placeholder="e.g. VIX low, following the SOP")
    if st.button("Log this trade", key=f"log_{key}"):
        from src.logging_tools.trade_logger import log_trade
        dest, live, trade_id = log_trade(trade, strategy_name, size, passed, note,
                                         account=account)
        st.session_state.pop("trades_rows", None)   # My trades reloads fresh
        if live:
            st.success(f"Logged to your Google Sheet ✅ - now tracked in **📒 My trades**.  \n{dest}")
        else:
            st.success(f"Saved to the local log and tracked in **📒 My trades**.  \n{dest}")


# ------------------------------------------ settings (main tab + desktop sidebar)
def _data_mode_note(provider) -> None:
    text, tone = _mode_badge(provider)
    st.markdown(theme.chip(text, tone), unsafe_allow_html=True)
    if provider.mode == "demo":
        st.error(DEMO_WARNING)
    elif provider.mode == "yahoo":
        theme.note("Real market data, ~15 minutes delayed - fine for 21-45 day trades.")


def _plan_metrics(settings, per_row: int = 4) -> None:
    acct, tgt, risk = settings["account"], settings["targets"], settings["risk_limits"]
    vals = [("Capital", money(acct["starting_capital"])),
            ("Monthly goal", money(tgt["monthly"])),
            ("Weekly goal", money(tgt["weekly"])),
            ("BP limit", money(risk["monthly_bp_limit"]))]
    cols = st.columns(per_row)
    for i, (label, v) in enumerate(vals):
        cols[i % per_row].metric(label, v)


def _plan_editor(settings) -> None:
    """Set the four numbers that define the plan, from inside the app.

    They drive the goal bars, the pace read and the buying-power guardrail, so
    editing them by hand in a YAML file was the one part of her own plan she
    could not change without a text editor.
    """
    from src.engine import config_loader, plan_settings

    current = plan_settings.read(settings)

    with st.form("plan_form"):
        c1, c2 = st.columns(2)
        capital = c1.number_input(
            "Capital in the account ($)", min_value=0.0, step=1000.0,
            value=float(current["capital"]), format="%.0f",
            help="What the account holds. Everything shown as a percentage of "
                 "your account measures against this.")
        bp_limit = c2.number_input(
            "Monthly buying-power budget ($)", min_value=0.0, step=1000.0,
            value=float(current["bp_limit"]), format="%.0f",
            help="The most buying power you will commit across a whole month. "
                 "A cumulative budget - closing a trade early does not hand its "
                 "room back.")
        c3, c4 = st.columns(2)
        monthly = c3.number_input(
            "Monthly income goal ($)", min_value=0.0, step=100.0,
            value=float(current["monthly"]), format="%.0f",
            help="What the month's income report measures against.")
        auto = c4.checkbox(
            "Set the weekly goal from the monthly one", value=True,
            help="A month is 52/12 weeks, not 4. Your $3,500 and $808 already "
                 "sit on exactly that ratio.")
        weekly = st.number_input(
            "Weekly income goal ($)", min_value=0.0, step=10.0,
            value=float(plan_settings.weekly_from_monthly(monthly) if auto
                        else current["weekly"]),
            format="%.0f", disabled=auto,
            help="The same target at the rhythm you actually trade in - the "
                 "dashed line on the by-week chart.")
        submitted = st.form_submit_button("Save my plan", type="primary")

    if submitted:
        values = {"capital": capital, "monthly": monthly,
                  "weekly": (plan_settings.weekly_from_monthly(monthly)
                             if auto else weekly),
                  "bp_limit": bp_limit}
        try:
            plan_settings.save(values)
        except ValueError as e:
            st.error(str(e))
        else:
            config_loader.clear_cache()
            st.success(f"Saved. Goal **{money(values['monthly'])} a month** "
                       f"({money(values['weekly'])} a week) on "
                       f"{money(values['capital'])}, with a "
                       f"{money(values['bp_limit'])} monthly buying-power budget.")
            st.rerun()

    theme.note("These four numbers drive the goal bars in **📒 My trades**, the "
               "pace read on the income report, and the buying-power warning in "
               "**🎯 Find a trade**.")
    theme.note("**One caveat on the hosted app:** a saved plan lives in the app's "
               "own file, and the hosted version rebuilds that file whenever it "
               "restarts or updates - so a change made here can be lost. If you "
               "want new numbers to stick for good, tell me and I will put them "
               "in permanently.")


def _tab_settings(settings, provider) -> None:
    """The one home for connections, data status, and her plan numbers.

    These used to render here AND in a sidebar, so on a computer she saw two of
    every form at once - two "Connect your Google Sheet" boxes, each with its own
    half-filled text box. The sidebar is gone; this tab is the only copy.
    """
    theme.section("Your connections and your plan - all in one place", "Settings")
    _data_mode_note(provider)

    st.markdown("#### 🔗 Where your trades log")
    from src.logging_tools import webhook_logger
    if not webhook_logger.is_configured():
        st.warning("Trades are saving **only on this device** right now - they won't reach "
                   "your Google Sheet until it is connected below.")
    _connect_sheet_ui()

    st.markdown("#### 📡 Data sources")
    _connect_earnings_ui()
    _connect_schwab_ui(provider)

    st.divider()
    st.markdown("#### 🎯 Your goals and budget")
    _plan_metrics(settings)
    _plan_editor(settings)
    st.markdown(f"[📖 Open your Notion hub]({settings['notion']['hub_url']})")
    live = trades_account.live_from(settings)
    if live is None:
        theme.note("You are paper trading to learn the process. Follow the rules, not "
                   "the P&L. When you fund the account, set `account.live_from` in "
                   "`config/settings.yaml` to that date - from then on the income "
                   "report counts real money only.")
    else:
        theme.note(f"**Real money since {live.day} {live:%B} {live.year}.** Trades "
                   "opened before that "
                   "date stay in your log as practice history and never count as "
                   "income. Follow the rules, not the P&L - that does not change now "
                   "that the money is real, it matters more.")


def _connect_schwab_ui(provider) -> None:
    live = provider.mode == "schwab"
    label = "⚡ Schwab: connected ✅" if live else "⚡ Connect Schwab (real-time)"
    with st.expander(label, expanded=False):
        if live:
            st.success("You are on real-time Schwab data.")
            return
        theme.note("Right now you have real market data (~15 min delayed), which is fine for "
                   "your trades. To get true real-time from your own account (only works on "
                   "a computer, not the hosted app):")
        st.markdown(
            "1. Go to **developer.schwab.com** and sign in with your Schwab login.\n"
            "2. Create an app - choose **Trader API - Individual**.\n"
            "3. Set the callback URL to **https://127.0.0.1:8182**\n"
            "4. Wait for the app status to become **Ready for Use** (can take a few days).\n"
            "5. Copy `.env.example` to `.env` and paste in your **App Key** and **App Secret**.\n"
            "6. Run once in a terminal: `python -m src.data.schwab_client` (a browser opens to "
            "log in).\n"
            "7. Restart the app - this will switch to **LIVE** automatically.")
        theme.note("Your keys stay on your PC. Full details are in the README.")


def _connect_earnings_ui() -> None:
    """Paste a free Alpha Vantage key to pull years of earnings history (works on
    the hosted app, where Yahoo's earnings endpoint is blocked)."""
    from src.data import alphavantage_client as av
    connected = av.is_configured()
    label = "📈 Earnings history: connected ✅" if connected else "📈 Add earnings history (free)"
    with st.expander(label, expanded=False):
        theme.note("Gets years of expected-vs-delivered EPS for the Analyze tab. Yahoo blocks "
                   "this on the hosted app, so a free Alpha Vantage key fills it in.")
        current = av.get_key() or ""
        key = st.text_input("Alpha Vantage key", value=current, key="av_key",
                            type="password", placeholder="paste your key")
        if st.button("Save key", key="save_av"):
            if key.strip():
                av.set_key(key.strip())
                st.success("Saved. The earnings chart will now show years of history.")
            else:
                st.error("Paste your key first.")
        theme.note("Free key: alphavantage.co/support/#api-key. On the **hosted** app, also add "
                   "it under **Settings → Secrets** as:  alphavantage_key = \"YOUR_KEY\"")


def _connect_sheet_ui() -> None:
    from src.logging_tools import webhook_logger
    connected = webhook_logger.is_configured()
    label = "🔗 Google Sheet: connected ✅" if connected else "🔗 Connect Google Sheet"
    with st.expander(label, expanded=not connected):
        theme.note("One-time setup. In your sheet: **Extensions → Apps Script**, paste the "
                   "script from the `google_apps_script` folder, **Deploy → Web app** "
                   "(access: Anyone), then paste the link it gives you here.")
        if connected:
            theme.note("**Update the script to v8 - this one you have to do.** v8 keeps "
                       "real money and practice money in two separate tabs: "
                       "**Real Money Log** and **Practice Log**, both created for you the "
                       "first time each kind of trade is logged. Your existing "
                       "**Options Assistant Log** tab is never written to again and is read "
                       "as practice history - nothing in it is moved, renamed or deleted. "
                       "The old Hebrew-format **App Trades** tab stays a frozen archive.")
            theme.note("To update: paste the new `LogTrade.gs` (in the `google_apps_script` "
                       "folder) over the old one, then **Deploy → Manage deployments → "
                       "✏️ Edit → Version: New version → Deploy**. Your link stays the "
                       "same. Until you do, trades keep landing in the single old tab and "
                       "the app tells the two books apart by the **Account** column instead "
                       "- nothing breaks, the tabs just are not split yet.")
        current = webhook_logger.get_url() or ""
        url = st.text_input("Web app link", value=current, key="webhook_url",
                            placeholder="https://script.google.com/macros/s/.../exec")
        c1, c2 = st.columns(2)
        if c1.button("Save link", key="save_webhook"):
            if url.strip().startswith("https://"):
                webhook_logger.set_url(url.strip())
                st.success("Saved. Your trades will now log to your Google Sheet.")
            else:
                st.error("That does not look like a link. It should start with https://")
        if connected and c2.button("Test it", key="test_webhook"):
            _test_sheet_connection()
        theme.note("On the **hosted** app the link comes from **Settings → Secrets** "
                   "(share.streamlit.io → your app → ⋮ → Settings → Secrets) as:  "
                   "google_sheet_webhook = \"https://script.google.com/...\"  - the box "
                   "above only covers this device.")


def _test_sheet_connection() -> None:
    from datetime import date
    from src.logging_tools import webhook_logger
    test_row = ["TEST " + date.today().isoformat(), "-", "connection test",
                "-", "-", "-", "-", "-", "-", "-", "-", "you can delete this row"]
    try:
        webhook_logger.append(test_row, [])
        st.success("Test row sent - check your sheet. You can delete that test row.")
    except Exception as e:
        st.error(f"Could not reach the sheet: {e}. Re-check the Deploy step "
                 "(access must be 'Anyone') and the link.")


if __name__ == "__main__":
    main()
