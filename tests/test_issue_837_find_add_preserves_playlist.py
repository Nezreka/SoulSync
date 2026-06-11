"""#837 — manual Find & Add must NOT recreate the Jellyfin/Emby playlist.

Reporter (carlosjfcasero, Emby/Jellyfin): automations + auto-sync respect the
'append' sync mode and preserve the playlist's description/image, but manually
matching a missing track ("Find & add") recreated the whole playlist and wiped
them. Root cause: the add-track endpoint's Jellyfin branch called
`update_playlist(full list)` (delete + recreate) instead of the in-place
`append_to_playlist`. These pin that the endpoint now appends in place.

(Emby routes through the 'jellyfin' branch — no separate emby branch exists.)
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-837-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'i837.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

_GUID = 'aaaaaaaa-bbbb-cccc-dddd-000000000001'


class _FakeJellyfin:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.append_calls = []
        self.update_calls = []

    def get_playlist_tracks(self, pid):
        return [SimpleNamespace(ratingKey=str(r)) for r in self.existing]

    def append_to_playlist(self, name, tracks):
        self.append_calls.append((name, [getattr(t, 'id', None) for t in tracks]))
        return True

    def update_playlist(self, name, tracks):  # the destructive recreate path
        self.update_calls.append((name, tracks))
        return True


class _FakeEngine:
    def __init__(self, jf):
        self._jf = jf

    def client(self, name):
        return self._jf if name == 'jellyfin' else None


@pytest.fixture
def client():
    return web_server.app.test_client()


def _wire(monkeypatch, jf):
    monkeypatch.setattr(web_server, 'media_server_engine', _FakeEngine(jf))
    monkeypatch.setattr(web_server.config_manager, 'get_active_media_server', lambda: 'jellyfin')
    # the durable source->server match write touches the DB; not under test here
    monkeypatch.setattr(web_server, '_persist_find_and_add_match', lambda *a, **k: None)


def test_find_and_add_appends_in_place_not_recreate(client, monkeypatch):
    jf = _FakeJellyfin(existing=[])  # the missing track isn't on the server yet
    _wire(monkeypatch, jf)

    resp = client.post('/api/server/playlist/PL1/add-track',
                       json={'track_id': _GUID, 'playlist_name': 'Disney'})
    body = resp.get_json()

    assert body['success'] and body['message'] == 'Track added'
    assert len(jf.append_calls) == 1, 'should append in place'
    assert jf.update_calls == [], 'must NOT recreate the playlist (#837)'
    # append_to_playlist reads `.id` off the track — the endpoint must set it
    assert jf.append_calls[0][1] == [_GUID]


def test_find_and_add_link_to_existing_track_touches_nothing(client, monkeypatch):
    # Matching a source to a track already in the playlist is a LINK, not an add.
    jf = _FakeJellyfin(existing=[_GUID])
    _wire(monkeypatch, jf)

    resp = client.post('/api/server/playlist/PL1/add-track',
                       json={'track_id': _GUID, 'playlist_name': 'Disney',
                             'source_track_id': 'spotify-xyz'})
    body = resp.get_json()

    assert body['success'] and body['message'] == 'Track linked'
    assert jf.append_calls == [] and jf.update_calls == []
