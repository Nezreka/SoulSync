"""Flask-level tests for the scoped Automatic Search endpoint (deep-dive C1).

``POST /api/library/v2/<entity>/<id>/search`` mirrors wanted artist/album
scope into Wishlist, while a direct track search dispatches a transient
server-owned payload without a Wishlist write. Both must stay scoped and
must not dispatch a file that already satisfies its quality profile.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    """MusicDatabase stand-in: real sqlite connection + recorded mirror calls."""

    def __init__(self, path: str):
        self.database_path = path
        self.wishlist_adds = []

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_to_wishlist(self, payload, source_type="unknown", source_info=None,
                        user_initiated=False, profile_id=1, quality_profile_id=None,
                        raise_on_error=False):
        self.wishlist_adds.append({"id": payload.get("id"), "profile_id": profile_id})
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        return True

    def add_artist_to_watchlist(self, ext_id, name, profile_id, source, raise_on_error=False):
        return True

    def remove_artist_from_watchlist(self, ext_id, profile_id, raise_on_error=False):
        return True


def _build_api(tmp_path, *, dispatcher=None, direct_dispatcher=None):
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
    ep_id = _album("Best EP", "ep")

    def _track(album_id, title, spotify_id=None):
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id) "
            "VALUES(?,?,1,?)", (album_id, title, spotify_id))
        track_id = cur.lastrowid
        cur.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id) "
                    "VALUES(?,?)", (track_id, artist_id))
        return track_id

    album_track = _track(views_id, "One Dance", spotify_id="sp-t1")
    ep_track = _track(ep_id, "EP Song", spotify_id="sp-t2")
    cur.execute("INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
                "VALUES(?, '/m/one-dance.flac', 'flac', 1000)", (album_track,))
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    db.active_profile = 1
    db.config = {"features.library_v2": True}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: db.config.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: db.active_profile,
        scoped_wishlist_search_dispatcher=dispatcher,
        scoped_track_search_dispatcher=direct_dispatcher,
    )
    ids = {"artist": artist_id, "views": views_id, "ep": ep_id,
           "album_track": album_track, "ep_track": ep_track}
    return app.test_client(), db, ids


@pytest.fixture
def api(tmp_path):
    yield _build_api(tmp_path)


def _await_job(client, job_id):
    for _ in range(200):
        status = client.get(
            "/api/library/v2/jobs/status", query_string={"job_id": job_id}
        ).get_json()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("scoped search job never finished")


def test_track_scope_searches_only_that_track(api):
    client, db, ids = api
    client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor", json={"monitored": True})

    response = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/search")
    assert response.status_code == 200
    job_id = response.get_json()["job_id"]
    status = _await_job(client, job_id)

    assert status["error"] is None
    assert status["result"] == {
        "checked": 1, "queued": 0, "searching": 1, "batch_id": None,
        "dispatch_error": None,
    }
    assert {a["id"] for a in db.wishlist_adds} == {"sp-t2"}


def test_direct_track_search_bypasses_monitor_filter_without_changing_it(api):
    """§52.6: a direct track click is one-shot explicit intent, not a hidden
    monitoring mutation.  Artist/album searches remain wanted-only."""
    client, db, ids = api
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE lib2_tracks SET monitored=0 WHERE id=?", (ids["ep_track"],)
        )
        conn.commit()

    response = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/search")
    status = _await_job(client, response.get_json()["job_id"])

    assert status["error"] is None
    assert status["result"] == {
        "checked": 1,
        "queued": 0,
        "searching": 1,
        "batch_id": None,
        "dispatch_error": None,
    }
    assert db.wishlist_adds == []
    with db._get_connection() as conn:
        track = conn.execute(
            "SELECT monitored FROM lib2_tracks WHERE id=?", (ids["ep_track"],)
        ).fetchone()
        wanted = conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks WHERE track_id=?",
            (ids["ep_track"],),
        ).fetchone()
    assert track["monitored"] == 0
    assert wanted["wanted"] == 0


def test_album_scope_excludes_tracks_that_already_have_a_satisfying_file(api):
    client, db, ids = api
    client.post(f"/api/library/v2/albums/{ids['views']}/monitor", json={"monitored": True})

    response = client.post(f"/api/library/v2/albums/{ids['views']}/search")
    job_id = response.get_json()["job_id"]
    status = _await_job(client, job_id)

    assert status["error"] is None
    # album_track already has a file and no upgrade-capable profile — nothing
    # to search for, even though it's monitored.
    assert status["result"]["checked"] == 1
    assert status["result"]["searching"] == 0
    assert db.wishlist_adds == []


def test_artist_scope_only_dispatches_the_missing_track(api):
    client, db, ids = api
    client.post(f"/api/library/v2/artists/{ids['artist']}/monitor", json={"monitored": True})

    response = client.post(f"/api/library/v2/artists/{ids['artist']}/search")
    job_id = response.get_json()["job_id"]
    status = _await_job(client, job_id)

    assert status["error"] is None
    assert status["result"]["checked"] == 2  # both tracks under the artist
    assert status["result"]["searching"] == 1  # only the missing one
    assert {a["id"] for a in db.wishlist_adds} == {"sp-t2"}


def test_direct_dispatcher_receives_transient_payload_and_profile(tmp_path):
    calls = []

    def _dispatcher(tracks, profile_id):
        calls.append((list(tracks), profile_id))
        return {"success": True, "batch_id": "batch-123"}

    client, db, ids = _build_api(tmp_path, direct_dispatcher=_dispatcher)
    client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor", json={"monitored": True})
    db.wishlist_adds.clear()  # isolate Automatic Search from the monitor mirror

    response = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/search")
    job_id = response.get_json()["job_id"]
    status = _await_job(client, job_id)

    assert status["error"] is None
    assert status["result"]["batch_id"] == "batch-123"
    assert len(calls) == 1
    tracks, profile_id = calls[0]
    assert profile_id == 1
    assert len(tracks) == 1
    assert tracks[0]["id"] == "sp-t2"
    assert tracks[0]["source_info"]["lib2_track_id"] == ids["ep_track"]
    assert tracks[0]["_lib2_direct_search"] is True
    assert db.wishlist_adds == []


def test_dispatcher_failure_is_surfaced_not_silently_swallowed(tmp_path):
    """docs §69.3: a failed dispatch (e.g. the download executor rejected the
    submission) must not look like a successful "search started" — the UI
    would otherwise show an OK banner while nothing was actually queued."""

    def _dispatcher(track_ids, profile_id):
        return {"success": False, "error": "executor rejected submission"}

    client, _db, ids = _build_api(tmp_path, direct_dispatcher=_dispatcher)
    client.post(f"/api/library/v2/tracks/{ids['ep_track']}/monitor", json={"monitored": True})

    response = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/search")
    job_id = response.get_json()["job_id"]
    status = _await_job(client, job_id)

    assert status["error"] is None
    assert status["result"]["batch_id"] is None
    assert status["result"]["dispatch_error"] == "executor rejected submission"


def test_unknown_entity_rejected(api):
    client, _db, ids = api
    response = client.post(f"/api/library/v2/playlists/{ids['ep_track']}/search")
    assert response.status_code == 400


def test_missing_entity_404(api):
    client, _db, _ids = api
    response = client.post("/api/library/v2/tracks/999999/search")
    assert response.status_code == 404


def test_disabled_flag_guards_the_route(tmp_path):
    client, db, ids = _build_api(tmp_path)
    db.config["features.library_v2"] = False
    response = client.post(f"/api/library/v2/tracks/{ids['ep_track']}/search")
    assert response.status_code == 403
