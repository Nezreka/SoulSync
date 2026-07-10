"""Bulk metadata ops: one action looped through the SAME edit-and-lock engine
as the Manage sidebar (never a second write path), plus the inline
add-to-collection include-override merge."""

from __future__ import annotations

import time

import pytest
from flask import Flask

from core.video import bulk_ops
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db, n=3):
    ids = []
    for i in range(1, n + 1):
        ids.append(db.upsert_movie("plex", {
            "server_id": "m%d" % i, "title": "Movie %d" % i, "year": 2000 + i,
            "tmdb_id": 100 + i, "genres": ["Action"], "file": {"path": "/m%d.mkv" % i}}))
    return ids


def _movie(db, mid):
    conn = db._get_connection()
    try:
        row = dict(conn.execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone())
        row["genres"] = [r["name"] for r in conn.execute(
            "SELECT g.name FROM movie_genres mg JOIN genres g ON g.id=mg.genre_id "
            "WHERE mg.movie_id=? ORDER BY g.name", (mid,)).fetchall()]
        return row
    finally:
        conn.close()


def _wait_idle(timeout=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not bulk_ops.bulk_status()["running"]:
            return bulk_ops.bulk_status()
        time.sleep(0.05)
    raise AssertionError("bulk job did not finish")


class FakeSource:
    server_name = "plex"

    def __init__(self):
        self.edits = []

    def edit_item_metadata(self, server_id, changes, kind="movie", unlock_fields=None):
        self.edits.append((server_id, dict(changes)))
        return {"ok": True}

    def set_watched(self, server_id, watched, kind="movie"):
        return {"ok": True}


# ── validate ─────────────────────────────────────────────────────────────────
def test_validate():
    v = bulk_ops.validate
    assert v("movie", [1], "content_rating", {"value": "PG"}) is None
    assert v("album", [1], "content_rating", {"value": "PG"})       # bad kind
    assert v("movie", [], "content_rating", {"value": "PG"})        # empty ids
    assert v("movie", ["x"], "content_rating", {"value": "PG"})     # non-int ids
    assert v("movie", [1], "explode", {})                           # unknown action
    assert v("movie", [1], "content_rating", {})                    # missing value
    assert v("movie", [1], "genre_add", {})                         # missing genre
    assert v("movie", [1], "watched", {"value": "yes"})             # non-bool


# ── per-item application ─────────────────────────────────────────────────────
def test_apply_content_rating_locks_and_pushes(db):
    ids = _seed(db, 1)
    src = FakeSource()
    res = bulk_ops._apply_one(db, "movie", ids[0], "content_rating", {"value": "PG-13"}, src)
    assert res["ok"] and res["pushed"]
    m = _movie(db, ids[0])
    assert m["content_rating"] == "PG-13" and '"content_rating"' in m["locked_fields"]


def test_apply_genre_add_and_remove(db):
    ids = _seed(db, 1)
    src = FakeSource()
    assert bulk_ops._apply_one(db, "movie", ids[0], "genre_add", {"genre": "Comfort"}, src)["ok"]
    assert _movie(db, ids[0])["genres"] == ["Action", "Comfort"]
    # Adding a genre the item already has (case-insensitive) is a clean no-op —
    # no edit, no lock churn.
    before = _movie(db, ids[0])["locked_fields"]
    r = bulk_ops._apply_one(db, "movie", ids[0], "genre_add", {"genre": "comfort"}, src)
    assert r["ok"] and not r.get("pushed") and _movie(db, ids[0])["locked_fields"] == before
    assert bulk_ops._apply_one(db, "movie", ids[0], "genre_remove", {"genre": "action"}, src)["ok"]
    assert _movie(db, ids[0])["genres"] == ["Comfort"]
    r = bulk_ops._apply_one(db, "movie", ids[0], "genre_remove", {"genre": "Nope"}, src)
    assert r["ok"] and not r.get("pushed")


def test_apply_monitored_and_watched(db):
    ids = _seed(db, 1)
    src = FakeSource()
    assert bulk_ops._apply_one(db, "movie", ids[0], "monitored", {"value": False}, src)["ok"]
    assert _movie(db, ids[0])["monitored"] == 0
    assert bulk_ops._apply_one(db, "movie", ids[0], "watched", {"value": True}, src)["ok"]
    assert _movie(db, ids[0])["play_count"] == 1


# ── the job ──────────────────────────────────────────────────────────────────
def test_start_bulk_runs_all_and_reports(db, monkeypatch):
    ids = _seed(db, 3)
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda: None)
    res = bulk_ops.start_bulk(db, "movie", ids + [999999], "content_rating", {"value": "R"})
    assert res["ok"] and res["total"] == 4 and "R" in res["label"]
    final = _wait_idle()
    assert final["done"] == 4 and final["ok"] == 3 and final["failed"] == 1
    assert final["phase"] == "done"
    for mid in ids:
        assert _movie(db, mid)["content_rating"] == "R"


def test_start_bulk_rejects_invalid_and_busy(db):
    assert not bulk_ops.start_bulk(db, "movie", [], "content_rating", {"value": "R"})["ok"]
    assert not bulk_ops.start_bulk(db, "movie", [1], "explode", {})["ok"]


# ── add-to-collection (include-override merge) ───────────────────────────────
def test_add_to_collection_merges_include(db):
    ids = _seed(db, 3)
    cid = db.create_collection_definition(
        "Favorites", kind="smart", media_type="movie",
        definition={"rules": [], "include": [101]})
    res = bulk_ops.add_to_collection(db, "movie", ids, cid)
    assert res["ok"] and res["added"] == 2 and res["skipped"] == 1   # 101 already pinned
    body = db.get_collection_definition(cid)["definition"]
    assert body["include"] == [101, 102, 103]
    # Idempotent: run again — nothing new.
    assert bulk_ops.add_to_collection(db, "movie", ids, cid)["added"] == 0


def test_add_to_collection_guards(db):
    ids = _seed(db, 1)
    assert not bulk_ops.add_to_collection(db, "movie", ids, 999999)["ok"]
    cid = db.create_collection_definition("Shows", kind="smart", media_type="show",
                                          definition={"rules": []})
    assert not bulk_ops.add_to_collection(db, "movie", ids, cid)["ok"]   # kind mismatch
    orphan = db.upsert_movie("plex", {"server_id": "nm", "title": "No Match"})
    cid2 = db.create_collection_definition("Fav", kind="smart", media_type="movie",
                                           definition={"rules": []})
    assert not bulk_ops.add_to_collection(db, "movie", [orphan], cid2)["ok"]  # no tmdb ids


# ── API seam ─────────────────────────────────────────────────────────────────
def test_bulk_api_routes(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db = videoapi._video_db
    ids = _seed(db, 2)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        assert client.post("/api/video/bulk/start", json={}).status_code == 400
        assert client.post("/api/video/bulk/start",
                           json={"kind": "movie", "ids": ids, "action": "explode"}).status_code == 400
        monkeypatch.setattr("core.video.sources.get_active_video_source", lambda: None)
        r = client.post("/api/video/bulk/start",
                        json={"kind": "movie", "ids": ids, "action": "monitored",
                              "params": {"value": False}})
        assert r.status_code == 200 and r.get_json()["total"] == 2
        final = _wait_idle()
        assert final["ok"] == 2
        assert client.get("/api/video/bulk/status").get_json()["running"] is False
        # collection_add runs inline (no job).
        cid = db.create_collection_definition("Fav", kind="smart", media_type="movie",
                                              definition={"rules": []})
        r = client.post("/api/video/bulk/start",
                        json={"kind": "movie", "ids": ids, "action": "collection_add",
                              "params": {"collection_id": cid}})
        assert r.status_code == 200 and r.get_json()["added"] == 2
    finally:
        videoapi._video_db = None
