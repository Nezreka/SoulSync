"""Tests for `BackgroundDownloadWorker` (Phase C1).

These tests pin the worker's state-machine semantics, semaphore
serialization, rate-limit-delay behavior, and exception handling.
Future phases (C2–C7) migrate each per-source client onto this
worker — these tests stay green as the regression net.
"""

from __future__ import annotations

import threading
import time

from core.download_engine import DownloadEngine


# ---------------------------------------------------------------------------
# Dispatch — initial state + thread spawn
# ---------------------------------------------------------------------------


def test_dispatch_returns_uuid_download_id():
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        return '/tmp/file.flac'

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='abc123',
        display_name='Some Song',
        original_filename='abc123||Some Song',
        impl_callable=impl,
    )
    assert len(download_id) == 36  # UUID4
    assert download_id.count('-') == 4


def test_dispatch_inserts_initial_record_with_canonical_state():
    """Pinning: initial record matches the legacy per-client shape so
    consumers reading the state dict via API or context-key lookup
    keep working unchanged after migration."""
    engine = DownloadEngine()
    captured = threading.Event()

    def impl(download_id, target_id, display_name):
        captured.wait(timeout=1.0)  # block so we can read 'Initializing' / 'InProgress' state
        return '/tmp/file.flac'

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='abc',
        display_name='X',
        original_filename='abc||X',
        impl_callable=impl,
    )
    record = engine.get_record('youtube', download_id)
    assert record is not None
    assert record['id'] == download_id
    assert record['filename'] == 'abc||X'
    assert record['username'] == 'youtube'
    assert record['state'] in ('Initializing', 'InProgress, Downloading')
    assert record['progress'] == 0.0
    assert record['file_path'] is None
    captured.set()  # release impl


def test_dispatch_merges_extra_record_fields():
    """Pinning: source-specific slots (video_id, track_id, etc.)
    merge into the initial record so frontend + status APIs that
    read those keys keep working."""
    engine = DownloadEngine()
    started = threading.Event()
    release = threading.Event()

    def impl(download_id, target_id, display_name):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/x.flac'

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid123',
        display_name='Title',
        original_filename='vid123||Title',
        impl_callable=impl,
        extra_record_fields={
            'video_id': 'vid123',
            'url': 'https://youtube.com/watch?v=vid123',
            'title': 'Title',
        },
    )
    started.wait(timeout=1.0)
    record = engine.get_record('youtube', download_id)
    assert record['video_id'] == 'vid123'
    assert record['url'] == 'https://youtube.com/watch?v=vid123'
    assert record['title'] == 'Title'
    release.set()


def test_dispatch_username_override_preserves_legacy_slot():
    """Pinning: Deezer's record stores `'deezer_dl'` (legacy) in the
    username slot, not the canonical `'deezer'`. Worker accepts
    override so frontend status indicators keep their key."""
    engine = DownloadEngine()
    release = threading.Event()

    def impl(download_id, target_id, display_name):
        release.wait(timeout=1.0)
        return '/tmp/x.flac'

    download_id = engine.worker.dispatch(
        source_name='deezer',
        target_id='999',
        display_name='X',
        original_filename='999||X',
        impl_callable=impl,
        username_override='deezer_dl',
    )
    record = engine.get_record('deezer', download_id)
    assert record['username'] == 'deezer_dl'
    release.set()


# ---------------------------------------------------------------------------
# Worker lifecycle — state transitions
# ---------------------------------------------------------------------------


def test_worker_marks_completed_on_successful_impl():
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        return '/tmp/done.flac'

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    # Wait for thread to finish.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] == 'Completed, Succeeded':
            break
        time.sleep(0.01)

    record = engine.get_record('youtube', download_id)
    assert record['state'] == 'Completed, Succeeded'
    assert record['progress'] == 100.0
    assert record['file_path'] == '/tmp/done.flac'


def test_worker_preserves_cancelled_when_impl_returns_none():
    """Pinning: if the user cancels mid-download (state flips to
    Cancelled via engine.update_record from cancel_download), the
    worker must NOT clobber it back to Errored when impl returns
    None. The legacy per-client thread workers had this guard
    (``if state != 'Cancelled': state = 'Errored'``); the shared
    worker preserves that contract."""
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        # Simulate user cancelling mid-impl by writing Cancelled.
        engine.update_record('youtube', download_id, {'state': 'Cancelled'})
        return None  # impl returns None because download was interrupted

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] in ('Cancelled', 'Errored'):
            break
        time.sleep(0.01)

    record = engine.get_record('youtube', download_id)
    assert record['state'] == 'Cancelled', (
        f"Worker clobbered user's Cancelled with {record['state']}"
    )


def test_worker_preserves_cancelled_when_impl_returns_success():
    """Cin's bug 3 follow-up: the success path also has a read-then-write
    race. If the user cancels between the impl returning a valid file
    path and the worker writing 'Completed, Succeeded', the cancel is
    overwritten. The success-path write must use the same atomic
    Cancelled-preserve guard as _mark_terminal."""
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        # User cancels mid-impl, then impl finishes successfully.
        engine.update_record('youtube', download_id, {'state': 'Cancelled'})
        return '/tmp/file.flac'

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] in ('Cancelled', 'Completed, Succeeded'):
            break
        time.sleep(0.01)

    record = engine.get_record('youtube', download_id)
    assert record['state'] == 'Cancelled', (
        f"Worker clobbered user's Cancelled with {record['state']}"
    )


def test_worker_preserves_cancelled_when_impl_raises():
    """Same Cancelled-preserve guard, but for the impl-raises path."""
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        engine.update_record('youtube', download_id, {'state': 'Cancelled'})
        raise RuntimeError("simulated mid-cancel exception")

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] in ('Cancelled', 'Errored'):
            break
        time.sleep(0.01)

    assert engine.get_record('youtube', download_id)['state'] == 'Cancelled'


def test_worker_marks_errored_when_impl_returns_none():
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        return None  # signaling failure

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] == 'Errored':
            break
        time.sleep(0.01)

    record = engine.get_record('youtube', download_id)
    assert record['state'] == 'Errored'
    # file_path stays None (default).
    assert record['file_path'] is None


def test_worker_marks_errored_and_captures_message_when_impl_raises():
    engine = DownloadEngine()

    def impl(download_id, target_id, display_name):
        raise RuntimeError("api blew up")

    download_id = engine.worker.dispatch(
        source_name='youtube',
        target_id='vid',
        display_name='X',
        original_filename='vid||X',
        impl_callable=impl,
    )

    deadline = time.time() + 2.0
    while time.time() < deadline:
        record = engine.get_record('youtube', download_id)
        if record and record['state'] == 'Errored':
            break
        time.sleep(0.01)

    record = engine.get_record('youtube', download_id)
    assert record['state'] == 'Errored'
    assert 'api blew up' in record.get('error', '')


# ---------------------------------------------------------------------------
# Per-source semaphore serialization
# ---------------------------------------------------------------------------


def test_semaphore_serializes_downloads_for_same_source():
    """Pinning: with concurrency=1 (default), two dispatches against
    the same source run sequentially. The legacy per-client
    semaphore did the same — consumers depend on this for
    rate-limit safety against APIs like YouTube."""
    engine = DownloadEngine()
    in_progress = threading.Event()
    can_finish = threading.Event()
    overlap_count = 0
    overlap_lock = threading.Lock()
    active_count = [0]

    def impl(download_id, target_id, display_name):
        nonlocal overlap_count
        with overlap_lock:
            active_count[0] += 1
            if active_count[0] > 1:
                overlap_count += 1
        in_progress.set()
        can_finish.wait(timeout=2.0)
        with overlap_lock:
            active_count[0] -= 1
        return '/tmp/x.flac'

    # Default concurrency=1 — two dispatches must serialize.
    dl1 = engine.worker.dispatch(
        source_name='youtube', target_id='a', display_name='A',
        original_filename='a||A', impl_callable=impl,
    )
    in_progress.wait(timeout=1.0)
    in_progress.clear()
    dl2 = engine.worker.dispatch(
        source_name='youtube', target_id='b', display_name='B',
        original_filename='b||B', impl_callable=impl,
    )
    # Give second dispatch a chance to attempt running in parallel
    # (it should be blocked on the semaphore).
    time.sleep(0.1)
    assert overlap_count == 0, "second dispatch should be blocked behind semaphore"

    # Release first; second proceeds.
    can_finish.set()

    # Wait for both to finish.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        r1 = engine.get_record('youtube', dl1)
        r2 = engine.get_record('youtube', dl2)
        if r1 and r2 and r1['state'] == 'Completed, Succeeded' and r2['state'] == 'Completed, Succeeded':
            break
        time.sleep(0.01)

    assert overlap_count == 0


def test_semaphore_concurrency_can_be_increased():
    """When `set_concurrency(source, N)` is called, N downloads can
    run in parallel for that source. Used by sources that support
    parallel transfers (none today, but contract supports it)."""
    engine = DownloadEngine()
    engine.worker.set_concurrency('parallel-source', 3)

    in_flight = []
    in_flight_lock = threading.Lock()
    can_finish = threading.Event()
    max_observed = [0]

    def impl(download_id, target_id, display_name):
        with in_flight_lock:
            in_flight.append(download_id)
            max_observed[0] = max(max_observed[0], len(in_flight))
        can_finish.wait(timeout=2.0)
        with in_flight_lock:
            in_flight.remove(download_id)
        return '/tmp/x.flac'

    for i in range(3):
        engine.worker.dispatch(
            source_name='parallel-source',
            target_id=str(i),
            display_name=f'd{i}',
            original_filename=f'{i}||d{i}',
            impl_callable=impl,
        )
    # Give threads time to ramp up.
    time.sleep(0.2)
    can_finish.set()

    # Wait for them to finish.
    time.sleep(0.5)
    assert max_observed[0] == 3


# ---------------------------------------------------------------------------
# Per-source rate-limit delay
# ---------------------------------------------------------------------------


def test_delay_enforces_minimum_gap_between_downloads():
    """Pinning: YouTube uses 3s delay today (legacy
    `_download_delay`). Worker-driven delay must enforce the same
    gap so YouTube doesn't 429."""
    engine = DownloadEngine()
    engine.worker.set_delay('youtube', 0.2)  # 200ms — short for test speed

    completion_times = []

    def impl(download_id, target_id, display_name):
        completion_times.append(time.time())
        return '/tmp/x.flac'

    # Two back-to-back dispatches.
    engine.worker.dispatch(
        source_name='youtube', target_id='a', display_name='A',
        original_filename='a||A', impl_callable=impl,
    )
    engine.worker.dispatch(
        source_name='youtube', target_id='b', display_name='B',
        original_filename='b||B', impl_callable=impl,
    )

    # Wait for both to finish (semaphore serializes + delay).
    deadline = time.time() + 3.0
    while time.time() < deadline and len(completion_times) < 2:
        time.sleep(0.01)

    assert len(completion_times) == 2
    gap = completion_times[1] - completion_times[0]
    # Gap is at LEAST the configured delay.
    assert gap >= 0.18, f"expected gap >= 0.2s, got {gap:.3f}"
