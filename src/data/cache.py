"""A tiny in-memory cache so we do not hammer the Schwab API. Option chains
change second to second, but for a 45-day trade a 60-second-old chain is fine.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

_STORE: dict[str, tuple[float, Any]] = {}


def get_or_fetch(key: str, fetch: Callable[[], Any], ttl_seconds: int = 60) -> Any:
    """Return a cached value if it is fresh, otherwise fetch and store it."""
    now = time.time()
    hit = _STORE.get(key)
    if hit is not None:
        ts, value = hit
        if now - ts < ttl_seconds:
            return value
    value = fetch()
    _STORE[key] = (now, value)
    return value


def clear(key: Optional[str] = None) -> None:
    if key is None:
        _STORE.clear()
    else:
        _STORE.pop(key, None)
