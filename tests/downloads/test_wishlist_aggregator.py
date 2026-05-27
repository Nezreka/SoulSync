"""Unit tests for ``core/downloads/wishlist_aggregator.merge_wishlist_run_status``.

Pins the merge contract the wishlist-modal status path depends on
(Phase 1c.2.1 follow-up): when one logical wishlist run is split
across N sub-batches, the frontend modal polls the original
batch_id and expects a unified view that covers every sibling.
"""

from __future__ import annotations

from core.downloads.wishlist_aggregator import merge_wishlist_run_status


def _status(phase, **kwargs):
    """Build a minimal per-batch status dict shaped like
    ``build_batch_status_data``'s output."""
    base = {
        'phase': phase,
        'playlist_id': 'wishlist',
        'playlist_name': 'Wishlist',
        'active_count': 0,
        'max_concurrent': 3,
    }
    base.update(kwargs)
    return base


def test_empty_siblings_returns_primary_unchanged():
    primary = _status('downloading', tasks=[{'task_id': 't1', 'track_index': 0}])
    out = merge_wishlist_run_status(primary, [])
    assert out is primary


def test_two_siblings_merge_tasks_with_reindexed_track_index():
    """Both siblings locally start at track_index 0 — after merge,
    indices are globally unique 0..N-1."""
    primary = _status(
        'downloading',
        analysis_results=[
            {'track_index': 0, 'track': {'name': 'A1'}, 'found': False, 'confidence': 0.0},
            {'track_index': 1, 'track': {'name': 'A2'}, 'found': False, 'confidence': 0.0},
        ],
        tasks=[
            {'task_id': 'task-a1', 'track_index': 0, 'status': 'downloading'},
            {'task_id': 'task-a2', 'track_index': 1, 'status': 'downloading'},
        ],
    )
    sibling = _status(
        'downloading',
        analysis_results=[
            {'track_index': 0, 'track': {'name': 'B1'}, 'found': False, 'confidence': 0.0},
        ],
        tasks=[
            {'task_id': 'task-b1', 'track_index': 0, 'status': 'searching'},
        ],
    )

    merged = merge_wishlist_run_status(primary, [sibling])

    # Three globally-unique track indices.
    assert [r['track_index'] for r in merged['analysis_results']] == [0, 1, 2]
    # Each task's track_index re-indexed to match its analysis_result.
    indices_by_task = {t['task_id']: t['track_index'] for t in merged['tasks']}
    assert indices_by_task == {'task-a1': 0, 'task-a2': 1, 'task-b1': 2}
    # Tasks sorted by their new track_index.
    assert [t['task_id'] for t in merged['tasks']] == ['task-a1', 'task-a2', 'task-b1']


def test_phase_aggregation_least_complete_pre_terminal_wins():
    """analysis + downloading + complete → analysis."""
    primary = _status('complete')
    sibling1 = _status('downloading')
    sibling2 = _status('analysis')
    merged = merge_wishlist_run_status(primary, [sibling1, sibling2])
    assert merged['phase'] == 'analysis'


def test_phase_aggregation_album_downloading_wins_over_downloading():
    primary = _status('downloading')
    sibling = _status('album_downloading')
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['phase'] == 'album_downloading'


def test_phase_aggregation_all_complete_returns_complete():
    primary = _status('complete')
    sibling1 = _status('complete')
    merged = merge_wishlist_run_status(primary, [sibling1])
    assert merged['phase'] == 'complete'


def test_phase_aggregation_mixed_complete_and_other_returns_downloading():
    """A finished sibling alongside a still-downloading sibling
    surfaces 'downloading' (the run isn't done)."""
    primary = _status('complete')
    sibling = _status('downloading')
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['phase'] == 'downloading'


def test_phase_aggregation_error_is_sticky():
    """If any sibling errored, the merged phase is 'error' even
    if other siblings are still running. Modal should show the
    failure so the user notices."""
    primary = _status('downloading')
    sibling = _status('error')
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['phase'] == 'error'


def test_analysis_progress_summed_across_siblings():
    primary = _status(
        'analysis',
        analysis_progress={'total': 10, 'processed': 7},
    )
    sibling = _status(
        'analysis',
        analysis_progress={'total': 5, 'processed': 2},
    )
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['analysis_progress'] == {'total': 15, 'processed': 9}


def test_album_bundle_picks_active_sibling_over_idle():
    """Primary is past its bundle stage (state='staged');
    sibling is currently downloading_release. Merge surfaces the
    active sibling's bundle so the progress bar stays useful."""
    primary = _status(
        'downloading',
        album_bundle={'state': 'staged', 'progress': 100, 'release': 'PRISM (Deluxe)'},
    )
    sibling = _status(
        'album_downloading',
        album_bundle={'state': 'downloading_release', 'progress': 42, 'release': '1432'},
    )
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['album_bundle']['release'] == '1432'
    assert merged['album_bundle']['progress'] == 42


def test_album_bundle_falls_back_when_no_active_sibling():
    primary = _status(
        'complete',
        album_bundle={'state': 'staged', 'progress': 100, 'release': 'PRISM (Deluxe)'},
    )
    sibling = _status(
        'complete',
        album_bundle={'state': 'staged', 'progress': 100, 'release': '1432'},
    )
    merged = merge_wishlist_run_status(primary, [sibling])
    # Falls back to primary's bundle (first non-empty).
    assert merged['album_bundle']['release'] == 'PRISM (Deluxe)'


def test_active_count_summed_across_siblings():
    primary = _status('downloading', active_count=2)
    sibling = _status('downloading', active_count=1)
    merged = merge_wishlist_run_status(primary, [sibling])
    assert merged['active_count'] == 3


def test_primary_playlist_id_preserved():
    primary = _status('downloading', playlist_id='wishlist', playlist_name='Wishlist (Auto)')
    sibling = _status('downloading', playlist_id='wishlist', playlist_name='Wishlist (Album: 1432)')
    merged = merge_wishlist_run_status(primary, [sibling])
    # Primary's playlist_name + playlist_id propagate (it's the row the modal opened against).
    assert merged['playlist_id'] == 'wishlist'
    assert merged['playlist_name'] == 'Wishlist (Auto)'
