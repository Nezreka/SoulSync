"""Flask-level tests for the lib2 reorganize routes (docs §50).

``core.library_reorganize``/``core.reorganize_queue`` are core-level (no
circular-import risk, unlike ``run_enrichment``) so the routes import them
directly rather than taking them injected — these tests monkeypatch those
module functions instead, mirroring ``test_enrich_endpoint.py``'s shape:
legacy-id resolution, the "no legacy record" 409, and that a successful call
delegates to the right planner/queue call with the right args.
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

    def get_album_display_meta(self, album_id):
        conn = self._get_connection()
        row = conn.execute(
            """SELECT al.title AS album_title, ar.id AS artist_id, ar.name AS artist_name
               FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE al.id=?""",
            (str(album_id),),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_artist_albums_for_reorganize(self, artist_id):
        conn = self._get_connection()
        rows = conn.execute(
            """SELECT al.id AS album_id, al.title AS album_title, ar.id AS artist_id,
                      ar.name AS artist_name
               FROM albums al JOIN artists ar ON al.artist_id = ar.id WHERE ar.id=?
               ORDER BY al.year ASC, al.title ASC""",
            (str(artist_id),),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
def reset_queue_singleton():
    from core.reorganize_queue import reset_queue_for_tests
    reset_queue_for_tests()
    yield
    reset_queue_for_tests()


@pytest.fixture
def api(tmp_path):
    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.executescript(
        """
        CREATE TABLE artists(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE albums(
            id INTEGER PRIMARY KEY, artist_id INTEGER, title TEXT, year INTEGER
        );
        """
    )
    cur = conn.cursor()
    cur.execute("INSERT INTO artists(id, name) VALUES(501, 'Drake')")
    cur.execute("INSERT INTO albums(id, artist_id, title, year) VALUES(601, 501, 'Views', 2016)")
    cur.execute("INSERT INTO albums(id, artist_id, title, year) VALUES(602, 501, 'One Dance', 2016)")

    cur.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Drake', 501)"
    )
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Views', 601)", (artist_id,)
    )
    album_id = cur.lastrowid

    # A discography-only album/artist — never had a legacy counterpart.
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Discography Only', NULL)", (artist_id,)
    )
    no_legacy_album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('New Artist', NULL)")
    no_legacy_artist_id = cur.lastrowid

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
    ids = {
        "artist": artist_id, "album": album_id,
        "no_legacy_album": no_legacy_album_id, "no_legacy_artist": no_legacy_artist_id,
    }
    yield app.test_client(), db, ids


# -- sources -------------------------------------------------------------


def test_global_sources_delegates(monkeypatch, api):
    client, _db, _ids = api
    monkeypatch.setattr(
        "core.library_reorganize.authed_sources",
        lambda: [{"source": "deezer", "label": "Deezer"}], raising=True,
    )
    resp = client.get("/api/library/v2/reorganize/sources")
    assert resp.status_code == 200
    assert resp.get_json()["sources"] == [{"source": "deezer", "label": "Deezer"}]


def test_album_sources_404_for_missing_album(api):
    client, _db, _ids = api
    resp = client.get("/api/library/v2/albums/999999/reorganize/sources")
    assert resp.status_code == 404


def test_album_sources_409_for_discography_only(api):
    client, _db, ids = api
    resp = client.get(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize/sources")
    assert resp.status_code == 409
    assert "Update Discography" in resp.get_json()["error"]


# -- preview ---------------------------------------------------------------


def test_preview_delegates_with_resolved_legacy_id(monkeypatch, api):
    client, _db, ids = api
    captured = {}

    def fake_preview(**kwargs):
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": [{"title": "One Dance"}]}

    monkeypatch.setattr("core.library_reorganize.preview_album_reorganize", fake_preview, raising=True)

    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/reorganize/preview",
        json={"source": "spotify", "mode": "tags"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "planned"
    assert captured["album_id"] == "601"
    assert captured["primary_source"] == "spotify"
    assert captured["metadata_source"] == "tags"


def test_preview_409_for_discography_only(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize/preview", json={})
    assert resp.status_code == 409


def test_preview_defaults_to_api_mode_with_empty_body(monkeypatch, api):
    client, _db, ids = api
    captured = {}

    def fake_preview(**kwargs):
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": []}

    monkeypatch.setattr("core.library_reorganize.preview_album_reorganize", fake_preview, raising=True)
    resp = client.post(f"/api/library/v2/albums/{ids['album']}/reorganize/preview")
    assert resp.status_code == 200
    assert captured["metadata_source"] == "api"
    assert captured["primary_source"] is None
    assert captured["strict_source"] is False


# -- apply (single album) ----------------------------------------------------


def test_apply_enqueues_resolved_legacy_album(api):
    client, _db, ids = api
    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/reorganize",
        json={"source": "deezer", "mode": "api"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["queued"] is True

    from core.reorganize_queue import get_queue
    snap = get_queue().snapshot()
    queued_ids = [item["album_id"] for item in snap["queued"]]
    active_id = snap["active"]["album_id"] if snap["active"] else None
    assert "601" in (queued_ids + ([active_id] if active_id else []))


def test_apply_409_for_discography_only_album(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize", json={})
    assert resp.status_code == 409


def test_apply_404_for_missing_album(api):
    client, _db, _ids = api
    resp = client.post("/api/library/v2/albums/999999/reorganize", json={})
    assert resp.status_code == 404


# -- reorganize-all (artist scope) -------------------------------------------


def test_reorganize_all_enqueues_every_album(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/artists/{ids['artist']}/reorganize-all", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["total_albums"] == 2
    assert body["enqueued"] == 2


def test_reorganize_all_409_for_discography_only_artist(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/artists/{ids['no_legacy_artist']}/reorganize-all", json={})
    assert resp.status_code == 409


def test_reorganize_all_404_for_missing_artist(api):
    client, _db, _ids = api
    resp = client.post("/api/library/v2/artists/999999/reorganize-all", json={})
    assert resp.status_code == 404
