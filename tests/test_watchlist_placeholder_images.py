"""breakbot and billie eilish on boulder's watchlist showed a mic icon while
every other artist had a photo. their image_url was a deezer "no picture" url:

    https://cdn-images.dzcdn.net/images/artist//1000x1000-000000-80-0-0.jpg
    https://cdn-images.dzcdn.net/images/artist/d41d8cd98f00b204e9800998ecf8427e/...

an empty picture hash, or the md5 of an empty string. well formed, https,
never renders. every "is there an image" check looked for http and let it
through, so the backfill never looked further, and both artists had plex
thumbs in the library the watchlist never consulted.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.metadata.artwork import is_placeholder_image_url, usable_image_url

EMPTY_HASH = "https://cdn-images.dzcdn.net/images/artist//1000x1000-000000-80-0-0.jpg"
MD5_EMPTY = "https://cdn-images.dzcdn.net/images/artist/d41d8cd98f00b204e9800998ecf8427e/1000x1000-000000-80-0-0.jpg"
REAL = "https://cdn-images.dzcdn.net/images/artist/fd606e35a50b9c5cc4d30838714ae5df/1000x1000-000000-80-0-0.jpg"


def test_deezer_no_picture_urls_are_placeholders():
    assert is_placeholder_image_url(EMPTY_HASH)
    assert is_placeholder_image_url(MD5_EMPTY)
    assert is_placeholder_image_url("https://api.deezer.com/images/cover//500x500.jpg")


def test_real_urls_and_empties_are_not():
    assert not is_placeholder_image_url(REAL)
    assert not is_placeholder_image_url("https://i.scdn.co/image/ab6761610000e5eb")
    assert not is_placeholder_image_url("/api/image-cache/" + "a" * 64)
    assert not is_placeholder_image_url(None)
    assert not is_placeholder_image_url("")
    # an empty hash on some other host is not deezer's marker
    assert not is_placeholder_image_url("https://example.invalid/images/artist//x.jpg")


def test_usable_means_present_and_not_a_placeholder():
    assert usable_image_url(REAL)
    assert not usable_image_url(EMPTY_HASH)
    assert not usable_image_url("None")
    assert not usable_image_url("  ")
    assert not usable_image_url(None)


# ── the database refuses to store one ────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / "wl.db"))


def _image(db, name):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT image_url FROM watchlist_artists WHERE artist_name = ?",
                            (name,)).fetchone()["image_url"]
    finally:
        conn.close()


def test_update_refuses_a_placeholder_and_keeps_what_was_there(db):
    assert db.add_artist_to_watchlist("9635624", "Billie Eilish", source="deezer")
    assert db.update_watchlist_artist_image("9635624", REAL) is True
    assert db.update_watchlist_artist_image("9635624", MD5_EMPTY) is False
    assert _image(db, "Billie Eilish") == REAL


def test_library_thumbs_by_name_is_case_insensitive_and_skips_empty(db):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES (?,?,?)",
                     ("486570", "Breakbot", "/library/metadata/486570/thumb/1776139955"))
        conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES (?,?,?)",
                     ("1", "No Thumb", None))
        conn.commit()
    finally:
        conn.close()
    thumbs = db.get_library_artist_thumbs_by_name(["breakbot", "NO THUMB", "Nobody"])
    assert thumbs == {"breakbot": "/library/metadata/486570/thumb/1776139955"}
    assert db.get_library_artist_thumbs_by_name([]) == {}


# ── the list endpoint falls back to the library thumb ────────────────────────

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-wlimg-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'wlimg.db'))


def test_list_endpoint_serves_the_library_thumb_for_a_placeholder(monkeypatch):
    web_server = pytest.importorskip('web_server')
    from api import artist_watchlist as wl
    database = web_server.get_database()
    profile = wl.get_current_profile_id()

    assert database.add_artist_to_watchlist("272927292", "Breakbot", profile_id=profile, source="deezer")
    assert database.add_artist_to_watchlist("41X1TR6hrK8Q2ZCpp2EqCz", "bbno$", profile_id=profile, source="spotify")
    conn = database._get_connection()
    try:
        # written straight in: this is the state an existing install is in
        conn.execute("UPDATE watchlist_artists SET image_url = ? WHERE artist_name = 'Breakbot'", (EMPTY_HASH,))
        conn.execute("UPDATE watchlist_artists SET image_url = ? WHERE artist_name = 'bbno$'", (REAL,))
        conn.execute("INSERT OR REPLACE INTO artists (id, name, thumb_url) VALUES (?,?,?)",
                     ("486570", "Breakbot", "/library/metadata/486570/thumb/1776139955"))
        conn.commit()
    finally:
        conn.close()
    # the plex path normalizes through config we don't have here; pin what it produces
    monkeypatch.setattr("core.metadata.artwork.normalize_image_url", lambda u: f"/api/image-cache/{'b' * 64}")

    try:
        body = web_server.app.test_client().get('/api/watchlist/artists').get_json()
        by_name = {a['artist_name']: a['image_url'] for a in body['artists']}
        assert by_name['bbno$'] == REAL, "a real stored image is left alone"
        assert by_name['Breakbot'] == f"/api/image-cache/{'b' * 64}", "the placeholder gives way to the library thumb"
        assert _image(database, 'Breakbot') == EMPTY_HASH, "resolved at read time, not written back"
    finally:
        conn = database._get_connection()
        try:
            conn.execute("DELETE FROM watchlist_artists WHERE artist_name IN ('Breakbot', 'bbno$')")
            conn.execute("DELETE FROM artists WHERE id = '486570'")
            conn.commit()
        finally:
            conn.close()
