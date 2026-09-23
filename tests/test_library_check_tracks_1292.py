"""#1292: the import page flagged 'The Noose' as already in the library because
the artist had 'The Doomed' on another album. hits the real
/api/library/check-tracks route the matcher calls, with a stub db."""

from __future__ import annotations

import os
import tempfile
import types

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-1292-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'a.db'))
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


def _row(title, album, path):
    return types.SimpleNamespace(id=title, title=title, album_title=album,
                                 file_path=path, bitrate=900)


@pytest.fixture
def check(monkeypatch):
    rows = [
        _row('The Doomed', 'Eat The Elephant', '/music/APC/[2018] Eat The Elephant/04 - The Doomed.flac'),
        _row('Weak and Powerless', 'Thirteenth Step', '/music/APC/[2003] Thirteenth Step/02.flac'),
    ]

    class _DB:
        def search_tracks(self, **kwargs):
            return rows

    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'plex')
    client = web_server.app.test_client()

    def _post(names, album):
        r = client.post('/api/library/check-tracks', json={
            'artist_name': 'A Perfect Circle', 'album_name': album,
            'tracks': [{'name': n} for n in names],
        })
        assert r.status_code == 200
        return r.get_json()['owned_tracks']

    return _post


def test_the_noose_is_not_the_doomed(check):
    owned = check(['The Noose'], 'Thirteenth Step')
    assert owned['The Noose'] == {'owned': False}


def test_the_noose_is_not_the_doomed_when_the_album_is_new(check):
    # album not in the library at all -> #808 falls back to artist-wide titles
    owned = check(['The Noose'], 'Mer de Noms')
    assert owned['The Noose'] == {'owned': False}


def test_a_real_owned_track_still_shows(check):
    owned = check(['Weak And Powerless'], 'Thirteenth Step')
    assert owned['Weak And Powerless']['owned'] is True
