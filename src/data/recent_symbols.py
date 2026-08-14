"""Tickers she has typed into the Analyze tab, kept between visits.

The Analyze symbol box accepts any ticker, not just the ones in the S&P 500 /
Nasdaq-100 universe files. Those additions used to live for a single rerun, then
for a single session; both meant retyping a name she looks at every week.

Different job from the watchlist next door, which is a list she CURATES and
which changes what the Picks sweep screens. This one is a memory of where she
has been - written automatically, never asked about, and it changes nothing
except what the dropdown offers her.

Newest first, because the ticker she wants next is almost always the one she
looked at last. Cleaning rules are shared with the watchlist so "what counts as
a ticker" cannot drift between the two.

Stored as plain JSON next to the other universe files. Same durability as the
watchlist and her saved plan: it survives locally, and the hosted app rebuilds
its filesystem on restart, so a redeploy starts the list fresh.

Pure: no Streamlit, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.data.watchlist import clean_symbols

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECENTS_PATH = PROJECT_ROOT / "sample_data" / "analyze_recents.json"

# Enough to cover the names she actually revisits without the dropdown filling
# with one-off look-ups from months ago.
MAX_SYMBOLS = 20


def read(path: Optional[Path] = None) -> list[str]:
    """The saved list, newest first, or [] when there is no file yet.

    Never raises: a corrupt file must not take the Analyze tab down. This is a
    convenience list, and losing it costs her one retype.
    """
    path = path or RECENTS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("symbols", [])
    return clean_symbols(data if isinstance(data, list) else [], MAX_SYMBOLS)


def save(symbols, path: Optional[Path] = None) -> list[str]:
    """Write the list and return exactly what was stored."""
    path = path or RECENTS_PATH
    cleaned = clean_symbols(symbols, MAX_SYMBOLS)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "_comment": ("Tickers typed into the app's Analyze tab, so they stay "
                         "in the dropdown. Written automatically; safe to delete."),
            "symbols": cleaned,
        }, indent=1), encoding="utf-8")
    except Exception:
        # A read-only filesystem must not break the tab she is standing in.
        # The list simply does not persist; the session still remembers it.
        return cleaned
    return cleaned


def remember(symbol, path: Optional[Path] = None) -> list[str]:
    """Move one ticker to the front of the list and save. Returns the new list.

    Idempotent, so re-selecting the same name on every rerun neither duplicates
    it nor reshuffles anything - it is already at the front.
    """
    cleaned = clean_symbols([symbol], MAX_SYMBOLS)
    if not cleaned:
        return read(path)
    current = read(path)
    if current[:1] == cleaned:
        return current
    return save(cleaned + [s for s in current if s != cleaned[0]], path)
