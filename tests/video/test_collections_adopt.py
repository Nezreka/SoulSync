"""Adopt-from-server: a foreign (Kometa/hand-made) server collection becomes a
SoulSync-managed definition — membership snapshot, ledger binding, append mode,
server art kept. The migration path that beats delete-and-rebuild."""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.collections.server_cleanup import adopt_collections
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    conn = db._get_connection()
    try:
        for mid, tmdb in ((1, 601), (2, 602), (3, None)):    # 3 = unmatched (no tmdb id)
            conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                         "VALUES (?,?,?,?,?,1)", (mid, "plex", f"m{mid}", tmdb, f"M{mid}"))
        conn.execute("INSERT INTO shows (id, server_source, server_id, tmdb_id, title) "
                     "VALUES (10, 'plex', 's10', 701, 'S10')")
        conn.commit()
    finally:
        conn.close()


class _Source:
    server_name = "plex"

    def __init__(self, members):
        self.members = members    # server_id -> member ids (None = gone)

    def collection_member_ids(self, sid):
        return self.members.get(str(sid))

    def delete_collection(self, sid):
        return {"ok": True}


def test_adopt_creates_static_definition_and_binds_ledger(db):
    _seed(db)
    src = _Source({"k1": ["m1", "m2", "m3", "zzz-unknown"]})
    r = adopt_collections(db, [{"server_id": "k1", "name": "IMDb Top 250"}], source=src)
    assert r["ok"] and r["skipped"] == []
    a = r["adopted"][0]
    assert a["name"] == "IMDb Top 250" and a["media_type"] == "movie"
    assert a["members"] == 4 and a["mapped"] == 2            # m3 has no tmdb id, zzz unknown

    full = db.get_collection_definition(a["definition_id"])
    assert full["kind"] == "list" and full["sync_mode"] == "append"   # never strips unmapped
    assert full["definition"] == {"source": "static", "tmdb_ids": [601, 602],
                                  "keep_server_art": True}
    ledger = db.get_collection_sync(a["definition_id"])
    assert ledger["server_id"] == "k1" and ledger["server_source"] == "plex"

    # The static source resolves the snapshot against the library.
    from core.video.collections.resolver import resolve_collection
    res = resolve_collection(db, full)
    assert res.ok and sorted(m["tmdb_id"] for m in res.owned) == [601, 602]
    assert res.missing == []


def test_adopt_majority_kind_and_skip_reasons(db):
    _seed(db)
    src = _Source({"tv1": ["s10"], "gone": None, "empty": ["zzz"]})
    # Pre-manage one to prove the managed skip.
    did = db.create_collection_definition("Mine", media_type="movie")
    db.record_collection_sync(did, server_source="plex", server_id="mine1",
                              members_sig="x", member_count=1)

    r = adopt_collections(db, [
        {"server_id": "tv1", "name": "HBO Stuff"},
        {"server_id": "gone"}, {"server_id": "empty"}, {"server_id": "mine1"},
    ], source=src)
    assert [a["media_type"] for a in r["adopted"]] == ["show"]
    reasons = {s["server_id"]: s["reason"] for s in r["skipped"]}
    assert "no longer exists" in reasons["gone"]
    assert "no members matched" in reasons["empty"]
    assert "already managed" in reasons["mine1"]


def test_adopted_sync_updates_the_same_server_object_and_keeps_art(db):
    _seed(db)
    src = _Source({"k1": ["m1", "m2"]})
    r = adopt_collections(db, [{"server_id": "k1", "name": "Keepers"}], source=src)
    did = r["adopted"][0]["definition_id"]

    class _SyncSource(_Source):
        def __init__(self):
            super().__init__({})
            self.created = []
            self.added = []
            self.meta = None

        def collection_member_ids(self, sid):
            return ["m1"] if str(sid) == "k1" else None    # k1 still exists, missing m2

        def find_collection(self, kind, name):
            raise AssertionError("adopted collections must never re-find by name")

        def create_collection(self, kind, name, ids):
            self.created.append(name)
            return {"ok": True, "server_id": "new"}

        def collection_add(self, cid, ids):
            self.added.append((str(cid), list(ids)))
            return {"ok": True}

        def collection_remove(self, cid, ids):
            raise AssertionError("append mode must never remove")

        def set_collection_meta(self, cid, **kw):
            self.meta = kw
            return {"ok": True}

    from core.video.collections.sync import sync_collection, _default_poster_generator
    sync_src = _SyncSource()
    gen_calls = []
    r = sync_collection(db, db.get_collection_definition(did), source=sync_src,
                        poster_generator=lambda d, o: gen_calls.append(1) or None)
    assert r["ok"] and sync_src.created == []              # bound ledger → adopted, not recreated
    assert sync_src.added == [("k1", ["m2"])]              # only ADDS the mapped-but-absent one
    assert gen_calls == []                                 # keep_server_art → no auto poster
    assert sync_src.meta["poster_url"] is None and sync_src.meta["poster_bytes"] is None


# ── API seam ─────────────────────────────────────────────────────────────────
def test_adopt_api(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(videoapi._video_db)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    monkeypatch.setattr("core.video.collections.sync.get_collection_source",
                        lambda: _Source({"k1": ["m1", "m2"]}))

    r = client.post("/api/video/collections/server/adopt",
                    json={"items": [{"server_id": "k1", "name": "Kometa Faves"}]}).get_json()
    assert r["ok"] and r["adopted"][0]["name"] == "Kometa Faves"
    assert client.post("/api/video/collections/server/adopt", json={}).status_code == 400
