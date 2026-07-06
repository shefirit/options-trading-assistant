"""Options Trading Assistant - a free-navigation trading dashboard.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

Six tabs, all open at once - use them in any order, nothing is locked:

  📊 Market   - is today a good day to sell premium? (holiday-aware)
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


def _days_phrase(n) -> str:
    if n is None:
        return ""
    if n <= 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"in {n} days"


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
        "Read the market, screen for premium, analyze a name, build and check the trade.",
        [_mode_badge(provider), _log_badge()])

    t_market, t_prem, t_analyze, t_build, t_trades, t_settings = st.tabs(
        ["📊 Market", "🔎 Premium", "🔬 Analyze", "🎯 Build", "📒 Trades", "⚙️ Settings"])
    with t_market:
        _guard(_tab_market, provider, strategies)
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
    vix = ctx.vix
    big_soon = next((e for e in events
                     if e.kind in ("fomc", "jobs") and e.days_away is not None
                     and e.days_away <= 2), None)
    if vix is not None and vix >= 28:
        return ("Sit this one out", "red",
                f"Fear is high (VIX {vix:.0f}). Big, fast swings can blow right through your "
                "strikes. Premium sellers do best when things are calm - wait for the VIX to "
                "settle back down before selling new premium.")
    if big_soon is not None:
        return ("Trade carefully today", "amber",
                f"{big_soon.label} is {_days_phrase(big_soon.days_away)}. A surprise there can "
                "move the whole market. If you do trade, keep size small and deltas low - or "
                "wait until it has passed.")
    if vix is not None and vix >= 20:
        return ("Okay - but keep size small", "amber",
                f"Volatility is a bit elevated (VIX {vix:.0f}). Premiums are richer, but so are "
                "the swings. Fine to sell premium, just trade smaller and stay at low delta.")
    if vix is not None:
        return ("Good conditions to sell premium", "green",
                f"The market is calm (VIX {vix:.0f}) with no big event in the next couple of "
                "days. A comfortable backdrop for your 21-45 day premium-selling trades.")
    return ("Read the market before you trade", "amber",
            "Live volatility is unavailable right now, so check conditions yourself before "
            "selling premium.")


def _tab_market(provider, strategies) -> None:
    import datetime as dt

    from src.data import market_calendar as cal
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

    sent_label, sent_note = daily_sentiment(changes, ctx.vix)
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

    with st.expander("What's coming up (events that move the market)"):
        theme.note(sent_note)
        components.render_events(events)

    # Today's best index play, with a one-click handoff to Build.
    best_key = ctx.best_strategy_key or list(strategies.keys())[0]
    best_name = ctx.best_strategy_name or strategies[best_key]["name"]
    st.divider()
    c1, c2 = st.columns([5, 2])
    c1.info(f"💡 Today's market leans toward **{best_name}** on an index - {ctx.recommendation_reason}")
    if c2.button("Set this up in Build ▸", use_container_width=True, key="mkt_to_build"):
        st.session_state["build_strategy"] = best_key
        st.session_state["build_underlyings"] = ["SPX"]
        st.session_state["_prev_build_strategy"] = best_key
        st.success("Loaded into **🎯 Build** - open that tab to scan it.")

    if not provider.is_real:
        st.info("You are offline, so these are sample numbers. Connect to the internet for real "
                "market data (or set up Schwab for true real-time).")


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


def _tab_trades(settings, strategies, provider) -> None:
    from src.engine import positions as pos_mod

    theme.section("Every logged trade, tracked against your own exit rules", "My trades")

    top = st.columns([1, 6])
    if top[0].button("↻ Refresh", key="trades_refresh"):
        st.session_state.pop("trades_rows", None)
        st.session_state.pop("_priced_positions", None)
    header, rows, source = _load_trade_log()

    all_pos = pos_mod.parse_rows(header, rows)
    open_pos = pos_mod.open_positions(all_pos)
    closed = pos_mod.closed_positions(all_pos)
    legacy = [p for p in all_pos if p.status == "legacy"]
    bp_used = pos_mod.bp_in_use(all_pos)
    st.session_state["open_bp_in_use"] = bp_used

    if not all_pos:
        theme.note("Nothing here yet. When you press **Log this trade** in 🎯 Build, "
                   "the trade lands here and the app starts watching your exit rules for "
                   "it: take the win at 50% of the credit, close at 21 days to expiration, "
                   "stop the loss at 2x the credit.")
        if source == "local" and not rows:
            from src.logging_tools import webhook_logger
            if webhook_logger.is_configured():
                st.info("Your Google Sheet link is saved, but the log could not be read "
                        "back. That usually means the sheet still runs the older script - "
                        "paste the updated **LogTrade.gs** (in the google_apps_script "
                        "folder) into Apps Script, then Deploy → Manage deployments → "
                        "Edit → New version → Deploy.")
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

        needs_action = [it for it in items if it["signal"].action in ("stop", "time", "profit")]
        if needs_action:
            st.error(f"🔔 {len(needs_action)} trade(s) hit an exit rule today - see the "
                     "What to do column, then close them in thinkorswim.")
        else:
            st.success("No exit rule has triggered today.")
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
            cols = st.columns(4)
            cols[0].metric("Credit received", money(p.credit))
            cols[1].metric("Costs to close now",
                           money(live["cost_to_close"]) if live.get("cost_to_close")
                           is not None else "n/a")
            dte_now = p.dte_left()
            cols[2].metric("Days left", dte_now if dte_now is not None else "n/a")
            cols[3].metric("Max loss", money(p.max_loss))
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

            with st.expander("✔️ Close this trade (records the result)"):
                theme.note("Close it in thinkorswim first, then record the fill here so "
                           "your results stay accurate.")
                default_cost = float(live["cost_to_close"]) if live.get("cost_to_close") \
                    is not None else 0.0
                exit_cost = st.number_input(
                    "What you paid to close it (total $, from your TOS fill)",
                    min_value=0.0, value=round(max(default_cost, 0.0), 2), step=5.0,
                    key=f"exit_cost_{p.trade_id}")
                reason = st.selectbox(
                    "Why you closed it",
                    ["Profit target (50%) hit", "21 DTE time exit", "Stop loss hit",
                     "Rolled to a new position", "Expired worthless", "Other"],
                    key=f"exit_reason_{p.trade_id}")
                note = st.text_input("Lesson learned (optional - future you says thanks)",
                                     key=f"exit_note_{p.trade_id}")
                realized = p.credit - exit_cost
                st.markdown(components._esc(
                    f"Result: **${realized:,.0f}** "
                    f"({'profit' if realized >= 0 else 'loss'})"))
                if st.button("Record the close", type="primary",
                             key=f"close_{p.trade_id}"):
                    from src.logging_tools.trade_logger import close_trade
                    dest, live_log = close_trade(p.trade_id, p.underlying, p.strategy_name,
                                                 exit_cost, realized, reason, note)
                    st.session_state.pop("trades_rows", None)
                    st.rerun()

            with st.expander("🗑️ Delete this trade (logged by mistake / just testing)"):
                _delete_control(p.trade_id,
                                f"{p.underlying} {p.strategy_name} opened {p.opened}",
                                key=f"open_{p.trade_id}")
    else:
        st.success("No open trades right now. When you log one in **Build & check** it "
                   "shows up here.")

    if legacy:
        with st.expander(f"Trades logged before tracking existed ({len(legacy)})"):
            theme.note("These were logged with an older version of the app, so they can't "
                       "be tracked live - shown for your records only.")
            import pandas as pd
            st.dataframe(pd.DataFrame([{
                "Date": p.opened, "Symbol": p.underlying, "Strategy": p.strategy_name,
                "Credit $": p.credit, "Notes": p.note} for p in legacy]),
                width="stretch", hide_index=True)

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
            theme.note("**Keep the script updated (v7).** The newest script logs to a hidden "
                       "tracking tab, mirrors each trade into your **App Trades** tab, and "
                       "enables tracking + deleting. Paste the updated `LogTrade.gs` over the "
                       "old one, then **Deploy → Manage deployments → ✏️ Edit → Version: New "
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
