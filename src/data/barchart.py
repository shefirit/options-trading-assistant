"""Reads the CSV you export from Barchart's "IV Rank and IV Percentile" screener.

Why a file and not a live call: Barchart's own data API (OnDemand) is a separate
paid product, not part of a Premier or Plus membership, and the website's
internal endpoints need a logged-in session - scraping those would breach their
terms. The download button on the screener is the path your subscription
actually gives you, so the app reads what you export.

What the file buys us is the one number nothing else in the stack can produce:
a REAL IV Rank for an individual stock. Free sources give price history, and you
can rank realized volatility from that, but realized volatility is what the
stock DID - IV Rank is what options are CHARGING, and only an options data
vendor keeps a year of it.

Parsing is deliberately forgiving. Barchart's exports change column order and
wording between views, sometimes carry a "Downloaded from Barchart.com" footer
line, and format numbers with commas, percent signs and "N/A". A rigid parser
would break silently the first time she picks a different view.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

# Column wording we accept, most specific FIRST. Order matters: "IV Rank" also
# starts with "iv", so a loose "iv" test placed first would swallow it.
_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
    ("iv_rank",    ("iv rank", "ivrank", "iv rk")),
    ("iv_pctl",    ("iv pctl", "iv percentile", "ivpctl", "iv pct")),
    ("hv30",       ("30d hv", "hv30", "30-day historical volatility",
                    "historic volatility", "historical volatility")),
    ("iv",         ("imp vol", "implied volatility", "impvol", "iv")),
    ("opt_volume", ("options vol", "option volume", "options volume", "total volume")),
    ("pc_ratio",   ("p/c vol", "put/call vol", "put/call ratio", "p/c ratio")),
    ("earnings",   ("earnings", "next earnings", "earnings date")),
    ("price",      ("latest", "last", "last price", "price")),
    ("name",       ("name", "company", "description")),
    ("symbol",     ("symbol", "ticker")),
]

# A symbol cell matching this is Barchart's footer line, not a stock.
_FOOTER = re.compile(r"downloaded from|barchart\.com", re.I)

_MISSING = {"n/a", "na", "-", "--", "unch", "null", ""}


class VolSnapshot(BaseModel):
    """One symbol's volatility picture, as Barchart reported it."""

    symbol: str
    name: str = ""
    price: Optional[float] = None
    iv: Optional[float] = None           # implied volatility, percent (28.4 = 28.4%)
    iv_rank: Optional[float] = None      # 0-100, where IV sits in its 1-year range
    iv_pctl: Optional[float] = None      # 0-100, share of days IV was lower
    hv30: Optional[float] = None         # 30-day realized volatility, percent
    opt_volume: Optional[int] = None
    pc_ratio: Optional[float] = None
    earnings: Optional[dt.date] = None

    @property
    def iv_over_hv(self) -> Optional[float]:
        """Implied divided by realized. Above 1 means options are charging more
        than the stock has actually been moving - the premium seller's edge."""
        if self.iv is None or not self.hv30:
            return None
        return round(self.iv / self.hv30, 2)


class BarchartImport(BaseModel):
    """Everything one exported file gave us, plus how the import went."""

    rows: dict[str, VolSnapshot] = Field(default_factory=dict)
    as_of: Optional[dt.date] = None      # the file's own date, when it states one
    imported_at: Optional[dt.datetime] = None
    source: str = ""                     # the file name, for the screen
    columns_found: list[str] = Field(default_factory=list)
    skipped: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not self.error

    def get(self, symbol: str) -> Optional[VolSnapshot]:
        return self.rows.get(norm_symbol(symbol))

    def age_days(self, today: Optional[dt.date] = None) -> Optional[int]:
        """How stale the file is. IV Rank is a one-year range measure so it
        crawls rather than jumps, but a week-old file is still a week old."""
        stamp = self.as_of or (self.imported_at.date() if self.imported_at else None)
        if stamp is None:
            return None
        return ((today or dt.date.today()) - stamp).days


def norm_symbol(raw: str) -> str:
    """Barchart writes indexes as $SPX and some names with a trailing exchange.
    Reduce to the plain ticker the rest of the app uses."""
    s = str(raw or "").strip().upper()
    s = s.lstrip("$^")
    return s.split(".")[0].split(" ")[0]


def _norm_header(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def _num(raw: Any) -> Optional[float]:
    """Barchart numbers arrive as '141.42', '1,234', '82.92%', 'N/A', 'unch'."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "").replace("+", "")
    if text.lower() in _MISSING:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(raw: Any) -> Optional[int]:
    v = _num(raw)
    return int(v) if v is not None else None


def _date(raw: Any) -> Optional[dt.date]:
    """Barchart writes earnings as 09/12/26 or 09/12/2026, occasionally ISO."""
    text = str(raw or "").strip()
    if text.lower() in _MISSING:
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _map_columns(header: list[str]) -> dict[str, int]:
    """Which column index holds which field. Each field claims at most one
    column and each column is claimed at most once, so a header like "IV Rank"
    cannot also be read as plain "IV"."""
    normed = [_norm_header(h) for h in header]
    taken: set[int] = set()
    found: dict[str, int] = {}
    for field, wordings in _COLUMNS:
        for i, head in enumerate(normed):
            if i in taken or not head:
                continue
            if head in wordings or any(head.startswith(w) for w in wordings):
                found[field] = i
                taken.add(i)
                break
    return found


def _header_row(rows: list[list[str]]) -> int:
    """Barchart sometimes puts a title line above the header. The header is the
    first row that names a symbol column."""
    for i, row in enumerate(rows[:5]):
        if any(_norm_header(c) in ("symbol", "ticker") for c in row):
            return i
    return 0


def _file_date(rows: list[list[str]]) -> Optional[dt.date]:
    """The footer usually reads 'Downloaded from Barchart.com as of 09-03-2026'."""
    for row in rows[-4:]:
        blob = " ".join(str(c) for c in row)
        if not re.search(r"barchart|downloaded", blob, re.I):
            continue
        m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", blob)
        if m:
            month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return dt.date(year, month, day)
            except ValueError:
                return None
    return None


def parse(text: str, source: str = "") -> BarchartImport:
    """Turn the exported CSV's text into snapshots keyed by symbol."""
    now = dt.datetime.now()
    if not (text or "").strip():
        return BarchartImport(error="That file is empty.", source=source,
                              imported_at=now)

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return BarchartImport(error="Could not read any rows from that file.",
                              source=source, imported_at=now)

    head_at = _header_row(rows)
    cols = _map_columns(rows[head_at])
    if "symbol" not in cols:
        return BarchartImport(
            error="No Symbol column in that file. Export from Barchart's "
                  "'IV Rank and IV Percentile' screener using the download link "
                  "above the table.",
            source=source, imported_at=now)
    if "iv_rank" not in cols:
        return BarchartImport(
            error="That file has no IV Rank column, so it cannot fill the "
                  "volatility read. Use the 'IV Rank and IV Percentile' screener "
                  "on Barchart rather than a plain quote export.",
            source=source, imported_at=now)

    out: dict[str, VolSnapshot] = {}
    skipped = 0

    def cell(row: list[str], field: str) -> Any:
        i = cols.get(field)
        return row[i] if i is not None and i < len(row) else None

    for row in rows[head_at + 1:]:
        if not row:
            continue
        raw_symbol = str(cell(row, "symbol") or "")
        symbol = norm_symbol(raw_symbol)
        if not symbol or _FOOTER.search(raw_symbol):
            skipped += 1
            continue
        rank = _num(cell(row, "iv_rank"))
        if rank is None:
            skipped += 1
            continue
        out[symbol] = VolSnapshot(
            symbol=symbol,
            name=str(cell(row, "name") or "").strip(),
            price=_num(cell(row, "price")),
            iv=_num(cell(row, "iv")),
            iv_rank=rank,
            iv_pctl=_num(cell(row, "iv_pctl")),
            hv30=_num(cell(row, "hv30")),
            opt_volume=_int(cell(row, "opt_volume")),
            pc_ratio=_num(cell(row, "pc_ratio")),
            earnings=_date(cell(row, "earnings")),
        )

    if not out:
        return BarchartImport(
            error="Found the columns but no usable rows - every line was missing "
                  "an IV Rank.",
            source=source, imported_at=now, skipped=skipped)

    return BarchartImport(rows=out, as_of=_file_date(rows), imported_at=now,
                          source=source, columns_found=sorted(cols), skipped=skipped)
