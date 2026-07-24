"""The TradingView chart embed: symbol mapping and widget HTML (no network)."""

import json
import re

from ui import tv_chart


# ---------------------------------------------------------------- symbol map
def test_indexes_chart_as_their_etf_proxy():
    """The cash indexes are licensed data - TradingView's free embed refuses them,
    so each one charts as the ETF holding the same basket."""
    assert tv_chart.tv_symbol("SPX", "index") == "AMEX:SPY"
    assert tv_chart.tv_symbol("XSP", "index") == "AMEX:SPY"
    assert tv_chart.tv_symbol("NDX", "index") == "NASDAQ:QQQ"
    assert tv_chart.tv_symbol("RUT", "index") == "AMEX:IWM"


def test_a_proxied_index_says_what_she_is_actually_looking_at():
    note = tv_chart.proxy_note("SPX")
    assert "SPY" in note and "SPX" in note
    assert "10" in note                      # the one-tenth scale is spelled out


def test_an_index_with_no_fixed_scale_does_not_invent_a_multiplier():
    note = tv_chart.proxy_note("NDX")
    assert "QQQ" in note
    assert "multiply" not in note.lower()


def test_a_real_ticker_gets_no_proxy_note():
    assert tv_chart.proxy_note("SPY") == ""
    assert tv_chart.proxy_note("AAPL") == ""
    assert tv_chart.proxy_note("") == ""


def test_etfs_map_to_their_listing_exchange():
    assert tv_chart.tv_symbol("SPY", "etf") == "AMEX:SPY"
    assert tv_chart.tv_symbol("QQQ", "etf") == "NASDAQ:QQQ"
    assert tv_chart.tv_symbol("TLT", "etf") == "NASDAQ:TLT"


def test_a_plain_stock_falls_through_as_a_bare_ticker():
    """TradingView resolves a bare US ticker to its primary listing."""
    assert tv_chart.tv_symbol("AAPL", "stock") == "AAPL"
    assert tv_chart.tv_symbol("nvda", "stock") == "NVDA"


def test_an_unmapped_index_is_passed_through_for_tradingview_to_resolve():
    assert tv_chart.tv_symbol("SOMEIDX", "index") == "SOMEIDX"


def test_an_empty_symbol_maps_to_nothing():
    assert tv_chart.tv_symbol("", "stock") == ""
    assert tv_chart.tv_symbol(None) == ""


# ---------------------------------------------------------------- widget HTML
def _config_from(html: str) -> dict:
    """Pull the JSON config back out of the embed script tag."""
    m = re.search(r"async>(\{.*?\})</script>", html, re.S)
    assert m, "no widget config found in the embed HTML"
    return json.loads(m.group(1))


def test_the_html_carries_the_symbol_interval_and_studies():
    html = tv_chart.chart_html("AMEX:SPY", "W",
                               ["BB@tv-basicstudies"], {"moving average.length": 50})
    cfg = _config_from(html)
    assert cfg["symbol"] == "AMEX:SPY"
    assert cfg["interval"] == "W"
    assert cfg["studies"] == ["BB@tv-basicstudies"]
    assert cfg["studies_overrides"]["moving average.length"] == 50


def test_the_config_never_sets_a_range():
    """A `range` makes the widget pick its own candle size and ignore the
    interval - asking for daily candles over 6M silently gave 2-hour candles."""
    for interval in tv_chart._TIMEFRAMES.values():
        assert "range" not in _config_from(tv_chart.chart_html("AMEX:SPY", interval))


def test_the_chart_keeps_volume_and_hides_the_drawing_tools():
    """Volume is part of the beginner read; drawing tools are clutter."""
    cfg = _config_from(tv_chart.chart_html("AMEX:SPY"))
    assert cfg["hide_volume"] is False
    assert cfg["hide_side_toolbar"] is True
    assert cfg["allow_symbol_change"] is False
    assert cfg["style"] == "1"          # candles


def test_the_embed_links_out_to_the_full_tradingview_page():
    html = tv_chart.chart_html("AMEX:SPY")
    assert "tradingview.com/symbols/AMEX-SPY/" in html
    assert "embed-widget-advanced-chart.js" in html


# ---------------------------------------------------------------- the presets
def test_bollinger_bands_and_the_moving_averages_are_on_by_default():
    on = [label for label, spec in tv_chart._INDICATORS.items() if spec["default"]]
    assert any("Bollinger" in label for label in on)
    assert any("Moving averages" in label for label in on)


def test_the_moving_averages_are_set_to_20_and_50():
    spec = next(v for k, v in tv_chart._INDICATORS.items() if "Moving averages" in k)
    assert spec["overrides"]["moving average exponential.length"] == 20
    assert spec["overrides"]["moving average.length"] == 50


def test_daily_is_the_default_timeframe():
    assert tv_chart._DEFAULT_TIMEFRAME in tv_chart._TIMEFRAMES
    assert tv_chart._TIMEFRAMES[tv_chart._DEFAULT_TIMEFRAME] == "D"
