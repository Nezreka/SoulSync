"""#1613 follow-up, through the real routes: the tidal fetch hands the counts
to the sync page, the mirror post stores them, the mirrored playlist detail
returns them, and identify on a mirror shows them again."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-skipped-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.tidal_client import Playlist, Track  # noqa: E402


@pytest.fixture
def client():
    return web_server.app.test_client()


class _Tidal:
    def __init__(self, playlist):
        self.playlist = playlist

    def is_authenticated(self):
        return True

    def get_playlist(self, playlist_id):
        return self.playlist


def _gappy(pid):
    return Playlist(
        id=pid, name="Cam's playlist",
        tracks=[Track(id="1", name="t1", artists=["a"], album="al", duration_ms=1000)],
        skipped_videos=1, unavailable_tracks=29,
    )


def _post_mirror(client, pid, **extra):
    body = {
        'source': 'tidal', 'source_playlist_id': pid, 'name': "Cam's playlist",
        'tracks': [{'track_name': 't1', 'artist_name': 'a', 'source_track_id': '1'}],
        **extra,
    }
    r = client.post('/api/mirror-playlist', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['playlist_id']


def test_tidal_playlist_fetch_returns_the_counts(client, monkeypatch):
    import api.source_playlists as sp
    monkeypatch.setattr(sp, 'get_tidal_client_for_profile', lambda: _Tidal(_gappy('F1')))
    body = client.get('/api/tidal/playlist/F1').get_json()
    assert body['source_skipped'] == {'videos': 1, 'unavailable': 29}

    whole = _gappy('F1')
    whole.skipped_videos = whole.unavailable_tracks = 0
    monkeypatch.setattr(sp, 'get_tidal_client_for_profile', lambda: _Tidal(whole))
    body = client.get('/api/tidal/playlist/F1').get_json()
    assert 'source_skipped' in body and body['source_skipped'] is None


def test_mirror_post_stores_and_detail_returns_the_counts(client):
    pl_id = _post_mirror(client, 'M1', source_skipped={'videos': 1, 'unavailable': 29})
    detail = client.get(f'/api/mirrored-playlists/{pl_id}').get_json()
    assert detail['source_skipped'] == {'videos': 1, 'unavailable': 29}

    # a post without the key (other sources, older clients) keeps them
    _post_mirror(client, 'M1')
    detail = client.get(f'/api/mirrored-playlists/{pl_id}').get_json()
    assert detail['source_skipped'] == {'videos': 1, 'unavailable': 29}

    # null clears them
    _post_mirror(client, 'M1', source_skipped=None)
    detail = client.get(f'/api/mirrored-playlists/{pl_id}').get_json()
    assert detail['source_skipped'] is None


def test_identify_on_a_mirror_shows_the_saved_counts(client):
    pl_id = _post_mirror(client, 'I1', source_skipped={'unavailable': 29})
    r = client.post(f'/api/mirrored-playlists/{pl_id}/prepare-discovery')
    assert r.status_code == 200, r.get_json()
    body = client.get(f'/api/youtube/discovery/status/mirrored_{pl_id}').get_json()
    assert body['source_skipped'] == {'videos': 0, 'unavailable': 29}


def test_tidal_identify_refreshes_the_mirrors_counts(client, monkeypatch):
    import api.source_playlists as sp
    pl_id = _post_mirror(client, 'D1')
    monkeypatch.setattr(sp, '_tidal_client', lambda: _Tidal(_gappy('D1')))
    monkeypatch.setattr(sp.tidal_discovery_executor, 'submit', lambda *a, **k: None)
    monkeypatch.setitem(sp.tidal_discovery_states, 'D1', {'phase': 'discovered'})
    monkeypatch.setattr(sp, 'add_activity_item', lambda *a, **k: None)
    r = client.post('/api/tidal/discovery/start/D1')
    assert r.status_code == 200, r.get_json()
    detail = client.get(f'/api/mirrored-playlists/{pl_id}').get_json()
    assert detail['source_skipped'] == {'videos': 1, 'unavailable': 29}
