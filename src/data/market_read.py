"""The Market tab's read logic: the day's verdict, the plain-English brief, and
the sector pulse.

Pure functions only - no Streamlit, no network - so every rule here is unit
tested. The VIX threshold numbers live in config/settings.yaml (market_read:),
not in code, matching the rest of your rules.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data.market_context import daily_sentiment

# Used when config/settings.yaml has no market_read block - these reproduce
# the app's original behavior exactly (verdict amber at 20, red at 28).
DEFAULTS: dict[str, float] = {
    "vix_zone_low": 13.0,
    "vix_zone_high": 25.0,
    "vix_caution": 20.0,
    "vix_stop": 28.0,
}

# The scheduled events big enough to headline the brief and drive the verdict.
BIG_EVENT_KINDS = {"fomc", "cpi", "pce", "gdp", "jobs"}


def read_cfg(settings: Optional[dict[str, Any]]) -> dict[str, float]:
    """The market_read thresholds from settings, with defaults filled in."""
    block = (settings or {}).get("market_read") or {}
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if block.get(key) is not None:
            out[key] = float(block[key])
    return out


def days_phrase(n) -> str:
    if n is None:
        return ""
    if n <= 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"in {n} days"


def trading_verdict(ctx, events, cfg: dict[str, float]) -> tuple[str, str, str]:
    """(headline, tone, why) for the day - the Market tab's verdict card.

    Also used by the Picks tab. Thresholds come from cfg (see read_cfg).
    """
    vix = ctx.vix
    big_soon = next((e for e in events
                     if e.kind in ("fomc", "jobs") and e.days_away is not None
                     and e.days_away <= 2), None)
    if vix is not None and vix >= cfg["vix_stop"]:
        return ("Sit this one out", "red",
                f"Fear is high (VIX {vix:.0f}). Big, fast swings can blow right through your "
                "strikes. Premium sellers do best when things are calm - wait for the VIX to "
                "settle back down before selling new premium.")
    if big_soon is not None:
        return ("Trade carefully today", "amber",
                f"{big_soon.label} is {days_phrase(big_soon.days_away)}. A surprise there can "
                "move the whole market. If you do trade, keep size small and deltas low - or "
                "wait until it has passed.")
    if vix is not None and vix >= cfg["vix_caution"]:
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


# ------------------------------------------------------------------ today's brief
def next_big_event(events):
    """The soonest market-moving scheduled event (Fed, CPI, jobs, PCE, GDP),
    or None. `events` is already sorted soonest-first by upcoming_events."""
    return next((e for e in events if e.kind in BIG_EVENT_KINDS), None)


_TREND_WORDS = {
    "up": "leaning up (its 20-day average is above its 50-day)",
    "down": "leaning down (its 20-day average is below its 50-day)",
    "sideways": "moving sideways",
    "unknown": "hard to read right now",
}


def build_brief(index_changes: list[Optional[float]], vix: Optional[float],
                trend: str, pulse_rows: list[dict], next_event,
                cfg: dict[str, float], underlying: str = "SPX") -> str:
    """A few plain-English sentences reading the market today - built entirely
    from numbers the tab already has, so it costs no extra fetch.

    Weaves together the index move + mood, the trend, today's sector leader and
    laggard, the next big scheduled event, and what it means for selling premium.
    Returns a **-bold-friendly string (theme.note renders the bold).
    """
    parts: list[str] = []

    # 1. The day's move and mood (reuses the same read as the chips above).
    _label, note = daily_sentiment(index_changes, vix)
    if note:
        parts.append(note)

    # 2. Trend.
    parts.append(f"{underlying} is {_TREND_WORDS.get(trend, _TREND_WORDS['unknown'])}.")

    # 3. Today's sector leader and laggard, from the pulse.
    rows = [r for r in pulse_rows if r.get("change_pct") is not None]
    if len(rows) >= 2:
        top = max(rows, key=lambda r: r["change_pct"])
        bot = min(rows, key=lambda r: r["change_pct"])
        if top["symbol"] != bot["symbol"]:
            parts.append(
                f"**{top['label']}** led today ({top['change_pct']:+.2f}%) and "
                f"**{bot['label']}** lagged ({bot['change_pct']:+.2f}%).")

    # 4. The next big scheduled event.
    if next_event is not None:
        parts.append(f"Next big event: **{next_event.label}** "
                     f"{days_phrase(next_event.days_away)}.")

    # 5. What it means for premium selling (decision support, not a directive).
    parts.append(_takeaway(vix, next_event, cfg))

    return " ".join(p for p in parts if p)


def _takeaway(vix: Optional[float], next_event, cfg: dict[str, float]) -> str:
    if next_event is not None and getattr(next_event, "in_window", False):
        return (f"For your premium selling: **{next_event.label}** lands inside your "
                "trade window, so your plan says be careful opening new trades right "
                "before it.")
    if vix is not None and vix >= cfg["vix_stop"]:
        return ("For your premium selling: fear is high, and your plan leans toward "
                "waiting for calmer conditions.")
    if vix is not None and vix >= cfg["vix_caution"]:
        return ("For your premium selling: premiums are richer than usual, but so are "
                "the swings - smaller size and lower delta fit here.")
    if vix is not None and vix < cfg["vix_zone_low"]:
        return ("For your premium selling: it is calm, but premiums are thin down here - "
                "worth checking whether a trade pays enough to bother.")
    return ("For your premium selling: conditions look comfortable for your usual "
            "21-45 day trades.")


# ------------------------------------------------------------------ sector pulse
# Plain-English tile names (fallback: the ticker itself, so config additions
# still render).
PULSE_LABELS: dict[str, str] = {
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Small companies", "DIA": "Dow 30",
    "GLD": "Gold", "SLV": "Silver", "TLT": "Long bonds",
    "EEM": "Emerging markets", "EFA": "International",
    "XLF": "Banks", "XLE": "Energy", "XLK": "Tech", "XLV": "Healthcare",
    "SMH": "Chips",
}

GROUP_ORDER = ["Indexes", "Sectors", "Other assets"]
_GROUP_OF: dict[str, str] = {
    "SPY": "Indexes", "QQQ": "Indexes", "IWM": "Indexes", "DIA": "Indexes",
    "XLF": "Sectors", "XLE": "Sectors", "XLK": "Sectors", "XLV": "Sectors",
    "SMH": "Sectors",
}


def build_pulse_rows(history: dict[str, tuple[list[float], list[float]]],
                     symbols: list[str]) -> list[dict]:
    """Rows for the sector-pulse grid from batch_history-shaped data.

    Empty history (a throttled download) -> [] so the caller can show a retry
    note. A symbol missing from the batch still gets a row (change None) so
    partial data never hides the rest of the grid.
    """
    if not history:
        return []
    rows = []
    for sym in symbols:
        s = sym.upper()
        closes, _vols = history.get(s, ([], []))
        change = None
        if len(closes) >= 2 and closes[-2] > 0:
            change = (closes[-1] / closes[-2] - 1) * 100
        rows.append({
            "symbol": s,
            "label": PULSE_LABELS.get(s, s),
            "group": _GROUP_OF.get(s, "Other assets"),
            "change_pct": change,
        })
    return rows


# ------------------------------------------------------------------ demo data
def demo_pulse_history(symbols: list[str]) -> dict[str, tuple[list[float], list[float]]]:
    """Deterministic fake batch_history for demo mode: every symbol gets two
    closes implying a small move in the -1.5%..+1.5% range."""
    out: dict[str, tuple[list[float], list[float]]] = {}
    for sym in symbols:
        s = sym.upper()
        pct = ((sum(ord(ch) for ch in s) * 7) % 13 - 6) / 4
        out[s] = ([100.0, 100.0 * (1 + pct / 100)], [1_000_000.0, 1_000_000.0])
    return out
