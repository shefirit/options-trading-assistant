"""Reusable Streamlit pieces: the SOP checklist and the candidate table.

Kept beginner-friendly: green means good to go, red means a rule is broken,
yellow means allowed but pay attention, blue is a reminder.
"""

from __future__ import annotations

import html as _htmllib
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

# Accessible replacements for Streamlit's bright :red[]/:orange[]/:green[]/:blue[]
# markdown colors (those fail contrast - the built-in orange is only ~3.4:1).
_STATUS_TEXT = {
    "FAIL": "#C02A1B",   # ~5.9:1
    "WARN": "#8A4B08",   # ~7.2:1 (dark amber)
    "PASS": "#0A5C3F",   # ~7:1 (dark emerald)
    "INFO": "#0B5566",   # ~7:1 (teal)
}

from src.data.stock_analysis import StockAnalysis
from src.engine.models import Candidate, CheckStatus, ValidationReport
from ui import theme


def _esc(text: str) -> str:
    """Escape $ so Streamlit's markdown does not read '$...$' as a math formula
    (that bug turns '**$59.7**' into garbled italics with visible asterisks).
    """
    return text.replace("$", "\\$")


# Day first, the way she writes a date. The app used to print ISO everywhere
# (2026-09-30), which is right for the log file and wrong for a person reading
# a sentence. Storage stays ISO - only what she reads changes.
DATE_FMT = "DD/MM/YYYY"


def fmt_date(value, empty: str = "-") -> str:
    """A date as 30/09/2026. Accepts None so callers need no guard."""
    return f"{value:%d/%m/%Y}" if value else empty


def quality_label(symbol: str, grade: str | None) -> str:
    """What to print in the Quality column when there may be no letter grade.

    This used to be `grade or "ETF"`, which quietly told a lie: a STOCK whose
    grade could not be computed was printed as "ETF". That happens routinely on
    the hosted app, where Yahoo throttles the fundamentals call from datacenter
    IPs, so a real company like SOFI showed up looking like a fund.

    A missing grade and an ETF are different facts, so say which one it is.
    """
    if grade:
        return grade
    from src.engine.config_loader import underlying_kind
    kind = underlying_kind(symbol or "")
    if kind == "etf":
        return "ETF"
    if kind == "index":
        return "Index"
    return "—"      # a stock we could not grade, not a fund


# "unknown" is grey and a question mark on purpose - it means the number did
# not load, which is not a caution and must not wear a warning colour.
_STATUS_COLOR = {"good": "green", "ok": "orange", "watch": "red",
                 "unknown": "gray"}
_STATUS_ICON = {"good": "✅", "ok": "➖", "watch": "⚠️", "unknown": "❔"}


def render_market_tiles(tiles: list[dict], market_open: bool = True) -> None:
    """The index + VIX strip as compact HTML tiles that wrap two-up on a phone
    (st.columns would stack them into a tall list there, pushing the day's
    verdict below the fold). VIX colors are inverted: falling fear = green."""
    cells = []
    for t in tiles:
        sym = t["symbol"]
        label = "VIX (fear)" if sym == "VIX" else sym
        price = f"{t['price']:,.0f}" if t.get("price") else "n/a"
        pct = t.get("change_pct")
        delta_html = ""
        if market_open and pct is not None:
            good = (pct <= 0) if sym == "VIX" else (pct >= 0)
            color = "#0A5C3F" if good else "#C02A1B"
            arrow = "▲" if pct >= 0 else "▼"
            delta_html = (f"<div class='ota-tile-delta' style='color:{color};'>"
                          f"{arrow} {pct:+.2f}%</div>")
        cells.append(
            f"<div class='ota-tile'><div class='ota-tile-label'>{label}</div>"
            f"<div class='ota-tile-value'>{price}</div>{delta_html}</div>")
    st.markdown(f"<div class='ota-tiles'>{''.join(cells)}</div>", unsafe_allow_html=True)


def render_news(items: list) -> None:
    """Recent market headlines as a compact list: linked title + source + how
    long ago. Headlines only (never article text), each opening in a new tab -
    context to read, not a signal to trade."""
    rows = []
    for n in items:
        # Escape HTML, then '$' as an entity so a headline like "$10K" never
        # trips Streamlit's '$...$' LaTeX rendering.
        title = _htmllib.escape(n.title).replace("$", "&#36;")
        url = _htmllib.escape(n.url, quote=True)
        source = _htmllib.escape(n.source)
        age = _htmllib.escape(n.age())
        meta = f"{source} · {age}" if age else source
        rows.append(
            f"<div class='ota-news-item'>"
            f"<a class='ota-news-title' href='{url}' target='_blank' "
            f"rel='noopener noreferrer'>{title}</a>"
            f"<div class='ota-news-meta'>{meta}</div></div>")
    st.markdown(f"<div class='ota-news'>{''.join(rows)}</div>", unsafe_allow_html=True)


def _fit_chip(score: float, rank: int) -> str:
    """The fit label for one strategy, from its SCORE rather than its position.

    Position alone was misleading: in a ranking of nine, third place could be a
    genuinely good fit or the least-bad of a bad bunch, and the old fixed
    "1/2/3 · Best/Also workable/Weaker" chips could not tell those apart. A
    score-based band can, and it also means the chips visibly change as
    conditions move even when the ORDER happens to hold.
    """
    if score >= 1.5:
        tone, tag = "green", "Strong fit"
    elif score >= 0.5:
        tone, tag = "indigo", "Workable"
    elif score >= -0.5:
        tone, tag = "amber", "Weak fit"
    else:
        tone, tag = "red", "Fighting conditions"
    return theme.chip(f"{rank} · {tag}", tone)


def render_strategy_fit(suggestions: list, show_instrument: bool = False) -> None:
    """The ranked strategy board: each strategy with a fit chip and the one-line
    reason - a vertical list, so it reads the same on a phone.

    show_instrument adds what you would trade it on and what you need to already
    have, which the whole-SOP board needs and the index-only list does not.
    """
    # Not "today": this is a multi-week read off 20- and 50-day averages, and
    # calling it daily is what made an unchanged-but-correct ranking look stuck.
    for i, s in enumerate(suggestions):
        # The advanced flag rides next to the fit chip, not buried in the
        # reason - a good fit score on a strategy that can run away from you
        # must not read as a simple green light.
        flag = theme.chip("Advanced", "red") if getattr(s, "advanced", False) else ""
        st.markdown(
            _fit_chip(getattr(s, "score", 0.0), i + 1)
            + f" <b>{_htmllib.escape(s.name)}</b> {flag}",
            unsafe_allow_html=True)
        theme.note(s.reason)
        if show_instrument and getattr(s, "requires", ""):
            theme.note(f"Needs: {s.requires}.")


def render_strategy_board(board: list) -> None:
    """The whole SOP ranked, split by what you can actually trade it on.

    Split rather than one long list because the two halves answer different
    questions. The index strategies are the ones you can open today with cash
    alone; the US-style ones mostly need you to already own something, so a
    covered call sitting at the top of a single merged list would read as
    "do this now" when the honest answer is "if you hold 100 shares".
    """
    index_side = [s for s in board if s.instrument == "index"]
    us_side = [s for s in board if s.instrument != "index"]

    if index_side:
        st.markdown("**On the index (SPX, NDX, RUT, XSP)** - cash-settled, no shares "
                    "involved, and no early assignment.")
        render_strategy_fit(index_side)
    if us_side:
        st.write("")
        st.markdown("**On stocks and ETFs** - ranked on the same overall market read. "
                    "The individual name still has to pass its own check in 💰 Picks.")
        render_strategy_fit(us_side, show_instrument=True)


def render_pulse_grid(rows: list[dict], market_open: bool = True) -> None:
    """The sector-pulse color grid: small tinted tiles grouped Indexes /
    Sectors / Other assets. The number stays dark ink on every tile - the
    arrow and the +/- sign carry the direction, so color is never the only
    signal. rows come from market_read.build_pulse_rows."""
    from src.data.market_read import GROUP_ORDER

    suffix = "" if market_open else (" <span style='font-weight:600;letter-spacing:0;"
                                     "text-transform:none;color:#35463D;'>"
                                     "(last close - market closed)</span>")
    parts = []
    for gi, group in enumerate(GROUP_ORDER):
        tiles = [r for r in rows if r["group"] == group]
        if not tiles:
            continue
        parts.append(f"<div class='ota-pulse-group'>{group}"
                     f"{suffix if gi == 0 else ''}</div>")
        cells = []
        for r in tiles:
            pct = r["change_pct"]
            label = _htmllib.escape(r["label"])
            sym = _htmllib.escape(r["symbol"])
            if pct is None:
                cls, val = "ota-pulse-tile", "n/a"
            elif pct > 0:
                cls = "ota-pulse-tile ota-pulse-up"
                val = f"<span style='color:#0A5C3F;'>▲</span> +{pct:.2f}%"
            elif pct < 0:
                cls = "ota-pulse-tile ota-pulse-down"
                val = f"<span style='color:#C02A1B;'>▼</span> {pct:.2f}%"
            else:
                cls, val = "ota-pulse-tile", "0.00%"
            cells.append(
                f"<div class='{cls}'><div class='ota-pulse-label'>{label} "
                f"<span class='ota-pulse-sym'>{sym}</span></div>"
                f"<div class='ota-pulse-val'>{val}</div></div>")
        parts.append(f"<div class='ota-pulse'>{''.join(cells)}</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_stock_analysis(a: StockAnalysis) -> None:
    """The metric-by-metric checks behind a stock's grade (no header - the
    overview above already shows name, price, and verdict)."""
    col_f, col_t = st.columns(2)
    with col_f:
        st.markdown("**Fundamentals** (is it a good company?)")
        for m in a.fundamentals:
            _metric_line(m)
    with col_t:
        st.markdown("**Technicals** (what is the price doing?)")
        for m in a.technicals:
            _metric_line(m)


def _metric_line(m) -> None:
    color = _STATUS_COLOR.get(m.status, "gray")
    icon = _STATUS_ICON.get(m.status, "")
    st.markdown(_esc(f"{icon} **{m.label}:** :{color}[{m.value}] - {m.read}"))


_GRADE_COLOR = {"A": "#0A6A49", "B": "#12855C", "C": "#B45309", "D": "#C2410C", "F": "#C02A1B"}


def _fmt_big(n) -> str:
    """$4.7T style formatting. Uses &#36; so markdown never sees a raw '$'."""
    if not n:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= size:
            return f"&#36;{n / size:.1f}{unit}"
    return f"&#36;{n:,.0f}"


def _score_card(analysis: StockAnalysis, info: dict) -> str:
    """The 'Quality score' box - stats strip with the grade badge, like the
    EarningsHub score card. Pure HTML so dollar signs render safely.
    """
    # Three badges, matching the three things that can be true. A fund gets the
    # "ETF" badge premium_finder already uses rather than a letter from company
    # metrics it does not have; a name whose data never arrived gets a question
    # mark in neutral grey, because an amber "-" reads as a poor score.
    unknown = analysis.kind == "unknown" or (
        analysis.grade is None and not analysis.is_fund)
    badge = analysis.grade or ("ETF" if analysis.is_fund else "?")
    gcolor = _GRADE_COLOR.get(
        analysis.grade,
        "#3730A3" if analysis.is_fund else "#4E625A" if unknown else "#B45309")
    if unknown:
        heading, sub = "Quality score", "could not be worked out - see below"
    elif analysis.is_fund:
        heading, sub = "What it is", "a basket of holdings, not one company"
    else:
        heading, sub = "Quality score", "(from the checks below)"
    pe = info.get("trailingPE")
    fpe = info.get("forwardPE")
    ps = info.get("priceToSalesTrailing12Months")
    rev = info.get("totalRevenue")
    rg = info.get("revenueGrowth")
    eps = info.get("trailingEps")
    eg = info.get("earningsGrowth")

    def stat(label, value):
        return (f"<span style='margin-right:18px;white-space:nowrap;'>"
                f"<span style='color:#35463D;'>{label}</span> "
                f"<b>{value}</b></span>") if value else ""

    row1 = "".join([
        stat("Mkt Cap", _fmt_big(info.get("marketCap"))),
        stat("P/E", f"{pe:.1f}" if pe else ""),
        stat("Fwd P/E", f"{fpe:.1f}" if fpe else ""),
        stat("P/S", f"{ps:.1f}" if ps else ""),
    ])
    row2 = "".join([
        stat("Revenue (12mo)", f"{_fmt_big(rev)}" + (f" <span style='color:#0B7A54;'>({rg*100:+.0f}%)</span>" if rg is not None else "") if rev else ""),
        stat("EPS (12mo)", f"&#36;{eps:.2f}" + (f" <span style='color:#0B7A54;'>({eg*100:+.0f}%)</span>" if eg is not None else "") if eps else ""),
    ])
    return (
        f"<div style='background:#F2F9F5;border:1px solid #DAE7E0;border-radius:14px;"
        f"padding:12px 16px;display:flex;justify-content:space-between;align-items:center;"
        f"gap:12px;margin:4px 0 10px;'>"
        f"<div style='line-height:2;'>"
        f"<div style='font-weight:700;margin-bottom:2px;'>"
        f"{heading} "
        f"<span style='color:#35463D;font-weight:500;font-size:0.85rem;'>"
        f"{sub}</span></div>"
        f"<div>{row1}</div><div>{row2}</div></div>"
        f"<div style='background:{gcolor};color:#fff;border-radius:12px;min-width:52px;"
        f"height:52px;display:flex;align-items:center;justify-content:center;"
        f"font-size:{'1.05rem' if analysis.is_fund else '1.5rem'};font-weight:800;'>"
        f"{badge}</div></div>"
    )


# Time ranges for the price chart (label -> yfinance period).
PRICE_RANGES = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "YTD": "ytd",
                "1Y": "1y", "2Y": "2y", "Max": "max"}


def period_change(frame) -> tuple[float, float] | None:
    """Dollar and percent change across a price frame, or None if it cannot be
    computed honestly.

    The price frames come back with NaN closes at the edges often enough to
    matter - a holiday on the boundary, a row with no print. Taking .iloc[0]
    blindly gave NaN, and NaN formats as text: the header read "$nan (+nan%)"
    with a red down arrow, because NaN >= 0 is False. Better to show nothing
    than a number that is not one.
    """
    if frame is None or "Close" not in getattr(frame, "columns", []):
        return None
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(closes) < 2:
        return None
    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    if first <= 0:
        return None
    return last - first, (last - first) / first * 100


def render_price_chart(frame, earnings_dates: list | None = None) -> None:
    """A modern stock chart: thin line + soft gradient, y-axis zoomed to the
    data (not from zero), hover crosshair with price tooltip, and dashed 'E'
    markers on past earnings dates - like the chart Rita liked on EarningsHub.
    """
    df = frame.reset_index()
    df.columns = ["Date", "Close"]
    # Same NaN trap as the header above: a NaN at either end made every
    # comparison False, so a stock that had risen all year drew in red.
    change = period_change(frame)
    rising = change is None or change[0] >= 0
    color = "#0B7A54" if rising else "#DC2626"
    rgba = "5,150,105" if rising else "220,38,38"

    base = alt.Chart(df).encode(
        x=alt.X("Date:T", axis=alt.Axis(title=None, format="%b '%y", grid=False,
                                        labelColor="#35463D", domainColor="#DAE7E0")),
    )
    area = base.mark_area(
        line={"color": color, "strokeWidth": 2},
        interpolate="monotone",
        color=alt.Gradient(
            gradient="linear",
            stops=[alt.GradientStop(color=f"rgba({rgba},0.02)", offset=0),
                   alt.GradientStop(color=f"rgba({rgba},0.22)", offset=1)],
            x1=1, x2=1, y1=1, y2=0,
        ),
    ).encode(
        y=alt.Y("Close:Q",
                scale=alt.Scale(zero=False, nice=True),
                axis=alt.Axis(title=None, format="$,.0f", labelColor="#35463D",
                              gridColor="#EEF2F7", domainOpacity=0)),
    )

    hover = alt.selection_point(fields=["Date"], nearest=True,
                                on="pointerover", empty=False)
    points = base.mark_point(size=75, filled=True, color=color).encode(
        y="Close:Q",
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    )
    rule = base.mark_rule(color="#94A3B8", strokeDash=[3, 3]).encode(
        opacity=alt.condition(hover, alt.value(0.7), alt.value(0)),
        tooltip=[alt.Tooltip("Date:T", format="%b %d, %Y"),
                 alt.Tooltip("Close:Q", format="$,.2f", title="Price")],
    ).add_params(hover)

    layers = [area, points, rule]

    # Dashed vertical lines + a small "E" where past earnings reports landed.
    if earnings_dates:
        stamps = pd.to_datetime([d for d in earnings_dates if d])
        lo, hi = df["Date"].min(), df["Date"].max()
        stamps = [s for s in stamps if lo <= s <= hi]
        if stamps:
            edf = pd.DataFrame({"Date": stamps})
            layers.append(alt.Chart(edf).mark_rule(
                color="#94A3B8", strokeDash=[4, 4], opacity=0.35).encode(x="Date:T"))
            layers.append(alt.Chart(edf).mark_text(
                text="E", dy=0, fontSize=11, fontWeight="bold", color="#0B7A54",
            ).encode(x="Date:T", y=alt.value(248),
                     tooltip=alt.value("Earnings report")))

    chart = alt.layer(*layers).properties(height=260).configure_view(strokeOpacity=0)
    st.altair_chart(chart, width="stretch")


def render_stock_overview(
    analysis: StockAnalysis,
    info: dict,
    frame_loader,
    change_pct,
    analysts: dict,
    eps_history: list,
    key_prefix: str = "main",
) -> None:
    """EarningsHub-style overview: score card with grade, big price + period
    change, range-selectable chart with earnings markers, analyst ratings bar,
    and the earnings beat/miss history.

    frame_loader: callable(period_str) -> price DataFrame (lets the range
    buttons re-fetch without this component knowing about the data source).
    """
    # ---- name + sector ----
    st.markdown(f"### {analysis.symbol} - {analysis.name}")
    if analysis.sector:
        theme.note(f"Sector: {analysis.sector}")

    # ---- quality score card (grade + key stats) ----
    st.markdown(_score_card(analysis, info), unsafe_allow_html=True)

    # ---- price + period change + range selector ----
    pr_col, rng_col = st.columns([3, 2])
    with rng_col:
        rng_key = f"rng_{key_prefix}_{analysis.symbol}"
        try:
            choice = st.segmented_control(
                "Range", list(PRICE_RANGES), default="1Y",
                key=rng_key, label_visibility="collapsed")
        except Exception:   # older Streamlit fallback
            choice = st.radio("Range", list(PRICE_RANGES), index=4, horizontal=True,
                              key=rng_key, label_visibility="collapsed")
    period = PRICE_RANGES.get(choice or "1Y", "1y")
    frame = frame_loader(period)

    with pr_col:
        if analysis.price:
            change_html = ""
            change = period_change(frame)
            if change is not None:
                diff, pct = change
                ccolor = "#0B7A54" if diff >= 0 else "#DC2626"
                arrow = "▲" if diff >= 0 else "▼"
                change_html = (f"<span style='color:{ccolor};font-weight:700;'>"
                               f"{arrow} &#36;{abs(diff):,.2f} ({pct:+.1f}%)</span>"
                               f"<span style='color:#35463D;'> · past {choice or '1Y'}</span>")
            today_html = (f"<span style='color:#35463D;font-size:0.9rem;'> · today "
                          f"{change_pct:+.2f}%</span>") if change_pct is not None else ""
            st.markdown(
                f"<div style='font-size:2rem;font-weight:800;line-height:1.1;'>"
                f"&#36;{analysis.price:,.2f}</div>"
                f"<div>{change_html}{today_html}</div>",
                unsafe_allow_html=True,
            )

    # ---- the chart itself, with earnings markers ----
    if frame is not None and len(frame) > 5:
        earnings_dates = [q.get("date") for q in eps_history if q.get("date")]
        render_price_chart(frame, earnings_dates)
    else:
        theme.note("Price history unavailable right now.")

    # ---- analyst ratings + earnings beats, side by side ----
    col_a, col_e = st.columns(2)

    with col_a:
        st.markdown("**🧑‍💼 What Wall Street analysts say**")
        total = sum(analysts.values()) if analysts else 0
        if total:
            buy = analysts.get("strong_buy", 0) + analysts.get("buy", 0)
            hold = analysts.get("hold", 0)
            sell = analysts.get("sell", 0) + analysts.get("strong_sell", 0)
            verdict = ("BUY" if buy / total >= 0.5 else
                       "SELL" if sell / total >= 0.4 else "HOLD")
            vcolor = {"BUY": "#0B7A54", "HOLD": "#B45309", "SELL": "#DC2626"}[verdict]
            st.markdown(
                f"<span style='background:{vcolor};color:#fff;border-radius:10px;"
                f"padding:2px 14px;font-weight:800;'>{verdict}</span> "
                f"<span style='font-size:0.95rem;'>({total} analysts)</span>",
                unsafe_allow_html=True,
            )
            for label, n, color in (("Buy", buy, "#0B7A54"), ("Hold", hold, "#B45309"),
                                    ("Sell", sell, "#DC2626")):
                pct = n / total * 100
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:8px;margin:4px 0;'>"
                    f"<div style='width:44px;font-weight:600;'>{label}</div>"
                    f"<div style='flex:1;background:#EEF2F7;border-radius:6px;height:14px;'>"
                    f"<div style='width:{pct:.0f}%;background:{color};height:14px;"
                    f"border-radius:6px;'></div></div>"
                    f"<div style='width:44px;text-align:right;'>{pct:.0f}%</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            theme.note("No analyst data available for this name.")

    with col_e:
        st.markdown("**🎯 Earnings: expected vs delivered**")
        if eps_history:
            def _result(q):
                return "Delivered" if q["beat"] is None else ("Beat" if q["beat"] else "Missed")
            df = pd.DataFrame({
                "Quarter": [q["label"] for q in eps_history],
                "EPS": [q["actual"] for q in eps_history],
                "Expected": [q["estimate"] for q in eps_history],
                "Surprise": [f"{q['surprise_pct']:+.1f}%" if q["surprise_pct"] is not None
                             else "n/a" for q in eps_history],
                "Result": [_result(q) for q in eps_history],
            })
            scatter = alt.Chart(df).mark_circle(size=110, opacity=1).encode(
                x=alt.X("Quarter:N", sort=None,
                        axis=alt.Axis(title=None, labelAngle=-45,
                                      labelColor="#35463D", domainColor="#DAE7E0")),
                y=alt.Y("EPS:Q", scale=alt.Scale(zero=False, nice=True),
                        axis=alt.Axis(title=None, format="$,.2f",
                                      labelColor="#35463D", gridColor="#EEF2F7",
                                      domainOpacity=0)),
                color=alt.Color("Result:N", legend=None,
                                scale=alt.Scale(domain=["Beat", "Missed", "Delivered"],
                                                range=["#0B7A54", "#DC2626", "#5F7169"])),
                tooltip=[alt.Tooltip("Quarter:N"),
                         alt.Tooltip("Expected:Q", format="$,.2f", title="Analysts expected"),
                         alt.Tooltip("EPS:Q", format="$,.2f", title="Delivered"),
                         alt.Tooltip("Surprise:N", title="Surprise"),
                         alt.Tooltip("Result:N")],
            ).properties(height=210).configure_view(strokeOpacity=0)
            st.altair_chart(scatter, width="stretch")
            graded = [q for q in eps_history if q["beat"] is not None]
            if graded:
                beats = sum(1 for q in graded if q["beat"])
                misses = len(graded) - beats
                theme.note(f"🟢 beat / 🔴 missed analyst estimates (hover a dot for the numbers) - "
                           f"beat in **{beats} of the last {len(graded)} quarters**"
                           + (f", missed {misses}" if misses else "")
                           + ". Companies that beat steadily tend to hold up better.")
            else:
                theme.note("Delivered earnings per share by quarter (hover a dot for the numbers). "
                           "Analyst estimates weren't available from the data source, so beat/miss "
                           "isn't shown.")
        else:
            theme.note("No earnings history available for this name.")


def render_tv_ratings(ratings: dict, title: str = "TradingView technical rating") -> None:
    """TradingView's verdict, shown as an indicator vote: TradingView runs ~26
    technical indicators (moving averages, RSI, MACD...) and each one votes
    buy, neutral, or sell. We show the tally as a colored bar so it reads at
    a glance instead of as cryptic numbers.
    """
    if not ratings:
        return
    st.markdown(f"**📊 {title}**")
    verdict_color = {"green": "#0B7A54", "orange": "#B45309", "red": "#DC2626"}
    cols = st.columns(len(ratings))
    for col, (label, r) in zip(cols, ratings.items()):
        total = max(r.buy + r.neutral + r.sell, 1)
        b, n, s = (r.buy / total * 100, r.neutral / total * 100, r.sell / total * 100)
        vc = verdict_color.get(r.color, "#B45309")
        window = "on daily charts" if label == "daily" else "on weekly charts (longer view)"
        with col:
            st.markdown(
                f"<div style='margin-bottom:2px;'>{label.title()}: "
                f"<b style='color:{vc};'>{r.recommendation}</b></div>"
                # one bar, three colored segments = the indicator vote
                f"<div style='display:flex;height:12px;border-radius:6px;overflow:hidden;"
                f"border:1px solid #DAE7E0;max-width:340px;'>"
                f"<div style='width:{b:.0f}%;background:#0B7A54;'></div>"
                f"<div style='width:{n:.0f}%;background:#CBD5E1;'></div>"
                f"<div style='width:{s:.0f}%;background:#DC2626;'></div></div>"
                f"<div style='font-size:0.85rem;color:#35463D;margin-top:2px;'>"
                f"{total} indicators {window}: "
                f"<span style='color:#0B7A54;font-weight:600;'>{r.buy} buy</span> · "
                f"{r.neutral} neutral · "
                f"<span style='color:#DC2626;font-weight:600;'>{r.sell} sell</span></div>",
                unsafe_allow_html=True,
            )
    theme.note("How to read this: TradingView runs ~26 technical indicators (moving averages, "
               "RSI, MACD...). Each votes buy, neutral, or sell - the verdict is the tally. "
               "A second opinion, not a signal to trade on its own.")


_VERDICT_STYLE = {
    "sell": ("✅ Good to sell", "#0B7A54", "#ECFDF5", "#A7F3D0"),
    "okay": ("⚠️ Okay", "#B45309", "#FFFBEB", "#FDE68A"),
    "skip": ("❌ Skip", "#B91C1C", "#FEF2F2", "#FECACA"),
}


def render_premium_cards(snapshots: list) -> None:
    """Verdict-first cards: for each name, the bottom-line call, the income, and
    one reason. Detail lives behind the picker below (progressive disclosure)."""
    for s in snapshots:
        if s.error:
            st.markdown(
                f"<div style='border:1px solid #DAE7E0;border-radius:14px;padding:12px 16px;"
                f"margin-bottom:10px;color:#35463D;'><b>{s.symbol}</b> - {s.error}</div>",
                unsafe_allow_html=True)
            continue

        label, vcolor, vbg, vborder = _VERDICT_STYLE.get(s.verdict, _VERDICT_STYLE["okay"])
        gcolor = _GRADE_COLOR.get(s.grade, "#0B7A54")   # ETF/None -> indigo
        grade_txt = quality_label(s.symbol, s.grade)
        rich_color = ("#0B7A54" if s.richness == "Rich"
                      else "#B45309" if s.richness == "Fair" else "#B91C1C")
        cushion = (f" · falls to ${s.breakeven:,.0f} before you lose "
                   f"({s.cushion_pct:.0f}% cushion)" if s.cushion_pct is not None else "")
        flags = ("<div style='color:#B45309;margin-top:4px;font-size:0.95rem;'>⚠️ "
                 + " · ".join(s.flags) + "</div>") if s.flags else ""

        st.markdown(
            f"""
            <div style="border:1px solid #DAE7E0;border-radius:14px;padding:14px 16px;
                        margin-bottom:10px;background:#fff;box-shadow:0 1px 3px rgba(15,23,42,.05);">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                <div style="display:flex;align-items:center;gap:10px;">
                  <span style="background:{gcolor};color:#fff;border-radius:8px;padding:2px 9px;
                               font-weight:800;font-size:0.95rem;">{grade_txt}</span>
                  <span style="font-size:1.25rem;font-weight:800;">{s.symbol}</span>
                  <span style="color:#35463D;">${s.price:,.2f}</span>
                </div>
                <span style="background:{vbg};border:1px solid {vborder};color:{vcolor};
                             border-radius:999px;padding:3px 14px;font-weight:700;
                             white-space:nowrap;">{label}</span>
              </div>
              <div style="margin-top:8px;font-size:1.1rem;">
                <b>${s.credit_dollars:,.0f}/month</b>
                <span style="color:#35463D;">({s.monthly_yield_pct:.1f}%)</span>
                &nbsp;·&nbsp; Premium <b style="color:{rich_color};">{s.richness}</b>
                &nbsp;·&nbsp; {s.action}
              </div>
              <div style="color:#475569;margin-top:4px;">{s.verdict_reason}{cushion}</div>
              {flags}
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_premium_detail(s) -> None:
    """The full, clear picture for one symbol: odds, safety, both sides, the plan."""
    if s.error:
        st.warning(f"{s.symbol}: {s.error}")
        return
    grade_txt = f"  ·  quality {s.grade}" if s.grade else "  ·  ETF"
    st.markdown(f"### {s.symbol} · ${s.price:,.2f}  ·  trend {s.trend}{grade_txt}")

    # The numbers a beginner actually decides on.
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Income / month", f"${s.credit_dollars:,.0f}")
    m2.metric("As % of cash", f"{s.monthly_yield_pct:.2f}%")
    m3.metric("Premium deal", s.richness,
              help="Rich = you're paid more than this stock's usual moves would justify (good "
                   "for you). Thin = it moves a lot but pays little (bad). Fair = normal.")
    m4.metric("Vol rank", "n/a" if s.iv_rank is None else f"{s.iv_rank:.0f}",
              help="0-100: how expensive this name's options are compared with its own past "
                   "year. Above 70 means options are near their priciest all year, which is "
                   "when selling pays best. Below 30 means they are cheap and you are being "
                   "paid little. Note: ranked against how much the stock has actually moved, "
                   "since no free source publishes a year of implied-volatility history.")
    m5.metric("Can trade?", s.liquidity,
              help=f"Bid-ask spread {s.spread_pct:.0f}% of price, open interest "
                   f"{s.open_interest or 0:,}." if s.spread_pct else None)

    for f in s.flags:
        st.warning(f"⚠️ {f}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Selling puts** (get paid, maybe buy shares cheaper)")
        st.markdown(_esc(
            f"- Sell the **${s.short_strike:g} put** (~{s.dte} days, delta {s.short_delta})\n"
            f"- Collect **${s.credit_dollars:,.0f}** = **{s.monthly_yield_pct:.2f}%** for the month\n"
            f"- Breakeven **${s.breakeven:,.2f}** · Strategy: **{s.strategy}**"))
    with c2:
        st.markdown("**Selling calls** (income if you own the shares)")
        if s.call_strike:
            st.markdown(_esc(
                f"- Sell the **${s.call_strike:g} call** (~{s.dte} days)\n"
                f"- Collect **${s.call_credit_dollars:,.0f}** = **{s.call_yield_pct:.2f}%** for the month\n"
                f"- Strategy: **Covered Call** (needs 100 shares)"))
        else:
            theme.note("No suitable call found for a covered call right now.")

    deal = {
        "Rich": "Premium is **Rich** - you're paid more than this stock's usual movement would "
                "justify. That's a good deal for you as the seller.",
        "Fair": "Premium is **Fair** - about normal for how much this stock moves.",
        "Thin": "Premium is **Thin** - the stock swings a lot but pays little for the risk. A poor "
                "deal; look for a name that pays more.",
    }.get(s.richness)
    if deal:
        theme.note(deal)
    st.warning(_esc(f"⚠️ Risk: {s.risk_note}"))
    st.success(_esc(f"💡 {s.recommendation}"))


_VERDICT_WORD = {"sell": "✅ Good to sell", "okay": "⚠️ Okay", "skip": "❌ Skip"}


def premium_dataframe(snapshots: list) -> "pd.DataFrame":
    """A lean, sortable comparison of every scanned name - only the handful of
    things a beginner needs to decide. Click any column header to sort."""
    rows = []
    for s in snapshots:
        if s.error:
            rows.append({"Symbol": s.symbol, "Verdict": "— " + s.error})
            continue
        rows.append({
            "Symbol": s.symbol,
            "Verdict": _VERDICT_WORD.get(s.verdict, s.verdict),
            "Quality": quality_label(s.symbol, s.grade),
            "Sell put at": s.short_strike,
            "Delta": s.short_delta,
            "Income $/mo": s.credit_dollars,
            "Yield %/mo": s.monthly_yield_pct,
            "Yield %/yr": s.annualized_yield_pct,
            "Premium deal": s.richness,
            "Vol rank": s.iv_rank,
            "Watch out": ("⚠ earnings first" if s.earnings_before_expiry
                          else "⚠ hard to trade" if s.liquidity == "Thin" else "—"),
        })
    return pd.DataFrame(rows)


# Column formatting for the premium comparison table (st.dataframe column_config).
def premium_column_config():
    import streamlit as _st
    return {
        "Verdict": _st.column_config.TextColumn(help="The bottom-line call for a beginner."),
        "Quality": _st.column_config.TextColumn(
            help="Company quality grade A-F (ETFs are baskets, so shown as ETF). Matters because "
                 "a put can leave you owning the shares."),
        "Income $/mo": _st.column_config.NumberColumn(format="$%d",
            help="Cash you collect for one contract this month."),
        "Sell put at": _st.column_config.NumberColumn(format="%.0f",
            help="The strike to sell, about 0.30 delta - your SOP's cash-secured-put strike."),
        "Delta": _st.column_config.NumberColumn(format="%.2f",
            help="Roughly the chance the put finishes in the money and you are assigned the "
                 "shares. Your SOP sells around 0.30."),
        "Yield %/mo": _st.column_config.NumberColumn(format="%.2f%%",
            help="That income as a % of the cash you set aside - the fair way to compare names."),
        "Yield %/yr": _st.column_config.NumberColumn(format="%.0f%%",
            help="The same rate repeated for a year. Simple, not compounded, and it assumes "
                 "you keep finding the same trade - use it to compare names, not as a forecast."),
        "Premium deal": _st.column_config.TextColumn(
            help="Is the premium a good deal for the risk? Rich = you're paid MORE than this "
                 "stock's usual swings would justify (good for you). Thin = it swings a lot but "
                 "pays little (bad). Fair = about normal."),
        "Vol rank": _st.column_config.NumberColumn(format="%.0f",
            help="0-100: how expensive this name's options are versus its own past year. Above "
                 "70 is near the priciest all year, the best time to sell. Below 30 is cheap "
                 "and pays you little. Ranked against how much the stock has actually moved, "
                 "because no free source publishes a year of implied-volatility history."),
    }


def render_advice(advice) -> None:
    """The options-strategy plan for a symbol: outlook, the recommended play
    (from HER eight strategies), alternatives, and cautions - plain English.
    """
    tone = {"bullish": "green", "neutral": "amber",
            "bearish": "red", "avoid": "red"}[advice.outlook]
    st.markdown(
        theme.chip(f"Outlook: {advice.outlook.title()}", tone)
        + theme.chip(advice.kind.upper(), "indigo"),
        unsafe_allow_html=True,
    )
    if advice.outlook_reasons:
        theme.note(_esc(" · ".join(advice.outlook_reasons)))

    if advice.primary:
        st.markdown(
            f"<div class='ota-eyebrow'>Recommended play</div>"
            f"<div style='font-size:1.35rem;font-weight:800;margin:2px 0 4px;'>"
            f"{advice.primary.name}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(_esc(advice.primary.why))
    else:
        st.markdown(
            "<div class='ota-eyebrow'>Recommended play</div>"
            "<div style='font-size:1.2rem;font-weight:800;margin:2px 0 4px;'>"
            "No safe play here right now</div>",
            unsafe_allow_html=True,
        )

    if advice.alternatives:
        st.markdown("**Also worth considering:**")
        for alt_play in advice.alternatives:
            st.markdown(_esc(f"- **{alt_play.name}** - {alt_play.why}"))

    for c in advice.cautions:
        st.warning(_esc(c))

    theme.note(advice.dte_note)


def render_events(events, empty_note: str = "No major events in the next few weeks.") -> None:
    """Compact list of upcoming market events, soonest first."""
    if not events:
        theme.note(empty_note)
        return
    for e in events:
        when = e.date.strftime("%a %d %b")
        days = "today" if e.days_away == 0 else f"in {e.days_away} day{'s' if e.days_away != 1 else ''}"
        label = f"{e.icon} <b>{_htmllib.escape(e.label)}</b> - {when} ({days})"
        st.markdown(f"<div style='color:#213229;line-height:1.55;margin-top:4px;'>{label}</div>",
                    unsafe_allow_html=True)
        if getattr(e, "is_warning", e.in_window):
            # A real mover inside the trade window - this one earns the red flag.
            st.markdown(
                f"<div style='color:{_STATUS_TEXT['WARN']};font-weight:600;line-height:1.5;'>"
                f"⚠️ big mover inside your trade window - {_htmllib.escape(e.note)}</div>",
                unsafe_allow_html=True)
        elif e.note:
            # Everything else keeps its explanation but drops the alarm, so the
            # flags above stay meaningful instead of firing on every row.
            st.markdown(
                f"<div style='color:#213229;line-height:1.5;'>{_htmllib.escape(e.note)}</div>",
                unsafe_allow_html=True)


def render_checklist(report: ValidationReport) -> None:
    """Show every SOP rule as a colored line."""
    if report.passed:
        st.success(f"This trade PASSES your SOP for {report.strategy_name}. "
                   f"{report.n_warned} thing(s) to watch." if report.n_warned
                   else f"This trade PASSES your SOP for {report.strategy_name}. Clear to enter.")
    else:
        st.error(f"This trade BREAKS {report.n_failed} of your rules. Do not enter until fixed.")

    status_key = {CheckStatus.FAIL: "FAIL", CheckStatus.WARN: "WARN",
                  CheckStatus.PASS: "PASS"}
    for r in report.results:
        color = _STATUS_TEXT.get(status_key.get(r.status, "INFO"), "#0B5566")
        st.markdown(
            f"<div style='color:{color};line-height:1.55;margin:4px 0;font-size:1rem;'>"
            f"{r.icon}  <b>{_htmllib.escape(r.name)}</b> - {_htmllib.escape(r.message)}</div>",
            unsafe_allow_html=True)


# Why a shown setup does not fully fit. Both are context, never a green light.
_NEAR_MISS_LABEL = {
    "delta": "⚠️ delta a bit over",
    "credit": "⚠️ credit too thin",
}


def candidates_dataframe(candidates: list[Candidate]) -> pd.DataFrame:
    """Turn scanner candidates into a readable table."""
    rows = []
    for i, c in enumerate(candidates):
        strikes = " / ".join(f"{leg.strike:g}" for leg in c.trade.legs)
        rows.append({
            "#": i + 1,
            "Fits my rules": "✅ yes" if c.fits_sop else _NEAR_MISS_LABEL.get(
                c.near_miss, "⚠️ see the note"),
            "Underlying": c.trade.underlying,
            "Legs (strikes)": strikes,
            "Short delta": round(c.short_delta, 3),
            "Days left": c.dte,
            "Credit $": round(c.credit, 0),
            "Max loss $": round(c.max_loss, 0),
            "Buying power $": round(c.buying_power, 0),
            "Return/Risk": f"{c.return_on_risk * 100:.1f}%",
        })
    return pd.DataFrame(rows)


def candidates_column_config():
    """Hover help for the one table she actually picks a trade from. Every other
    table in the app explains its columns; this one used to show a bare Greek
    letter and expect her to know it."""
    return {
        "#": st.column_config.NumberColumn(
            format="%d",
            help="Type this number into 'Look at trade #' below to open the full "
                 "detail, checklist and payoff for that setup."),
        "Fits my rules": st.column_config.TextColumn(
            help="Whether the setup obeys every SOP rule the scanner can check. "
                 "'delta a bit over' means the closest available strike sits just "
                 "past your delta limit. 'credit too thin' means the premium is "
                 "below your 6%-of-spread-width floor - the trade is legal in every "
                 "other way, it just does not pay enough for the risk. Both are "
                 "shown for context, not as a green light."),
        "Legs (strikes)": st.column_config.TextColumn(
            help="The strike prices of the options in the trade, in the order the "
                 "strategy lists them (the one you SELL first)."),
        "Short delta": st.column_config.NumberColumn(
            format="%.3f",
            help="Delta on the option you SELL - roughly the chance it finishes in "
                 "the money. 0.25 means about a 1-in-4 chance the market reaches "
                 "your strike, so lower is safer and pays less."),
        "Days left": st.column_config.NumberColumn(
            format="%d",
            help="Days from today until this expiration. Your SOP wants about 45 at "
                 "entry, and closes at 21 no matter what - so a setup near 21 has "
                 "almost no time to work."),
        "Credit $": st.column_config.NumberColumn(
            format="$%d",
            help="Cash this setup pays you up front, for the number of contracts "
                 "you set above. This is what your 50% profit target is measured "
                 "against."),
        "Max loss $": st.column_config.NumberColumn(
            format="$%d",
            help="The worst case if it goes completely against you. On a credit "
                 "spread it is capped by the distance between your two strikes, "
                 "minus the credit."),
        "Buying power $": st.column_config.NumberColumn(
            format="$%d",
            help="Cash the broker sets aside while the trade is open. It counts "
                 "against your $50,000 monthly limit."),
        "Return/Risk": st.column_config.TextColumn(
            help="Credit divided by max loss - what you earn per dollar at risk. "
                 "Higher is richer premium, but it usually comes with a higher "
                 "delta, so read the two together."),
    }


def candidate_labels(candidates: list[Candidate]) -> list[str]:
    """One readable line per setup, for the picker under the table.

    This used to be a number box: read a row number off the table, then type it
    into a separate widget. Every other "show me one of these" control in the
    app is a dropdown you read and click, so this is too - which means the label
    has to carry enough to choose on without looking back up at the table.

    The leading #N still matches the table's own # column, so the two line up.
    """
    # The list arrives sorted by how well each expiration fits the SOP target,
    # grouped by underlying - so the first row of each underlying is its
    # best-timed setup. Say which, rather than making her infer it.
    #
    # The star only ever goes on a setup that PASSES the SOP. Marking the
    # best-timed one regardless produced "⭐ best timed · ⚠️ delta over" sitting
    # above a compliant setup with no star at all, and a star reads as a
    # recommendation. Timing is not worth breaking a rule for.
    star_rows: set[int] = set()
    seen: set[str] = set()
    for i, c in enumerate(candidates):
        u = c.trade.underlying
        if c.fits_sop and u not in seen:
            seen.add(u)
            star_rows.add(i)

    out = []
    for i, c in enumerate(candidates):
        strikes = "/".join(f"{leg.strike:g}" for leg in c.trade.legs)
        bits = [f"#{i + 1}", f"{c.trade.underlying} {strikes}"]
        if c.dte is not None:
            bits.append(f"{c.dte} days")
        bits.append(f"${c.credit:,.0f} credit")
        if i in star_rows:
            bits.append("⭐ best timed")
        if not c.fits_sop:
            bits.append("⚠️ delta over")
        out.append("  ·  ".join(bits))
    return out


def candidate_leg_detail(candidate: Candidate) -> pd.DataFrame:
    """Leg-by-leg breakdown, worded the thinkorswim way (+ buy / - sell)."""
    rows = []
    for leg in candidate.trade.legs:
        sign = "+" if leg.action.value == "buy" else "-"
        rows.append({
            "Leg": leg.role.replace("_", " ").title(),
            "In TOS": f"{sign}{leg.quantity}",
            "Type": leg.option_type.value,
            "Strike": leg.strike,
            "Delta": round(leg.delta, 3),
            "Mid price": leg.premium,
            "DTE": leg.dte,
        })
    return pd.DataFrame(rows)


# ================================================================== risk card
def _dollars(x: float) -> str:
    """Money for the screen. The minus goes BEFORE the dollar sign - "-&#36;97",
    not "&#36;-97", which is where the sign lands if you format the number
    straight and is how every other money helper here already writes it."""
    return f"{'-' if x < 0 else ''}&#36;{abs(x):,.0f}"


def render_risk_card(trade, strategy, size: dict, payoff_profile=None,
                     bp_limit: float = 50_000) -> None:
    """The stop-and-look card shown right before the Log button: the most you
    can lose in plain dollars, plus the three exit alerts to set in
    thinkorswim the moment the trade is filled."""
    credit = float(size.get("credit", 0.0))
    max_loss = float(size.get("max_loss", 0.0))
    bp = float(size.get("buying_power", 0.0))
    capital = float(size.get("capital", bp))
    # Cash out and buying power held are different things - but only on the
    # shapes where they differ. On a credit spread max loss, capital and buying
    # power are all the same number, and showing it a third time was noise.
    show_cash = capital > 0 and abs(capital - bp) > 0.5
    contracts = max(int(trade.contracts), 1)
    exit_cfg = strategy.get("exit", {})

    max_profit = credit
    breakevens: list[float] = []
    if payoff_profile is not None:
        max_profit = max(payoff_profile.max_profit, 0.0)
        breakevens = payoff_profile.breakevens

    be_txt = " / ".join(f"{b:,.2f}" for b in breakevens) if breakevens else "-"
    pct_of_limit = (bp / bp_limit * 100) if bp_limit else 0.0
    def _tile(label: str, value: str, color: str, extra: str = "") -> str:
        return (f'<div><div style="color:#213229;font-weight:600;font-size:.85rem;">'
                f'{label}</div><div style="font-size:1.5rem;font-weight:800;'
                f'color:{color};">{value}{extra}</div></div>')

    # Built as one joined string, never as a placeholder that can come out
    # empty: an empty line inside the HTML ends the block as far as markdown is
    # concerned, and everything after it rendered as visible raw tags.
    tiles = [
        _tile("MOST YOU CAN LOSE", _dollars(max_loss), theme.RED),
        _tile("MOST YOU CAN MAKE", _dollars(max_profit), theme.GREEN),
        _tile("BREAKEVEN PRICE", be_txt, theme.INK),
    ]
    if show_cash:
        tiles.append(_tile("CASH OUT TODAY", _dollars(capital), theme.INK))
    tiles.append(_tile(
        "BUYING POWER USED", _dollars(bp), theme.INK,
        f'<span style="font-size:.9rem;font-weight:600;">'
        f' ({pct_of_limit:.0f}% of your monthly limit)</span>'))

    st.markdown(
        f'<div style="border:2px solid {theme.RED};border-radius:14px;'
        f'padding:14px 18px;background:#FDF3F2;margin:8px 0 4px;">'
        f'<div style="font-weight:800;color:{theme.RED};font-size:1.05rem;">'
        f'⚠️ Know your risk before you log this</div>'
        f'<div style="display:flex;gap:28px;flex-wrap:wrap;margin-top:10px;">'
        f'{"".join(tiles)}</div></div>',
        unsafe_allow_html=True)
    if capital > 0 and bp == 0:
        # A PMCC's LEAPS is paid for in cash, so thinkorswim holds no buying
        # power against it and the tile above reads $0. That is right, and on
        # its own it would look like the trade costs nothing.
        theme.note(f"Your broker holds no buying power against this one - you pay for the "
                   f"long side in cash, so **\\${capital:,.0f} leaves your account** instead. "
                   f"It does not count against your \\${bp_limit:,.0f} monthly buying-power "
                   f"budget, but it is real money out.")

    # The three exits, translated into numbers she can type into TOS alerts.
    lines = []
    pt = exit_cfg.get("profit_target_pct")
    if pt and credit > 0:
        # Same number the checklist shows - see rules.profit_target_keep for why
        # this is computed in one place instead of two.
        from src.engine.rules import profit_target_keep
        keep = profit_target_keep(credit, pt)
        target_cost = round(credit, 2) - keep
        per_share = target_cost / (100 * contracts)
        lines.append(
            f"✅ <b>Profit target ({pt:g}%):</b> close when buying it back costs about "
            f"{_dollars(target_cost)} (&#36;{per_share:,.2f} per share) - you keep "
            f"{_dollars(keep)}.")
    sl = exit_cfg.get("stop_loss_multiple")
    if sl and credit > 0:
        stop_cost = credit * (1 + float(sl))
        per_share = stop_cost / (100 * contracts)
        lines.append(
            f"🛑 <b>Stop loss ({sl:g}x credit):</b> close if buying it back costs "
            f"{_dollars(stop_cost)} (&#36;{per_share:,.2f} per share) - a "
            f"{_dollars(float(sl) * credit)} loss. No rolling at that point.")
    te = exit_cfg.get("time_exit_dte")
    if te and trade.dte is not None:
        import datetime as _dt
        exit_day = _dt.date.today() + _dt.timedelta(days=int(trade.dte) - int(te))
        if exit_day <= _dt.date.today():
            lines.append(f"⏰ <b>Time exit:</b> this trade is already inside {int(te)} days "
                         "to expiration - it needs daily attention from day one.")
        else:
            lines.append(f"⏰ <b>Time exit:</b> close by <b>{exit_day:%A}, {exit_day.day} {exit_day:%B}</b> "
                         f"({int(te)} days before expiration) no matter what.")
    if lines:
        st.markdown(
            "<div style='border:1px solid " + theme.BORDER_STRONG + ";border-radius:12px;"
            "padding:12px 16px;background:#FFFFFF;'>"
            "<div style='font-weight:700;color:" + theme.INK + ";margin-bottom:6px;'>"
            "Set these alerts in thinkorswim right after you enter:</div>"
            + "".join(f"<div style='color:{theme.CAPTION};line-height:1.7;'>{l}</div>"
                      for l in lines)
            + "</div>",
            unsafe_allow_html=True)


def render_payoff_chart(payoff_profile, current_price=None) -> None:
    """The profit-zone picture: where you win (green), where you lose (red),
    with your breakeven and today's price marked."""
    p = payoff_profile
    df = pd.DataFrame({"price": p.prices, "pl": p.values})
    df["profit"] = df["pl"].clip(lower=0)
    df["loss"] = df["pl"].clip(upper=0)

    base = alt.Chart(df).encode(
        x=alt.X("price:Q", title="Underlying price at expiration",
                scale=alt.Scale(domain=[p.prices[0], p.prices[-1]], nice=False)))
    win = base.mark_area(color="#10B981", opacity=0.25).encode(
        y=alt.Y("profit:Q", title="Profit / loss ($)"))
    lose = base.mark_area(color="#DC2626", opacity=0.22).encode(y="loss:Q")
    line = base.mark_line(color=theme.INK, strokeWidth=2.5).encode(
        y="pl:Q",
        tooltip=[alt.Tooltip("price:Q", title="Price", format=",.2f"),
                 alt.Tooltip("pl:Q", title="P&L $", format=",.0f")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color=theme.BORDER_STRONG).encode(y="y:Q")

    layers = [win, lose, line, zero]
    marks = [{"price": b, "label": f"breakeven {b:,.0f}"} for b in p.breakevens]
    if current_price:
        marks.append({"price": float(current_price), "label": f"today {current_price:,.0f}"})
    if marks:
        mdf = pd.DataFrame(marks)
        layers.append(alt.Chart(mdf).mark_rule(color=theme.ACCENT, strokeDash=[5, 4],
                                               strokeWidth=1.5).encode(x="price:Q"))
        layers.append(alt.Chart(mdf).mark_text(align="left", dx=4, dy=-6, angle=270,
                                               color=theme.ACCENT, fontWeight=600,
                                               fontSize=12).encode(x="price:Q", text="label:N"))

    st.altair_chart(alt.layer(*layers).properties(height=260), width="stretch")

    caveats = []
    if p.loss_grows_below:
        caveats.append("losses keep growing if price falls below the left edge of the chart")
    if p.loss_grows_above:
        caveats.append("losses keep growing if price rises past the right edge")
    if p.includes_shares:
        caveats.append("the math includes your 100 shares per contract")
    if p.approximate:
        caveats.append("the long-dated LEAPS is estimated at its floor value, so the real "
                       "picture is usually a bit better than shown")
    note = ("This is the picture **at expiration** - your SOP normally exits earlier "
            "(50% profit or 21 days left).")
    if caveats:
        note += " Note: " + "; ".join(caveats) + "."
    theme.note(note)


# ================================================================== My trades
_SIGNAL_WORD = {
    "stop": "🛑 Close - stop loss",
    "time": "⏰ Decide today",
    "profit": "✅ Take the win",
    "watch": "⚠️ Watch closely",
    "uncovered": "➕ No call sold",
    # She sold the long put off a spread and is letting the short one assign
    # her. Not "hold" - there is something to do, and it is have the cash.
    "awaiting": "🎯 Let it assign",
    "hold": "✋ Hold",
    "unpriced": "❓ Could not price",
}


def short_strategy(name: str) -> str:
    """A table-width version of a strategy name. The full name still shows in the
    trade's detail card below the table, so nothing is lost.

    "Poor Man's Covered Call (PMCC)"              -> "PMCC"
    "Covered Call - Model 3: Zero Cost Ratio"     -> "Covered Call M3"
    "Call Credit Spread (Bear Call Spread)"       -> "Call Credit Spread"
    """
    if not name:
        return ""
    head, _, rest = name.partition(" (")
    acronym = rest.rstrip(")").strip()
    # A short all-caps parenthetical IS the common name (PMCC, CSP) - prefer it.
    if acronym and len(acronym) <= 6 and acronym.isupper():
        return acronym
    return head.split(":")[0].replace(" - Model ", " M").strip()


def _decide_by(pos, time_exit_dte: int = 21) -> str:
    """The DATE her 21-day time exit falls on, not a countdown.

    The app dates this precisely before she enters ("close by Monday, July 27")
    and then, once the trade is open, only ever says "34 days left" - leaving
    her to subtract 21 herself for every position, every time she plans a week.
    """
    import datetime as _dt

    dte = pos.dte_left()
    if dte is None:
        return "-"
    days_to_go = int(dte) - int(time_exit_dte or 21)
    if days_to_go < 0:
        return "overdue"
    if days_to_go == 0:
        return "today"
    return f"{_dt.date.today() + _dt.timedelta(days=days_to_go):%a %d %b}"


def positions_dataframe(items: list[dict]) -> pd.DataFrame:
    """items: [{"position": Position, "live": dict, "signal": ExitSignal}]"""
    from src.engine.positions import strike_cushion

    rows = []
    for it in items:
        pos, live, sig = it["position"], it["live"], it["signal"]
        px = live.get("underlying_price")
        cushion = strike_cushion(pos, px)
        strike_label = None
        if cushion:
            side = "C" if cushion["option_type"] == "call" else "P"
            strike_label = f"{cushion['strike']:g} {side}"
        # P&L means the same thing in every row: what the whole trade is worth
        # if closed right now. On a credit spread that is credit minus cost to
        # close, which is what the exit signal already computed. On a PMCC it
        # has to include the LEAPS, or the column reports the small short-call
        # number on a trade whose real result is ten times that.
        pl = sig.pl_dollars
        if pos.is_debit and live.get("open_pl") is not None:
            pl = live["open_pl"]
        rows.append({
            "What to do": _SIGNAL_WORD.get(sig.action, sig.action),
            "Symbol": pos.underlying,
            "Price now": px,
            "You sold": strike_label,
            "Room to it": (cushion["room_pct"] * 100) if cushion else None,
            "Strategy": short_strategy(pos.strategy_name),
            "Days left": pos.dte_left(),
            # A trade she is holding to expiration on purpose has no 21-day
            # decision to date - saying "overdue" there would nag her about a
            # rule she has already, deliberately, stepped outside.
            "Decide by": ("at expiry"
                          if getattr(pos, "awaiting_assignment", False)
                          else _decide_by(pos, it.get("time_exit_dte", 21))),
            "Credit $": pos.credit,
            "Close now $": live.get("cost_to_close"),
            "P&L $": pl,
            "% kept": sig.profit_pct,
        })
    return pd.DataFrame(rows)


def positions_column_config():
    # Widths are pixels on purpose: 11 columns of auto-sized text overflowed the
    # page and forced a horizontal scrollbar. This budget totals ~1010px, which
    # fits the content area on a laptop screen with the sidebar collapsed.
    return {
        "What to do": st.column_config.TextColumn(width=125,
            help="Your own exit rules applied to live prices. Red = close, green = take "
                 "the win, amber = needs eyes on it."),
        "Symbol": st.column_config.TextColumn(width=70,
            help="The underlying you traded."),
        "Price now": st.column_config.NumberColumn(format="%.2f", width=88,
            help="What the underlying is trading at right now (about 15 minutes delayed)."),
        "You sold": st.column_config.TextColumn(width=78,
            help="The strike you SOLD that price is closest to - the one that matters. "
                 "C = a call you sold (trouble if price rises to it), P = a put you sold "
                 "(trouble if price falls to it)."),
        "Room to it": st.column_config.NumberColumn(format="%.1f%%", width=85,
            help="How far price still has to move to reach that strike. Bigger is safer. "
                 "Under 1.5% your SOP says consider rolling; below zero the strike is "
                 "already breached."),
        "Strategy": st.column_config.TextColumn(width=160,
            help="The strategy you opened, shortened to fit. The full name is in the "
                 "trade's detail card below the table."),
        "Days left": st.column_config.NumberColumn(format="%d", width=75,
            help="Days to expiration. At 21 your SOP makes you decide: close, or roll "
                 "for a net credit. Never hold past it without deciding."),
        "Decide by": st.column_config.TextColumn(width=95,
            help="The date your 21-day time exit lands on. Before this day you are "
                 "just holding; on it you decide - close, or roll for a credit. "
                 "\"Today\" or \"overdue\" means that day has arrived."),
        "Credit $": st.column_config.NumberColumn(format="$%.0f", width=80,
            help="Cash you collected for the short leg when you opened it. On a "
                 "PMCC or covered call that is the short call only - the LEAPS "
                 "or the shares are not in this number."),
        "Close now $": st.column_config.NumberColumn(format="$%.0f", width=95,
            help="What it costs to buy the short side back right now (mid prices)."),
        "P&L $": st.column_config.NumberColumn(format="$%.0f", width=78,
            help="What the whole trade is worth if you closed it today - on a "
                 "PMCC or covered call the long leg and your banked roll credits "
                 "are counted in."),
        "% kept": st.column_config.NumberColumn(format="%.0f%%", width=76,
            help="How much of the SHORT CALL's credit is yours so far. Your SOP "
                 "takes the win at 50%. On a PMCC this is about the call only - "
                 "the P&L column is the whole trade."),
    }


def render_exit_signal(sig) -> None:
    """One position's instruction, big and clear."""
    tone_color = {"red": _STATUS_TEXT["FAIL"], "amber": _STATUS_TEXT["WARN"],
                  "green": _STATUS_TEXT["PASS"], "neutral": theme.INK}[sig.tone]
    st.markdown(
        f"<div style='font-size:1.3rem;font-weight:800;color:{tone_color};margin:2px 0;'>"
        f"{_SIGNAL_WORD.get(sig.action, sig.action)}</div>"
        f"<div style='color:{theme.CAPTION};line-height:1.6;'>{_htmllib.escape(sig.reason)}</div>",
        unsafe_allow_html=True)
    for n in sig.notes:
        st.warning(_esc(n))


def render_debit_position_card(position, live: dict) -> None:
    """The whole-position picture for a PMCC or covered call.

    The metrics above this answer "how is the short call doing", which is what
    the 50% rule needs but is a small slice of the money. This answers "how is
    the TRADE doing" - the long-dated leg included, where most of the profit or
    loss actually lives.
    """
    st.markdown("**The whole position**")
    out = abs(position.open_cash)
    value = live.get("position_value")
    open_pl = live.get("open_pl")
    owns_shares = position.shares_cost > 0
    # A bought call financed by sold puts is the one shape where the cash that
    # left the account is not the size of the trade: $240 out, $22,500 frozen
    # behind the puts. A return measured on the $240 is a real number about a
    # misleading denominator, and it used to sit four lines under the exit
    # reason quoting the OTHER denominator - two percentages for one $3.
    # is_leaps_call_trade, not is_long_premium: the puts stay financed - and
    # their collateral stays frozen - after she writes a call against the LEAPS.
    financed = position.is_leaps_call_trade and position.short_put_collateral > 0
    basis = position.capital_at_risk if financed else out

    cols = st.columns(4)
    cols[0].metric("Cash you put in", _dollars(out),
                   help=("What left your account to open this: the shares and "
                         "the put side you bought, minus the call credit."
                         if owns_shares else
                         "What the call cost you, less what the put(s) you sold "
                         "paid you. The collateral behind those puts is on top "
                         "of this - see the max loss above."
                         if financed else
                         "What left your account to open this: the long side "
                         "you bought, minus the call credit you collected."))
    banked = position.roll_income
    # SINCE opening, deliberately: the call sold ON the opening day is already
    # inside "Cash you put in" to its left, because that is the one number the
    # log stores for day one. Counting it here as well would show the same
    # premium twice on one row of four figures. The whole tally, with the two
    # halves pulled apart, is in "See one trade from start to finish".
    cols[1].metric("Premium banked since", _dollars(banked),
                   help="Every leg you have sold or bought back since opening,"
                        " netted. Counted in the month each one happened. It "
                        "goes negative when you have just paid to close one "
                        "and not yet sold the next. The call you sold on the "
                        "opening day is not in here - it is already netted into "
                        "Cash you put in."
                        if banked < 0 else
                        f"Net credit from every roll of the short leg since you "
                        f"opened it - {_dollars(banked)} so far, already yours "
                        f"and counted in the month each roll happened. The call "
                        f"you sold on the OPENING day is not in here: it is "
                        f"netted into Cash you put in. For the whole premium "
                        f"tally with the long side held apart "
                        f"({_dollars(position.premium_collected)} on this "
                        f"trade), open Correct and look back → See one trade "
                        f"from start to finish.")
    cols[2].metric("Worth now",
                   _dollars(value) if value is not None else "n/a",
                   help="What unwinding every leg would pay you at today's "
                        "prices. 'n/a' means the chain is missing a contract - "
                        "usually the long-dated one.")
    if open_pl is None:
        cols[3].metric("Profit if closed now", "n/a")
        theme.note("The long-dated leg could not be priced just now, so the "
                   "whole-trade number is unavailable. The short call's numbers "
                   "above, and every day-count and strike check, still work.")
        return

    pct = (open_pl / basis * 100) if basis > 0 else 0.0
    cols[3].metric("Profit if closed now", _dollars(open_pl),
                   delta=f"{pct:+.1f}%",
                   help="Everything unwound at today's prices, plus the premium "
                        "you already banked, minus what you put in, measured "
                        f"against the ${basis:,.0f} this trade ties up. This is "
                        "the number the trade is actually worth to you."
                        if financed else
                        "Everything unwound at today's prices, plus the premium "
                        "you already banked, minus what you put in. This is the "
                        "number the trade is actually worth to you.")

    options_pl, shares_pl = live.get("options_pl"), live.get("shares_pl")
    if owns_shares and options_pl is not None and shares_pl is not None:
        # You own the shares; the options are the trade you run against them.
        # One blended number hides the case that matters - calls earning well
        # while the stock drifts down, or the reverse.
        split = st.columns(2)
        split[0].metric("Your options", _dollars(options_pl),
                        help="The put side plus every call you have sold, "
                             "including the premium you have banked. This is "
                             "the part your covered call strategy controls.")
        split[1].metric("Your shares", _dollars(shares_pl),
                        help="What the 100 shares per contract have done since "
                             "you bought them. You own these - the options are "
                             "the trade running against them.")
        o_word = "made" if options_pl >= 0 else "lost"
        s_word = "up" if shares_pl >= 0 else "down"
        theme.note(
            f"Your **options have {o_word} \\${abs(options_pl):,.0f}** and your "
            f"**shares are {s_word} \\${abs(shares_pl):,.0f}**, so the two "
            f"together are **{'up' if open_pl >= 0 else 'down'} "
            f"\\${abs(open_pl):,.0f}** right now. Your 50% profit target "
            "applies to the short call on its own, not to any of these.")
        return

    word = "up" if open_pl >= 0 else "down"
    tail = ("Your 50% profit target applies to the short call on its own, not "
            "to this number - the long leg is a stock substitute you hold on to "
            "while the short calls earn.")
    if position.is_long_premium:
        # There is no short call in this strategy and no 50% target in its SOP,
        # so the PMCC line above was describing a trade she is not in. What
        # this one measures against is the two take-it windows, and the reason
        # it has no stop is worth repeating on the card that shows a loss.
        tail = ("This strategy has no 50% target and no stop: you take 10-20% "
                "if it comes inside a week, or 20-40% inside four weeks, and "
                "otherwise you sit on the time you paid for.")
    elif position.is_uncovered:
        # There is no short call to talk about, so the 50% line would be noise.
        tail = ("Nothing is sold against it at the moment, so this is just the "
                "long leg riding the stock. Selling the next call starts the "
                "income again and gives your 50% target something to measure.")
    from src.engine.exit_rules import pct_text

    # Names the denominator the percentage actually used, which on a financed
    # LEAPS is the capital tied up rather than the cash that left the account.
    against = (f"the \\${basis:,.0f} this trade ties up" if financed
               else f"the \\${out:,.0f} you put in")
    theme.note(
        f"Closing everything today would leave you **{word} "
        f"\\${abs(open_pl):,.0f}** on {against} "
        f"(**{pct_text(pct)}**). " + tail)


def render_protection_read(position, read: dict) -> None:
    """Where a covered call's downside protection holds, and what is below it.

    Replaces "Max loss = what you paid", which was never the max loss: it
    ignored the protective put on a collar and understated the ratio's tail,
    where the two short puts make losses accelerate past the cash outlay.
    """
    zones = read["zones"]
    flat_to, worst = read["flat_to"], read["worst_case"]
    below = read["slope_below"]
    sym = position.underlying

    if flat_to is not None:
        drop = ((flat_to - zones[0]["from"]) / zones[0]["from"] * 100)
        line = (f"**You are flat all the way down to \\${flat_to:,.0f}** "
                f"({drop:.0f}% from here) - the put side is carrying the "
                f"shares that far. ")
        if read["capped"]:
            line += (f"Below that your loss stops at "
                     f"**\\${abs(worst):,.0f}**, however far {sym} falls.")
        else:
            line += (f"Below \\${flat_to:,.0f} you lose about "
                     f"**\\${abs(below):,.0f} for every \\$1** {sym} falls, "
                     f"and at zero you would be down "
                     f"**\\${abs(worst):,.0f}**.")
    else:
        first = zones[0]
        line = (f"You lose about **\\${abs(first['slope']):,.0f} for every "
                f"\\$1** {sym} falls, down to \\${first['to']:,.0f}. ")
        if read["capped"]:
            line += (f"Below \\${first['to']:,.0f} your protective put caps "
                     f"the loss at **\\${abs(worst):,.0f}**, however far it "
                     "falls.")
        else:
            line += (f"Below \\${first['to']:,.0f} it gets worse - at zero you "
                     f"would be down **\\${abs(worst):,.0f}**.")
    theme.note(line)


_EARLIEST = date(1900, 1, 1)


def by_closed_date(closed: list) -> list:
    """Closed trades, most recently closed first."""
    return sorted(closed, key=lambda p: (p.closed_on or p.opened or _EARLIEST),
                  reverse=True)


def by_opened_date(open_pos: list) -> list:
    """Open trades, most recently opened first."""
    return sorted(open_pos, key=lambda p: (p.opened or _EARLIEST), reverse=True)


def closed_dataframe(closed: list) -> pd.DataFrame:
    rows = []
    for p in by_closed_date(closed):
        rows.append({
            "Closed": p.closed_on,
            "Opened": p.opened,
            "Symbol": p.underlying,
            "Strategy": p.strategy_name,
            "Credit $": p.credit,
            "Rolls $": p.roll_income or None,
            "Cash back $": p.close_cash if p.is_debit else None,
            "Exit cost $": None if p.is_debit else p.exit_cost,
            "Result $": p.realized_total,
            "Why closed": p.exit_reason,
        })
    return pd.DataFrame(rows)


def closed_column_config():
    """The dates as she writes them, and the money without stray decimals.

    This table went out with no column_config at all, so the two date columns
    rendered as raw timestamps - the one thing on the page she reads by shape
    rather than by parsing.
    """
    return {
        "Closed": st.column_config.DateColumn(format=DATE_FMT,
            help="The day you closed it."),
        "Opened": st.column_config.DateColumn(format=DATE_FMT),
        "Credit $": st.column_config.NumberColumn(format="$%.0f",
            help="Premium collected for the short leg at the open. On a PMCC "
                 "or covered call that is the call only."),
        "Rolls $": st.column_config.NumberColumn(format="$%.0f",
            help="Everything the rolls banked over the life of the trade, "
                 "netted. Blank if you never rolled it."),
        "Cash back $": st.column_config.NumberColumn(format="$%.0f",
            help="What closing PAID you - selling the long leg back on a PMCC "
                 "or covered call."),
        "Exit cost $": st.column_config.NumberColumn(format="$%.0f",
            help="What closing COST you - buying a spread or condor back."),
        "Result $": st.column_config.NumberColumn(format="$%.0f",
            help="The whole trade, start to finish, roll credits included."),
        "Why closed": st.column_config.TextColumn(
            help="The exit rule (or reason) recorded at close."),
    }


def open_dataframe(open_pos: list, today: date | None = None) -> pd.DataFrame:
    """The open book as a record, newest first.

    Deliberately not a second copy of the open-trades cards above: those answer
    "what needs doing today" and are priced live. This answers "what is on the
    books and since when", which is the question the rest of Records is about.
    """
    today = today or date.today()
    rows = []
    for p in by_opened_date(open_pos):
        left = p.dte_left(today)
        rows.append({
            "Opened": p.opened,
            "Expires": p.expiration,
            "Days left": left,
            "Symbol": p.underlying,
            "Strategy": p.strategy_name,
            "Contracts": p.contracts,
            "Credit $": p.credit,
            "Banked so far $": p.realized_total,
        })
    return pd.DataFrame(rows)


def open_column_config():
    return {
        "Opened": st.column_config.DateColumn(format=DATE_FMT,
            help="The day you placed it."),
        "Expires": st.column_config.DateColumn(format=DATE_FMT,
            help="When the nearest leg expires. A roll moves this out."),
        "Days left": st.column_config.NumberColumn(format="%d",
            help="Calendar days to that expiration. Your SOP closes or rolls "
                 "at 21."),
        "Credit $": st.column_config.NumberColumn(format="$%.0f",
            help="Premium collected for the short leg you hold right now."),
        "Banked so far $": st.column_config.NumberColumn(format="$%.0f",
            help="Roll credits already banked on this trade. That money is "
                 "yours whatever the trade does from here. Blank until you "
                 "roll it the first time."),
    }


# ============================================================ the whole story
def _story_esc(text) -> str:
    """Escape for HTML, then neutralise any dollar sign.

    Every string here came out of her own log - strategy names, roll notes she
    typed herself - and a raw pair of dollar signs turns Streamlit's markdown
    into LaTeX and garbles the line.
    """
    return _htmllib.escape(str(text), quote=True).replace("$", "&#36;")


def _story_amount(cash: float) -> str:
    """One signed amount, coloured and with its sign spelled out.

    The sign is on the number rather than only in the colour: green and red
    alone would leave the whole column meaningless to anyone who cannot
    separate them, and this column is the entire point of the panel.
    """
    if round(cash) == 0:              # "+$0" reads as a thing that happened
        return '<span class="ota-story-amt">&#36;0</span>'
    cls = "ota-story-in" if cash > 0 else "ota-story-out"
    sign = "+" if cash > 0 else "-"
    return (f'<span class="ota-story-amt {cls}">{sign}&#36;'
            f'{abs(cash):,.0f}</span>')


def _story_row(n: str, when: str, what: str, detail: str, cash,
               extra: str = "") -> str:
    amount = _story_amount(cash) if cash is not None else ""
    body = f'<div>{_story_esc(what)}</div>'
    if detail:
        body += f'<div class="ota-story-detail">{_story_esc(detail)}</div>'
    return (f'<div class="ota-story-row {extra}">'
            f'<div class="ota-story-n">{_story_esc(n)}</div>'
            f'<div class="ota-story-date">{_story_esc(when)}</div>'
            f'<div class="ota-story-what">{body}</div>'
            f'{amount}</div>')


def _premium_panel(position, closed: bool) -> None:
    """How much premium this trade has collected, apart from the long side.

    Rita, on her SMH PMCC: the story showed the long call as a minus lumped in
    with the first short call, and she wanted the premium on its own. It was
    not a display quirk - the opening fill really is one netted number in the
    log, so the first call she sold on a PMCC was invisible everywhere. The
    story now tells day one as two lines, and this adds up what the writing
    side has actually paid her over the life of the trade.

    Only on the shapes that HAVE a long side to keep separate. On a credit
    spread the credit is the whole trade and this would just repeat the
    headline.
    """
    if position.open_bought_cost <= 0:
        return
    sold = position.premium_sold
    kept = position.premium_collected
    if sold <= 0:
        return

    rows = [_story_row("", "", "The first one you sold", "",
                       position.open_premium, "ota-story-sum")]
    rolled = round(sold - position.open_premium, 2)
    if rolled:
        rows.append(_story_row("", "", "Sold on rolls since",
                               "at the price each one sold for", rolled))
    if abs(sold - kept) > 0.005:
        rows.append(_story_row("", "", "Paid to buy them back", "",
                               round(kept - sold, 2)))
    rows.append(_story_row("", "", "Premium collected so far", "", kept,
                           "ota-story-final"))

    st.markdown(
        f'<div class="ota-story">'
        f'<div class="ota-story-head"><div class="ota-story-title">'
        f'&#128176; Premium collected, on its own</div>'
        f'<div class="ota-story-when">What the options you SOLD have paid you '
        f'- the long side you bought is not in this</div></div>'
        + "".join(rows) + '</div>', unsafe_allow_html=True)

    what = "shares" if position.shares_cost > 0 else "long call"
    owned = ("they are still yours and the money comes back when you sell them"
             if position.shares_cost > 0 else
             "it is still yours and the money comes back when you sell it")
    tail = (" The last one was bought back inside your closing fill, so it "
            "shows in the result above rather than here." if closed else "")
    # \\$ rather than the &#36; the HTML above uses: a raw pair of dollar signs
    # turns Streamlit's markdown into LaTeX and garbles the line.
    theme.note(
        f"Your {what} cost **\\${position.open_bought_cost:,.0f}**. That is "
        f"capital, not premium - {owned}, which is why it is kept out of this "
        f"tally.{tail}")


def render_story(position, steps: list[dict]) -> None:
    """One trade from the first fill to the last, as a list she can read.

    This was a dataframe with a "Running total" column, and Rita's verdict was
    "not friendly and not clear" - fairly. On a PMCC the running total sits
    around -14,000 for a dozen rows before the close flips it positive, so a
    trade that made $1,515 read like a disaster until the very last line.

    What she actually needs answering is two questions, and they are now two
    separate things on the screen: "did this work" is the result at the top,
    and "does this match thinkorswim" is one line per fill with the paid /
    collected / result block underneath that visibly adds up.
    """
    if not steps:
        return

    paid = sum(s["cash"] for s in steps if s["cash"] < 0)
    took = sum(s["cash"] for s in steps if s["cash"] > 0)
    final = steps[-1]["running"]
    closed = steps[-1]["kind"] == "close"

    # ---- the header: what this trade was, and how it ended
    when = fmt_date(position.opened)
    if closed:
        when += f" &rarr; {fmt_date(position.closed_on)}"
        days = ((position.closed_on - position.opened).days
                if position.closed_on and position.opened else None)
        if days is not None:
            when += f" &middot; {days} day{'' if days == 1 else 's'}"
    else:
        when += " &middot; still open"
    when += (f" &middot; {len(steps)} move{'' if len(steps) == 1 else 's'}")

    if closed:
        tone = "ota-story-in" if final >= 0 else "ota-story-out"
        word = "You made" if final >= 0 else "You lost"
        headline = (f'<div class="ota-story-result {tone}">{word} &#36;'
                    f'{abs(final):,.0f}</div>')
        risked = position.capital_at_risk
        if risked > 0:
            pct = final / risked * 100
            sub = (f"{pct:+.1f}% on the &#36;{risked:,.0f} this trade "
                   "tied up")
        else:
            sub = "Every fill from the day you opened it to the day you closed it"
    else:
        headline = (f'<div class="ota-story-result" style="color:{theme.INK};">'
                    f'&#36;{took + paid:,.0f} so far</div>')
        sub = "Not a profit yet - it becomes one on the day you close"

    head = (f'<div class="ota-story-head">'
            f'<div class="ota-story-title">{_story_esc(position.underlying)} '
            f'&middot; {_story_esc(short_strategy(position.strategy_name))}</div>'
            f'<div class="ota-story-when">{when}</div>'
            f'{headline}'
            f'<div class="ota-story-resultsub">{sub}</div></div>')

    # ---- one line per fill, numbered so she can count them against TOS
    body = "".join(
        _story_row(str(i), fmt_date(s["on"]), s["what"], s["detail"], s["cash"])
        for i, s in enumerate(steps, 1))

    # ---- the arithmetic, spelled out
    summary = (
        _story_row("", "", "Money you paid out", "", paid, "ota-story-sum")
        + _story_row("", "", "Money you collected", "", took)
        + _story_row("", "", "Result" if closed else "Where that leaves you",
                     "", final, "ota-story-final"))

    st.markdown(f'<div class="ota-story">{head}{body}{summary}</div>',
                unsafe_allow_html=True)

    _premium_panel(position, closed)

    if closed:
        # The opening line covers every leg she opened with, which thinkorswim
        # may well show as two or three separate fills seconds apart. Without
        # this she counts 7 rows there against 6 here and goes looking for a
        # missing fill that was never missing - the exact worry this panel is
        # meant to settle.
        multi = len(position.open_legs or position.legs) > 1
        extra = (" Your opening line here covers every leg you opened with, so "
                 "thinkorswim may list it as two or three fills seconds apart."
                 if multi else "")
        theme.note("Those lines are every fill on this trade. Put them next to "
                   "the same trade in thinkorswim - if a line is missing or an "
                   "amount is different, that is why your result does not "
                   "match." + extra)
    else:
        theme.note("Every fill so far. The last line is money in minus money "
                   "out - on a trade where you bought a long leg it stays "
                   "negative until you sell that leg back at the close.")


# ================================================================== month view
def _roll_reason(roll) -> str:
    """The one line the month's table shows for a roll.

    It names the side, because a put rolled down and out and a call rolled up
    and out are different decisions and the log used to call both of them
    "the short call" - which read as a mistake on every put she rolled.
    """
    side = "put" if str(roll.option_type).lower() == "put" else "call"
    if roll.new_strike is None:
        return f"Bought the short {side} back"
    if roll.new_long_strike is not None:
        return (f"Rolled the {side} spread to "
                f"{roll.new_strike:g}/{roll.new_long_strike:g}")
    return f"Rolled the short {side} to {roll.new_strike:g}"


def _month_result_word(position, tag: str) -> str:
    """One plain-English word for how this trade sits in THIS month's list."""
    if tag == "rolled":
        return "🔄 Rolled"
    if tag == "legclose":
        return "✂️ Leg sold"
    if tag in ("closed", "both") and position.status == "closed":
        pl = position.realized_total
        if pl is None:
            return "✔️ Closed"
        if pl > 0:
            return "✅ Won"
        if pl < 0:
            return "❌ Lost"
        return "➖ Broke even"
    if position.status == "open":
        return "⏳ Still open"
    if position.status == "closed" and position.closed_on is not None:
        return f"→ Closed in {position.closed_on.strftime('%B')}"
    return "📜 History"


def month_trades_dataframe(rows: list[dict]) -> pd.DataFrame:
    """One month's trades, friendliest facts first.

    rows: [{"position": Position, "tag": "closed"|"opened"|"both"|"rolled"
            |"legclose"}] from positions.monthly_summary. Money banked this
    month sorts to the top. A "rolled" row is its own line: the credit landed
    in THIS month even when the position was opened earlier and is still open,
    and a "legclose" row - a long put sold off a spread - is the same idea.
    """
    def sort_key(r):
        banked_here = (r["tag"] in ("rolled", "legclose")
                       or (r["tag"] in ("closed", "both")
                           and r["position"].status == "closed"))
        return 0 if banked_here else 1

    out = []
    for r in sorted(rows, key=sort_key):
        p, tag = r["position"], r["tag"]
        roll = r.get("roll")
        if tag == "rolled" and roll is not None:
            out.append({
                "Result": _month_result_word(p, tag),
                "Symbol": p.underlying,
                "Strategy": p.strategy_name,
                "Opened": p.opened,
                "Closed": roll.rolled_on,
                "Credit $": roll.new_credit or None,
                "Result $": roll.cash,
                "Why closed": _roll_reason(roll),
            })
            continue
        leg = r.get("leg_close")
        if tag == "legclose" and leg is not None:
            out.append({
                "Result": _month_result_word(p, tag),
                "Symbol": p.underlying,
                "Strategy": p.strategy_name,
                "Opened": p.opened,
                "Closed": leg.closed_on,
                "Credit $": None,
                "Result $": leg.cash,
                "Why closed": leg.note or "Sold one leg, kept the rest open",
            })
            continue
        reason = (p.exit_reason or "").split(" - ", 1)[0]
        # Every row in a MONTH view reports what was banked in THAT month, so
        # the column adds up to the month's headline total. For a rolled trade
        # that splits across months: the roll lines carry their credits, and
        # this line carries the closing result only. The trade's whole-life
        # number lives in the Closed trades table, where nothing is summed.
        # Without rolls (almost every trade) the two are identical anyway.
        result = p.realized_pl if tag in ("closed", "both") else None
        out.append({
            "Result": _month_result_word(p, tag),
            "Symbol": p.underlying,
            "Strategy": p.strategy_name,
            "Opened": p.opened,
            "Closed": p.closed_on,
            "Credit $": p.credit,
            "Result $": result,
            "Why closed": reason or "-",
        })
    return pd.DataFrame(out)


def month_trades_column_config():
    return {
        "Result": st.column_config.TextColumn(
            help="How this trade ended up. 'Still open' trades are being "
                 "watched in the open-trades list above. 'Rolled' is a short "
                 "leg rolled out - a call, or a put rolled down and out. The "
                 "credit was banked that day. 'Leg sold' "
                 "is one leg taken off while the trade carried on, usually the "
                 "long put of a spread left to be assigned."),
        "Opened": st.column_config.DateColumn(format=DATE_FMT),
        "Closed": st.column_config.DateColumn(
            format=DATE_FMT,
            help="When the trade closed, or when the roll happened."),
        "Credit $": st.column_config.NumberColumn(format="$%.0f",
            help="Premium collected for the short leg. On a PMCC or covered "
                 "call that is the call only, not the size of the position."),
        "Result $": st.column_config.NumberColumn(format="$%.0f",
            help="Money banked THIS month, so the column adds up to the month's "
                 "total above. A rolled trade banks each roll's credit on the "
                 "day it rolled and the rest when it closes - its whole-life "
                 "result is in 'All closed trades' at the bottom of the page."),
        "Why closed": st.column_config.TextColumn(
            help="The exit rule (or reason) recorded at close."),
    }


# ================================================================== Today's picks
def picks_index_dataframe(picks: list) -> pd.DataFrame:
    """The index-plays table: one row per cash-settled index with its
    trend-fitting strategy and the real scanned monthly setup's numbers."""
    rows = []
    for p in picks:
        c = p.candidate
        note = p.error or ("" if c is None or c.fits_sop else "delta a touch over")
        rows.append({
            "Symbol": p.symbol,
            "Price": round(p.price, 2) if p.price else None,
            "Today's fit": p.strategy_name,
            "Trend": p.trend,
            "Premium deal": p.richness,
            "Credit $": round(c.credit, 0) if c else None,
            "Max loss $": round(c.max_loss, 0) if c else None,
            "Return/Risk": f"{c.return_on_risk * 100:.1f}%" if c else "-",
            "Days": c.dte if c else None,
            "Note": note or "—",
        })
    return pd.DataFrame(rows)


def picks_index_column_config():
    return {
        "Price": st.column_config.NumberColumn(format="$%.2f",
            help="The underlying's current level/price (about 15 minutes delayed)."),
        "Today's fit": st.column_config.TextColumn(
            help="The strategy from YOUR playbook that fits this index's trend today."),
        "Premium deal": st.column_config.TextColumn(
            help="Rich = options pay more than this index's usual moves justify (good for the "
                 "seller). Thin = pays little. Fair = normal."),
        "Credit $": st.column_config.NumberColumn(format="$%d",
            help="Cash collected for one contract of the scanned setup."),
        "Max loss $": st.column_config.NumberColumn(format="$%d",
            help="The worst case for one contract - defined up front on a credit spread."),
        "Return/Risk": st.column_config.TextColumn(
            help="Credit divided by max loss - the premium you earn per dollar at risk."),
        "Days": st.column_config.NumberColumn(format="%d",
            help="Days to the expiration used (the monthly when it fits your SOP window)."),
    }


# Short labels for the income table's "Fits your SOP" column.
_STRATEGY_SHORT = {
    "cash_secured_put": "Cash Secured Put",
    "poor_mans_covered_call": "PMCC",
    "covered_call_model_1": "Covered Call M1 (collar)",
    "covered_call_model_2": "Covered Call M2",
    "covered_call_model_3": "Covered Call M3",
}


def picks_income_dataframe(picks: list) -> pd.DataFrame:
    """The stock/ETF income table: verdict-first, with the dividend alongside."""
    rows = []
    for p in picks:
        s = p.snapshot
        if s.error:
            rows.append({"Symbol": s.symbol, "Verdict": "— " + s.error})
            continue
        rows.append({
            "Symbol": s.symbol,
            "Price": round(s.price, 2) if s.price else None,
            "Verdict": _VERDICT_WORD.get(s.verdict, s.verdict),
            "Quality": quality_label(s.symbol, s.grade),
            "Fits your SOP": _STRATEGY_SHORT.get(p.strategy_key, p.strategy_key),
            "Income $/mo": s.credit_dollars,
            "Yield %/mo": s.monthly_yield_pct,
            "Premium deal": s.richness,
            "Vol rank": s.iv_rank,
            "Dividend %/yr": p.dividend.yield_pct,
            "Watch out": ("⚠ earnings first" if s.earnings_before_expiry
                          else "⚠ hard to trade" if s.liquidity == "Thin" else "—"),
        })
    return pd.DataFrame(rows)


def picks_income_column_config():
    cfg = premium_column_config()
    cfg["Price"] = st.column_config.NumberColumn(format="$%.2f",
        help="The share price now (about 15 minutes delayed). 100 shares cost this x100.")
    cfg["Fits your SOP"] = st.column_config.TextColumn(
        help="The strategy from YOUR playbook this name points to: a Cash Secured Put when "
             "it's affordable and steady, a PMCC when 100 shares cost too much, a covered "
             "call model when the trend is down (income only if you own the shares).")
    cfg["Dividend %/yr"] = st.column_config.NumberColumn(format="%.2f%%",
        help="Cash the fund or company pays its shareholders each year, as a % of the price. "
             "A nice extra if you ever end up owning the shares - it is NOT part of the "
             "option premium. Blank = pays none.")
    return cfg


# ================================================= covered call candidates
_CC_VERDICT = {"sell": "✅ good", "okay": "➖ okay", "skip": "❌ skip"}


def covered_call_dataframe(picks: list) -> pd.DataFrame:
    """The covered-call table: what it pays, and what it pays in a year.

    Yield is the point here - the credit against the cost of the 100 shares -
    so both the monthly and the annualised rate are columns, not footnotes.
    """
    rows = []
    for p in picks:
        rows.append({
            "Verdict": _CC_VERDICT.get(p.verdict, p.verdict),
            "Symbol": p.symbol,
            "Price": p.price,
            "Delta": p.call_delta,
            "Credit $": p.call_credit,
            "Yield/mo %": p.monthly_yield_pct,
            "Yield/yr %": p.annualized_yield_pct,
            "Quality": quality_label(p.symbol, p.grade),
            "Trend": p.trend.title(),
            "Days": p.dte,
        })
    return pd.DataFrame(rows)


def covered_call_column_config():
    return {
        "Verdict": st.column_config.TextColumn(width=80,
            help="Judged only on the covered call: is the premium worth the money the "
                 "shares tie up, is it tradable, and is it a name worth owning."),
        "Price": st.column_config.NumberColumn(format="$%.2f", width=80,
            help="What one share costs right now."),
        "Delta": st.column_config.NumberColumn(format="%.2f", width=70,
            help="The delta of the call being sold - your SOP's covered-call strike is "
                 "about 0.30, roughly a 30% chance the shares get called away."),
        "Credit $": st.column_config.NumberColumn(format="$%d", width=85,
            help="Cash the call pays you today, for one contract."),
        "Yield/mo %": st.column_config.NumberColumn(format="%.2f%%", width=95,
            help="The credit as a percentage of what the shares cost - what this month's "
                 "call earns on the money tied up."),
        "Yield/yr %": st.column_config.NumberColumn(format="%.0f%%", width=95,
            help="That same rate repeated for a year. Simple, not compounded, and it "
                 "assumes you keep finding the same trade every month - treat it as a "
                 "way to compare names, not a forecast."),
        "Quality": st.column_config.TextColumn(width=75,
            help="Company grade A-F. ETFs are baskets, so shown as ETF. It matters "
                 "because you own the shares."),
        "Trend": st.column_config.TextColumn(width=80,
            help="Covered calls work on any trend - but a downtrend means the premium "
                 "is cushioning a fall, not adding to a rise."),
        "Days": st.column_config.NumberColumn(format="%d", width=65,
            help="Days until the call expires."),
    }


def render_covered_call_detail(pick) -> None:
    """One covered call in full: the plan, the yields, and the honest caveats."""
    st.markdown(_esc(
        f"**{pick.symbol}** · ${pick.price:,.2f} · trend {pick.trend} · "
        f"quality {quality_label(pick.symbol, pick.grade)}"))
    m = st.columns(4)
    m[0].metric("Yield this month", f"{pick.monthly_yield_pct:.2f}%"
                if pick.monthly_yield_pct is not None else "-",
                help="The call credit as a percentage of what the 100 shares cost.")
    m[1].metric("Annualised", f"{pick.annualized_yield_pct:.0f}%"
                if pick.annualized_yield_pct is not None else "-",
                help="The same rate repeated for a year - simple, not compounded.")
    m[2].metric("Credit", _dollars(pick.call_credit or 0))
    m[3].metric("Shares cost", _dollars(pick.shares_cost or 0))
    for line in pick.why:
        theme.note("• " + line)
    for w in pick.warnings:
        st.warning(_esc(w))


# ================================================== shared trade-logging inputs
def bp_effect_input(size, key: str) -> None:
    """Let the real thinkorswim BP Effect override the app's estimate.

    Her ruling: TOS is always right. The app cannot see her broker's margin
    rules - house requirements are not the Reg-T textbook - so where it can only
    guess, the number she can read off the screen wins.

    Shared: both Quick Log and the Find-a-trade log button ask for it.
    """
    est = float(size.get("buying_power", 0.0))
    typed = st.number_input(
        "Buying power effect from thinkorswim ($, optional)",
        min_value=0.0, value=0.0, step=25.0, key=f"bpeff_{key}",
        help=f"The **BP Effect** column on the position row in TOS. The app "
             f"estimates ${est:,.0f}, and your broker's own number beats that "
             f"estimate every time - type it here and the monthly budget uses "
             f"it. Leave at 0 to keep the estimate.")
    if typed > 0:
        size["bp_effect"] = float(typed)
        theme.note(f"Using **\\${typed:,.0f}** from thinkorswim instead of the app's "
                   f"**\\${est:,.0f}** estimate.")


# ==================================================== several names, side by side
def compare_dataframe(rows) -> pd.DataFrame:
    """One row per ticker, typed - not pre-formatted strings.

    Real numbers rather than "$305.26" text so the column can sort by value and
    a missing figure stays genuinely blank. A formatted "n/a" would sort as text
    and sit in the middle of the prices.
    """
    return pd.DataFrame([{
        "Symbol": r.symbol,
        "Price": r.price,
        "Today %": r.change_pct,
        "1 year %": r.year_pct,
        "Off high %": r.off_high_pct,
        "Trend": (r.trend or "").title(),
        "RSI": r.rsi,
        "Quality": r.grade,
        "P/E": r.pe,
        "Sales growth %": r.rev_growth_pct,
        "Earnings in": r.days_to_earnings,
    } for r in rows])


def compare_column_config():
    return {
        "Symbol": st.column_config.TextColumn(
            help="Click a ticker's button above the table to open it in full."),
        "Price": st.column_config.NumberColumn(format="$%.2f",
            help="Latest price, about 15 minutes delayed."),
        "Today %": st.column_config.NumberColumn(format="%+.2f%%",
            help="Move so far today."),
        "1 year %": st.column_config.NumberColumn(format="%+.0f%%",
            help="Where it is against this time last year."),
        "Off high %": st.column_config.NumberColumn(format="%+.0f%%",
            help="How far below its 12-month high it is trading. Near 0 means "
                 "it is at the top of its range - where your SOP sells calls "
                 "rather than buys them."),
        "Trend": st.column_config.TextColumn(
            help="Up, down or sideways, from the moving averages."),
        "RSI": st.column_config.NumberColumn(format="%.0f",
            help="Momentum, 0-100. Under 30 is oversold, over 70 overbought. "
                 "Your LEAPS rule buys under 45."),
        "Quality": st.column_config.TextColumn(
            help="A-F report card on the company. ETF means it is a basket, so "
                 "there is no single company to grade."),
        "P/E": st.column_config.NumberColumn(format="%.1f",
            help="Price against earnings. Blank for funds and for anything that "
                 "does not turn a profit."),
        "Sales growth %": st.column_config.NumberColumn(format="%+.0f%%",
            help="Revenue growth over the past year."),
        "Earnings in": st.column_config.NumberColumn(format="%d days",
            help="Days to the next report. Your SOP does not open a trade that "
                 "straddles one. Blank for funds."),
    }
