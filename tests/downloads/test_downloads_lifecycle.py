"""Tests for core/downloads/lifecycle.py — batch lifecycle (start, complete, check)."""

from __future__ import annotations

import threading

import pytest

from core.downloads import lifecycle as lc
from core.runtime_state import (
    download_batches,
    download_tasks,
)


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def _inner(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _inner


class _FakeAutoEngine:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


class _FakeMonitor:
    def __init__(self):
        self.stopped = []

    def stop_monitoring(self, batch_id):
        self.stopped.append(batch_id)


class _FakeRepair:
    def __init__(self):
        self.batches = []

    def process_batch(self, batch_id):
        self.batches.append(batch_id)


class _FakeConfig:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


def _build_deps(
    *,
    automation=None,
    monitor=None,
    repair=None,
    config=None,
    is_shutting_down=lambda: False,
    submit_dl_worker=None,
    submit_failed=None,
    submit_failed_auto=None,
    process_failed=None,
    process_failed_auto=None,
    yt_states=None,
    tidal_states=None,
    deezer_states=None,
    spotify_states=None,
):
    rec = _Recorder()
    return lc.LifecycleDeps(
        config_manager=config or _FakeConfig(),
        automation_engine=automation,
        download_monitor=monitor or _FakeMonitor(),
        repair_worker=repair,
        mb_worker=None,
        is_shutting_down=is_shutting_down,
        get_batch_lock=lambda bid: threading.Lock(),
        submit_download_track_worker=submit_dl_worker or rec('submit_dl'),
        submit_failed_to_wishlist=submit_failed or rec('submit_failed'),
        submit_failed_to_wishlist_with_auto_completion=submit_failed_auto or rec('submit_failed_auto'),
        process_failed_to_wishlist=process_failed or rec('process_failed'),
        process_failed_to_wishlist_with_auto_completion=process_failed_auto or rec('process_failed_auto'),
        ensure_wishlist_track_format=lambda track: track,
        get_track_artist_name=lambda track: 'Artist',
        check_and_remove_from_wishlist=rec('check_wishlist'),
        regenerate_batch_m3u=rec('regen_m3u'),
        youtube_playlist_states=yt_states or {},
        tidal_discovery_states=tidal_states or {},
        deezer_discovery_states=deezer_states or {},
        spotify_public_discovery_states=spotify_states or {},
    ), rec


# ---------------------------------------------------------------------------
# start_next_batch_of_downloads
# ---------------------------------------------------------------------------

def test_start_next_returns_silently_for_missing_batch():
    deps, rec = _build_deps()
    lc.start_next_batch_of_downloads('absent', deps)
    assert rec.calls == []


def test_start_next_skipped_when_shutting_down():
    download_batches['b1'] = {'queue': ['t1'], 'queue_index': 0, 'active_count': 0, 'max_concurrent': 1}
    deps, rec = _build_deps(is_shutting_down=lambda: True)
    lc.start_next_batch_of_downloads('b1', deps)
    assert rec.calls == []  # no submit


def test_start_next_submits_up_to_max_concurrent():
    download_tasks['t1'] = {'status': 'queued'}
    download_tasks['t2'] = {'status': 'queued'}
    download_tasks['t3'] = {'status': 'queued'}
    download_batches['b1'] = {
        'queue': ['t1', 't2', 't3'], 'queue_index': 0,
        'active_count': 0, 'max_concurrent': 2,
    }
    deps, rec = _build_deps()
    lc.start_next_batch_of_downloads('b1', deps)
    submits = [c for c in rec.calls if c[0] == 'submit_dl']
    assert len(submits) == 2
    assert download_batches['b1']['active_count'] == 2
    assert download_batches['b1']['queue_index'] == 2


def test_start_next_skips_cancelled_tasks_without_consuming_slots():
    download_tasks['t1'] = {'status': 'cancelled'}
    download_tasks['t2'] = {'status': 'queued'}
    download_batches['b1'] = {
        'queue': ['t1', 't2'], 'queue_index': 0,
        'active_count': 0, 'max_concurrent': 1,
    }
    deps, rec = _build_deps()
    lc.start_next_batch_of_downloads('b1', deps)
    submits = [c for c in rec.calls if c[0] == 'submit_dl']
    assert len(submits) == 1
    # t2 should be the one submitted (t1 skipped)
    assert submits[0][1] == ('t2', 'b1')


def test_start_next_sets_searching_status_before_submit():
    download_tasks['t1'] = {'status': 'queued'}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 0,
        'active_count': 0, 'max_concurrent': 1,
    }
    deps, _ = _build_deps()
    lc.start_next_batch_of_downloads('b1', deps)
    assert download_tasks['t1']['status'] == 'searching'
    assert download_tasks['t1']['status_change_time'] is not None


def test_start_next_submit_failure_marks_task_failed_no_ghost_worker():
    download_tasks['t1'] = {'status': 'queued'}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 0,
        'active_count': 0, 'max_concurrent': 1,
    }

    def _broken_submit(task_id, batch_id):
        raise RuntimeError("executor dead")

    deps, _ = _build_deps(submit_dl_worker=_broken_submit)
    lc.start_next_batch_of_downloads('b1', deps)
    # No counters incremented
    assert download_batches['b1']['active_count'] == 0
    assert download_batches['b1']['queue_index'] == 0
    # Task marked failed
    assert download_tasks['t1']['status'] == 'failed'


def test_start_next_orphan_task_in_queue_skipped():
    download_batches['b1'] = {
        'queue': ['absent', 't2'], 'queue_index': 0,
        'active_count': 0, 'max_concurrent': 2,
    }
    download_tasks['t2'] = {'status': 'queued'}
    deps, rec = _build_deps()
    lc.start_next_batch_of_downloads('b1', deps)
    submits = [c for c in rec.calls if c[0] == 'submit_dl']
    # Only t2 submitted
    assert len(submits) == 1
    assert submits[0][1] == ('t2', 'b1')


# ---------------------------------------------------------------------------
# on_download_completed
# ---------------------------------------------------------------------------

def test_on_complete_missing_batch_returns_silently():
    deps, rec = _build_deps()
    lc.on_download_completed('absent', 't1', True, deps)
    assert rec.calls == []


def test_on_complete_decrements_active_count():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    lc.on_download_completed('b1', 't1', True, deps)
    assert download_batches['b1']['active_count'] == 0


def test_on_complete_duplicate_call_skips_decrement_but_checks_completion():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    # First call: decrements
    lc.on_download_completed('b1', 't1', True, deps)
    assert download_batches['b1']['active_count'] == 0
    # Mark as complete to prevent batch completion path
    download_batches['b1']['phase'] = 'complete'
    # Second call: should NOT decrement again (would go negative)
    lc.on_download_completed('b1', 't1', True, deps)
    assert download_batches['b1']['active_count'] == 0  # still 0, not -1


def test_on_complete_failed_task_appended_to_permanently_failed_tracks():
    download_tasks['t1'] = {
        'status': 'failed', 'track_info': {'name': 'Money'},
        'track_index': 0, 'retry_count': 0,
    }
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    lc.on_download_completed('b1', 't1', False, deps)
    assert len(download_batches['b1']['permanently_failed_tracks']) == 1
    assert download_batches['b1']['permanently_failed_tracks'][0]['track_name'] == 'Money'
    assert download_batches['b1']['permanently_failed_tracks'][0]['track_data'] == {'name': 'Money'}
    assert download_batches['b1']['permanently_failed_tracks'][0]['spotify_track'] == {'name': 'Money'}


def test_on_complete_cancelled_task_added_to_cancelled_tracks():
    download_tasks['t1'] = {
        'status': 'cancelled', 'track_info': {'name': 'X'},
        'track_index': 5,
    }
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    lc.on_download_completed('b1', 't1', False, deps)
    assert 5 in download_batches['b1']['cancelled_tracks']


def test_on_complete_emits_download_failed_for_not_found():
    download_tasks['t1'] = {
        'status': 'not_found', 'track_info': {'name': 'X'},
        'track_index': 0, 'retry_count': 0,
    }
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    auto = _FakeAutoEngine()
    deps, _ = _build_deps(automation=auto)
    lc.on_download_completed('b1', 't1', False, deps)
    events = [e for e in auto.events if e[0] == 'download_failed']
    assert len(events) == 1


def test_on_complete_success_calls_check_and_remove_wishlist():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X', 'artists': ['A']}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, rec = _build_deps()
    lc.on_download_completed('b1', 't1', True, deps)
    assert any(c[0] == 'check_wishlist' for c in rec.calls)


# ---------------------------------------------------------------------------
# Batch completion (via on_download_completed)
# ---------------------------------------------------------------------------

def test_batch_completion_emits_batch_complete_when_all_done():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_tasks['t2'] = {'status': 'completed', 'track_info': {'name': 'Y'}}
    download_batches['b1'] = {
        'queue': ['t1', 't2'], 'queue_index': 2, 'active_count': 1,
        'max_concurrent': 2, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
        'playlist_name': 'PL',
    }
    auto = _FakeAutoEngine()
    monitor = _FakeMonitor()
    deps, _ = _build_deps(automation=auto, monitor=monitor)
    # Final task completes
    lc.on_download_completed('b1', 't2', True, deps)
    assert download_batches['b1']['phase'] == 'complete'
    assert ('batch_complete', {'playlist_name': 'PL', 'total_tracks': '2', 'completed_tracks': '2', 'failed_tracks': '0'}) in auto.events
    assert 'b1' in monitor.stopped


def test_batch_completion_skips_emit_when_zero_successful():
    """Don't emit batch_complete if nothing actually downloaded."""
    download_tasks['t1'] = {'status': 'failed', 'track_info': {'name': 'X'}, 'track_index': 0}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1,
        'permanently_failed_tracks': [{'track_name': 'X'}],  # 1 failed
        'cancelled_tracks': set(),
        'playlist_name': 'PL',
    }
    auto = _FakeAutoEngine()
    deps, _ = _build_deps(automation=auto)
    lc.on_download_completed('b1', 't1', False, deps)
    # Pre-existing failed already counted, so successful = 1 (count) - 1 (already failed) = 0
    # but on_download_completed appends another failure for t1, so failed count = 2 > finished = 1
    # the emit is only triggered if successful_downloads > 0
    events = [e for e in auto.events if e[0] == 'batch_complete']
    # successful = finished_count(1) - failed_count(2) = -1, which is not > 0 → no emit
    assert events == []


def test_batch_completion_routes_auto_batch_to_auto_completion_handler():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1,
        'permanently_failed_tracks': [], 'cancelled_tracks': set(),
        'auto_initiated': True,
        'playlist_name': 'PL',
    }
    deps, rec = _build_deps()
    lc.on_download_completed('b1', 't1', True, deps)
    assert any(c[0] == 'submit_failed_auto' and c[1] == ('b1',) for c in rec.calls)


def test_batch_completion_routes_manual_batch_to_regular_handler():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1,
        'permanently_failed_tracks': [], 'cancelled_tracks': set(),
        # no auto_initiated → manual
    }
    deps, rec = _build_deps()
    lc.on_download_completed('b1', 't1', True, deps)
    assert any(c[0] == 'submit_failed' and c[1] == ('b1',) for c in rec.calls)


def test_batch_completion_does_not_complete_when_tasks_still_searching():
    download_tasks['t1'] = {'status': 'searching', 'track_info': {'name': 'X'},
                             'status_change_time': lc.time.time()}  # fresh
    download_tasks['t2'] = {'status': 'completed', 'track_info': {'name': 'Y'}}
    download_batches['b1'] = {
        'queue': ['t1', 't2'], 'queue_index': 2, 'active_count': 1,
        'max_concurrent': 2, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    auto = _FakeAutoEngine()
    deps, _ = _build_deps(automation=auto)
    lc.on_download_completed('b1', 't2', True, deps)
    # Batch NOT marked complete (t1 still searching)
    assert download_batches['b1'].get('phase') != 'complete'


def test_stuck_searching_task_forced_to_not_found():
    """Task searching > 10min gets forced to not_found."""
    download_tasks['t1'] = {
        'status': 'searching', 'track_info': {'name': 'X'},
        'status_change_time': 0,  # very ancient
    }
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    # Trigger completion check
    lc.on_download_completed('b1', 't1', False, deps)
    # t1 forced to not_found
    assert download_tasks['t1']['status'] == 'not_found'


def test_stuck_post_processing_task_forced_to_completed():
    """Task post_processing > 5min gets forced to completed."""
    download_tasks['t1'] = {
        'status': 'post_processing', 'track_info': {'name': 'X'},
        'status_change_time': 0,
    }
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
    }
    deps, _ = _build_deps()
    lc.on_download_completed('b1', 't1', True, deps)
    assert download_tasks['t1']['status'] == 'completed'


def test_youtube_playlist_phase_updated_on_completion():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(),
        'playlist_id': 'youtube_abc123',
    }
    yt = {'abc123': {'phase': 'downloading'}}
    deps, _ = _build_deps(yt_states=yt)
    lc.on_download_completed('b1', 't1', True, deps)
    assert yt['abc123']['phase'] == 'download_complete'


# ---------------------------------------------------------------------------
# check_batch_completion_v2
# ---------------------------------------------------------------------------

def test_check_v2_returns_none_for_missing_batch():
    deps, _ = _build_deps()
    result = lc.check_batch_completion_v2('absent', deps)
    assert result is None


def test_check_v2_returns_false_when_not_complete():
    download_tasks['t1'] = {'status': 'downloading', 'track_info': {}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
    }
    deps, _ = _build_deps()
    result = lc.check_batch_completion_v2('b1', deps)
    assert result is False


def test_check_v2_returns_true_when_complete():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 0,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
    }
    deps, _ = _build_deps()
    result = lc.check_batch_completion_v2('b1', deps)
    assert result is True
    assert download_batches['b1']['phase'] == 'complete'


def test_check_v2_already_complete_returns_true_without_reprocessing():
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 0,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'phase': 'complete',  # already marked
    }
    deps, rec = _build_deps()
    result = lc.check_batch_completion_v2('b1', deps)
    assert result is True
    # Wishlist NOT submitted again
    assert not any(c[0] in ('process_failed', 'process_failed_auto') for c in rec.calls)


def test_check_v2_routes_auto_batch_to_auto_handler():
    """v2 calls wishlist processing DIRECTLY (sync), not via executor submit.
    Different from on_download_completed which uses async submit."""
    download_tasks['t1'] = {'status': 'completed', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 0,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'auto_initiated': True,
    }
    deps, rec = _build_deps()
    lc.check_batch_completion_v2('b1', deps)
    assert any(c[0] == 'process_failed_auto' for c in rec.calls)


def test_check_v2_exception_returns_false():
    download_batches['b1'] = {'queue': ['t1'], 'queue_index': 1, 'active_count': 0}
    # Force an exception by putting invalid type in batch
    download_batches['b1']['queue'] = None  # will raise TypeError on len()
    deps, _ = _build_deps()
    result = lc.check_batch_completion_v2('b1', deps)
    assert result is False
