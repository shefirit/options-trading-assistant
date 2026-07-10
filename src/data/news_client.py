"""Recent market-news headlines from free public RSS feeds.

Why RSS: Yahoo and the other quote APIs get throttled from datacenter IPs
(Streamlit Cloud), but RSS feeds are meant to be crawled and stay reliable
there - no API key, no signup. We show only the headline, its source, and a
link (never the article body), so there is nothing to act on and no copyright
issue. A feed that fails is skipped, never fatal.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from pydantic import BaseModel

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# (source label, feed url). Market and economy focused; a few, so one going
# down still leaves headlines. Confirmed reachable from a datacenter host.
FEEDS: list[tuple[str, str]] = [
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),      # economy
    ("CNBC", "https://www.cnbc.com/id/15839069/device/rss/rss.html"),      # markets
]


class NewsItem(BaseModel):
    title: str
    source: str
    url: str
    published: Optional[datetime] = None   # timezone-aware (UTC) when known

    def age(self, now: Optional[datetime] = None) -> str:
        """A short 'how long ago' label, e.g. '2h ago'. Empty if undated."""
        if self.published is None:
            return ""
        now = now or datetime.now(timezone.utc)
        secs = (now - self.published).total_seconds()
        mins = int(secs // 60)
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        return f"{hrs // 24}d ago"


def _parse_date(text: Optional[str]) -> Optional[datetime]:
    """RFC-822 (RSS pubDate) or ISO -> aware UTC datetime, or None."""
    if not text:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/rss+xml,application/xml,text/xml,*/*",
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read()


def _to_text(raw: bytes) -> str:
    """Decode a feed tolerantly. Some feeds (MarketWatch) send Windows-1252
    smart quotes in a doc that does not decode as strict UTF-8, so fall back to
    cp1252. The XML encoding declaration is stripped so ElementTree accepts the
    resulting unicode string."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    return re.sub(r"<\?xml[^>]*\?>", "", text, count=1)


def _parse_feed(raw: bytes, source: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    root = ET.fromstring(_to_text(raw))
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(NewsItem(title=title, source=source, url=link,
                              published=_parse_date(it.findtext("pubDate"))))
    return items


def fetch_headlines(limit: int = 6, feeds: Optional[list] = None) -> list[NewsItem]:
    """Merge the feeds, newest first, de-duplicated by title, capped at `limit`."""
    items: list[NewsItem] = []
    for source, url in (feeds or FEEDS):
        try:
            items.extend(_parse_feed(_fetch(url), source))
        except Exception:
            continue   # one bad feed never sinks the section
    items.sort(key=lambda n: n.published or _EPOCH, reverse=True)
    seen: set[str] = set()
    unique: list[NewsItem] = []
    for n in items:
        key = n.title.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(n)
    return unique[:limit]


def demo_headlines(limit: int = 6) -> list[NewsItem]:
    """Deterministic sample headlines for demo/offline mode (no network)."""
    base = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)
    canned = [
        ("Fed holds rates steady, signals patience on further cuts", "CNBC"),
        ("S&P 500 drifts to a quiet close as traders eye inflation data", "MarketWatch"),
        ("Weekly jobless claims fall to a two-month low", "MarketWatch"),
        ("Chip stocks lead the market higher; energy lags on softer oil", "CNBC"),
        ("Treasury yields ease ahead of next week's CPI report", "CNBC"),
        ("Gold steadies near a record as the dollar softens", "MarketWatch"),
    ]
    return [NewsItem(title=t, source=s, url="https://example.com/markets-news",
                     published=base - timedelta(hours=i))
            for i, (t, s) in enumerate(canned)][:limit]
