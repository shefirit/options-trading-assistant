"""The live TradingView chart shown in the Analyze tab.

This embeds TradingView's own Advanced Chart widget (free, no key, no account)
straight into the page, pre-loaded with the indicators a beginner actually
needs: Bollinger Bands, two moving averages, RSI, MACD, and volume.

Why the real TradingView widget and not our own drawing:
- It is the same chart she already sees on tradingview.com, so what she learns
  there transfers here.
- The indicators are computed by TradingView, not by us, so there is nothing to
  keep in sync and no extra data fetching from our side.
- It loads in HER browser, so it works even when our data provider is on the
  delayed CBOE feed or on sample data.

The chart is context, never a signal. Every explanation here points back to her
SOP: the chart describes what price has done, the SOP decides what to trade.
"""

from __future__ import annotations

import json

import streamlit as st

from ui import theme

# ---------------------------------------------------------------- symbol map
# TradingView needs EXCHANGE:SYMBOL to be certain which instrument it is showing.
# A bare ticker usually resolves, but "usually" is not good enough when the wrong
# match would show her a chart of something else entirely.
#
# The cash indexes she trades (SPX, NDX, RUT, XSP, VIX) are NOT available in the
# free embedded chart - CBOE and Nasdaq license that data, and TradingView answers
# "this symbol is only available on TradingView" (verified 2026-07-24). So each
# index charts as the ETF that holds the same basket. Same shape, same trend, same
# indicators; only the price scale differs, and _PROXY_NOTE spells that out.
#
# The x-factor is filled in only where the ETF is BUILT as a fixed fraction of the
# index (SPY is one tenth of SPX by design, DIA one hundredth of the Dow). QQQ and
# IWM only track their index loosely, so they get no multiplier rather than a
# number that quietly goes stale.
_INDEX_PROXY = {
    #  index -> (TradingView symbol, what she is actually looking at, x-factor)
    "SPX": ("AMEX:SPY", "SPY, the ETF holding the same 500 stocks", 10),
    "XSP": ("AMEX:SPY", "SPY, the ETF holding the same 500 stocks", 1),
    "NDX": ("NASDAQ:QQQ", "QQQ, the ETF holding the same Nasdaq 100 stocks", None),
    "RUT": ("AMEX:IWM", "IWM, the ETF holding the same Russell 2000 stocks", None),
    "DJX": ("AMEX:DIA", "DIA, the ETF holding the same Dow 30 stocks", 1),
    "VIX": ("AMEX:VIXY", "VIXY, a fund that tracks VIX futures", None),
}

# NYSE Arca funds show up as "AMEX" on TradingView.
_ETF_TV = {
    "SPY": "AMEX:SPY", "QQQ": "NASDAQ:QQQ", "IWM": "AMEX:IWM", "DIA": "AMEX:DIA",
    "GLD": "AMEX:GLD", "SLV": "AMEX:SLV", "TLT": "NASDAQ:TLT", "EEM": "AMEX:EEM",
    "EFA": "AMEX:EFA", "XLF": "AMEX:XLF", "XLE": "AMEX:XLE", "XLK": "AMEX:XLK",
    "XLV": "AMEX:XLV", "SMH": "NASDAQ:SMH",
}


def tv_symbol(symbol: str, kind: str = "stock") -> str:
    """The TradingView ticker to chart for one of our symbols.

    kind is "index" | "etf" | "stock" - the same classification the rest of the
    app uses. Indexes come back as their ETF proxy (see _INDEX_PROXY); unknown
    stocks fall through as a bare ticker, which TradingView resolves to the
    primary US listing.
    """
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if s in _INDEX_PROXY:
        return _INDEX_PROXY[s][0]
    if s in _ETF_TV:
        return _ETF_TV[s]
    if kind == "index":
        # An index with no proxy of ours - let TradingView try to resolve it.
        return s
    return s


def proxy_note(symbol: str) -> str:
    """The plain-English "you are looking at X, not Y" line, or "" when the chart
    really is the thing she asked for."""
    s = (symbol or "").strip().upper()
    if s not in _INDEX_PROXY:
        return ""
    tv, what, factor = _INDEX_PROXY[s]
    line = (f"**{s} itself has no free chart** - the exchange licenses that data. "
            f"This is **{what}**. Same stocks, same trend, same indicators.")
    if factor and factor > 1:
        line += (f" Only the price scale differs: {tv.split(':')[1]} is one "
                 f"{'tenth' if factor == 10 else f'{factor}th'} of {s}, so multiply "
                 f"what you see here by {factor} to get the {s} level.")
    elif factor == 1:
        line += f" The price scale is close to {s}'s too."
    else:
        line += (f" The price scale is different from {s}, so read the shape and "
                 f"the trend here - take your strike levels from the app.")
    return line


# ---------------------------------------------------------------- indicators
# Plain-English label -> (TradingView study ids, settings to override).
# Order here is the order they appear in the picker.
_INDICATORS: dict[str, dict] = {
    "Bollinger Bands - how stretched the price is": {
        "studies": ["BB@tv-basicstudies"],
        "overrides": {"bollinger bands.median.color": "#2962FF"},
        "default": True,
    },
    "Moving averages 20 + 50 - which way the trend runs": {
        "studies": ["MAExp@tv-basicstudies", "MASimple@tv-basicstudies"],
        "overrides": {
            "moving average exponential.length": 20,
            "moving average.length": 50,
        },
        "default": True,
    },
    "RSI - overbought / oversold meter": {
        "studies": ["RSI@tv-basicstudies"],
        "overrides": {},
        "default": True,
    },
    "MACD - is the momentum turning?": {
        "studies": ["MACD@tv-basicstudies"],
        "overrides": {},
        "default": False,
    },
}

# One timeframe choice -> the TradingView interval code.
# Matched to how she trades: entries at 21-45 days out, so the daily chart is the
# working view and the weekly is the "step back and see the big trend" view.
#
# Deliberately no "range" key in the config: setting range makes the widget pick
# its own candle size and quietly ignore the interval (verified 2026-07-24 - asking
# for daily candles over 6M gave 2-hour candles). The date buttons on the chart's
# own toolbar cover zooming out.
_TIMEFRAMES: dict[str, str] = {
    "Daily (each candle = 1 day)": "D",
    "Weekly (each candle = 1 week)": "W",
    "Hourly (each candle = 1 hour)": "60",
}

_DEFAULT_TIMEFRAME = "Daily (each candle = 1 day)"


def _widget_config(tv_sym: str, interval: str,
                   studies: list[str], overrides: dict) -> dict:
    return {
        "autosize": True,
        "symbol": tv_sym,
        "interval": interval,
        "timezone": "America/New_York",     # US market hours, like thinkorswim
        "theme": "light",
        "style": "1",                       # candles
        "locale": "en",
        "withdateranges": True,             # the 1D / 1M / 6M buttons
        "hide_side_toolbar": True,          # drawing tools - noise for now
        "hide_top_toolbar": False,          # keep interval + indicator buttons
        "allow_symbol_change": False,       # the symbol comes from the tab above
        "hide_volume": False,
        "details": False,
        "studies": studies,
        "studies_overrides": overrides,
        "backgroundColor": "#FFFFFF",
        "gridColor": "rgba(11, 122, 84, 0.06)",
        "support_host": "https://www.tradingview.com",
    }


def chart_html(tv_sym: str, interval: str = "D",
               studies: list[str] | None = None,
               overrides: dict | None = None) -> str:
    """The self-contained HTML for one embedded chart."""
    cfg = _widget_config(tv_sym, interval, studies or [], overrides or {})
    return f"""
<div class="tradingview-widget-container" style="height:100%;width:100%;">
  <div class="tradingview-widget-container__widget"
       style="height:calc(100% - 28px);width:100%;"></div>
  <div class="tradingview-widget-copyright"
       style="font:13px Inter,-apple-system,sans-serif;color:#182A21;padding-top:4px;">
    <a href="https://www.tradingview.com/symbols/{tv_sym.replace(':', '-')}/"
       rel="noopener nofollow" target="_blank"
       style="color:#0B7A54;text-decoration:none;">Open {tv_sym} on TradingView</a>
  </div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"
    async>{json.dumps(cfg)}</script>
</div>
<style>html,body{{margin:0;padding:0;height:100%;background:#FFFFFF;}}</style>
"""


def render(symbol: str, kind: str = "stock", key_prefix: str = "analyze",
           height: int = 620) -> None:
    """The whole chart block: title, controls, chart, and the beginner guide."""
    import streamlit.components.v1 as components

    tv_sym = tv_symbol(symbol, kind)
    if not tv_sym:
        return

    st.markdown(f"### 📈 {symbol} chart with technical analysis")
    theme.note("The live TradingView chart, with the beginner indicators already turned on. "
               "Tap **How to read this chart** underneath if any of it looks like alphabet "
               "soup.")
    note = proxy_note(symbol)
    if note:
        theme.note(f"ℹ️ {note}")

    c1, c2 = st.columns([1, 2])
    tf_label = c1.selectbox("Timeframe", list(_TIMEFRAMES), index=0,
                            key=f"tvchart_tf_{key_prefix}_{symbol}",
                            help="Daily is the working view for 21-45 day trades. "
                                 "Weekly steps back to see the bigger trend.")
    picked = c2.multiselect(
        "Indicators", list(_INDICATORS),
        default=[k for k, v in _INDICATORS.items() if v["default"]],
        key=f"tvchart_ind_{key_prefix}_{symbol}",
        help="Turn things off to declutter. Volume (the bars along the bottom) is "
             "always on.")

    interval = _TIMEFRAMES.get(tf_label or _DEFAULT_TIMEFRAME,
                               _TIMEFRAMES[_DEFAULT_TIMEFRAME])
    studies: list[str] = []
    overrides: dict = {}
    for label in picked:
        spec = _INDICATORS[label]
        studies.extend(spec["studies"])
        overrides.update(spec["overrides"])

    components.html(chart_html(tv_sym, interval, studies, overrides),
                    height=height, scrolling=False)
    theme.note(f"Showing **{tv_sym}**. Prices on the free feed can run a few minutes "
               "behind - use it to read the shape, not to time a fill.")
    render_guide(symbol)


def render_guide(symbol: str = "this name") -> None:
    """Every indicator explained in plain English, tied to what she does with it."""
    with st.expander("📖 How to read this chart (plain English)", expanded=False):
        st.markdown(f"""
**The candles.** Each candle is one time period. The thick body runs from the
opening price to the closing price - green means it closed higher than it
opened, red means lower. The thin wicks show the highest and lowest price
touched in that period. A long wick means the price went there and got rejected.

**📊 The bars along the bottom (volume).** How many shares changed hands. A big
move on big volume is more convincing than a big move on quiet volume.
""")
        st.markdown("""
---
**🎯 Bollinger Bands - is the price stretched?**

Three lines wrapped around the price. The middle line is the average closing
price of the last 20 periods. The outer two sit one standard deviation move
above and below it - in plain terms, price spends roughly 95% of its time
between them.

- Price riding the **upper** band = it has run up fast and is stretched.
- Price riding the **lower** band = it has fallen fast and is stretched down.
- **Narrow** bands = the market is calm. Calm means thin option premium.
- **Wide** bands = the market is swinging. Fatter premium, but the risk that
  earns it is real, not free money.

Stretched is not the same as "about to reverse". In a strong trend price can
hug a band for weeks. Treat it as "this move is extended", not as a signal.
""")
        st.markdown("""
---
**📉 Moving averages 20 and 50 - which way is the trend?**

The average price over the last 20 periods (fast) and 50 periods (slow).

- Price above both, and the 20 above the 50 = **uptrend**.
- Price below both, and the 20 below the 50 = **downtrend**.
- The two lines tangled together and flat = **no trend**, the market is chopping
  sideways. Sideways is where neutral setups like the Iron Condor live.

The number next to each line's name in the chart's top-left corner is its
period. If one shows something other than 20 or 50, tap the name and change the
length - the chart remembers nothing, so it comes back at the default next time.
""")
        st.markdown("""
---
**🌡️ RSI - the overbought / oversold meter**

A 0 to 100 scale in its own panel below the price. Above 70 is called
overbought (bought hard and fast), below 30 is oversold. Two warnings that
catch beginners out:

- RSI above 70 does **not** mean "it will fall". Strong names sit above 70 for
  months.
- RSI is most useful as agreement or disagreement with what the price and the
  bands are already telling you, never on its own.

**⚡ MACD - is momentum turning?**

Two lines in a panel below. When the fast line crosses above the slow one,
momentum is turning up; crossing below, turning down. It always confirms a
move that already started - it never predicts one.
""")
        st.markdown(f"""
---
**How this feeds your SOP**

The chart sets the context, your SOP picks the trade. What it is good for:

- **Reading the trend** before you choose a direction. Uptrend points to
  put credit spreads and cash secured puts (0.25 and 0.30 delta per your SOP);
  downtrend points to call credit spreads (0.10 delta - stricter, because
  markets drift up); sideways points to the Iron Condor (0.15 per leg).
- **Sanity-checking your short strike.** Pull up the chart and look at where the
  strike you are about to sell sits. Is it outside the Bollinger Bands, or right
  in the middle of where {symbol} has been trading all month?
- **Reading how wild it is.** Wide bands and big candles mean the premium is fat
  for a reason. That is the same message your VIX comfort zone of 13 to 25
  carries on the Market tab.

⚠️ Every indicator on this chart is arithmetic on past prices. None of them
knows what happens next, and none of them replaces your SOP's entry checks,
your 2x credit stop, your 21 DTE time exit, or your 50% profit target.
""")
