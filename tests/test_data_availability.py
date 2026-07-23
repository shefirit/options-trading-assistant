"""The Yahoo reachability probe, and what the app does when it says no.

A false "no" here is the expensive kind of wrong: the app quietly falls back to
bundled SAMPLE data, and fake prices on screen read exactly like real ones. The
old probe gave Yahoo 8 seconds, which a cold TLS handshake regularly overran, so
a perfectly healthy connection landed in demo mode. These tests pin the budget,
the retry, and the fact that the timeout is still honestly enforced.

No network and no real waiting beyond a few hundred milliseconds.
"""

from __future__ import annotations

import threading
import time

from src.data import yfinance_client as yc
from src.data.provider import DataProvider


class _HangingProbe:
    """A probe that blocks until the test lets it go.

    Abandoned probe threads are not daemons - Python joins them at interpreter
    exit - so a test that walks away from one has to release it afterwards or it
    stalls the whole run.
    """

    def __init__(self, answer: bool = True, hang_first_only: bool = False):
        self.release = threading.Event()
        self.answer = answer
        self.hang_first_only = hang_first_only
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if not (self.hang_first_only and self.calls > 1):
            self.release.wait(30)
        return self.answer


def test_a_reachable_yahoo_answers_yes_on_the_first_probe(monkeypatch):
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True

    monkeypatch.setattr(yc, "_probe", probe)
    assert yc.is_available() is True
    assert calls["n"] == 1          # no pointless second round trip


def test_a_slow_cold_handshake_is_not_called_offline(monkeypatch):
    """The exact bug: the first call is slow because it opens the connection.
    It must be waited for, not abandoned."""
    monkeypatch.setattr(yc, "_probe", lambda: (time.sleep(0.3), True)[1])
    assert yc.is_available(timeout=5.0) is True


def test_a_timed_out_first_attempt_is_retried(monkeypatch):
    """Attempt one hangs past its budget; attempt two - riding the connection
    the first one opened - comes back fine. That must read as available."""
    probe = _HangingProbe(answer=True, hang_first_only=True)
    monkeypatch.setattr(yc, "_probe", probe)
    try:
        started = time.monotonic()
        assert yc.is_available(timeout=0.2) is True
        assert probe.calls == 2
        # Giving up on attempt one must not secretly wait for it to finish.
        assert time.monotonic() - started < 2.0
    finally:
        probe.release.set()


def test_a_probe_that_answers_no_is_retried_once(monkeypatch):
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return calls["n"] > 1       # transient throttle, clears on the retry

    monkeypatch.setattr(yc, "_probe", probe)
    assert yc.is_available(timeout=1.0) is True
    assert calls["n"] == 2


def test_a_dead_network_still_gives_up_inside_the_budget(monkeypatch):
    """The timeout is a real promise: a network that never answers must not hang
    the app's startup, however generous the budget got."""
    probe = _HangingProbe(answer=True)
    monkeypatch.setattr(yc, "_probe", probe)
    try:
        started = time.monotonic()
        assert yc.is_available(timeout=0.2, tries=2) is False
        assert time.monotonic() - started < 5.0
    finally:
        probe.release.set()


def test_the_probe_budget_is_generous_enough_for_a_cold_handshake():
    """Regression guard on the number itself. 8 seconds was too tight - a cold
    first TLS handshake to Yahoo overruns it on an ordinary connection."""
    assert yc.PROBE_TIMEOUT >= 20.0


def test_probe_never_raises_when_yahoo_blows_up(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Yahoo said no")

    monkeypatch.setattr(yc, "_ticker", boom)
    assert yc._probe() is False


def test_probe_threads_do_not_outlive_a_successful_check(monkeypatch):
    """Housekeeping: the executor is not left holding a worker per call."""
    monkeypatch.setattr(yc, "_probe", lambda: True)
    before = threading.active_count()
    for _ in range(5):
        yc.is_available(timeout=1.0)
    time.sleep(0.2)
    assert threading.active_count() <= before + 1


# ---------- what the provider does with the answer ----------
def test_provider_goes_real_when_the_probe_says_yes(monkeypatch):
    monkeypatch.setattr("src.data.schwab_client.SchwabClient.is_configured",
                        staticmethod(lambda: False))
    monkeypatch.setattr(yc, "is_available", lambda *a, **k: True)
    assert DataProvider.create().mode == "yahoo"


def test_provider_falls_back_to_demo_only_when_the_probe_says_no(monkeypatch):
    monkeypatch.setattr("src.data.schwab_client.SchwabClient.is_configured",
                        staticmethod(lambda: False))
    monkeypatch.setattr(yc, "is_available", lambda *a, **k: False)
    provider = DataProvider.create()
    assert provider.mode == "demo"
    assert provider.is_real is False
