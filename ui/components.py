"""Reusable Streamlit pieces: the SOP checklist and the candidate table.

Kept beginner-friendly: green means good to go, red means a rule is broken,
yellow means allowed but pay attention, blue is a reminder.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

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


_GRADE_COLOR = {"A": "#059669", "B": "#10B981", "C": "#D97706", "D": "#EA580C", "F": "#DC2626"}


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
    gcolor = _GRADE_COLOR.get(analysis.grade, "#D97706")
    pe = info.get("trailingPE")
    fpe = info.get("forwardPE")
    ps = info.get("priceToSalesTrailing12Months")
    rev = info.get("totalRevenue")
    rg = info.get("revenueGrowth")
    eps = info.get("trailingEps")
    eg = info.get("earningsGrowth")

    def stat(label, value):
        return (f"<span style='margin-right:18px;white-space:nowrap;'>"
                f"<span style='color:#64748B;'>{label}</span> "
                f"<b>{value}</b></span>") if value else ""

    row1 = "".join([
        stat("Mkt Cap", _fmt_big(info.get("marketCap"))),
        stat("P/E", f"{pe:.1f}" if pe else ""),
        stat("Fwd P/E", f"{fpe:.1f}" if fpe else ""),
        stat("P/S", f"{ps:.1f}" if ps else ""),
    ])
    row2 = "".join([
        stat("Revenue (12mo)", f"{_fmt_big(rev)}" + (f" <span style='color:#059669;'>({rg*100:+.0f}%)</span>" if rg is not None else "") if rev else ""),
        stat("EPS (12mo)", f"&#36;{eps:.2f}" + (f" <span style='color:#059669;'>({eg*100:+.0f}%)</span>" if eg is not None else "") if eps else ""),
    ])
    return (
        f"<div style='background:#F8F9FC;border:1px solid #E2E8F0;border-radius:14px;"
        f"padding:12px 16px;display:flex;justify-content:space-between;align-items:center;"
        f"gap:12px;margin:4px 0 10px;'>"
        f"<div style='line-height:2;'>"
        f"<div style='font-weight:700;margin-bottom:2px;'>Quality score "
        f"<span style='color:#64748B;font-weight:500;font-size:0.85rem;'>"
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
    color = "#059669" if rising else "#DC2626"
    rgba = "5,150,105" if rising else "220,38,38"

    base = alt.Chart(df).encode(
        x=alt.X("Date:T", axis=alt.Axis(title=None, format="%b '%y", grid=False,
                                        labelColor="#64748B", domainColor="#E2E8F0")),
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
                axis=alt.Axis(title=None, format="$,.0f", labelColor="#64748B",
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
                text="E", dy=0, fontSize=11, fontWeight="bold", color="#059669",
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
        st.caption(f"Sector: {analysis.sector}")

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
                ccolor = "#059669" if diff >= 0 else "#DC2626"
                arrow = "▲" if diff >= 0 else "▼"
                change_html = (f"<span style='color:{ccolor};font-weight:700;'>"
                               f"{arrow} &#36;{abs(diff):,.2f} ({pct:+.1f}%)</span>"
                               f"<span style='color:#64748B;'> · past {choice or '1Y'}</span>")
            today_html = (f"<span style='color:#64748B;font-size:0.9rem;'> · today "
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
        st.caption("Price history unavailable right now.")

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
            vcolor = {"BUY": "#059669", "HOLD": "#D97706", "SELL": "#DC2626"}[verdict]
            st.markdown(
                f"<span style='background:{vcolor};color:#fff;border-radius:10px;"
                f"padding:2px 14px;font-weight:800;'>{verdict}</span> "
                f"<span style='font-size:0.95rem;'>({total} analysts)</span>",
                unsafe_allow_html=True,
            )
            for label, n, color in (("Buy", buy, "#059669"), ("Hold", hold, "#D97706"),
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
            st.caption("No analyst data available for this name.")

    with col_e:
        st.markdown("**🎯 Earnings: expected vs delivered**")
        if eps_history:
            df = pd.DataFrame({
                "Quarter": [q["label"] for q in eps_history],
                "EPS": [q["actual"] for q in eps_history],
                "Expected": [q["estimate"] for q in eps_history],
                "Surprise": [f"{q['surprise_pct']:+.1f}%" for q in eps_history],
                "Result": ["Beat" if q["beat"] else "Missed" for q in eps_history],
            })
            scatter = alt.Chart(df).mark_circle(size=110, opacity=1).encode(
                x=alt.X("Quarter:N", sort=None,
                        axis=alt.Axis(title=None, labelAngle=-45,
                                      labelColor="#64748B", domainColor="#E2E8F0")),
                y=alt.Y("EPS:Q", scale=alt.Scale(zero=False, nice=True),
                        axis=alt.Axis(title=None, format="$,.2f",
                                      labelColor="#64748B", gridColor="#EEF2F7",
                                      domainOpacity=0)),
                color=alt.Color("Result:N", legend=None,
                                scale=alt.Scale(domain=["Beat", "Missed"],
                                                range=["#059669", "#DC2626"])),
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
            beats = sum(1 for q in eps_history if q["beat"])
            misses = len(eps_history) - beats
            st.caption(f"🟢 beat / 🔴 missed analyst estimates (hover a dot for the numbers) - "
                       f"beat in **{beats} of the last {len(eps_history)} quarters**"
                       + (f", missed {misses}" if misses else "")
                       + ". Companies that beat steadily tend to hold up better.")
        else:
            st.caption("No earnings history available for this name.")


def render_tv_ratings(ratings: dict, title: str = "TradingView technical rating") -> None:
    """TradingView's verdict, shown as an indicator vote: TradingView runs ~26
    technical indicators (moving averages, RSI, MACD...) and each one votes
    buy, neutral, or sell. We show the tally as a colored bar so it reads at
    a glance instead of as cryptic numbers.
    """
    if not ratings:
        return
    st.markdown(f"**📊 {title}**")
    verdict_color = {"green": "#059669", "orange": "#D97706", "red": "#DC2626"}
    cols = st.columns(len(ratings))
    for col, (label, r) in zip(cols, ratings.items()):
        total = max(r.buy + r.neutral + r.sell, 1)
        b, n, s = (r.buy / total * 100, r.neutral / total * 100, r.sell / total * 100)
        vc = verdict_color.get(r.color, "#D97706")
        window = "on daily charts" if label == "daily" else "on weekly charts (longer view)"
        with col:
            st.markdown(
                f"<div style='margin-bottom:2px;'>{label.title()}: "
                f"<b style='color:{vc};'>{r.recommendation}</b></div>"
                # one bar, three colored segments = the indicator vote
                f"<div style='display:flex;height:12px;border-radius:6px;overflow:hidden;"
                f"border:1px solid #E2E8F0;max-width:340px;'>"
                f"<div style='width:{b:.0f}%;background:#059669;'></div>"
                f"<div style='width:{n:.0f}%;background:#CBD5E1;'></div>"
                f"<div style='width:{s:.0f}%;background:#DC2626;'></div></div>"
                f"<div style='font-size:0.85rem;color:#64748B;margin-top:2px;'>"
                f"{total} indicators {window}: "
                f"<span style='color:#059669;font-weight:600;'>{r.buy} buy</span> · "
                f"{r.neutral} neutral · "
                f"<span style='color:#DC2626;font-weight:600;'>{r.sell} sell</span></div>",
                unsafe_allow_html=True,
            )
    st.caption("How to read this: TradingView runs ~26 technical indicators (moving averages, "
               "RSI, MACD...). Each votes buy, neutral, or sell - the verdict is the tally. "
               "A second opinion, not a signal to trade on its own.")


_VERDICT_STYLE = {
    "sell": ("✅ Good to sell", "#059669", "#ECFDF5", "#A7F3D0"),
    "okay": ("⚠️ Okay", "#B45309", "#FFFBEB", "#FDE68A"),
    "skip": ("❌ Skip", "#B91C1C", "#FEF2F2", "#FECACA"),
}


def render_premium_cards(snapshots: list) -> None:
    """Verdict-first cards: for each name, the bottom-line call, the income, and
    one reason. Detail lives behind the picker below (progressive disclosure)."""
    for s in snapshots:
        if s.error:
            st.markdown(
                f"<div style='border:1px solid #E2E8F0;border-radius:14px;padding:12px 16px;"
                f"margin-bottom:10px;color:#64748B;'><b>{s.symbol}</b> - {s.error}</div>",
                unsafe_allow_html=True)
            continue

        label, vcolor, vbg, vborder = _VERDICT_STYLE.get(s.verdict, _VERDICT_STYLE["okay"])
        gcolor = _GRADE_COLOR.get(s.grade, "#4F46E5")   # ETF/None -> indigo
        grade_txt = s.grade or "ETF"
        rich_color = ("#059669" if s.richness == "Rich"
                      else "#B45309" if s.richness == "Fair" else "#B91C1C")
        cushion = (f" · falls to ${s.breakeven:,.0f} before you lose "
                   f"({s.cushion_pct:.0f}% cushion)" if s.cushion_pct is not None else "")
        flags = ("<div style='color:#B45309;margin-top:4px;font-size:0.95rem;'>⚠️ "
                 + " · ".join(s.flags) + "</div>") if s.flags else ""

        st.markdown(
            f"""
            <div style="border:1px solid #E2E8F0;border-radius:14px;padding:14px 16px;
                        margin-bottom:10px;background:#fff;box-shadow:0 1px 3px rgba(15,23,42,.05);">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                <div style="display:flex;align-items:center;gap:10px;">
                  <span style="background:{gcolor};color:#fff;border-radius:8px;padding:2px 9px;
                               font-weight:800;font-size:0.95rem;">{grade_txt}</span>
                  <span style="font-size:1.25rem;font-weight:800;">{s.symbol}</span>
                  <span style="color:#64748B;">${s.price:,.2f}</span>
                </div>
                <span style="background:{vbg};border:1px solid {vborder};color:{vcolor};
                             border-radius:999px;padding:3px 14px;font-weight:700;
                             white-space:nowrap;">{label}</span>
              </div>
              <div style="margin-top:8px;font-size:1.1rem;">
                <b>${s.credit_dollars:,.0f}/month</b>
                <span style="color:#64748B;">({s.monthly_yield_pct:.1f}%)</span>
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
            st.caption("No suitable call found for a covered call right now.")

    deal = {
        "Rich": "Premium is **Rich** - you're paid more than this stock's usual movement would "
                "justify. That's a good deal for you as the seller.",
        "Fair": "Premium is **Fair** - about normal for how much this stock moves.",
        "Thin": "Premium is **Thin** - the stock swings a lot but pays little for the risk. A poor "
                "deal; look for a name that pays more.",
    }.get(s.richness)
    if deal:
        st.caption(deal)
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
        st.caption(_esc(" · ".join(advice.outlook_reasons)))

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

    st.caption(advice.dte_note)


def render_events(events, empty_note: str = "No major events in the next few weeks.") -> None:
    """Compact list of upcoming market events, soonest first."""
    if not events:
        st.caption(empty_note)
        return
    for e in events:
        when = e.date.strftime("%a %b %d")
        days = "today" if e.days_away == 0 else f"in {e.days_away} day{'s' if e.days_away != 1 else ''}"
        line = f"{e.icon} **{e.label}** - {when} ({days})"
        if e.in_window:
            st.markdown(f":orange[{line}]  \n:orange[⚠️ lands inside your trade window - {e.note}]")
        else:
            st.markdown(line)


def render_checklist(report: ValidationReport) -> None:
    """Show every SOP rule as a colored line."""
    if report.passed:
        st.success(f"This trade PASSES your SOP for {report.strategy_name}. "
                   f"{report.n_warned} thing(s) to watch." if report.n_warned
                   else f"This trade PASSES your SOP for {report.strategy_name}. Clear to enter.")
    else:
        st.error(f"This trade BREAKS {report.n_failed} of your rules. Do not enter until fixed.")

    for r in report.results:
        line = _esc(f"{r.icon}  **{r.name}** - {r.message}")
        if r.status == CheckStatus.FAIL:
            st.markdown(f":red[{line}]")
        elif r.status == CheckStatus.WARN:
            st.markdown(f":orange[{line}]")
        elif r.status == CheckStatus.PASS:
            st.markdown(f":green[{line}]")
        else:
            st.markdown(f":blue[{line}]")


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
