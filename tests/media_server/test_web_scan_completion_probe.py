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

def test_exact_state_finishes_fast_scan_before_five_minute_fallback(clock, monkeypatch):
    now, fired = clock

    class _ExactStateClient:
        def get_scan_state(self):
            return {"scanning": False, "last_scan": "new-finished-at"}

        def is_library_scanning(self):
            pytest.fail("exact state should finish before the five-minute probe")

    engine = SimpleNamespace(client=lambda name: _ExactStateClient())
    mgr = wsm.WebScanManager(media_server_engine=engine, delay_seconds=60)
    mgr._current_server_type = "music_assistant"
    mgr._scan_start_time = time.time()
    mgr._scan_baseline = {"scanning": False, "last_scan": "old-finished-at"}
    mgr.add_scan_completion_callback(lambda: fired.append("completed"))
    mgr._start_periodic_completion_check()

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == ["completed"]


class _TaskMonitorClient:
    def __init__(self, state):
        self.state = state
        self.calls = 0

    def get_scan_state(self):
        self.calls += 1
        return self.state

    def is_library_scanning(self):
        pytest.fail("Music Assistant task outcomes must not use the generic probe")


def _task_monitor_manager(client, fired, monkeypatch):
    engine = SimpleNamespace(client=lambda name: client)
    mgr = wsm.WebScanManager(media_server_engine=engine, delay_seconds=60)
    mgr._current_server_type = "music_assistant"
    mgr._scan_start_time = time.time()
    mgr._scan_in_progress = True
    mgr._scan_baseline = {"task_monitor": True, "scanning": False, "outcome": None}
    mgr.add_scan_completion_callback(lambda: fired.append("completed"))
    mgr._start_periodic_completion_check()
    return mgr


def test_unreadable_music_assistant_baseline_cannot_use_generic_success_fallback(
        clock, monkeypatch):
    now, _fired = clock
    database_updates = []

    class _Client:
        def __init__(self):
            self.reads = 0

        def get_scan_state(self):
            self.reads += 1
            if self.reads == 1:
                return None
            return {
                "task_monitor": True,
                "scanning": False,
                "outcome": "failed",
                "last_scan": "changed-task-signature",
            }

        def trigger_library_scan(self):
            return True

    client = _Client()
    engine = SimpleNamespace(client=lambda name: client)
    mgr = wsm.WebScanManager(media_server_engine=engine, delay_seconds=60)
    monkeypatch.setattr(mgr, "_get_active_media_client", lambda: (client, "music_assistant"))
    mgr.add_scan_completion_callback(lambda: database_updates.append("updated"))

    mgr._execute_scan()
    assert mgr._scan_baseline["task_monitor"] is True
    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert database_updates == []
    assert mgr.get_scan_status()["last_result"]["outcome"] == "failed"


def test_music_assistant_success_outcome_alone_runs_completion_callback(clock, monkeypatch):
    now, fired = clock
    client = _TaskMonitorClient({
        "task_monitor": True, "scanning": False, "outcome": "success",
    })
    mgr = _task_monitor_manager(client, fired, monkeypatch)

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == ["completed"]
    assert mgr.get_scan_status()["last_result"]["outcome"] == "success"


@pytest.mark.parametrize("outcome", [
    "failed", "cancelled", "unknown",
])
def test_music_assistant_non_success_outcomes_never_run_callbacks(
        clock, monkeypatch, outcome):
    now, fired = clock
    client = _TaskMonitorClient({
        "task_monitor": True, "scanning": False, "outcome": outcome,
    })
    mgr = _task_monitor_manager(client, fired, monkeypatch)

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == []
    assert mgr.get_scan_status()["last_result"]["outcome"] == outcome
    assert mgr.get_scan_status()["status"] == "idle"


def test_music_assistant_running_task_times_out_without_success_or_database_update(
        clock, monkeypatch):
    now, fired = clock
    client = _TaskMonitorClient({
        "task_monitor": True, "scanning": True, "outcome": "running",
    })
    mgr = _task_monitor_manager(client, fired, monkeypatch)
    mgr._max_scan_time = 1

    _tick_to(mgr, now, 2)

    assert fired == []
    assert mgr._scan_in_progress is False
    assert mgr.get_scan_status()["last_result"]["outcome"] == "timeout"


def test_music_assistant_unreadable_state_times_out_without_database_update(
        clock, monkeypatch):
    now, fired = clock
    client = _TaskMonitorClient(None)
    mgr = _task_monitor_manager(client, fired, monkeypatch)
    mgr._max_scan_time = 1

    _tick_to(mgr, now, 2)

    assert fired == []
    assert mgr._scan_in_progress is False
    assert mgr.get_scan_status()["last_result"]["outcome"] == "timeout"


def test_music_assistant_partial_success_still_runs_the_database_update(clock, monkeypatch):
    """#2115: partial_success means the sync finished with a few bad files.
    the library changed, so the update after the scan still runs."""
    now, fired = clock
    client = _TaskMonitorClient({
        "task_monitor": True, "scanning": False, "outcome": "partial_success",
    })
    mgr = _task_monitor_manager(client, fired, monkeypatch)

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == ["completed"]
    assert mgr.get_scan_status()["last_result"]["outcome"] == "partial_success"


@pytest.mark.parametrize("outcome", ["failed", "unknown"])
def test_downloads_during_a_failed_scan_still_get_a_follow_up(clock, monkeypatch, outcome):
    """#2115: a download that landed mid scan is scanned next time, whatever
    this scan's outcome was."""
    now, fired = clock
    client = _TaskMonitorClient({"task_monitor": True, "scanning": False, "outcome": outcome})
    mgr = _task_monitor_manager(client, fired, monkeypatch)
    mgr._downloads_during_scan = True
    asked = []
    monkeypatch.setattr(mgr, "request_scan", lambda reason, scope="full": asked.append(scope))

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == []
    assert asked == ["downloads"]


def test_a_sync_that_was_already_running_gets_one_follow_up_scan(clock, monkeypatch):
    """#2115: music assistant drops a sync request while that sync runs, so
    the scan asks again once the old run is done."""
    now, fired = clock
    client = _TaskMonitorClient({
        "task_monitor": True, "scanning": False, "outcome": "already_running",
    })
    mgr = _task_monitor_manager(client, fired, monkeypatch)
    asked = []
    monkeypatch.setattr(mgr, "request_scan", lambda reason, scope="full": asked.append(scope))

    _tick_to(mgr, now, wsm.WebScanManager.EXACT_POLL_SECONDS)

    assert fired == []
    assert asked == ["full"]
    assert mgr._last_scan_result["outcome"] == "already_running"


def test_plex_timeout_still_runs_the_database_update(clock, monkeypatch):
    """#2115 is music assistant only: plex, jellyfin and navidrome have no
    outcome to read, so a scan past the 30 minute cap still counts as done."""
    now, fired = clock
    mgr = _manager_with_client(_FakeClient(scanning=True), fired, monkeypatch)
    mgr._max_scan_time = 1

    _tick_to(mgr, now, 2)

    assert fired == ["completed"]
