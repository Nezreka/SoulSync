"""the album art picker writes to the same three places the artist one does.

found while answering "does the album pick stick?": it stuck in the db (locked)
but the rest of the path was a thinner copy of the artist one:

- ``_derive_album_folder`` did ``int(album_id)``, which raised for every
  jellyfin/navidrome album (text ids), was swallowed, and meant cover.jpg was
  never written for those servers;
- nothing was pushed to the media server's poster, so plex/jellyfin kept
  showing the old cover;
- a pasted url that returned an html page was pinned without a look.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-albumart-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'albumart.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')
from api import artist_detail as _artist_detail  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
HTML = b"<!doctype html><html><body>not art</body></html>"
CUSTOM = "https://example.invalid/custom-cover.jpg"


@pytest.fixture
def client():
    return web_server.app.test_client()


# The endpoint addresses an album by the id the picker was opened with, which on
# the v2 catalogue is the lib2 row id; ALBUM_ID is the row the fixture created.
ALBUM_ID: dict = {}


@pytest.fixture
def seeded():
    from tests.support.catalogue_seed import seed_album, seed_artist

    db = web_server.get_database()
    conn = db._get_connection()
    try:
        artist = seed_artist(conn, server_id='art-9', name='Parity Artist',
                             server_source='jellyfin',
                             image_url='http://server/artist.jpg')
        album = seed_album(conn, server_id='jf-guid-9', title='Parity Album',
                           artist_id=artist, server_source='jellyfin',
                           image_url='http://server/cover.jpg')
        ALBUM_ID['album'] = album
        ALBUM_ID['artist'] = artist
        conn.commit()
    finally:
        conn.close()
    yield db
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM lib2_albums WHERE id = ?", (album,))
        conn.execute("DELETE FROM lib2_artists WHERE id = ?", (artist,))
        conn.commit()
    finally:
        conn.close()


def _row(db, album_id):
    conn = db._get_connection()
    try:
        return conn.execute(
            "SELECT image_url AS thumb_url, art_locked FROM lib2_albums WHERE id = ?",
            (album_id,)).fetchone()
    finally:
        conn.close()


# ── the folder is found for text ids ─────────────────────────────────────────

def test_album_folder_resolves_for_a_text_id(tmp_path, monkeypatch):
    track_file = tmp_path / "01 - song.flac"
    track_file.write_bytes(b"x")
    seen = {}

    class _Track:
        file_path = str(track_file)

    class _Db:
        def get_tracks_by_album(self, album_id):
            seen['id'] = album_id
            return [_Track()]

    monkeypatch.setattr(_artist_detail, '_resolve_library_file_path', lambda p: p)
    folder = _artist_detail._derive_album_folder(_Db(), 'jf-guid-9')
    assert folder == str(tmp_path)
    # the id reaches the db as the string it is; int() would have raised first
    assert seen['id'] == 'jf-guid-9'


# ── the endpoint: validate, lock, push, write ────────────────────────────────

class _ServerAlbum:
    ratingKey = 'jf-guid-9'
    title = 'Parity Album'


class _JellyfinLike:
    def __init__(self):
        self.pushed = []

    def get_album_by_id(self, album_id):
        return _ServerAlbum() if album_id == 'jf-guid-9' else None

    def update_album_poster(self, album, image_data):
        self.pushed.append((album.ratingKey, image_data))
        return True


class _Engine:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        return self._client


class _Config:
    def get_active_media_server(self):
        return 'jellyfin'


def test_pick_locks_pushes_and_writes_cover(client, seeded, tmp_path, monkeypatch):
    fake = _JellyfinLike()
    monkeypatch.setattr(_artist_detail, '_download_art', lambda url: JPEG)
    monkeypatch.setattr(_artist_detail, '_derive_album_folder', lambda db, album_id: str(tmp_path))
    monkeypatch.setattr(_artist_detail, 'media_server_engine', _Engine(fake))
    monkeypatch.setattr(_artist_detail, 'config_manager', _Config())

    r = client.post(f"/api/album/{ALBUM_ID['album']}/art", json={'url': CUSTOM})

    assert r.status_code == 200
    body = r.get_json()
    assert body['server_updated'] is True
    assert body['cover_written'] is True
    assert fake.pushed == [('jf-guid-9', JPEG)]
    assert (tmp_path / 'cover.jpg').read_bytes() == JPEG
    row = _row(seeded, ALBUM_ID['album'])
    assert row['thumb_url'] == CUSTOM and row['art_locked'] == 1


def test_pick_reaches_a_plex_album_through_fetch_item(client, seeded, monkeypatch):
    class _PlexServer:
        def fetchItem(self, key):
            assert key == '/library/metadata/jf-guid-9'
            return _ServerAlbum()

    class _PlexLike:
        server = _PlexServer()

        def __init__(self):
            self.pushed = []

        def update_album_poster(self, album, image_data):
            self.pushed.append(album.ratingKey)
            return True

    fake = _PlexLike()
    monkeypatch.setattr(_artist_detail, '_download_art', lambda url: JPEG)
    monkeypatch.setattr(_artist_detail, '_derive_album_folder', lambda db, album_id: None)
    monkeypatch.setattr(_artist_detail, 'media_server_engine', _Engine(fake))
    monkeypatch.setattr(_artist_detail, 'config_manager', _Config())

    r = client.post(f"/api/album/{ALBUM_ID['album']}/art", json={'url': CUSTOM})
    assert r.get_json()['server_updated'] is True
    assert fake.pushed == ['jf-guid-9']


def test_html_url_is_refused_before_anything_is_pinned(client, seeded, monkeypatch):
    fake = _JellyfinLike()
    monkeypatch.setattr(_artist_detail, '_download_art', lambda url: HTML)
    monkeypatch.setattr(_artist_detail, 'media_server_engine', _Engine(fake))
    monkeypatch.setattr(_artist_detail, 'config_manager', _Config())

    r = client.post(f"/api/album/{ALBUM_ID['album']}/art", json={'url': 'https://example.invalid/page'})

    assert r.status_code == 400
    assert fake.pushed == []
    row = _row(seeded, ALBUM_ID['album'])
    assert row['thumb_url'] == 'http://server/cover.jpg' and not row['art_locked']


def test_failed_download_still_locks_the_pick(client, seeded, monkeypatch):
    # hotlink-protected art renders in the browser; the db pick must not
    # depend on the server being able to fetch it
    fake = _JellyfinLike()
    monkeypatch.setattr(_artist_detail, '_download_art', lambda url: None)
    monkeypatch.setattr(_artist_detail, '_derive_album_folder', lambda db, album_id: None)
    monkeypatch.setattr(_artist_detail, 'media_server_engine', _Engine(fake))
    monkeypatch.setattr(_artist_detail, 'config_manager', _Config())

    r = client.post(f"/api/album/{ALBUM_ID['album']}/art", json={'url': CUSTOM})
    body = r.get_json()
    assert r.status_code == 200
    assert body['server_updated'] is False and body['cover_written'] is False
    assert fake.pushed == []
    assert _row(seeded, ALBUM_ID['album'])['art_locked'] == 1
