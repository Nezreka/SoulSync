"""Flask-level tests for POST /api/library/v2/<entity>/<id>/enrich (docs §44).

``run_enrichment`` is injected (mirrors ``web_server._run_single_enrichment``)
so these tests never touch a real provider — they assert the route's own
logic: legacy-id resolution, service validation, the "no legacy record"
guard, and that a successful call resyncs the lib2 row from the (now
enriched) legacy row.
"""

from __future__ import annotations

import sqlite3

import pytest

flask = pytest.importorskip("flask")


class FakeDB:
    def __init__(self, path: str):
        self.database_path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def api(tmp_path):
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.executescript(
        """
        CREATE TABLE artists(
            id INTEGER PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT,
            summary TEXT, style TEXT, mood TEXT, label TEXT, banner_url TEXT
        );
        CREATE TABLE albums(
            id INTEGER PRIMARY KEY, title TEXT, thumb_url TEXT, genres TEXT,
            label TEXT, explicit INTEGER, upc TEXT
        );
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, bpm REAL, explicit INTEGER,
            genius_lyrics TEXT, copyright TEXT
        );
        """
    )
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Drake', 501)"
    )
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO artists(id, name) VALUES(501, 'Drake')")

    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Views', 601)", (artist_id,)
    )
    album_id = cur.lastrowid
    cur.execute("INSERT INTO albums(id, title) VALUES(601, 'Views')")

    # A discography-only album — never had a legacy counterpart.
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Discography Only', NULL)", (artist_id,)
    )
    no_legacy_album_id = cur.lastrowid

    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, legacy_track_id) "
        "VALUES(?, 'One Dance', 701)", (album_id,)
    )
    track_id = cur.lastrowid
    cur.execute("INSERT INTO tracks(id, title) VALUES(701, 'One Dance')")
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    calls = []

    def fake_run_enrichment(service, entity_type, legacy_id, name, artist_name):
        calls.append({
            "service": service, "entity_type": entity_type, "legacy_id": legacy_id,
            "name": name, "artist_name": artist_name,
        })
        if service == "genius" and entity_type == "album":
            return {"success": False, "error": "Genius does not support album enrichment"}
        # Simulate the worker having just written fresh data into the legacy row.
        conn2 = db._get_connection()
        if entity_type == "artist":
            conn2.execute("UPDATE artists SET genres=? WHERE id=?", ('["rap","hip hop"]', legacy_id))
        elif entity_type == "album":
            conn2.execute("UPDATE albums SET label=? WHERE id=?", ("OVO Sound", legacy_id))
        else:
            conn2.execute("UPDATE tracks SET bpm=? WHERE id=?", (104.0, legacy_id))
        conn2.commit()
        conn2.close()
        return {"success": True, "message": f"{service} lookup complete for {entity_type}"}

    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
        run_enrichment=fake_run_enrichment,
    )
    ids = {
        "artist": artist_id, "album": album_id,
        "no_legacy_album": no_legacy_album_id, "track": track_id,
    }
    yield app.test_client(), db, ids, calls


def test_enrich_artist_delegates_and_resyncs(api):
    client, db, ids, calls = api
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/enrich",
                       json={"service": "lastfm"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["resynced"] is True
    assert calls == [{
        "service": "lastfm", "entity_type": "artist", "legacy_id": 501,
        "name": "Drake", "artist_name": "",
    }]

    conn = db._get_connection()
    row = conn.execute("SELECT genres FROM lib2_artists WHERE id=?", (ids["artist"],)).fetchone()
    conn.close()
    assert row["genres"] == '["rap", "hip hop"]'


def test_enrich_album_passes_artist_name_and_resyncs(api):
    client, db, ids, calls = api
    resp = client.post(f"/api/library/v2/albums/{ids['album']}/enrich",
                       json={"service": "deezer"})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert calls[0] == {
        "service": "deezer", "entity_type": "album", "legacy_id": 601,
        "name": "Views", "artist_name": "Drake",
    }
    conn = db._get_connection()
    row = conn.execute("SELECT label FROM lib2_albums WHERE id=?", (ids["album"],)).fetchone()
    conn.close()
    assert row["label"] == "OVO Sound"


def test_enrich_track_resyncs_bpm(api):
    client, db, ids, _calls = api
    resp = client.post(f"/api/library/v2/tracks/{ids['track']}/enrich",
                       json={"service": "musicbrainz"})
    assert resp.status_code == 200
    assert resp.get_json()["resynced"] is True
    conn = db._get_connection()
    row = conn.execute("SELECT bpm FROM lib2_tracks WHERE id=?", (ids["track"],)).fetchone()
    conn.close()
    assert row["bpm"] == 104.0


def test_enrich_rejects_service_unsupported_for_entity_type(api):
    client, _db, ids, calls = api
    resp = client.post(f"/api/library/v2/albums/{ids['album']}/enrich",
                       json={"service": "genius"})
    assert resp.status_code == 400
    assert "does not support album" in resp.get_json()["error"]
    assert calls == []  # rejected before ever calling the worker


def test_enrich_rejects_unknown_service(api):
    client, _db, ids, _calls = api
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/enrich",
                       json={"service": "not-a-real-service"})
    assert resp.status_code == 400


def test_enrich_requires_service(api):
    client, _db, ids, _calls = api
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/enrich", json={})
    assert resp.status_code == 400
    assert "service is required" in resp.get_json()["error"]


def test_enrich_rejects_unsupported_entity(api):
    client, _db, ids, _calls = api
    resp = client.post(f"/api/library/v2/playlists/{ids['artist']}/enrich",
                       json={"service": "lastfm"})
    assert resp.status_code == 400


def test_enrich_404_for_missing_entity(api):
    client, _db, _ids, _calls = api
    resp = client.post("/api/library/v2/artists/999999/enrich", json={"service": "lastfm"})
    assert resp.status_code == 404


def test_enrich_409_when_entity_has_no_legacy_record(api):
    """A discography-only album (never imported from the legacy library) has
    no legacy row to enrich — must be a clear, honest error, not a crash."""
    client, _db, ids, calls = api
    resp = client.post(f"/api/library/v2/albums/{ids['no_legacy_album']}/enrich",
                       json={"service": "deezer"})
    assert resp.status_code == 409
    assert "no legacy library record" in resp.get_json()["error"]
    assert calls == []


def test_enrich_returns_503_when_not_wired(tmp_path):
    """register_library_v2_routes without run_enrichment (e.g. an older
    web_server.py wiring) must fail closed with a clear error, not 500."""
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Drake', 501)")
    artist_id = conn.execute("SELECT id FROM lib2_artists").fetchone()["id"]
    conn.commit()
    conn.close()

    db = FakeDB(db_path)
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes
    register_library_v2_routes(
        app,
        get_database=lambda: db,
        config_get=lambda key, default=None: {"features.library_v2": True}.get(key, default),
        config_manager=None,
        profile_id_getter=lambda: 1,
    )
    resp = app.test_client().post(f"/api/library/v2/artists/{artist_id}/enrich",
                                  json={"service": "lastfm"})
    assert resp.status_code == 503
