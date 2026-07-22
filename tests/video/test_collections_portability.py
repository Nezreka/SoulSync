"""Export/import (community-shareable configs) and custom member ordering."""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.collections.sync import _ordered_server_ids, sync_collection
from database.video_database import VideoDatabase


def _make_client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


# ── export / import ──────────────────────────────────────────────────────────
def test_export_is_portable_and_import_is_idempotent(tmp_path):
    client, db = _make_client(tmp_path)
    db.create_collection_definition(
        "Christmas", kind="list", media_type="movie",
        definition={"source": "tmdb_keyword", "query": "christmas", "limit": 250},
        summary="ho ho ho", window_start="11-20", window_end="01-06",
        collection_mode="hideItems", poster_url="/api/video/collections/1/poster?v=x")

    d = client.get("/api/video/collections/export").get_json()
    assert d["soulsync_collections"] == 1 and len(d["collections"]) == 1
    row = d["collections"][0]
    assert row["name"] == "Christmas" and row["window_start"] == "11-20"
    assert row["collection_mode"] == "hideItems"
    assert "id" not in row and "poster_url" not in row       # nothing machine-local

    # Import into a fresh instance: lands whole; re-import: skipped.
    client2, db2 = _make_client(tmp_path / "b")
    r = client2.post("/api/video/collections/import",
                     json={"collections": d["collections"]}).get_json()
    assert r["ok"] and [i["name"] for i in r["imported"]] == ["Christmas"]
    full = db2.get_collection_definition(r["imported"][0]["id"])
    assert full["definition"]["query"] == "christmas"
    assert full["window_start"] == "11-20" and full["collection_mode"] == "hideItems"
    r = client2.post("/api/video/collections/import",
                     json={"collections": d["collections"]}).get_json()
    assert r["imported"] == [] and r["skipped"] == ["Christmas"]
    assert client2.post("/api/video/collections/import", json={}).status_code == 400


def test_import_sanitizes_junk(tmp_path):
    client, db = _make_client(tmp_path)
    r = client.post("/api/video/collections/import", json={"collections": [
        {"name": "OK", "kind": "evil", "media_type": "cartoon",
         "definition": "not-a-dict", "sync_mode": "hax",
         "window_start": "13-99", "collection_mode": "explode"},
        {"no_name": True}, "not-a-dict",
    ]}).get_json()
    assert r["ok"] and len(r["imported"]) == 1
    full = db.get_collection_definition(r["imported"][0]["id"])
    assert full["kind"] == "smart" and full["media_type"] == "movie"
    assert full["definition"] == {} and full["sync_mode"] == "sync"
    assert full["window_start"] is None and full["collection_mode"] is None


# ── custom member order ──────────────────────────────────────────────────────
def test_ordered_server_ids_maps_and_appends_rest():
    owned = [{"server_id": "s1", "tmdb_id": 11}, {"server_id": "s2", "tmdb_id": 22},
             {"server_id": "s3", "tmdb_id": 33}, {"server_id": "s4", "tmdb_id": None}]
    out = _ordered_server_ids([33, 11, 999], owned)
    assert out == ["s3", "s1", "s2", "s4"]                   # ordered head, rest follow
    assert _ordered_server_ids([], owned) == ["s1", "s2", "s3", "s4"]


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_sync_pushes_custom_order(db):
    conn = db._get_connection()
    for mid, tmdb in ((1, 11), (2, 22)):
        conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                     "VALUES (?,?,?,?,?,1)", (mid, "plex", f"s{mid}", tmdb, f"M{mid}"))
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES ('Action')")
        gid = conn.execute("SELECT id FROM genres WHERE name='Action'").fetchone()[0]
        conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
    conn.commit(); conn.close()
    cid = db.create_collection_definition(
        "Ordered", media_type="movie", sort_order="custom",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}],
                    "order": [22, 11]})

    class _Src:
        server_name = "plex"

        def __init__(self):
            self.reordered = None

        def find_collection(self, kind, name):
            return None

        def create_collection(self, kind, name, ids):
            return {"ok": True, "server_id": "col1"}

        def collection_member_ids(self, cid_):
            return []

        def collection_add(self, cid_, ids):
            return {"ok": True}

        def collection_remove(self, cid_, ids):
            return {"ok": True}

        def set_collection_meta(self, cid_, **kw):
            return {"ok": True}

        def collection_reorder(self, cid_, ordered):
            self.reordered = (str(cid_), list(ordered))
            return {"ok": True}

    src = _Src()
    r = sync_collection(db, db.get_collection_definition(cid), source=src)
    assert r["ok"]
    assert src.reordered == ("col1", ["s2", "s1"])           # tmdb order → server ids


def test_members_endpoint_pre_sorts_by_saved_order(tmp_path):
    client, db = _make_client(tmp_path)
    conn = db._get_connection()
    for mid, tmdb in ((1, 11), (2, 22), (3, 33)):
        conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                     "VALUES (?,?,?,?,?,1)", (mid, "plex", f"s{mid}", tmdb, f"M{mid}"))
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES ('Action')")
        gid = conn.execute("SELECT id FROM genres WHERE name='Action'").fetchone()[0]
        conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
    conn.commit(); conn.close()
    cid = db.create_collection_definition(
        "Ordered", media_type="movie", sort_order="custom",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}],
                    "order": [33, 11]})
    d = client.get(f"/api/video/collections/{cid}/members").get_json()
    assert d["ok"] and [m["tmdb_id"] for m in d["members"]] == [33, 11, 22]
