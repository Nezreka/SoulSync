"""Flask-level tests for native Library-v2 reorganize routes."""

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
               FROM lib2_albums al JOIN lib2_artists ar ON al.primary_artist_id = ar.id
               WHERE al.id=? AND EXISTS (
                 SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
                 WHERE t.album_id=al.id AND f.file_state='active')""", (album_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_artist_albums_for_reorganize(self, artist_id):
        conn = self._get_connection()
        rows = conn.execute(
            """SELECT al.id AS album_id, al.title AS album_title, ar.id AS artist_id,
                      ar.name AS artist_name FROM lib2_albums al
               JOIN lib2_artists ar ON al.primary_artist_id = ar.id WHERE ar.id=?
               AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
                 WHERE t.album_id=al.id AND f.file_state='active')
               ORDER BY al.year ASC, al.title ASC""", (artist_id,),
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
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lib2_artists(name, legacy_artist_id) VALUES('Drake', 501)"
    )
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, legacy_album_id) "
        "VALUES(?, 'Views', 601)", (artist_id,)
    )
    album_id = cur.lastrowid
    second_album_id = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,year) VALUES(?,'One Dance',2016)",
        (artist_id,),
    ).lastrowid
    for owned_album, title in ((album_id, 'One Dance'), (second_album_id, 'Hotline Bling')):
        track_id = cur.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (owned_album, title),
        ).lastrowid
        cur.execute("INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES(?,?,1)",
                    (track_id, f'/music/{title}.flac'))

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


def test_album_sources_are_empty_for_discography_only(api):
    client, _db, ids = api
    resp = client.get(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize/sources")
    assert resp.status_code == 200
    assert resp.get_json()["sources"] == []


# -- preview ---------------------------------------------------------------


def test_preview_delegates_with_native_id(monkeypatch, api):
    """The native album id reaches the catalogue planner. A source and a mode
    in the body are accepted for an older client and then ignored — reorganize
    computes a path, and a path has no metadata source."""
    client, _db, ids = api
    captured = {}

    def fake_plan(conn, album_id, **kwargs):
        captured["album_id"] = album_id
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": [{"title": "One Dance"}]}

    monkeypatch.setattr(
        "core.library2.reorganize_plan.plan_album_reorganize", fake_plan, raising=True)

    resp = client.post(
        f"/api/library/v2/albums/{ids['album']}/reorganize/preview",
        json={"source": "spotify", "mode": "tags"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "planned"
    assert captured["album_id"] == ids["album"]
    assert callable(captured["build_final_path_fn"])
    assert "primary_source" not in captured
    assert "metadata_source" not in captured


def test_preview_404_for_discography_only(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize/preview", json={})
    assert resp.status_code == 404


def test_preview_needs_nothing_in_the_body(monkeypatch, api):
    """An empty body is the normal request now: nothing about a destination
    path varies per call, so two previews of one album cannot disagree."""
    client, _db, ids = api
    captured = {}

    def fake_plan(conn, album_id, **kwargs):
        captured["album_id"] = album_id
        captured.update(kwargs)
        return {"success": True, "status": "planned", "tracks": []}

    monkeypatch.setattr(
        "core.library2.reorganize_plan.plan_album_reorganize", fake_plan, raising=True)
    resp = client.post(f"/api/library/v2/albums/{ids['album']}/reorganize/preview")
    assert resp.status_code == 200
    assert captured["album_id"] == ids["album"]
    assert set(captured) == {"album_id", "build_final_path_fn",
                             "transfer_dir", "resolve_file_path_fn"}


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
    assert str(ids["album"]) in (queued_ids + ([active_id] if active_id else []))


def test_apply_404_for_discography_only_album(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/albums/{ids['no_legacy_album']}/reorganize", json={})
    assert resp.status_code == 404


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


def test_reorganize_all_404_for_artist_without_owned_albums(api):
    client, _db, ids = api
    resp = client.post(f"/api/library/v2/artists/{ids['no_legacy_artist']}/reorganize-all", json={})
    assert resp.status_code == 404


def test_reorganize_all_404_for_missing_artist(api):
    client, _db, _ids = api
    resp = client.post("/api/library/v2/artists/999999/reorganize-all", json={})
    assert resp.status_code == 404
