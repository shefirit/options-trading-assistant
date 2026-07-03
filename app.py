"""Options Trading Assistant - your friendly scanner + SOP checker.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

It reads today's market, tells you which strategy fits best, finds trades that
match your rules, and checks every trade against your SOP before you enter it in
thinkorswim. It never places trades and never gives buy/sell advice.
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
from src.engine.validator import validate_trade
from ui import components, theme

st.set_page_config(page_title="Options Trading Assistant", page_icon="📈", layout="wide")
theme.inject()

# Which underlying to read the overall market from (European index, matches spreads).
MARKET_READ_SYMBOL = "SPX"


@st.cache_resource
def get_provider() -> DataProvider:
    return DataProvider.create()


def money(x: float) -> str:
    return f"${x:,.0f}"


def main() -> None:
    settings = load_settings()
    strategies = load_strategies()
    provider = get_provider()
    keys = list(strategies.keys())

    _sidebar(settings, provider)

    badge_tone = {"schwab": "green", "yahoo": "green", "demo": "amber"}[provider.mode]
    badge_text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
                  "demo": "● DEMO · sample data"}[provider.mode]
    theme.hero(
        "Options Trading Assistant",
        "Read the market, pick the strategy, check your rules - then trade in thinkorswim.",
        badge_text, badge_tone,
    )

    ctx = provider.get_market_context(MARKET_READ_SYMBOL)
    best_key = ctx.best_strategy_key or keys[0]
    best_name = ctx.best_strategy_name or strategies[best_key]["name"]

    # Slim market context, always visible above the tabs.
    _market_bar(provider, ctx)

    # Three clear jobs: find something, analyze one name, or build the trade.
    tab_find, tab_analyze, tab_build = st.tabs(
        ["🔍  Find a trade", "🔬  Analyze a symbol", "🎯  Build & check"])
    with tab_find:
        _find_tab(settings, provider, best_key, best_name, ctx)
    with tab_analyze:
        _symbol_advisor_section(settings, provider)
    with tab_build:
        _build_tab(settings, strategies, provider, keys, best_key)


def _market_bar(provider, ctx) -> None:
    """A slim strip of market context (indexes, sentiment, next event) above the tabs."""
    from src.data.market_context import daily_sentiment
    tiles = provider.get_market_tiles()
    cols = st.columns(len(tiles))
    changes = []
    for col, t in zip(cols, tiles):
        price = f"{t['price']:,.0f}" if t["price"] else "n/a"
        delta = f"{t['change_pct']:+.2f}%" if t["change_pct"] is not None else None
        if t["symbol"] == "VIX":
            col.metric("VIX (fear)", price, delta, delta_color="inverse")
        else:
            col.metric(t["symbol"], price, delta)
        if t["symbol"] != "VIX":
            changes.append(t["change_pct"])

    sent_label, sent_note = daily_sentiment(changes, ctx.vix)
    low = sent_label.lower()
    sent_tone = "green" if "positive" in low else "red" if "negative" in low else "amber"
    events = provider.get_macro_events(trade_dte=35)
    nxt = events[0] if events else None
    bits = [theme.chip(f"Sentiment: {sent_label}", sent_tone),
            theme.chip(f"Trend: {ctx.trend.title()}", "indigo")]
    if nxt:
        bits.append(theme.chip(f"Next: {nxt.label} · in {nxt.days_away}d",
                               "amber" if nxt.in_window else "neutral"))
    st.markdown(" ".join(bits), unsafe_allow_html=True)
    with st.expander("Market detail & upcoming events"):
        st.caption(f"{sent_note} {ctx.summary.split('. ', 1)[-1]}")
        components.render_events(events)
    st.write("")


def _find_tab(settings, provider, best_key, best_name, ctx) -> None:
    st.markdown("Two ways in: take today's best **index** play, or screen **stocks/ETFs** "
                "for the richest premium.")
    rec1, rec2 = st.columns([5, 1])
    rec1.info(f"💡 Today's best index play: **{best_name}** - {ctx.recommendation_reason}")
    if rec2.button("Use it ▸", key="use_rec_btn"):
        st.session_state["strategy_widget"] = best_key
        st.session_state.pop("underlyings_widget", None)   # reset to the index default
        st.success("Loaded into **Build & check** - open that tab to scan it.")
    st.divider()
    _premium_finder_section(settings, provider)


def _build_tab(settings, strategies, provider, keys, best_key) -> None:
    if "strategy_widget" not in st.session_state:
        st.session_state["strategy_widget"] = best_key

    top = st.columns([2, 2])
    with top[0]:
        strategy_key = st.selectbox("Strategy", keys, key="strategy_widget",
                                    format_func=lambda k: strategies[k]["name"])
    strat = strategies[strategy_key]
    allowed = allowed_underlyings_for(strategy_key)
    from src.data import stock_universe
    priority = [u for u in (settings["underlyings"]["us_style"] + stock_universe.FEATURED)
                if u in allowed]
    ordered = priority + [u for u in allowed if u not in priority]
    default_u = ["SPX"] if "SPX" in allowed else ordered[:1]
    # Initialize once; after that YOU control the list (including clearing it).
    if "underlyings_widget" not in st.session_state:
        st.session_state["underlyings_widget"] = default_u
    # Only when the strategy changes do we drop picks that no longer fit it - so
    # removing an underlying yourself always sticks.
    if st.session_state.get("_prev_strategy") != strategy_key:
        st.session_state["_prev_strategy"] = strategy_key
        valid = [u for u in st.session_state["underlyings_widget"] if u in ordered]
        st.session_state["underlyings_widget"] = valid or default_u
    with top[1]:
        underlyings = st.multiselect(
            "Underlying(s)", ordered, key="underlyings_widget",
            help="Type to search.")
    if strat.get("family") == "credit_spread":
        st.caption("ℹ️ Credit spreads use cash-settled **index** names only (SPX, NDX, RUT, XSP) "
                   "- no early-assignment risk. To trade a **stock or ETF**, pick **Cash Secured "
                   "Put** or a **Covered Call** in the Strategy box above.")
    else:
        st.caption("Type any S&P 500 or Nasdaq-100 **stock** (AAPL, NVDA, TSLA...) or an ETF "
                   "(SPY, QQQ, IWM, DIA).")

    picked_stocks = [u for u in underlyings if stock_universe.is_stock(u)]
    if picked_stocks:
        _stock_analysis_section(picked_stocks, provider)

    # Spread width only matters when the trade HAS a spread - credit spreads / condors.
    uses_width = strat.get("family") == "credit_spread"
    row = st.columns([2, 1, 1] if uses_width else [3, 1])
    target_dte = row[0].slider("Days to expiration (DTE)", min_value=21, max_value=44, value=30,
                               help="Used when you check a trade you built yourself. The "
                                    "scanner shows a few expirations across 21-44 days for you.")
    contracts = row[1].number_input("Contracts", min_value=1, max_value=50, value=1, step=1,
                                    help="How many copies of the whole position. Default is 1.")
    width = None
    if uses_width:
        width = row[2].number_input("Spread width ($)", min_value=1.0, max_value=200.0,
                                    value=25.0, step=1.0,
                                    help="Gap between your short and long strike.")

    # If any setting changes, old scan results no longer apply - clear them.
    scan_sig = (strategy_key, tuple(underlyings), int(target_dte), int(contracts), width)
    if st.session_state.get("scan_sig") != scan_sig:
        st.session_state["scan_sig"] = scan_sig
        st.session_state.pop("candidates", None)
        st.session_state.pop("checked_trade", None)

    with st.expander(f"ℹ️ About {strat['name']}", expanded=False):
        st.markdown(f"**What it is:** {strat['plain_english']}")
        c = st.columns(2)
        c[0].markdown(f"**Market outlook:** {strat.get('market_outlook', '-')}")
        c[1].markdown(f"**Difficulty:** {strat.get('difficulty', '-')}")
        st.caption(f"👀 In thinkorswim: {strat.get('tos_hint', '')}")
        st.markdown(f"[📖 Read the full SOP in Notion]({strat['notion_url']})")
        if strat.get("warning"):
            st.warning(f"⚠️ {strat['warning']}")

    st.divider()
    mode = st.radio("How", ["🔎 Find setups for me", "✅ Check a trade I built"],
                    horizontal=True, label_visibility="collapsed", key="build_mode")
    if mode.startswith("🔎"):
        _scanner_tab(strategy_key, strat, underlyings, provider, contracts, width, target_dte)
    else:
        _validator_tab(strategy_key, strat, underlyings, contracts, target_dte)


def _stock_analysis_section(symbols, provider) -> None:
    theme.section("Is this a good stock to trade?", "Stock check")
    if not provider.is_real:
        st.info("Stock analysis needs real data. Connect to the internet (or Schwab) and this "
                "will show the company's fundamentals and price trend.")
        return
    sym = symbols[0] if len(symbols) == 1 else st.selectbox(
        "Analyze which stock?", symbols, key="analyze_sym")
    with st.container(border=True):
        _stock_overview_block(sym, provider)


def _stock_overview_block(sym, provider, key_prefix="setup"):
    """The full stock overview (score card, chart, analysts, earnings). Reused
    by both the Symbol advisor and the setup flow. key_prefix keeps widget keys
    unique when the same stock shows in both places. Returns (analysis, earn_info).
    """
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

    # The at-a-glance overview (score card, price + range-selectable chart,
    # analyst bar, earnings beats). The loader lets the range buttons work.
    components.render_stock_overview(
        analysis, info,
        frame_loader=lambda period: provider.get_price_frame(sym, period),
        change_pct=change_pct, analysts=analysts, eps_history=eps_history,
        key_prefix=key_prefix)

    # Verdict + upcoming dates - the decision-relevant bits stay visible.
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
            # \$ stops markdown from reading the dollar amounts as math.
            st.caption(f"Analysts expect about \\${eps:.2f} earnings per share next report "
                       f"(range \\${earn_info.get('eps_low', eps):.2f}"
                       f" to \\${earn_info.get('eps_high', eps):.2f}).")

    # The full metric-by-metric detail, tucked away but one click deep.
    with st.expander("🔬 Full checks: fundamentals, technicals, TradingView"):
        components.render_stock_analysis(analysis)
        if tv:
            st.divider()
            components.render_tv_ratings(tv)
    return analysis, earn_info


def _premium_finder_section(settings, provider) -> None:
    """Compare symbols by how rich their option premiums are - helps choose
    which name to sell options on."""
    from src.data import stock_universe

    theme.section("Which names pay the best premium?", "Premium finder")
    st.caption("For each name it checks selling a one-month put (~0.30 delta, about a 70% "
               "chance of keeping the premium) and gives you a simple call: good to sell, "
               "okay, or skip.")

    if not provider.is_real:
        st.info("The premium finder needs real market data - connect to the internet first.")
        return

    etfs = settings["underlyings"]["us_style"]
    options = list(dict.fromkeys(etfs + stock_universe.FEATURED + stock_universe.all_stocks()))
    picks = st.multiselect(
        "Compare a few names", options,
        default=[s for s in ["AAPL", "NVDA", "SPY"] if s in options],
        max_selections=12,
        help="Start with 2-4 names. You can add more, but fewer is easier to compare.")

    monthly_bp = float(settings["risk_limits"]["monthly_bp_limit"])
    if st.button("Compare", type="primary", key="premium_scan"):
        if not picks:
            st.warning("Pick at least one symbol.")
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
        st.caption("Press **Compare** for a simple good-to-sell / okay / skip on each name.")
        return

    # Verdict-first cards, best call first.
    components.render_premium_cards(snaps)

    # Progressive disclosure: full plan + advisor handoff only when asked.
    valid = [s for s in snaps if not s.error]
    if valid:
        with st.expander("See the full plan for one of these"):
            chosen = st.selectbox(
                "Which one?", [s.symbol for s in valid], key="premium_detail_sym")
            detail = next(s for s in valid if s.symbol == chosen)
            components.render_premium_detail(detail)
            if st.button(f"Analyze {chosen} in the Symbol advisor", key="finder_to_advisor"):
                st.session_state["advisor_sym"] = chosen
                st.rerun()
            if st.session_state.get("advisor_sym") == chosen:
                st.success(f"Loaded {chosen} into the **Analyze a symbol** tab - open it for "
                           "its grade, chart, and earnings check.")


def _symbol_advisor_section(settings, provider) -> None:
    """Pick ANY symbol -> full analysis -> which of her strategies fits, and why."""
    from src.data import stock_universe
    from src.engine.strategy_advisor import advise

    theme.section("Pick a symbol, get a plan", "Symbol advisor")
    european = list(settings["underlyings"]["european_style"])
    etfs = list(settings["underlyings"]["us_style"])
    options = european + etfs + [s for s in stock_universe.all_stocks() if s not in etfs]

    sym = st.selectbox(
        "Symbol", options, index=None, key="advisor_sym",
        placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...",
        help="Indexes (SPX, NDX...) map to credit spreads; ETFs and stocks map to "
             "cash secured puts, covered calls, and PMCC.")
    if not sym:
        st.caption("Choose an index, ETF, or stock and the app will analyze it and "
                   "recommend the right monthly options strategy from your playbook.")
        return
    if not provider.is_real:
        st.info("The advisor needs real market data - connect to the internet first.")
        return

    kind = "index" if sym in european else "etf" if sym in etfs else "stock"
    with st.container(border=True):
        analysis, earn_info = None, {}
        if kind == "index":
            price, chg = provider.get_price_change(sym)
            c1, c2 = st.columns([1, 3])
            c1.metric(sym, f"{price:,.0f}" if price else "n/a",
                      f"{chg:+.2f}%" if chg is not None else None)
            tv = provider.get_tradingview(sym, is_index=True)
            with c2:
                if tv:
                    components.render_tv_ratings(tv, title=f"TradingView on {sym}")
        else:
            analysis, earn_info = _stock_overview_block(sym, provider, key_prefix="advisor")
            if analysis is None:
                return
            tv = provider.get_tradingview(sym)

        ctx = provider.get_market_context(sym)
        advice = advise(
            symbol=sym, kind=kind,
            price=(analysis.price if analysis else ctx.price),
            trend=ctx.trend, vix=ctx.vix, tv=tv, analysis=analysis,
            earnings_date=earn_info.get("earnings_date") if kind == "stock" else None,
            capital=float(settings["account"]["starting_capital"]),
            monthly_bp=float(settings["risk_limits"]["monthly_bp_limit"]),
        )
        st.divider()
        components.render_advice(advice)
        if advice.primary and st.button(
                f"Use this plan: {advice.primary.name} on {sym}",
                type="primary", key="use_plan_btn"):
            st.session_state["strategy_widget"] = advice.primary.key
            st.session_state["underlyings_widget"] = [sym]
            st.session_state["plan_loaded"] = sym
        if st.session_state.get("plan_loaded") == sym:
            st.success(f"Loaded {advice.primary.name if advice.primary else ''} on {sym} into "
                       "the **Build & check** tab - open it to scan and place the trade.")


def _sidebar(settings, provider) -> None:
    with st.sidebar:
        st.markdown("### Trading Assistant")
        tone = {"schwab": "green", "yahoo": "green", "demo": "amber"}[provider.mode]
        text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
                "demo": "● DEMO · sample data"}[provider.mode]
        st.markdown(theme.chip(text, tone), unsafe_allow_html=True)
        if provider.mode == "demo":
            st.info("Offline - showing sample prices. Connect to the internet for real market "
                    "data, or set up Schwab for true real-time.")
        elif provider.mode == "yahoo":
            st.caption("Real market data, ~15 minutes delayed - fine for 21-45 day trades.")
        st.divider()
        st.markdown("**Your plan**")
        acct, tgt, risk = settings["account"], settings["targets"], settings["risk_limits"]
        a, b = st.columns(2)
        a.metric("Capital", money(acct["starting_capital"]))
        b.metric("Monthly goal", money(tgt["monthly"]))
        a.metric("Weekly goal", money(tgt["weekly"]))
        b.metric("BP limit", money(risk["monthly_bp_limit"]))
        st.divider()
        _connect_schwab_ui(provider)
        _connect_sheet_ui()
        st.divider()
        st.markdown(f"[📖 Open your Notion hub]({settings['notion']['hub_url']})")
        st.caption("You are paper trading to learn the process. Follow the rules, not the P&L.")


def _connect_schwab_ui(provider) -> None:
    """Steps to upgrade from delayed Yahoo data to real-time Schwab data."""
    live = provider.mode == "schwab"
    label = "⚡ Schwab: connected ✅" if live else "⚡ Connect Schwab (real-time)"
    with st.expander(label, expanded=False):
        if live:
            st.success("You are on real-time Schwab data.")
            return
        st.caption("Right now you have real Yahoo data (~15 min delayed), which is fine for "
                   "your trades. To get true real-time from your own account:")
        st.markdown(
            "1. Go to **developer.schwab.com** and sign in with your Schwab login.\n"
            "2. Create an app - choose **Trader API - Individual**.\n"
            "3. Set the callback URL to **https://127.0.0.1:8182**\n"
            "4. Wait for the app status to become **Ready for Use** (can take a few days).\n"
            "5. Copy `.env.example` to `.env` and paste in your **App Key** and **App Secret**.\n"
            "6. Run once in a terminal: `python -m src.data.schwab_client` (a browser opens to "
            "log in).\n"
            "7. Restart the app - this will switch to **LIVE** automatically."
        )
        st.caption("Your keys stay on your PC. Full details are in the README.")


def _connect_sheet_ui() -> None:
    """Paste-one-link setup for Google Sheet logging (Apps Script web app)."""
    from src.logging_tools import webhook_logger
    connected = webhook_logger.is_configured()
    label = "🔗 Google Sheet: connected ✅" if connected else "🔗 Connect Google Sheet"
    with st.expander(label, expanded=not connected):
        st.caption("One-time setup. In your sheet: **Extensions → Apps Script**, paste the "
                   "script from the `google_apps_script` folder, **Deploy → Web app** "
                   "(access: Anyone), then paste the link it gives you here.")
        current = webhook_logger.get_url() or ""
        url = st.text_input("Web app link", value=current, key="webhook_url_input",
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


def _test_sheet_connection() -> None:
    """Send a harmless test row so Rita can confirm logging works end to end."""
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


def _scanner_tab(key, strat, underlyings, provider, contracts, width, target_dte) -> None:
    if not scanner.can_scan(key):
        st.info("This strategy depends on your real share position, so for now check it in the "
                "**Check a trade I built** tab. Automatic scanning for it comes later.")
        return
    if not underlyings:
        st.warning("Pick at least one underlying above.")
        return

    existing_bp = st.number_input("Buying power already used this month ($)", min_value=0.0,
                                  value=0.0, step=1000.0,
                                  help="So the monthly-limit check is realistic.")

    is_pmcc = strat.get("family") == "diagonal"
    st.caption(f"Shows up to 10 {strat['name']} setups - one per expiration, sorted by days to "
               "expiration, each at the delta your SOP calls for."
               + (" (PMCC also picks a deep-in-the-money LEAPS as the stock stand-in.)"
                  if is_pmcc else ""))
    if st.button("Scan the market now", type="primary"):
        found = []
        for u in underlyings:
            try:
                chain = provider.get_chain(u)
                leaps = provider.get_leaps_chain(u) if is_pmcc else None
                found.extend(scanner.scan_setups(key, chain, width=width, contracts=int(contracts),
                                                 max_setups=10, leaps_chain=leaps))
            except Exception as e:
                st.error(f"{u}: {e}")
        found.sort(key=lambda c: (c.trade.underlying, c.dte if c.dte is not None else 0))
        st.session_state["candidates"] = found

    candidates = st.session_state.get("candidates", [])
    if not candidates:
        if "candidates" in st.session_state:   # a scan ran but found nothing
            fam = strat.get("family")
            if fam == "covered_call":
                st.info("No setups found. A covered call needs 100 shares, and for these names "
                        "that may cost more than your monthly buying-power limit. Try a "
                        "lower-priced stock, or use a Poor Man's Covered Call.")
            elif is_pmcc:
                st.info("No setups found. PMCC needs long-dated LEAPS options, which some names "
                        "do not offer (and demo mode has none). Try a large, liquid stock in "
                        "real-data mode.")
            else:
                st.info("No setups found for these names right now.")
        else:
            st.caption("Press **Scan the market now** for a short list of the best setups.")
        return

    scanned_dtes = sorted({c.dte for c in candidates if c.dte is not None})
    st.success(f"Found {len(candidates)} setup(s) at your SOP delta, across "
               f"{', '.join(str(d) for d in scanned_dtes)} days to expiration.")

    st.dataframe(components.candidates_dataframe(candidates), width="stretch", hide_index=True)
    with st.expander("🎓 What do these columns mean?"):
        st.markdown(
            "- **DTE** - days to expiration. You get a few, spread from 21 to 44 days.\n"
            "- **Short Δ (delta)** - roughly the chance the option you SOLD finishes in the "
            "money. Each setup is at the delta your SOP aims for.\n"
            "- **Credit $** - cash you collect up front.\n"
            "- **Max loss $** - the worst case if the trade goes fully against you.\n"
            "- **Return/Risk** - credit divided by max loss. Higher = richer premium for the "
            "risk taken."
        )

    pick = st.number_input("Look at trade #", min_value=1, max_value=len(candidates),
                           value=1, step=1)
    chosen = candidates[int(pick) - 1]

    with st.container(border=True):
        if not chosen.fits_sop:
            st.warning(f"⚠️ {chosen.note}")
        st.markdown("**Leg-by-leg (build it this way in thinkorswim):**")
        st.dataframe(components.candidate_leg_detail(chosen), width="stretch", hide_index=True)
        st.markdown("**Your SOP checklist:**")
        report = validate_trade(chosen.trade, existing_month_bp=existing_bp)
        components.render_checklist(report)
        _log_button(chosen.trade, strat["name"],
                    {"credit": chosen.credit, "max_loss": chosen.max_loss,
                     "buying_power": chosen.buying_power}, report.passed, key="scan")


def _validator_tab(key, strat, underlyings, contracts, target_dte) -> None:
    st.caption("Type in the trade exactly as you set it up in thinkorswim. "
               "Long = you bought it (+), short = you sold it (-).")
    underlying = st.selectbox("Underlying", underlyings or allowed_underlyings_for(key),
                              key="val_underlying")
    existing_bp = st.number_input("Buying power already used this month ($)", min_value=0.0,
                                  value=0.0, step=1000.0, key="val_bp")

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
        dte = cols[3].number_input("DTE", min_value=0, max_value=1000, value=int(target_dte),
                                   step=1, key=f"dte_{i}")
        legs.append(Leg(role=role, action=action, option_type=opt_type, strike=strike,
                        delta=delta, premium=premium, quantity=qty, dte=int(dte)))

    if st.button("Check this trade", type="primary"):
        trade = Trade(strategy_key=key, underlying=underlying, contracts=int(contracts), legs=legs)
        # Remember the checked trade so the checklist stays visible after any
        # button press (Streamlit reruns the page on every click).
        st.session_state["checked_trade"] = trade

    checked = st.session_state.get("checked_trade")
    if checked is not None:
        report = validate_trade(checked, existing_month_bp=existing_bp)
        with st.container(border=True):
            components.render_checklist(report)
            from src.engine import sizing
            _log_button(checked, strat["name"], sizing.estimate(checked, strat),
                        report.passed, key="manual")


def _log_button(trade, strategy_name, size, passed, key: str) -> None:
    # Keys must be stable across page reruns, or the click gets lost.
    note = st.text_input("Note (optional)", key=f"note_{key}",
                         placeholder="e.g. VIX low, following the SOP")
    if st.button("Log this trade", key=f"log_{key}"):
        from src.logging_tools.trade_logger import log_trade
        dest, live = log_trade(trade, strategy_name, size, passed, note)
        if live:
            st.success(f"Logged to your Google Sheet ✅  \n{dest}")
        else:
            st.success(f"Saved to {dest}. (Local backup - connect Google Sheets to log online. "
                       "See the README.)")


if __name__ == "__main__":
    main()
