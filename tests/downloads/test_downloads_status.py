"""Tests for core/downloads/status.py — batch + unified status helpers."""

from __future__ import annotations

import pytest

from core.downloads import status as st
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

class _FakeConfig:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


def _build_deps(
    *,
    config=None,
    docker_resolve=None,
    find_completed=None,
    make_key=None,
    submit_pp=None,
    cached_transfers=None,
):
    submitted = []

    def _default_submit(task_id, batch_id):
        submitted.append((task_id, batch_id))

    deps = st.StatusDeps(
        config_manager=config or _FakeConfig({'soulseek.download_timeout': 600}),
        docker_resolve_path=docker_resolve or (lambda p: p),
        find_completed_file=find_completed or (lambda *a, **kw: (None, None)),
        make_context_key=make_key or (lambda u, f: f"{u}::{f}"),
        submit_post_processing=submit_pp or _default_submit,
        get_cached_transfer_data=cached_transfers or (lambda: {}),
    )
    return deps, submitted


# ---------------------------------------------------------------------------
# build_batch_status_data — phase routing
# ---------------------------------------------------------------------------

def test_unknown_phase_includes_basic_fields_only():
    deps, _ = _build_deps()
    batch = {'phase': 'unknown', 'playlist_id': 'pl1', 'playlist_name': 'My PL'}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert out['phase'] == 'unknown'
    assert out['playlist_id'] == 'pl1'
    assert out['playlist_name'] == 'My PL'
    assert 'tasks' not in out
    assert 'analysis_progress' not in out


def test_analysis_phase_includes_analysis_progress_and_results():
    deps, _ = _build_deps()
    batch = {
        'phase': 'analysis',
        'analysis_total': 10,
        'analysis_processed': 4,
        'analysis_results': [{'track_index': 0, 'found': True}],
    }
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert out['analysis_progress'] == {'total': 10, 'processed': 4}
    assert out['analysis_results'] == [{'track_index': 0, 'found': True}]


def test_complete_phase_includes_wishlist_summary_when_present():
    deps, _ = _build_deps()
    batch = {
        'phase': 'complete',
        'queue': [],
        'wishlist_summary': {'added': 3, 'failed': 0},
    }
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert out['wishlist_summary'] == {'added': 3, 'failed': 0}


def test_complete_phase_omits_wishlist_summary_when_missing():
    deps, _ = _build_deps()
    batch = {'phase': 'complete', 'queue': []}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert 'wishlist_summary' not in out


# ---------------------------------------------------------------------------
# Task formatting
# ---------------------------------------------------------------------------

def test_downloading_phase_includes_active_count_and_max_concurrent():
    deps, _ = _build_deps()
    batch = {'phase': 'downloading', 'queue': [], 'active_count': 2, 'max_concurrent': 5}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert out['active_count'] == 2
    assert out['max_concurrent'] == 5


def test_tasks_sorted_by_track_index():
    deps, _ = _build_deps()
    download_tasks['t1'] = {'track_index': 2, 'status': 'queued', 'track_info': {}}
    download_tasks['t2'] = {'track_index': 0, 'status': 'queued', 'track_info': {}}
    download_tasks['t3'] = {'track_index': 1, 'status': 'queued', 'track_info': {}}
    batch = {'phase': 'downloading', 'queue': ['t1', 't2', 't3']}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert [t['track_index'] for t in out['tasks']] == [0, 1, 2]


def test_missing_task_in_queue_is_skipped():
    deps, _ = _build_deps()
    download_tasks['t1'] = {'track_index': 0, 'status': 'queued', 'track_info': {}}
    batch = {'phase': 'downloading', 'queue': ['t1', 'absent']}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    assert len(out['tasks']) == 1


def test_task_status_includes_v2_state_fields():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'cancelling', 'track_info': {},
        'cancel_requested': True, 'cancel_timestamp': 12345,
        'ui_state': 'cancelling', 'playlist_id': 'pl1',
        'error_message': 'oh no', 'cached_candidates': [{'x': 1}],
    }
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, {}, deps)
    t = out['tasks'][0]
    assert t['cancel_requested'] is True
    assert t['cancel_timestamp'] == 12345
    assert t['ui_state'] == 'cancelling'
    assert t['playlist_id'] == 'pl1'
    assert t['error_message'] == 'oh no'
    assert t['has_candidates'] is True


# ---------------------------------------------------------------------------
# Live transfer state mapping
# ---------------------------------------------------------------------------

def test_live_state_cancelled_maps_to_cancelled():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {'state': 'Cancelled', 'percentComplete': 50}}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['status'] == 'cancelled'
    assert download_tasks['t1']['status'] == 'cancelled'  # mutates source state


def test_live_state_succeeded_with_full_bytes_marks_post_processing_and_submits():
    deps, submitted = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {
        'state': 'Succeeded', 'size': 100, 'bytesTransferred': 100, 'percentComplete': 100,
    }}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['status'] == 'post_processing'
    assert download_tasks['t1']['status'] == 'post_processing'
    assert submitted == [('t1', 'b1')]


def test_live_state_succeeded_with_byte_mismatch_keeps_status():
    deps, submitted = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {
        'state': 'Succeeded', 'size': 100, 'bytesTransferred': 50,
    }}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['status'] == 'downloading'  # not promoted to post_processing
    assert submitted == []  # not submitted


def test_live_state_inprogress_maps_to_downloading():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'queued', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {'state': 'InProgress', 'percentComplete': 42}}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['status'] == 'downloading'
    assert out['tasks'][0]['progress'] == 42


def test_live_state_errored_keeps_active_status_for_monitor():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {'state': 'Errored'}}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    # Keeps current status so monitor handles retry
    assert out['tasks'][0]['status'] == 'downloading'
    assert download_tasks['t1']['status'] == 'downloading'  # not marked failed


def test_terminal_status_not_overridden_by_live_state():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'completed', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {'u1::song.flac': {'state': 'InProgress', 'percentComplete': 50}}
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['status'] == 'completed'
    assert out['tasks'][0]['progress'] == 100


def test_post_processing_status_progress_is_95():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'post_processing', 'track_info': {},
        'filename': 'song.flac', 'username': 'u1',
    }
    live = {}  # no live entry
    batch = {'phase': 'downloading', 'queue': ['t1']}
    out = st.build_batch_status_data('b1', batch, live, deps)
    assert out['tasks'][0]['progress'] == 95


# ---------------------------------------------------------------------------
# Safety valve (stuck task handling)
# ---------------------------------------------------------------------------

def test_safety_valve_stuck_searching_marks_not_found():
    deps, _ = _build_deps(config=_FakeConfig({'soulseek.download_timeout': 1}))
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'searching', 'track_info': {},
        'status_change_time': 0,  # ancient
    }
    batch = {'phase': 'downloading', 'queue': ['t1']}
    st.build_batch_status_data('b1', batch, {}, deps)
    assert download_tasks['t1']['status'] == 'not_found'
    assert 'Search stuck' in download_tasks['t1']['error_message']


def test_safety_valve_stuck_downloading_with_recovered_file_routes_to_post_processing():
    deps, submitted = _build_deps(
        config=_FakeConfig({'soulseek.download_timeout': 1, 'soulseek.download_path': '/d', 'soulseek.transfer_path': '/t'}),
        find_completed=lambda *a, **kw: ('/found.flac', 'transfer'),
    )
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac',
        'status_change_time': 0,
    }
    batch = {'phase': 'downloading', 'queue': ['t1']}
    st.build_batch_status_data('b1', batch, {}, deps)
    assert download_tasks['t1']['status'] == 'post_processing'
    assert submitted == [('t1', 'b1')]


def test_safety_valve_stuck_downloading_no_file_marks_failed():
    deps, _ = _build_deps(config=_FakeConfig({'soulseek.download_timeout': 1}))
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'downloading', 'track_info': {},
        'filename': 'song.flac',
        'status_change_time': 0,
    }
    batch = {'phase': 'downloading', 'queue': ['t1']}
    st.build_batch_status_data('b1', batch, {}, deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'Task stuck' in download_tasks['t1']['error_message']


# ---------------------------------------------------------------------------
# build_single_batch_status (route helper)
# ---------------------------------------------------------------------------

def test_single_batch_status_404_for_unknown_batch():
    deps, _ = _build_deps()
    body, status = st.build_single_batch_status('absent', deps)
    assert status == 404
    assert body['error'] == 'Batch not found'


def test_single_batch_status_returns_payload_for_known_batch():
    deps, _ = _build_deps()
    download_batches['b1'] = {'phase': 'unknown', 'playlist_id': 'pl1'}
    body, status = st.build_single_batch_status('b1', deps)
    assert status == 200
    assert body['phase'] == 'unknown'
    assert body['playlist_id'] == 'pl1'


# ---------------------------------------------------------------------------
# build_batched_status (route helper)
# ---------------------------------------------------------------------------

def test_batched_status_filters_to_requested_ids():
    deps, _ = _build_deps()
    download_batches['b1'] = {'phase': 'unknown'}
    download_batches['b2'] = {'phase': 'unknown'}
    download_batches['b3'] = {'phase': 'unknown'}
    out = st.build_batched_status(['b1', 'b3'], deps)
    assert set(out['batches'].keys()) == {'b1', 'b3'}


def test_batched_status_no_filter_returns_all_batches():
    deps, _ = _build_deps()
    download_batches['b1'] = {'phase': 'unknown'}
    download_batches['b2'] = {'phase': 'unknown'}
    out = st.build_batched_status([], deps)
    assert set(out['batches'].keys()) == {'b1', 'b2'}


def test_batched_status_metadata_present():
    deps, _ = _build_deps()
    download_batches['b1'] = {'phase': 'unknown'}
    out = st.build_batched_status([], deps)
    assert out['metadata']['total_batches'] == 1
    assert out['metadata']['requested_batch_ids'] == []
    assert isinstance(out['metadata']['timestamp'], (int, float))


def test_batched_status_per_batch_failure_isolated():
    deps, _ = _build_deps()
    # b1 valid, b2 raises
    download_batches['b1'] = {'phase': 'downloading', 'queue': []}

    class _BadBatch(dict):
        def get(self, k, default=None):
            if k == 'phase':
                raise RuntimeError("batch boom")
            return super().get(k, default)

    download_batches['b2'] = _BadBatch()

    out = st.build_batched_status([], deps)
    assert 'error' in out['batches']['b2']
    assert out['batches']['b1'].get('phase') == 'downloading'


def test_batched_status_debug_info_pre_existing_bug_never_populates():
    """Documents a pre-existing bug: every batch payload includes
    `"error": batch.get('error')` (key always present, value usually None).
    The debug_info loop checks `if "error" not in batch_status:` which is
    therefore always False → debug_info stays empty in production.

    Lift preserves this exactly. A future PR can flip the check to
    `if batch_status.get('error') is None` to fix it.
    """
    deps, _ = _build_deps()
    download_tasks['t1'] = {'track_index': 0, 'status': 'downloading', 'track_info': {}}
    download_tasks['t2'] = {'track_index': 1, 'status': 'downloading', 'track_info': {}}
    download_batches['b1'] = {
        'phase': 'downloading', 'queue': ['t1', 't2'],
        'active_count': 5,
        'max_concurrent': 5,
    }
    out = st.build_batched_status([], deps)
    # Bug: stays empty because "error" key is always present in payload
    assert out['debug_info'] == {}


# ---------------------------------------------------------------------------
# build_unified_downloads_response
# ---------------------------------------------------------------------------

def test_unified_response_sorts_by_priority_then_recency():
    deps, _ = _build_deps()
    download_tasks['old_complete'] = {
        'track_index': 0, 'status': 'completed', 'track_info': {'name': 'A'},
        'status_change_time': 100,
    }
    download_tasks['new_active'] = {
        'track_index': 1, 'status': 'downloading', 'track_info': {'name': 'B'},
        'status_change_time': 50,
    }
    out = st.build_unified_downloads_response(100, deps)
    # Active downloads come first regardless of timestamp
    assert out['downloads'][0]['title'] == 'B'


def test_unified_response_artist_list_of_dicts_normalized():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'queued',
        'track_info': {'name': 'X', 'artists': [{'name': 'Pink Floyd'}, {'name': 'Roger'}]},
    }
    out = st.build_unified_downloads_response(100, deps)
    assert out['downloads'][0]['artist'] == 'Pink Floyd, Roger'


def test_unified_response_album_dict_extracted_to_name():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'queued',
        'track_info': {'name': 'X', 'album': {'name': 'DSOTM', 'images': [{'url': 'http://thumb.jpg'}]}},
    }
    out = st.build_unified_downloads_response(100, deps)
    assert out['downloads'][0]['album'] == 'DSOTM'
    assert out['downloads'][0]['artwork'] == 'http://thumb.jpg'


def test_unified_response_completed_task_progress_is_100():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'completed',
        'track_info': {'name': 'X'},
    }
    out = st.build_unified_downloads_response(100, deps)
    assert out['downloads'][0]['progress'] == 100


def test_unified_response_post_processing_progress_is_95():
    deps, _ = _build_deps()
    download_tasks['t1'] = {
        'track_index': 0, 'status': 'post_processing',
        'track_info': {'name': 'X'},
    }
    out = st.build_unified_downloads_response(100, deps)
    assert out['downloads'][0]['progress'] == 95


def test_unified_response_includes_batch_summaries():
    deps, _ = _build_deps()
    download_tasks['t1'] = {'track_index': 0, 'status': 'completed', 'track_info': {}}
    download_tasks['t2'] = {'track_index': 1, 'status': 'failed', 'track_info': {}}
    download_tasks['t3'] = {'track_index': 2, 'status': 'downloading', 'track_info': {}}
    download_tasks['t4'] = {'track_index': 3, 'status': 'queued', 'track_info': {}}
    download_batches['b1'] = {
        'phase': 'downloading', 'playlist_name': 'PL',
        'queue': ['t1', 't2', 't3', 't4'],
    }
    out = st.build_unified_downloads_response(100, deps)
    bs = out['batches'][0]
    assert bs['total'] == 4
    assert bs['completed'] == 1
    assert bs['failed'] == 1
    assert bs['active'] == 1
    assert bs['queued'] == 1


def test_unified_response_respects_limit():
    deps, _ = _build_deps()
    for i in range(20):
        download_tasks[f't{i}'] = {
            'track_index': i, 'status': 'completed', 'track_info': {},
        }
    out = st.build_unified_downloads_response(5, deps)
    assert len(out['downloads']) == 5
    assert out['total'] == 20  # total still reflects all
