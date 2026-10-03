"""Regression tests for the backend-review DOWNLOADING findings.

Each test fails on the pre-fix code and passes after the minimal fix:

* H1 — one failed slskd transfer poll restarts a healthy >90s download and
  blacklists its peer (fetch failure returned {} = "no transfers").
* H2 — stop/restart leaves two monitor threads double-processing ticks.
* H3 — cancellation does slskd network I/O while holding global tasks_lock.
* M1 — cross-source download aggregation is serial on the 1s monitor path.
* M2 — identical peer/path downloads overwrite each other's post-processing
  context (silent wrong tagging/filing).
* M3 — atomic-publish retry leaves a stale final_file_path after a failed
  rollback.
* L1 — album-bundle staging rmtree runs under the global tasks_lock.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass

import pytest

from core.downloads import atomic_album_publish as ap
from core.downloads import candidates as dc
from core.downloads import monitor as dm
from core.downloads import post_processing as pp
from core.download_engine.engine import DownloadEngine
from core.runtime_state import (
    download_batches,
    download_tasks,
    matched_downloads_context,
    tasks_lock,
)


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    download_tasks.clear()
    download_batches.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    download_batches.clear()
    matched_downloads_context.clear()


def _legacy_key_fn(u, f, task_id=None):
    """Test double mirroring the fixed _make_context_key contract."""
    if task_id:
        return f"{u}::{task_id}::{f}"
    return f"{u}::{f}"


# ---------------------------------------------------------------------------
# H1 — failed transfer poll must not read as "no transfers"
# ---------------------------------------------------------------------------

class _ExplodingSoulseekClient:
    base_url = "http://localhost:9999"

    async def _make_request(self, *args, **kwargs):
        raise RuntimeError("simulated slskd hiccup")


class _ExplodingEngine:
    async def get_all_downloads(self, exclude=()):
        raise RuntimeError("simulated engine hiccup")


class _ExplodingOrchestrator:
    engine = _ExplodingEngine()

    def client(self, name):
        return _ExplodingSoulseekClient()

    async def _make_request(self, *args, **kwargs):
        raise RuntimeError("simulated slskd hiccup")


def _seed_old_downloading_task(tasks, batches):
    now = time.time()
    tasks["t1"] = {
        "track_info": {"name": "Some Track"},
        "username": "good_peer",
        "filename": "Some Artist - Some Track.mp3",
        "download_id": "dl-1",
        "status": "downloading",
        "status_change_time": now - 120,  # >90s: "stuck" if the poll reads {}
    }
    batches["b1"] = {"queue": ["t1"]}


def test_h1_fetch_failure_returns_sentinel_not_empty_dict(monkeypatch):
    """The poll itself failing must be distinguishable from 'no transfers'."""
    monkeypatch.setattr(dm.config_manager, "get", lambda key, default=None:
                        "soulseek" if key == "download_source.mode" else default)
    monkeypatch.setattr(dm, "download_orchestrator", _ExplodingOrchestrator())
    monkeypatch.setattr(dm, "_make_context_key", _legacy_key_fn)
    mon = dm.WebUIDownloadMonitor()
    mon.monitoring = True

    result = mon._get_live_transfers()

    assert result is dm._LIVE_TRANSFERS_FETCH_FAILED
    assert result != {}


def test_h1_failed_poll_does_not_restart_healthy_download(monkeypatch):
    """End to end: a failed poll leaves a healthy >90s download alone."""
    monkeypatch.setattr(dm.config_manager, "get", lambda key, default=None:
                        "soulseek" if key == "download_source.mode" else default)
    monkeypatch.setattr(dm, "download_orchestrator", _ExplodingOrchestrator())
    monkeypatch.setattr(dm, "_make_context_key", _legacy_key_fn)
    tasks, batches = {}, {}
    monkeypatch.setattr(dm, "download_tasks", tasks)
    monkeypatch.setattr(dm, "download_batches", batches)
    monkeypatch.setattr(dm, "tasks_lock", threading.Lock())
    _seed_old_downloading_task(tasks, batches)

    mon = dm.WebUIDownloadMonitor()
    mon.monitoring = True
    mon.monitored_batches.add("b1")
    mon._check_all_downloads()

    assert tasks["t1"]["status"] == "downloading"
    assert "stuck_retry_count" not in tasks["t1"]
    assert tasks["t1"].get("download_id") == "dl-1"
    assert "used_sources" not in tasks["t1"]  # peer must not be blacklisted


def test_h1_engine_poll_failure_keeps_transfers_unknown(monkeypatch):
    """Streaming-engine failures must not restart transfers as if they vanished."""
    monkeypatch.setattr(dm.config_manager, "get", lambda key, default=None:
                        "youtube" if key == "download_source.mode" else default)
    monkeypatch.setattr(dm, "download_orchestrator", _ExplodingOrchestrator())
    mon = dm.WebUIDownloadMonitor()
    mon.monitoring = True
    assert mon._get_live_transfers() is dm._LIVE_TRANSFERS_FETCH_FAILED


# ---------------------------------------------------------------------------
# H2 — stop/restart must not leave two monitor threads ticking
# ---------------------------------------------------------------------------

def test_h2_restart_leaves_single_monitor_thread(monkeypatch):
    real_sleep = time.sleep
    tick_idents = []
    tick_started = threading.Event()

    def slow_check():
        tick_started.set()
        tick_idents.append(threading.get_ident())
        real_sleep(1.5)  # mid-tick when stop+restart fires

    mon = dm.WebUIDownloadMonitor()
    monkeypatch.setattr(mon, "_check_all_downloads", slow_check)
    mon.start_monitoring("b1")
    assert tick_started.wait(timeout=5)
    t1 = mon.monitor_thread

    mon.stop_monitoring("b1")
    assert not t1.is_alive(), "stop must join the old thread (bounded)"
    mark = len(tick_idents)
    mon.start_monitoring("b2")
    real_sleep(2.0)
    try:
        assert len(set(tick_idents[mark:])) == 1, (
            f"two threads ticked after restart: {set(tick_idents[mark:])}"
        )
    finally:
        mon.shutdown()


# ---------------------------------------------------------------------------
# H3 — cancel network I/O must run outside tasks_lock
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    username: str = "user1"
    filename: str = "song.flac"
    confidence: float = 0.9
    size: int = 1000
    title: str = "Song"
    artist: str = "Artist"
    album: str = "Album"
    quality_score: float = 0.0
    upload_speed: int = 0
    queue_length: int = 0
    free_upload_slots: int = 0


@dataclass
class _Track:
    name: str = "Song Title"
    album: str = "Album Name"
    artists: list = None
    id: str = "spt-1"

    def __post_init__(self):
        if self.artists is None:
            self.artists = ["Artist Name"]


class _SlowCancelSoulseek:
    """cancel_download simulates a 1.5s slskd API round-trip."""

    def __init__(self, delay=1.5):
        self.delay = delay
        self.cancel_calls = []
        self.cancel_started = threading.Event()

    async def download(self, username, filename, size, *, quality_profile_id=None):
        return "dl-1"

    async def cancel_download(self, download_id, username, remove=True):
        self.cancel_started.set()
        time.sleep(self.delay)
        self.cancel_calls.append((download_id, username, remove))
        return True


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_h3_cancel_after_start_releases_tasks_lock():
    soulseek = _SlowCancelSoulseek(delay=1.5)
    completed = []
    deps = dc.CandidatesDeps(
        download_orchestrator=soulseek,
        spotify_client=None,
        run_async=_run_async,
        get_database=lambda: None,
        update_task_status=lambda task_id, status: None,
        make_context_key=_legacy_key_fn,
        on_download_completed=lambda *a, **k: completed.append((a, k)),
    )
    download_tasks["t-cancel"] = {"status": "pending", "track_info": {},
                                 "used_sources": set()}

    async def download_then_user_cancels(username, filename, size,
                                         *, quality_profile_id=None):
        download_id = await _SlowCancelSoulseek.download(
            soulseek, username, filename, size,
            quality_profile_id=quality_profile_id)
        # Simulate the user hitting cancel while the download starts.
        with tasks_lock:
            download_tasks["t-cancel"]["status"] = "cancelled"
        return download_id

    soulseek.download = download_then_user_cancels

    result = {}
    worker = threading.Thread(
        target=lambda: result.update(
            rc=dc.attempt_download_with_candidates(
                "t-cancel", [_Candidate()], _Track(),
                batch_id="b1", deps=deps)),
        daemon=True,
    )
    worker.start()
    assert soulseek.cancel_started.wait(timeout=10), "cancel never started"

    start = time.monotonic()
    acquired = tasks_lock.acquire(timeout=5)
    elapsed = time.monotonic() - start
    try:
        assert acquired, "tasks_lock never became available"
        # The 1.5s slskd cancel must NOT block the global lock.
        assert elapsed < 0.8, (
            f"monitor tick blocked on tasks_lock for {elapsed:.2f}s during cancel"
        )
    finally:
        if acquired:
            tasks_lock.release()
    worker.join(timeout=10)

    assert result.get("rc") is False
    assert soulseek.cancel_calls == [("dl-1", "user1", True)], \
        "the cancel must still be issued, just outside the lock"
    assert completed and completed[0][0][:2] == ("b1", "t-cancel") \
        and completed[0][1].get("success") is False  # on_download_completed(..., success=False)


# ---------------------------------------------------------------------------
# M1 — engine aggregation must fan out concurrently
# ---------------------------------------------------------------------------

class _SlowPlugin:
    def __init__(self, name, delay=0.2, rows=()):
        self._name = name
        self._delay = delay
        self._rows = list(rows)

    async def get_all_downloads(self):
        await asyncio.sleep(self._delay)
        return list(self._rows)


def test_m1_get_all_downloads_runs_plugins_concurrently():
    engine = DownloadEngine()
    for i in range(3):
        engine.register_plugin(f"src{i}", _SlowPlugin(f"src{i}"))

    start = time.monotonic()
    rows = asyncio.run(engine.get_all_downloads())
    elapsed = time.monotonic() - start

    assert rows == []
    assert elapsed < 0.5, f"serial aggregation took {elapsed:.2f}s for 3x0.2s plugins"


def test_m1_plugin_failure_does_not_break_aggregation():
    engine = DownloadEngine()

    class _Boom:
        async def get_all_downloads(self):
            raise RuntimeError("boom")

    engine.register_plugin("boom", _Boom())
    engine.register_plugin("ok", _SlowPlugin("ok", delay=0, rows=["row"]))
    assert asyncio.run(engine.get_all_downloads()) == ["row"]


# ---------------------------------------------------------------------------
# M2 — same peer/path tasks must not share one context entry
# ---------------------------------------------------------------------------

def _context_for(username, filename, task_id):
    """Mirror the production read order: task-scoped key, then legacy key."""
    scoped = _legacy_key_fn(username, filename, task_id)
    ctx = matched_downloads_context.get(scoped)
    if ctx is None:
        ctx = matched_downloads_context.get(_legacy_key_fn(username, filename))
    return ctx


def test_m2_colliding_tasks_keep_separate_contexts():
    deps = dc.CandidatesDeps(
        download_orchestrator=_SlowCancelSoulseek(delay=0),
        spotify_client=None,
        run_async=_run_async,
        get_database=lambda: None,
        update_task_status=lambda task_id, status: None,
        make_context_key=_legacy_key_fn,
        on_download_completed=lambda *a, **k: None,
    )
    download_tasks["tA"] = {"status": "pending", "track_info": {},
                            "used_sources": set()}
    download_tasks["tB"] = {"status": "pending", "track_info": {},
                            "used_sources": set()}
    candidate = _Candidate(username="user1", filename="same.flac", confidence=0.95)

    assert dc.attempt_download_with_candidates(
        "tA", [candidate], _Track(), batch_id="b1", deps=deps) is True
    assert dc.attempt_download_with_candidates(
        "tB", [candidate], _Track(), batch_id="b2", deps=deps) is True

    ctx_a = _context_for("user1", "same.flac", "tA")
    ctx_b = _context_for("user1", "same.flac", "tB")
    assert ctx_a is not None and ctx_b is not None
    assert ctx_a is not ctx_b
    assert ctx_a["task_id"] == "tA" and ctx_a["batch_id"] == "b1"
    assert ctx_b["task_id"] == "tB" and ctx_b["batch_id"] == "b2"


def test_m2_no_collision_keeps_legacy_key():
    """Without a collision the legacy username::path key is untouched."""
    deps = dc.CandidatesDeps(
        download_orchestrator=_SlowCancelSoulseek(delay=0),
        spotify_client=None,
        run_async=_run_async,
        get_database=lambda: None,
        update_task_status=lambda task_id, status: None,
        make_context_key=_legacy_key_fn,
        on_download_completed=lambda *a, **k: None,
    )
    download_tasks["tSolo"] = {"status": "pending", "track_info": {},
                               "used_sources": set()}
    assert dc.attempt_download_with_candidates(
        "tSolo", [_Candidate(filename="solo.flac")], _Track(),
        batch_id="b1", deps=deps) is True
    assert "user1::solo.flac" in matched_downloads_context
    assert matched_downloads_context["user1::solo.flac"]["task_id"] == "tSolo"


# ---------------------------------------------------------------------------
# M3 — atomic-publish retry must not leave a stale final_file_path
# ---------------------------------------------------------------------------

def _flaky_mover_factory(state, staged_a, staged_b, final_a):
    import core.imports.file_ops as file_ops
    real_move = file_ops.safe_move_file

    def flaky_move(src, dst):
        if state["fail_b"] and os.path.basename(src) == "b.flac" and src == staged_b:
            raise OSError("simulated move failure for b.flac")
        if state["fail_rollback_a"] and src == final_a and dst == staged_a:
            raise OSError("simulated rollback failure for a.flac")
        return real_move(src, dst)

    return flaky_move


def test_m3_retry_remaps_stale_final_file_path(monkeypatch, tmp_path):
    import core.downloads.lifecycle as lc
    import core.imports.file_ops as file_ops

    transfer = tmp_path / "transfer"
    staging_root = ap.staging_root_for_batch(str(transfer), "batch1")
    staged_a = os.path.join(staging_root, "Artist", "Album", "a.flac")
    staged_b = os.path.join(staging_root, "Artist", "Album", "b.flac")
    for p in (staged_a, staged_b):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x")
    final_a = os.path.join(str(transfer), "Artist", "Album", "a.flac")
    final_b = os.path.join(str(transfer), "Artist", "Album", "b.flac")

    state = {"fail_b": True, "fail_rollback_a": True}
    monkeypatch.setattr(file_ops, "safe_move_file",
                        _flaky_mover_factory(state, staged_a, staged_b, final_a))
    # On this branch a publish that moves a file the catalogue does not know
    # is rolled back (L2-002); this test is about the task remap, so every
    # file is one the catalogue knows.
    import core.downloads.atomic_recovery as atomic_recovery
    monkeypatch.setattr(atomic_recovery, "make_db_path_updater", lambda db, **kw: lambda old, new: 1)

    batch = {
        "_atomic_active": True,
        "_atomic_staging_root": staging_root,
        "_atomic_transfer_dir": str(transfer),
        "queue": ["tA", "tB"],
        "_consistency_files": [],
    }
    tasks = {
        "tA": {"final_file_path": staged_a},
        "tB": {"final_file_path": staged_b},
    }
    monkeypatch.setattr(lc, "download_tasks", tasks)

    # Attempt 1: B's move fails; rolling A back also fails → A is live at
    # final_a while its task still pointed at the (now gone) staging path.
    # The healing remap fires right here: the file genuinely is at final_a.
    assert lc._publish_atomic_album("batch1", batch, deps=None) is False
    assert os.path.exists(final_a)
    assert tasks["tA"]["final_file_path"] == final_a

    # Attempt 2 (healing loop): only B is still staged; it publishes fine.
    # A's remap must persist (and must not be clobbered by this retry).
    state.update(fail_b=False, fail_rollback_a=False)
    assert lc._publish_atomic_album("batch1", batch, deps=None) is True

    assert tasks["tB"]["final_file_path"] == final_b
    assert tasks["tA"]["final_file_path"] == final_a
    assert os.path.exists(tasks["tA"]["final_file_path"])


# ---------------------------------------------------------------------------
# L1 — album-bundle staging rmtree must run outside tasks_lock
# ---------------------------------------------------------------------------

def _lifecycle_deps(monitor=None):
    import core.downloads.lifecycle as lc

    class _Cfg:
        def get(self, key, default=None):
            return default

    return lc.LifecycleDeps(
        config_manager=_Cfg(),
        automation_engine=None,
        download_monitor=monitor,
        repair_worker=None,
        mb_worker=None,
        is_shutting_down=lambda: False,
        get_batch_lock=lambda bid: threading.Lock(),
        submit_download_track_worker=lambda *a: None,
        submit_failed_to_wishlist=lambda bid: None,
        submit_failed_to_wishlist_with_auto_completion=lambda bid: None,
        process_failed_to_wishlist=lambda bid: None,
        process_failed_to_wishlist_with_auto_completion=lambda bid: None,
        get_track_artist_name=lambda *a: "",
        check_and_remove_from_wishlist=lambda *a: False,
        regenerate_batch_m3u=lambda *a: None,
        youtube_playlist_states={},
        tidal_discovery_states={},
        deezer_discovery_states={},
        spotify_public_discovery_states={},
        ensure_wishlist_track_format=lambda t: t,
    )


class _FakeMonitor:
    def __init__(self):
        self.stopped = []

    def stop_monitoring(self, batch_id):
        self.stopped.append(batch_id)


def test_l1_staging_cleanup_runs_outside_tasks_lock(monkeypatch, tmp_path):
    import core.downloads.lifecycle as lc

    staging = tmp_path / "batch1"
    (staging / "sub").mkdir(parents=True)
    (staging / "sub" / "x.flac").write_bytes(b"x")
    batch = {
        "phase": "downloading",
        "queue": [],
        "playlist_name": "P",
        "album_bundle_private_staging": True,
        "album_bundle_source": "soulseek",
        "album_bundle_staging_path": str(staging),
    }

    held_during_cleanup = []
    real_cleanup = lc._cleanup_private_album_bundle_staging

    def spy(batch_id, batch):
        got = tasks_lock.acquire(blocking=False)
        held_during_cleanup.append(not got)
        if got:
            tasks_lock.release()
        return real_cleanup(batch_id, batch)

    monkeypatch.setattr(lc, "_cleanup_private_album_bundle_staging", spy)
    monitor = _FakeMonitor()
    deps = _lifecycle_deps(monitor=monitor)

    with tasks_lock:
        outcome = lc._mark_batch_complete(
            "batch1", batch, deps, queue=[], finished_count=0, tag="[Test]")

    # The batch is terminal but the staging tree must still be there: the
    # rmtree no longer runs under the global lock.
    assert staging.exists(), "staging rmtree ran under tasks_lock"
    lc._run_batch_completion_side_effects("batch1", batch, deps, outcome,
                                          tag="[Test]")
    assert not staging.exists()
    assert held_during_cleanup == [False]
    assert monitor.stopped == ["batch1"]
