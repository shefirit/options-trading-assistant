"""Renders the analyst report as a visual HTML page.

`analyst_report.py --html out.html` collects the data and hands it here. The
page is self-contained: no scripts, no external fonts, SVG charts drawn inline.

The charts follow the validated categorical palette - blue, orange, aqua in that
fixed order. That three-slot set passes CVD separation and the normal-vision
floor in both light and dark against these surfaces. Light-mode aqua sits under
3:1 on white, so every line carries a visible direct label rather than relying on
its color to identify it.

Two comment markers are left in the output for the agent to replace with prose:
BOTTOM_LINE and OUTLOOK. Everything else is generated from the data.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Optional

# Categorical slots 1-3, light / dark. Fixed order, never cycled.
S1_L, S2_L, S3_L = "#2a78d6", "#eb6834", "#1baf7a"
S1_D, S2_D, S3_D = "#3987e5", "#d95926", "#199e70"


def _e(v) -> str:
    return html.escape(str(v), quote=True)


def _f(v) -> Optional[float]:
    try:
        out = float(v)
        return None if out != out else out
    except (TypeError, ValueError):
        return None


def _fmt(v, digits=2, suffix="") -> str:
    v = _f(v)
    return "n/a" if v is None else f"{v:,.{digits}f}{suffix}"


def _pct(v, digits=1) -> str:
    v = _f(v)
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def _big(v) -> str:
    v = _f(v)
    if v is None:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= size:
            return f"${v / size:.2f}{unit}"
    return f"${v:,.0f}"


def _sma_series(closes: list[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for i in range(len(closes)):
        out.append(sum(closes[i - n + 1:i + 1]) / n if i >= n - 1 else None)
    return out


# ------------------------------------------------------------------ charts
def _rsi_series(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """Wilder's RSI, computed across the whole series so the visible window is
    already warmed up rather than starting from a partial average."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def levels_from_pivots(bars: list[dict], price: float,
                       reach: int = 5, tol: float = 0.015) -> list[dict]:
    """Support and resistance the way a chart reader actually finds them.

    A pivot high is a bar whose high beats the `reach` bars on both sides; a
    pivot low is the mirror. Pivots that sit within `tol` of each other are the
    same level touched more than once, so they get clustered and the touch count
    becomes the level's strength. Returns the strongest two above and two below
    the current price.
    """
    if len(bars) < reach * 2 + 5 or not price:
        return []
    highs, lows = [], []
    for i in range(reach, len(bars) - reach):
        window = bars[i - reach:i + reach + 1]
        if bars[i]["h"] >= max(b["h"] for b in window):
            highs.append(bars[i]["h"])
        if bars[i]["l"] <= min(b["l"] for b in window):
            lows.append(bars[i]["l"])

    def cluster(vals: list[float]) -> list[dict]:
        out: list[dict] = []
        for v in sorted(vals):
            if out and abs(v - out[-1]["price"]) / out[-1]["price"] <= tol:
                grp = out[-1]
                grp["touches"] += 1
                grp["price"] = (grp["price"] * (grp["touches"] - 1) + v) / grp["touches"]
            else:
                out.append({"price": v, "touches": 1})
        return out

    # A price touched once is just a high, not a level - it takes at least two
    # visits before traders treat it as one.
    res = [c for c in cluster(highs)
           if c["price"] > price * 1.005 and c["touches"] >= 2]
    sup = [c for c in cluster(lows)
           if c["price"] < price * 0.995 and c["touches"] >= 2]
    # Nearest levels first. A wall 18% away is real but it is not what decides
    # the next move, and showing it above a closer level buries the useful one.
    res.sort(key=lambda c: c["price"] - price)
    sup.sort(key=lambda c: price - c["price"])
    picked = [dict(c, kind="resistance") for c in res[:2]] + \
             [dict(c, kind="support") for c in sup[:2]]
    return picked


def _decollide(labels: list[tuple[float, str, str]], gap: float = 13.0
               ) -> list[tuple[float, str, str]]:
    """Push overlapping right-edge labels apart.

    Two series sitting at nearly the same value printed their names on top of
    each other, which is what made the first chart unreadable.
    """
    out = sorted(labels, key=lambda t: t[0])
    for i in range(1, len(out)):
        if out[i][0] - out[i - 1][0] < gap:
            out[i] = (out[i - 1][0] + gap, out[i][1], out[i][2])
    return out


def _candle_chart(bars: list[dict], full_closes: list[float], w=760) -> str:
    """A TradingView-style read of the chart: candles, the two moving averages
    traders actually watch, support and resistance, volume, and RSI."""
    if len(bars) < 40:
        return ""
    visible = 130                                   # about six months of trading
    show = bars[-visible:]
    n = len(show)
    start = len(full_closes) - n

    sma50_full, sma200_full = _sma_series(full_closes, 50), _sma_series(full_closes, 200)
    rsi_full = _rsi_series(full_closes)
    sma50 = sma50_full[start:]
    sma200 = sma200_full[start:]
    rsi = rsi_full[start:]

    price = show[-1]["c"]
    levels = levels_from_pivots(bars, price)

    pad_l, pad_r = 8, 62
    p_top, p_h = 20, 286                            # price panel
    v_top, v_h = 330, 62                            # volume panel
    r_top, r_h = 424, 78                            # RSI panel
    h = 534
    plot_w = w - pad_l - pad_r

    # Scale to the CANDLES, not to the moving averages. A 200-day line sitting
    # far below the recent range drags the axis down and squashes every candle
    # into the top half - which is exactly what made the first version unreadable.
    # The averages are clipped to the panel instead.
    vals = [b["h"] for b in show] + [b["l"] for b in show]
    vals += [lv["price"] for lv in levels]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    span = hi - lo

    def x(i):
        return pad_l + (i + 0.5) * (plot_w / n)

    def y(v):
        return p_top + (1 - (v - lo) / span) * p_h

    out = [f'<defs><clipPath id="pp"><rect x="{pad_l}" y="{p_top}" '
           f'width="{plot_w}" height="{p_h}"/></clipPath></defs>']

    # ---- price grid + right axis. The tick nearest the live price is dropped so
    # the "now" marker has clear space to sit in.
    now_y = y(price)
    for frac in (0, 0.25, 0.5, 0.75, 1):
        gy = p_top + frac * p_h
        out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{gy:.1f}" class="grid"/>')
        if abs(gy - now_y) > 14:
            out.append(f'<text x="{pad_l + plot_w + 6}" y="{gy + 3.5:.1f}" '
                       f'class="tick">{hi - frac * span:,.0f}</text>')

    # ---- support and resistance
    # The dashed lines belong behind the candles, but the labels must sit on top
    # of everything or the bars paint straight over them - so they are collected
    # here and appended after the candles and averages are drawn.
    level_labels: list[str] = []
    for lv in levels:
        ly = y(lv["price"])
        if not (p_top <= ly <= p_top + p_h):
            continue
        cls = "res" if lv["kind"] == "resistance" else "sup"
        out.append(f'<line x1="{pad_l}" y1="{ly:.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{ly:.1f}" class="lvl {cls}"><title>{_e(lv["kind"])} near '
                   f'{lv["price"]:,.2f}, touched {lv["touches"]} times</title></line>')
        times = "touch" if lv["touches"] == 1 else "touches"
        text = f'{lv["kind"]} {lv["price"]:,.0f} · {lv["touches"]} {times}'
        # The label sits over the candles at the left edge, so it needs its own
        # background to stay readable - without it the text and the bars merge.
        tw = len(text) * 5.6 + 8
        level_labels.append(
            f'<rect x="{pad_l + 2}" y="{ly - 14:.1f}" width="{tw:.1f}" '
            f'height="13" rx="2" class="lvlbg"/>'
            f'<text x="{pad_l + 6}" y="{ly - 4:.1f}" class="lvllabel {cls}">'
            f'{_e(text)}</text>')

    # ---- candles
    cw = max(plot_w / n * 0.62, 1.6)
    for i, b in enumerate(show):
        up = b["c"] >= b["o"]
        cls = "up" if up else "dn"
        cx = x(i)
        out.append(f'<line x1="{cx:.1f}" y1="{y(b["h"]):.1f}" x2="{cx:.1f}" '
                   f'y2="{y(b["l"]):.1f}" class="wick {cls}"/>')
        top, bot = y(max(b["o"], b["c"])), y(min(b["o"], b["c"]))
        out.append(f'<rect x="{cx - cw / 2:.1f}" y="{top:.1f}" width="{cw:.1f}" '
                   f'height="{max(bot - top, 1):.1f}" class="body {cls}">'
                   f'<title>{b["d"]}  open {b["o"]:,.2f}  high {b["h"]:,.2f}  '
                   f'low {b["l"]:,.2f}  close {b["c"]:,.2f}</title></rect>')

    # ---- moving averages
    def path(series):
        return " ".join(f"{x(i):.1f},{y(v):.1f}"
                        for i, v in enumerate(series) if v is not None)

    ends = []
    for series, var, name in ((sma50, "--s2", "50-day"), (sma200, "--s1", "200-day")):
        pts = path(series)
        if not pts:
            continue
        out.append(f'<polyline class="ln" clip-path="url(#pp)" '
                   f'style="stroke:var({var})" points="{pts}">'
                   f'<title>{name} average</title></polyline>')
        last = next((v for v in reversed(series) if v is not None), None)
        if last is not None:
            ly = min(max(y(last), p_top + 8), p_top + p_h - 4)
            ends.append((ly, f"{name} {last:,.0f}", var))
    # Labels go INSIDE the plot, right-aligned. Putting them in the right gutter
    # printed them straight over the price ticks.
    for ly, name, var in _decollide(ends):
        tw = len(name) * 5.9 + 10
        out.append(f'<rect x="{pad_l + plot_w - tw - 4:.1f}" y="{ly - 9:.1f}" '
                   f'width="{tw:.1f}" height="13" rx="2" class="lvlbg"/>'
                   f'<text x="{pad_l + plot_w - 8:.1f}" y="{ly + 1:.1f}" '
                   f'class="dlabel end" style="fill:var({var})">{_e(name)}</text>')

    out.extend(level_labels)

    # ---- the live price, on the axis, so the eye lands on it first
    out.append(f'<line x1="{pad_l}" y1="{now_y:.1f}" x2="{pad_l + plot_w}" '
               f'y2="{now_y:.1f}" class="nowline"/>')
    out.append(f'<rect x="{pad_l + plot_w + 2}" y="{now_y - 8:.1f}" width="52" '
               f'height="16" rx="2" class="nowchip"/>')
    out.append(f'<text x="{pad_l + plot_w + 28}" y="{now_y + 4:.1f}" '
               f'class="nowtext">{price:,.2f}</text>')

    # ---- volume
    vmax = max(b["v"] for b in show) or 1
    out.append(f'<text x="{pad_l}" y="{v_top - 6}" class="panel">Volume - how many '
               f'shares changed hands</text>')
    for i, b in enumerate(show):
        bh = (b["v"] / vmax) * v_h
        cls = "up" if b["c"] >= b["o"] else "dn"
        out.append(f'<rect x="{x(i) - cw / 2:.1f}" y="{v_top + v_h - bh:.1f}" '
                   f'width="{cw:.1f}" height="{max(bh, 0.5):.1f}" class="vol {cls}">'
                   f'<title>{b["d"]}: {b["v"] / 1e6:,.1f}M shares</title></rect>')

    # ---- RSI
    out.append(f'<text x="{pad_l}" y="{r_top - 6}" class="panel">RSI - momentum on a '
               f'0 to 100 scale. Over 70 is stretched, under 30 is beaten down</text>')

    def ry(v):
        return r_top + (1 - v / 100) * r_h

    # Shade the zones so "stretched" and "beaten down" are visible, not just numbers
    out.append(f'<rect x="{pad_l}" y="{r_top:.1f}" width="{plot_w}" '
               f'height="{ry(70) - r_top:.1f}" class="rsizone hot"/>')
    out.append(f'<rect x="{pad_l}" y="{ry(30):.1f}" width="{plot_w}" '
               f'height="{r_top + r_h - ry(30):.1f}" class="rsizone cold"/>')
    for band in (30, 70):
        out.append(f'<line x1="{pad_l}" y1="{ry(band):.1f}" x2="{pad_l + plot_w}" '
                   f'y2="{ry(band):.1f}" class="grid dash"/>')
        out.append(f'<text x="{pad_l + plot_w + 6}" y="{ry(band) + 3.5:.1f}" '
                   f'class="tick">{band}</text>')
    rpts = " ".join(f"{x(i):.1f},{ry(v):.1f}"
                    for i, v in enumerate(rsi) if v is not None)
    if rpts:
        out.append(f'<polyline class="ln rsi" points="{rpts}"><title>RSI 14</title></polyline>')
        last_rsi = next((v for v in reversed(rsi) if v is not None), None)
        if last_rsi is not None:
            # Inside the panel, right-aligned - in the gutter it landed on the 30 tick
            out.append(f'<rect x="{pad_l + plot_w - 34:.1f}" y="{ry(last_rsi) - 9:.1f}" '
                       f'width="30" height="14" rx="2" class="lvlbg"/>'
                       f'<text x="{pad_l + plot_w - 8:.1f}" y="{ry(last_rsi) + 1:.1f}" '
                       f'class="dlabel end rsi">{last_rsi:.0f}</text>')

    # ---- date ticks
    for i in (0, n // 2, n - 1):
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        out.append(f'<text x="{x(i):.1f}" y="{h - 6}" class="axlabel" '
                   f'style="text-anchor:{anchor}">{show[i]["d"]}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="Six months of daily candles with the 50 and 200 day averages,
     support and resistance levels, volume and RSI">
  {''.join(out)}
</svg>'''


def _price_chart(closes: list[float], w=680, h=260) -> str:
    """Price with its 50 and 200 day averages. Every line is directly labelled."""
    if len(closes) < 200:
        return ""
    pad_l, pad_r, pad_t, pad_b = 8, 76, 14, 26
    sma50, sma200 = _sma_series(closes, 50), _sma_series(closes, 200)

    vals = [c for c in closes] + [v for v in sma50 if v] + [v for v in sma200 if v]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.06, hi + span * 0.06
    span = hi - lo

    n = len(closes)
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b

    def x(i):
        return pad_l + (i / (n - 1)) * plot_w

    def y(v):
        return pad_t + (1 - (v - lo) / span) * plot_h

    def path(series):
        pts = [f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series) if v is not None]
        return " ".join(pts)

    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1):
        gy = pad_t + frac * plot_h
        val = hi - frac * span
        grid.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + plot_w}" '
                    f'y2="{gy:.1f}" class="grid"/>')
        grid.append(f'<text x="{pad_l + plot_w + 6}" y="{gy + 3.5:.1f}" '
                    f'class="tick">{val:,.0f}</text>')

    last_i = n - 1
    labels = []
    for series, color_var, name in ((closes, "--s1", "Price"),
                                    (sma50, "--s2", "50-day"),
                                    (sma200, "--s3", "200-day")):
        v = series[last_i]
        if v is None:
            continue
        labels.append(
            f'<text x="{x(last_i) + 6:.1f}" y="{y(v) + 3.5:.1f}" class="dlabel" '
            f'style="fill:var({color_var})">{_e(name)}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="One year of daily closes with the 50 and 200 day moving averages">
  {''.join(grid)}
  <polyline class="ln" style="stroke:var(--s3)" points="{path(sma200)}"><title>200-day average</title></polyline>
  <polyline class="ln" style="stroke:var(--s2)" points="{path(sma50)}"><title>50-day average</title></polyline>
  <polyline class="ln lnw" style="stroke:var(--s1)" points="{path(closes)}"><title>Daily closing price</title></polyline>
  {''.join(labels)}
</svg>'''


def _rs_chart(rows: list[tuple[str, float, float]], sym: str, w=680, h=210) -> str:
    """Grouped bars: the stock against SPY over four lookbacks."""
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 8, 8, 16, 34
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    vals = [v for _, a, b in rows for v in (a, b)]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.12
    span = hi - lo
    zero_y = pad_t + (1 - (0 - lo) / span) * plot_h

    group_w = plot_w / len(rows)
    bar_w = min(38, group_w * 0.3)
    out = [f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{pad_l + plot_w}" '
           f'y2="{zero_y:.1f}" class="axis"/>']

    for gi, (label, a, b) in enumerate(rows):
        cx = pad_l + group_w * (gi + 0.5)
        for bi, (val, var, who) in enumerate(((a, "--s1", sym), (b, "--s2", "SPY"))):
            bx = cx - bar_w - 2 + bi * (bar_w + 4)
            vy = pad_t + (1 - (val - lo) / span) * plot_h
            top, height = min(vy, zero_y), abs(vy - zero_y)
            out.append(
                f'<rect x="{bx:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
                f'height="{max(height, 1):.1f}" rx="3" style="fill:var({var})">'
                f'<title>{_e(who)} {label}: {val:+.1f}%</title></rect>')
            ty = top - 5 if val >= 0 else top + height + 12
            out.append(f'<text x="{bx + bar_w / 2:.1f}" y="{ty:.1f}" '
                       f'class="blabel">{val:+.0f}%</text>')
        out.append(f'<text x="{cx:.1f}" y="{h - 8}" class="axlabel">{_e(label)}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="Total return of {_e(sym)} against SPY over four periods">
  {''.join(out)}
</svg>'''


def _surprise_chart(eps: list[dict], w=680, h=170) -> str:
    """How far each quarter beat or missed what analysts expected."""
    rows = [q for q in eps if _f(q.get("surprise_pct")) is not None][-8:]
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 8, 8, 16, 32
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    vals = [_f(q["surprise_pct"]) for q in rows]
    hi = max(max(vals), 1) * 1.25
    lo = min(min(vals), 0) * 1.25 if min(vals) < 0 else 0
    span = (hi - lo) or 1
    zero_y = pad_t + (1 - (0 - lo) / span) * plot_h
    slot = plot_w / len(rows)
    bar_w = min(44, slot * 0.5)

    out = [f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{pad_l + plot_w}" '
           f'y2="{zero_y:.1f}" class="axis"/>']
    for i, q in enumerate(rows):
        val = _f(q["surprise_pct"])
        cx = pad_l + slot * (i + 0.5)
        vy = pad_t + (1 - (val - lo) / span) * plot_h
        top, height = min(vy, zero_y), abs(vy - zero_y)
        # Beat and miss is a state, not a series - status colors, with the
        # sign printed on every bar so color is never the only signal.
        fill = "var(--good)" if q.get("beat") else "var(--critical)"
        out.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
            f'height="{max(height, 1):.1f}" rx="3" style="fill:{fill}">'
            f'<title>{_e(q.get("label", ""))}: expected {_fmt(q.get("estimate"))}, '
            f'delivered {_fmt(q.get("actual"))}</title></rect>')
        out.append(f'<text x="{cx:.1f}" y="{top - 5:.1f}" class="blabel">'
                   f'{val:+.1f}%</text>')
        out.append(f'<text x="{cx:.1f}" y="{h - 8}" class="axlabel">'
                   f'{_e(q.get("label", "")).replace(" ", "&#160;")}</text>')

    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="Earnings surprise by quarter, percent above or below expectations">
  {''.join(out)}
</svg>'''


def _ladder(price: float, levels: list[tuple[str, float]], w=680, h=112) -> str:
    """Where price sits among the levels that matter."""
    pts = [(n, v) for n, v in levels if _f(v)]
    if not pts or not price:
        return ""
    vals = [v for _, v in pts] + [price]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.12
    span = hi - lo
    pad = 14
    plot_w = w - pad * 2
    y_line = 62

    def x(v):
        return pad + ((v - lo) / span) * plot_w

    out = [f'<line x1="{pad}" y1="{y_line}" x2="{pad + plot_w}" y2="{y_line}" '
           f'class="axis"/>']
    for name, v in pts:
        out.append(f'<line x1="{x(v):.1f}" y1="{y_line - 9}" x2="{x(v):.1f}" '
                   f'y2="{y_line + 9}" class="tickmark"/>')
        out.append(f'<text x="{x(v):.1f}" y="{y_line + 26}" class="axlabel">'
                   f'{_e(name)}</text>')
        out.append(f'<text x="{x(v):.1f}" y="{y_line + 39}" class="axlabel dim">'
                   f'{v:,.0f}</text>')
    px = x(price)
    out.append(f'<circle cx="{px:.1f}" cy="{y_line}" r="7" class="nowdot">'
               f'<title>Now: {price:,.2f}</title></circle>')
    out.append(f'<text x="{px:.1f}" y="{y_line - 20}" class="nowlabel">'
               f'now {price:,.2f}</text>')
    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="Current price against the levels that matter">{''.join(out)}</svg>'''


def _iv_bar(iv: Optional[float], hv: Optional[float], w=680, h=118) -> str:
    iv, hv = _f(iv), _f(hv)
    if iv is None or hv is None:
        return ""
    hi = max(iv, hv) * 1.3 or 1
    pad_l, pad_r, pad_t = 8, 8, 12
    plot_w = w - pad_l - pad_r
    out = []
    for i, (val, var, name) in enumerate(((iv, "--s1", "Priced in (implied)"),
                                          (hv, "--s2", "Actually moved (realized)"))):
        y = pad_t + i * 46
        bw = (val / hi) * plot_w * 0.72
        out.append(f'<rect x="{pad_l}" y="{y}" width="{max(bw, 2):.1f}" height="22" '
                   f'rx="3" style="fill:var({var})"><title>{_e(name)}: '
                   f'{val * 100:.1f}%</title></rect>')
        out.append(f'<text x="{pad_l + bw + 10:.1f}" y="{y + 16}" class="blabel lft">'
                   f'{val * 100:.1f}% &#183; {_e(name)}</text>')
    return f'''<svg viewBox="0 0 {w} {h}" role="img" class="chart"
     aria-label="Implied volatility against realized volatility">{''.join(out)}</svg>'''


# ------------------------------------------------------------------ pieces
def chart_readout(bars: list[dict], full_closes: list[float]) -> list[tuple[str, str, str]]:
    """Turn the chart into sentences. Returns (state, headline, explanation).

    The chart shows what happened; this says what it means, which is the part a
    beginner cannot yet do for themselves.
    """
    if len(bars) < 200:
        return []
    price = bars[-1]["c"]
    s20, s50, s200 = (_sma_series(full_closes, 20)[-1], _sma_series(full_closes, 50)[-1],
                      _sma_series(full_closes, 200)[-1])
    r = _rsi_series(full_closes)[-1]
    levels = levels_from_pivots(bars, price)
    rows: list[tuple[str, str, str]] = []

    above = [n for n, v in (("20-day", s20), ("50-day", s50), ("200-day", s200))
             if v and price > v]
    below = [n for n, v in (("20-day", s20), ("50-day", s50), ("200-day", s200))
             if v and price < v]
    if len(above) == 3:
        rows.append(("good", "Price is above all three averages",
                     "Buyers have been in control over the short, medium and long "
                     "term. This is the healthiest arrangement a chart can have."))
    elif len(below) == 3:
        rows.append(("crit", "Price is below all three averages",
                     "Sellers have been in control at every timeframe. This is the "
                     "setup where selling puts most often goes wrong, because there "
                     "is no level underneath that has recently held."))
    else:
        rows.append(("warn", f"Price is above the {' and '.join(above)} but below "
                             f"the {' and '.join(below)}",
                     "A mixed chart. One timeframe disagrees with another, which "
                     "usually means the market has not decided yet."))

    if s50 and s200:
        if s50 > s200:
            rows.append(("good", "Golden cross is intact",
                         "The 50-day average sits above the 200-day. Traders read "
                         "that as the long-term uptrend still being in place, even "
                         "when the recent weeks have been poor."))
        else:
            rows.append(("crit", "Death cross",
                         "The 50-day average has fallen below the 200-day. That is "
                         "the classic signal that the damage is no longer just a "
                         "short-term dip."))

    if r is not None:
        if r >= 70:
            rows.append(("warn", f"RSI is {r:.0f} - stretched",
                         "It has run up hard and fast. Moves like this often pause "
                         "or pull back, though a strong stock can stay stretched "
                         "for weeks."))
        elif r <= 30:
            rows.append(("warn", f"RSI is {r:.0f} - heavily sold off",
                         "It has fallen hard and fast. Bounces often start from "
                         "here, but a falling stock can stay oversold for a long "
                         "time. Oversold is not the same as cheap."))
        else:
            rows.append(("good", f"RSI is {r:.0f} - neutral",
                         "Momentum is not stretched in either direction, so there "
                         "is room to move both ways."))

    res = [lv for lv in levels if lv["kind"] == "resistance"]
    sup = [lv for lv in levels if lv["kind"] == "support"]
    if res:
        lv = res[0]
        rows.append(("warn", f"Nearest ceiling: {lv['price']:,.0f}, "
                             f"{(lv['price'] / price - 1) * 100:.1f}% above",
                     f"It has failed to break above this {lv['touches']} times. "
                     f"Getting through it usually takes heavy buying."))
    if sup:
        lv = sup[0]
        rows.append(("good", f"Nearest floor: {lv['price']:,.0f}, "
                             f"{(1 - lv['price'] / price) * 100:.1f}% below",
                     f"It has bounced off this {lv['touches']} times. Losing it "
                     f"is what would turn a dip into something worse."))
    return rows


def _analyst_block(info: dict, ratings: dict, price: Optional[float]) -> str:
    """What professional analysts think - the distribution and the target range."""
    counts = [("Strong buy", ratings.get("strong_buy", 0), "sb"),
              ("Buy", ratings.get("buy", 0), "b"),
              ("Hold", ratings.get("hold", 0), "h"),
              ("Sell", ratings.get("sell", 0), "s"),
              ("Strong sell", ratings.get("strong_sell", 0), "ss")]
    total = sum(c for _, c, _ in counts)
    lo, mean, hi = (_f(info.get("targetLowPrice")), _f(info.get("targetMeanPrice")),
                    _f(info.get("targetHighPrice")))
    if not total and not mean:
        return ""

    parts = []
    if total:
        seg = "".join(
            f'<div class="seg {k}" style="flex:{c}"><span>{c}</span></div>'
            for _, c, k in counts if c)
        key = "".join(f'<span><i class="sw {k}"></i>{_e(n)} {c}</span>'
                      for n, c, k in counts if c)
        bullish = ratings.get("strong_buy", 0) + ratings.get("buy", 0)
        parts.append(
            f'<p><strong>{bullish} of {total} analysts rate it buy or better'
            f'</strong> ({bullish / total * 100:.0f}%).</p>'
            f'<div class="ratebar">{seg}</div>'
            f'<div class="legend ratekey">{key}</div>')

    if lo and hi and mean and price:
        rng = (hi - lo) or 1

        def pos(v):
            return max(0.0, min(100.0, (v - lo) / rng * 100))
        parts.append(
            f'<div class="targets">'
            f'<div class="trail">'
            f'<div class="tmark mean" style="left:{pos(mean):.1f}%"></div>'
            f'<div class="tmark now" style="left:{pos(price):.1f}%"></div>'
            f'</div>'
            f'<div class="tends"><span>lowest {lo:,.0f}</span>'
            f'<span>highest {hi:,.0f}</span></div>'
            f'<p class="cap">The average target is <strong>{mean:,.0f}</strong>, '
            f'which is {((mean / price) - 1) * 100:+.0f}% from today\'s '
            f'{price:,.2f}. The black mark is where it trades now, the blue mark '
            f'is the average target. Treat the spread between lowest and highest '
            f'as the honest measure of how much disagreement there is - a wide '
            f'gap means nobody really knows.</p></div>')

    rec = str(info.get("recommendationKey") or "").replace("_", " ").upper()
    head = (f'<p class="eyebrow">Consensus: {_e(rec)}</p>' if rec else "")
    return (f'<section class="stack"><p class="eyebrow">The professionals</p>'
            f'<h2>What analysts think</h2><div class="card">{head}'
            f'{"".join(parts)}'
            f'<p class="cap">Analysts are paid to cover companies and skew '
            f'optimistic, so read the direction and the disagreement rather than '
            f'trusting the number.</p></div></section>')


VENDOR = Path(__file__).resolve().parent / "vendor"


def _library() -> str:
    """TradingView's Lightweight Charts, inlined.

    Artifacts run under a CSP that blocks every external host, so the library
    cannot be pulled from a CDN at runtime - it ships inside the page. Apache
    2.0, which requires the attribution notice rendered with the chart.
    """
    js = VENDOR / "lightweight-charts.standalone.production.js"
    return js.read_text(encoding="utf-8") if js.exists() else ""


def _interactive_chart(bars: list[dict], full_closes: list[float],
                       symbol: str) -> str:
    """A real trading chart: candles, volume and RSI panes, moving averages,
    support and resistance price lines, crosshair with a live OHLC readout."""
    lib = _library()
    if not lib or len(bars) < 200:
        return ""

    show = bars[-260:]                              # about a year, pannable
    start = len(full_closes) - len(show)
    s50 = _sma_series(full_closes, 50)[start:]
    s200 = _sma_series(full_closes, 200)[start:]
    rsi = _rsi_series(full_closes)[start:]

    candles = [{"time": b["d"], "open": round(b["o"], 2), "high": round(b["h"], 2),
                "low": round(b["l"], 2), "close": round(b["c"], 2)} for b in show]
    volume = [{"time": b["d"], "value": b["v"],
               "up": b["c"] >= b["o"]} for b in show]
    line50 = [{"time": b["d"], "value": round(v, 2)}
              for b, v in zip(show, s50) if v is not None]
    line200 = [{"time": b["d"], "value": round(v, 2)}
               for b, v in zip(show, s200) if v is not None]
    rsi_line = [{"time": b["d"], "value": round(v, 1)}
                for b, v in zip(show, rsi) if v is not None]
    levels = levels_from_pivots(bars, show[-1]["c"])

    payload = json.dumps({
        "candles": candles, "volume": volume, "s50": line50, "s200": line200,
        "rsi": rsi_line, "levels": levels, "symbol": symbol,
    }, separators=(",", ":"))

    return f'''<div class="chartbox">
  <h3>{_e(symbol)} - interactive chart</h3>
  <p class="cap">Drag to pan, scroll to zoom, hover for that day's numbers.
  The dashed lines are support and resistance, labelled with how many times
  price has touched them.</p>
  <div class="tvlegend" id="tvlegend">Hover the chart</div>
  <div id="tvchart" class="tvchart"></div>
  <div class="tvfoot">
    <span><i class="sw" style="background:#e0a03a"></i>50-day average</span>
    <span><i class="sw" style="background:#3b82d6"></i>200-day average</span>
    <span>Charts by <a href="https://www.tradingview.com/" target="_blank"
      rel="noopener">TradingView</a></span>
  </div>
</div>
<script>{lib}</script>
<script>
(function(){{
  var D = {payload};
  var el = document.getElementById('tvchart');
  if (!el || !window.LightweightCharts) return;
  var LC = window.LightweightCharts;

  function dark(){{
    var t = document.documentElement.getAttribute('data-theme');
    if (t === 'dark') return true;
    if (t === 'light') return false;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }}

  var chart, candleSeries;
  function theme(){{
    var d = dark();
    return {{
      layout: {{ background: {{ color: 'transparent' }},
                textColor: d ? '#c3c2b7' : '#52514e',
                attributionLogo: false,
                panes: {{ separatorColor: d ? '#2c2c2a' : '#e1e0d9' }} }},
      grid: {{ vertLines: {{ color: d ? '#232322' : '#eeede8' }},
              horzLines: {{ color: d ? '#232322' : '#eeede8' }} }},
      rightPriceScale: {{ borderColor: d ? '#383835' : '#c3c2b7' }},
      timeScale: {{ borderColor: d ? '#383835' : '#c3c2b7' }},
      crosshair: {{ mode: LC.CrosshairMode.Normal }},
    }};
  }}

  function build(){{
    if (chart) {{ chart.remove(); chart = null; }}
    el.innerHTML = '';
    chart = LC.createChart(el, Object.assign({{
      width: el.clientWidth, height: 460, autoSize: true
    }}, theme()));

    candleSeries = chart.addSeries(LC.CandlestickSeries, {{
      upColor: '#0ca30c', downColor: '#d03b3b',
      borderUpColor: '#0ca30c', borderDownColor: '#d03b3b',
      wickUpColor: '#0ca30c', wickDownColor: '#d03b3b',
    }});
    candleSeries.setData(D.candles);

    chart.addSeries(LC.LineSeries, {{ color: '#e0a03a', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false }}).setData(D.s50);
    chart.addSeries(LC.LineSeries, {{ color: '#3b82d6', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false }}).setData(D.s200);

    D.levels.forEach(function(lv){{
      var res = lv.kind === 'resistance';
      candleSeries.createPriceLine({{
        price: lv.price, color: res ? '#d03b3b' : '#0f7a48',
        lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true,
        title: lv.kind + ' · ' + lv.touches + 'x'
      }});
    }});

    var vol = chart.addSeries(LC.HistogramSeries, {{
      priceFormat: {{ type: 'volume' }}, priceScaleId: '',
      lastValueVisible: false, priceLineVisible: false
    }}, 1);
    vol.setData(D.volume.map(function(v){{
      return {{ time: v.time, value: v.value,
               color: v.up ? 'rgba(12,163,12,.5)' : 'rgba(208,59,59,.5)' }};
    }}));

    var rsi = chart.addSeries(LC.LineSeries, {{
      color: dark() ? '#c3c2b7' : '#52514e', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: true
    }}, 2);
    rsi.setData(D.rsi);
    [30, 70].forEach(function(b){{
      rsi.createPriceLine({{ price: b, color: dark() ? '#4a4a48' : '#c3c2b7',
        lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: true }});
    }});

    var panes = chart.panes();
    if (panes[0]) panes[0].setHeight(300);
    if (panes[1]) panes[1].setHeight(80);
    if (panes[2]) panes[2].setHeight(100);
    chart.timeScale().fitContent();

    var legend = document.getElementById('tvlegend');
    chart.subscribeCrosshairMove(function(p){{
      if (!p || !p.time || !p.seriesData) {{ legend.textContent = 'Hover the chart'; return; }}
      var c = p.seriesData.get(candleSeries);
      if (!c) return;
      var up = c.close >= c.open;
      var chg = ((c.close - c.open) / c.open * 100).toFixed(2);
      legend.innerHTML = '<b>' + p.time + '</b>' +
        '<span>open ' + c.open.toFixed(2) + '</span>' +
        '<span>high ' + c.high.toFixed(2) + '</span>' +
        '<span>low ' + c.low.toFixed(2) + '</span>' +
        '<span>close <b>' + c.close.toFixed(2) + '</b></span>' +
        '<span class="' + (up ? 'gain' : 'loss') + '">' +
        (up ? '+' : '') + chg + '%</span>';
    }});
  }}

  build();
  if (window.matchMedia) {{
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    (mq.addEventListener ? mq.addEventListener.bind(mq, 'change')
                         : mq.addListener.bind(mq))(build);
  }}
  new MutationObserver(build).observe(document.documentElement,
    {{ attributes: true, attributeFilter: ['data-theme'] }});
}})();
</script>'''


def _tile(label: str, value: str, note: str = "") -> str:
    return (f'<div class="tile"><span class="tl">{_e(label)}</span>'
            f'<span class="tv">{value}</span>'
            f'<span class="tn">{_e(note)}</span></div>')


def _metric(label: str, value: str, read: str, tone: str = "") -> str:
    cls = f" {tone}" if tone else ""
    return (f'<div class="m"><span class="ml">{_e(label)}</span>'
            f'<span class="mv{cls}">{value}</span>'
            f'<span class="mr">{_e(read)}</span></div>')


def _chip(text: str, tone: str) -> str:
    return f'<span class="chip {tone}">{_e(text)}</span>'


CSS = """
:root{
  --paper:#f1eee5; --plane:#e9e5da; --card:#ffffff; --ink:#191510;
  --ink2:#5b5446; --muted:#8c8578; --grid:#e3dfd3; --axis:#c9c4b6;
  --ring:rgba(25,21,16,.12);
  --band:#181410; --band-ink:#f4f1e8; --band-sub:#b8b1a2; --band-accent:#b4adff;
  --accent:#3f34e6; --accent-soft:#e7e5fb; --accent-ink:#2a22b8;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --good:#0ca30c; --warning:#f0a51a; --critical:#d03b3b; --goodtext:#0a6e30;
  --good-bg:#e2f2e3; --warn-bg:#fbeecf; --crit-bg:#fae5e5;
  --shadow:0 1px 2px rgba(25,21,16,.05),0 10px 30px -18px rgba(25,21,16,.22);
  --ui:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    --paper:#100e0a; --plane:#0a0805; --card:#1b1813; --ink:#f5f2ea;
    --ink2:#c3bcac; --muted:#948d7e; --grid:#2b2721; --axis:#3a352d;
    --ring:rgba(245,242,234,.13);
    --band:#231f18; --band-ink:#f5f2ea; --band-sub:#b4ac9c; --band-accent:#b7b0ff;
    --accent:#8f86ff; --accent-soft:#221d3d; --accent-ink:#b7b0ff;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70;
    --good:#0ca30c; --warning:#f0a51a; --critical:#e8635f; --goodtext:#3fc06f;
    --good-bg:#123016; --warn-bg:#33280a; --crit-bg:#331616;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 14px 34px -20px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --paper:#100e0a; --plane:#0a0805; --card:#1b1813; --ink:#f5f2ea;
  --ink2:#c3bcac; --muted:#948d7e; --grid:#2b2721; --axis:#3a352d;
  --ring:rgba(245,242,234,.13);
  --band:#231f18; --band-ink:#f5f2ea; --band-sub:#b4ac9c; --band-accent:#b7b0ff;
  --accent:#8f86ff; --accent-soft:#221d3d; --accent-ink:#b7b0ff;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --good:#0ca30c; --warning:#f0a51a; --critical:#e8635f; --goodtext:#3fc06f;
  --good-bg:#123016; --warn-bg:#33280a; --crit-bg:#331616;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 14px 34px -20px rgba(0,0,0,.7);
}
*{box-sizing:border-box;}
body{background:var(--paper);color:var(--ink);font-family:var(--ui);
  font-size:17px;line-height:1.62;-webkit-font-smoothing:antialiased;
  background-image:radial-gradient(120% 60% at 50% -10%,var(--plane),transparent 60%);}
.wrap{max-width:57rem;margin:0 auto;padding:1.5rem 1.15rem 5rem;
  display:flex;flex-direction:column;gap:2.5rem;}
h1{font-size:clamp(2.1rem,6vw,3.1rem);line-height:1.02;margin:0;
  letter-spacing:-.03em;text-wrap:balance;font-weight:800;}
h2{font-size:clamp(1.35rem,3vw,1.65rem);margin:0;letter-spacing:-.02em;
  text-wrap:balance;font-weight:800;}
h3{font-size:1.02rem;margin:0;font-weight:700;letter-spacing:-.01em;}
p{margin:0;} .stack{display:flex;flex-direction:column;gap:1rem;}
.eyebrow{font-size:.7rem;letter-spacing:.16em;text-transform:uppercase;
  color:var(--accent-ink);margin:0;font-weight:800;display:flex;align-items:center;
  gap:.5rem;}
.eyebrow::before{content:"";width:1.4rem;height:2px;border-radius:2px;
  background:var(--accent);display:inline-block;}

/* ---- masthead ---- */
.masthead{background:var(--band);color:var(--band-ink);border-radius:16px;
  padding:1.9rem 1.7rem 1.6rem;display:flex;flex-direction:column;gap:.55rem;
  box-shadow:var(--shadow);position:relative;overflow:hidden;}
.masthead::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(80% 130% at 100% 0%,rgba(143,134,255,.16),transparent 55%);}
.mh-top{display:flex;justify-content:space-between;align-items:center;gap:1rem;
  position:relative;z-index:1;}
.mh-eyebrow{font-size:.68rem;letter-spacing:.18em;text-transform:uppercase;
  color:var(--band-accent);font-weight:800;}
.mh-sym{font-size:.8rem;letter-spacing:.12em;font-weight:700;color:var(--band-sub);
  font-variant-numeric:tabular-nums;}
.masthead h1{color:var(--band-ink);position:relative;z-index:1;}
.mh-price{display:flex;align-items:baseline;gap:.7rem;position:relative;z-index:1;
  margin-top:.15rem;}
.mh-now{font-size:2rem;font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;}
.mh-chg{font-size:.95rem;font-weight:700;padding:.15rem .5rem;border-radius:999px;
  font-variant-numeric:tabular-nums;}
.mh-chg.up{color:#7ee6a2;background:rgba(12,163,12,.16);}
.mh-chg.down{color:#ff9f9b;background:rgba(208,59,59,.18);}
.mh-meta{font-size:.78rem;color:var(--band-sub);position:relative;z-index:1;
  font-variant-numeric:tabular-nums;margin-top:.2rem;}

.chips{display:flex;flex-wrap:wrap;gap:.45rem;position:relative;z-index:1;
  margin-top:.4rem;}
.chip{font-size:.71rem;letter-spacing:.03em;text-transform:uppercase;font-weight:800;
  padding:.34rem .7rem;border-radius:999px;display:inline-flex;align-items:center;
  gap:.35rem;}
.chip::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;
  opacity:.9;}
/* Default chips (on light cards, e.g. the outlook scenarios) read dark-on-tint. */
.chip.good{background:#cfeed9;color:#0a6e30;}
.chip.warn{background:#f6e3b4;color:#8a5600;}
.chip.crit{background:#f7d2d2;color:#9e2222;}
/* On the dark masthead band the same chips flip to bright-on-translucent. */
.masthead .chip.good{background:rgba(12,163,12,.20);color:#8ff0b0;}
.masthead .chip.warn{background:rgba(240,165,26,.22);color:#ffd280;}
.masthead .chip.crit{background:rgba(208,59,59,.22);color:#ffaca8;}

.card{background:var(--card);border:1px solid var(--ring);border-radius:14px;
  padding:1.4rem 1.5rem;display:flex;flex-direction:column;gap:.95rem;
  box-shadow:var(--shadow);}
.card.accent{border-left:4px solid var(--accent);}
/* The three outlook scenarios become bright, distinct callout cards - kept light
   with dark text in BOTH themes so the label and prose stay crisp. The only
   .card elements that contain a chip are these three. */
.card:has(.chip.good),.card:has(.chip.warn),.card:has(.chip.crit){color:#20201a;}
.card:has(.chip.good) strong,.card:has(.chip.warn) strong,
.card:has(.chip.crit) strong{color:#111;}
.card:has(.chip.good){background:#e6f6ec;border-color:#b0dcbe;
  border-left:4px solid var(--good);}
.card:has(.chip.warn){background:#fdf3d8;border-color:#e9d29a;
  border-left:4px solid var(--warning);}
.card:has(.chip.crit){background:#fde3e3;border-color:#f0bcbc;
  border-left:4px solid var(--critical);}
.lead{font-size:1.2rem;line-height:1.5;text-wrap:pretty;font-weight:600;
  letter-spacing:-.01em;}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(8.5rem,1fr));
  gap:.7rem;}
.tile{background:var(--card);border:1px solid var(--ring);border-radius:12px;
  padding:1rem 1.05rem;display:flex;flex-direction:column;gap:.28rem;
  box-shadow:var(--shadow);position:relative;overflow:hidden;}
.tile::before{content:"";position:absolute;top:0;left:0;width:100%;height:3px;
  background:var(--accent);opacity:.85;}
.tl{font-size:.66rem;letter-spacing:.11em;text-transform:uppercase;color:var(--muted);
  font-weight:800;}
.tv{font-size:1.55rem;font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;}
.tn{font-size:.78rem;color:var(--ink2);}
.m{display:grid;grid-template-columns:minmax(9rem,1fr) minmax(4.5rem,auto) minmax(0,1.9fr);
  gap:.25rem 1.1rem;align-items:baseline;padding:.85rem 0;
  border-bottom:1px solid var(--grid);}
.m:last-child{border-bottom:none;padding-bottom:0;}
.m:first-child{padding-top:0;}
.ml{font-weight:700;font-size:.95rem;}
.mv{font-variant-numeric:tabular-nums;font-weight:800;text-align:right;
  font-size:1.02rem;}
.mv.good{color:var(--goodtext);} .mv.crit{color:var(--critical);}
.mr{color:var(--ink2);font-size:.9rem;}
@media(max-width:34rem){.m{grid-template-columns:1fr auto;}.mr{grid-column:1/-1;}}
.chart{width:100%;height:auto;display:block;overflow:visible;}
.chartbox{background:var(--card);border:1px solid var(--ring);border-radius:14px;
  padding:1.4rem 1.5rem;display:flex;flex-direction:column;gap:.75rem;
  box-shadow:var(--shadow);}
.chartbox .cap{font-size:.85rem;color:var(--ink2);line-height:1.5;}
.legend{display:flex;flex-wrap:wrap;gap:.25rem 1rem;font-size:.8rem;color:var(--ink2);}
.legend span{display:inline-flex;align-items:center;gap:.35rem;}
.legend i{width:12px;height:3px;border-radius:2px;display:inline-block;}
.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round;}
.lnw{stroke-width:2.4;}
.body.up,.wick.up{fill:var(--good);stroke:var(--good);}
.body.dn,.wick.dn{fill:var(--critical);stroke:var(--critical);}
.wick{stroke-width:1.1;}
.vol{opacity:.42;}
.vol.up{fill:var(--good);} .vol.dn{fill:var(--critical);}
.lvl{stroke-width:1.4;stroke-dasharray:5 4;}
.lvl.res{stroke:var(--critical);} .lvl.sup{stroke:var(--goodtext);}
.lvllabel{font-size:10.5px;font-weight:700;}
.lvlbg{fill:var(--card);opacity:.88;}
.lvllabel.res{fill:var(--critical);} .lvllabel.sup{fill:var(--goodtext);}
.grid.dash{stroke-dasharray:4 4;}
.panel{fill:var(--ink2);font-size:11px;}
.ln.rsi{stroke:var(--ink2);stroke-width:1.6;}
.dlabel.rsi{fill:var(--ink2);}
.nowline{stroke:var(--ink);stroke-width:1;stroke-dasharray:2 3;opacity:.55;}
.nowchip{fill:var(--ink);}
.nowtext{fill:var(--card);font-size:11px;font-weight:700;text-anchor:middle;
  font-variant-numeric:tabular-nums;}
.dlabel.end{text-anchor:end;}
.rsizone{opacity:.09;}
.rsizone.hot{fill:var(--critical);} .rsizone.cold{fill:var(--goodtext);}
.readout{display:flex;flex-direction:column;gap:.5rem;}
.ro{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.3rem .8rem;
  align-items:baseline;padding:.7rem .85rem;border-radius:10px;
  background:var(--plane);border:1px solid var(--ring);}
.ro .dot{width:10px;height:10px;border-radius:50%;margin-top:.42rem;
  box-shadow:0 0 0 3px color-mix(in srgb,currentColor 18%,transparent);}
.ro .dot.good{background:var(--good);color:var(--good);}
.ro .dot.warn{background:var(--warning);color:var(--warning);}
.ro .dot.crit{background:var(--critical);color:var(--critical);}
.ro .rh{font-weight:800;letter-spacing:-.01em;}
.ro .rx{grid-column:2;color:var(--ink2);font-size:.92rem;line-height:1.5;}
.ratebar{display:flex;gap:2px;height:30px;margin:.2rem 0;}
.seg{display:flex;align-items:center;justify-content:center;border-radius:2px;
  min-width:1.4rem;}
.seg span{font-size:.76rem;font-weight:700;color:#fff;}
.seg.sb{background:#184f95;} .seg.b{background:#2a78d6;}
.seg.h{background:#898781;} .seg.s{background:#e34948;} .seg.ss{background:#96251f;}
.legend i.sw,.tvfoot i.sw{width:10px;height:10px;border-radius:2px;
  display:inline-block;flex:0 0 auto;}
.sw.sb{background:#184f95;} .sw.b{background:#2a78d6;}
.sw.h{background:#898781;} .sw.s{background:#e34948;} .sw.ss{background:#96251f;}
.ratekey{margin-top:.1rem;}
.targets{display:flex;flex-direction:column;gap:.4rem;margin-top:.9rem;}
.trail{position:relative;height:12px;border-radius:6px;
  background:linear-gradient(90deg,var(--grid),var(--axis));}
.tmark{position:absolute;top:-4px;width:4px;height:20px;border-radius:2px;}
.tmark.mean{background:var(--s1);} .tmark.now{background:var(--ink);}
.tends{display:flex;justify-content:space-between;font-size:.78rem;
  color:var(--muted);font-variant-numeric:tabular-nums;}
.tvchart{width:100%;height:460px;}
.tvlegend{display:flex;flex-wrap:wrap;gap:.3rem 1rem;font-size:.85rem;
  color:var(--ink2);font-variant-numeric:tabular-nums;min-height:1.5rem;
  padding:.35rem .5rem;background:var(--paper);border-radius:3px;}
.tvlegend b{color:var(--ink);}
.tvlegend .gain{color:var(--goodtext);font-weight:700;}
.tvlegend .loss{color:var(--critical);font-weight:700;}
.tvfoot{display:flex;flex-wrap:wrap;gap:.3rem 1.25rem;font-size:.78rem;
  color:var(--muted);align-items:center;}
.tvfoot span{display:inline-flex;align-items:center;gap:.35rem;}
.tvfoot a{color:var(--muted);text-decoration:underline;}
.howto{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
  gap:.75rem 1.25rem;font-size:.85rem;color:var(--ink2);}
.howto b{color:var(--ink);}
.swatch{display:inline-block;width:9px;height:9px;border-radius:2px;
  vertical-align:baseline;}
.grid{stroke:var(--grid);stroke-width:1;}
.axis{stroke:var(--axis);stroke-width:1;}
.tickmark{stroke:var(--axis);stroke-width:2;}
.tick{fill:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums;}
.dlabel{font-size:11.5px;font-weight:700;}
.blabel{fill:var(--ink2);font-size:11px;text-anchor:middle;
  font-variant-numeric:tabular-nums;}
.blabel.lft{text-anchor:start;}
.axlabel{fill:var(--muted);font-size:11px;text-anchor:middle;}
.axlabel.dim{font-size:10px;font-variant-numeric:tabular-nums;}
.nowdot{fill:var(--ink);stroke:var(--card);stroke-width:2;}
.nowlabel{fill:var(--ink);font-size:11.5px;font-weight:700;text-anchor:middle;
  font-variant-numeric:tabular-nums;}
.scroll{overflow-x:auto;}
footer{border-top:1px solid var(--grid);padding-top:1.5rem;color:var(--muted);
  font-size:.83rem;display:flex;flex-direction:column;gap:.6rem;line-height:1.55;}
a{color:var(--accent-ink);font-weight:600;}
a:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px;}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;}}
"""


def render(d: dict) -> str:
    sym, kind = d["symbol"], d["kind"]
    info, snap = d.get("info") or {}, d.get("snap")
    closes = d.get("closes") or []
    price = _f(d.get("price"))
    change = _f(d.get("change"))

    # ---- headline chips, computed from the data
    chips = []
    if snap and not getattr(snap, "error", None) and not d.get("liq_broken"):
        liq = snap.liquidity
        chips.append(_chip(f"Options liquidity: {liq}",
                           "good" if liq == "Good" else "warn" if liq == "OK" else "crit"))
        iv, hv = _f(snap.atm_iv), _f(snap.hv)
        if iv and hv:
            r = iv / hv
            chips.append(_chip(
                f"Premium edge {r:.2f}x",
                "good" if r >= 1.15 else "warn" if r >= 1.0 else "crit"))
    if d.get("trend"):
        t = d["trend"]
        chips.append(_chip(f"Trend: {t}",
                           "good" if t == "up" else "crit" if t == "down" else "warn"))
    days = d.get("earnings_days")
    if days is not None:
        # A negative count means the date has already passed - the calendar has
        # not rolled to the next quarter yet. "Earnings in -1 days" is nonsense,
        # and a just-reported quarter is the opposite of a pending risk.
        if days < 0:
            chips.append(_chip("Earnings just reported", "good"))
        else:
            chips.append(_chip(f"Earnings in {days} days",
                               "crit" if days <= 45 else "good"))

    # ---- tiles
    tiles = [_tile("Price", _fmt(price),
                   "n/a" if change is None else f"{change:+.2f}% today")]
    if kind == "stock":
        tiles += [
            _tile("Market value", _big(info.get("marketCap")), "size of the company"),
            _tile("P/E", _fmt(info.get("trailingPE"), 1), "paid per $1 of profit"),
            _tile("Net margin", _pct(info.get("profitMargins"), 0), "kept as profit"),
            _tile("Revenue growth", _pct(info.get("revenueGrowth"), 0), "past year"),
        ]
    if d.get("hv") is not None:
        tiles.append(_tile("How much it moves", _pct(d["hv"], 0), "per year, realized"))

    # ---- charts
    charts = []
    bars = d.get("ohlc") or []
    if bars and closes:
        legend = ('<div class="legend">'
                  '<span><i style="background:var(--s2)"></i>50-day average</span>'
                  '<span><i style="background:var(--s1)"></i>200-day average</span>'
                  '<span><i style="background:var(--good)"></i>Up day</span>'
                  '<span><i style="background:var(--critical)"></i>Down day</span>'
                  '</div>')
        howto = (
            '<div class="howto">'
            '<p><b>Each candle is one day.</b> The thin line is the highest and '
            'lowest price that day. The thick body runs from the opening price to '
            'the closing price. Green means it closed higher than it opened, red '
            'means lower.</p>'
            '<p><b>Moving averages</b> are the average closing price over the last '
            '50 or 200 days. Traders treat them as the trend line: price above '
            'them means buyers have been in control.</p>'
            '<p><b>Resistance</b> is a price the stock has repeatedly failed to '
            'climb above. <b>Support</b> is one it has repeatedly bounced off. '
            'The more times a level was touched, the more it matters.</p>'
            '<p><b>Volume</b> is how many shares traded. A big move on heavy '
            'volume is more convincing than the same move on light volume.</p>'
            '</div>')
        readout = chart_readout(bars, d.get("chart_closes") or closes)
        ro = "".join(
            f'<div class="ro"><span class="dot {t}"></span>'
            f'<span class="rh">{_e(head)}</span>'
            f'<span class="rx">{_e(why)}</span></div>'
            for t, head, why in readout)
        interactive = _interactive_chart(bars, d.get("chart_closes") or closes, sym)
        if interactive:
            charts.append(interactive)
            charts.append(f'<div class="chartbox">{howto}</div>')
        else:
            # No vendored library, or too little history - fall back to the
            # static SVG so the page still shows a chart.
            charts.append(
                f'<div class="chartbox"><h3>Six months of daily candles</h3>'
                f'<p class="cap">Hover any candle for that day\'s open, high, low '
                f'and close.</p>{legend}'
                f'<div class="scroll">'
                f'{_candle_chart(bars, d.get("chart_closes") or closes)}'
                f'</div>{howto}</div>')
        if ro:
            charts.append(
                f'<div class="chartbox"><h3>What this chart is saying right now</h3>'
                f'<p class="cap">The chart above shows what happened. This is what '
                f'it means.</p><div class="readout">{ro}</div></div>')
    elif closes:
        legend = ('<div class="legend">'
                  '<span><i style="background:var(--s1)"></i>Price</span>'
                  '<span><i style="background:var(--s2)"></i>50-day average</span>'
                  '<span><i style="background:var(--s3)"></i>200-day average</span>'
                  '</div>')
        charts.append(
            f'<div class="chartbox"><h3>One year of price</h3>'
            f'<p class="cap">A moving average is the average closing price over that '
            f'many days. Price above the line means buyers have been in control.</p>'
            f'{legend}<div class="scroll">{_price_chart(closes)}</div></div>')

    if closes:
        lv = d.get("levels") or []
        if lv and price:
            charts.append(
                f'<div class="chartbox"><h3>Where the price sits now</h3>'
                f'<p class="cap">The levels traders watch. Sitting close to one of '
                f'these usually means the next move is decided there.</p>'
                f'<div class="scroll">{_ladder(price, lv)}</div></div>')

    rs = d.get("rs_rows") or []
    if rs:
        legend = ('<div class="legend">'
                  f'<span><i style="background:var(--s1)"></i>{_e(sym)}</span>'
                  '<span><i style="background:var(--s2)"></i>SPY, the whole market</span>'
                  '</div>')
        charts.append(
            f'<div class="chartbox"><h3>Against the market</h3>'
            f'<p class="cap">Total return over each period. Beating the market '
            f'matters more than the raw number.</p>'
            f'{legend}<div class="scroll">{_rs_chart(rs, sym)}</div></div>')

    if snap and not getattr(snap, "error", None) and not d.get("liq_broken"):
        iv, hv = _f(snap.atm_iv), _f(snap.hv)
        if iv and hv:
            ratio = iv / hv
            verdict = ("You are paid MORE than it moves. Good for a seller."
                       if ratio >= 1.15 else
                       "About break-even. A thin edge."
                       if ratio >= 1.0 else
                       "You are paid LESS than it moves. Wrong side for a seller.")
            charts.append(
                f'<div class="chartbox"><h3>Is the premium worth it</h3>'
                f'<p class="cap">Option buyers pay for an expected amount of '
                f'movement. Compare that with how much the stock really moved. '
                f'<strong>{_e(verdict)}</strong></p>'
                f'<div class="scroll">{_iv_bar(iv, hv)}</div></div>')

    eps = d.get("eps") or []
    if eps and kind == "stock":
        charts.append(
            f'<div class="chartbox"><h3>Earnings: expected against delivered</h3>'
            f'<p class="cap">How far each quarter came in above or below what '
            f'analysts expected. Green is a beat, red is a miss.</p>'
            f'<div class="scroll">{_surprise_chart(eps)}</div></div>')

    # ---- fundamentals
    fund = ""
    if kind == "stock":
        rows = [
            _metric("P/E, trailing", _fmt(info.get("trailingPE"), 1),
                    "what you pay for each $1 of last year's profit"),
            _metric("P/E, forward", _fmt(info.get("forwardPE"), 1),
                    "same, using next year's expected profit"),
            _metric("PEG ratio", _fmt(info.get("pegRatio"), 2),
                    "P/E against growth. Under 1 is cheap for the growth"),
            _metric("Gross margin", _pct(info.get("grossMargins"), 0),
                    "kept from each sale before overheads"),
            _metric("Operating margin", _pct(info.get("operatingMargins"), 0),
                    "kept after running the business"),
            _metric("Net profit margin", _pct(info.get("profitMargins"), 0),
                    "kept as real profit after everything"),
            _metric("Return on equity", _pct(info.get("returnOnEquity"), 0),
                    "profit per $1 of owners' money. Over 20% is excellent"),
            _metric("Revenue growth", _pct(info.get("revenueGrowth"), 0), "past year"),
            _metric("Earnings growth", _pct(info.get("earningsGrowth"), 0), "past year"),
            _metric("Cash", _big(info.get("totalCash")),
                    f"against {_big(info.get('totalDebt'))} of debt"),
            _metric("Debt to equity", _fmt(info.get("debtToEquity"), 1),
                    "debt per $100 of owners' money. Under 100 is conservative"),
            _metric("Free cash flow", _big(info.get("freeCashflow")),
                    "cash left after running and investing"),
            _metric("Beta", _fmt(info.get("beta"), 2),
                    "1.0 moves with the market, 2.0 moves twice as hard"),
        ]
        fund = (f'<section class="stack"><p class="eyebrow">Fundamentals</p>'
                f'<h2>Is this a good business</h2>'
                f'<div class="card">{"".join(rows)}</div></section>')

    business = ""
    summary = (info.get("longBusinessSummary") or "").strip()
    if kind == "stock" and summary:
        short = " ".join(summary.split()[:75])
        business = (f'<section class="stack"><p class="eyebrow">The business</p>'
                    f'<h2>What it does</h2><div class="card">'
                    f'<p>{_e(info.get("sector") or "")} &#183; '
                    f'{_e(info.get("industry") or "")}</p>'
                    f'<p>{_e(short)}&#8230;</p></div></section>')

    name = info.get("longName") or info.get("shortName") or sym
    today = dt.date.today().isoformat()

    return f'''<title>{_e(sym)} research note</title>
<style>{CSS}</style>
<div class="wrap">

  <header class="masthead">
    <div class="mh-top">
      <span class="mh-eyebrow">Research note &#183; {_e(kind)}</span>
      <span class="mh-sym">{_e(sym)}</span>
    </div>
    <h1>{_e(name)}</h1>
    <div class="mh-price">
      <span class="mh-now">{_fmt(price)}</span>
      {"" if change is None else
       f'<span class="mh-chg {"up" if change >= 0 else "down"}">'
       f'{change:+.2f}% today</span>'}
    </div>
    <div class="chips">{"".join(chips)}</div>
    <div class="mh-meta">{_e(today)} &#183; {_e(d.get("mode_label", ""))}</div>
  </header>

  <section class="card accent">
    <p class="eyebrow">Bottom line</p>
    <!--BOTTOM_LINE-->
  </section>

  <div class="tiles">{"".join(tiles)}</div>

  {business}

  {"".join(charts)}

  {fund}

  {_analyst_block(info, d.get("ratings") or {}, price) if kind == "stock" else ""}

  <section class="stack">
    <p class="eyebrow">What happens next</p>
    <h2>The outlook</h2>
    <!--OUTLOOK-->
  </section>

  <footer>
    <p>Research and education only. Nothing here is advice to buy or sell, and it
    contains no strike selection, strategy or position sizing. Scenarios are
    possibilities with conditions attached, never forecasts - nobody knows what a
    price will do.</p>
    <p>Prices from the Yahoo feed, roughly 15 minutes delayed, pulled {_e(today)}.
    Fundamentals as last reported by the company.</p>
  </footer>

</div>'''
