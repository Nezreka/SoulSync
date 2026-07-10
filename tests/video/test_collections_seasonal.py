"""Seasonal windows: in-window a collection syncs like any other; out-of-window
the sync REMOVES our server object (ledger-verified, never foreign) so seasonal
shelves appear for the holiday and disappear after it."""

from __future__ import annotations

import datetime

import pytest

from core.video.collections.sync import in_season, sync_collection
from database.video_database import VideoDatabase


def _d(md):
    m, day = md.split("-")
    return datetime.date(2026, int(m), int(day))


# ── in_season (pure) ─────────────────────────────────────────────────────────
def test_in_season_plain_range_inclusive():
    w = {"window_start": "10-01", "window_end": "11-02"}
    assert in_season(w, today=_d("10-01"))       # start inclusive
    assert in_season(w, today=_d("10-20"))
    assert in_season(w, today=_d("11-02"))       # end inclusive
    assert not in_season(w, today=_d("09-30"))
    assert not in_season(w, today=_d("11-03"))


def test_in_season_wraps_the_new_year():
    w = {"window_start": "12-26", "window_end": "01-08"}
    assert in_season(w, today=_d("12-26"))
    assert in_season(w, today=_d("12-31"))
    assert in_season(w, today=_d("01-01"))
    assert in_season(w, today=_d("01-08"))
    assert not in_season(w, today=_d("01-09"))
    assert not in_season(w, today=_d("07-01"))


def test_in_season_no_or_invalid_window_is_always_on():
    assert in_season({}, today=_d("07-01"))
    assert in_season({"window_start": "12-01"}, today=_d("07-01"))       # half a window
    assert in_season({"window_start": "junk", "window_end": "01-08"}, today=_d("07-01"))
    assert in_season({"window_start": "13-40", "window_end": "01-08"}, today=_d("07-01"))
    assert in_season(None, today=_d("07-01"))


# ── sync behavior ────────────────────────────────────────────────────────────
@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, title, has_file) "
                     "VALUES (1, 'plex', 'srv1', 'Elf', 1)")
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES ('Family')")
        gid = conn.execute("SELECT id FROM genres WHERE name='Family'").fetchone()[0]
        conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (1, ?)", (gid,))
        conn.commit()
    finally:
        conn.close()


def _definition(db, start, end):
    cid = db.create_collection_definition(
        "Christmas", media_type="movie", window_start=start, window_end=end,
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Family"]}]})
    return db.get_collection_definition(cid)


class _Source:
    server_name = "plex"

    def __init__(self):
        self.created = []
        self.deleted = []

    def find_collection(self, kind, name):
        return None

    def create_collection(self, kind, name, ids):
        self.created.append(name)
        return {"ok": True, "server_id": "col1"}

    def collection_member_ids(self, cid):
        return []

    def collection_add(self, cid, ids):
        return {"ok": True}

    def collection_remove(self, cid, ids):
        return {"ok": True}

    def set_collection_meta(self, cid, **kw):
        return {"ok": True}

    def delete_collection(self, cid):
        self.deleted.append(str(cid))
        return {"ok": True}


def test_in_window_syncs_normally(db):
    _seed(db)
    d = _definition(db, "11-20", "01-06")
    src = _Source()
    r = sync_collection(db, d, source=src, today=_d("12-25"))
    assert r["ok"] and r.get("skipped") != "out_of_season"
    assert src.created == ["Christmas"]
    assert db.get_collection_sync(d["id"])["server_id"] == "col1"


def test_out_of_window_removes_our_server_collection(db):
    _seed(db)
    d = _definition(db, "11-20", "01-06")
    src = _Source()
    sync_collection(db, d, source=src, today=_d("12-25"))    # in season → created
    r = sync_collection(db, d, source=src, today=_d("07-01"))  # off season → removed
    assert r["ok"] and r["skipped"] == "out_of_season" and r["removed_server"] is True
    assert src.deleted == ["col1"]
    assert db.get_collection_sync(d["id"]) is None           # no ghost ledger
    # Back in season → recreated cleanly.
    r = sync_collection(db, d, source=src, today=_d("12-01"))
    assert r["ok"] and src.created == ["Christmas", "Christmas"]


def test_out_of_window_never_touches_unmanaged(db):
    _seed(db)
    d = _definition(db, "11-20", "01-06")
    src = _Source()
    # No ledger row (never synced) → nothing to remove, nothing deleted.
    r = sync_collection(db, d, source=src, today=_d("07-01"))
    assert r["ok"] and r["skipped"] == "out_of_season" and r["removed_server"] is False
    assert src.deleted == [] and src.created == []


def test_windows_roundtrip_and_clear(db):
    cid = db.create_collection_definition("S", window_start="12-26", window_end="01-08")
    c = db.get_collection_definition(cid)
    assert (c["window_start"], c["window_end"]) == ("12-26", "01-08")
    assert db.update_collection_definition(cid, window_start="", window_end="")
    c = db.get_collection_definition(cid)
    assert c["window_start"] is None and c["window_end"] is None


def test_seasonal_pack_applies_windows(db):
    from core.video.collections.presets import apply_pack
    r = apply_pack(db, "seasonal", "movie", ["seasonal:christmas"],
                   fetcher=lambda s, ref: [])
    full = db.get_collection_definition(r["created"][0]["id"])
    assert (full["window_start"], full["window_end"]) == ("11-20", "01-06")
