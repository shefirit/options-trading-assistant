"""A tiny in-memory cache so we do not hammer the Schwab API. Option chains
change second to second, but for a 45-day trade a 60-second-old chain is fine.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

# key -> (stored_at, value, ttl). The ttl rides along per entry because a
# THROTTLED answer is kept far more briefly than a real one - see get_or_fetch.
_STORE: dict[str, tuple[float, Any, float]] = {}

# How long a value that failed its `keep` test is held. Long enough that a
# Streamlit rerun storm - every widget click reruns the whole script - cannot
# turn one blank into a hundred requests, short enough that she gets a real
# answer by the time she has read the panel.
RETRY_AFTER = 30


def get_or_fetch(key: str, fetch: Callable[[], Any], ttl_seconds: int = 60,
                 keep: Optional[Callable[[Any], bool]] = None,
                 retry_after: int = RETRY_AFTER) -> Any:
    """Return a cached value if it is fresh, otherwise fetch and store it.

    `keep` decides whether what came back deserves the FULL ttl. Anything it
    rejects is still cached, but only for `retry_after` seconds.

    That distinction is the whole point. Yahoo throttles datacenter IPs, so on
    the hosted app a company-info call routinely comes back empty - and an empty
    answer used to be cached exactly like a real one. A name fetched during a
    throttled moment therefore showed "did not load" for a solid hour, while the
    same name fetched a minute later was perfect: the app looked like it could
    not handle certain tickers when nothing was wrong with them at all. Rita hit
    this on SKWD and ACIW, both of which Yahoo answers fully.
    """
    now = time.time()
    hit = _STORE.get(key)
    if hit is not None:
        ts, value, ttl = hit
        if now - ts < ttl:
            return value
    value = fetch()
    ttl = ttl_seconds if (keep is None or keep(value)) else retry_after
    _STORE[key] = (now, value, ttl)
    return value


def clear(key: Optional[str] = None) -> None:
    if key is None:
        _STORE.clear()
    else:
        _STORE.pop(key, None)
