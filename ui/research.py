"""Renderers for the Research tab - the "size up any stock" tools.

Same rules as the rest of the app: every number gets a plain-English read
next to it, dollar signs go through &#36; so markdown never eats them, and
nothing here decides anything for her.
"""

from __future__ import annotations

import html as _html
from typing import Optional

import pandas as pd
import streamlit as st

from ui import theme

_STATUS_COLOR = {"good": theme.GREEN, "ok": theme.SECONDARY, "watch": theme.RED}
_STATUS_ICON = {"good": "🟢", "ok": "🟡", "watch": "🔴"}


def _esc(text: str) -> str:
    return _html.escape(str(text))


def _money(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"&#36;{value:,.{decimals}f}"


def _big(n: Optional[float]) -> str:
    if not n:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= size:
            return f"&#36;{n / size:.1f}{unit}"
    return f"&#36;{n:,.0f}"


def stat_tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    """One box in a stats strip. `value` may contain &#36; entities."""
    color = tone or theme.INK
    sub_html = (f"<div style='color:{theme.SECONDARY};font-size:0.82rem;margin-top:2px;'>"
                f"{sub}</div>") if sub else ""
    return (
        f"<div style='background:{theme.TILE};border:1px solid {theme.BORDER};"
        f"border-radius:12px;padding:10px 14px;flex:1 1 150px;min-width:140px;'>"
        f"<div style='color:{theme.SECONDARY};font-size:0.75rem;text-transform:uppercase;"
        f"letter-spacing:0.04em;'>{label}</div>"
        f"<div style='font-size:1.25rem;font-weight:700;color:{color};margin-top:2px;'>"
        f"{value}</div>{sub_html}</div>")


def stat_row(tiles: list[str]) -> None:
    st.markdown(
        "<div style='display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 14px;'>"
        + "".join(tiles) + "</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ pillars
def render_pillars(pillars: list, score: float, headline: str = "") -> None:
    """The score breakdown - a labelled bar per pillar, each expandable to the
    reasons behind it. This is the part their tool hides behind a tap."""
    tone = theme.GREEN if score >= 70 else theme.AMBER if score >= 50 else theme.RED
    st.markdown(
        f"<div style='display:flex;align-items:baseline;justify-content:space-between;'>"
        f"<div style='font-size:1.05rem;font-weight:700;'>LEAPS score</div>"
        f"<div style='font-size:1.9rem;font-weight:800;color:{tone};'>{score:.0f}"
        f"<span style='font-size:0.9rem;color:{theme.SECONDARY};font-weight:600;'>"
        f"/100</span></div></div>", unsafe_allow_html=True)
    if headline:
        theme.note(headline)

    for p in pillars:
        if not p.measured:
            st.markdown(f"<div style='color:{theme.SECONDARY};margin:6px 0;'>"
                        f"<b>{_esc(p.label)}</b> - {_esc(p.read)}</div>",
                        unsafe_allow_html=True)
            continue
        colour = _STATUS_COLOR.get(p.status, theme.SECONDARY)
        st.markdown(
            f"<div style='margin:10px 0 2px;display:flex;align-items:center;gap:10px;'>"
            f"<div style='width:170px;font-weight:650;'>{_esc(p.label)} "
            f"<span style='color:{theme.SECONDARY};font-weight:500;font-size:0.8rem;'>"
            f"{p.weight * 100:.0f}%</span></div>"
            f"<div style='flex:1;background:{theme.BORDER};border-radius:99px;height:9px;'>"
            f"<div style='width:{max(2, min(100, p.score)):.0f}%;background:{colour};"
            f"height:9px;border-radius:99px;'></div></div>"
            f"<div style='width:42px;text-align:right;font-weight:750;color:{colour};'>"
            f"{p.score:.0f}</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='margin-left:180px;color:{theme.CAPTION};"
                    f"font-size:0.9rem;'>{_esc(p.read)}</div>", unsafe_allow_html=True)
        with st.expander(f"Why {p.label.lower()} scored {p.score:.0f}"):
            for factor in p.factors:
                st.markdown(f"- {_esc(factor)}")


# ------------------------------------------------------------- LEAPS detail
def render_leaps_detail(c) -> None:
    """The full scorecard for one LEAPS candidate."""
    title = f"{c.symbol}" + (f" - {c.name}" if c.name else "")
    st.markdown(f"### {_esc(title)}")
    if c.sector:
        st.markdown(theme.chip(c.sector, "neutral"), unsafe_allow_html=True)

    tiles = [
        stat_tile("Price", _money(c.price)),
        stat_tile("Market cap", _big(c.market_cap)),
        stat_tile("Off 52-week high",
                  f"{c.pct_off_52w_high:.1f}%" if c.pct_off_52w_high is not None else "n/a"),
        stat_tile("Realized volatility",
                  f"{c.realized_vol_pct:.0f}%" if c.realized_vol_pct else "n/a",
                  "how much it actually moves"),
    ]
    if c.weekly_k is not None:
        tiles.append(stat_tile("Weekly stochastic", f"{c.weekly_k:.0f}",
                               f"signal line {c.weekly_d:.0f}" if c.weekly_d else ""))
    if c.analyst_target:
        upside = ((c.analyst_target / c.price - 1) * 100) if c.price else None
        tiles.append(stat_tile("Analyst target", _money(c.analyst_target),
                               f"{upside:+.0f}% from here" if upside is not None else ""))
    stat_row(tiles)

    for flag in c.flags:
        st.markdown(theme.chip(f"⚠ {flag}", "amber"), unsafe_allow_html=True)

    if c.econ:
        _render_economics(c)

    st.write("")
    render_pillars(c.pillars, c.score, c.headline)

    if c.base_rate and c.base_rate.hit_rate is not None:
        st.write("")
        _render_base_rate(c)

    if c.comparison:
        st.write("")
        with st.container(border=True):
            st.markdown("**Versus just buying the shares**")
            theme.note(c.comparison.verdict)

    if c.strike_ladder:
        st.write("")
        with st.expander("Compare every strike at this expiration", expanded=False):
            theme.note(
                "Deeper strikes cost more but need a smaller move and lose less to time. "
                "Cheaper ones give you more leverage and need a much bigger move. This "
                "is the real decision - a fixed 0.75 delta is only one row of it.")
            st.dataframe(ladder_frame(c.strike_ladder), hide_index=True,
                         column_config=ladder_columns(), width="stretch")

    st.write("")
    theme.note(c.summary)


def _render_economics(c) -> None:
    e = c.econ
    st.markdown(f"**The contract** - the &#36;{e.strike:,.0f} call expiring {e.expiration}, "
                f"{e.dte} days out", unsafe_allow_html=True)
    cost_tone = theme.RED if e.all_in_cost_ann_pct > 12 else (
        theme.AMBER if e.all_in_cost_ann_pct > 8 else theme.GREEN)
    stat_row([
        stat_tile("What you pay", _money(e.cost_dollars, 0), "per contract"),
        stat_tile("Delta", f"{e.delta:.2f}" if e.delta else "n/a",
                  f"{e.leverage:.1f}x exposure per dollar" if e.leverage else ""),
        stat_tile("Breakeven", _money(e.breakeven),
                  f"{e.required_move_pct:+.1f}% from here"),
        stat_tile("Cost of time", f"{e.extrinsic_ann_pct:.1f}%/yr",
                  f"{_money(e.extrinsic)} per share", cost_tone),
        stat_tile("All-in cost", f"{e.all_in_cost_ann_pct:.1f}%/yr",
                  "time premium + dividends given up", cost_tone),
        stat_tile("Total loss below", _money(e.total_loss_price),
                  f"a {abs(e.total_loss_drop_pct):.0f}% fall wipes it out", theme.RED),
    ])
    bits = [f"Liquidity: {e.liquidity}"]
    if e.spread_pct is not None:
        bits.append(f"bid-ask {e.spread_pct:.0f}% of mid")
    if e.open_interest:
        bits.append(f"{e.open_interest:,} contracts open")
    if e.iv:
        bits.append(f"implied volatility {e.iv * 100:.0f}%")
    if c.iv_percentile is not None:
        bits.append(f"{c.iv_percentile:.0f}th percentile versus its own realized vol")
    theme.note(" · ".join(bits))


def _render_base_rate(c) -> None:
    b = c.base_rate
    with st.container(border=True):
        st.markdown("**Has this stock actually done what you need?**")
        theme.note(
            "This is the question a price target cannot answer. We slide a window the "
            "length of your contract across every day of history and count how often "
            "the stock really did make the move.")
        tone = theme.GREEN if b.hit_rate >= 55 else (
            theme.AMBER if b.hit_rate >= 35 else theme.RED)
        tiles = [
            stat_tile("Needs", f"{b.required_pct:+.1f}%", f"in {b.horizon_days} days"),
            stat_tile("Managed it", f"{b.hit_rate:.0f}% of the time",
                      f"across {b.windows:,} stretches", tone),
            stat_tile("Typical stretch", f"{b.median_pct:+.1f}%",
                      f"over {b.years_used:.0f} years of history"),
        ]
        if b.loss_rate is not None:
            tiles.append(stat_tile("Expired worthless", f"{b.loss_rate:.0f}% of the time",
                                   "finished below the strike", theme.RED))
        if b.p10_pct is not None and b.p90_pct is not None:
            tiles.append(stat_tile("Bad to good", f"{b.p10_pct:+.0f}% to {b.p90_pct:+.0f}%",
                                   "10th to 90th percentile"))
        stat_row(tiles)
        theme.note("Overlapping windows, so treat this as texture rather than a clean "
                   "statistic. It is still this stock's own behaviour rather than a guess.")


def leaps_frame(candidates: list) -> pd.DataFrame:
    rows = []
    for c in candidates:
        rows.append({
            "Rank": c.rank,
            "Symbol": c.symbol,
            "Score": c.score,
            "Price": c.price,
            "Off high %": c.pct_off_52w_high,
            "Trend": (c.pillar("trend").score if c.pillar("trend") else None),
            "Entry": (c.pillar("entry").score if c.pillar("entry") else None),
            "Quality": (c.pillar("quality").score if c.pillar("quality") else None),
            "Weekly stoch": c.weekly_k,
            "Volatility %": c.realized_vol_pct,
            "Market cap": c.market_cap,
        })
    return pd.DataFrame(rows)


def leaps_columns():
    return {
        "Rank": st.column_config.NumberColumn("#", width="small"),
        "Score": st.column_config.ProgressColumn("Setup score", min_value=0, max_value=100,
                                                 format="%.0f"),
        "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
        "Off high %": st.column_config.NumberColumn("Off 52w high", format="%.1f%%"),
        "Trend": st.column_config.NumberColumn("Trend", format="%.0f"),
        "Entry": st.column_config.NumberColumn("Entry", format="%.0f"),
        "Quality": st.column_config.NumberColumn("Quality", format="%.0f"),
        "Weekly stoch": st.column_config.NumberColumn("Wk stoch", format="%.0f"),
        "Volatility %": st.column_config.NumberColumn("Volatility", format="%.0f%%"),
        "Market cap": st.column_config.NumberColumn("Market cap", format="compact"),
    }


def ladder_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Strike": r["strike"],
        "Delta": r["delta"],
        "Cost": r["cost"],
        "Cost % of stock": r["cost_pct_of_spot"],
        "Time cost /yr": r["extrinsic_ann_pct"],
        "Breakeven": r["breakeven"],
        "Needs": r["required_move_pct"],
        "Leverage": r["leverage"],
        "Zero below": r["total_loss_drop_pct"],
        "Open interest": r["open_interest"],
    } for r in rows])


def ladder_columns():
    return {
        "Strike": st.column_config.NumberColumn("Strike", format="$%.0f"),
        "Delta": st.column_config.NumberColumn("Delta", format="%.2f"),
        "Cost": st.column_config.NumberColumn("Cost", format="$%.0f"),
        "Cost % of stock": st.column_config.NumberColumn("Cost vs stock", format="%.1f%%"),
        "Time cost /yr": st.column_config.NumberColumn("Time cost/yr", format="%.1f%%"),
        "Breakeven": st.column_config.NumberColumn("Breakeven", format="$%.2f"),
        "Needs": st.column_config.NumberColumn("Needs", format="%+.1f%%"),
        "Leverage": st.column_config.NumberColumn("Leverage", format="%.1fx"),
        "Zero below": st.column_config.NumberColumn("Worthless at", format="%.0f%%"),
        "Open interest": st.column_config.NumberColumn("Open int", format="%d"),
    }


# ------------------------------------------------------------- seasonality
_HEAT_STEPS = [(-8, "#C02A1B"), (-4, "#D9695C"), (-1.5, "#EFB3AB"), (0, "#F6DCD9"),
               (1.5, "#DDEEE4"), (4, "#A9D6BE"), (8, "#5FB98C"), (999, "#0B7A54")]


def _heat_colour(value: Optional[float]) -> str:
    if value is None:
        return "#FFFFFF"
    for threshold, colour in _HEAT_STEPS:
        if value <= threshold:
            return colour
    return "#0B7A54"


def _heat_text(value: Optional[float]) -> str:
    if value is None:
        return theme.SECONDARY
    return "#FFFFFF" if value <= -8 or value >= 8 else theme.INK


def render_seasonality(s) -> None:
    if not s.months:
        theme.note(s.summary)
        return

    now, nxt = s.this_month, s.next_month
    tiles = []
    if now and now.avg_pct is not None:
        tiles.append(stat_tile(
            f"Average {now.name} return", f"{now.avg_pct:+.2f}%",
            f"{now.rank} of 12 months · {now.hit_rate:.0f}% green",
            theme.GREEN if now.avg_pct > 0 else theme.RED))
    if s.best_month:
        tiles.append(stat_tile("Strongest month",
                               f"{s.best_month.short} {s.best_month.avg_pct:+.2f}%",
                               f"{s.best_month.hit_rate:.0f}% green", theme.GREEN))
    if s.worst_month:
        tiles.append(stat_tile("Weakest month",
                               f"{s.worst_month.short} {s.worst_month.avg_pct:+.2f}%",
                               f"{s.worst_month.hit_rate:.0f}% green", theme.RED))
    if nxt and nxt.avg_pct is not None:
        tiles.append(stat_tile(f"Next up - {nxt.name}", f"{nxt.avg_pct:+.2f}%",
                               f"{nxt.hit_rate:.0f}% green"))
    stat_row(tiles)

    st.markdown("**Average return by calendar month**")
    chart = pd.DataFrame({
        "Month": [m.short for m in s.months],
        "Average %": [m.avg_pct if m.avg_pct is not None else 0.0 for m in s.months],
    }).set_index("Month")
    st.bar_chart(chart, color=theme.ACCENT, height=240)

    st.markdown("**Month by month, year by year**")
    theme.note(f"Total returns with dividends reinvested, {s.first_year} to {s.last_year}. "
               "Green is a positive month, red negative.")
    st.markdown(_heatmap_html(s), unsafe_allow_html=True)

    st.write("")
    with st.expander("What each month has actually done"):
        for m in s.months:
            if m.avg_pct is None:
                continue
            icon = _STATUS_ICON.get(m.status, "")
            st.markdown(
                f"{icon} **{m.name}** - average {m.avg_pct:+.2f}%, green "
                f"{m.hit_rate:.0f}% of {m.years} years "
                f"(best {m.best_pct:+.1f}%, worst {m.worst_pct:+.1f}%). {_esc(m.read)}")
    theme.note(s.summary)


def _heatmap_html(s) -> str:
    from src.research.seasonality import MONTH_SHORT

    cell = ("padding:5px 4px;text-align:center;font-size:0.78rem;border-radius:5px;"
            "min-width:44px;")
    head = "".join(f"<th style='{cell}color:{theme.SECONDARY};font-weight:600;'>{m}</th>"
                   for m in MONTH_SHORT)
    html = [f"<div style='overflow-x:auto;'><table style='border-collapse:separate;"
            f"border-spacing:3px;width:100%;'><thead><tr>"
            f"<th style='{cell}color:{theme.SECONDARY};'></th>{head}"
            f"<th style='{cell}color:{theme.SECONDARY};font-weight:600;'>Year</th>"
            f"</tr></thead><tbody>"]

    def row(label: str, values: list[Optional[float]], total: Optional[float],
            bold: bool = False) -> str:
        weight = "700" if bold else "500"
        cells = "".join(
            f"<td style='{cell}background:{_heat_colour(v)};color:{_heat_text(v)};"
            f"font-weight:{weight};'>" + (f"{v:+.1f}" if v is not None else "-") + "</td>"
            for v in values)
        total_cell = (f"<td style='{cell}background:{_heat_colour(total)};"
                      f"color:{_heat_text(total)};font-weight:700;'>"
                      + (f"{total:+.1f}" if total is not None else "-") + "</td>")
        return (f"<tr><td style='{cell}text-align:left;font-weight:{weight};"
                f"color:{theme.INK};white-space:nowrap;'>{label}</td>{cells}"
                f"{total_cell}</tr>")

    html.append(row("Average", [m.avg_pct for m in s.months], None, bold=True))
    html.append(
        "<tr><td style='" + cell + f"text-align:left;font-weight:700;color:{theme.INK};'>"
        "Win rate</td>" + "".join(
            f"<td style='{cell}color:{theme.SECONDARY};font-weight:600;'>"
            + (f"{m.hit_rate:.0f}%" if m.hit_rate is not None else "-") + "</td>"
            for m in s.months) + f"<td style='{cell}'></td></tr>")
    for r in s.rows:
        html.append(row(str(r.year), r.returns, r.full_year_pct))
    html.append("</tbody></table></div>")
    return "".join(html)


# ------------------------------------------------------------------ analyst
def render_analyst(v) -> None:
    tone = {"good": theme.GREEN, "watch": theme.RED}.get(v.status, theme.SECONDARY)
    tiles = [stat_tile("Consensus", v.consensus,
                       f"{v.total_analysts} analysts" if v.total_analysts else "", tone)]
    if v.target_mean:
        tiles.append(stat_tile("Average target", _money(v.target_mean),
                               f"{v.upside_pct:+.1f}% from here"
                               if v.upside_pct is not None else ""))
    if v.target_low and v.target_high:
        tiles.append(stat_tile("Range", f"{_money(v.target_low, 0)} - "
                                        f"{_money(v.target_high, 0)}",
                               f"{v.dispersion_pct:.0f}% apart"
                               if v.dispersion_pct else ""))
    if v.base_rate_pct is not None:
        rate_tone = theme.GREEN if v.base_rate_pct >= 50 else (
            theme.AMBER if v.base_rate_pct >= 30 else theme.RED)
        tiles.append(stat_tile("It has managed that", f"{v.base_rate_pct:.0f}% of years",
                               f"over {v.base_rate_years:.0f} years", rate_tone))
    stat_row(tiles)

    if v.total_analysts:
        st.markdown("**How the ratings split**")
        for b in v.buckets:
            if not b.count:
                continue
            colour = {"Strong buy": theme.GREEN, "Buy": theme.ACCENT_BRIGHT,
                      "Hold": theme.SECONDARY, "Sell": theme.AMBER,
                      "Strong sell": theme.RED}.get(b.label, theme.SECONDARY)
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:10px;margin:4px 0;'>"
                f"<div style='width:96px;font-size:0.9rem;'>{b.label}</div>"
                f"<div style='flex:1;background:{theme.BORDER};border-radius:99px;"
                f"height:9px;'><div style='width:{b.pct:.0f}%;background:{colour};"
                f"height:9px;border-radius:99px;'></div></div>"
                f"<div style='width:64px;text-align:right;font-size:0.85rem;'>"
                f"{b.count} ({b.pct:.0f}%)</div></div>", unsafe_allow_html=True)

    if v.agreement:
        st.write("")
        theme.note(v.agreement)
    if v.reality_check:
        with st.container(border=True):
            st.markdown("**The reality check**")
            theme.note(v.reality_check)
    theme.note(v.summary)


# --------------------------------------------------------- instant analyzer
def render_criteria_result(r) -> None:
    tone = {"pass": theme.GREEN, "near": theme.AMBER}.get(r.verdict, theme.RED)
    label = {"pass": "Passes every rule", "near": "Only just misses"}.get(
        r.verdict, "Fails your rules")
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:14px;margin:6px 0 12px;'>"
        f"<div style='background:{tone};color:#fff;border-radius:12px;padding:8px 16px;"
        f"font-weight:750;font-size:1.05rem;'>{r.passed_count}/{r.measured_count}</div>"
        f"<div><div style='font-weight:700;font-size:1.05rem;'>{_esc(r.symbol)} - "
        f"{label}</div>"
        f"<div style='color:{theme.SECONDARY};font-size:0.88rem;'>"
        f"{r.score:.0f}% of your checkable rules</div></div></div>",
        unsafe_allow_html=True)

    for rule in r.rules:
        if not rule.measured:
            icon, colour = "⚪", theme.SECONDARY
        elif rule.passed:
            icon, colour = "🟢", theme.GREEN
        elif rule.near_miss:
            icon, colour = "🟡", theme.AMBER
        else:
            icon, colour = "🔴", theme.RED
        st.markdown(
            f"<div style='margin:5px 0;'>{icon} <b>{_esc(rule.label)}</b> "
            f"<span style='color:{colour};'>{_esc(rule.read)}</span></div>",
            unsafe_allow_html=True)
    st.write("")
    theme.note(r.summary)


def screen_frame(results: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Symbol": r.symbol,
        "Name": r.name,
        "Passed": f"{r.passed_count}/{r.measured_count}",
        "Score": r.score,
        "Verdict": {"pass": "Passes", "near": "Just misses"}.get(r.verdict, "Fails"),
    } for r in results])


# ------------------------------------------------------------- fair value
def render_valuation(r) -> None:
    tone = {"buy": theme.GREEN, "fair": theme.AMBER}.get(r.verdict, theme.RED)
    tiles = [
        stat_tile("Pay no more than", _money(r.buy_below), "to hit your return", tone),
    ]
    if r.current_price:
        tiles.append(stat_tile("Trades at", _money(r.current_price),
                               f"{r.discount_pct:+.0f}% versus fair"
                               if r.discount_pct is not None else ""))
    if r.implied_return_pct is not None:
        tiles.append(stat_tile("At today's price you earn",
                               f"{r.implied_return_pct:.1f}%/yr", "on your assumptions"))
    if r.implied_growth_pct is not None:
        tiles.append(stat_tile("Price already assumes",
                               f"{r.implied_growth_pct:.1f}%/yr", "earnings growth"))
    tiles.append(stat_tile("Future value", _money(r.future_price),
                           f"EPS {_money(r.future_eps)} x {r.inputs.exit_pe:.0f}"))
    stat_row(tiles)

    with st.container(border=True):
        st.markdown("**The arithmetic, step by step**")
        for line in r.reads:
            st.markdown(f"- {_esc(line)}")
    theme.note(r.summary)


def sensitivity_frame(grid: dict) -> pd.DataFrame:
    rows = {}
    for row in grid["rows"]:
        rows[f"{row['growth_pct']:.0f}% growth"] = {
            f"{c['exit_pe']:.0f}x P/E": c["buy_below"] for c in row["cells"]
        }
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------- options
def render_options_view(v) -> None:
    rich_tone = {"Rich": theme.RED, "Cheap": theme.GREEN}.get(v.richness, theme.SECONDARY)
    tiles = [
        stat_tile("Implied volatility",
                  f"{v.atm_iv_pct:.1f}%" if v.atm_iv_pct else "n/a",
                  f"at {v.selected_dte} days"),
        stat_tile("Actually moves",
                  f"{v.realized_vol_pct:.1f}%" if v.realized_vol_pct else "n/a",
                  "realized volatility"),
        stat_tile("Options are", v.richness,
                  f"{v.iv_premium_pct:+.1f} points versus realized"
                  if v.iv_premium_pct is not None else "", rich_tone),
        stat_tile("Sentiment", v.sentiment,
                  f"{v.put_call_volume:.2f} puts per call"
                  if v.put_call_volume is not None else
                  (f"{v.put_call_oi:.2f} puts per call open"
                   if v.put_call_oi is not None else "")),
    ]
    stat_row(tiles)
    theme.note(v.richness_read)
    if v.sentiment_read and v.sentiment != "n/a":
        theme.note(v.sentiment_read)

    if v.expirations:
        st.markdown("**Expected move by expiration**")
        theme.note("The range options are pricing, and how often this stock has actually "
                   "exceeded it over the same stretch. When the historical figure is much "
                   "lower than you would expect, the options are asking a lot.")
        st.dataframe(expected_move_frame(v.expirations), hide_index=True,
                     column_config=expected_move_columns(), width="stretch")
        for exp in v.expirations:
            if exp.dte == v.selected_dte and exp.read:
                theme.note(exp.read)

    if v.rows:
        st.markdown(f"**The chain** - {v.selected_expiration or 'nearest expiration'}")
        st.dataframe(chain_frame(v.rows), hide_index=True,
                     column_config=chain_columns(), width="stretch")


def expected_move_frame(expirations: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Expiration": e.expiration,
        "Days": e.dte,
        "IV": e.atm_iv_pct,
        "Expected move": e.expected_move_pct,
        "Low": e.lower,
        "High": e.upper,
        "Beat it historically": e.historical_beat_pct,
    } for e in expirations])


def expected_move_columns():
    return {
        "Days": st.column_config.NumberColumn("Days", format="%d"),
        "IV": st.column_config.NumberColumn("Implied vol", format="%.1f%%"),
        "Expected move": st.column_config.NumberColumn("Expected move", format="±%.1f%%"),
        "Low": st.column_config.NumberColumn("Low", format="$%.2f"),
        "High": st.column_config.NumberColumn("High", format="$%.2f"),
        "Beat it historically": st.column_config.NumberColumn("Exceeded historically",
                                                              format="%.0f%%"),
    }


def chain_frame(rows: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Call bid": r.call_bid, "Call ask": r.call_ask, "Call delta": r.call_delta,
        "Call IV": r.call_iv_pct, "Call OI": r.call_oi,
        "Strike": r.strike,
        "Put bid": r.put_bid, "Put ask": r.put_ask, "Put delta": r.put_delta,
        "Put IV": r.put_iv_pct, "Put OI": r.put_oi,
    } for r in rows])


def chain_columns():
    money = lambda label: st.column_config.NumberColumn(label, format="$%.2f")
    return {
        "Call bid": money("Call bid"), "Call ask": money("Call ask"),
        "Call delta": st.column_config.NumberColumn("Call Δ", format="%.2f"),
        "Call IV": st.column_config.NumberColumn("Call IV", format="%.0f%%"),
        "Call OI": st.column_config.NumberColumn("Call OI", format="%d"),
        "Strike": st.column_config.NumberColumn("Strike", format="$%.2f"),
        "Put bid": money("Put bid"), "Put ask": money("Put ask"),
        "Put delta": st.column_config.NumberColumn("Put Δ", format="%.2f"),
        "Put IV": st.column_config.NumberColumn("Put IV", format="%.0f%%"),
        "Put OI": st.column_config.NumberColumn("Put OI", format="%d"),
    }
