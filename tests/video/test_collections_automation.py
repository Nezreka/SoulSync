"""Phase 4: list fetcher, wishlist tie-in, and the daily sync automation handler."""

from __future__ import annotations

import pytest

from core.automation.handlers.video_sync_collections import auto_video_sync_collections
from core.video.collections.list_sources import build_list_fetcher
from core.video.collections.sync import sync_all_collections, wishlist_missing_movies
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class FakeSource:
    def __init__(self):
        self.server_name = "plex"
        self.collections = {}
        self._n = 0

    def find_collection(self, kind, name):
        for cid, c in self.collections.items():
            if c["name"] == name:
                return cid
        return None

    def create_collection(self, kind, name, member_ids):
        self._n += 1
        cid = "col-%d" % self._n
        self.collections[cid] = {"name": name, "members": {str(i) for i in member_ids}}
        return {"ok": True, "server_id": cid}

    def collection_member_ids(self, cid):
        c = self.collections.get(str(cid))
        return None if c is None else sorted(c["members"])

    def collection_add(self, cid, ids):
        self.collections[str(cid)]["members"].update(str(i) for i in ids); return {"ok": True}

    def collection_remove(self, cid, ids):
        self.collections[str(cid)]["members"].difference_update(str(i) for i in ids); return {"ok": True}

    def set_collection_meta(self, cid, **kw):
        return {"ok": True}


def _add_movie(db, mid, tmdb_id, franchise=None):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, "
                     "tmdb_collection_id, has_file) VALUES (?,?,?,?,?,?,1)",
                     (mid, "plex", "srv%d" % mid, tmdb_id, "Movie %d" % mid, franchise))
        conn.commit()
    finally:
        conn.close()


def _wishlist_titles(db):
    conn = db._get_connection()
    try:
        return {r[0] for r in conn.execute("SELECT title FROM video_wishlist WHERE kind='movie'").fetchall()}
    finally:
        conn.close()


# ── list fetcher ────────────────────────────────────────────────────────────
def test_franchise_fetcher_normalizes_items():
    class FakeEng:
        def collection(self, cid):
            assert cid == 10
            return [{"id": 1, "title": "A", "release_date": "1999-03-31", "poster_path": "/a.jpg"},
                    {"tmdb_id": 2, "title": "B", "year": 2003}]
    f = build_list_fetcher(engine_factory=lambda: FakeEng())
    out = f("tmdb_collection", 10)
    assert out[0] == {"tmdb_id": 1, "title": "A", "year": 1999, "poster_url": "/a.jpg"}
    assert out[1]["tmdb_id"] == 2 and out[1]["year"] == 2003


def test_deferred_sources_return_empty():
    f = build_list_fetcher(engine_factory=lambda: object())
    assert f("trakt_list", "x") == []
    assert f("tmdb_list", "x") == []


# ── wishlist tie-in ─────────────────────────────────────────────────────────
def test_wishlist_missing_only_for_list_movie_with_flag(db):
    missing = [{"tmdb_id": 604, "title": "Reloaded", "year": 2003}]
    # flag off
    assert wishlist_missing_movies(db, {"kind": "list", "media_type": "movie", "wishlist_missing": False}, missing) == 0
    # smart never has missing to wishlist
    assert wishlist_missing_movies(db, {"kind": "smart", "media_type": "movie", "wishlist_missing": True}, missing) == 0
    # list + movie + flag on
    assert wishlist_missing_movies(db, {"kind": "list", "media_type": "movie", "wishlist_missing": True}, missing) == 1
    assert "Reloaded" in _wishlist_titles(db)


def test_sync_all_wishlists_missing_franchise_members(db):
    _add_movie(db, 1, tmdb_id=603, franchise=2344)   # own The Matrix
    src = FakeSource()
    db.create_collection_definition(
        "Matrix", kind="list", media_type="movie", wishlist_missing=True,
        definition={"source": "tmdb_collection", "collection_id": 2344})

    def fetcher(source, ref):
        return [{"tmdb_id": 603, "title": "Matrix"},
                {"tmdb_id": 604, "title": "Reloaded", "year": 2003},
                {"tmdb_id": 605, "title": "Revolutions"}]

    out = sync_all_collections(db, source=src, list_fetcher=fetcher)
    assert out["wishlisted"] == 2
    assert {"Reloaded", "Revolutions"} <= _wishlist_titles(db)
    # owned member still synced to the server collection
    assert any("srv1" in c["members"] for c in src.collections.values())


# ── automation handler ──────────────────────────────────────────────────────
class _Deps:
    def __init__(self):
        self.calls = []

    def update_progress(self, aid, **kw):
        self.calls.append(kw)


def test_handler_reports_completed():
    deps = _Deps()
    seen = {}

    def fake_run(db, on_progress):
        on_progress(1, 1, "X")
        return {"ok": True, "synced": 2, "failed": 0, "added": 5, "removed": 1, "wishlisted": 3}

    class DB:
        def list_collection_definitions(self):
            return [{"id": 1, "enabled": True}]

    r = auto_video_sync_collections({"_automation_id": "a"}, deps, db=DB(), run=fake_run)
    assert r["status"] == "completed" and r["synced"] == 2 and r["wishlisted"] == 3
    assert any(c.get("status") == "finished" for c in deps.calls)


def test_handler_noop_when_none_enabled():
    deps = _Deps()

    class DB:
        def list_collection_definitions(self):
            return [{"id": 1, "enabled": False}]

    r = auto_video_sync_collections({"_automation_id": "a"}, deps, db=DB(),
                                    run=lambda *a, **k: {"ok": True})
    assert r["status"] == "completed" and r["synced"] == 0


def test_handler_surfaces_run_error():
    deps = _Deps()

    class DB:
        def list_collection_definitions(self):
            return [{"id": 1, "enabled": True}]

    r = auto_video_sync_collections({"_automation_id": "a"}, deps, db=DB(),
                                    run=lambda *a, **k: {"ok": False, "error": "no server"})
    assert r["status"] == "error" and "no server" in r["error"]
    assert any(c.get("status") == "error" for c in deps.calls)
