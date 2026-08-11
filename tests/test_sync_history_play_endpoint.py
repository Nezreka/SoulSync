"""GET /api/sync/history/<id>/play — the Listen button's resolver.

Stubbed db + resolver (the hermetic pattern from the radio endpoint test):
this file owns the endpoint's parsing — spotify-shaped cached tracks (name +
artists as dicts) resolved per track, unmatched ones skipped, `total` honest
about the playlist's real size — not the matcher, which has its own coverage.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-syncplay-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 's.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


class _FakeDb:
    def __init__(self, entry):
        self._entry = entry

    def get_sync_history_entry(self, entry_id, profile_id=None):
        return self._entry


@pytest.fixture
def client():
    return web_server.app.test_client()


def _install(monkeypatch, entry, resolutions):
    """Stub the db handle and the per-track resolver. `resolutions` maps
    (title, artist) -> resolver row (or None)."""
    monkeypatch.setattr(web_server, 'MusicDatabase', lambda: _FakeDb(entry))

    calls = []

    def fake_resolve(database, fixer, title, artist):
        calls.append((title, artist))
        return resolutions.get((title, artist))

    monkeypatch.setattr(web_server._stats_queries, 'resolve_track', fake_resolve)
    return calls


def test_resolves_matched_tracks_and_skips_the_rest(client, monkeypatch):
    entry = {
        'playlist_name': 'Hot Hits',
        'tracks_json': json.dumps([
            {'name': 'Owned Song', 'artists': [{'name': 'Ado'}]},
            {'name': 'Missing Song', 'artists': [{'name': 'Nobody'}]},
            {'name': 'String Artist', 'artists': ['Baauer']},
        ]),
    }
    calls = _install(monkeypatch, entry, {
        ('Owned Song', 'Ado'): {
            'id': 't1', 'title': 'Owned Song', 'artist_name': 'Ado',
            'album_title': 'Kyougen', 'file_path': '/m/o.flac',
            'image_url': 'k.jpg', 'bitrate': 1411, 'duration': 200,
            'artist_id': 'ar1', 'album_id': 'al1',
        },
    })
    r = client.get('/api/sync/history/7/play')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    assert body['name'] == 'Hot Hits'
    assert body['total'] == 3                     # honest playlist size
    assert len(body['tracks']) == 1               # only what the library has
    t = body['tracks'][0]
    # The radio-row shape the player's one mapper expects.
    assert t == {'id': 't1', 'title': 'Owned Song', 'artist': 'Ado',
                 'album': 'Kyougen', 'file_path': '/m/o.flac',
                 'image_url': 'k.jpg', 'bitrate': 1411, 'duration': 200,
                 'artist_id': 'ar1', 'album_id': 'al1'}
    # Artists-as-strings parse too (older cached entries).
    assert ('String Artist', 'Baauer') in calls


def test_missing_entry_is_404(client, monkeypatch):
    monkeypatch.setattr(web_server, 'MusicDatabase', lambda: _FakeDb(None))
    r = client.get('/api/sync/history/999/play')
    assert r.status_code == 404
