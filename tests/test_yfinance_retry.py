"""The Yahoo rate-limit retry helper: retries a throttled call a few times with
backoff, then gives up. Uses no network and no real sleeps."""

from __future__ import annotations

import pytest
from yfinance.exceptions import YFRateLimitError

from src.data import yfinance_client as yc


def test_retry_succeeds_after_a_transient_rate_limit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise YFRateLimitError()
        return "chain"

    assert yc._with_retry(flaky) == "chain"
    assert calls["n"] == 3


def test_retry_reraises_when_it_never_clears(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def always():
        raise YFRateLimitError()

    with pytest.raises(YFRateLimitError):
        yc._with_retry(always, tries=2)


def test_retry_does_not_swallow_other_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def boom():
        raise ValueError("not a rate limit")

    with pytest.raises(ValueError):
        yc._with_retry(boom)


def test_no_retry_when_first_call_succeeds():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return 42

    assert yc._with_retry(ok) == 42
    assert calls["n"] == 1
