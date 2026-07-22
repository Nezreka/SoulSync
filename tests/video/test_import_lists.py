"""Acquisition lists (Radarr import-list mode) — a collection definition whose
body carries ``acquire_only`` resolves its list and reports the missing set
(run_sync wishlists it) but NEVER creates/pushes a server collection. Flipping
an existing synced collection to acquire-only removes its stale server shelf
once (ledger-verified). Rides the whole collections engine: TMDB charts/lists,
Trakt, MDBList, include/exclude overrides, seasonal windows — zero new
machinery, one flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video.collections.sync import sync_collection, wishlist_missing_movies
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_EDITOR_JS = (_ROOT / "webui" / "static" / "video" / "video-collection-editor.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    conn = d._get_connection()
    conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                 "VALUES (1, 'plex', 'm1', 601, 'Owned', 1)")
    conn.commit(); conn.close()
    return d


class FakeSource:
    server_name = "plex"

    def __init__(self):
        self.created = []
        self.deleted = []
        self.collections = {"col-old": {"members": {"m1"}}}

    def create_collection(self, kind, name, member_ids):
        self.created.append(name)
        return {"ok": True, "server_id": "col-new"}

    def collection_member_ids(self, cid):
        c = self.collections.get(str(cid))
        return None if c is None else sorted(c["members"])

    def delete_collection(self, cid):
        self.deleted.append(str(cid))
        self.collections.pop(str(cid), None)
        return {"ok": True}

    # full surface for the regular (non-acquire) control path
    def find_collection(self, kind, name):
        return None

    def collection_add(self, cid, ids):
        self.collections.setdefault(str(cid), {"members": set()})["members"].update(map(str, ids))
        return {"ok": True}

    def collection_remove(self, cid, ids):
        self.collections.get(str(cid), {"members": set()})["members"].difference_update(map(str, ids))
        return {"ok": True}

    def set_collection_meta(self, cid, **kw):
        return {"ok": True}


def _fetcher(source, ref):
    return [{"tmdb_id": 601, "title": "Owned"},
            {"tmdb_id": 777, "title": "Missing One", "year": 2020}]


def _acq_def(db, **extra):
    cid = db.create_collection_definition(
        "IMDb Top", kind="list", media_type="movie", wishlist_missing=True,
        definition={"source": "tmdb_chart", "chart": "top_movies",
                    "acquire_only": True, **extra})
    return db.get_collection_definition(cid)


def test_acquire_only_never_touches_the_server(db):
    src = FakeSource()
    d = _acq_def(db)
    r = sync_collection(db, d, source=src, list_fetcher=_fetcher)
    assert r["ok"] and r["acquire_only"] is True
    assert src.created == []                      # no shelf created
    assert [m["tmdb_id"] for m in r["missing"]] == [777]
    # and the missing set feeds the wishlist exactly like a synced list
    assert wishlist_missing_movies(db, d, r["missing"]) == 1
    assert set(db.wishlisted_movie_status()) == {777}


def test_flipping_to_acquire_only_removes_the_stale_shelf_once(db):
    src = FakeSource()
    d = _acq_def(db)
    db.record_collection_sync(d["id"], server_source="plex", server_id="col-old",
                              members_sig="x", member_count=1)
    r = sync_collection(db, d, source=src, list_fetcher=_fetcher)
    assert r["ok"] and src.deleted == ["col-old"]
    assert db.get_collection_sync(d["id"]) is None   # ledger row gone too
    # second sync: nothing left to remove
    src.deleted.clear()
    r2 = sync_collection(db, d, source=src, list_fetcher=_fetcher)
    assert r2["ok"] and src.deleted == []


def test_regular_collections_are_untouched(db):
    src = FakeSource()
    cid = db.create_collection_definition(
        "Real Shelf", kind="list", media_type="movie",
        definition={"source": "tmdb_chart", "chart": "top_movies"})
    d = db.get_collection_definition(cid)
    r = sync_collection(db, d, source=src, list_fetcher=_fetcher)
    assert r["ok"] and src.created == ["Real Shelf"]


def test_editor_has_the_acquisition_toggle():
    assert "data-acquire" in _EDITOR_JS
    assert "acquire_only" in _EDITOR_JS
    assert "Acquisition list only" in _EDITOR_JS
    # turning it on force-enables wishlist_missing (an import list must wish)
    wiring = _EDITOR_JS.split("var acq = page.querySelector('[data-acquire]')")[1].split("page.querySelectorAll('[data-f]')")[0]
    assert "wishlist_missing" in wiring
