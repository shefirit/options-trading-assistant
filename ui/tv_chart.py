"""The Analyze tab's chart: candles plus the technical analysis a beginner needs.

Drawn with TradingView's Lightweight Charts library (vendored in `tools/vendor`,
Apache 2.0) over the app's own price history, with every indicator computed in
`src.engine.indicators`.

Why we compute the indicators instead of embedding TradingView's chart widget:

1. The widget silently ignored the indicators we asked for - candles and volume
   drew, Bollinger Bands and the rest never appeared, and there is no way to
   check from outside because the widget renders in a cross-origin frame.
2. The widget refuses her actual underlyings. SPX, NDX, RUT, XSP and VIX all
   answer "this symbol is only available on TradingView" - the exchanges license
   that data - so indexes could only ever be charted through an ETF stand-in.
   Reading the SOP's price history ourselves charts the real SPX.

The chart describes what price has already done. It never predicts, and it never
overrides the SOP's entry checks or exits.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.engine import indicators as ind
from ui import theme

_VENDOR = (Path(__file__).resolve().parent.parent / "tools" / "vendor"
           / "lightweight-charts.standalone.production.js")

# How many bars each range shows, per candle size.
_RANGES: dict[str, tuple[int, int]] = {
    #  label        -> (daily bars, weekly bars)
    "6 months": (126, 26),
    "1 year": (252, 52),
    "2 years": (504, 104),
}
_DEFAULT_RANGE = "1 year"

_CANDLES = {
    "Daily (each candle = 1 day)": False,
    "Weekly (each candle = 1 week)": True,
}
_DEFAULT_CANDLE = "Daily (each candle = 1 day)"

# Plain-English label -> which series it switches on, and whether it starts on.
_INDICATORS: dict[str, dict] = {
    "Bollinger Bands - how stretched the price is": {"key": "bb", "default": True},
    "Moving averages 20 + 50 - which way the trend runs": {"key": "ma", "default": True},
    "200-day average - the long-term line": {"key": "ma200", "default": False},
    "RSI - overbought / oversold meter": {"key": "rsi", "default": True},
    "MACD - is the momentum turning?": {"key": "macd", "default": False},
}

# Chart colours, from the app's palette. Candles keep the red/green everyone
# expects; the indicator lines are chosen to stay apart for colour-blind eyes
# (blue / amber / purple rather than red vs green).
_UP, _DOWN = "#0B7A54", "#C02A1B"
_BB_LINE, _BB_FILL = "#2563EB", "rgba(37, 99, 235, 0.07)"
_MA20, _MA50, _MA200 = "#B45309", "#7C3AED", "#0F766E"


def _library() -> str:
    return _VENDOR.read_text(encoding="utf-8") if _VENDOR.exists() else ""


def _weekly(frame):
    """Fold daily bars into weekly ones (open of the week, the week's high and
    low, closing price on the last day, volume summed)."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in frame.columns:
        agg["Volume"] = "sum"
    return frame.resample("W-FRI").agg(agg).dropna(subset=["Open", "High", "Low", "Close"])


def build_payload(frame, bars: int, wanted: set[str], weekly: bool = False) -> dict:
    """Everything the chart draws, as plain JSON-able lists.

    Indicators are computed over the WHOLE history and only then sliced to the
    visible window, so a 200-day average is already drawn at the left edge
    instead of starting 200 bars into the picture.
    """
    dates = [d.strftime("%Y-%m-%d") for d in frame.index]
    o = [float(v) for v in frame["Open"]]
    h = [float(v) for v in frame["High"]]
    low = [float(v) for v in frame["Low"]]
    c = [float(v) for v in frame["Close"]]
    vol = ([float(v) for v in frame["Volume"]] if "Volume" in frame.columns
           else [0.0] * len(c))

    start = max(0, len(c) - bars)

    def line(series):
        return [{"time": dates[i], "value": round(v, 4)}
                for i, v in enumerate(series) if i >= start and v is not None]

    out: dict = {
        "candles": [{"time": dates[i], "open": round(o[i], 4), "high": round(h[i], 4),
                     "low": round(low[i], 4), "close": round(c[i], 4)}
                    for i in range(start, len(c))],
        "volume": [{"time": dates[i], "value": vol[i], "up": c[i] >= o[i]}
                   for i in range(start, len(c))],
        "digits": 2 if max(c) < 2000 else 0,
        "weekly": bool(weekly),
    }

    if "bb" in wanted:
        upper, mid, lower = ind.bollinger(c, 20, 2.0)
        out["bb"] = {"upper": line(upper), "middle": line(mid), "lower": line(lower)}
    if "ma" in wanted:
        out["ma20"] = line(ind.sma(c, 20))
        out["ma50"] = line(ind.sma(c, 50))
    if "ma200" in wanted:
        out["ma200"] = line(ind.sma(c, 200))
    if "rsi" in wanted:
        out["rsi"] = line(ind.rsi(c, 14))
    if "macd" in wanted:
        m_line, m_sig, m_hist = ind.macd(c)
        out["macd"] = {
            "line": line(m_line), "signal": line(m_sig),
            "hist": [{"time": d["time"], "value": d["value"],
                      "color": _UP if d["value"] >= 0 else _DOWN}
                     for d in line(m_hist)],
        }
    return out


def _key(payload: dict) -> str:
    """The colour key under the chart - only the lines actually drawn, so she is
    never hunting for a teal 200-day line that is switched off."""
    period = "week" if payload.get("weekly") else "day"
    rows = []
    if payload.get("bb"):
        rows.append((_BB_LINE, "Bollinger Bands"))
    if payload.get("ma20"):
        rows.append((_MA20, f"20-{period} average"))
    if payload.get("ma50"):
        rows.append((_MA50, f"50-{period} average"))
    if payload.get("ma200"):
        rows.append((_MA200, f"200-{period} average"))
    return "\n    ".join(f'<span><i class="sw" style="background:{c}"></i>{label}</span>'
                         for c, label in rows)


def chart_height(payload: dict, base: int = 460) -> int:
    """Grow the chart for each panel added below the price, so switching RSI or
    MACD on never squeezes the candles into a sliver."""
    lower = sum(1 for k in ("rsi", "macd") if payload.get(k))
    return base + 90 * lower


def chart_html(payload: dict, height: int = 520) -> str:
    """A self-contained page: the charting library plus this symbol's data."""
    lib = _library()
    if not lib:
        return ""
    data = json.dumps(payload, separators=(",", ":"))
    return f"""
<div id="wrap">
  <div id="legend">Hover the chart to read a day's numbers</div>
  <div id="chart"></div>
  <div id="foot">
    {_key(payload)}
    <span>Chart by <a href="https://www.tradingview.com/" target="_blank"
      rel="noopener">TradingView</a></span>
  </div>
</div>
<style>
  html, body {{ margin:0; padding:0; background:#FFFFFF; }}
  #wrap {{ font-family:Inter,-apple-system,'Segoe UI',sans-serif; color:{theme.INK}; }}
  #chart {{ width:100%; height:{height}px; }}
  #legend {{ font-size:0.95rem; color:{theme.INK}; min-height:24px; padding:2px 0 4px;
             display:flex; gap:14px; flex-wrap:wrap; }}
  #legend span {{ color:{theme.SECONDARY}; }}
  #legend .gain {{ color:{_UP}; font-weight:700; }}
  #legend .loss {{ color:{_DOWN}; font-weight:700; }}
  #foot {{ display:flex; gap:16px; flex-wrap:wrap; font-size:0.9rem;
           color:{theme.CAPTION}; padding-top:6px; }}
  #foot a {{ color:{theme.ACCENT}; }}
  .sw {{ display:inline-block; width:12px; height:3px; border-radius:2px;
         margin-right:5px; vertical-align:middle; }}
</style>
<script>{lib}</script>
<script>
(function(){{
  var D = {data};
  var el = document.getElementById('chart');
  if (!el || !window.LightweightCharts) return;
  var LC = window.LightweightCharts;
  var dg = D.digits;

  var chart = LC.createChart(el, {{
    autoSize: true,
    layout: {{ background: {{ color: '#FFFFFF' }}, textColor: '{theme.SECONDARY}',
               attributionLogo: false,
               panes: {{ separatorColor: '{theme.BORDER}' }} }},
    grid: {{ vertLines: {{ color: '#EEF4F0' }}, horzLines: {{ color: '#EEF4F0' }} }},
    rightPriceScale: {{ borderColor: '{theme.BORDER_STRONG}' }},
    timeScale: {{ borderColor: '{theme.BORDER_STRONG}', rightOffset: 4 }},
    // No chart-level priceFormatter here: it would win over every series and
    // print the RSI panel's 0-100 readings as "70.00". Each series formats itself.
    crosshair: {{ mode: LC.CrosshairMode.Normal }}
  }});

  var candles = chart.addSeries(LC.CandlestickSeries, {{
    upColor: '{_UP}', downColor: '{_DOWN}',
    borderUpColor: '{_UP}', borderDownColor: '{_DOWN}',
    wickUpColor: '{_UP}', wickDownColor: '{_DOWN}',
    priceFormat: {{ type: 'price', precision: dg, minMove: dg ? 0.01 : 1 }}
  }});
  candles.setData(D.candles);

  function overlay(data, color, width, style) {{
    var s = chart.addSeries(LC.LineSeries, {{
      color: color, lineWidth: width, lineStyle: style || LC.LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
    }});
    s.setData(data);
    return s;
  }}

  var bbUpper = null, bbLower = null;
  if (D.bb) {{
    bbUpper = overlay(D.bb.upper, '{_BB_LINE}', 2);
    bbLower = overlay(D.bb.lower, '{_BB_LINE}', 2);
    // The middle band IS the 20-day average, so only draw it when the moving
    // averages are off - otherwise it is a second line in a second colour
    // sitting exactly on top of the first, which just looks like a bug.
    if (!D.ma20) overlay(D.bb.middle, '{_BB_LINE}', 1, LC.LineStyle.Dashed);
  }}
  var ma20 = D.ma20 ? overlay(D.ma20, '{_MA20}', 2) : null;
  var ma50 = D.ma50 ? overlay(D.ma50, '{_MA50}', 2) : null;
  var ma200 = D.ma200 ? overlay(D.ma200, '{_MA200}', 2) : null;

  var paneIndex = 1;
  var volPane = paneIndex++;
  var vol = chart.addSeries(LC.HistogramSeries, {{
    priceFormat: {{ type: 'volume' }}, priceScaleId: '',
    lastValueVisible: false, priceLineVisible: false
  }}, volPane);
  vol.setData(D.volume.map(function(v){{
    return {{ time: v.time, value: v.value,
              color: v.up ? 'rgba(11,122,84,.45)' : 'rgba(192,42,27,.45)' }};
  }}));

  var rsiSeries = null, rsiPane = null;
  if (D.rsi) {{
    rsiPane = paneIndex++;
    rsiSeries = chart.addSeries(LC.LineSeries, {{
      color: '#334155', lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      // RSI is a 0-100 reading; the price scale's decimals would render it "70.00".
      priceFormat: {{ type: 'price', precision: 0, minMove: 1 }}
    }}, rsiPane);
    rsiSeries.setData(D.rsi);
    [30, 70].forEach(function(b){{
      rsiSeries.createPriceLine({{ price: b, color: '{theme.BORDER_STRONG}',
        lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: true,
        title: b === 70 ? 'overbought' : 'oversold' }});
    }});
  }}

  var macdPane = null;
  if (D.macd) {{
    macdPane = paneIndex++;
    var hist = chart.addSeries(LC.HistogramSeries, {{
      priceLineVisible: false, lastValueVisible: false
    }}, macdPane);
    hist.setData(D.macd.hist);
    var ml = chart.addSeries(LC.LineSeries, {{
      color: '{_BB_LINE}', lineWidth: 2, priceLineVisible: false, lastValueVisible: false
    }}, macdPane);
    ml.setData(D.macd.line);
    var sl = chart.addSeries(LC.LineSeries, {{
      color: '{_MA20}', lineWidth: 2, priceLineVisible: false, lastValueVisible: false
    }}, macdPane);
    sl.setData(D.macd.signal);
  }}

  // Proportional pane sizing rather than pixel heights: the price pane keeps the
  // lion's share whatever combination of lower panels is switched on, and the
  // split survives the chart being resized by the browser.
  var panes = chart.panes();
  function share(i, factor) {{
    var p = panes[i];
    if (!p) return;
    if (p.setStretchFactor) p.setStretchFactor(factor);
    else if (p.setHeight) p.setHeight(Math.round({height} * factor / 10));
  }}
  share(0, 6);
  share(volPane, 1.4);
  if (rsiPane !== null) share(rsiPane, 1.8);
  if (macdPane !== null) share(macdPane, 1.8);
  chart.timeScale().fitContent();

  var legend = document.getElementById('legend');
  var idle = 'Hover the chart to read a day\\u2019s numbers';
  function val(p, s) {{
    if (!s) return null;
    var d = p.seriesData.get(s);
    return d && d.value !== undefined ? d.value : null;
  }}
  chart.subscribeCrosshairMove(function(p){{
    if (!p || !p.time || !p.seriesData) {{ legend.textContent = idle; return; }}
    var c = p.seriesData.get(candles);
    if (!c) {{ legend.textContent = idle; return; }}
    var chg = ((c.close - c.open) / c.open * 100);
    var html = '<b>' + p.time + '</b>' +
      '<span>open ' + c.open.toFixed(dg) + '</span>' +
      '<span>high ' + c.high.toFixed(dg) + '</span>' +
      '<span>low ' + c.low.toFixed(dg) + '</span>' +
      '<span>close <b>' + c.close.toFixed(dg) + '</b></span>' +
      '<span class="' + (chg >= 0 ? 'gain' : 'loss') + '">' +
      (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span>';
    var u = val(p, bbUpper), l = val(p, bbLower);
    if (u !== null && l !== null) {{
      html += '<span>bands ' + l.toFixed(dg) + ' to ' + u.toFixed(dg) + '</span>';
    }}
    var r = val(p, rsiSeries);
    if (r !== null) html += '<span>RSI ' + r.toFixed(0) + '</span>';
    legend.innerHTML = html;
  }});
  legend.textContent = idle;
  // A handle on the finished chart, so its state can be inspected from the page
  // when something looks wrong (pane count, series, bar count).
  window.__chart = chart;
  window.__chartReady = {{
    bars: D.candles.length,
    panes: chart.panes().length,
    series: {{ bb: !!D.bb, ma20: !!D.ma20, ma50: !!D.ma50, ma200: !!D.ma200,
               rsi: !!D.rsi, macd: !!D.macd }}
  }};
}})();
</script>
"""


def render(symbol: str, provider, kind: str = "stock", key_prefix: str = "analyze",
           height: int | None = None) -> None:
    """The whole chart block: title, controls, chart, and the beginner guide."""
    import streamlit.components.v1 as components

    if not symbol:
        return

    st.markdown(f"### 📈 {symbol} chart with technical analysis")
    theme.note("Candles with the beginner indicators already switched on. Drag to pan, "
               "scroll to zoom, hover any candle for that day's numbers. Tap **How to "
               "read this chart** underneath if any of it looks like alphabet soup.")

    c1, c2, c3 = st.columns([1, 1, 2])
    rng = c1.selectbox("Range", list(_RANGES), index=list(_RANGES).index(_DEFAULT_RANGE),
                       key=f"chart_rng_{key_prefix}_{symbol}")
    candle_label = c2.selectbox(
        "Candles", list(_CANDLES), index=0, key=f"chart_cdl_{key_prefix}_{symbol}",
        help="Daily is the working view for 21-45 day trades. Weekly steps back to "
             "see the bigger trend.")
    picked = c3.multiselect(
        "Indicators", list(_INDICATORS),
        default=[k for k, v in _INDICATORS.items() if v["default"]],
        key=f"chart_ind_{key_prefix}_{symbol}",
        help="Turn things off to declutter. Volume (the bars under the price) is "
             "always on.")

    weekly = _CANDLES.get(candle_label, False)
    bars = _RANGES[rng][1 if weekly else 0]
    wanted = {_INDICATORS[label]["key"] for label in picked}

    if not provider.is_real:
        theme.note("The chart needs real market data - you are on sample data right now.")
        return

    with st.spinner(f"Loading {symbol} price history..."):
        frame = provider.get_ohlc(symbol, period="5y" if weekly else "2y")
    if frame is None or len(frame) < 30:
        theme.note(f"Could not load price history for **{symbol}** right now. "
                   "Try again in a moment.")
        return
    if weekly:
        frame = _weekly(frame)

    payload = build_payload(frame, bars, wanted, weekly=weekly)
    tall = height or chart_height(payload)
    html = chart_html(payload, height=tall)
    if not html:
        theme.note("The charting library is missing from this install "
                   "(`tools/vendor`), so the chart cannot be drawn.")
        return
    components.html(html, height=tall + 70, scrolling=False)

    last = frame.index[-1].strftime("%d %b %Y")
    theme.note(f"{'Weekly' if weekly else 'Daily'} bars through **{last}**, about 15 "
               "minutes delayed - read the shape here, not the exact fill price.")
    render_guide(symbol)


def render_guide(symbol: str = "this name") -> None:
    """Every indicator explained in plain English, tied to what she does with it."""
    with st.expander("📖 How to read this chart (plain English)", expanded=False):
        st.markdown("""
**The candles.** Each candle is one time period. The thick body runs from the
opening price to the closing price - green means it closed higher than it
opened, red means lower. The thin wicks show the highest and lowest price
touched in that period. A long wick means price went there and got rejected.

**📊 The bars underneath (volume).** How many shares changed hands. A big move
on big volume is more convincing than a big move on a quiet day.
""")
        st.markdown("""
---
**🎯 Bollinger Bands - is the price stretched?** (the blue lines)

Bands wrapped around the price, built off the average closing price of the last
20 periods. They sit two standard deviation moves either side of that average -
in plain terms, price spends roughly 95% of its time between them. That middle
average is the orange 20-day line, so with the moving averages switched on you
are already looking at it; switch them off and it appears as a dashed blue line.

- Price riding the **upper** band = it has run up fast and is stretched.
- Price riding the **lower** band = it has fallen fast and is stretched down.
- **Narrow** bands = the market is calm. Calm means thin option premium.
- **Wide** bands = the market is swinging. Fatter premium, but the risk that
  earns it is real, not free money.

Stretched is not the same as "about to reverse". In a strong trend price can
hug a band for weeks. Read it as "this move is extended", not as a signal.
""")
        st.markdown("""
---
**📉 Moving averages - which way is the trend?**

The average price over the last 20 periods (orange, fast), 50 periods (purple,
medium) and 200 periods (teal, slow, off by default).

- Price above the lines, and the 20 above the 50 = **uptrend**.
- Price below the lines, and the 20 below the 50 = **downtrend**.
- Lines tangled together and flat = **no trend**, the market is chopping
  sideways. Sideways is where neutral setups like the Iron Condor live.
""")
        st.markdown("""
---
**🌡️ RSI - the overbought / oversold meter**

A 0 to 100 scale in its own panel below the price, with dotted lines at 70 and
30. Above 70 is called overbought (bought hard and fast), below 30 oversold.
Two warnings that catch beginners out:

- RSI above 70 does **not** mean "it will fall". Strong names sit above 70 for
  months.
- RSI is most useful as agreement or disagreement with what the price and the
  bands already say, never on its own.

**⚡ MACD - is momentum turning?** (off by default)

Two lines and a bar chart in their own panel. When the blue line crosses above
the orange one, momentum is turning up; crossing below, turning down. It always
confirms a move that has already started - it never predicts one.
""")
        st.markdown(f"""
---
**How this feeds your SOP**

The chart sets the context, your SOP picks the trade. What it is good for:

- **Reading the trend** before you choose a direction. Uptrend points to put
  credit spreads and cash secured puts (0.25 and 0.30 delta per your SOP);
  downtrend points to call credit spreads (0.10 delta - stricter, because
  markets drift up); sideways points to the Iron Condor (0.15 per leg).
- **Sanity-checking your short strike.** Look at where the strike you are about
  to sell sits on this chart. Is it outside the Bollinger Bands, or right in the
  middle of where {symbol} has been trading all month?
- **Reading how wild it is.** Wide bands and big candles mean the premium is fat
  for a reason. That is the same message your VIX comfort zone of 13 to 25
  carries on the Market tab.

⚠️ Every indicator here is arithmetic on past prices. None of them knows what
happens next, and none of them replaces your SOP's entry checks, your 2x credit
stop, your 21 DTE time exit, or your 50% profit target.
""")
