"""WebScanManager must ask the media server whether its library scan is still
running before declaring completion — not decide on a wall clock alone.

``check_completion`` used to fire ``_handle_scan_completion`` (→
``library_scan_completed`` → the Auto-Update Database automation) after
exactly 300 seconds, never calling ``is_library_scanning()`` even though
every client implements it. Any server-side scan lasting over 5 minutes got
its DB update run against an incomplete library — feeding the deep-scan
stale-deletion path (C1) — and ``_max_scan_time`` (1800s) was unreachable.

Rule pinned here:
- probe says scanning → keep waiting (completion NOT declared at 5 min);
- probe says done (or probe unavailable) → the 5-minute time heuristic may
  declare completion;
- probe raises → treat as "still scanning" (fail closed); the 30-minute
  ``_max_scan_time`` timeout remains the backstop.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import core.web_scan_manager as wsm


class _FakeClient:
    def __init__(self, scanning=True, probe_raises=False):
        self._scanning = scanning
        self._probe_raises = probe_raises
        self.probe_calls = 0

    def is_library_scanning(self):
        self.probe_calls += 1
        if self._probe_raises:
            raise RuntimeError("probe blew up")
        return self._scanning


class _CaptureTimer:
    """Stand-in for threading.Timer: records the scheduled check instead of
    waiting 30 real seconds."""

    captured = {}

    def __init__(self, delay, fn):
        type(self).captured["delay"] = delay
        type(self).captured["fn"] = fn
        self.daemon = True

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture()
def clock(monkeypatch):
    """Fake clock + capturing Timer; returns (now_list, fired_list)."""
    now = [1_000_000.0]
    fired = []
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(threading, "Timer", _CaptureTimer)
    _CaptureTimer.captured.clear()
    return now, fired


def _manager_with_client(client, fired, monkeypatch):
    engine = SimpleNamespace(client=lambda name: client)
    mgr = wsm.WebScanManager(media_server_engine=engine, delay_seconds=60)
    mgr._current_server_type = "plex"
    mgr._scan_start_time = time.time()
    mgr.add_scan_completion_callback(lambda: fired.append("completed"))
    mgr._start_periodic_completion_check()
    return mgr


def _tick_to(manager, now, seconds):
    now[0] += seconds
    _CaptureTimer.captured["fn"]()


def test_completion_waits_while_server_still_scanning(clock, monkeypatch):
    """REGRESSION (H15): at t+301s with the server still scanning, no
    'completed' may be emitted — the probe must be consulted first."""
    now, fired = clock
    client = _FakeClient(scanning=True)
    mgr = _manager_with_client(client, fired, monkeypatch)

    _tick_to(mgr, now, 60)
    assert not fired
    _tick_to(mgr, now, 241)  # t+301s, server STILL scanning

    assert client.probe_calls > 0, "is_library_scanning() was never consulted"
    assert not fired, "completion declared at 5 min while the server was still scanning"


def test_completion_declared_once_server_reports_done(clock, monkeypatch):
    """Control: when the probe says the scan is done, the 5-minute heuristic
    still declares completion (no infinite waiting)."""
    now, fired = clock
    client = _FakeClient(scanning=False)
    mgr = _manager_with_client(client, fired, monkeypatch)

    _tick_to(mgr, now, 301)

    assert fired == ["completed"]


def test_probe_failure_means_still_scanning(clock, monkeypatch):
    """REGRESSION (H15): a failing probe must not let the clock declare
    completion — unknown scan state waits, it does not complete."""
    now, fired = clock
    client = _FakeClient(scanning=True, probe_raises=True)
    mgr = _manager_with_client(client, fired, monkeypatch)

    _tick_to(mgr, now, 301)

    assert not fired, "completion declared at 5 min with a failing scan-state probe"


def test_time_heuristic_fallback_when_probe_unavailable(clock, monkeypatch):
    """Control: when the client has no is_library_scanning() probe at all,
    the time heuristic remains the fallback (old behavior preserved)."""
    now, fired = clock

    class _NoProbeClient:
        pass

    mgr = _manager_with_client(_NoProbeClient(), fired, monkeypatch)
    _tick_to(mgr, now, 301)

    assert fired == ["completed"]
