"""GET /api/library/radio — mode dispatch.

Seeded mode (track_id) is the classic similar-tracks refill; seedless mode
(?library=1) is the Library Radio starter (ranked-random across the whole
library). Neither param → 400. The selection logic itself is pinned in
tests/radio/ — this covers the endpoint's dispatch + response shape.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-radio-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'r.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _seed_library():
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO artists (id, name) VALUES (?,?)",
            ('rad-ar1', 'Radio Artist'))
        conn.execute(
            "INSERT OR REPLACE INTO albums (id, artist_id, title) VALUES (?,?,?)",
            ('rad-al1', 'rad-ar1', 'Radio Album'))
        conn.execute(
            "INSERT OR REPLACE INTO albums (id, artist_id, title) VALUES (?,?,?)",
            ('rad-al2', 'rad-ar1', 'Radio Album Two'))
        for tid, alid in (('rad-t1', 'rad-al1'), ('rad-t2', 'rad-al2')):
            conn.execute(
                "INSERT OR REPLACE INTO tracks (id, album_id, artist_id, title, file_path) "
                "VALUES (?,?,?,?,?)",
                (tid, alid, 'rad-ar1', f'Track {tid}', f'/m/{tid}.flac'))
        conn.commit()
    finally:
        conn.close()


def test_no_seed_and_no_library_flag_is_400(client):
    r = client.get('/api/library/radio')
    assert r.status_code == 400
    assert r.get_json()['success'] is False


def test_library_mode_returns_seedless_tracks(client):
    _seed_library()
    r = client.get('/api/library/radio?library=1&limit=10')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    ids = {t['id'] for t in body['tracks']}
    assert {'rad-t1', 'rad-t2'} <= ids


def test_seeded_mode_still_dispatches_on_track_id(client):
    _seed_library()
    r = client.get('/api/library/radio?track_id=rad-t1&limit=10')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    ids = [t['id'] for t in body['tracks']]
    assert 'rad-t2' in ids            # same artist, other album
    assert 'rad-t1' not in ids        # seed always excluded
