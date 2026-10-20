"""Tests for post-download playlist synchronization registry and execution."""

import time
from unittest.mock import MagicMock, patch
import pytest

from core.sync.post_download_sync import (
    register_completed_playlist_batch,
    drain_pending_playlist_syncs,
    get_pending_playlist_syncs_count,
    clear_pending_playlist_syncs,
    sync_pending_downloaded_playlists,
)


@pytest.fixture(autouse=True)
def clean_registry():
    clear_pending_playlist_syncs()
    yield
    clear_pending_playlist_syncs()


class FakeDatabase:
    def __init__(self, assignments=None):
        self.assignments = assignments or {}

    def resolve_mirrored_playlist_assignment(self, playlist_ref=None, playlist_name=None, profile_id=1, **kw):
        key = (str(playlist_ref) if playlist_ref is not None else None, playlist_name, profile_id)
        if key in self.assignments:
            return self.assignments[key]
        # Fallback by ref
        for (r, n, p), val in self.assignments.items():
            if (r is not None and r == str(playlist_ref)) or (n is not None and n == playlist_name):
                return val
        return None


def test_register_batch_direct_mirrored_id():
    batch = {
        'mirrored_playlist_id': 42,
        'playlist_name': 'My Top Tracks',
        'profile_id': 1,
    }
    registered = register_completed_playlist_batch(batch)
    assert registered == [42]
    assert get_pending_playlist_syncs_count() == 1

    drained = drain_pending_playlist_syncs()
    assert len(drained) == 1
    assert drained[0]['mirrored_playlist_id'] == 42
    assert drained[0]['playlist_name'] == 'My Top Tracks'
    assert drained[0]['profile_id'] == 1


def test_register_batch_by_name_and_ref_with_database():
    db = FakeDatabase({
        ('spotify-pl-123', 'Party Mix', 1): {'id': 99, 'name': 'Party Mix'},
    })
    batch = {
        'is_playlist_batch': True,
        'source_playlist_name': 'Party Mix',
        'source_playlist_id': 'spotify-pl-123',
        'playlist_id': 'wishlist',
        'profile_id': 1,
    }
    registered = register_completed_playlist_batch(batch, database=db)
    assert registered == [99]
    assert get_pending_playlist_syncs_count() == 1

    drained = drain_pending_playlist_syncs()
    assert len(drained) == 1
    assert drained[0]['mirrored_playlist_id'] == 99


def test_register_batch_tasks_snapshot_playlist_tracks():
    db = FakeDatabase({
        ('sp-roadtrip', 'Road Trip', 1): {'id': 77, 'name': 'Road Trip'},
    })
    batch = {
        'playlist_name': 'Wishlist (Album: Rumours)',
        'playlist_id': 'wishlist',
        'profile_id': 1,
    }
    tasks_snapshot = {
        't1': {
            'status': 'completed',
            'track_info': {
                'source_type': 'playlist',
                'source_info': {
                    'playlist_id': 'sp-roadtrip',
                    'playlist_name': 'Road Trip',
                },
                'profile_id': 1,
            },
        },
        't2': {
            'status': 'failed',
            'track_info': {
                'source_type': 'playlist',
                'source_info': {
                    'playlist_id': 'sp-other',
                    'playlist_name': 'Other Playlist',
                },
            },
        },
    }
    registered = register_completed_playlist_batch(batch, tasks_snapshot=tasks_snapshot, database=db)
    assert registered == [77]
    assert get_pending_playlist_syncs_count() == 1


def test_register_batch_no_completed_tasks_ignored():
    db = FakeDatabase()
    batch = {
        'playlist_name': 'Wishlist (Residual)',
        'playlist_id': 'wishlist',
        'profile_id': 1,
    }
    tasks_snapshot = {
        't1': {
            'status': 'error',
            'track_info': {'name': 'Track 1'},
        },
    }
    registered = register_completed_playlist_batch(batch, tasks_snapshot=tasks_snapshot, database=db)
    assert registered == []
    assert get_pending_playlist_syncs_count() == 0


def test_deduplication_across_multiple_batches():
    batch1 = {'mirrored_playlist_id': 10, 'playlist_name': 'Hits', 'profile_id': 1}
    batch2 = {'mirrored_playlist_id': 10, 'playlist_name': 'Hits', 'profile_id': 1}
    register_completed_playlist_batch(batch1)
    register_completed_playlist_batch(batch2)
    assert get_pending_playlist_syncs_count() == 1

    drained = drain_pending_playlist_syncs()
    assert len(drained) == 1
    assert drained[0]['mirrored_playlist_id'] == 10


def test_drain_clears_queue_atomically():
    batch = {'mirrored_playlist_id': 5, 'playlist_name': 'Favs', 'profile_id': 1}
    register_completed_playlist_batch(batch)
    drained1 = drain_pending_playlist_syncs()
    assert len(drained1) == 1

    drained2 = drain_pending_playlist_syncs()
    assert drained2 == []


def test_drain_filters_expired_items(monkeypatch):
    batch = {'mirrored_playlist_id': 5, 'playlist_name': 'Favs', 'profile_id': 1}
    register_completed_playlist_batch(batch)

    # Simulate registration happening 2 days ago
    from core.sync import post_download_sync
    with post_download_sync._lock:
        for k in post_download_sync._pending_playlists:
            post_download_sync._pending_playlists[k]['registered_at'] = time.time() - 200000

    drained = drain_pending_playlist_syncs(max_age_seconds=86400.0)
    assert drained == []
    assert get_pending_playlist_syncs_count() == 0


def test_sync_pending_downloaded_playlists_empty():
    deps = MagicMock()
    result = sync_pending_downloaded_playlists(deps)
    assert result['status'] == 'skipped'
    assert 'No downloaded playlists pending sync' in result['reason']


def test_sync_pending_downloaded_playlists_triggers_auto_sync():
    batch = {'mirrored_playlist_id': 42, 'playlist_name': 'Synthwave', 'profile_id': 1}
    register_completed_playlist_batch(batch)

    deps = MagicMock()
    with patch('core.automation.handlers.sync_playlist.auto_sync_playlist') as mock_sync:
        mock_sync.return_value = {'status': 'started'}
        res = sync_pending_downloaded_playlists(deps, auto_id='auto-123')

        assert res['status'] == 'started'
        assert res['synced_playlists'] == ['Synthwave']
        assert mock_sync.call_count == 1
        call_config = mock_sync.call_args[0][0]
        assert call_config['playlist_id'] == '42'
        assert call_config['skip_wishlist_add'] is True
        assert call_config['force_sync'] is True
        assert call_config['_automation_id'] == 'auto-123'

    # Queue should now be drained
    assert get_pending_playlist_syncs_count() == 0


def test_sync_pending_downloaded_playlists_handles_errors():
    batch = {'mirrored_playlist_id': 42, 'playlist_name': 'Broken', 'profile_id': 1}
    register_completed_playlist_batch(batch)

    deps = MagicMock()
    with patch('core.automation.handlers.sync_playlist.auto_sync_playlist') as mock_sync:
        mock_sync.side_effect = RuntimeError("Sync error")
        res = sync_pending_downloaded_playlists(deps, auto_id='auto-123')

        assert res['status'] == 'error'
        assert len(res['errors']) == 1
        assert 'Sync error' in res['errors'][0]


class CountingDatabase(FakeDatabase):
    def __init__(self, assignments=None):
        super().__init__(assignments)
        self.calls = 0

    def resolve_mirrored_playlist_assignment(self, *a, **kw):
        self.calls += 1
        return super().resolve_mirrored_playlist_assignment(*a, **kw)


def test_a_digit_string_ref_is_an_upstream_id_not_a_mirror_pk():
    """deezer playlist ids are all digits. a digit string has to be resolved,
    not taken as a mirrored_playlists pk."""
    db = CountingDatabase({('908622995', 'Deezer Mix', 1): {'id': 7, 'name': 'Deezer Mix'}})
    batch = {
        'is_playlist_batch': True,
        'source_playlist_name': 'Deezer Mix',
        'source_playlist_id': '908622995',
        'profile_id': 1,
    }
    assert register_completed_playlist_batch(batch, database=db) == [7]
    assert db.calls == 1
    assert [p['mirrored_playlist_id'] for p in drain_pending_playlist_syncs()] == [7]


def test_an_unresolved_digit_string_registers_nothing():
    batch = {
        'is_playlist_batch': True,
        'source_playlist_name': 'Gone',
        'source_playlist_id': '908622995',
        'profile_id': 1,
    }
    assert register_completed_playlist_batch(batch, database=FakeDatabase()) == []
    assert get_pending_playlist_syncs_count() == 0


def test_a_playlist_batch_looks_its_playlist_up_once():
    db = CountingDatabase({('sp-big', 'Big', 1): {'id': 5, 'name': 'Big'}})
    tasks = {
        f't{i}': {'status': 'completed', 'track_info': {
            'source_info': {'playlist_id': 'sp-big', 'playlist_name': 'Big'}}}
        for i in range(50)
    }
    assert register_completed_playlist_batch({'profile_id': 1}, tasks_snapshot=tasks, database=db) == [5]
    assert db.calls == 1
