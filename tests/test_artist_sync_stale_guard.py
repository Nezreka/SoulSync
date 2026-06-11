"""Artist 'Sync' button (enhanced tab) must not wipe an artist when storage is
unreachable, and must stay admin-only (it deletes tracks + albums). #828 pattern."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-stale-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 's.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _seed(artist_id, *, missing, present, tmp_path):
    """Artist with server_source NULL (skips the media-server pull phase), an
    album, ``present`` tracks pointing at real files + ``missing`` at dead paths."""
    db = web_server.get_database()
    album_id = artist_id * 10
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, server_source) VALUES (?, ?, NULL)",
                     (artist_id, f'Artist {artist_id}'))
        conn.execute("INSERT OR REPLACE INTO albums (id, title, artist_id) VALUES (?, 'Alb', ?)",
                     (album_id, artist_id))
        tid = artist_id * 1000
        for i in range(present):
            p = tmp_path / f"{artist_id}_present_{i}.flac"
            p.write_bytes(b'\x00\x00')
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path) "
                         "VALUES (?, ?, ?, ?, 1, 100, ?)", (tid, album_id, artist_id, f'P{i}', str(p)))
            tid += 1
        for i in range(missing):
            conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path) "
                         "VALUES (?, ?, ?, ?, 1, 100, ?)", (tid, album_id, artist_id, f'M{i}', f'/nonexistent/{artist_id}_{i}.flac'))
            tid += 1
        conn.commit()


def _track_count(artist_id):
    db = web_server.get_database()
    with db._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM tracks WHERE artist_id = ?", (artist_id,)).fetchone()['c']


def test_all_files_missing_skips_removal_and_keeps_tracks(client, tmp_path):
    _seed(9001, missing=8, present=0, tmp_path=tmp_path)
    body = client.post('/api/library/artist/9001/sync').get_json()
    assert body['success'] is True
    assert body['removal_skipped'] is True          # guard tripped
    assert body['stale_removed'] == 0
    assert _track_count(9001) == 8                   # nothing deleted — storage looked down


def test_a_few_missing_files_are_removed(client, tmp_path):
    _seed(9002, missing=2, present=8, tmp_path=tmp_path)
    body = client.post('/api/library/artist/9002/sync').get_json()
    assert body['success'] is True
    assert body['removal_skipped'] is False
    assert body['stale_removed'] == 2               # only the genuinely-gone ones
    assert _track_count(9002) == 8


def test_sync_is_admin_only(client, tmp_path):
    _seed(9003, missing=2, present=2, tmp_path=tmp_path)
    nonadmin = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin
    assert client.post('/api/library/artist/9003/sync').status_code == 403
    assert _track_count(9003) == 4                   # untouched
