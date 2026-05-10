"""Pin the bounded-executor + scan-lock concurrency model in
``AutoImportWorker``.

Pre-refactor (before 2026-05-09): manual "Scan Now" clicks spawned a
fresh `threading.Thread(target=_scan_cycle)` per click on top of the
worker's existing 60-second timer-driven scan. Emergent parallelism
with no upper bound, no shared queue, no graceful shutdown. Different
scan cycles raced on `_processing_paths` / `_folder_snapshots` state.

Post-refactor:
- ONE scan at a time (`_scan_lock` non-blocking acquire — duplicate
  triggers no-op).
- Per-candidate processing runs on a `ThreadPoolExecutor` (default 3
  workers, configurable via `auto_import.max_workers`).
- Both timer + manual triggers share `trigger_scan()` so they go
  through the same lock + executor.

These tests pin the CONCURRENCY CONTRACT, not the per-candidate
processing logic (which is covered separately by
``test_auto_import_live_progress.py`` etc.).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.auto_import_worker import AutoImportWorker, FolderCandidate


def _make_worker(max_workers: int = 3) -> AutoImportWorker:
    """Bare worker — for the executor/lock tests we don't need full
    db / config / process_callback dependencies."""
    return AutoImportWorker(
        database=MagicMock(),
        process_callback=MagicMock(),
        max_workers=max_workers,
    )


def _make_candidate(folder_hash: str = 'h1', name: str = 'TestAlbum') -> FolderCandidate:
    return FolderCandidate(
        path=f'/staging/{name}',
        name=name,
        audio_files=[f'/staging/{name}/01.flac'],
        folder_hash=folder_hash,
    )


# ---------------------------------------------------------------------------
# Pool configuration
# ---------------------------------------------------------------------------


def test_default_max_workers_is_three():
    """Match the existing pool patterns in this codebase
    (missing_download_executor, sync_executor, import_singles_executor
    all default to 3)."""
    w = _make_worker()
    assert w._max_workers == 3


def test_max_workers_configurable_via_constructor():
    w = _make_worker(max_workers=5)
    assert w._max_workers == 5


def test_max_workers_floors_at_one():
    """0 or negative pool size would deadlock anything submitted —
    floor at 1 so a misconfigured value still works."""
    w = _make_worker(max_workers=0)
    assert w._max_workers == 1


def test_max_workers_pulled_from_config_when_provided():
    config = MagicMock()
    config.get = MagicMock(side_effect=lambda key, default: 7 if key == 'auto_import.max_workers' else default)
    w = AutoImportWorker(
        database=MagicMock(),
        process_callback=MagicMock(),
        config_manager=config,
        max_workers=3,  # constructor default — overridden by config
    )
    assert w._max_workers == 7


# ---------------------------------------------------------------------------
# Scan lock — duplicate triggers no-op
# ---------------------------------------------------------------------------


def test_concurrent_triggers_only_one_scan_runs(monkeypatch):
    """Pre-refactor regression case: hitting "Scan Now" 5× in quick
    succession used to spawn 5 parallel scan cycles. Post-refactor:
    only one runs, the rest no-op via the non-blocking lock."""
    w = _make_worker()
    scan_count = 0
    scan_started = threading.Event()
    scan_can_finish = threading.Event()

    def fake_scan_and_submit():
        nonlocal scan_count
        scan_count += 1
        scan_started.set()
        scan_can_finish.wait(timeout=5)

    monkeypatch.setattr(w, '_scan_and_submit', fake_scan_and_submit)

    # Fire 5 trigger_scan calls in parallel
    threads = [threading.Thread(target=w.trigger_scan) for _ in range(5)]
    for t in threads:
        t.start()

    # Wait for the first scan to start
    assert scan_started.wait(timeout=5)
    # The other 4 should have already returned (lock was held)
    time.sleep(0.1)
    assert scan_count == 1, (
        f"Expected exactly 1 scan to run while the lock was held, got "
        f"{scan_count}. The non-blocking scan lock isn't gating "
        f"duplicate triggers."
    )

    # Release the held scan
    scan_can_finish.set()
    for t in threads:
        t.join(timeout=5)

    # No additional scans started after release (the 4 losers gave up,
    # didn't queue)
    assert scan_count == 1


def test_scan_after_previous_finishes_runs_normally(monkeypatch):
    """Lock releases when scan finishes — next trigger should acquire
    + run normally, not be permanently blocked."""
    w = _make_worker()
    scan_count = 0

    def fake_scan_and_submit():
        nonlocal scan_count
        scan_count += 1

    monkeypatch.setattr(w, '_scan_and_submit', fake_scan_and_submit)

    w.trigger_scan()
    w.trigger_scan()
    w.trigger_scan()

    assert scan_count == 3


# ---------------------------------------------------------------------------
# Executor — per-candidate parallelism
# ---------------------------------------------------------------------------


def test_candidates_dispatched_to_executor(monkeypatch):
    """Scan finds N candidates → submits N tasks to the executor pool.
    Pool runs them in parallel (up to max_workers). Each task ends up
    calling `_process_one_candidate`."""
    w = _make_worker(max_workers=3)
    w.start()  # initialises the executor

    try:
        candidates = [
            _make_candidate(folder_hash=f'h{i}', name=f'Album{i}')
            for i in range(5)
        ]
        monkeypatch.setattr(w, '_enumerate_folders', lambda staging: candidates)
        monkeypatch.setattr(w, '_resolve_staging_path', lambda: '/staging')
        monkeypatch.setattr('core.auto_import_worker.os.path.isdir', lambda p: True)
        monkeypatch.setattr(w, '_is_already_processed', lambda h: False)
        monkeypatch.setattr(w, '_is_folder_stable', lambda c: True)

        processed = []
        processed_lock = threading.Lock()

        def fake_process(candidate):
            with processed_lock:
                processed.append(candidate.folder_hash)

        monkeypatch.setattr(w, '_process_one_candidate', fake_process)

        w.trigger_scan()

        # Wait for all 5 to finish (executor runs async)
        deadline = time.time() + 5
        while len(processed) < 5 and time.time() < deadline:
            time.sleep(0.05)

        assert sorted(processed) == [f'h{i}' for i in range(5)]
    finally:
        w.stop()


def test_pool_runs_candidates_in_parallel():
    """With max_workers=3, the pool should run up to 3 candidates
    concurrently — proves the bounded parallelism the user asked for."""
    w = _make_worker(max_workers=3)
    w.start()
    try:
        # Submit 3 long-running tasks directly to the executor and
        # confirm they run concurrently.
        in_flight = [0]
        peak_in_flight = [0]
        lock = threading.Lock()
        proceed = threading.Event()

        def slow_task():
            with lock:
                in_flight[0] += 1
                if in_flight[0] > peak_in_flight[0]:
                    peak_in_flight[0] = in_flight[0]
            proceed.wait(timeout=2)
            with lock:
                in_flight[0] -= 1

        futures = [w._executor.submit(slow_task) for _ in range(3)]
        # Give them a beat to start
        time.sleep(0.2)
        assert peak_in_flight[0] == 3, (
            f"Expected 3 concurrent tasks, peaked at {peak_in_flight[0]}"
        )
        proceed.set()
        for f in futures:
            f.result(timeout=2)
    finally:
        w.stop()


def test_executor_max_workers_caps_concurrency():
    """max_workers=2 must NOT allow 3 concurrent tasks. Bounded
    parallelism — predictable system load."""
    w = _make_worker(max_workers=2)
    w.start()
    try:
        in_flight = [0]
        peak = [0]
        lock = threading.Lock()
        proceed = threading.Event()

        def slow_task():
            with lock:
                in_flight[0] += 1
                if in_flight[0] > peak[0]:
                    peak[0] = in_flight[0]
            proceed.wait(timeout=2)
            with lock:
                in_flight[0] -= 1

        futures = [w._executor.submit(slow_task) for _ in range(5)]
        time.sleep(0.3)
        assert peak[0] == 2, (
            f"max_workers=2 should cap concurrency at 2, peaked at {peak[0]}"
        )
        proceed.set()
        for f in futures:
            f.result(timeout=2)
    finally:
        w.stop()


# ---------------------------------------------------------------------------
# Submitted-hashes dedup across triggers
# ---------------------------------------------------------------------------


def test_candidate_only_submitted_once_across_concurrent_scans(monkeypatch):
    """Scenario: scan A submits candidate X to the pool; pool worker
    is mid-processing. Scan B (manual trigger) enumerates again and
    sees X — must NOT re-submit. `_submitted_hashes` set + lock
    prevents double-submission."""
    w = _make_worker()
    w.start()

    try:
        cand = _make_candidate(folder_hash='shared-hash')
        monkeypatch.setattr(w, '_enumerate_folders', lambda staging: [cand])
        monkeypatch.setattr(w, '_resolve_staging_path', lambda: '/staging')
        monkeypatch.setattr('core.auto_import_worker.os.path.isdir', lambda p: True)
        monkeypatch.setattr(w, '_is_already_processed', lambda h: False)
        monkeypatch.setattr(w, '_is_folder_stable', lambda c: True)

        process_count = 0
        process_lock = threading.Lock()
        process_can_finish = threading.Event()

        def slow_process(candidate):
            nonlocal process_count
            with process_lock:
                process_count += 1
            process_can_finish.wait(timeout=5)

        monkeypatch.setattr(w, '_process_one_candidate', slow_process)

        # First scan submits the candidate
        w.trigger_scan()
        # Wait for processing to start
        time.sleep(0.1)

        # Second scan WHILE first is processing — must not re-submit
        w.trigger_scan()
        time.sleep(0.1)
        assert process_count == 1, (
            f"Expected only 1 process call (dedup active), got {process_count}"
        )

        process_can_finish.set()
        time.sleep(0.2)

        # After the first finishes, the candidate still has the same
        # hash + would be `_is_already_processed`, but our mock returns
        # False — even so, the post-finally `discard` should let a
        # third trigger re-pick if needed. Here we just verify dedup
        # held while in flight.
    finally:
        process_can_finish.set()
        w.stop()


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def test_stop_waits_for_inflight_pool_work():
    """`stop()` must call `executor.shutdown(wait=True)` so in-flight
    file moves / tag writes / DB inserts complete before shutdown
    reports done. Otherwise interrupted writes corrupt state."""
    w = _make_worker()
    w.start()

    finished = threading.Event()

    def slow_task():
        time.sleep(0.3)
        finished.set()

    w._executor.submit(slow_task)

    # Stop immediately — should block until slow_task completes
    w.stop()

    assert finished.is_set(), (
        "stop() returned before in-flight pool work finished — "
        "executor shutdown(wait=True) is missing or broken"
    )


# ---------------------------------------------------------------------------
# Per-candidate state isolation under parallel pool workers
# ---------------------------------------------------------------------------
#
# Pre-refactor `_current_folder` / `_current_track_*` / `_current_status` were
# scalar fields on the worker. Three pool workers running in parallel would
# stomp each other's values — UI showed "Processing AlbumA, track 7/14:
# SongFromAlbumB" interleaved garbage. These tests pin the per-candidate
# isolation introduced by the `_active_imports` dict + `_active_lock`.


def test_concurrent_candidates_dont_stomp_each_other():
    """Two pool workers updating their own candidate state must not
    interfere — each candidate's track_index / track_name / folder_name
    is read back exactly as written for that hash."""
    w = _make_worker(max_workers=2)
    w.start()
    try:
        cand_a = _make_candidate(folder_hash='hA', name='AlbumA')
        cand_b = _make_candidate(folder_hash='hB', name='AlbumB')

        # Register both
        w._register_active(cand_a, status='processing')
        w._register_active(cand_b, status='processing')

        ready = threading.Barrier(2)
        done = threading.Event()

        def worker_for(cand, name_prefix, total):
            ready.wait(timeout=2)
            for i in range(1, total + 1):
                w._update_active(
                    cand.folder_hash,
                    track_index=i,
                    track_total=total,
                    track_name=f'{name_prefix}-track-{i}',
                )
                # Tight loop so the two threads interleave aggressively
                time.sleep(0.001)

        ta = threading.Thread(target=worker_for, args=(cand_a, 'A', 50))
        tb = threading.Thread(target=worker_for, args=(cand_b, 'B', 50))
        ta.start(); tb.start()
        ta.join(timeout=5); tb.join(timeout=5)
        done.set()

        snap = w._snapshot_active()
        by_hash = {a['folder_hash']: a for a in snap}

        assert by_hash['hA']['folder_name'] == 'AlbumA', (
            "Candidate A's folder_name was overwritten by a parallel candidate — "
            f"got {by_hash['hA']['folder_name']!r}"
        )
        assert by_hash['hB']['folder_name'] == 'AlbumB', (
            "Candidate B's folder_name was overwritten — "
            f"got {by_hash['hB']['folder_name']!r}"
        )
        assert by_hash['hA']['track_index'] == 50
        assert by_hash['hB']['track_index'] == 50
        assert by_hash['hA']['track_name'].startswith('A-')
        assert by_hash['hB']['track_name'].startswith('B-')
    finally:
        w.stop()


def test_get_status_returns_coherent_active_imports_array():
    """`get_status()` must return one entry per in-flight candidate
    with the right per-candidate fields — the polling UI reads this
    array to render multiple in-flight imports simultaneously."""
    w = _make_worker(max_workers=3)
    w.start()
    try:
        for i, name in enumerate(['One', 'Two', 'Three']):
            cand = _make_candidate(folder_hash=f'h{i}', name=name)
            w._register_active(cand, status='processing')
            w._update_active(cand.folder_hash, track_index=i + 1, track_total=10)

        status = w.get_status()
        active = status.get('active_imports') or []
        assert len(active) == 3
        names = {a['folder_name'] for a in active}
        assert names == {'One', 'Two', 'Three'}

        # Aggregate top-level should be 'processing' (any active is
        # processing → processing wins)
        assert status['current_status'] == 'processing'

        # Legacy single-import scalars: populated from the FIRST
        # active entry (insertion order) so the existing UI keeps
        # working when only one candidate is in flight.
        assert status['current_folder'] == 'One'
        assert status['current_track_index'] == 1
        assert status['current_track_total'] == 10
    finally:
        w.stop()


def test_unregister_removes_only_that_candidate():
    """`_unregister_active(hash)` removes one entry; others stay
    visible. Pool workers finishing in any order must not affect
    other in-flight candidates' UI state."""
    w = _make_worker()
    w.start()
    try:
        for i, name in enumerate(['X', 'Y', 'Z']):
            w._register_active(_make_candidate(folder_hash=f'k{i}', name=name))

        w._unregister_active('k1')
        snap = w._snapshot_active()
        names = {a['folder_name'] for a in snap}
        assert names == {'X', 'Z'}, f"Unexpected snapshot after unregister: {snap}"
    finally:
        w.stop()


# ---------------------------------------------------------------------------
# Stats counter integrity under parallel bumps
# ---------------------------------------------------------------------------


def test_stats_increments_are_thread_safe():
    """`self._stats[k] += 1` from multiple threads is read-modify-
    write — under load the counters drift. `_bump_stat` wraps every
    mutation in `_stats_lock` so 1000 parallel bumps land at 1000."""
    w = _make_worker()
    iterations = 200
    threads_count = 5
    expected = iterations * threads_count

    def hammer():
        for _ in range(iterations):
            w._bump_stat('scanned')

    threads = [threading.Thread(target=hammer) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert w._stats['scanned'] == expected, (
        f"Lost increments: expected {expected}, got {w._stats['scanned']}. "
        f"Stats counter is not thread-safe."
    )


def test_get_status_stats_snapshot_is_consistent():
    """`get_status()` reads stats under the same lock that mutations
    use, so the returned snapshot can't show a partial mid-update
    state. Verify the snapshot is a copy (not a live reference)."""
    w = _make_worker()
    w._bump_stat('scanned')
    snap = w.get_status()['stats']
    snap['scanned'] = 9999
    # Mutating the snapshot must not affect the worker's internal stats
    assert w._stats['scanned'] == 1, (
        "get_status() returned a live reference to _stats — "
        "callers can corrupt internal state."
    )
