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
    def __init__(self, entry, resolutions=None):
        self._entry = entry
        self._resolutions = resolutions or {}
        self.batch_calls = []

    def get_sync_history_entry(self, entry_id, profile_id=None):
        return self._entry

    def resolve_library_tracks(self, pairs):
        self.batch_calls.append(list(pairs))
        out = {}
        for title, artist in pairs:
            row = self._resolutions.get((title, artist))
            if row:
                out[(title.lower(), artist.lower())] = row
        return out


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_resolves_matched_tracks_and_skips_the_rest(client, monkeypatch):
    entry = {
        'playlist_name': 'Hot Hits',
        'tracks_json': json.dumps([
            {'name': 'Owned Song', 'artists': [{'name': 'Ado'}]},
            {'name': 'Missing Song', 'artists': [{'name': 'Nobody'}]},
            {'name': 'String Artist', 'artists': ['Baauer']},
        ]),
    }
    db = _FakeDb(entry, {
        ('Owned Song', 'Ado'): {
            'id': 't1', 'title': 'Owned Song', 'artist_name': 'Ado',
            'album_title': 'Kyougen', 'file_path': '/m/o.flac',
            'thumb_url': None, 'bitrate': 1411, 'duration': 200,
            'artist_id': 'ar1', 'album_id': 'al1',
        },
    })
    monkeypatch.setattr(web_server, 'MusicDatabase', lambda: db)
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
                 'image_url': None, 'bitrate': 1411, 'duration': 200,
                 'artist_id': 'ar1', 'album_id': 'al1'}
    # ONE batch resolution for the whole playlist (the per-track version
    # full-scanned the tracks table once per song — minutes on a big
    # library), and artists-as-strings parse too (older cached entries).
    assert len(db.batch_calls) == 1
    assert ('String Artist', 'Baauer') in db.batch_calls[0]


def test_missing_entry_is_404(client, monkeypatch):
    monkeypatch.setattr(web_server, 'MusicDatabase', lambda: _FakeDb(None))
    r = client.get('/api/sync/history/999/play')
    assert r.status_code == 404
