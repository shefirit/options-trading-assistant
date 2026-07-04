"""Options Trading Assistant - a guided, explore-first options helper.

Run it with:  streamlit run app.py   (or double-click run_app.bat)

Two ways to use it, chosen with the toggle up top:

  🧭 Place a trade - a guided flow, one decision per step, in order:
       1 Market   - should I even trade today?
       2 Name     - pick or find a stock/ETF/index, and see if it's good
       3 Strategy - the app recommends the play that fits that name
       4 Setup    - the exact trade at your SOP delta and DTE
       5 Check    - does it pass your rules? -> log it

  🔎 Explore names - free research: screen for premium, look up any name's
       full analysis and the strategy that fits it, then jump into placing it.

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

# Which underlying to read the overall market from (European index, matches spreads).
MARKET_READ_SYMBOL = "SPX"

MODE_TRADE = "🧭  Place a trade"
MODE_EXPLORE = "🔎  Explore names"

# The five steps: (number, short label, the question it answers).
STEPS = [
    (1, "Market", "Should I trade today?"),
    (2, "Name", "Which name - and is it good?"),
    (3, "Strategy", "What's the best play for it?"),
    (4, "Setup", "What is the exact trade?"),
    (5, "Check + Log", "Am I following my rules?"),
]


@st.cache_resource
def get_provider() -> DataProvider:
    return DataProvider.create()


def money(x: float) -> str:
    return f"${x:,.0f}"


# ------------------------------------------------------------------ small helpers
def _classify(sym: str, settings) -> str:
    """index (European, cash-settled) | etf (US-style) | stock."""
    if sym in settings["underlyings"]["european_style"]:
        return "index"
    if sym in settings["underlyings"]["us_style"]:
        return "etf"
    return "stock"


def _symbol_options(settings) -> list:
    """Everything you can pick: European indexes + ETFs + the stock universe."""
    from src.data import stock_universe
    european = list(settings["underlyings"]["european_style"])
    etfs = list(settings["underlyings"]["us_style"])
    return list(dict.fromkeys(
        european + etfs + stock_universe.FEATURED + stock_universe.all_stocks()))


def _strategies_for_symbol(sym: str, strategies) -> list:
    """Which of your strategies are allowed on this symbol (from the config rules)."""
    return [k for k in strategies if sym in allowed_underlyings_for(k)]


def _compute_advice(sym: str, kind: str, provider, settings):
    """Full per-symbol recommendation: which strategy fits this name, and why."""
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


# ------------------------------------------------------------------ navigation
def _seed_widget(widget_key: str, store_key: str, default) -> None:
    """Seed a widget's value from its persistent mirror the first time it appears
    (e.g. after navigating back). Streamlit garbage-collects a widget's own key
    once it stops rendering, so selections are mirrored into a plain store key that
    survives. Seeds only when the widget key is absent, so live edits aren't lost."""
    if widget_key not in st.session_state:
        st.session_state[widget_key] = st.session_state.get(store_key, default)


def _goto(n: int) -> None:
    st.session_state["step"] = n
    st.session_state["max_step"] = max(st.session_state.get("max_step", 1), n)
    st.rerun()


def _stepper(current: int) -> None:
    """A clickable progress bar. Jump to any step you've reached; steps ahead are locked."""
    reached = st.session_state.get("max_step", 1)
    cols = st.columns(len(STEPS))
    for col, (n, label, _) in zip(cols, STEPS):
        mark = "✓" if n < current else str(n)
        if col.button(f"{mark}  {label}", key=f"nav_{n}", use_container_width=True,
                      type="primary" if n == current else "secondary",
                      disabled=n > reached):
            _goto(n)
    question = next(q for n, _, q in STEPS if n == current)
    st.markdown(
        f'<div class="ota-eyebrow">Step {current} of {len(STEPS)}</div>'
        f'<div class="ota-section-title">{question}</div>',
        unsafe_allow_html=True)


def _nav(current: int, can_continue: bool = True,
         continue_label: str = "Continue ▸", hint: str = "") -> None:
    st.write("")
    c1, c2, c3 = st.columns([1, 1.4, 3])
    if current > 1:
        if c1.button("◂ Back", key=f"back_{current}", use_container_width=True):
            _goto(current - 1)
    if current < len(STEPS):
        if c2.button(continue_label, key=f"cont_{current}", type="primary",
                     use_container_width=True, disabled=not can_continue):
            _goto(current + 1)
        if hint and not can_continue:
            c3.caption(hint)


# ------------------------------------------------------------------ main
def main() -> None:
    settings = load_settings()
    strategies = load_strategies()
    provider = get_provider()

    st.session_state.setdefault("step", 1)
    st.session_state.setdefault("max_step", 1)

    _sidebar(settings, provider)

    badge_tone = {"schwab": "green", "yahoo": "green", "demo": "amber"}[provider.mode]
    badge_text = {"schwab": "● LIVE · real-time", "yahoo": "● REAL · 15 min delayed",
                  "demo": "● DEMO · sample data"}[provider.mode]
    theme.hero(
        "Options Trading Assistant",
        "Read the market, find a good name, get the strategy that fits it, then check your rules.",
        badge_text, badge_tone)

    mode = st.segmented_control(
        "Mode", [MODE_TRADE, MODE_EXPLORE], default=MODE_TRADE, key="app_mode",
        label_visibility="collapsed")
    st.write("")

    if mode == MODE_EXPLORE:
        _explore(settings, provider, strategies)
        return

    ctx = provider.get_market_context(MARKET_READ_SYMBOL)
    _stepper(st.session_state["step"])
    st.write("")

    step = st.session_state["step"]
    if step == 1:
        _step_market(provider, ctx, strategies)
    elif step == 2:
        _step_name(settings, provider, strategies)
    elif step == 3:
        _step_strategy(settings, provider, strategies)
    elif step == 4:
        _step_setup(strategies, provider)
    elif step == 5:
        _step_check(strategies, provider)


# ------------------------------------------------------------------ Step 1: Market
def _days_phrase(n) -> str:
    if n is None:
        return ""
    if n <= 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"in {n} days"


def _trading_verdict(ctx, events):
    """A single clear call on whether today is a good day to sell premium.
    Returns (headline, tone, why). tone: green | amber | red."""
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
                "the swings. Fine to sell premium, just trade smaller than usual and stay at low "
                "delta.")
    if vix is not None:
        return ("Good conditions to sell premium", "green",
                f"The market is calm (VIX {vix:.0f}) with no big event in the next couple of "
                "days. This is a comfortable backdrop for your 21-45 day premium-selling trades.")
    return ("Read the market before you trade", "amber",
            "Live volatility is unavailable right now, so check conditions yourself before "
            "selling premium.")


def _step_market(provider, ctx, strategies) -> None:
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
        st.caption("Showing the **last close** - the market is closed today.")

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
                f"last close). Markets reopen <b>{nxt_str}</b>. Nothing to do today; come back "
                f"when it's open and I'll read the conditions for you.</div>",
                unsafe_allow_html=True)
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

    # A shortcut for when she has no particular name in mind.
    best_key = ctx.best_strategy_key or list(strategies.keys())[0]
    best_name = ctx.best_strategy_name or strategies[best_key]["name"]
    st.write("")
    c1, c2 = st.columns([5, 2])
    c1.caption(f"No name in mind? Today's market leans toward **{best_name}** on an index (SPX).")
    if c2.button("Use today's best index play ▸", use_container_width=True):
        st.session_state["flow_symbol"] = "SPX"
        st.session_state["flow_strategy"] = best_key
        st.session_state["_prev_flow_symbol"] = "SPX"
        for k in ("w_symbol", "w_strategy", "candidates", "wiz_chosen", "scan_sig"):
            st.session_state.pop(k, None)
        _goto(3)

    if not provider.is_real:
        st.info("You are offline, so these are sample numbers. Connect to the internet for real "
                "market data (or set up Schwab for true real-time).")

    _nav(1)


# ------------------------------------------------------------------ Step 2: Name
def _symbol_research(sym, provider, settings, key_prefix) -> None:
    """Full at-a-glance research for one name: index -> price + TradingView;
    stock/ETF -> the EarningsHub-style overview (grade, chart, analysts, earnings)."""
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
        st.caption("Indexes have no earnings or fundamentals - the market read in Step 1 is your "
                   "main guide here.")
    else:
        if not provider.is_real:
            st.info("The full name check needs real data. Connect to the internet and this will "
                    "show the grade, price trend, and earnings dates.")
            return
        _stock_overview_block(sym, provider, key_prefix=key_prefix)


def _step_name(settings, provider, strategies) -> None:
    opts = _symbol_options(settings)

    # Restore the last pick when navigating back (index=), without touching the
    # widget key directly - so within-step edits and 'removal sticks' both work.
    default_sym = st.session_state.get("flow_symbol")
    idx = opts.index(default_sym) if default_sym in opts else None
    sym = st.selectbox(
        "Which name do you want to trade?", opts, index=idx, key="w_symbol",
        placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...",
        help="Indexes (SPX, NDX...) run credit spreads. ETFs and stocks (SPY, AAPL...) run cash "
             "secured puts, covered calls, and PMCC.")
    st.session_state["flow_symbol"] = sym

    # Changing the name makes the strategy + any scan stale - clear them.
    if st.session_state.get("_prev_flow_symbol") != sym:
        st.session_state["_prev_flow_symbol"] = sym
        for k in ("flow_strategy", "w_strategy", "candidates", "wiz_chosen", "scan_sig"):
            st.session_state.pop(k, None)

    with st.expander("🔎 Not sure which name? Compare a few by premium richness",
                     expanded=not sym):
        _premium_finder_section(settings, provider, embedded=True)

    if sym:
        with st.container(border=True):
            _symbol_research(sym, provider, settings, key_prefix="name")

    _nav(2, can_continue=bool(sym), hint="Pick a name to continue.")


# ------------------------------------------------------------------ Step 3: Strategy
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


def _step_strategy(settings, provider, strategies) -> None:
    sym = st.session_state.get("flow_symbol")
    if not sym:
        st.warning("Go back to Step 2 and pick a name first.")
        _nav(3, can_continue=False)
        return

    kind = _classify(sym, settings)
    valid = _strategies_for_symbol(sym, strategies)

    advice = None
    if provider.is_real or kind == "index":
        with st.spinner(f"Working out the best play for {sym}..."):
            advice = _compute_advice(sym, kind, provider, settings)
        components.render_advice(advice)
    else:
        st.info("Connect to the internet for a tailored recommendation. You can still pick a "
                "strategy below and check it against your rules.")

    default_strat = st.session_state.get("flow_strategy")
    if default_strat not in valid:
        default_strat = (advice.primary.key if advice and advice.primary
                         and advice.primary.key in valid else valid[0])
    _seed_widget("w_strategy", "flow_strategy", default_strat)
    if st.session_state["w_strategy"] not in valid:
        st.session_state["w_strategy"] = default_strat

    st.write("")
    strategy_key = st.selectbox(
        "Strategy for this trade", valid, key="w_strategy",
        format_func=lambda k: strategies[k]["name"],
        help="Pre-set to the recommended play above - change it if you prefer another.")
    st.session_state["flow_strategy"] = strategy_key

    if st.session_state.get("_prev_flow_strategy_s3") != strategy_key:
        st.session_state["_prev_flow_strategy_s3"] = strategy_key
        st.session_state.pop("candidates", None)
        st.session_state.pop("wiz_chosen", None)
        st.session_state.pop("scan_sig", None)

    _strategy_about(strategies[strategy_key])
    _nav(3)


# ------------------------------------------------------------------ Step 4: Setup
def _step_setup(strategies, provider) -> None:
    strategy_key = st.session_state.get("flow_strategy")
    sym = st.session_state.get("flow_symbol")
    if not strategy_key or not sym:
        st.warning("Go back and choose a name and a strategy first.")
        _nav(4, can_continue=False)
        return
    strat = strategies[strategy_key]
    underlyings = [sym]

    if not scanner.can_scan(strategy_key):
        st.info("This strategy depends on the real shares you already own, so the app can't scan "
                "it for you. In the next step you can type in the trade exactly as you set it up "
                "in thinkorswim, and the app will check it against your SOP.")
        _nav(4, continue_label="Enter it myself ▸")
        return

    uses_width = strat.get("family") == "credit_spread"
    row = st.columns([1, 1] if uses_width else [1, 2])
    contracts = row[0].number_input("Contracts", min_value=1, max_value=50, value=1, step=1,
                                    help="How many copies of the whole position. Default is 1.")
    width = None
    if uses_width:
        width = row[1].number_input("Spread width ($)", min_value=1.0, max_value=200.0,
                                    value=25.0, step=1.0,
                                    help="Gap between your short and long strike.")

    scan_sig = (strategy_key, sym, int(contracts), width)
    if st.session_state.get("scan_sig") != scan_sig:
        st.session_state["scan_sig"] = scan_sig
        st.session_state.pop("candidates", None)
        st.session_state.pop("wiz_chosen", None)

    is_pmcc = strat.get("family") == "diagonal"
    st.caption(f"Shows up to 10 {strat['name']} setups on {sym} - one per expiration, sorted by "
               "days to expiration, each at the delta your SOP calls for."
               + (" (PMCC also picks a deep-in-the-money LEAPS as the stock stand-in.)"
                  if is_pmcc else ""))

    if st.button("🔎 Scan the market now", type="primary"):
        found = []
        with st.spinner(f"Reading {sym} option chains..."):
            try:
                chain = provider.get_chain(sym)
                leaps = provider.get_leaps_chain(sym) if is_pmcc else None
                found = scanner.scan_setups(strategy_key, chain, width=width,
                                            contracts=int(contracts), max_setups=10,
                                            leaps_chain=leaps)
            except Exception as e:
                st.error(f"{sym}: {e}")
        found.sort(key=lambda c: (c.dte if c.dte is not None else 0))
        st.session_state["candidates"] = found
        st.session_state.pop("wiz_chosen", None)

    candidates = st.session_state.get("candidates", [])
    if not candidates:
        if "candidates" in st.session_state:
            _no_setups_message(strat, is_pmcc)
        else:
            st.caption("Press **Scan the market now** for a short list of the best setups.")
        _nav(4, can_continue=False)
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
            "risk taken.")

    pick = st.number_input("Pick a setup to check (trade #)", min_value=1,
                           max_value=len(candidates), value=1, step=1)
    chosen = candidates[int(pick) - 1]
    st.session_state["wiz_chosen"] = chosen

    with st.container(border=True):
        if not chosen.fits_sop:
            st.warning(f"⚠️ {chosen.note}")
        st.markdown("**Leg-by-leg (build it this way in thinkorswim):**")
        st.dataframe(components.candidate_leg_detail(chosen), width="stretch", hide_index=True)

    _nav(4, continue_label="Check this setup ▸")


def _no_setups_message(strat, is_pmcc) -> None:
    fam = strat.get("family")
    if fam == "covered_call":
        st.info("No setups found. A covered call needs 100 shares, and for this name that may "
                "cost more than your monthly buying-power limit. Try a lower-priced stock, or use "
                "a Poor Man's Covered Call.")
    elif is_pmcc:
        st.info("No setups found. PMCC needs long-dated LEAPS options, which some names do not "
                "offer (and demo mode has none). Try a large, liquid stock in real-data mode.")
    else:
        st.info("No setups found for this name right now.")


# ------------------------------------------------------------------ Step 5: Check + Log
def _step_check(strategies, provider) -> None:
    strategy_key = st.session_state.get("flow_strategy")
    sym = st.session_state.get("flow_symbol")
    if not strategy_key:
        st.warning("Go back and choose a name and a strategy first.")
        _nav(5, can_continue=False)
        return
    strat = strategies[strategy_key]
    chosen = st.session_state.get("wiz_chosen")

    options = ["✅ Check the setup I picked", "✏️ Check a trade I built myself"]
    default_idx = 0 if chosen is not None else 1
    mode = st.radio("What do you want to check?", options, index=default_idx,
                    horizontal=True, key="check_mode")

    existing_bp = st.number_input("Buying power already used this month ($)", min_value=0.0,
                                  value=0.0, step=1000.0,
                                  help="So the monthly-limit check is realistic.")

    if mode.startswith("✅"):
        if chosen is None:
            st.info("You haven't picked a setup yet. Go back to Step 4 and scan, or switch to "
                    "**Check a trade I built myself** above.")
            _nav(5)
            return
        with st.container(border=True):
            st.markdown(f"**{strat['name']} on {chosen.trade.underlying}**")
            st.dataframe(components.candidate_leg_detail(chosen), width="stretch", hide_index=True)
            st.markdown("**Your SOP checklist:**")
            report = validate_trade(chosen.trade, existing_month_bp=existing_bp)
            components.render_checklist(report)
            _log_button(chosen.trade, strat["name"],
                        {"credit": chosen.credit, "max_loss": chosen.max_loss,
                         "buying_power": chosen.buying_power}, report.passed, key="scan")
    else:
        _manual_check(strategy_key, strat, sym, existing_bp)

    _nav(5)


def _manual_check(key, strat, sym, existing_bp) -> None:
    st.caption("Type in the trade exactly as you set it up in thinkorswim. "
               "Long = you bought it (+), short = you sold it (-).")
    default_list = [sym] if sym else allowed_underlyings_for(key)
    underlying = st.selectbox("Underlying", default_list, key="val_underlying")
    contracts = st.number_input("Contracts", min_value=1, max_value=50, value=1, step=1,
                                key="val_contracts")

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
        dte = cols[3].number_input("DTE", min_value=0, max_value=1000, value=30,
                                   step=1, key=f"dte_{i}")
        legs.append(Leg(role=role, action=action, option_type=opt_type, strike=strike,
                        delta=delta, premium=premium, quantity=qty, dte=int(dte)))

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


# ------------------------------------------------------------------ Explore mode
def _explore(settings, provider, strategies) -> None:
    theme.section("Explore names - look around, no commitment", "Research")
    st.caption("Screen for premium or look up any name's full picture. When you find one you "
               "like, jump straight into placing the trade.")

    _premium_finder_section(settings, provider, embedded=False)

    st.divider()
    theme.section("Look up any name", "Deep dive")
    opts = _symbol_options(settings)
    sym = st.selectbox("Symbol", opts, index=None, key="explore_sym",
                       placeholder="Type any ticker - SPX, SPY, AAPL, NVDA...")
    if not sym:
        st.caption("Pick a name for its full analysis and the strategy that fits it.")
        return
    if not provider.is_real and _classify(sym, settings) != "index":
        st.info("The deep dive needs real market data - connect to the internet first.")
        return

    kind = _classify(sym, settings)
    with st.container(border=True):
        _symbol_research(sym, provider, settings, key_prefix="explore")
        advice = _compute_advice(sym, kind, provider, settings)
        st.divider()
        components.render_advice(advice)
        if advice.primary:
            if st.button(f"Trade this: {advice.primary.name} on {sym} ▸", type="primary",
                         key="explore_trade"):
                st.session_state["flow_symbol"] = sym
                st.session_state["flow_strategy"] = advice.primary.key
                st.session_state["_prev_flow_symbol"] = sym
                for k in ("w_symbol", "w_strategy", "candidates", "wiz_chosen", "scan_sig",
                          "app_mode"):
                    st.session_state.pop(k, None)
                _goto(3)


# ------------------------------------------------------------------ shared pieces
def _stock_overview_block(sym, provider, key_prefix="setup"):
    """The full stock overview (score card, chart, analysts, earnings).
    Returns (analysis, earn_info)."""
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


def _premium_finder_section(settings, provider, embedded=False) -> None:
    """Compare symbols by how rich their option premiums are - helps choose a name."""
    from src.data import stock_universe

    if not embedded:
        theme.section("Which names pay the best premium?", "Premium finder")
    st.caption("For each name it checks selling a one-month put (~0.30 delta, about a 70% "
               "chance of keeping the premium) and gives a simple call: good to sell, okay, "
               "or skip.")

    if not provider.is_real:
        st.info("The premium finder needs real market data - connect to the internet first.")
        return

    etfs = settings["underlyings"]["us_style"]
    options = list(dict.fromkeys(etfs + stock_universe.FEATURED + stock_universe.all_stocks()))
    picks = st.multiselect(
        "Compare a few names", options,
        default=[s for s in ["AAPL", "NVDA", "SPY"] if s in options],
        max_selections=12, key="premium_picks",
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
    components.render_premium_cards(snaps)
    st.caption("See a name you like? Type it into the name box above to analyze and trade it.")


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
        if st.button("↺ Start over", use_container_width=True,
                     help="Clear this trade and go back to Step 1."):
            for k in ("flow_symbol", "flow_strategy", "w_symbol", "w_strategy", "candidates",
                      "wiz_chosen", "checked_trade", "scan_sig", "_prev_flow_symbol",
                      "_prev_flow_strategy_s3"):
                st.session_state.pop(k, None)
            _goto(1)

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
