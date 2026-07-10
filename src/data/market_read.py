"""The Market tab's read logic: your VIX comfort zone, the day's verdict, the
expected move, premium richness, and the sector pulse.

Pure functions only - no Streamlit, no network - so every rule here is unit
tested. The threshold numbers live in config/settings.yaml (market_read:),
not in code, matching the rest of your rules.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

from src.data import premium_finder

# Used when config/settings.yaml has no market_read block - these reproduce
# the app's original behavior exactly (verdict amber at 20, red at 28).
DEFAULTS: dict[str, float] = {
    "vix_zone_low": 13.0,
    "vix_zone_high": 25.0,
    "vix_caution": 20.0,
    "vix_stop": 28.0,
}


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


def classify_vix_zone(vix: Optional[float], low: float,
                      high: float) -> tuple[str, str, str]:
    """Where today's VIX sits against YOUR comfort zone.

    Returns (zone, chip_text, tone); zone is "below" / "inside" / "above" /
    "unknown", and the boundaries themselves count as inside.
    """
    if vix is None:
        return ("unknown", "VIX unavailable right now - check it in thinkorswim", "amber")
    if vix < low:
        return ("below",
                f"VIX {vix:.1f} - below your comfort zone ({low:g}-{high:g}): "
                "calm, but premiums run thin", "amber")
    if vix > high:
        return ("above",
                f"VIX {vix:.1f} - above your comfort zone ({low:g}-{high:g}): "
                "rich premiums, big swings", "red")
    return ("inside",
            f"VIX {vix:.1f} - inside your comfort zone ({low:g}-{high:g})", "green")


def expected_move(price: Optional[float], atm_iv: Optional[float],
                  dte: Optional[int]) -> Optional[tuple[float, float]]:
    """(points, pct): how far options price the underlying to typically move by
    an expiration dte days away. The standard one-standard-deviation estimate:
    price * IV * sqrt(days / 365) - real moves land inside it about 2 times in 3.
    """
    if not price or price <= 0 or not atm_iv or atm_iv <= 0 or not dte or dte <= 0:
        return None
    points = price * atm_iv * math.sqrt(dte / 365)
    return (points, points / price * 100)


def richness_read(atm_iv: Optional[float],
                  hv: Optional[float]) -> tuple[str, Optional[float]]:
    """(Rich/Fair/Thin/n-a, iv_hv ratio) - what options PAY (implied volatility)
    vs how much the underlying actually MOVED (realized volatility).

    Delegates to premium_finder's thresholds so the Market and Premium tabs
    can never disagree about what "Rich" means.
    """
    iv_hv = round(atm_iv / hv, 2) if (atm_iv and hv) else None
    return premium_finder._richness(iv_hv, atm_iv), iv_hv


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
def demo_vix_frame(today: Optional[dt.date] = None):
    """A deterministic year of fake VIX closes for demo mode - shaped exactly
    like yfinance's price frame (datetime index, one Close column). Spans
    roughly 10.5-25.5 so the comfort-zone band visibly matters, and ends at
    13.5 to match the demo VIX tile."""
    import pandas as pd

    end = today or dt.date.today()
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=252)
    closes = [18 + 6 * math.sin(i / 23) + 1.5 * math.sin(i / 6)
              for i in range(len(dates))]
    closes[-1] = 13.5
    return pd.DataFrame({"Close": closes}, index=dates)


def demo_pulse_history(symbols: list[str]) -> dict[str, tuple[list[float], list[float]]]:
    """Deterministic fake batch_history for demo mode: every symbol gets two
    closes implying a small move in the -1.5%..+1.5% range."""
    out: dict[str, tuple[list[float], list[float]]] = {}
    for sym in symbols:
        s = sym.upper()
        pct = ((sum(ord(ch) for ch in s) * 7) % 13 - 6) / 4
        out[s] = ([100.0, 100.0 * (1 + pct / 100)], [1_000_000.0, 1_000_000.0])
    return out
