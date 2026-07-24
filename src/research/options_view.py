"""Options Data - what the option market is actually saying about a stock.

Four readings, in plain language:

  Implied volatility  - how big a move options are priced for. On its own the
                        number means nothing; against what the stock ACTUALLY
                        does, it tells you whether options are dear or cheap.
  Expected move       - that volatility turned into dollars: the range the
                        market is pricing between now and each expiration.
  Put/call sentiment  - whether the money is leaning protective or bullish.
  The chain itself    - strikes, prices, deltas, laid out readably.

The one twist worth having, which most chain viewers skip: we check the
expected move against the stock's own history. If options are pricing a 9%
move over the next month and this stock has only exceeded 9% in one month out
of five historically, the options are expensive - and that is worth knowing
whether you are buying them or selling them.
"""

from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, Field

from src.data.chain import OptionChain
from src.engine.models import OptionType
from src.research.leaps import TRADING_DAYS_YEAR, realized_vol


class ExpirationView(BaseModel):
    expiration: str
    dte: int
    atm_iv_pct: Optional[float] = None
    expected_move_pct: Optional[float] = None      # one standard deviation
    expected_move_dollars: Optional[float] = None
    upper: Optional[float] = None
    lower: Optional[float] = None
    historical_beat_pct: Optional[float] = None    # how often it moved MORE than this
    call_oi: int = 0
    put_oi: int = 0
    read: str = ""


class ChainRow(BaseModel):
    strike: float
    call_bid: Optional[float] = None
    call_ask: Optional[float] = None
    call_mid: Optional[float] = None
    call_delta: Optional[float] = None
    call_iv_pct: Optional[float] = None
    call_oi: int = 0
    put_bid: Optional[float] = None
    put_ask: Optional[float] = None
    put_mid: Optional[float] = None
    put_delta: Optional[float] = None
    put_iv_pct: Optional[float] = None
    put_oi: int = 0
    moneyness: str = ""        # "ITM call" / "ATM" / "OTM call"


class OptionsView(BaseModel):
    symbol: str
    price: float = 0.0
    atm_iv_pct: Optional[float] = None
    realized_vol_pct: Optional[float] = None
    iv_premium_pct: Optional[float] = None     # implied minus realized
    richness: str = "n/a"                      # "Rich" | "Fair" | "Cheap"
    richness_read: str = ""

    put_call_volume: Optional[float] = None
    put_call_oi: Optional[float] = None
    sentiment: str = "n/a"
    sentiment_read: str = ""

    expirations: list[ExpirationView] = Field(default_factory=list)
    rows: list[ChainRow] = Field(default_factory=list)
    selected_expiration: str = ""
    selected_dte: int = 0

    summary: str = ""


def _atm_iv(chain: OptionChain, dte: int, spot: float) -> Optional[float]:
    """Average the implied volatility of the call and put nearest the money."""
    ivs = []
    for kind in (OptionType.CALL, OptionType.PUT):
        options = [c for c in chain.by(kind, dte) if c.iv and c.iv > 0]
        if options:
            nearest = min(options, key=lambda c: abs(c.strike - spot))
            ivs.append(nearest.iv)
    return sum(ivs) / len(ivs) if ivs else None


def historical_move_beat(closes: list[float], horizon_days: int,
                         move_pct: float) -> Optional[float]:
    """How often did this stock move MORE than `move_pct` (either way) over a
    window of this length? The honest yardstick for an expected move."""
    span = max(1, int(round(horizon_days * TRADING_DAYS_YEAR / 365)))
    if len(closes) < span + 30 or move_pct <= 0:
        return None
    beats = total = 0
    for i in range(len(closes) - span):
        if closes[i] > 0:
            change = abs(closes[i + span] / closes[i] - 1) * 100
            total += 1
            beats += change > move_pct
    return round(100.0 * beats / total, 1) if total else None


def _richness(iv_pct: Optional[float], hv_pct: Optional[float]) -> tuple[str, str]:
    if not iv_pct or not hv_pct:
        return "n/a", ("Not enough data to say whether these options are dear or cheap.")
    ratio = iv_pct / hv_pct
    if ratio >= 1.25:
        return "Rich", (
            f"Options are pricing {iv_pct:.0f}% volatility while the stock has actually "
            f"been moving {hv_pct:.0f}% ({ratio:.2f}x). They are expensive - good for "
            "selling premium, a headwind if you are buying.")
    if ratio >= 1.05:
        return "Fair", (
            f"Options price {iv_pct:.0f}% against {hv_pct:.0f}% realized ({ratio:.2f}x) - "
            "a normal, slight premium. Sellers have a small edge, as usual.")
    if ratio >= 0.9:
        return "Fair", (
            f"Options price {iv_pct:.0f}% against {hv_pct:.0f}% realized ({ratio:.2f}x) - "
            "about right for what the stock has been doing.")
    return "Cheap", (
        f"Options price only {iv_pct:.0f}% while the stock has been moving {hv_pct:.0f}% "
        f"({ratio:.2f}x). They are cheap - a good backdrop for buying options, and thin "
        "reward for selling them.")


def _sentiment(pc_volume: Optional[float], pc_oi: Optional[float]) -> tuple[str, str]:
    ratio = pc_volume if pc_volume is not None else pc_oi
    if ratio is None:
        return "n/a", "No volume or open interest to read sentiment from."
    if ratio >= 1.3:
        return "Defensive", (
            f"{ratio:.2f} puts for every call. Money is leaning protective - either "
            "hedging or betting on a fall.")
    if ratio >= 0.9:
        return "Balanced", f"{ratio:.2f} puts per call - no strong lean either way."
    if ratio >= 0.6:
        return "Bullish", (
            f"Only {ratio:.2f} puts per call - positioning leans bullish.")
    return "Very bullish", (
        f"Just {ratio:.2f} puts per call - heavily call-side. Crowded bullishness can "
        "itself be a warning.")


def build(chain: OptionChain, closes: Optional[list[float]] = None,
          target_dte: Optional[int] = None) -> OptionsView:
    closes = closes or []
    spot = chain.underlying_price
    view = OptionsView(symbol=chain.underlying.upper(), price=spot)

    rv = realized_vol(closes) if closes else None
    view.realized_vol_pct = round(rv * 100, 1) if rv else None

    dtes = chain.dtes()
    if not dtes:
        view.summary = f"No option data came back for {view.symbol}."
        return view

    chosen = (chain.nearest_dte(target_dte) if target_dte else None) or dtes[0]
    view.selected_dte = chosen

    for dte in dtes:
        rows = [c for c in chain.contracts if c.dte == dte]
        if not rows:
            continue
        exp = ExpirationView(expiration=rows[0].expiration, dte=dte)
        exp.call_oi = sum(c.open_interest for c in rows if c.option_type == OptionType.CALL)
        exp.put_oi = sum(c.open_interest for c in rows if c.option_type == OptionType.PUT)
        iv = _atm_iv(chain, dte, spot)
        if iv:
            exp.atm_iv_pct = round(iv * 100, 1)
            # One standard deviation over the life of the option.
            move = iv * math.sqrt(max(dte, 1) / 365.0)
            exp.expected_move_pct = round(move * 100, 1)
            exp.expected_move_dollars = round(spot * move, 2)
            exp.upper = round(spot * (1 + move), 2)
            exp.lower = round(spot * (1 - move), 2)
            if closes:
                exp.historical_beat_pct = historical_move_beat(closes, dte,
                                                               exp.expected_move_pct)
            exp.read = _expiration_read(exp)
        view.expirations.append(exp)
        if dte == chosen:
            view.atm_iv_pct = exp.atm_iv_pct
            view.selected_expiration = exp.expiration

    view.richness, view.richness_read = _richness(view.atm_iv_pct, view.realized_vol_pct)
    if view.atm_iv_pct and view.realized_vol_pct:
        view.iv_premium_pct = round(view.atm_iv_pct - view.realized_vol_pct, 1)

    calls = [c for c in chain.contracts if c.option_type == OptionType.CALL]
    puts = [c for c in chain.contracts if c.option_type == OptionType.PUT]
    call_vol, put_vol = sum(c.volume for c in calls), sum(c.volume for c in puts)
    call_oi, put_oi = sum(c.open_interest for c in calls), sum(c.open_interest for c in puts)
    if call_vol > 0:
        view.put_call_volume = round(put_vol / call_vol, 2)
    if call_oi > 0:
        view.put_call_oi = round(put_oi / call_oi, 2)
    view.sentiment, view.sentiment_read = _sentiment(view.put_call_volume, view.put_call_oi)

    view.rows = chain_rows(chain, chosen, spot)
    view.summary = _summary(view)
    return view


def _expiration_read(exp: ExpirationView) -> str:
    base = (f"Options are pricing a move of about {exp.expected_move_pct:.1f}% "
            f"(${exp.expected_move_dollars:,.2f}) either way by {exp.expiration}, "
            f"so roughly ${exp.lower:,.2f} to ${exp.upper:,.2f}.")
    if exp.historical_beat_pct is None:
        return base
    beat = exp.historical_beat_pct
    if beat >= 40:
        judge = ("Historically it has exceeded that in {:.0f}% of stretches this long, "
                 "so the pricing looks reasonable or even light.").format(beat)
    elif beat >= 25:
        judge = ("Historically it exceeded that in {:.0f}% of stretches this long - "
                 "about what you would expect.").format(beat)
    else:
        judge = ("Historically it only exceeded that in {:.0f}% of stretches this long, "
                 "which means the options are asking a lot.").format(beat)
    return base + " " + judge


def chain_rows(chain: OptionChain, dte: int, spot: float,
               width_pct: float = 25.0) -> list[ChainRow]:
    """Calls and puts side by side at one expiration, near the money only."""
    calls = {c.strike: c for c in chain.by(OptionType.CALL, dte)}
    puts = {c.strike: c for c in chain.by(OptionType.PUT, dte)}
    strikes = sorted(set(calls) | set(puts))
    lo, hi = spot * (1 - width_pct / 100), spot * (1 + width_pct / 100)

    rows = []
    for strike in strikes:
        if spot > 0 and not (lo <= strike <= hi):
            continue
        call, put = calls.get(strike), puts.get(strike)
        row = ChainRow(strike=strike)
        if call:
            row.call_bid, row.call_ask, row.call_mid = call.bid, call.ask, call.mid
            row.call_delta = round(call.delta, 3) if call.delta else None
            row.call_iv_pct = round(call.iv * 100, 1) if call.iv else None
            row.call_oi = call.open_interest
        if put:
            row.put_bid, row.put_ask, row.put_mid = put.bid, put.ask, put.mid
            row.put_delta = round(put.delta, 3) if put.delta else None
            row.put_iv_pct = round(put.iv * 100, 1) if put.iv else None
            row.put_oi = put.open_interest
        if spot > 0:
            gap = (strike - spot) / spot * 100
            row.moneyness = ("ATM" if abs(gap) <= 1.5 else
                             "ITM call" if gap < 0 else "OTM call")
        rows.append(row)
    return rows


def _summary(v: OptionsView) -> str:
    parts = []
    if v.atm_iv_pct:
        parts.append(f"{v.symbol} options are pricing {v.atm_iv_pct:.0f}% volatility at "
                     f"{v.selected_dte} days.")
    if v.richness_read:
        parts.append(v.richness_read)
    if v.sentiment_read and v.sentiment != "n/a":
        parts.append(v.sentiment_read)
    return " ".join(parts) or f"Limited option data for {v.symbol}."
