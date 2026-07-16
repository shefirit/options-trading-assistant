"""Options Trading Assistant - a free-navigation trading dashboard.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

Seven tabs, all open at once - use them in any order, nothing is locked:

  📊 Market   - is today a good day to sell premium? (holiday-aware)
  💡 Picks    - WHAT looks good today: scans the indexes, the big ETFs, and the
                S&P 500 for SOP-fitting monthly premium candidates, ranked,
                with dividends and a risk picture
  🔎 Premium  - screen names for the richest, safest option premium
  🔬 Analyze  - any stock/ETF/index: full picture + the strategy that fits it
  🎯 Build    - pick a strategy, scan real setups, check your SOP rules, log it
  📒 Trades   - every logged trade tracked live against your own exit rules,
                plus your results vs your weekly/monthly goals
  ⚙️ Settings - connections (Google Sheet, earnings data, Schwab) and your plan
                numbers, in the main screen where they work on a phone too

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
from src.engine.config_loader import allowed_underlyings_for, load_settings, load_strategies
from src.engine.models import Action, Leg, OptionType, Trade
from src.engine.strategy_advisor import advise
from src.engine.validator import validate_trade
from ui import components, theme

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
    tone = {"schwab": "green", "yahoo": "green", "demo": "amber"}[provider.mode]
    text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
            "demo": "● DEMO · sample data"}[provider.mode]
    return text, tone


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

    _sidebar(settings, provider)

    theme.hero(
        "Options Trading Assistant",
        "Read the market, get today's picks, screen premium, analyze a name, build and "
        "check the trade.",
        [_mode_badge(provider), _log_badge()])

    t_market, t_picks, t_prem, t_analyze, t_build, t_trades, t_settings = st.tabs(
        ["📊 Market", "💡 Picks", "🔎 Premium", "🔬 Analyze", "🎯 Build", "📒 Trades",
         "⚙️ Settings"])
    with t_market:
        _guard(_tab_market, settings, provider, strategies)
    with t_picks:
        _guard(_tab_picks, settings, strategies, provider)
    with t_prem:
        _guard(_tab_premium, settings, provider)
    with t_analyze:
        _guard(_tab_analyze, settings, provider, strategies)
    with t_build:
        _guard(_tab_build, settings, strategies, provider)
    with t_trades:
        _guard(_tab_trades, settings, strategies, provider)
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
            nxt_str = f"{nxt:%A}, {nxt:%B} {nxt.day}"
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
    _soft(_market_fit_section, ctx, strategies, what="strategy board")
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


def _market_fit_section(ctx, strategies) -> None:
    """Every index strategy ranked against today's trend and volatility -
    the old single 'best play' line, upgraded to show the reasoning."""
    st.markdown("### 🧭 Strategy fit today")
    theme.note("The app ranks your three index strategies against today's trend and "
               "volatility. These are reasons, not instructions - you check the winner "
               "in 🎯 Build and you decide.")
    if ctx.suggestions:
        components.render_strategy_fit(ctx.suggestions)
    best_key = ctx.best_strategy_key or list(strategies.keys())[0]
    best_name = ctx.best_strategy_name or strategies[best_key]["name"]
    if st.button(f"Set up {best_name} in Build ▸", type="primary", key="mkt_to_build"):
        st.session_state["build_strategy"] = best_key
        st.session_state["build_underlyings"] = ["SPX"]
        st.session_state["_prev_build_strategy"] = best_key
        st.success("Loaded into **🎯 Build** - open that tab to scan it.")


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
               "through your strikes. Anything inside your trade window is flagged.")
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
    """WHAT looks good today: scan the allowed universe, rank the SOP fits."""
    import time as _time

    from src.engine import recommender

    theme.section("Who looks good to sell premium on right now?", "Today's picks")
    theme.note("One button scans your allowed universe - the 4 cash-settled indexes, the big "
               "liquid ETFs, and the whole S&P 500 - and ranks who fits your SOP for a "
               "monthly premium trade today: generous premium, sane risk, and a dividend "
               "when there is one. These are **candidates with reasons, not instructions** - "
               "you check the winner in 🎯 Build and you decide.")

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
         "🌐 Full market sweep - screen every S&P 500 stock + ~45 big ETFs for hidden "
         "gems (a few minutes the first time each day)"],
        key="picks_scope")
    full = scope.startswith("🌐")

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
    st.markdown("### 💰 Stock and ETF plays - puts and covered calls for income")
    theme.note("Only the names actually worth selling are shown - anything hard to trade, in a "
               "downtrend, weak, or paying thin premium is left out (listed at the bottom). "
               "For each: the one-month put you'd sell (~0.30 delta), the strategy it points "
               "to, and the dividend as a bonus. Ranked by verdict, then income; a dividend "
               "only breaks near-ties.")
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

    if report.left_out:
        with st.expander(f"Left out - not among the best right now ({len(report.left_out)})"):
            theme.note("These were scanned but didn't make the cut - shown here so nothing is "
                       "hidden. If you disagree with one, you can still build it in 🎯 Build.")
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


def _run_picks_scan(provider, settings, strategies, monthly, vix, full: bool):
    """Stage 1 (screen the market) + stage 2 (option-chain read on the survivors)."""
    import datetime as dt
    import time as _time

    from src.data import cache, market_screener, premium_finder, stock_universe
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
            screen = provider.get_screen(f"full:{dt.date.today().isoformat()}",
                                         stock_universe.sp500(),
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


def _picks_risk_block(max_loss, bp, settings, liquidity_line, settlement, events,
                      extra=None) -> None:
    """One candidate's risk picture: worst case, buying power vs the monthly
    limit, liquidity, settlement style, and the events inside the window."""
    bp_limit = float(settings["risk_limits"]["monthly_bp_limit"])
    loss_txt = f"&#36;{max_loss:,.0f}" if max_loss is not None else "see Build"
    bp_txt = (f"&#36;{bp:,.0f} <span style='font-size:.9rem;font-weight:600;'>"
              f"({bp / bp_limit * 100:.0f}% of your &#36;{bp_limit:,.0f} monthly limit)"
              "</span>" if bp is not None else "see Build")
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
        st.warning(components._esc(pick.error + " Use 🎯 Build to scan other expirations."))
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
    if st.button(f"Set up {pick.strategy_name} on {pick.symbol} in 🎯 Build ▸",
                 type="primary", key=f"picks_spread_build_{pick.symbol}"):
        st.session_state["build_strategy"] = pick.strategy_key
        st.session_state["build_underlyings"] = [pick.symbol]
        st.session_state["_prev_build_strategy"] = pick.strategy_key
        st.success("Loaded into **🎯 Build** - open that tab to scan and check it.")


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
        extra.append("A PMCC's real risk is the long-dated call you buy - scan it in Build "
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
    if st.button(f"Set up {label} on {s.symbol} in 🎯 Build ▸",
                 type="primary", key="picks_inc_to_build"):
        st.session_state["build_strategy"] = pick.strategy_key
        st.session_state["build_underlyings"] = [s.symbol]
        st.session_state["_prev_build_strategy"] = pick.strategy_key
        st.success("Loaded into **🎯 Build** - open that tab to scan and check it.")


# ------------------------------------------------------------------ Find premium tab
def _tab_premium(settings, provider) -> None:
    from src.data import stock_universe

    theme.section("Which names pay the best premium - and are worth it?", "Premium finder")
    theme.note("For each name it prices the one-month put you'd sell (~0.30 delta) and lays out "
               "the income, your odds, the safety cushion, how rich the premium really is, and "
               "whether it's tradable. Sort the table by any column.")

    if not provider.is_real:
        st.info("The premium finder needs real market data - connect to the internet first.")
        return

    etfs = settings["underlyings"]["us_style"]
    options = list(dict.fromkeys(etfs + stock_universe.FEATURED + stock_universe.all_stocks()))
    picks = st.multiselect(
        "Names to compare", options,
        default=[s for s in ["AAPL", "NVDA", "MSFT", "SPY", "QQQ"] if s in options],
        max_selections=20, key="premium_picks",
        help="Add as many as you like - the table compares them all at once.")

    monthly_bp = float(settings["risk_limits"]["monthly_bp_limit"])
    if st.button("Compare", type="primary", key="premium_scan"):
        if not picks:
            st.warning("Pick at least one name.")
        else:
            snaps = []
            bar = st.progress(0.0, text="Reading option premiums...")
            for i, sym in enumerate(picks):
                try:
                    snaps.append(provider.get_premium_snapshot(sym, monthly_bp=monthly_bp))
                except Exception as e:
                    from src.data.premium_finder import PremiumSnapshot
                    snaps.append(PremiumSnapshot(symbol=sym, error=str(e)[:40]))
                bar.progress((i + 1) / len(picks), text=f"Checked {sym} ({i+1}/{len(picks)})")
            bar.empty()
            from src.data import premium_finder
            st.session_state["premium_snaps"] = premium_finder.rank(snaps)

    snaps = st.session_state.get("premium_snaps")
    if not snaps:
        theme.note("Press **Compare** to build the table.")
        return

    st.dataframe(components.premium_dataframe(snaps), width="stretch", hide_index=True,
                 column_config=components.premium_column_config())
    with st.expander("🎓 What the columns mean"):
        st.markdown(
            "- **Verdict** - the bottom-line call: ✅ good to sell / ⚠️ okay / ❌ skip. It already "
            "weighs everything below, so if you only read one column, read this.\n"
            "- **Quality** - the company's A-F grade (ETFs are baskets, shown as ETF). It matters "
            "because if the put assigns, you end up owning the shares.\n"
            "- **Income $/mo** and **Yield %/mo** - the cash you collect, and that as a % of the "
            "money you set aside (the fair way to compare names).\n"
            "- **Premium deal** - is the premium a *good deal for the risk*? **Rich** = you're "
            "paid more than this stock's usual swings would justify (good for you). **Thin** = it "
            "moves a lot but pays little (bad). **Fair** = normal.\n"
            "- **Watch out** - flags a landmine: an earnings report before expiry, or options "
            "that are hard to trade.")

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
                st.success(f"Loaded {chosen} into **🔬 Analyze** - open that tab.")


# ------------------------------------------------------------------ Analyze tab
def _tab_analyze(settings, provider, strategies) -> None:
    theme.section("Analyze any name - and get the strategy that fits it", "Deep dive")
    opts = _symbol_options(settings)
    default = st.session_state.get("analyze_sym")
    idx = opts.index(default) if default in opts else None
    sym = st.selectbox("Symbol", opts, index=idx, key="analyze_sym",
                       placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...")
    if not sym:
        theme.note("Pick an index, ETF, or stock for its full picture and the strategy that "
                   "fits it.")
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
            if st.button(f"Build this: {advice.primary.name} on {sym} ▸", type="primary",
                         key="analyze_to_build"):
                st.session_state["build_strategy"] = advice.primary.key
                st.session_state["build_underlyings"] = [sym]
                st.session_state["_prev_build_strategy"] = advice.primary.key
                st.success("Loaded into **🎯 Build** - open that tab to scan and check it.")


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


# ------------------------------------------------------------------ Build & check tab
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


def _tab_build(settings, strategies, provider) -> None:
    from src.data import stock_universe
    keys = list(strategies.keys())
    st.session_state.setdefault("build_strategy", keys[0])

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
        valid = [u for u in st.session_state["build_underlyings"] if u in ordered]
        st.session_state["build_underlyings"] = valid or default_u
    underlyings = top[1].multiselect("Underlying(s)", ordered, key="build_underlyings",
                                     help="Type to search. Pick more than one to scan together.")

    _underlying_prices(underlyings, provider)

    if strat.get("family") == "credit_spread":
        theme.note("ℹ️ Per your SOP, credit spreads run on **any liquid stock, ETF, or index**. "
                   "**Indexes** (SPX, NDX, RUT, XSP) are cash-settled with no assignment risk - "
                   "the cleanest choice. **Stocks/ETFs** can be assigned, so the app enters them "
                   "nearer 45 DTE and warns about earnings.")
    else:
        theme.note("Type any S&P 500 or Nasdaq-100 **stock** (AAPL, NVDA...) or an ETF "
                   "(SPY, QQQ, IWM, DIA). Want the recommended play for a name? Use **Analyze**.")

    uses_width = strat.get("family") == "credit_spread"
    row = st.columns([1, 1] if uses_width else [1, 2])
    contracts = row[0].number_input("Contracts", min_value=1, max_value=50, value=1, step=1)
    width = None
    if uses_width:
        from src.engine.config_loader import underlying_kind
        # SOP width: individual stocks $5-10; indexes and ETFs $25-50. Default to the
        # right tier for the picked names, resetting when you switch types.
        kinds = {underlying_kind(u) for u in underlyings} if underlyings else {"index"}
        default_width = 5.0 if kinds == {"stock"} else 25.0
        if st.session_state.get("_prev_width_kinds") != kinds:
            st.session_state["_prev_width_kinds"] = kinds
            st.session_state["build_width"] = default_width
        width = row[1].number_input("Spread width ($)", min_value=1.0, max_value=200.0,
                                    step=1.0, key="build_width",
                                    help="SOP: individual stocks $5-10; indexes and ETFs $25-50.")

    sig = (strategy_key, tuple(underlyings), int(contracts), width)
    if st.session_state.get("build_sig") != sig:
        st.session_state["build_sig"] = sig
        st.session_state.pop("build_candidates", None)
        st.session_state.pop("build_chosen", None)

    _strategy_about(strat)
    st.divider()
    mode = st.radio("How", ["🔎 Find setups for me", "✅ Check a trade I built myself"],
                    horizontal=True, label_visibility="collapsed", key="build_mode")
    if mode.startswith("🔎"):
        _build_scan(strategy_key, strat, underlyings, provider, contracts, width, settings)
    else:
        _build_manual(strategy_key, strat, underlyings, settings)


def _spread_event_warnings(underlyings, provider, dte_window=45) -> list:
    """Your SOP: no binary events (earnings / Fed) during a credit-spread trade."""
    import datetime as dt

    from src.data import stock_universe
    notes = []
    today = dt.date.today()
    if provider.is_real:
        for u in underlyings:
            if not stock_universe.is_stock(u):
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
        st.info("This strategy depends on the real shares you already own, so use **Check a "
                "trade I built myself** above to validate it against your SOP.")
        return
    if not underlyings:
        st.warning("Pick at least one underlying above.")
        return

    if strat.get("family") == "credit_spread":
        for _msg in _spread_event_warnings(underlyings, provider):
            st.warning(_msg)

    existing_bp = st.number_input(
        "Buying power already used this month ($)", min_value=0.0,
        value=float(st.session_state.get("open_bp_in_use", 0.0)), step=1000.0,
        help="Auto-filled from your open trades in My trades - adjust if you also have "
             "positions the app doesn't know about.")
    is_pmcc = strat.get("family") == "diagonal"
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
                leaps = provider.get_leaps_chain(u) if is_pmcc else None
                found.extend(scanner.scan_setups(key, chain, width=width,
                                                 contracts=int(contracts), max_setups=10,
                                                 leaps_chain=leaps))
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
        found.sort(key=lambda c: (c.trade.underlying, c.dte if c.dte is not None else 0))
        st.session_state["build_candidates"] = found
        st.session_state.pop("build_chosen", None)

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

    st.dataframe(components.candidates_dataframe(candidates), width="stretch", hide_index=True)

    pick = st.number_input("Look at trade #", min_value=1, max_value=len(candidates),
                           value=1, step=1)
    chosen = candidates[int(pick) - 1]
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
        _log_button(chosen.trade, strat["name"], size, report.passed, key="scan")


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


def _build_manual(key, strat, underlyings, settings) -> None:
    theme.note("Type in the trade exactly as you set it up in thinkorswim. "
               "Long = you bought it (+), short = you sold it (-).")
    underlying = st.selectbox("Underlying", underlyings or allowed_underlyings_for(key),
                              key="val_underlying")
    existing_bp = st.number_input(
        "Buying power already used this month ($)", min_value=0.0,
        value=float(st.session_state.get("open_bp_in_use", 0.0)), step=1000.0, key="val_bp",
        help="Auto-filled from your open trades in My trades - adjust if you also have "
             "positions the app doesn't know about.")

    legs: list[Leg] = []
    for i, leg_def in enumerate(strat["legs"]):
        role = leg_def["role"]
        action = Action(leg_def["action"])
        opt_type = OptionType(leg_def["option_type"])
        qty = int(leg_def.get("quantity", 1))
        sign = "+" if action == Action.BUY else "-"
        st.markdown(f"**{role.replace('_', ' ').title()}** "
                    f"({sign}{qty} {opt_type.value} - you {action.value.upper()} it)")
        cols = st.columns(4)
        strike = cols[0].number_input("Strike", min_value=0.0, value=0.0, step=1.0, key=f"strike_{i}")
        delta = cols[1].number_input("Delta", min_value=-1.0, max_value=1.0, value=0.0, step=0.01,
                                     key=f"delta_{i}", help="Puts negative, calls positive.")
        premium = cols[2].number_input("Mid price", min_value=0.0, value=0.0, step=0.05, key=f"prem_{i}")
        dte = cols[3].number_input("DTE", min_value=0, max_value=1000, value=30, step=1, key=f"dte_{i}")
        legs.append(Leg(role=role, action=action, option_type=opt_type, strike=strike,
                        delta=delta, premium=premium, quantity=qty, dte=int(dte)))

    contracts = st.number_input("Contracts", min_value=1, max_value=50, value=1, step=1,
                                key="val_contracts")
    if st.button("Check this trade", type="primary"):
        trade = Trade(strategy_key=key, underlying=underlying, contracts=int(contracts), legs=legs)
        st.session_state["checked_trade"] = trade

    checked = st.session_state.get("checked_trade")
    if checked is not None:
        report = validate_trade(checked, existing_month_bp=existing_bp)
        with st.container(border=True):
            components.render_checklist(report)
            from src.engine import sizing
            size = sizing.estimate(checked, strat)
            _risk_and_payoff(checked, strat, size, settings)
            _log_button(checked, strat["name"], size, report.passed, key="manual")


# ------------------------------------------------------------------ My trades tab
_SIGNAL_ORDER = {"stop": 0, "time": 1, "profit": 2, "watch": 3, "unpriced": 4, "hold": 5}
_DEFAULT_EXIT = {"profit_target_pct": 50, "stop_loss_multiple": 2.0, "time_exit_dte": 21}


def _load_trade_log() -> tuple[list, list, str]:
    """The trade log rows, fetched once per session (Refresh re-reads)."""
    if "trades_rows" not in st.session_state:
        with st.spinner("Reading your trade log..."):
            try:
                from src.logging_tools.trade_logger import fetch_all_rows
                st.session_state["trades_rows"] = fetch_all_rows()
            except Exception:
                st.session_state["trades_rows"] = ([], [], "local")
    return st.session_state["trades_rows"]


def _exit_cfg_for(pos, strategies) -> dict:
    strat = strategies.get(pos.strategy_key)
    if strat is None:   # older row - find the strategy by its display name
        strat = next((s for s in strategies.values()
                      if s.get("name") == pos.strategy_name), None)
    return (strat or {}).get("exit", _DEFAULT_EXIT) or _DEFAULT_EXIT


def _price_positions(open_pos, provider, strategies) -> tuple[list, str]:
    """Price every open position and run the exit rules - memoized for a few
    minutes in the session. Every tap anywhere in the app reruns the whole
    script (all tabs), so without this the pricing loop would replay on each
    interaction; with it, only the first look and every ~3 minutes do work.
    Returns (items, as-of time)."""
    import datetime as dt
    import time

    from src.engine import exit_rules

    sig = (tuple(sorted(p.trade_id or f"{p.underlying}|{p.opened}" for p in open_pos)),
           int(time.time() // 180))
    cached = st.session_state.get("_priced_positions")
    if cached and cached["sig"] == sig:
        return cached["items"], cached["at"]

    items = []
    bar = st.progress(0.0, text="Pricing your open trades...")
    for i, p in enumerate(open_pos):
        live = provider.price_position(p)
        s = exit_rules.evaluate(
            p, _exit_cfg_for(p, strategies),
            current_cost=live.get("cost_to_close"),
            underlying_price=live.get("underlying_price"),
            short_delta=live.get("short_delta"))
        items.append({"position": p, "live": live, "signal": s})
        bar.progress((i + 1) / len(open_pos),
                     text=f"Priced {p.underlying} ({i + 1}/{len(open_pos)})")
    bar.empty()
    items.sort(key=lambda it: _SIGNAL_ORDER.get(it["signal"].action, 9))
    at = dt.datetime.now().strftime("%H:%M")
    st.session_state["_priced_positions"] = {"sig": sig, "items": items, "at": at}
    return items, at


def _delete_control(trade_id, what: str, key: str) -> None:
    """A guarded delete: tick a box, then the button removes the trade's rows
    from the log (sheet or local backup). For trades logged by mistake / tests."""
    theme.note(f"This permanently removes **{what}** from your log. Use it only for a "
               "trade you logged by mistake or while testing - not one you actually "
               "traded (close that instead, so your results stay honest).")
    sure = st.checkbox("Yes, I logged this by mistake - delete it", key=f"delsure_{key}")
    if st.button("🗑️ Delete this trade", key=f"del_{key}", disabled=not sure):
        from src.logging_tools.trade_logger import delete_trade
        try:
            removed, source = delete_trade(trade_id)
        except Exception as e:
            st.error(f"Could not delete it: {e}")
            return
        st.session_state.pop("trades_rows", None)
        if removed:
            st.success(f"Deleted ({removed} row(s) removed from your "
                       f"{'Google Sheet' if source == 'sheet' else 'local log'}).")
            st.rerun()
        else:
            st.warning("Nothing was deleted - it may already be gone. Press ↻ Refresh.")


def _quick_log_form(settings, strategies, provider) -> None:
    """Record a trade she ALREADY placed in thinkorswim, in under a minute:
    strategy, strikes, expiration, contracts, and the credit on her fill.
    The chain fills in deltas when it can; the SOP check informs, never blocks."""
    import datetime as dt

    from src.engine import quick_log

    # Stay open while a checked draft is waiting, otherwise the rerun after
    # "Check it" would collapse the expander and hide the preview.
    with st.expander("➕ Quick Log - a trade you already placed in thinkorswim",
                     expanded=bool(st.session_state.get("ql_draft"))):
        theme.note("Place the trade in TOS first, then write it down here. Type only "
                   "what is on your fill - the app fills in the market details and "
                   "starts watching your exit rules for it.")

        keys = list(strategies.keys())
        top = st.columns([3, 2])
        strategy_key = top[0].selectbox(
            "Strategy", keys, key="ql_strategy",
            format_func=lambda k: strategies[k]["name"])
        strat = strategies[strategy_key]
        if st.session_state.get("_prev_ql_strategy") != strategy_key:
            st.session_state["_prev_ql_strategy"] = strategy_key
            st.session_state.pop("ql_draft", None)   # a draft for another strategy

        allowed = allowed_underlyings_for(strategy_key)
        default_i = allowed.index("SPX") if "SPX" in allowed else 0
        underlying = top[1].selectbox("Underlying", allowed, index=default_i,
                                      key=f"ql_u_{strategy_key}",
                                      help="Type to search.")

        basis = str(strat.get("sizing", {}).get("max_loss_basis", "vertical_width"))
        has_far_leg = basis in ("debit", "shares_plus_protection", "ratio_risk")
        today = dt.date.today()

        with st.form("ql_form"):
            d1, d2 = st.columns(2)
            expiration = d1.date_input(
                "Expiration date (from your TOS fill)"
                if not has_far_leg else "Short call expiration (the near one)",
                value=today + dt.timedelta(days=45), min_value=today,
                key=f"ql_exp_{strategy_key}")
            opened_on = d2.date_input(
                "Opened on", value=today, max_value=today,
                help="Change this only if you placed the trade on an earlier day.",
                key=f"ql_opened_{strategy_key}")

            far_exp = None
            leaps_cost = None
            share_price = None
            protection_cost = None
            if basis == "debit":
                f1, f2 = st.columns(2)
                far_exp = f1.date_input(
                    "LEAPS expiration (the far-dated call you BOUGHT)",
                    value=today + dt.timedelta(days=365), min_value=today,
                    key=f"ql_farexp_{strategy_key}")
                leaps_cost = f2.number_input(
                    "What you paid for the LEAPS ($ total)", min_value=0.0,
                    step=50.0, key=f"ql_leaps_{strategy_key}",
                    help="From your TOS fill: the price you paid x 100 x "
                         "contracts. A 40.00 fill on 1 contract = $4,000. This "
                         "is your real money at risk, so the app needs it to "
                         "tell you what the trade actually made.")
            elif has_far_leg:
                f1, f2 = st.columns(2)
                far_exp = f1.date_input(
                    "Protective put expiration (the far-dated one)",
                    value=today + dt.timedelta(days=365), min_value=today,
                    key=f"ql_farexp_{strategy_key}")
                share_price = f2.number_input(
                    "Share price when you bought the 100 shares ($)",
                    min_value=0.0, step=1.0, key=f"ql_shares_{strategy_key}")
                protection_cost = st.number_input(
                    "What the put side cost you ($ total, net)",
                    step=25.0, key=f"ql_prot_{strategy_key}",
                    help="Model 1: what the long put cost. Model 2: the net "
                         "debit of the put spread. Model 3: often near zero - "
                         "and if the ratio paid you a credit, type a negative "
                         "number. Leave at 0 only if it really was free.")

            leg_defs = strat.get("legs", [])
            cols = st.columns(min(len(leg_defs), 4) or 1)
            strikes: dict[str, float] = {}
            for i, leg_def in enumerate(leg_defs):
                role = str(leg_def["role"])
                verb = "SOLD" if leg_def["action"] == "sell" else "BOUGHT"
                label = (f"{role.replace('_', ' ').capitalize()} strike "
                         f"(you {verb} this {leg_def['option_type']})")
                strikes[role] = cols[i % len(cols)].number_input(
                    label, min_value=0.0, step=1.0,
                    key=f"ql_strike_{strategy_key}_{role}")

            b1, b2 = st.columns(2)
            contracts = b1.number_input("Contracts", min_value=1, max_value=50,
                                        value=1, step=1,
                                        key=f"ql_contracts_{strategy_key}")
            credit_label = ("Total credit received ($, from your TOS fill)"
                            if basis not in ("debit", "shares_plus_protection",
                                             "ratio_risk")
                            else "Credit collected for the call you SOLD ($ total)")
            credit_total = b2.number_input(credit_label, min_value=0.0, step=5.0,
                                           key=f"ql_credit_{strategy_key}")
            note = st.text_input("Note (optional)", key=f"ql_note_{strategy_key}")

            submitted = st.form_submit_button("Check it", type="primary")

    # Everything below renders OUTSIDE the expander, so the result of
    # "Check it" (a warning or the preview card) is visible even after
    # Streamlit collapses the expander on the rerun.
    if submitted:
        if any(v <= 0 for v in strikes.values()):
            st.warning("Almost - type every strike first, one of them is still 0. "
                       "Open ➕ Quick Log above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif credit_total <= 0:
            st.warning("Almost - type the credit you collected (it is on your TOS "
                       "fill). Open ➕ Quick Log above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif basis == "debit" and not leaps_cost:
            # Without it the position looks like a tiny credit trade and every
            # number downstream - result, return, buying power - comes out wrong.
            st.warning("Almost - type what you paid for the LEAPS. That is the "
                       "money actually at risk in a PMCC, and without it the app "
                       "cannot tell you what the trade made. Open ➕ Quick Log "
                       "above to fill it in.")
            st.session_state.pop("ql_draft", None)
        elif has_far_leg and basis != "debit" and not share_price:
            st.warning("Almost - type the share price you paid. That is most of "
                       "the money in a covered call, and the app needs it to "
                       "track the trade's result. Open ➕ Quick Log above to "
                       "fill it in.")
            st.session_state.pop("ql_draft", None)
        else:
            dte = max((expiration - opened_on).days, 0)
            leaps_dte = (max((far_exp - opened_on).days, 0)
                         if far_exp is not None else None)
            legs = quick_log.legs_from_strategy(strat, strikes, dte,
                                                leaps_dte=leaps_dte)
            notes: list[str] = []
            underlying_price = None
            try:
                chain = provider.get_chain(underlying,
                                           dte_min=max(dte - 4, 0),
                                           dte_max=dte + 4)
                underlying_price = chain.underlying_price
                legs, fill_notes = quick_log.fill_from_chain(
                    legs, chain, expiration.isoformat(),
                    leaps_expiration_iso=(far_exp.isoformat()
                                          if far_exp else None))
                notes.extend(fill_notes)
            except Exception:
                notes.append("Live option prices were not available just now - "
                             "saved without deltas. Tracking still works from "
                             "your credit and strikes.")
            trade = Trade(strategy_key=strategy_key, underlying=underlying,
                          contracts=int(contracts), legs=legs,
                          underlying_price=underlying_price or share_price)
            sizing = quick_log.sizing_from_fill(
                trade, strat, float(credit_total),
                leaps_cost_total=leaps_cost, share_price=share_price,
                protection_cost_total=protection_cost)
            passed = True
            try:
                report = validate_trade(
                    trade,
                    existing_month_bp=st.session_state.get("open_bp_in_use", 0.0))
                passed = report.passed
            except Exception:
                notes.append("The SOP check could not run just now - the trade "
                             "still gets logged and tracked.")
            st.session_state["ql_draft"] = {
                "trade": trade, "strat_name": strat["name"], "sizing": sizing,
                "passed": passed, "notes": notes, "note": note,
                "opened_on": opened_on, "expiration": expiration, "dte": dte,
            }

    draft = st.session_state.get("ql_draft")
    if draft:
        with st.container(border=True):
            p_trade, p_size = draft["trade"], draft["sizing"]
            theme.note(f"**Ready to save: {p_trade.underlying} · "
                       f"{draft['strat_name']}** · {p_trade.contracts} "
                       f"contract(s) · opened {draft['opened_on'].isoformat()} · "
                       f"expires {draft['expiration'].isoformat()} "
                       f"({draft['dte']} days)")
            open_cash = float(p_size.get("open_cash", p_size["credit"]))
            if open_cash < 0:
                # A PMCC or covered call takes money OUT to open. Showing only
                # the call credit here is what made a multi-thousand-dollar
                # position look like a trade worth a couple hundred.
                m = st.columns(4)
                m[0].metric("Call credit", money(p_size["credit"]),
                            help="What the short call paid you. Your 50% profit "
                                 "target measures against this - not against the "
                                 "whole position.")
                m[1].metric("Cash out today", money(-open_cash),
                            help="What actually left your account: the long side "
                                 "you bought, minus the call credit. Closing the "
                                 "trade pays this back, plus or minus your result.")
                m[2].metric("Max loss", money(p_size["max_loss"]))
                m[3].metric("Buying power", money(p_size["buying_power"]))
            else:
                m = st.columns(3)
                m[0].metric("Credit", money(p_size["credit"]))
                m[1].metric("Max loss", money(p_size["max_loss"]))
                m[2].metric("Buying power", money(p_size["buying_power"]))
            if draft["passed"]:
                st.markdown(theme.chip("SOP check: passed", "green"),
                            unsafe_allow_html=True)
            else:
                st.markdown(theme.chip(
                    "Heads up: outside your SOP rules - logged anyway, since "
                    "it is already placed", "amber"), unsafe_allow_html=True)
            for n in draft["notes"]:
                theme.note(n)
            c1, c2 = st.columns([1, 1])
            if c1.button("✅ Save to my log", type="primary", key="ql_save"):
                from src.logging_tools.trade_logger import log_trade
                dest, live, trade_id = log_trade(
                    draft["trade"], draft["strat_name"], draft["sizing"],
                    draft["passed"], draft["note"],
                    opened_on=draft["opened_on"],
                    expiration_on=draft["expiration"])
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state.pop("ql_draft", None)
                st.session_state["ql_flash"] = (
                    "Saved. It now shows in your open trades below"
                    + (" and in your Google Sheet." if live
                       else " (saved on this device - connect your Google "
                            "Sheet in ⚙️ Settings to sync it everywhere)."))
                st.rerun()
            if c2.button("Never mind - discard this draft", key="ql_discard"):
                st.session_state.pop("ql_draft", None)
                st.rerun()


def _live_call_mid(provider, underlying: str, strike: float,
                   expiration: dt.date) -> Optional[float]:
    """Today's mid for one call, or None. Used to suggest what a freshly sold
    call was worth, so she doesn't have to dig per-leg prices out of TOS."""
    import datetime as dt

    if not strike or expiration is None:
        return None
    try:
        dte = max((expiration - dt.date.today()).days, 0)
        chain = provider.get_chain(underlying, dte_min=max(dte - 4, 0),
                                   dte_max=dte + 4)
    except Exception:
        return None
    if chain is None:
        return None
    exp = expiration.isoformat()
    contract = next(
        (c for c in chain.contracts
         if c.option_type == OptionType.CALL and c.expiration == exp
         and abs(c.strike - strike) < 1e-6), None)
    if contract is None or contract.mid <= 0:
        return None
    return round(contract.mid * 100, 2)


def _roll_form(p, live: dict, provider) -> None:
    """Record a roll of the short call: buy back the near one, sell a further-out
    one, usually for a net credit.

    This keeps ONE position from the LEAPS purchase to the LEAPS sale. Closing
    and re-logging instead would re-enter the LEAPS as a fresh several-thousand
    dollar purchase every month and make the results meaningless.
    """
    import datetime as dt

    with st.expander("🔄 Roll the short call (records the credit you collected)"):
        theme.note("Roll it in thinkorswim first, then write the fill down here. "
                   "The credit is banked in this month's profit, and the app "
                   "starts watching the new call - same trade, no re-typing the "
                   "LEAPS.")
        r1, r2, r3 = st.columns(3)
        rolled_on = r1.date_input("Rolled on", value=dt.date.today(),
                                  max_value=dt.date.today(),
                                  key=f"roll_when_{p.trade_id}")
        new_strike = r2.number_input(
            "New short call strike", min_value=0.0, step=1.0,
            key=f"roll_strike_{p.trade_id}",
            help="The call you SOLD in the roll - the further-out one.")
        new_exp = r3.date_input(
            "New expiration", value=dt.date.today() + dt.timedelta(days=30),
            min_value=dt.date.today(), key=f"roll_exp_{p.trade_id}")

        cash = st.number_input(
            "Net credit from the roll ($ total, from your TOS fill)",
            step=5.0, key=f"roll_cash_{p.trade_id}",
            help="The net price on the fill, x100 x contracts. A diagonal "
                 "filled at 0.80 credit on 1 contract = $80. If the roll cost "
                 "you money instead, type a negative number.")

        suggested = _live_call_mid(provider, p.underlying, new_strike, new_exp)
        # Keying on the strike and date re-seeds the default whenever she
        # changes them - Streamlit ignores value= once a key has been seen.
        new_credit = st.number_input(
            "What the NEW call sold for on its own ($ total)",
            min_value=0.0, step=5.0, value=float(suggested or 0.0),
            key=f"roll_credit_{p.trade_id}_{new_strike:g}_{new_exp}",
            help="Not the net - what the call you just sold was worth by "
                 "itself. Your 50% profit target measures against this from "
                 "now on.")
        if suggested:
            theme.note(f"Suggested from today's chain: **\\${suggested:,.0f}** "
                       f"for the {new_strike:g} call expiring {new_exp}. Change "
                       "it if your fill said otherwise.")
        elif new_strike:
            theme.note("That contract could not be priced from the chain just "
                       "now, so type what it sold for. Without it the app "
                       "cannot tell you when the new call hits your 50% target.")
        note = st.text_input("Note (optional)", key=f"roll_note_{p.trade_id}")

        if st.button("Record the roll", type="primary", key=f"rollbtn_{p.trade_id}"):
            if not new_strike:
                st.warning("Type the new short call's strike first.")
            elif not cash:
                st.warning("Type the net credit from the roll - it is the money "
                           "this roll actually made you.")
            elif not new_credit:
                st.warning("Type what the new call sold for on its own, so the "
                           "app knows when it reaches your 50% target.")
            elif new_exp <= (p.expiration or dt.date.today()):
                st.warning(f"A roll moves the call OUT in time, but {new_exp} is "
                           f"not after this position's current expiration "
                           f"({p.expiration}). Check the date.")
            else:
                from src.logging_tools.trade_logger import roll_trade
                roll_trade(p.trade_id, p.underlying, p.strategy_name,
                           float(cash), float(new_strike), new_exp,
                           float(new_credit), note, rolled_on=rolled_on)
                st.session_state.pop("trades_rows", None)
                st.session_state.pop("_priced_positions", None)
                st.session_state["ql_flash"] = (
                    f"Roll recorded: ${cash:,.0f} banked, now tracking the "
                    f"{new_strike:g} call expiring {new_exp}.")
                st.rerun()


def _today_card(items: list[dict]) -> None:
    """One line that answers the beginner's first question: anything to DO today?"""
    needs = [it for it in items if it["signal"].action in ("stop", "time", "profit")]
    if needs:
        word = "trade needs" if len(needs) == 1 else "trades need"
        st.error(f"🔔 {len(needs)} of {len(items)} open {word} action today - "
                 "see the What to do column, then close in thinkorswim.")
    else:
        st.markdown(theme.chip(
            f"✅ {len(items)} open · nothing to do today", "green"),
            unsafe_allow_html=True)


def _month_section(all_pos, settings) -> None:
    """Her requirement, verbatim: tracking separated by months, each month its
    own trades, monthly profit easy to see."""
    from src.engine import positions as pos_mod

    theme.section("One month at a time", "Monthly tracking")
    summaries = pos_mod.monthly_summary(all_pos)
    names = [m["label"] for m in summaries]
    by_label = {m["label"]: m for m in summaries}
    if st.session_state.get("trades_month_pick") not in names:
        st.session_state.pop("trades_month_pick", None)
    pick = st.selectbox("Month", names, key="trades_month_pick")
    entry = by_label[pick]

    monthly_goal = float(settings["targets"]["monthly"])
    bp_limit = float(settings["risk_limits"]["monthly_bp_limit"])
    components.render_month_summary(entry, monthly_goal, bp_limit)

    if entry["rows"]:
        st.dataframe(components.month_trades_dataframe(entry["rows"]),
                     width="stretch", hide_index=True,
                     column_config=components.month_trades_column_config())
    else:
        theme.note("No trades touched this month yet. Log one with ➕ Quick Log "
                   "above, or build one in 🎯 Build.")

    components.render_month_bars(summaries, monthly_goal)


def _tab_trades(settings, strategies, provider) -> None:
    from src.engine import positions as pos_mod

    theme.section("Every logged trade, tracked against your own exit rules", "My trades")

    top = st.columns([1, 6])
    if top[0].button("↻ Refresh", key="trades_refresh"):
        st.session_state.pop("trades_rows", None)
        st.session_state.pop("_priced_positions", None)

    flash = st.session_state.pop("ql_flash", None)
    if flash:
        st.success(flash)

    _quick_log_form(settings, strategies, provider)

    header, rows, source = _load_trade_log()

    all_pos = pos_mod.parse_rows(header, rows)
    open_pos = pos_mod.open_positions(all_pos)
    closed = pos_mod.closed_positions(all_pos)
    legacy = [p for p in all_pos if p.status == "legacy"]
    bp_used = pos_mod.bp_in_use(all_pos)
    st.session_state["open_bp_in_use"] = bp_used

    if not all_pos:
        theme.note("Nothing here yet. Two ways to log your first trade: use "
                   "**➕ Quick Log** above for a trade you already placed in "
                   "thinkorswim, or press **Log this trade** in 🎯 Build when the "
                   "app finds the setup for you. Either way it lands here and the "
                   "app starts watching your exit rules: take the win at 50% of "
                   "the credit, at 21 days to expiration close or roll for a credit, "
                   "stop the loss at 2x the credit.")
        if source == "local" and not rows:
            from src.logging_tools import webhook_logger
            if webhook_logger.is_configured():
                st.info("Your Google Sheet link is saved, but the log could not be read "
                        "back. That usually means the sheet still runs the older script - "
                        "paste the updated **LogTrade.gs** (in the google_apps_script "
                        "folder) into Apps Script, then Deploy → Manage deployments → "
                        "Edit → New version → Deploy.")
        st.divider()
        _month_section(all_pos, settings)   # current month, zeros - shows the shape
        return

    if source == "local":
        theme.note("Reading the **local backup log** on this device. To track trades "
                   "everywhere, connect your Google Sheet in the **⚙️ Settings** tab "
                   "(one-time, ~2 minutes).")

    # ---------------- open positions, priced live
    theme.note(f"**{len(open_pos)} open** · {len(closed)} closed"
               + (f" · {len(legacy)} from before tracking" if legacy else ""))
    items = []
    if open_pos:
        items, priced_at = _price_positions(open_pos, provider, strategies)

        _today_card(items)
        theme.note(f"Prices checked at **{priced_at}** - they refresh on their own every "
                   "few minutes, or press ↻ Refresh.")

        st.dataframe(components.positions_dataframe(items), width="stretch",
                     hide_index=True, column_config=components.positions_column_config())

        # ---- one position in detail + the close flow
        st.divider()
        labels = {
            f"{it['position'].underlying} · {it['position'].strategy_name}"
            f" · opened {it['position'].opened}": it for it in items}
        pick = st.selectbox("Look at one trade", list(labels.keys()), key="trades_pick")
        it = labels[pick]
        p, live, sig = it["position"], it["live"], it["signal"]
        with st.container(border=True):
            components.render_exit_signal(sig)
            cols = st.columns(5)
            px = live.get("underlying_price")
            cols[0].metric(f"{p.underlying} now",
                           f"${px:,.2f}" if px else "n/a",
                           help="The underlying's price right now, about 15 minutes "
                                "delayed. This is what decides whether your strikes "
                                "are safe.")
            cols[1].metric("Credit received", money(p.credit),
                           help="What the short call paid you - the basis for your "
                                "50% target." if p.is_debit else None)
            cols[2].metric("Costs to close now",
                           money(live["cost_to_close"]) if live.get("cost_to_close")
                           is not None else "n/a",
                           help="Buying back the short call alone." if p.is_debit
                                else None)
            dte_now = p.dte_left()
            cols[3].metric("Days left", dte_now if dte_now is not None else "n/a")
            cols[4].metric("Max loss", money(p.max_loss))

            if p.is_debit:
                components.render_debit_position_card(p, live)

            # The single most useful read for a beginner: where is price, versus
            # the option she SOLD, and how much room is between them.
            cushion = pos_mod.strike_cushion(p, px)
            if cushion:
                side = "call" if cushion["option_type"] == "call" else "put"
                direction = "rise" if side == "call" else "fall"
                if cushion["breached"]:
                    theme.note(
                        f"**{p.underlying} is at \\${px:,.2f}, past the {cushion['strike']:g} "
                        f"{side} you sold.** That strike is breached - your SOP says roll "
                        f"{'up' if side == 'call' else 'down'} and out for a credit, or close.")
                else:
                    theme.note(
                        f"**{p.underlying} is at \\${px:,.2f}.** The closest option you sold "
                        f"is the **{cushion['strike']:g} {side}** - price would have to "
                        f"{direction} **{abs(cushion['room_pct']) * 100:.1f}%** to reach it. "
                        f"Your SOP says think about rolling once that room drops under 1.5%.")

            target_pct = float(_exit_cfg_for(p, strategies).get("profit_target_pct", 50) or 50)
            if sig.profit_pct is not None and p.credit > 0:
                if sig.profit_pct >= 0:
                    st.progress(min(sig.profit_pct / target_pct, 1.0))
                    theme.note(f"You've kept **{sig.profit_pct:.0f}%** of the credit so far - "
                               f"your SOP takes the win at **{target_pct:.0f}%**.")
                else:
                    stop_mult = float(_exit_cfg_for(p, strategies).get("stop_loss_multiple", 2) or 2)
                    st.progress(0.0)
                    theme.note(f"Right now closing costs **more** than you collected "
                               f"({sig.profit_pct:.0f}% of the credit). Your stop-loss rule "
                               f"says close if that reaches **-{stop_mult * 100:.0f}%**.")
            if p.legs:
                strikes = " / ".join(f"{leg.strike:g}" for leg in p.legs)
                theme.note(f"Legs: **{strikes}** · {p.contracts} contract(s)"
                           + (f" · expires {p.expiration}" if p.expiration else ""))

            if p.is_debit:
                _roll_form(p, live, provider)

            with st.expander("✔️ Close this trade (records the result)"):
                theme.note("Close it in thinkorswim first, then record the fill here so "
                           "your results stay accurate.")
                default_cost = float(live["cost_to_close"]) if live.get("cost_to_close") \
                    is not None else 0.0
                if p.is_debit:
                    # Closing a PMCC or covered call PAYS her - she sells the
                    # long side back. The old "what you paid" box could not go
                    # below zero, so a close that paid had nowhere to be typed.
                    default_in = live.get("position_value")
                    proceeds = st.number_input(
                        "What you RECEIVED when you closed it (total $, from your "
                        "TOS fill)",
                        min_value=0.0, step=25.0,
                        value=round(max(float(default_in or 0.0), 0.0), 2),
                        key=f"exit_in_{p.trade_id}",
                        help="Selling the LEAPS back, minus buying back the short "
                             "call - the net on your fill, x100 x contracts. A "
                             "50.00 credit on 1 contract = $5,000. If closing "
                             "somehow cost you money, type 0 and note it below.")
                    close_cash = float(proceeds)
                    exit_cost = 0.0
                else:
                    exit_cost = st.number_input(
                        "What you paid to close it (total $, from your TOS fill)",
                        min_value=0.0, value=round(max(default_cost, 0.0), 2), step=5.0,
                        key=f"exit_cost_{p.trade_id}")
                    close_cash = -float(exit_cost)
                reason = st.selectbox(
                    "Why you closed it",
                    ["Profit target (50%) hit", "21 DTE time exit",
                     "21 DTE credit roll (opened a new spread)", "Stop loss hit",
                     "Rolled to a new position", "Expired worthless", "Other"],
                    key=f"exit_reason_{p.trade_id}")
                note = st.text_input("Lesson learned (optional - future you says thanks)",
                                     key=f"exit_note_{p.trade_id}")
                # The close banks the capital result. Roll credits were banked on
                # the days they landed, so they are not counted again here.
                realized = p.open_cash + close_cash
                total = realized + p.roll_income
                if p.is_debit:
                    st.markdown(components._esc(
                        f"Result: **${total:,.0f}** "
                        f"({'profit' if total >= 0 else 'loss'}) - "
                        f"${-p.open_cash:,.0f} out, ${p.roll_income:,.0f} banked "
                        f"from rolls, ${close_cash:,.0f} back today."))
                else:
                    st.markdown(components._esc(
                        f"Result: **${realized:,.0f}** "
                        f"({'profit' if realized >= 0 else 'loss'})"))
                if st.button("Record the close", type="primary",
                             key=f"close_{p.trade_id}"):
                    from src.logging_tools.trade_logger import close_trade
                    dest, live_log = close_trade(p.trade_id, p.underlying, p.strategy_name,
                                                 exit_cost, realized, reason, note,
                                                 close_cash=close_cash)
                    st.session_state.pop("trades_rows", None)
                    st.session_state.pop("_priced_positions", None)
                    st.rerun()

            with st.expander("🗑️ Delete this trade (logged by mistake / just testing)"):
                _delete_control(p.trade_id,
                                f"{p.underlying} {p.strategy_name} opened {p.opened}",
                                key=f"open_{p.trade_id}")
    else:
        st.success("No open trades right now. Record one with ➕ Quick Log above, "
                   "or build one in 🎯 Build - it shows up here either way.")

    if legacy:
        with st.expander(f"Trades logged before tracking existed ({len(legacy)})"):
            theme.note("These were logged with an older version of the app, so they can't "
                       "be tracked live - shown for your records only.")
            import pandas as pd
            st.dataframe(pd.DataFrame([{
                "Date": p.opened, "Symbol": p.underlying, "Strategy": p.strategy_name,
                "Credit $": p.credit, "Notes": p.note} for p in legacy]),
                width="stretch", hide_index=True)

    # ---------------- month by month (her ask: monthly trades, monthly profit)
    st.divider()
    _month_section(all_pos, settings)

    # ---------------- results vs her goals
    st.divider()
    theme.section("Are you on pace for your goals?", "Your results")
    if closed:
        perf = pos_mod.performance(all_pos)
        components.render_results_dashboard(
            perf, settings["targets"], bp_used,
            float(settings["risk_limits"]["monthly_bp_limit"]))
        with st.expander(f"All closed trades ({len(closed)})"):
            st.dataframe(components.closed_dataframe(closed), width="stretch",
                         hide_index=True)
            deletable = [p for p in closed if p.trade_id]
            if deletable:
                st.divider()
                theme.note("Delete a closed trade you only entered as a test:")
                labels = {f"{p.underlying} · {p.strategy_name} · closed {p.closed_on}"
                          f" · result ${(p.realized_pl or 0):,.0f}": p for p in deletable}
                choice = st.selectbox("Closed trade to delete", list(labels.keys()),
                                      key="del_closed_pick")
                cp = labels[choice]
                _delete_control(cp.trade_id, choice, key=f"closed_{cp.trade_id}")
    else:
        theme.note("No closed trades yet - your results dashboard starts building the "
                   "first time you record a close. Remember: you are paper trading to "
                   "learn the **process**. Following your rules matters more than the "
                   "P&L right now.")
        if bp_used:
            limit = float(settings["risk_limits"]["monthly_bp_limit"])
            theme.note(f"Open trades are using **\\${bp_used:,.0f}** of your "
                       f"**\\${limit:,.0f}** monthly buying-power limit "
                       f"({bp_used / limit * 100:.0f}%).")


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


def _log_button(trade, strategy_name, size, passed, key: str) -> None:
    note = st.text_input("Note (optional)", key=f"note_{key}",
                         placeholder="e.g. VIX low, following the SOP")
    if st.button("Log this trade", key=f"log_{key}"):
        from src.logging_tools.trade_logger import log_trade
        dest, live, trade_id = log_trade(trade, strategy_name, size, passed, note)
        st.session_state.pop("trades_rows", None)   # My trades reloads fresh
        if live:
            st.success(f"Logged to your Google Sheet ✅ - now tracked in **📒 Trades**.  \n{dest}")
        else:
            st.success(f"Saved to the local log and tracked in **📒 Trades**.  \n{dest}")


# ------------------------------------------ settings (main tab + desktop sidebar)
def _data_mode_note(provider) -> None:
    text, tone = _mode_badge(provider)
    st.markdown(theme.chip(text, tone), unsafe_allow_html=True)
    if provider.mode == "demo":
        st.info("Offline - showing sample prices. Connect to the internet for real market "
                "data, or set up Schwab for true real-time.")
    elif provider.mode == "yahoo":
        theme.note("Real market data, ~15 minutes delayed - fine for 21-45 day trades.")


def _plan_metrics(settings, per_row: int = 2) -> None:
    acct, tgt, risk = settings["account"], settings["targets"], settings["risk_limits"]
    vals = [("Capital", money(acct["starting_capital"])),
            ("Monthly goal", money(tgt["monthly"])),
            ("Weekly goal", money(tgt["weekly"])),
            ("BP limit", money(risk["monthly_bp_limit"]))]
    cols = st.columns(per_row)
    for i, (label, v) in enumerate(vals):
        cols[i % per_row].metric(label, v)


def _tab_settings(settings, provider) -> None:
    """Everything that used to live only in the sidebar - which the phone app
    can't open. Connections, data status, and her plan numbers, in the main
    screen where they always work."""
    theme.section("Your connections and your plan - all in one place", "Settings")
    _data_mode_note(provider)

    st.markdown("#### 🔗 Where your trades log")
    from src.logging_tools import webhook_logger
    if not webhook_logger.is_configured():
        st.warning("Trades are saving **only on this device** right now - they won't reach "
                   "your Google Sheet or follow you between phone and computer until the "
                   "sheet is connected below.")
    _connect_sheet_ui(key_prefix="main")

    st.markdown("#### 📡 Data sources")
    _connect_earnings_ui(key_prefix="main")
    _connect_schwab_ui(provider, key_prefix="main")

    st.divider()
    st.markdown("#### 🎯 Your plan")
    _plan_metrics(settings, per_row=4)
    theme.note("These numbers come from `config/settings.yaml` - your capital, income goals, "
               "and the monthly buying-power limit every checklist enforces.")
    st.markdown(f"[📖 Open your Notion hub]({settings['notion']['hub_url']})")
    theme.note("You are paper trading to learn the process. Follow the rules, not the P&L.")


def _sidebar(settings, provider) -> None:
    with st.sidebar:
        st.markdown("### Trading Assistant")
        _data_mode_note(provider)
        st.divider()
        st.markdown("**Your plan**")
        _plan_metrics(settings, per_row=2)
        st.divider()
        _connect_schwab_ui(provider, key_prefix="sb")
        _connect_earnings_ui(key_prefix="sb")
        _connect_sheet_ui(key_prefix="sb")
        st.divider()
        st.markdown(f"[📖 Open your Notion hub]({settings['notion']['hub_url']})")
        theme.note("You are paper trading to learn the process. Follow the rules, not the P&L.")


def _connect_schwab_ui(provider, key_prefix: str = "main") -> None:
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


def _connect_earnings_ui(key_prefix: str = "main") -> None:
    """Paste a free Alpha Vantage key to pull years of earnings history (works on
    the hosted app, where Yahoo's earnings endpoint is blocked)."""
    from src.data import alphavantage_client as av
    connected = av.is_configured()
    label = "📈 Earnings history: connected ✅" if connected else "📈 Add earnings history (free)"
    with st.expander(label, expanded=False):
        theme.note("Gets years of expected-vs-delivered EPS for the Analyze tab. Yahoo blocks "
                   "this on the hosted app, so a free Alpha Vantage key fills it in.")
        current = av.get_key() or ""
        key = st.text_input("Alpha Vantage key", value=current, key=f"{key_prefix}_av_key",
                            type="password", placeholder="paste your key")
        if st.button("Save key", key=f"{key_prefix}_save_av"):
            if key.strip():
                av.set_key(key.strip())
                st.success("Saved. The earnings chart will now show years of history.")
            else:
                st.error("Paste your key first.")
        theme.note("Free key: alphavantage.co/support/#api-key. On the **hosted** app, also add "
                   "it under **Settings → Secrets** as:  alphavantage_key = \"YOUR_KEY\"")


def _connect_sheet_ui(key_prefix: str = "main") -> None:
    from src.logging_tools import webhook_logger
    connected = webhook_logger.is_configured()
    label = "🔗 Google Sheet: connected ✅" if connected else "🔗 Connect Google Sheet"
    with st.expander(label, expanded=not connected):
        theme.note("One-time setup. In your sheet: **Extensions → Apps Script**, paste the "
                   "script from the `google_apps_script` folder, **Deploy → Web app** "
                   "(access: Anyone), then paste the link it gives you here.")
        if connected:
            theme.note("**Keep the script updated (v7).** The script keeps your full trade "
                       "log in the **Options Assistant Log** tab - the app's month view and "
                       "tracking read from there, and delete works from the app too. The old "
                       "Hebrew-format **App Trades** tab is retired: the app no longer writes "
                       "to it, so it stays frozen as an archive (you can hide it). If you ever "
                       "need to update the script: paste the new `LogTrade.gs` over the old "
                       "one, then **Deploy → Manage deployments → ✏️ Edit → Version: New "
                       "version → Deploy**. Your link stays the same.")
        current = webhook_logger.get_url() or ""
        url = st.text_input("Web app link", value=current, key=f"{key_prefix}_webhook_url",
                            placeholder="https://script.google.com/macros/s/.../exec")
        c1, c2 = st.columns(2)
        if c1.button("Save link", key=f"{key_prefix}_save_webhook"):
            if url.strip().startswith("https://"):
                webhook_logger.set_url(url.strip())
                st.success("Saved. Your trades will now log to your Google Sheet.")
            else:
                st.error("That does not look like a link. It should start with https://")
        if connected and c2.button("Test it", key=f"{key_prefix}_test_webhook"):
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
