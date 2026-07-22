"""Sync-engine tests: diffing, sync_mode, ledger skip, adopt-by-name, and
resilience — all against an in-memory fake server so the orchestration is fully
exercised without a real Plex/Jellyfin."""

from __future__ import annotations

import pytest

from core.video.collections.sync import sync_all_collections, sync_collection
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class FakeSource:
    """In-memory stand-in for a media server's collection surface."""
    def __init__(self, server_name="plex"):
        self.server_name = server_name
        self.collections = {}   # id -> {name, kind, members:set, meta:dict}
        self._n = 0

    def find_collection(self, kind, name):
        for cid, c in self.collections.items():
            if c["name"] == name and c["kind"] == kind:
                return cid
        return None

    def create_collection(self, kind, name, member_ids):
        self._n += 1
        cid = f"col-{self._n}"
        self.collections[cid] = {"name": name, "kind": kind,
                                 "members": {str(i) for i in member_ids}, "meta": {}}
        return {"ok": True, "server_id": cid}

    def collection_member_ids(self, collection_id):
        c = self.collections.get(str(collection_id))
        return None if c is None else sorted(c["members"])

    def collection_add(self, collection_id, ids):
        self.collections[str(collection_id)]["members"].update(str(i) for i in ids)
        return {"ok": True}

    def collection_remove(self, collection_id, ids):
        self.collections[str(collection_id)]["members"].difference_update(str(i) for i in ids)
        return {"ok": True}

    def set_collection_meta(self, collection_id, **kw):
        self.collections[str(collection_id)]["meta"] = kw
        return {"ok": True}


def _add_movie(db, mid, genre="Action", server_id=None):
    sid = server_id or f"srv{mid}"
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, title, has_file) VALUES (?,?,?,?,1)",
            (mid, "plex", sid, f"Movie {mid}"))
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (genre,))
        gid = conn.execute("SELECT id FROM genres WHERE name=?", (genre,)).fetchone()[0]
        conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        conn.commit()
    finally:
        conn.close()


def _del_movie(db, mid):
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM movies WHERE id=?", (mid,))
        conn.commit()
    finally:
        conn.close()


def _make_def(db, *, sync_mode="sync", name="Action"):
    cid = db.create_collection_definition(
        name, kind="smart", media_type="movie", sync_mode=sync_mode,
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    return db.get_collection_definition(cid)


def test_first_sync_creates_with_all_members(db):
    _add_movie(db, 1); _add_movie(db, 2)
    src = FakeSource()
    d = _make_def(db)
    r = sync_collection(db, d, source=src)
    assert r["ok"] and r["added"] == 2 and r["removed"] == 0
    assert src.collections[r["server_id"]]["members"] == {"srv1", "srv2"}
    # ledger recorded
    led = db.get_collection_sync(d["id"])
    assert led["server_id"] == r["server_id"] and led["member_count"] == 2


def test_second_sync_unchanged_is_skipped(db):
    _add_movie(db, 1)
    src = FakeSource()
    d = _make_def(db)
    sync_collection(db, d, source=src)
    r2 = sync_collection(db, d, source=src)
    assert r2["skipped"] == "unchanged"


def test_new_member_is_added_on_resync(db):
    _add_movie(db, 1)
    src = FakeSource()
    d = _make_def(db)
    r1 = sync_collection(db, d, source=src)
    _add_movie(db, 2)   # now matches too
    r2 = sync_collection(db, d, source=src)
    assert r2["added"] == 1
    assert src.collections[r1["server_id"]]["members"] == {"srv1", "srv2"}


def test_sync_mode_removes_stale_member(db):
    _add_movie(db, 1); _add_movie(db, 2)
    src = FakeSource()
    d = _make_def(db, sync_mode="sync")
    r1 = sync_collection(db, d, source=src)
    _del_movie(db, 2)   # srv2 no longer owned/matching
    r2 = sync_collection(db, d, source=src)
    assert r2["removed"] == 1
    assert src.collections[r1["server_id"]]["members"] == {"srv1"}


def test_append_mode_keeps_stale_member(db):
    _add_movie(db, 1); _add_movie(db, 2)
    src = FakeSource()
    d = _make_def(db, sync_mode="append")
    r1 = sync_collection(db, d, source=src)
    _del_movie(db, 2)
    r2 = sync_collection(db, d, source=src)
    assert r2["removed"] == 0
    assert src.collections[r1["server_id"]]["members"] == {"srv1", "srv2"}   # kept


def test_adopts_existing_same_name_collection(db):
    _add_movie(db, 1)
    src = FakeSource()
    src.create_collection("movie", "Action", ["already-there"])   # user's manual collection
    d = _make_def(db, name="Action")
    r = sync_collection(db, d, source=src)
    # adopted col-1, added srv1; 'already-there' removed (sync mode) since it's not owned-matching
    assert r["server_id"] == "col-1"
    assert src.collections["col-1"]["members"] == {"srv1"}


def test_resolve_error_returns_error_no_server_change(db):
    src = FakeSource()
    cid = db.create_collection_definition("Bad", definition={"rules": []})  # empty -> error
    d = db.get_collection_definition(cid)
    r = sync_collection(db, d, source=src)
    assert r["ok"] is False and "no rules" in r["error"]
    assert src.collections == {}


def test_deleted_server_collection_is_recreated(db):
    _add_movie(db, 1)
    src = FakeSource()
    d = _make_def(db)
    r1 = sync_collection(db, d, source=src)
    del src.collections[r1["server_id"]]     # user deleted it on the server
    r2 = sync_collection(db, d, source=src)
    assert r2["ok"] and r2["server_id"] in src.collections


def test_empty_definition_creates_nothing(db):
    src = FakeSource()
    d = _make_def(db)   # no matching movies exist
    r = sync_collection(db, d, source=src)
    assert r.get("empty") is True and r["server_id"] is None
    assert src.collections == {}


def test_meta_is_pushed(db):
    _add_movie(db, 1)
    src = FakeSource()
    cid = db.create_collection_definition(
        "Pinned", media_type="movie", poster_url="http://x/p.jpg", summary="hi",
        sort_order="alpha", pinned=True,
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    d = db.get_collection_definition(cid)
    r = sync_collection(db, d, source=src)
    meta = src.collections[r["server_id"]]["meta"]
    assert meta["poster_url"] == "http://x/p.jpg" and meta["summary"] == "hi"
    assert meta["sort"] == "alpha" and meta["pinned"] is True


def test_sync_all_aggregates_and_isolates_failures(db):
    _add_movie(db, 1)
    src = FakeSource()
    _make_def(db, name="Good")
    db.create_collection_definition("Bad", definition={"rules": []})   # will error
    out = sync_all_collections(db, source=src)
    assert out["total"] == 2 and out["synced"] == 1 and out["failed"] == 1


# ── ranked list order (IMDb Top 250 rank, not release date) ──────────────────

class ReorderSource(FakeSource):
    """FakeSource that also records collection_reorder calls (Plex-only capability)."""
    def __init__(self, **k):
        super().__init__(**k)
        self.reorder_calls = []

    def collection_reorder(self, collection_id, ordered):
        self.reorder_calls.append((str(collection_id), list(ordered)))
        return {"ok": True}


def _add_movie_tmdb(db, mid, tmdb_id, sid):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, year, has_file) "
                     "VALUES (?,?,?,?,?,?,1)", (mid, "plex", sid, tmdb_id, f"M{mid}", 2000))
        conn.commit()
    finally:
        conn.close()


def _imdb_top_def(db, name="IMDb Top 250", sort_order="release"):
    # "release" is the DB default → the real-world case a chart collection lands in.
    cid = db.create_collection_definition(name, kind="list", media_type="movie",
                                          sort_order=sort_order,
                                          definition={"source": "imdb_chart", "chart": "top"})
    return db.get_collection_definition(cid)


def test_ranked_list_pushes_in_rank_order_with_custom_sort(db):
    # Inserted deliberately out of rank order; the chart ranks them A, B, C.
    _add_movie_tmdb(db, 1, 30, "srvC")   # rank #3
    _add_movie_tmdb(db, 2, 10, "srvA")   # rank #1
    _add_movie_tmdb(db, 3, 20, "srvB")   # rank #2

    def fetcher(source, ref):
        assert source == "imdb_chart"
        return [{"tmdb_id": 10}, {"tmdb_id": 20}, {"tmdb_id": 30}]   # rank order

    src = ReorderSource()
    r = sync_collection(db, _imdb_top_def(db), source=src, list_fetcher=fetcher)
    assert r["ok"]
    cid = r["server_id"]
    assert src.reorder_calls == [(cid, ["srvA", "srvB", "srvC"])]        # rank, not srvC/srvB/srvA
    assert src.collections[cid]["meta"]["sort"] == "custom"             # Plex told to keep custom order


def test_explicit_sort_on_ranked_list_is_respected(db):
    _add_movie_tmdb(db, 1, 10, "srvA")

    def fetcher(source, ref):
        return [{"tmdb_id": 10}]

    src = ReorderSource()
    sync_collection(db, _imdb_top_def(db, name="IMDb A-Z", sort_order="alpha"),
                    source=src, list_fetcher=fetcher)
    assert src.reorder_calls == []                                      # user picked alpha → no rank reorder
    cid = next(iter(src.collections))
    assert src.collections[cid]["meta"]["sort"] == "alpha"


def test_ranked_order_change_re_syncs(db):
    from core.video.collections.sync import members_signature
    defn = _imdb_top_def(db)
    sig_ab = members_signature(defn, ["srvA", "srvB"])
    sig_ba = members_signature(defn, ["srvB", "srvA"])
    assert sig_ab != sig_ba                                             # a re-rank changes the signature
