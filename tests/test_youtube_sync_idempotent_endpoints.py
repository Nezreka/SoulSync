"""#702: cancel/reset/delete of a mirrored-playlist sync whose in-memory state is
gone (restart/eviction) must return success, not 404 'YouTube playlist not found'
— otherwise the playlist is permanently wedged."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-ytsync-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'y.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


_GONE = 'state_wiped_by_restart_hash'


def test_cancel_missing_state_is_success(client):
    r = client.post(f'/api/youtube/sync/cancel/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True


def test_reset_missing_state_is_success(client):
    r = client.post(f'/api/youtube/reset/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True


def test_delete_missing_state_is_success(client):
    r = client.delete(f'/api/youtube/delete/{_GONE}')
    assert r.status_code == 200 and r.get_json().get('success') is True


def test_reset_mirrored_playlist_clears_db_discovery(client):
    import json
    db = web_server.get_database()
    pl_id = db.mirror_playlist(
        source='lastfm',
        source_playlist_id='test_radio_selfie',
        name='Test Radio',
        tracks=[
            {
                'track_name': 'Track 1',
                'artist_name': 'Artist 1',
                'album_name': 'Album 1',
                'duration_ms': 180000,
                'source_track_id': 't1',
            },
            {
                'track_name': 'Track 2',
                'artist_name': 'Artist 2',
                'album_name': 'Album 2',
                'duration_ms': 200000,
                'source_track_id': 't2',
            },
        ],
        profile_id=1,
    )
    tracks = db.get_mirrored_playlist_tracks(pl_id)
    t1_id = tracks[0]['id']
    t2_id = tracks[1]['id']

    db.update_mirrored_track_extra_data(t1_id, {
        'discovered': True,
        'matched_data': {'name': 'Match 1'},
        'confidence': 95,
        'provider': 'spotify',
    })
    db.update_mirrored_track_extra_data(t2_id, {
        'discovered': True,
        'matched_data': {'name': 'Match 2'},
        'confidence': 100,
        'provider': 'spotify',
        'manual_match': True,
    })

    # Add an entry in discovery_match_cache for track 1
    db.save_discovery_cache_match('track 1', 'artist 1', 'spotify', 0.95, {'id': 'sp1'})

    url_hash = f"mirrored_{pl_id}"
    r = client.post(f'/api/youtube/reset/{url_hash}')
    assert r.status_code == 200

    updated_tracks = db.get_mirrored_playlist_tracks(pl_id)
    assert updated_tracks[0]['extra_data'] is None
    assert updated_tracks[1]['extra_data'] is not None
    extra2 = json.loads(updated_tracks[1]['extra_data']) if isinstance(updated_tracks[1]['extra_data'], str) else updated_tracks[1]['extra_data']
    assert extra2.get('manual_match') is True

    # Verify discovery_match_cache for track 1 was deleted
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM discovery_match_cache WHERE normalized_title = ? AND normalized_artist = ?",
            ('track 1', 'artist 1')
        )
        assert cursor.fetchone()[0] == 0

    # Call prepare-discovery: only manual match remains cached (1 of 2)
    r_prep = client.post(f'/api/mirrored-playlists/{pl_id}/prepare-discovery')
    assert r_prep.status_code == 200
    prep_data = r_prep.get_json()
    assert prep_data['cached_matches'] == 1
    assert prep_data['has_pending'] is True
