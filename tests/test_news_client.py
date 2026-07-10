"""News client: RSS parsing, tolerant decoding, and merge logic - HTTP mocked."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data import news_client as nc

_SAMPLE_A = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Stocks rise on cooler inflation</title><link>https://ex.com/a1</link>
<pubDate>Thu, 09 Jul 2026 14:00:00 GMT</pubDate></item>
<item><title>Fed holds rates steady</title><link>https://ex.com/a2</link>
<pubDate>Thu, 09 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title></title><link>https://ex.com/empty</link></item>
</channel></rss>"""

_SAMPLE_B = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Jobs report tops forecasts</title><link>https://ex.com/b1</link>
<pubDate>Thu, 09 Jul 2026 16:00:00 GMT</pubDate></item>
<item><title>Fed holds rates steady</title><link>https://ex.com/b-dupe</link>
<pubDate>Thu, 09 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed_extracts_items_and_skips_empty():
    items = nc._parse_feed(_SAMPLE_A, "Test")
    assert len(items) == 2   # the empty-title item is skipped
    assert items[0].title == "Stocks rise on cooler inflation"
    assert items[0].url == "https://ex.com/a1"
    assert items[0].source == "Test"
    assert items[0].published is not None and items[0].published.tzinfo is not None


def test_parse_date_rfc822_iso_and_bad():
    assert nc._parse_date("Thu, 09 Jul 2026 14:00:00 GMT").tzinfo is not None
    assert nc._parse_date("2026-07-09T14:00:00Z").tzinfo is not None
    assert nc._parse_date("not a date") is None
    assert nc._parse_date(None) is None


def test_to_text_decodes_cp1252_smart_quotes():
    raw = b"The Fed won" + b"\x92" + b"t blink"   # 0x92 is a cp1252 apostrophe
    text = nc._to_text(raw)
    assert "’" in text          # became a real right single quote
    assert "�" not in text      # not a replacement character


def test_fetch_headlines_merges_sorts_and_dedupes(monkeypatch):
    raw = {"urlA": _SAMPLE_A, "urlB": _SAMPLE_B}
    monkeypatch.setattr(nc, "_fetch", lambda url: raw[url])
    items = nc.fetch_headlines(limit=6, feeds=[("A", "urlA"), ("B", "urlB")])
    titles = [n.title for n in items]
    assert titles[0] == "Jobs report tops forecasts"        # 16:00, newest
    assert titles[1] == "Stocks rise on cooler inflation"   # 14:00
    assert titles.count("Fed holds rates steady") == 1      # deduped across feeds


def test_fetch_headlines_skips_a_failing_feed(monkeypatch):
    def flaky(url):
        if url == "bad":
            raise RuntimeError("boom")
        return _SAMPLE_A
    monkeypatch.setattr(nc, "_fetch", flaky)
    items = nc.fetch_headlines(limit=6, feeds=[("Bad", "bad"), ("Good", "good")])
    assert items and all(n.source == "Good" for n in items)


def test_age_label():
    now = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)
    item = nc.NewsItem(title="x", source="s", url="u",
                       published=datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc))
    assert item.age(now) == "2h ago"


def test_demo_headlines_deterministic():
    a = nc.demo_headlines(4)
    assert len(a) == 4
    assert [n.title for n in a] == [n.title for n in nc.demo_headlines(4)]
    assert all(n.published is not None for n in a)
