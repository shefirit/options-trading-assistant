"""The credit-spread candidate check, graded against hand-worked numbers."""

from __future__ import annotations

import datetime as dt

import pytest

from src.data import barchart, vol_source
from src.engine import candidate as c


# ------------------------------------------------------------- price shapes
def _staircase(steps: int, up: bool = True, start: float = 100.0) -> list[float]:
    """A clean zig-zag: each leg overshoots the last, so pivots are unambiguous."""
    out = [start]
    level = start
    for i in range(steps):
        swing = 6.0 if up else -6.0
        for _ in range(4):
            level += swing / 4
            out.append(round(level, 2))
        for _ in range(4):
            level -= swing / 8
            out.append(round(level, 2))
    return out


def test_pivots_find_the_turns_and_ignore_noise():
    values = [1, 2, 3, 9, 3, 2, 1, 2, 3, 8, 3, 2, 1]
    highs = c.pivots(values, k=3, kind="high")
    assert [v for _, v in highs] == [9, 8]


def test_pivots_need_enough_bars():
    assert c.pivots([1, 2, 3], k=3, kind="high") == []


def test_swing_structure_reads_an_uptrend():
    closes = _staircase(6, up=True)
    direction, detail = c.swing_structure(closes, closes)
    assert direction == "up"
    assert "higher" in detail


def test_swing_structure_reads_a_downtrend():
    closes = _staircase(6, up=False)
    direction, _ = c.swing_structure(closes, closes)
    assert direction == "down"


def _range_bars(cycles: int, top: float = 110.0, bottom: float = 90.0):
    """A flat channel: same peak and same trough every cycle."""
    highs: list[float] = []
    lows: list[float] = []
    for _ in range(cycles):
        for v in (95.0, 100.0, top, 100.0, 95.0, 92.0, bottom, 92.0):
            highs.append(v)
            lows.append(v - 1.0)
    return highs, lows


def test_swing_structure_calls_a_flat_range_sideways_not_a_downtrend():
    # Peaks and troughs repeat at the same level. Without a tolerance band a
    # one-cent difference would score this a downtrend.
    highs, lows = _range_bars(6)
    direction, detail = c.swing_structure(highs, lows)
    assert direction == "sideways"
    assert "level" in detail


def test_swing_structure_calls_a_broadening_range_sideways():
    # Higher highs but lower lows - expanding, but not a trend either way.
    highs = [100, 104, 100, 96, 100, 108, 100, 96, 100, 112, 100, 96, 100,
             116, 100, 96, 100, 120, 100, 96, 100]
    lows = [95, 92, 95, 88, 95, 92, 95, 84, 95, 92, 95, 80, 95,
            92, 95, 76, 95, 92, 95, 72, 95]
    assert c.swing_structure(highs, lows)[0] == "sideways"


def test_swing_structure_says_unknown_when_it_cannot_tell():
    direction, why = c.swing_structure([1, 2], [1, 2])
    assert direction == "unknown"
    assert "Not enough" in why


# ------------------------------------------------------------------ volume
def test_volume_character_spots_a_quiet_pullback():
    closes = [100 + i for i in range(10)] + [109 - i for i in range(11)]
    # Up days heavy, down days light.
    volumes = [1000] * 10 + [400] * 11
    ratio = c.volume_character(closes, volumes, lookback=20)
    assert ratio is not None and ratio < 0.9


def test_volume_character_spots_heavy_selling():
    closes = [100 + i for i in range(10)] + [109 - i for i in range(11)]
    volumes = [400] * 10 + [1200] * 11
    ratio = c.volume_character(closes, volumes, lookback=20)
    assert ratio is not None and ratio > 1.15


def test_volume_character_refuses_a_one_sided_sample():
    closes = list(range(100, 130))          # every day an up day
    volumes = [1000] * 30
    assert c.volume_character(closes, volumes) is None


# --------------------------------------------------------- relative strength
def test_relative_strength_measures_the_gap_in_points():
    mine = [100.0] * 64
    mine[-1] = 120.0                         # +20% over the window
    theirs = [100.0] * 64
    theirs[-1] = 110.0                       # +10%
    assert c.relative_strength(mine, theirs, days=63) == pytest.approx(10.0, abs=0.1)


def test_relative_strength_needs_both_histories():
    assert c.relative_strength([1, 2, 3], [1, 2, 3], days=63) is None


# ------------------------------------------------------------------ ranking
def test_rank_in_range_is_the_iv_rank_formula():
    series = [10.0] * 100 + [30.0] + [20.0]   # low 10, high 30, latest 20
    assert c.rank_in_range(series) == pytest.approx(50.0)


def test_rank_in_range_handles_a_flat_series():
    assert c.rank_in_range([5.0] * 100) is None


def test_realized_vol_series_is_positive_and_aligned():
    closes = [100.0]
    for i in range(200):
        closes.append(closes[-1] * (1.01 if i % 2 else 0.99))
    series = c.realized_vol_series(closes, window=30)
    assert len(series) > 100
    assert all(v > 0 for v in series)


# ------------------------------------------------------------------- layers
def test_volatility_layer_rewards_expensive_options():
    lay = c.volatility_layer(72.0, "Barchart", iv=40.0, hv=25.0)
    assert lay.status == "good"
    assert lay.put_points == c.W_VOLATILITY
    assert lay.call_points == c.W_VOLATILITY
    assert "1.60x" in lay.read


def test_volatility_layer_penalises_cheap_options_on_both_sides():
    lay = c.volatility_layer(8.0, "Barchart")
    assert lay.status == "bad"
    assert lay.put_points < 0 and lay.call_points < 0


def test_volatility_layer_is_unknown_without_a_rank():
    lay = c.volatility_layer(None, "")
    assert lay.status == "unknown"
    assert not lay.known
    assert "Barchart" in lay.read


def test_market_layer_blocks_both_sides_past_the_stop():
    lay = c.market_layer(31.0, vix_stop=28.0)
    assert lay.blocks == [c.PUT, c.CALL]
    assert lay.status == "bad"


def test_market_layer_is_happy_inside_the_comfort_zone():
    lay = c.market_layer(17.0)
    assert lay.status == "good"
    assert lay.blocks == []


def test_market_layer_dislikes_dead_calm():
    lay = c.market_layer(11.0)
    assert lay.status == "watch"
    assert lay.put_points < 0


def test_events_layer_blocks_earnings_inside_the_window():
    today = dt.date(2026, 9, 3)
    lay = c.events_layer(dt.date(2026, 9, 20), today, dte_hi=45, kind="stock")
    assert lay.blocks == [c.PUT, c.CALL]


def test_events_layer_clears_earnings_beyond_the_window():
    today = dt.date(2026, 9, 3)
    lay = c.events_layer(dt.date(2026, 12, 1), today, dte_hi=45, kind="stock")
    assert lay.blocks == []
    assert lay.status == "good"


def test_events_layer_knows_an_index_has_no_earnings():
    lay = c.events_layer(None, dt.date(2026, 9, 3), 45, kind="index")
    assert lay.status == "good"
    assert lay.blocks == []


def test_events_layer_treats_a_missing_date_as_unknown_not_clear():
    lay = c.events_layer(None, dt.date(2026, 9, 3), 45, kind="stock")
    assert lay.status == "unknown"


def test_tradability_blocks_only_the_side_whose_credit_is_too_thin():
    read = c.ChainRead(dte=45, rel_spread=0.04, open_interest=2000, width=50,
                       put_credit_pct=0.084, call_credit_pct=0.031)
    lay = c.tradability_layer(read, min_credit_pct=0.06)
    assert lay.blocks == [c.CALL]
    # Both credits are stated, and only the failing one is called out.
    assert "The put side pays 8.4% of the width." in lay.read
    assert "The call side pays 3.1% of the width." in lay.read
    assert lay.read.count("under your 6% minimum") == 1


def test_tradability_scales_its_wording_to_how_wide_the_market_actually_is():
    read = c.ChainRead(dte=45, rel_spread=1.0, open_interest=22, width=5,
                       put_credit_pct=0.0, call_credit_pct=0.0)
    lay = c.tradability_layer(read, min_credit_pct=0.06)
    assert "about 100% of the premium" in lay.read
    assert "a fifth" not in lay.read           # was hardcoded for the >20% case
    assert ";" not in lay.read                 # sentences, not glued fragments


def test_tradability_blocks_everything_when_the_market_is_too_wide():
    read = c.ChainRead(dte=45, rel_spread=0.35, open_interest=10, width=5,
                       put_credit_pct=0.20, call_credit_pct=0.20)
    lay = c.tradability_layer(read, min_credit_pct=0.06)
    assert lay.blocks == [c.CALL, c.PUT]
    assert lay.status == "bad"


# ---------------------------------------------------------------- the whole
def _bullish_kwargs(**over):
    closes = _staircase(8, up=True)
    volumes = [1200 if closes[i] > closes[i - 1] else 500
               for i in range(1, len(closes))]
    volumes = [1000] + volumes
    bench = [100.0 + i * 0.02 for i in range(len(closes))]
    kwargs = dict(
        kind="etf", closes=closes, highs=closes, lows=closes, volumes=volumes,
        bench_closes=bench, iv_rank=65.0, iv_rank_source="Barchart", iv=30.0,
        hv=20.0, vix=16.0, earnings=None, dte_hi=45,
        chain_read=c.ChainRead(dte=45, rel_spread=0.03, open_interest=5000,
                               width=50, put_credit_pct=0.09,
                               call_credit_pct=0.07),
        min_credit_pct=0.06, today=dt.date(2026, 9, 3),
    )
    kwargs.update(over)
    return kwargs


def test_a_healthy_uptrend_in_rich_premium_favours_the_put_side():
    rep = c.assess("QQQ", **_bullish_kwargs())
    assert rep.best == c.PUT
    assert rep.put_side.verdict == "Good candidate"
    assert rep.put_side.score > rep.call_side.score
    assert not rep.put_side.blocked


def test_the_same_name_in_cheap_premium_stops_being_a_good_candidate():
    rich = c.assess("QQQ", **_bullish_kwargs())
    cheap = c.assess("QQQ", **_bullish_kwargs(iv_rank=6.0, iv=18.0, hv=20.0))
    assert cheap.put_side.score < rich.put_side.score - 2 * c.W_VOLATILITY + 0.01
    assert cheap.put_side.verdict != "Good candidate"


def test_a_downtrend_flips_the_favoured_side():
    closes = _staircase(8, up=False)
    volumes = [1200 if closes[i] < closes[i - 1] else 500
               for i in range(1, len(closes))]
    rep = c.assess("XYZ", **_bullish_kwargs(
        closes=closes, highs=closes, lows=closes, volumes=[1000] + volumes,
        bench_closes=[100.0 + i * 0.05 for i in range(len(closes))]))
    assert rep.best == c.CALL
    assert rep.call_side.score > rep.put_side.score


def test_earnings_inside_the_window_stands_the_whole_thing_down():
    rep = c.assess("XYZ", **_bullish_kwargs(kind="stock",
                                            earnings=dt.date(2026, 9, 15)))
    assert rep.best == "neither"
    assert rep.put_side.blocked and rep.call_side.blocked
    assert rep.put_side.verdict == "Stand aside"
    assert "blocked on both sides" in rep.summary


def test_a_high_vix_stands_the_whole_thing_down():
    rep = c.assess("SPX", **_bullish_kwargs(kind="index", vix=32.0))
    assert rep.put_side.verdict == "Stand aside"
    assert rep.call_side.verdict == "Stand aside"


def test_a_thin_call_credit_blocks_only_the_call_side():
    rep = c.assess("SPX", **_bullish_kwargs(
        kind="index",
        chain_read=c.ChainRead(dte=45, rel_spread=0.03, open_interest=5000,
                               width=50, put_credit_pct=0.09,
                               call_credit_pct=0.02)))
    assert rep.call_side.blocked
    assert not rep.put_side.blocked
    assert rep.best == c.PUT


def test_missing_layers_are_reported_not_counted_against_the_score():
    graded = c.assess("XYZ", **_bullish_kwargs())
    missing = c.assess("XYZ", **_bullish_kwargs(iv_rank=None, iv_rank_source=""))
    vol = next(lay for lay in missing.layers if lay.key == "volatility")
    assert not vol.known
    assert "Volatility (IV Rank)" in missing.data_gaps
    assert missing.graded == graded.graded - 1
    # The unknown layer lowers what was gradeable rather than scoring zero.
    assert missing.put_side.max_score == pytest.approx(
        graded.put_side.max_score - c.W_VOLATILITY)


def test_an_etf_is_not_marked_unknown_for_having_no_earnings_date():
    rep = c.assess("QQQ", **_bullish_kwargs(kind="etf", earnings=None))
    events = next(lay for lay in rep.layers if lay.key == "events")
    assert events.status == "good"
    assert "ETF" in events.read


def test_fit_pct_is_none_when_nothing_could_be_graded():
    rep = c.assess("XYZ", closes=[], today=dt.date(2026, 9, 3))
    assert rep.put_side.fit_pct is None
    assert rep.best == "neither"


def test_a_sideways_range_points_at_the_condor_instead():
    highs, lows = _range_bars(30)
    closes = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    rep = c.assess("SPX", **_bullish_kwargs(
        kind="index", closes=closes, highs=highs, lows=lows,
        volumes=[1000] * len(closes), bench_closes=closes))
    structure = next(lay for lay in rep.layers if lay.key == "structure")
    assert structure.value == "Sideways"
    assert "condor" in structure.read
    # A range hands neither side an edge - that is the whole point of the case.
    assert structure.put_points == structure.call_points


# ------------------------------------------------------- the Barchart import
BARCHART_CSV = """Symbol,Name,Latest,Change,%Change,Options Vol,P/C Vol,30D HV,Imp Vol,IV Rank,IV Pctl,Earnings
ODD,Oddity Tech Ltd Cl A,14.27,-0.22,-1.52%,"24,212",141.42,82.92%,131.59%,99.93%,99%,09/12/26
PCG,Pacific Gas & Elect,14.05,+0.72,+5.40%,"644,543",0.04,75.97%,59.13%,95.58%,99%,10/22/26
NOR,No Rank Corp,10.00,+0.10,+1.00%,100,0.50,20.00%,N/A,N/A,N/A,N/A
Downloaded from Barchart.com as of 09-03-2026,,,,,,,,,,,
"""


def test_barchart_import_reads_the_iv_rank_screener():
    imp = barchart.parse(BARCHART_CSV, source="iv-rank.csv")
    assert imp.ok
    assert set(imp.rows) == {"ODD", "PCG"}
    assert imp.as_of == dt.date(2026, 9, 3)
    assert imp.skipped == 2                  # the N/A row and the footer


def test_barchart_import_keeps_iv_rank_apart_from_plain_iv():
    row = barchart.parse(BARCHART_CSV).get("PCG")
    assert row.iv_rank == pytest.approx(95.58)
    assert row.iv == pytest.approx(59.13)
    assert row.hv30 == pytest.approx(75.97)
    assert row.iv_pctl == pytest.approx(99.0)
    assert row.iv_over_hv == pytest.approx(0.78, abs=0.01)


def test_barchart_import_parses_commas_percents_and_dates():
    row = barchart.parse(BARCHART_CSV).get("ODD")
    assert row.opt_volume == 24212
    assert row.pc_ratio == pytest.approx(141.42)
    assert row.earnings == dt.date(2026, 9, 12)


def test_barchart_import_is_case_and_dollar_insensitive_on_symbols():
    imp = barchart.parse("Symbol,IV Rank\n$SPX,44.0\n")
    assert imp.get("spx").iv_rank == 44.0
    assert imp.get("SPX").iv_rank == 44.0


def test_barchart_import_rejects_a_file_with_no_iv_rank_column():
    imp = barchart.parse("Symbol,Last,Volume\nAAPL,220,1000\n")
    assert not imp.ok
    assert "IV Rank column" in imp.error


def test_barchart_import_rejects_a_file_with_no_symbol_column():
    imp = barchart.parse("Name,IV Rank\nApple,44\n")
    assert not imp.ok
    assert "Symbol column" in imp.error


def test_barchart_import_survives_a_title_line_above_the_header():
    text = "Implied Volatility IV Rank,,\nSymbol,IV Rank,Imp Vol\nMU,61.2,44.0\n"
    imp = barchart.parse(text)
    assert imp.ok
    assert imp.get("MU").iv_rank == pytest.approx(61.2)


def test_barchart_import_reports_its_own_age():
    imp = barchart.parse(BARCHART_CSV)
    assert imp.age_days(today=dt.date(2026, 9, 10)) == 7


def test_barchart_import_handles_an_empty_file():
    assert not barchart.parse("").ok


# ------------------------------------------------- where the IV Rank comes from
def _vol_series(low: float, high: float, latest: float) -> list[float]:
    return [low] * 120 + [high] + [latest]


def test_vol_source_prefers_the_barchart_export_over_everything():
    row = barchart.parse(BARCHART_CSV).get("PCG")
    read = vol_source.resolve("PCG", barchart_row=row, manual_rank=50.0,
                              vol_index_closes=_vol_series(10, 30, 20),
                              own_closes=[100.0] * 300)
    assert read.source == "Barchart export"
    assert read.iv_rank == pytest.approx(95.58)
    assert not read.is_proxy


def test_vol_source_falls_back_to_a_typed_rank():
    read = vol_source.resolve("MU", manual_rank=42.0, own_closes=[100.0] * 300)
    assert read.source == "typed in by hand"
    assert read.iv_rank == 42.0
    assert not read.is_proxy


def test_vol_source_uses_the_matching_cboe_index_for_an_index():
    read = vol_source.resolve("SPX", vol_index_closes=_vol_series(10, 30, 20))
    assert read.source == "VIX"
    assert read.iv_rank == pytest.approx(50.0)
    assert not read.is_proxy


def test_vol_source_maps_each_underlying_to_its_own_index():
    assert vol_source.vol_index_for("QQQ") == "^VXN"
    assert vol_source.vol_index_for("IWM") == "^RVX"
    assert vol_source.vol_index_for("$SPX") == "^VIX"
    assert vol_source.vol_index_for("MU") is None


def test_vol_source_ignores_a_volatility_index_for_a_name_that_has_none():
    # A stock has no volatility index, so index closes must not be used for it.
    read = vol_source.resolve("MU", vol_index_closes=_vol_series(10, 30, 20),
                              own_closes=[100.0 * (1.01 ** i) for i in range(300)])
    assert read.source != "VIX"


def test_vol_source_last_resort_is_a_labelled_proxy():
    closes = [100.0]
    for i in range(400):
        closes.append(closes[-1] * (1.02 if i % 3 else 0.985))
    read = vol_source.resolve("MU", own_closes=closes)
    assert read.is_proxy
    assert "proxy" in read.source
    assert "not the same thing" in read.note


def test_vol_source_says_so_when_it_has_nothing():
    read = vol_source.resolve("MU")
    assert not read.known
    assert "Barchart" in read.note


def test_the_market_itself_is_not_graded_on_relative_strength():
    lay = c.strength_layer([], [], "SPY", is_benchmark=True)
    assert lay.known                      # a question that does not apply
    assert lay.status == "ok"
    assert lay.put_points == 0 and lay.call_points == 0
    assert "IS the broad market" in lay.read
