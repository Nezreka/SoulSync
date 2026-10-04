"""Tests for the manual library match ("I have this") feature.

Covers the DB layer (video_manual_matches CRUD, the lookup fallback order,
the picker search) and the /api/video/manual-match endpoints (including the
admin write gate).
"""

from __future__ import annotations

from flask import Flask


def _db(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    conn = db._get_connection()
    # On-server rows only (search + lookups ignore server-less rows).
    conn.execute("INSERT INTO shows (title, year, server_id) VALUES ('Australian Survivor', 2017, 'srv1')")
    conn.execute("INSERT INTO shows (title, year, server_id, tmdb_id) VALUES ('Survivor US', 2000, 'srv1', 999)")
    conn.execute("INSERT INTO movies (title, year, server_id) VALUES ('Dune', 2021, 'srv1')")
    conn.commit()
    conn.close()
    return db


# ── DB: set / get / clear ──────────────────────────────────────────────────

def test_set_and_get_manual_match(tmp_path):
    db = _db(tmp_path)
    assert db.set_manual_match("show", 12345, 1) is True
    assert db.manual_match_for_tmdb("show", 12345) == 1
    assert db.manual_match_for_library("show", 1) == 12345


def test_set_rejects_bad_input(tmp_path):
    db = _db(tmp_path)
    assert db.set_manual_match("episode", 1, 1) is False      # bad kind
    assert db.set_manual_match("show", "abc", 1) is False     # bad tmdb id
    assert db.set_manual_match("show", 1, 424242) is False    # no such row
    assert db.set_manual_match("movie", 1, 2) is False         # id 2 is a show, not a movie
    assert db.manual_match_for_tmdb("show", 12345) is None


def test_set_is_idempotent_replace(tmp_path):
    db = _db(tmp_path)
    assert db.set_manual_match("show", 12345, 1) is True
    assert db.set_manual_match("show", 12345, 2) is True     # re-point
    assert db.manual_match_for_tmdb("show", 12345) == 2


def test_clear_manual_match(tmp_path):
    db = _db(tmp_path)
    assert db.clear_manual_match("show", 12345) is False     # nothing there
    db.set_manual_match("show", 12345, 1)
    assert db.clear_manual_match("show", 12345) is True
    assert db.manual_match_for_tmdb("show", 12345) is None
    assert db.clear_manual_match("episode", 12345) is False


def test_lookup_falls_back_to_manual_match(tmp_path):
    db = _db(tmp_path)
    # No direct tmdb_id on row 1 -> miss before the link.
    assert db.library_id_for_tmdb("show", 12345) is None
    db.set_manual_match("show", 12345, 1)
    assert db.library_id_for_tmdb("show", 12345) == 1
    assert db.library_ids_for_tmdb("show", [12345, 999]) == {12345: 1, 999: 2}


def test_manual_match_beats_auto_match(tmp_path):
    db = _db(tmp_path)
    # Row 2 carries tmdb_id=999 (auto-matched). The user's word wins.
    assert db.library_id_for_tmdb("show", 999) == 2
    db.set_manual_match("show", 999, 1)
    assert db.library_id_for_tmdb("show", 999) == 1
    assert db.library_ids_for_tmdb("show", [999]) == {999: 1}


def test_lookup_ignores_dead_links(tmp_path):
    db = _db(tmp_path)
    db.set_manual_match("show", 12345, 1)
    conn = db._get_connection()
    conn.execute("DELETE FROM shows WHERE id=1")
    conn.commit()
    conn.close()
    # Linked row is gone -> no phantom ownership.
    assert db.library_id_for_tmdb("show", 12345) is None
    assert db.library_ids_for_tmdb("show", [12345]) == {}


def test_search_library_titles_includes_unmatched(tmp_path):
    db = _db(tmp_path)
    rows = db.search_library_titles("show", "australian")
    assert [(r["id"], r["tmdb_id"]) for r in rows] == [(1, None)]
    rows = db.search_library_titles("show", "survivor")
    assert {r["id"] for r in rows} == {1, 2}
    assert db.search_library_titles("movie", "survivor") == []
    assert db.search_library_titles("show", "  ") == []
    assert db.search_library_titles("episode", "survivor") == []


# ── API ────────────────────────────────────────────────────────────────────

def _client(tmp_path, *, is_admin=True):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    conn = videoapi._video_db._get_connection()
    conn.execute("INSERT INTO shows (title, year, server_id) VALUES ('Australian Survivor', 2017, 'srv1')")
    conn.commit()
    conn.close()
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_api_search(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/video/manual-match/search?kind=show&q=australian")
    assert r.status_code == 200
    body = r.get_json()
    assert [x["title"] for x in body["results"]] == ["Australian Survivor"]
    r = c.get("/api/video/manual-match/search?kind=bogus&q=x")
    assert r.status_code == 400
    r = c.get("/api/video/manual-match/search?kind=show&q=")
    assert r.get_json()["results"] == []


def test_api_link_unlink_roundtrip(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/video/manual-match",
               json={"kind": "show", "tmdb_id": 12345, "library_id": 1})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["success"] is True

    r = c.get("/api/video/manual-match/status?kind=show&tmdb_id=12345")
    assert r.get_json()["library_id"] == 1
    r = c.get("/api/video/manual-match/status?kind=show&library_id=1")
    assert r.get_json()["tmdb_id"] == 12345

    r = c.delete("/api/video/manual-match",
                 json={"kind": "show", "tmdb_id": 12345})
    assert r.get_json()["success"] is True
    r = c.get("/api/video/manual-match/status?kind=show&tmdb_id=12345")
    assert r.get_json()["library_id"] is None


def test_api_link_rejects_missing_row(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/video/manual-match",
               json={"kind": "show", "tmdb_id": 1, "library_id": 424242})
    assert r.status_code == 404
    r = c.delete("/api/video/manual-match",
                 json={"kind": "show", "tmdb_id": 424242})
    assert r.status_code == 404


def test_api_writes_are_admin_only(tmp_path):
    c = _client(tmp_path, is_admin=False)
    # Reads stay open for non-admins.
    r = c.get("/api/video/manual-match/search?kind=show&q=australian")
    assert r.status_code == 200
    # Writes are gated.
    r = c.post("/api/video/manual-match",
               json={"kind": "show", "tmdb_id": 1, "library_id": 1})
    assert r.status_code == 403
    r = c.delete("/api/video/manual-match",
                 json={"kind": "show", "tmdb_id": 1})
    assert r.status_code == 403
