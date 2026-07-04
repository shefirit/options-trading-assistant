"""Options Trading Assistant - a free-navigation trading dashboard.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

Four tabs, all open at once - use them in any order, nothing is locked:

  📊 Market        - is today a good day to sell premium? (holiday-aware)
  🔎 Find premium  - screen names for the richest, safest option premium
  🔬 Analyze       - any stock/ETF/index: full picture + the strategy that fits it
  🎯 Build & check - pick a strategy, scan real setups, check your SOP rules, log it

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
def main() -> None:
    settings = load_settings()
    strategies = load_strategies()
    provider = get_provider()

    _sidebar(settings, provider)

    badge_tone = {"schwab": "green", "yahoo": "green", "demo": "amber"}[provider.mode]
    badge_text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
                  "demo": "● DEMO · sample data"}[provider.mode]
    theme.hero(
        "Options Trading Assistant",
        "Read the market, screen for premium, analyze a name, build and check the trade.",
        badge_text, badge_tone)

    ctx = provider.get_market_context(MARKET_READ_SYMBOL)

    t_market, t_prem, t_analyze, t_build = st.tabs(
        ["📊  Market", "🔎  Find premium", "🔬  Analyze a name", "🎯  Build & check"])
    with t_market:
        _tab_market(provider, ctx, strategies)
    with t_prem:
        _tab_premium(settings, provider)
    with t_analyze:
        _tab_analyze(settings, provider, strategies)
    with t_build:
        _tab_build(settings, strategies, provider)


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


def _tab_market(provider, ctx, strategies) -> None:
    import datetime as dt

    from src.data import market_calendar as cal
    from src.data.market_context import daily_sentiment

    today = dt.date.today()
    market_open = cal.is_market_open(today)

    tiles = provider.get_market_tiles()
    cols = st.columns(len(tiles))
    changes = []
    for col, t in zip(cols, tiles):
        price = f"{t['price']:,.0f}" if t["price"] else "n/a"
        delta = None if not market_open else (
            f"{t['change_pct']:+.2f}%" if t["change_pct"] is not None else None)
        label = "VIX (fear)" if t["symbol"] == "VIX" else t["symbol"]
        if t["symbol"] == "VIX":
            col.metric(label, price, delta, delta_color="inverse")
        else:
            col.metric(label, price, delta)
            changes.append(t["change_pct"])
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
        st.caption(sent_note)
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
        st.success("Loaded into **Build & check** - open that tab to scan it.")

    if not provider.is_real:
        st.info("You are offline, so these are sample numbers. Connect to the internet for real "
                "market data (or set up Schwab for true real-time).")


# ------------------------------------------------------------------ Find premium tab
def _tab_premium(settings, provider) -> None:
    from src.data import stock_universe

    theme.section("Which names pay the best premium - and are worth it?", "Premium finder")
    st.caption("For each name it prices the one-month put you'd sell (~0.30 delta) and lays out "
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
        st.caption("Press **Compare** to build the table.")
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
                st.success(f"Loaded {chosen} into **Analyze a name** - open that tab.")


# ------------------------------------------------------------------ Analyze tab
def _tab_analyze(settings, provider, strategies) -> None:
    theme.section("Analyze any name - and get the strategy that fits it", "Deep dive")
    opts = _symbol_options(settings)
    default = st.session_state.get("analyze_sym")
    idx = opts.index(default) if default in opts else None
    sym = st.selectbox("Symbol", opts, index=idx, key="analyze_sym",
                       placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...")
    if not sym:
        st.caption("Pick an index, ETF, or stock for its full picture and the strategy that "
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
                st.success("Loaded into **Build & check** - open that tab to scan and check it.")


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
        st.caption("Indexes have no earnings or fundamentals - the Market tab is your main guide "
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
        st.caption(f"👀 In thinkorswim: {strat.get('tos_hint', '')}")
        st.markdown(f"[📖 Read the full SOP in Notion]({strat['notion_url']})")
        if strat.get("warning"):
            st.warning(f"⚠️ {strat['warning']}")


def _tab_build(settings, strategies, provider) -> None:
    from src.data import stock_universe
    keys = list(strategies.keys())
    st.session_state.setdefault("build_strategy", keys[0])

    top = st.columns([2, 2])
    strategy_key = top[0].selectbox("Strategy", keys, key="build_strategy",
                                    format_func=lambda k: strategies[k]["name"])
    strat = strategies[strategy_key]
    allowed = allowed_underlyings_for(strategy_key)
    priority = [u for u in (settings["underlyings"]["us_style"] + stock_universe.FEATURED)
                if u in allowed]
    ordered = priority + [u for u in allowed if u not in priority]
    default_u = ["SPX"] if "SPX" in allowed else ordered[:1]
    st.session_state.setdefault("build_underlyings", default_u)
    if st.session_state.get("_prev_build_strategy") != strategy_key:
        st.session_state["_prev_build_strategy"] = strategy_key
        valid = [u for u in st.session_state["build_underlyings"] if u in ordered]
        st.session_state["build_underlyings"] = valid or default_u
    underlyings = top[1].multiselect("Underlying(s)", ordered, key="build_underlyings",
                                     help="Type to search. Pick more than one to scan together.")

    if strat.get("family") == "credit_spread":
        st.caption("ℹ️ Credit spreads use cash-settled **index** names only (SPX, NDX, RUT, XSP). "
                   "To trade a stock or ETF, pick **Cash Secured Put** or a **Covered Call**.")
    else:
        st.caption("Type any S&P 500 or Nasdaq-100 **stock** (AAPL, NVDA...) or an ETF "
                   "(SPY, QQQ, IWM, DIA). Want the recommended play for a name? Use **Analyze**.")

    uses_width = strat.get("family") == "credit_spread"
    row = st.columns([1, 1] if uses_width else [1, 2])
    contracts = row[0].number_input("Contracts", min_value=1, max_value=50, value=1, step=1)
    width = None
    if uses_width:
        width = row[1].number_input("Spread width ($)", min_value=1.0, max_value=200.0,
                                    value=25.0, step=1.0,
                                    help="Gap between your short and long strike.")

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
        _build_scan(strategy_key, strat, underlyings, provider, contracts, width)
    else:
        _build_manual(strategy_key, strat, underlyings)


def _build_scan(key, strat, underlyings, provider, contracts, width) -> None:
    if not scanner.can_scan(key):
        st.info("This strategy depends on the real shares you already own, so use **Check a "
                "trade I built myself** above to validate it against your SOP.")
        return
    if not underlyings:
        st.warning("Pick at least one underlying above.")
        return

    existing_bp = st.number_input("Buying power already used this month ($)", min_value=0.0,
                                  value=0.0, step=1000.0,
                                  help="So the monthly-limit check is realistic.")
    is_pmcc = strat.get("family") == "diagonal"
    st.caption(f"Shows up to 10 {strat['name']} setups - one per expiration across 21-44 days, "
               "each at the delta your SOP calls for."
               + (" (PMCC also picks a deep-in-the-money LEAPS.)" if is_pmcc else ""))

    if st.button("🔎 Scan the market now", type="primary"):
        found = []
        bar = st.progress(0.0, text="Reading option chains...")
        for i, u in enumerate(underlyings):
            try:
                chain = provider.get_chain(u)
                leaps = provider.get_leaps_chain(u) if is_pmcc else None
                found.extend(scanner.scan_setups(key, chain, width=width,
                                                 contracts=int(contracts), max_setups=10,
                                                 leaps_chain=leaps))
            except Exception as e:
                st.error(f"{u}: {e}")
            bar.progress((i + 1) / len(underlyings), text=f"Scanned {u} ({i+1}/{len(underlyings)})")
        bar.empty()
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
            st.caption("Press **Scan the market now** for a short list of the best setups.")
        return

    scanned_dtes = sorted({c.dte for c in candidates if c.dte is not None})
    st.success(f"Found {len(candidates)} setup(s) at your SOP delta, across "
               f"{', '.join(str(d) for d in scanned_dtes)} days to expiration.")
    st.dataframe(components.candidates_dataframe(candidates), width="stretch", hide_index=True)

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


def _build_manual(key, strat, underlyings) -> None:
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
            _log_button(checked, strat["name"], sizing.estimate(checked, strat),
                        report.passed, key="manual")


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
            st.caption(f"Analysts expect about \\${eps:.2f} earnings per share next report "
                       f"(range \\${earn_info.get('eps_low', eps):.2f}"
                       f" to \\${earn_info.get('eps_high', eps):.2f}).")

    with st.expander("🔬 Full checks: fundamentals, technicals, TradingView"):
        components.render_stock_analysis(analysis)
        if tv:
            st.divider()
            components.render_tv_ratings(tv)
    return analysis, earn_info


def _log_button(trade, strategy_name, size, passed, key: str) -> None:
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


# ------------------------------------------------------------------ sidebar
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
            "7. Restart the app - this will switch to **LIVE** automatically.")
        st.caption("Your keys stay on your PC. Full details are in the README.")


def _connect_sheet_ui() -> None:
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
