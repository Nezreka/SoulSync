"""Flask-level tests for the Library v2 API (api/library_v2.py).

The core modules have their own unit tests; these cover the route layer's
own logic — artwork URL rewriting, monitor/profile cascades incl. the
consolidated-duplicate guard, delete cleanup, and input validation — against
a real (temp) SQLite schema with a fake MusicDatabase for the mirror calls.
"""

from __future__ import annotations

import sqlite3

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    """MusicDatabase stand-in: real sqlite connection + recorded mirror calls."""

    def __init__(self, path: str):
        self.database_path = path
        self.wishlist_adds = []
        self.wishlist_removes = []
        self.watchlist_adds = []
        self.watchlist_removes = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- wishlist/watchlist mirror surface (recorded, always succeeds) -------
    def add_to_wishlist(self, payload, source_type="unknown", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None):
        self.wishlist_adds.append({
            "id": payload.get("id"), "profile_id": profile_id,
            "quality_profile_id": quality_profile_id, "source_type": source_type,
        })
        return True

    def remove_from_wishlist(self, track_id, profile_id=1):
        self.wishlist_removes.append({"id": track_id, "profile_id": profile_id})
        return True

    def add_artist_to_watchlist(self, ext_id, name, profile_id, source):
        self.watchlist_adds.append({"ext_id": ext_id, "profile_id": profile_id})
        return True

    def remove_artist_from_watchlist(self, ext_id, profile_id):
        self.watchlist_removes.append({"ext_id": ext_id, "profile_id": profile_id})
        return True


@pytest.fixture
def api(tmp_path):
    """A test client over a seeded lib2 DB. Yields (client, FakeDB, ids)."""
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, sort_name, spotify_id, monitored) "
                "VALUES('Drake','Drake','sp-drake',0)")
    artist_id = cur.lastrowid

    def _album(title, album_type, monitored=0):
        cur.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored) "
            "VALUES(?,?,?,?)", (artist_id, title, album_type, monitored))
        album_id = cur.lastrowid
        cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                    (album_id, artist_id))
        return album_id

    views_id = _album("Views", "album")
    single_id = _album("One Dance", "single")
    ep_id = _album("Best EP", "ep")

    def _track(album_id, title, monitored=0, spotify_id=None, canonical=None):
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, monitored, "
            "spotify_id, canonical_track_id) VALUES(?,?,1,?,?,?)",
            (album_id, title, monitored, spotify_id, canonical))
        track_id = cur.lastrowid
        cur.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id) "
                    "VALUES(?,?)", (track_id, artist_id))
        return track_id

    # Canonical pair: the album version owns the file, the single variant was
    # consolidated away (no file, canonical link to the album version).
    album_track = _track(views_id, "One Dance", spotify_id="sp-t1")
    single_track = _track(single_id, "One Dance", canonical=album_track)
    ep_track = _track(ep_id, "EP Song", spotify_id="sp-t2")
    cur.execute("INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
                "VALUES(?, '/m/one-dance.flac', 'flac', 1000)", (album_track,))
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: (
            True if key == "features.library_v2" else default),
        config_manager=None,
        profile_id_getter=lambda: 7,
    )
    ids = {"artist": artist_id, "views": views_id, "single": single_id,
           "ep": ep_id, "album_track": album_track,
           "single_track": single_track, "ep_track": ep_track}
    yield app.test_client(), db, ids


def _conn(db: FakeDB) -> sqlite3.Connection:
    return db._get_connection()


def test_eps_get_local_artwork_urls(api):
    """Every release group — including EPs — must point at the local artwork
    endpoint, never at a raw DB image_url (which may be a media-server URL)."""
    client, _db, ids = api
    data = client.get(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert data["success"] is True
    for group in ("albums", "eps", "singles"):
        for entry in data["artist"][group]:
            assert entry["image_url"] == f"/api/library/v2/artwork/album/{entry['id']}"


def test_monitor_album_mirrors_with_active_profile(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['ep']}/monitor",
                       json={"monitored": True}).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        assert conn.execute("SELECT monitored FROM lib2_albums WHERE id=?",
                            (ids["ep"],)).fetchone()[0] == 1
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                            (ids["ep_track"],)).fetchone()[0] == 1
    # The wishlist mirror carries the ACTIVE user profile (7, from the
    # profile_id_getter) and the track's quality profile.
    assert db.wishlist_adds, "monitoring a fileless track must queue it"
    assert all(a["profile_id"] == 7 for a in db.wishlist_adds)
    assert all(a["quality_profile_id"] == 1 for a in db.wishlist_adds)


def test_profile_assign_skips_consolidated_duplicates(api):
    """An upgrade-policy profile auto-monitors an artist's tracks — but not a
    consolidated-away duplicate (no file, canonical partner owns the file)."""
    client, db, ids = api
    resp = client.post(
        f"/api/library/v2/artists/{ids['artist']}/quality-profile",
        json={"quality_profile_id": 2},  # seeded 'until_cutoff' profile
    ).get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        monitored = {r["id"]: r["monitored"] for r in conn.execute(
            "SELECT id, monitored FROM lib2_tracks")}
    assert monitored[ids["album_track"]] == 1
    assert monitored[ids["ep_track"]] == 1
    assert monitored[ids["single_track"]] == 0, (
        "the consolidated single variant must not be re-wanted")
    queued = {a["id"] for a in db.wishlist_adds}
    assert f"lib2-track:{ids['single_track']}" not in queued


def test_delete_artist_removes_rows_mirrors_and_artwork(api):
    client, db, ids = api
    # Cached artwork that must disappear with the entity.
    from core.library2.artwork import artwork_file, thumb_file
    art = artwork_file(db, "artist", ids["artist"])
    art.write_bytes(b"jpg")
    thumb = thumb_file(db, "album", ids["views"])
    thumb.write_bytes(b"jpg")

    resp = client.delete(f"/api/library/v2/artists/{ids['artist']}").get_json()
    assert resp["success"] is True
    with _conn(db) as conn:
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks", "lib2_track_files"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # Wishlist withdrawals went out for the artist's tracks; watchlist too.
    assert db.wishlist_removes
    assert db.watchlist_removes and db.watchlist_removes[0]["ext_id"] == "sp-drake"
    assert not art.exists()
    assert not thumb.exists()


def test_artist_list_rejects_non_numeric_page(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/artists?page=abc")
    assert resp.status_code == 400


def test_album_edit_refiles_release_type(api):
    client, db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                       json={"album_type": "ep"}).get_json()
    assert resp["success"] is True and resp["album_type"] == "ep"
    with _conn(db) as conn:
        assert conn.execute("SELECT album_type FROM lib2_albums WHERE id=?",
                            (ids["single"],)).fetchone()[0] == "ep"
    bad = client.post(f"/api/library/v2/albums/{ids['single']}/edit",
                      json={"album_type": "mixtape"})
    assert bad.status_code == 400


def test_refresh_busts_full_artwork_and_thumbnails(api):
    """Refresh must invalidate BOTH cached variants — the thumb wins the serve
    fast path, so a stale one would pin the old cover in lists forever."""
    client, db, ids = api
    from core.library2.artwork import artwork_file, thumb_file
    files = [
        artwork_file(db, "artist", ids["artist"]),
        thumb_file(db, "artist", ids["artist"]),
        artwork_file(db, "album", ids["views"]),
        thumb_file(db, "album", ids["views"]),
    ]
    for f in files:
        f.write_bytes(b"jpg")
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/refresh").get_json()
    assert resp["success"] is True
    for f in files:
        assert not f.exists(), f"{f.name} must be invalidated by refresh"
