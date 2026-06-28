from datetime import datetime, timedelta

from web_server import (
    _format_playlist_sync_status,
    _resolve_spotify_playlist_sync_status,
)


class _StubDatabase:
    def __init__(self, mirrored):
        self._mirrored = mirrored

    def get_mirrored_playlist_by_source(self, source, source_playlist_id, profile_id):
        assert source == 'spotify'
        assert source_playlist_id == 'spotify-playlist-1'
        assert profile_id == 7
        return self._mirrored


def _status(minutes_ago=0, **overrides):
    timestamp = (datetime(2026, 6, 25, 12, 0, 0) - timedelta(minutes=minutes_ago)).isoformat()
    return {'last_synced': timestamp, **overrides}


def test_resolve_spotify_playlist_sync_status_uses_mirrored_auto_sync_status():
    sync_statuses = {
        'auto_mirror_42': _status(matched_tracks=12),
    }

    status = _resolve_spotify_playlist_sync_status(
        'spotify-playlist-1',
        sync_statuses,
        database=_StubDatabase({'id': 42}),
        profile_id=7,
    )

    assert status['matched_tracks'] == 12


def test_resolve_spotify_playlist_sync_status_prefers_newest_status():
    sync_statuses = {
        'spotify-playlist-1': _status(minutes_ago=20, matched_tracks=1),
        'auto_mirror_42': _status(minutes_ago=5, matched_tracks=2),
    }

    status = _resolve_spotify_playlist_sync_status(
        'spotify-playlist-1',
        sync_statuses,
        database=_StubDatabase({'id': 42}),
        profile_id=7,
    )

    assert status['matched_tracks'] == 2


def test_format_playlist_sync_status_treats_missing_snapshot_as_synced():
    status = _status()

    assert _format_playlist_sync_status(status, 'current-snapshot') == 'Synced: Jun 25, 12:00'


def test_format_playlist_sync_status_marks_snapshot_mismatch_as_last_sync():
    status = _status(snapshot_id='old-snapshot')

    assert _format_playlist_sync_status(status, 'current-snapshot') == 'Last Sync: Jun 25, 12:00'
