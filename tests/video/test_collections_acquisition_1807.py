"""#1807 (hilburn): soulsync wishlisted ~90 movies overnight, mostly marvel.

the path: the preset picker started the "wishlist the movies i'm missing" box
TICKED for franchises + universes (and pre-ticked a universe whose count failed,
like the MCU on a library with no marvel). one click on Create and the nightly
collection sync fed every missing member to the wishlist. removing them didn't
help either, the next sync just fed them back.

these pin each piece: opt-in everywhere, fed-once ledger, imports land off, plus
the other collection bugs found in the same pass (empty fetch wiping a server
collection, duplicate losing its window/mode/art).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from core.video.collections.presets import apply_pack, expand_pack
from core.video.collections.sync import (sync_collection, wishlist_missing_members,
                                         wishlist_missing_movies)
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parents[2]
_EDITOR_JS = (_ROOT / "webui" / "static" / "video" / "video-collection-editor.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    conn = d._get_connection()
    conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                 "VALUES (1, 'plex', 'm1', 601, 'Owned', 1)")
    conn.commit()
    conn.close()
    return d


_MISSING = [{"tmdb_id": 777, "title": "Missing One", "year": 2020},
            {"tmdb_id": 888, "title": "Missing Two", "year": 2021}]


def _chart(db, wishlist=True, name="Top 250"):
    cid = db.create_collection_definition(
        name, kind="list", media_type="movie", wishlist_missing=wishlist,
        definition={"source": "tmdb_chart", "chart": "top_movies", "limit": 250})
    return db.get_collection_definition(cid)


# ── fed-once ledger ──────────────────────────────────────────────────────────

def test_removed_wishlist_row_does_not_come_back_next_sync(db):
    c = _chart(db)
    assert wishlist_missing_movies(db, c, _MISSING) == 2
    db.remove_from_wishlist("movie", tmdb_id=777)
    # next nightly sync, same missing set. 777 was fed already, it stays gone.
    assert wishlist_missing_movies(db, c, _MISSING) == 0
    assert set(db.wishlisted_movie_status()) == {888}


def test_new_list_members_still_get_fed(db):
    c = _chart(db)
    wishlist_missing_movies(db, c, _MISSING)
    grown = _MISSING + [{"tmdb_id": 999, "title": "New On The Chart"}]
    assert wishlist_missing_movies(db, c, grown) == 1
    assert 999 in db.wishlisted_movie_status()


def test_count_skips_rows_already_on_the_wishlist(db):
    db.add_movie_to_wishlist(777, "Missing One")
    c = _chart(db)
    # only 888 is a NEW wishlist row. the old count said 2 every single night.
    assert wishlist_missing_movies(db, c, _MISSING) == 1


def test_ledger_is_per_collection(db):
    a, b = _chart(db, name="A"), _chart(db, name="B")
    wishlist_missing_movies(db, a, _MISSING)
    db.remove_from_wishlist("movie", tmdb_id=777)
    # b never fed 777, so b feeding it is a real add, not a boomerang of a's
    assert wishlist_missing_movies(db, b, _MISSING) == 1


def test_refeed_ignores_the_ledger(db):
    c = _chart(db)
    wishlist_missing_movies(db, c, _MISSING)
    db.remove_from_wishlist("movie", tmdb_id=777)
    assert wishlist_missing_members(db, c, _MISSING, refeed=True) == 1
    assert 777 in db.wishlisted_movie_status()


def test_toggle_off_feeds_nothing(db):
    c = _chart(db, wishlist=False)
    assert wishlist_missing_movies(db, c, _MISSING) == 0
    assert db.wishlisted_movie_status() == {}


# ── api: manual button refeeds, delete clears the ledger, import lands off ─

@pytest.fixture()
def client_db(db, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = db

    def fake_fetcher(dbb):
        return lambda source, ref: [{"tmdb_id": 601, "title": "Owned"}] + _MISSING
    monkeypatch.setattr("core.video.collections.list_sources.build_list_fetcher", fake_fetcher)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), db


def test_manual_wishlist_button_brings_back_a_removed_title(client_db):
    client, db = client_db
    c = _chart(db)
    wishlist_missing_movies(db, c, _MISSING)
    db.remove_from_wishlist("movie", tmdb_id=777)
    d = client.post(f"/api/video/collections/{c['id']}/wishlist_missing", json={}).get_json()
    assert d["ok"] and d["added"] == 1
    assert 777 in db.wishlisted_movie_status()


def test_delete_collection_clears_its_ledger(client_db):
    client, db = client_db
    c = _chart(db)
    wishlist_missing_movies(db, c, _MISSING)
    assert db.get_setting("collection_fed_%d" % c["id"]) not in (None, "", "[]")
    assert client.delete(f"/api/video/collections/{c['id']}").get_json()["ok"]
    assert db.get_setting("collection_fed_%d" % c["id"]) == "[]"


def test_import_never_turns_wishlisting_on(client_db):
    client, db = client_db
    r = client.post("/api/video/collections/import", json={"collections": [
        {"name": "Someone's MCU", "kind": "list", "media_type": "movie",
         "definition": {"source": "tmdb_union", "keywords": ["marvel cinematic universe"]},
         "wishlist_missing": True}]}).get_json()
    assert r["ok"] and len(r["imported"]) == 1
    assert not db.get_collection_definition(r["imported"][0]["id"])["wishlist_missing"]


def test_preset_apply_api_defaults_to_no_wishlist(client_db):
    client, db = client_db
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_collection_id=131292, tmdb_collection_name='Iron Man Collection' "
                 "WHERE id=1")
    conn.commit()
    conn.close()
    r = client.post("/api/video/collections/presets/apply", json={
        "media_type": "movie", "pack": "franchises", "keys": ["franchise:131292"]}).get_json()
    assert r["ok"] and r["created"]
    assert not db.get_collection_definition(r["created"][0]["id"])["wishlist_missing"]


# ── presets: opt-in, no blind pre-ticks ──────────────────────────────────────

def test_apply_pack_defaults_to_no_wishlist(db):
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_collection_id=131292, tmdb_collection_name='Iron Man Collection' "
                 "WHERE id=1")
    conn.commit()
    conn.close()
    r = apply_pack(db, "franchises", "movie", ["franchise:131292"])
    assert not db.get_collection_definition(r["created"][0]["id"])["wishlist_missing"]


def test_uncounted_universe_is_not_pre_ticked(db):
    def boom(source, ref):
        raise RuntimeError("tmdb down")
    entries = expand_pack(db, "universes", "movie", fetcher=boom)
    assert entries and all(e["count"] is None for e in entries)
    assert not any(e["suggested"] for e in entries)


def test_picker_wishlist_box_starts_unticked():
    picker = _EDITOR_JS.split("function showPicker(pack)")[1]
    foot = picker.split("var foot = h('div', 'vce-picker-foot');")[1].split("foot.appendChild(h('div', 'vce-foot-spacer'))")[0]
    box = foot.split("foot.innerHTML =")[1].split(";")[0]
    assert "checked" not in box
    assert "wlDefault" not in _EDITOR_JS
    assert "data-wl-note" in foot          # the cost note is there


# ── resolver: an empty fetch must not empty the server collection ────────────

class _Src:
    server_name = "plex"

    def __init__(self):
        self.members = {"col-1": {"m1"}}
        self.removed = []

    def collection_member_ids(self, cid):
        return sorted(self.members.get(cid, set())) if cid in self.members else None

    def find_collection(self, kind, name):
        return None

    def create_collection(self, kind, name, ids):
        self.members["col-new"] = set(ids)
        return {"ok": True, "server_id": "col-new"}

    def collection_add(self, cid, ids):
        self.members[cid].update(ids)
        return {"ok": True}

    def collection_remove(self, cid, ids):
        self.removed.extend(ids)
        self.members[cid].difference_update(ids)
        return {"ok": True}

    def set_collection_meta(self, cid, **kw):
        return {"ok": True}


def test_empty_list_fetch_leaves_the_server_collection_alone(db):
    c = _chart(db, wishlist=False)
    db.record_collection_sync(c["id"], server_source="plex", server_id="col-1",
                              members_sig="old", member_count=1)
    src = _Src()
    r = sync_collection(db, c, source=src, list_fetcher=lambda s, ref: [])
    assert not r["ok"] and "no titles" in r["error"]
    assert src.removed == [] and src.members["col-1"] == {"m1"}


def test_real_list_still_syncs(db):
    c = _chart(db, wishlist=False)
    db.record_collection_sync(c["id"], server_source="plex", server_id="col-1",
                              members_sig="old", member_count=1)
    src = _Src()
    r = sync_collection(db, c, source=src,
                        list_fetcher=lambda s, ref: [{"tmdb_id": 601, "title": "Owned"}] + _MISSING)
    assert r["ok"] and src.members["col-1"] == {"m1"}
    assert [m["tmdb_id"] for m in r["missing"]] == [777, 888]


# ── duplicate keeps the window/mode and gets its own art ─────────────────────

def test_duplicate_keeps_window_and_mode_and_drops_borrowed_art(db):
    cid = db.create_collection_definition(
        "Christmas", kind="list", media_type="movie",
        definition={"source": "tmdb_keyword", "query": "christmas"},
        poster_url="/api/video/collections/1/poster?v=abcd1234",
        window_start="11-20", window_end="01-06", collection_mode="hide")
    copy = db.get_collection_definition(db.duplicate_collection_definition(cid))
    assert (copy["window_start"], copy["window_end"]) == ("11-20", "01-06")
    assert copy["collection_mode"] == "hide"
    assert not copy["poster_url"]


def test_duplicate_keeps_a_hand_set_poster(db):
    cid = db.create_collection_definition("Mine", poster_url="https://img/x.jpg")
    copy = db.get_collection_definition(db.duplicate_collection_definition(cid))
    assert copy["poster_url"] == "https://img/x.jpg"
