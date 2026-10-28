"""#1905: idle CPU — the video download monitor polled an empty queue every
3s (each pass opening fresh SQLite connections), the static cache-bust scan
re-walked webui/static on every template render, and the Docker healthcheck
rendered / every 30s.

These tests pin the three fixes:
- the monitor reports whether it found work, and its loop sleeps the idle
  interval (not the active one) when it did not — with the idle sleep
  interruptible via the wake event every grab's ensure_started() sets;
- the static cache-bust value is computed at most once per TTL (and never
  cached on a scan error, and never cached at all in dev no-cache mode);
- both healthchecks probe the lightweight /api/setup/status endpoint
  instead of rendering the index page, and that endpoint is allowlisted
  by the login / launch-PIN gates so the probe answers 200 in every mode.
"""

import os
from pathlib import Path

from core.video import download_monitor as monitor


class _EmptyDB:
    def get_active_video_downloads(self):
        return []


class _BusyDB:
    def get_active_video_downloads(self):
        return [{"id": 1, "source": "youtube", "status": "queued"}]


def test_tick_reports_idle_when_queue_empty():
    assert monitor._tick(_EmptyDB()) is False


def test_tick_reports_work_when_rows_in_flight():
    # A YouTube row is not polled by _tick itself (its worker owns it), but
    # its presence still means work is in flight — no idle backoff.
    assert monitor._tick(_BusyDB()) is True


def test_tick_reports_work_for_searching_only_row():
    # A 'searching' YouTube row is skipped by both _tick branches, but it is
    # still an active row — the bool(all_active) authority keeps the cadence.
    class _DB:
        def get_active_video_downloads(self):
            return [{"id": 7, "source": "youtube", "status": "searching"}]

    assert monitor._tick(_DB()) is True


def test_tick_main_path_processes_a_client_row_and_reports_work(monkeypatch):
    from core.video import client_download

    row = {"id": 1, "source": "torrent", "status": "downloading",
           "client_ref": "hash123", "progress": 0, "progress_at": None}

    class _DB:
        def get_active_video_downloads(self):
            return [row]

        def update_video_download(self, *a, **kw):
            pass

    routed = []
    monkeypatch.setattr(client_download, "process_active_client_download",
                        lambda dl, **kw: routed.append(dl["id"]) or {})
    assert monitor._tick(_DB()) is True
    assert routed == [1]


def test_recover_youtube_reports_idle_and_work(monkeypatch):
    import core.video.youtube_download as ytd

    monkeypatch.setattr(ytd, "recover_and_pump", lambda provider: (0, 0))
    assert monitor._recover_youtube(lambda: _EmptyDB()) is False

    monkeypatch.setattr(ytd, "recover_and_pump", lambda provider: (0, 1))
    assert monitor._recover_youtube(lambda: _EmptyDB()) is True

    monkeypatch.setattr(ytd, "recover_and_pump", lambda provider: (2, 0))
    assert monitor._recover_youtube(lambda: _EmptyDB()) is True


def test_recover_youtube_failure_reports_work_so_it_retries_fast(monkeypatch):
    import core.video.youtube_download as ytd

    def _boom(provider):
        raise RuntimeError("yt hiccup")

    monkeypatch.setattr(ytd, "recover_and_pump", _boom)
    assert monitor._recover_youtube(lambda: _EmptyDB()) is True


def test_idle_interval_is_slower_than_active_interval():
    assert monitor._IDLE_INTERVAL > monitor._INTERVAL


class _FakeWake:
    """Stands in for the monitor's wake Event: records the timeout _run
    asked for and stops the loop after `stop_after` waits."""

    def __init__(self, stop_after=1):
        self.timeouts = []
        self.stop_after = stop_after
        self.is_set = False

    def clear(self):
        self.is_set = False

    def set(self):
        self.is_set = True

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if len(self.timeouts) >= self.stop_after:
            raise _StopLoop
        return self.is_set


class _StopLoop(Exception):
    pass


def _run_one(monkeypatch, *, yt=False, tick=False, provider_none=False,
             tick_raises=False):
    wake = _FakeWake(stop_after=1)
    monkeypatch.setattr(monitor, "_wake_event", wake)
    monkeypatch.setattr(monitor, "_recover_youtube", lambda provider: yt)
    if tick_raises:
        def _tick(db_):
            raise RuntimeError("tick failed")
    else:
        _tick = lambda db_: tick  # noqa: E731
    monkeypatch.setattr(monitor, "_tick", _tick)
    monkeypatch.setattr(monitor, "_sweep_import_temps", lambda db_: None)
    monkeypatch.setattr(monitor, "requeue_runtime_rejects", lambda db_: 0)
    provider = (lambda: None) if provider_none else (lambda: _EmptyDB())
    try:
        monitor._run(provider)
    except _StopLoop:
        pass
    return wake.timeouts


def test_run_sleeps_idle_interval_when_no_work(monkeypatch):
    assert _run_one(monkeypatch) == [monitor._IDLE_INTERVAL]


def test_run_sleeps_active_interval_when_tick_worked(monkeypatch):
    assert _run_one(monkeypatch, tick=True) == [monitor._INTERVAL]


def test_run_sleeps_active_interval_when_only_youtube_worked(monkeypatch):
    # The OR that matters: a YouTube-only pickup keeps the fast cadence
    # even though _tick saw nothing.
    assert _run_one(monkeypatch, yt=True, tick=False) == [monitor._INTERVAL]


def test_run_sleeps_active_interval_after_a_failed_tick(monkeypatch):
    assert _run_one(monkeypatch, tick_raises=True) == [monitor._INTERVAL]


def test_run_backs_off_when_provider_returns_no_db(monkeypatch):
    assert _run_one(monkeypatch, provider_none=True) == [monitor._IDLE_INTERVAL]


class _StopThread(BaseException):
    """Sentinel to kill the real monitor thread at the end of the wake test.
    BaseException-derived on purpose: _run catches `Exception`, so this
    propagates out of the loop and the thread actually exits (a stray
    monitor thread would otherwise keep ticking through the whole session
    and pick up later tests' monkeypatches)."""


def test_run_wakes_promptly_on_a_real_event_set(monkeypatch):
    # End-to-end with the REAL threading.Event (the fake-event tests above
    # only prove the timeout argument): a monitor asleep in its idle wait
    # must run its next pass at once when a grab sets the event.
    import threading
    import time as _time

    ticks = []
    thread = None

    def _tick(db):
        # in a full run another test may have left a real monitor thread
        # alive; it calls this same patched _tick and shares the wake
        # event, so only this test's thread counts
        if threading.current_thread() is not thread:
            return False
        ticks.append(1)
        if len(ticks) >= 2:
            raise _StopThread
        return False

    monkeypatch.setattr(monitor, "_IDLE_INTERVAL", 3600)
    monkeypatch.setattr(monitor, "_tick", _tick)
    monkeypatch.setattr(monitor, "_recover_youtube", lambda provider: False)
    monkeypatch.setattr(monitor, "_sweep_import_temps", lambda db: None)
    monkeypatch.setattr(monitor, "requeue_runtime_rejects", lambda db: 0)
    # The sentinel's escape is the intended thread exit; keep pytest from
    # reporting it as an unhandled thread exception.
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    monitor._wake_event.clear()
    thread = threading.Thread(target=monitor._run, args=(lambda: _EmptyDB(),),
                              daemon=True)
    thread.start()
    try:
        deadline = _time.monotonic() + 5
        while len(ticks) < 1 and _time.monotonic() < deadline:
            _time.sleep(0.02)
        assert len(ticks) == 1      # first pass done, now asleep for 3600s
        monitor._wake_event.set()   # a grab lands
    finally:
        # Whatever happened above, wake + join so no live monitor thread
        # leaks into the rest of the session.
        monitor._wake_event.set()
        thread.join(timeout=3)
    assert not thread.is_alive()    # woke at once and exited via the sentinel
    assert len(ticks) == 2
    monitor._wake_event.clear()     # leave the shared event tidy


def test_ensure_started_wakes_an_already_started_monitor(monkeypatch):
    wake = _FakeWake()
    monkeypatch.setattr(monitor, "_wake_event", wake)
    monkeypatch.setattr(monitor, "_started", True)
    monkeypatch.setattr(monitor, "_db_provider", None)   # restored after the test
    monitor.ensure_started(lambda: _EmptyDB())
    assert wake.is_set is True


def test_ensure_started_first_call_starts_thread_and_subtitles_once(monkeypatch):
    # The restructured ensure_started must keep the original semantics:
    # first call spawns the monitor thread + the subtitle worker exactly
    # once; later calls spawn neither (but still wake, tested above).
    import core.video.subtitles.worker as sub_worker

    spawned = []

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            spawned.append(name)

        def start(self):
            spawned.append("started")

    subtitle_calls = []
    monkeypatch.setattr(monitor.threading, "Thread", _FakeThread)
    monkeypatch.setattr(sub_worker, "ensure_started",
                        lambda provider: subtitle_calls.append(1))
    monkeypatch.setattr(monitor, "_wake_event", _FakeWake())
    monkeypatch.setattr(monitor, "_started", False)
    monkeypatch.setattr(monitor, "_db_provider", None)

    monitor.ensure_started(lambda: _EmptyDB())
    assert spawned == ["video-download-monitor", "started"]
    assert subtitle_calls == [1]

    monitor.ensure_started(lambda: _EmptyDB())
    assert spawned == ["video-download-monitor", "started"]   # nothing new
    assert subtitle_calls == [1]


def test_ensure_started_with_wake_false_does_not_wake(monkeypatch):
    # The read-only downloads-page polls use wake=False: they may (re)start
    # the thread but must not interrupt its idle sleep.
    wake = _FakeWake()
    monkeypatch.setattr(monitor, "_wake_event", wake)
    monkeypatch.setattr(monitor, "_started", True)
    monkeypatch.setattr(monitor, "_db_provider", None)
    monitor.ensure_started(lambda: _EmptyDB(), wake=False)
    assert wake.is_set is False


def test_static_v_is_cached_within_ttl(monkeypatch):
    import web_server as ws

    calls = []

    def fake_compute():
        calls.append(1)
        return "v%d" % len(calls)

    monkeypatch.setattr(ws, "_compute_static_v", fake_compute)
    monkeypatch.setattr(ws, "_static_v_cache", None)
    monkeypatch.setattr(ws, "_static_v_cache_at", 0.0)
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", False)

    assert ws._get_static_v() == "v1"
    assert ws._get_static_v() == "v1"      # second call: no rescan
    assert len(calls) == 1

    # Once the TTL has passed the value is recomputed (a same-process
    # rebuild is picked up without a restart).
    monkeypatch.setattr(
        ws, "_static_v_cache_at",
        ws._static_v_cache_at - ws._STATIC_V_TTL_SECONDS - 1)
    assert ws._get_static_v() == "v2"
    assert len(calls) == 2


def test_static_v_scan_error_is_not_cached(monkeypatch):
    import web_server as ws

    calls = []

    def flaky_compute():
        calls.append(1)
        return None if len(calls) == 1 else "recovered"

    monkeypatch.setattr(ws, "_compute_static_v", flaky_compute)
    monkeypatch.setattr(ws, "_static_v_cache", None)
    monkeypatch.setattr(ws, "_static_v_cache_at", 0.0)
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", False)

    # The error falls back to the boot-time bust value for THIS render…
    assert ws._get_static_v() == ws._STATIC_CACHE_BUST
    # …but it is not cached: the very next call rescans and recovers.
    assert ws._get_static_v() == "recovered"
    assert len(calls) == 2


def test_static_v_scan_error_keeps_the_last_good_value(monkeypatch):
    import web_server as ws

    calls = []

    def flaky_compute():
        calls.append(1)
        return None if len(calls) == 1 else "new"

    monkeypatch.setattr(ws, "_compute_static_v", flaky_compute)
    monkeypatch.setattr(ws, "_static_v_cache", "good")
    monkeypatch.setattr(
        ws, "_static_v_cache_at",
        0.0)   # long expired, so every call below attempts a rescan
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", False)

    # A failed rescan serves the last good value, not the boot-time one…
    assert ws._get_static_v() == "good"
    # …and the failure is not latched: the next call rescans and recovers.
    assert ws._get_static_v() == "new"
    assert len(calls) == 2


def test_static_v_dev_no_cache_mode_never_caches(monkeypatch):
    import web_server as ws

    calls = []

    def fake_compute():
        calls.append(1)
        return "v%d" % len(calls)

    monkeypatch.setattr(ws, "_compute_static_v", fake_compute)
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", True)
    # Dev mode: every render rescans, exactly the pre-#1905 behavior the
    # edit-and-refresh promise relies on.
    assert ws._get_static_v() == "v1"
    assert ws._get_static_v() == "v2"


def test_compute_static_v_scans_css_and_js_only(tmp_path, monkeypatch):
    import web_server as ws

    (tmp_path / "a.css").write_text("x")
    (tmp_path / "b.js").write_text("x")
    (tmp_path / "c.txt").write_text("x")
    old = 1_000_000_000_000_000_000
    new = 2_000_000_000_000_000_000
    os.utime(tmp_path / "a.css", ns=(old, old))
    os.utime(tmp_path / "b.js", ns=(new, new))
    os.utime(tmp_path / "c.txt", ns=(new + 1, new + 1))   # newest, but not css/js
    monkeypatch.setattr(ws.app, "static_folder", str(tmp_path))
    assert ws._compute_static_v() == str(new)


def test_compute_static_v_empty_dir_falls_back(tmp_path, monkeypatch):
    import web_server as ws

    monkeypatch.setattr(ws.app, "static_folder", str(tmp_path))
    assert ws._compute_static_v() == ws._STATIC_CACHE_BUST


def test_inject_static_cache_bust_merges_appearance_context(monkeypatch):
    import web_server as ws

    monkeypatch.setattr(ws, "_static_v_cache", None)    # restored after the test
    monkeypatch.setattr(ws, "_static_v_cache_at", 0.0)
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", False)
    ctx = ws._inject_static_cache_bust()
    assert ctx["static_v"]
    assert "initial_accent_color" in ctx


def test_static_v_reflects_a_file_change_after_the_ttl(tmp_path, monkeypatch):
    # End to end through _get_static_v (not the stubbed compute): a static
    # file changed mid-process keeps the cached value within the TTL and
    # is reflected once the TTL expires — the documented #1905 trade-off.
    import web_server as ws

    f = tmp_path / "a.css"
    f.write_text("x")
    old = 1_000_000_000_000_000_000
    new = 2_000_000_000_000_000_000
    os.utime(f, ns=(old, old))
    monkeypatch.setattr(ws.app, "static_folder", str(tmp_path))
    monkeypatch.setattr(ws, "_static_v_cache", None)
    monkeypatch.setattr(ws, "_static_v_cache_at", 0.0)
    monkeypatch.setattr(ws, "DEV_STATIC_NO_CACHE", False)

    assert ws._get_static_v() == str(old)
    os.utime(f, ns=(new, new))
    assert ws._get_static_v() == str(old)     # within the TTL: cached
    monkeypatch.setattr(
        ws, "_static_v_cache_at",
        ws._static_v_cache_at - ws._STATIC_V_TTL_SECONDS - 1)
    assert ws._get_static_v() == str(new)     # after the TTL: rescanned


def test_healthchecks_use_lightweight_endpoint():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "http://localhost:8008/api/setup/status" in dockerfile
    assert "http://localhost:8008/api/setup/status" in compose
    # The old render-the-index probes are gone in both spellings.
    assert "curl -f http://localhost:8008/" not in dockerfile.replace(
        "http://localhost:8008/api/setup/status", "")
    assert '"http://localhost:8008/"' not in compose
    assert '"http://localhost:8008"' not in compose


def test_healthcheck_probe_is_skipped_by_the_access_log_filter():
    # The probe runs every 30s: if the gunicorn access-log filter didn't
    # skip it, docker logs would gain ~2,880 lines a day. The filter used
    # to special-case only the old / probe.
    import pytest

    pytest.importorskip("gunicorn")   # in requirements.txt; absent in some dev envs
    from utils.gunicorn_logger import FilteredGunicornLogger

    logger = FilteredGunicornLogger.__new__(FilteredGunicornLogger)
    skip = logger._should_skip_access_log
    curl = {"HTTP_USER_AGENT": "curl/8.5.0"}
    browser = {"HTTP_USER_AGENT": "Mozilla/5.0"}

    assert skip({"PATH_INFO": "/api/setup/status", **curl}) is True
    assert skip({"PATH_INFO": "/", **curl}) is True
    # A real browser/API call to the same path is still logged.
    assert skip({"PATH_INFO": "/api/setup/status", **browser}) is False
    # curl to any other API path is still logged.
    assert skip({"PATH_INFO": "/api/library/tracks", **curl}) is False


def test_healthcheck_endpoint_is_allowlisted_by_both_gates():
    # The probe is cookieless curl: it must survive login mode and the
    # launch-PIN gate or Docker would mark a locked-but-healthy app down.
    from core.security.launch_lock import request_is_locked
    from core.security.login_gate import login_request_is_blocked
    from core.security.session_profile import no_profile_request_is_blocked

    assert login_request_is_blocked(
        "/api/setup/status", "GET", require_login=True,
        authenticated=False) is False
    assert request_is_locked(
        "/api/setup/status", "GET", require_pin=True,
        pin_verified=False) is False
    assert no_profile_request_is_blocked(
        "/api/setup/status", "GET") is False
