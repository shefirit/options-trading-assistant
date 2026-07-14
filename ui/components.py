"""Reusable Streamlit pieces: the SOP checklist and the candidate table.

Kept beginner-friendly: green means good to go, red means a rule is broken,
yellow means allowed but pay attention, blue is a reminder.
"""

from __future__ import annotations

import html as _htmllib

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

_STATUS_COLOR = {"good": "green", "ok": "orange", "watch": "red"}
_STATUS_ICON = {"good": "✅", "ok": "➖", "watch": "⚠️"}


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


def render_strategy_fit(suggestions: list) -> None:
    """The ranked strategy board: every index strategy with a fit chip and the
    one-line reason - a vertical list, so it reads the same on a phone."""
    fit_tags = [("green", "Best fit today"), ("indigo", "Also workable"),
                ("amber", "Weaker fit today")]
    for i, s in enumerate(suggestions[:3]):
        tone, tag = fit_tags[i] if i < len(fit_tags) else ("neutral", "Also possible")
        st.markdown(
            theme.chip(f"{i + 1} · {tag}", tone)
            + f" <b>{_htmllib.escape(s.name)}</b>",
            unsafe_allow_html=True)
        theme.note(s.reason)


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
    gcolor = _GRADE_COLOR.get(analysis.grade, "#B45309")
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
        f"<div style='font-weight:700;margin-bottom:2px;'>Quality score "
        f"<span style='color:#35463D;font-weight:500;font-size:0.85rem;'>"
        f"(from the checks below)</span></div>"
        f"<div>{row1}</div><div>{row2}</div></div>"
        f"<div style='background:{gcolor};color:#fff;border-radius:12px;min-width:52px;"
        f"height:52px;display:flex;align-items:center;justify-content:center;"
        f"font-size:1.5rem;font-weight:800;'>{analysis.grade}</div></div>"
    )


# Time ranges for the price chart (label -> yfinance period).
PRICE_RANGES = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "YTD": "ytd",
                "1Y": "1y", "2Y": "2y", "Max": "max"}


def render_price_chart(frame, earnings_dates: list | None = None) -> None:
    """A modern stock chart: thin line + soft gradient, y-axis zoomed to the
    data (not from zero), hover crosshair with price tooltip, and dashed 'E'
    markers on past earnings dates - like the chart Rita liked on EarningsHub.
    """
    df = frame.reset_index()
    df.columns = ["Date", "Close"]
    rising = float(df["Close"].iloc[-1]) >= float(df["Close"].iloc[0])
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
    try:
        st.altair_chart(chart, width="stretch")
    except TypeError:  # older Streamlit
        st.altair_chart(chart, use_container_width=True)


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
            if frame is not None and len(frame) > 1:
                first = float(frame["Close"].iloc[0])
                last = float(frame["Close"].iloc[-1])
                diff, pct = last - first, (last - first) / first * 100
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
            try:
                st.altair_chart(scatter, width="stretch")
            except TypeError:
                st.altair_chart(scatter, use_container_width=True)
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
        grade_txt = s.grade or "ETF"
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
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Income / month", f"${s.credit_dollars:,.0f}")
    m2.metric("As % of cash", f"{s.monthly_yield_pct:.2f}%")
    m3.metric("Premium deal", s.richness,
              help="Rich = you're paid more than this stock's usual moves would justify (good "
                   "for you). Thin = it moves a lot but pays little (bad). Fair = normal.")
    m4.metric("Can trade?", s.liquidity,
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
            "Quality": s.grade or "ETF",
            "Income $/mo": s.credit_dollars,
            "Yield %/mo": s.monthly_yield_pct,
            "Premium deal": s.richness,
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
        "Yield %/mo": _st.column_config.NumberColumn(format="%.2f%%",
            help="That income as a % of the cash you set aside - the fair way to compare names."),
        "Premium deal": _st.column_config.TextColumn(
            help="Is the premium a good deal for the risk? Rich = you're paid MORE than this "
                 "stock's usual swings would justify (good for you). Thin = it swings a lot but "
                 "pays little (bad). Fair = about normal."),
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
        when = e.date.strftime("%a %b %d")
        days = "today" if e.days_away == 0 else f"in {e.days_away} day{'s' if e.days_away != 1 else ''}"
        label = f"{e.icon} <b>{_htmllib.escape(e.label)}</b> - {when} ({days})"
        if e.in_window:
            st.markdown(
                f"<div style='color:#213229;line-height:1.55;margin-top:4px;'>{label}</div>"
                f"<div style='color:{_STATUS_TEXT['WARN']};font-weight:600;line-height:1.5;'>"
                f"⚠️ lands inside your trade window - {_htmllib.escape(e.note)}</div>",
                unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='color:#213229;line-height:1.55;margin-top:4px;'>{label}</div>",
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


def candidates_dataframe(candidates: list[Candidate]) -> pd.DataFrame:
    """Turn scanner candidates into a readable table."""
    rows = []
    for i, c in enumerate(candidates):
        strikes = " / ".join(f"{leg.strike:g}" for leg in c.trade.legs)
        rows.append({
            "#": i + 1,
            "Fits my rules": "✅ yes" if c.fits_sop else "⚠️ delta a bit over",
            "Underlying": c.trade.underlying,
            "Legs (strikes)": strikes,
            "Short Δ": round(c.short_delta, 3),
            "DTE": c.dte,
            "Credit $": round(c.credit, 0),
            "Max loss $": round(c.max_loss, 0),
            "Buying power $": round(c.buying_power, 0),
            "Return/Risk": f"{c.return_on_risk * 100:.1f}%",
        })
    return pd.DataFrame(rows)


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
    return f"&#36;{x:,.0f}"


def render_risk_card(trade, strategy, size: dict, payoff_profile=None,
                     bp_limit: float = 50_000) -> None:
    """The stop-and-look card shown right before the Log button: the most you
    can lose in plain dollars, plus the three exit alerts to set in
    thinkorswim the moment the trade is filled."""
    credit = float(size.get("credit", 0.0))
    max_loss = float(size.get("max_loss", 0.0))
    bp = float(size.get("buying_power", 0.0))
    contracts = max(int(trade.contracts), 1)
    exit_cfg = strategy.get("exit", {})

    max_profit = credit
    breakevens: list[float] = []
    if payoff_profile is not None:
        max_profit = max(payoff_profile.max_profit, 0.0)
        breakevens = payoff_profile.breakevens

    be_txt = " / ".join(f"{b:,.2f}" for b in breakevens) if breakevens else "-"
    pct_of_limit = (bp / bp_limit * 100) if bp_limit else 0.0

    st.markdown(
        f"""
        <div style="border:2px solid {theme.RED};border-radius:14px;padding:14px 18px;
                    background:#FDF3F2;margin:8px 0 4px;">
          <div style="font-weight:800;color:{theme.RED};font-size:1.05rem;">
            ⚠️ Know your risk before you log this</div>
          <div style="display:flex;gap:28px;flex-wrap:wrap;margin-top:10px;">
            <div><div style="color:#5B2320;font-weight:600;font-size:.85rem;">MOST YOU CAN LOSE</div>
                 <div style="font-size:1.5rem;font-weight:800;color:{theme.RED};">{_dollars(max_loss)}</div></div>
            <div><div style="color:#1F4433;font-weight:600;font-size:.85rem;">MOST YOU CAN MAKE</div>
                 <div style="font-size:1.5rem;font-weight:800;color:{theme.GREEN};">{_dollars(max_profit)}</div></div>
            <div><div style="color:#213229;font-weight:600;font-size:.85rem;">BREAKEVEN PRICE</div>
                 <div style="font-size:1.5rem;font-weight:800;color:{theme.INK};">{be_txt}</div></div>
            <div><div style="color:#213229;font-weight:600;font-size:.85rem;">BUYING POWER USED</div>
                 <div style="font-size:1.5rem;font-weight:800;color:{theme.INK};">{_dollars(bp)}
                 <span style="font-size:.9rem;font-weight:600;"> ({pct_of_limit:.0f}% of your monthly limit)</span></div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True)

    # The three exits, translated into numbers she can type into TOS alerts.
    lines = []
    pt = exit_cfg.get("profit_target_pct")
    if pt and credit > 0:
        target_cost = credit * (1 - float(pt) / 100)
        per_share = target_cost / (100 * contracts)
        lines.append(
            f"✅ <b>Profit target ({pt:g}%):</b> close when buying it back costs about "
            f"{_dollars(target_cost)} (&#36;{per_share:,.2f} per share) - you keep "
            f"{_dollars(credit - target_cost)}.")
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
            lines.append(f"⏰ <b>Time exit:</b> close by <b>{exit_day:%A, %B %d}</b> "
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

    st.altair_chart(alt.layer(*layers).properties(height=260), use_container_width=True)

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
    "time": "⏰ Close - time exit",
    "profit": "✅ Take the win",
    "watch": "⚠️ Watch closely",
    "hold": "✋ Hold",
    "unpriced": "❓ Could not price",
}


def positions_dataframe(items: list[dict]) -> pd.DataFrame:
    """items: [{"position": Position, "live": dict, "signal": ExitSignal}]"""
    rows = []
    for it in items:
        pos, live, sig = it["position"], it["live"], it["signal"]
        rows.append({
            "What to do": _SIGNAL_WORD.get(sig.action, sig.action),
            "Symbol": pos.underlying,
            "Strategy": pos.strategy_name,
            "Opened": pos.opened.isoformat() if pos.opened else "-",
            "Days left": pos.dte_left(),
            "Credit $": pos.credit,
            "Close now $": live.get("cost_to_close"),
            "P&L $": sig.pl_dollars,
            "% kept": sig.profit_pct,
        })
    return pd.DataFrame(rows)


def positions_column_config():
    return {
        "What to do": st.column_config.TextColumn(
            help="Your own exit rules applied to live prices. Red = close, green = take "
                 "the win, amber = needs eyes on it."),
        "Days left": st.column_config.NumberColumn(format="%d",
            help="Days to expiration. Your SOP closes everything at 21."),
        "Credit $": st.column_config.NumberColumn(format="$%.0f",
            help="Cash you collected when you opened it."),
        "Close now $": st.column_config.NumberColumn(format="$%.0f",
            help="What it costs to buy the position back right now (mid prices)."),
        "P&L $": st.column_config.NumberColumn(format="$%.0f",
            help="Credit received minus today's cost to close."),
        "% kept": st.column_config.NumberColumn(format="%.0f%%",
            help="How much of the credit is yours so far. Your SOP takes the win at 50%."),
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


def closed_dataframe(closed: list) -> pd.DataFrame:
    rows = []
    for p in sorted(closed, key=lambda p: (p.closed_on or p.opened or pd.Timestamp.min.date()),
                    reverse=True):
        rows.append({
            "Closed": p.closed_on.isoformat() if p.closed_on else "-",
            "Symbol": p.underlying,
            "Strategy": p.strategy_name,
            "Opened": p.opened.isoformat() if p.opened else "-",
            "Credit $": p.credit,
            "Exit cost $": p.exit_cost,
            "Result $": p.realized_pl,
            "Why closed": p.exit_reason,
        })
    return pd.DataFrame(rows)


def render_results_dashboard(perf: dict, targets: dict, bp_used: float,
                             bp_limit: float) -> None:
    """Am I on pace? Realized results vs her weekly/monthly goals, win rate,
    which strategies earn, and how much buying power is tied up right now."""
    weekly_goal = float(targets.get("weekly", 0) or 0)
    monthly_goal = float(targets.get("monthly", 0) or 0)

    c1, c2 = st.columns(2)
    with c1:
        wk = perf["week_pl"]
        st.markdown(_esc(f"**This week:** ${wk:,.0f} of your ${weekly_goal:,.0f} goal"))
        st.progress(min(max(wk / weekly_goal, 0.0), 1.0) if weekly_goal else 0.0)
    with c2:
        mo = perf["month_pl"]
        st.markdown(_esc(f"**This month:** ${mo:,.0f} of your ${monthly_goal:,.0f} goal"))
        st.progress(min(max(mo / monthly_goal, 0.0), 1.0) if monthly_goal else 0.0)

    m = st.columns(5)
    m[0].metric("Closed trades", perf["closed_count"])
    m[1].metric("All-time result", f"${perf['total_pl']:,.0f}")
    m[2].metric("Win rate", f"{perf['win_rate'] * 100:.0f}%" if perf["win_rate"] is not None else "-")
    m[3].metric("Average winner", f"${perf['avg_win']:,.0f}" if perf["avg_win"] is not None else "-")
    m[4].metric("Average loser", f"${perf['avg_loss']:,.0f}" if perf["avg_loss"] is not None else "-")

    # Buying power tied up across every open trade - the real monthly-limit view.
    used_pct = (bp_used / bp_limit) if bp_limit else 0.0
    tone = theme.RED if used_pct > 1.0 else theme.AMBER if used_pct > 0.8 else theme.GREEN
    st.markdown(
        f"<div style='margin-top:6px;font-weight:700;color:{tone};'>"
        f"Open trades are using {_dollars(bp_used)} of your {_dollars(bp_limit)} monthly "
        f"buying-power limit ({used_pct * 100:.0f}%).</div>",
        unsafe_allow_html=True)
    st.progress(min(used_pct, 1.0))

    if perf["by_strategy"]:
        st.markdown("**By strategy** (which ones actually earn for you):")
        rows = [{"Strategy": name, "Trades": s["trades"],
                 "Wins": s["wins"], "Total $": round(s["pl"], 0)}
                for name, s in sorted(perf["by_strategy"].items(),
                                      key=lambda kv: -kv[1]["pl"])]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    if len(perf["cumulative"]) >= 2:
        df = pd.DataFrame(perf["cumulative"])
        df["date"] = pd.to_datetime(df["date"])
        chart = alt.Chart(df).mark_line(color=theme.ACCENT, strokeWidth=2.5,
                                        point=True).encode(
            x=alt.X("date:T", title="When you closed each trade"),
            y=alt.Y("total:Q", title="Running total ($)"),
            tooltip=[alt.Tooltip("date:T", title="Date"),
                     alt.Tooltip("total:Q", title="Total $", format=",.0f")])
        st.altair_chart(chart.properties(height=220), use_container_width=True)


# ================================================================== month view
def _month_result_word(position, tag: str) -> str:
    """One plain-English word for how this trade sits in THIS month's list."""
    if tag in ("closed", "both") and position.status == "closed":
        pl = position.realized_pl
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

    rows: [{"position": Position, "tag": "closed"|"opened"|"both"}] from
    positions.monthly_summary. Money banked this month sorts to the top."""
    def sort_key(r):
        closed_here = r["tag"] in ("closed", "both") and r["position"].status == "closed"
        return 0 if closed_here else 1

    out = []
    for r in sorted(rows, key=sort_key):
        p = r["position"]
        reason = (p.exit_reason or "").split(" - ", 1)[0]
        out.append({
            "Result": _month_result_word(p, r["tag"]),
            "Symbol": p.underlying,
            "Strategy": p.strategy_name,
            "Opened": p.opened.isoformat() if p.opened else "-",
            "Closed": p.closed_on.isoformat() if p.closed_on else "-",
            "Credit $": p.credit,
            "Result $": p.realized_pl,
            "Why closed": reason or "-",
        })
    return pd.DataFrame(out)


def month_trades_column_config():
    return {
        "Result": st.column_config.TextColumn(
            help="How this trade ended up. 'Still open' trades are being "
                 "watched in the open-trades list above."),
        "Credit $": st.column_config.NumberColumn(format="$%.0f",
            help="Cash collected when the trade was opened."),
        "Result $": st.column_config.NumberColumn(format="$%.0f",
            help="What the trade actually made or lost when it was closed."),
        "Why closed": st.column_config.TextColumn(
            help="The exit rule (or reason) recorded at close."),
    }


def render_month_summary(entry: dict, monthly_goal: float, bp_limit: float) -> None:
    """One month's report card: the profit number vs her goal, the counts,
    the discipline score, and what she wrote down as lessons."""
    realized = float(entry["realized_pl"])
    goal = float(monthly_goal or 0)
    st.markdown(_esc(f"**{entry['label']}: ${realized:,.0f}** of your "
                     f"${goal:,.0f} goal"))
    st.progress(min(max(realized / goal, 0.0), 1.0) if goal else 0.0)

    m = st.columns(4)
    m[0].metric("Closed trades", entry["closed_count"])
    m[1].metric("Win rate",
                f"{entry['win_rate'] * 100:.0f}%" if entry["win_rate"] is not None
                else "-")
    m[2].metric("Opened", entry["opened_count"],
                help="Trades opened during this month, whatever happened later.")
    m[3].metric("BP used", _dollars(entry["bp_opened"]),
                help=f"Buying power committed by trades opened this month. "
                     f"Your SOP allows {_dollars(bp_limit)} per month.")

    if entry["closed_count"]:
        n, total = entry["rules_followed"], entry["closed_count"]
        tone = "green" if n == total else "amber"
        st.markdown(theme.chip(
            f"Rules followed: {n} of {total} closes", tone),
            unsafe_allow_html=True)
        if n < total:
            theme.note("A close counts as 'by the rules' when the reason was "
                       "the 50% profit target, the 21-day time exit, the stop "
                       "loss, or expiring worthless. Following the rules "
                       "matters more than the P&L while you learn.")

    if entry["lessons"]:
        st.markdown("**What you learned this month:**")
        for lesson in entry["lessons"]:
            theme.note(f"• {lesson}")


def render_month_bars(summaries: list[dict], monthly_goal: float) -> None:
    """Profit per month as bars (green up, red down) with the goal as a
    dashed line - the 'is this working?' picture at a glance."""
    if not any(m["closed_count"] for m in summaries):
        return
    df = pd.DataFrame([{"label": m["label"], "month": m["month"],
                        "profit": m["realized_pl"]} for m in summaries])
    df = df.sort_values("month")   # oldest on the left
    bars = alt.Chart(df).mark_bar(size=42).encode(
        x=alt.X("label:N", sort=list(df["label"]), title=None),
        y=alt.Y("profit:Q", title="Profit ($)"),
        color=alt.condition("datum.profit >= 0",
                            alt.value(theme.GREEN), alt.value(theme.RED)),
        tooltip=[alt.Tooltip("label:N", title="Month"),
                 alt.Tooltip("profit:Q", title="Profit $", format=",.0f")])
    chart = bars
    if monthly_goal:
        rule = alt.Chart(pd.DataFrame({"goal": [monthly_goal]})).mark_rule(
            color=theme.AMBER, strokeDash=[6, 4], strokeWidth=2).encode(y="goal:Q")
        chart = bars + rule
    st.altair_chart(chart.properties(height=240), use_container_width=True)
    if monthly_goal:
        theme.note(f"The dashed line is your **\\${monthly_goal:,.0f}** "
                   "monthly goal.")


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
            "Quality": s.grade or "ETF",
            "Fits your SOP": _STRATEGY_SHORT.get(p.strategy_key, p.strategy_key),
            "Income $/mo": s.credit_dollars,
            "Yield %/mo": s.monthly_yield_pct,
            "Premium deal": s.richness,
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
