"""Tests for make_wishlist_batch_row — the single source of truth for a wishlist
download_batches row, shared by the auto and manual flows so their batch shapes
can't drift apart.
"""

from __future__ import annotations

from core.wishlist.processing import make_wishlist_batch_row


_CORE_KEYS = {
    'phase', 'playlist_id', 'playlist_name', 'queue', 'active_count',
    'max_concurrent', 'queue_index', 'analysis_total', 'analysis_processed',
    'analysis_results', 'permanently_failed_tracks', 'cancelled_tracks',
    'force_download_all', 'profile_id', 'is_album_download', 'album_context',
    'artist_context', 'wishlist_run_id',
}


def _row(**overrides):
    base = dict(
        playlist_id='wishlist', playlist_name='Wishlist', track_count=3,
        max_concurrent=4, profile_id=1, phase='analysis',
    )
    base.update(overrides)
    return make_wishlist_batch_row(**base)


def test_core_fields_always_present_and_consistent():
    row = _row()
    assert _CORE_KEYS <= set(row.keys())
    # Fresh-batch invariants.
    assert row['queue'] == [] and row['active_count'] == 0 and row['queue_index'] == 0
    assert row['analysis_processed'] == 0
    assert row['analysis_results'] == [] and row['permanently_failed_tracks'] == []
    assert row['cancelled_tracks'] == set()
    assert row['force_download_all'] is True
    assert row['analysis_total'] == 3
    assert row['max_concurrent'] == 4
    assert row['profile_id'] == 1


def test_residual_defaults_are_per_track():
    row = _row()
    assert row['is_album_download'] is False
    assert row['album_context'] is None and row['artist_context'] is None
    assert row['wishlist_run_id'] is None


def test_album_batch_carries_context():
    row = _row(
        phase='queued', run_id='run-1', is_album=True,
        album_context={'name': 'Album One'}, artist_context={'name': 'Artist 1'},
    )
    assert row['phase'] == 'queued'
    assert row['is_album_download'] is True
    assert row['album_context'] == {'name': 'Album One'}
    assert row['artist_context'] == {'name': 'Artist 1'}
    assert row['wishlist_run_id'] == 'run-1'


def test_extra_fields_merged_for_auto():
    row = _row(extra_fields={
        'auto_initiated': True, 'auto_processing_timestamp': 123.0,
        'current_cycle': 'albums',
    })
    assert row['auto_initiated'] is True
    assert row['auto_processing_timestamp'] == 123.0
    assert row['current_cycle'] == 'albums'


def test_manual_row_has_no_auto_fields():
    """Manual rows must not carry the auto-only fields (no extra_fields)."""
    row = _row(phase='analysis')
    assert 'auto_initiated' not in row
    assert 'current_cycle' not in row


def test_fresh_rows_do_not_share_mutable_state():
    """Each row must get its OWN queue/list/set — not a shared reference that
    one batch's tasks could leak into another's."""
    a = _row()
    b = _row()
    a['queue'].append('task-1')
    a['cancelled_tracks'].add('x')
    assert b['queue'] == []
    assert b['cancelled_tracks'] == set()
    assert b['analysis_results'] == []


def test_auto_and_manual_rows_share_identical_key_shape():
    """The drift-prevention guarantee: an auto album row and a manual album row
    expose the same set of keys (modulo the auto-only extras), so the modal /
    status code sees a consistent shape from both flows."""
    manual = _row(phase='analysis', run_id='r', is_album=True,
                  album_context={'name': 'A'}, artist_context={'name': 'B'})
    auto = _row(phase='queued', run_id='r', is_album=True,
                album_context={'name': 'A'}, artist_context={'name': 'B'},
                extra_fields={'auto_initiated': True, 'current_cycle': 'albums'})
    # Auto is a strict superset (the auto-only extras); the shared core is identical.
    assert set(manual.keys()) <= set(auto.keys())
    assert set(auto.keys()) - set(manual.keys()) == {'auto_initiated', 'current_cycle'}
