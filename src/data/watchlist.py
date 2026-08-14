"""Her own list of tickers to always screen, whatever the market screen says.

The Full sweep screens a big universe and then keeps only the finalists that
clear the size, volume and trend bars. That is the right default, but it means
a name she is actively researching can quietly vanish from her results on a day
it happens to miss a bar. This list is force-added to the sweep so the names
she cares about are always evaluated and always reported on.

Stored as plain JSON next to the other universe files. Same durability as her
saved plan: it survives locally, and the hosted app rebuilds its filesystem on
restart, so treat it as a convenience rather than permanent storage.

Pure: no Streamlit, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WATCHLIST_PATH = PROJECT_ROOT / "sample_data" / "watchlist.json"

MAX_SYMBOLS = 30        # past this the sweep slows down for little gain


def clean_symbols(symbols, limit: int = MAX_SYMBOLS) -> list[str]:
    """Uppercase, de-duplicated, order preserved, junk dropped, capped.

    Public because the Analyze tab's remembered-tickers list needs exactly the
    same rules with a different cap, and two copies of "what counts as a
    ticker" is how they drift apart.
    """
    out: list[str] = []
    for raw in symbols or []:
        # Guard the type before str(): str(None) is "None", which sails through
        # every check below and would be stored as a ticker called NONE.
        if not isinstance(raw, str):
            continue
        sym = raw.strip().upper()
        if not sym or not sym.replace("-", "").replace(".", "").isalnum():
            continue
        if len(sym) > 6 or sym in out:
            continue
        out.append(sym)
    return out[:limit]


def _clean(symbols) -> list[str]:
    """Kept as the module's own name for its own cap."""
    return clean_symbols(symbols, MAX_SYMBOLS)


def read(path: Optional[Path] = None) -> list[str]:
    """The saved list, or [] when there is no file yet or it is unreadable.

    Never raises: a corrupt watchlist must not take the Picks tab down with it.
    """
    path = path or WATCHLIST_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("symbols", [])
    return _clean(data if isinstance(data, list) else [])


def save(symbols, path: Optional[Path] = None) -> list[str]:
    """Write the list and return exactly what was stored."""
    path = path or WATCHLIST_PATH
    cleaned = _clean(symbols)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": ("Tickers the Picks Full sweep always screens, on top of "
                     "whatever the market screen finds. Edit in the app's Picks tab."),
        "symbols": cleaned,
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cleaned
