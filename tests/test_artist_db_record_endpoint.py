"""GET /api/artist/<id>/record — the artist-detail "DB Record" inspector source.
Returns the full artists row (JSON text columns decoded) + owned counts, 404 if
the artist isn't in the library."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-arec-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'a.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _insert_artist():
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO artists (id, name, genres, musicbrainz_id, "
            "musicbrainz_match_status, lastfm_listeners) VALUES (?,?,?,?,?,?)",
            ('99001', 'Test Artist', '["rock", "metal"]',
             'mbid-123', 'matched', 4242),
        )
        conn.commit()
    finally:
        conn.close()


def test_record_returns_full_row_with_decoded_json(client):
    _insert_artist()
    r = client.get('/api/artist/99001/record')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] is True
    rec = body['record']
    assert rec['name'] == 'Test Artist'
    assert rec['genres'] == ['rock', 'metal']            # JSON text decoded to a list
    assert rec['musicbrainz_id'] == 'mbid-123'
    assert rec['musicbrainz_match_status'] == 'matched'
    assert rec['lastfm_listeners'] == 4242
    assert 'counts' in body and 'albums' in body['counts'] and 'tracks' in body['counts']
    assert body['artist_id'] == '99001'


def test_missing_artist_is_404(client):
    r = client.get('/api/artist/does-not-exist-77777/record')
    assert r.status_code == 404
    assert r.get_json()['success'] is False


def test_track_record_returns_full_library_row(client):
    _insert_artist()
    conn = web_server.get_database()._get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO albums(id, artist_id, title) VALUES ('99003', '99001', 'Local album')")
        conn.execute("INSERT OR REPLACE INTO tracks(id, album_id, artist_id, title, file_path, track_number) VALUES ('99002', '99003', '99001', 'Local song', '/music/song.flac', 7)")
        conn.commit()
    finally:
        conn.close()
    response = client.get('/api/track/99002/record')
    assert response.status_code == 200
    record = response.get_json()['record']
    assert record['title'] == 'Local song' and record['track_number'] == 7
    assert 'musicbrainz_recording_id' in record
    assert client.get('/api/track/not-owned/record').status_code == 404
