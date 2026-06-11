"""Artist 'Sync' = a single-artist deep scan: stale removal is a server-diff
(tracks the media server no longer has), with the same safety net + admin gate as
the whole-library deep scan. #828 pattern."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-sync2-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _seed(artist_id, track_ids):
    """Plex artist + album + tracks (server_source='plex')."""
    db = web_server.get_database()
    aid, album_id = str(artist_id), artist_id * 10
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, server_source) VALUES (?, ?, 'plex')",
                     (aid, f'Artist {aid}'))
        conn.execute("INSERT OR REPLACE INTO albums (id, title, artist_id) VALUES (?, 'Alb', ?)",
                     (album_id, aid))
        for tid in track_ids:
            conn.execute("INSERT OR REPLACE INTO tracks (id, album_id, artist_id, title, track_number, "
                         "duration, file_path, server_source) VALUES (?, ?, ?, ?, 1, 100, ?, 'plex')",
                         (tid, album_id, aid, f'T{tid}', f'/m/{tid}.flac'))
        conn.commit()


def _track_ids(artist_id):
    db = web_server.get_database()
    with db._get_connection() as conn:
        return {r['id'] for r in conn.execute("SELECT id FROM tracks WHERE artist_id = ?", (str(artist_id),))}


def _mock_server_pull(monkeypatch, *, seen, success=True):
    """Fake the media server returning `seen` track IDs for the artist."""
    class _FakeServer:
        def fetchItem(self, _id):
            return SimpleNamespace(title=None)  # truthy artist, no name change

    class _FakePlex:
        server = _FakeServer()

    class _FakeEngine:
        def client(self, name):
            return _FakePlex() if name == 'plex' else None

    monkeypatch.setattr(web_server, 'media_server_engine', _FakeEngine())

    class _FakeWorker:
        def __init__(self, *a, **k):
            self.database = None
        def _process_artist_with_content(self, server_artist, skip_existing_tracks=False, seen_track_ids=None):
            if seen_track_ids is not None:
                seen_track_ids.update(seen)
            return (success, 'ok', 0, 0)

    monkeypatch.setattr('core.database_update_worker.DatabaseUpdateWorker', _FakeWorker)


def test_removes_tracks_the_server_no_longer_has(client, monkeypatch):
    _seed(7001, [f't{i}' for i in range(1, 11)])           # t1..t10 in DB
    _mock_server_pull(monkeypatch, seen={f't{i}' for i in range(1, 9)})  # server has t1..t8
    body = client.post('/api/library/artist/7001/sync').get_json()
    assert body['success'] and body['removal_skipped'] is False
    assert body['stale_removed'] == 2                      # t9,t10 gone from server
    assert _track_ids(7001) == {f't{i}' for i in range(1, 9)}


def test_guard_skips_when_most_tracks_unseen(client, monkeypatch):
    _seed(7002, [f't{i}' for i in range(1, 11)])
    _mock_server_pull(monkeypatch, seen={'t1'})            # 9/10 unseen → flaky response
    body = client.post('/api/library/artist/7002/sync').get_json()
    assert body['removal_skipped'] is True
    assert body['stale_removed'] == 0
    assert len(_track_ids(7002)) == 10                    # nothing deleted


def test_failed_pull_skips_removal(client, monkeypatch):
    _seed(7003, [f't{i}' for i in range(1, 11)])
    _mock_server_pull(monkeypatch, seen=set(), success=False)  # pull failed → no trustworthy view
    body = client.post('/api/library/artist/7003/sync').get_json()
    assert body['removal_skipped'] is True
    assert body['stale_removed'] == 0
    assert len(_track_ids(7003)) == 10


def test_sync_is_admin_only(client, monkeypatch):
    _seed(7004, ['t1', 't2'])
    _mock_server_pull(monkeypatch, seen={'t1', 't2'})
    nonadmin = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin
    assert client.post('/api/library/artist/7004/sync').status_code == 403
    assert len(_track_ids(7004)) == 2                     # untouched
