"""DB-backed tests for the collections spine: definition CRUD, smart-member
resolution against a real (temp) video DB, and the list/franchise resolver."""

from __future__ import annotations

import pytest

from core.video.collections.resolver import resolve_collection
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── fixture inserts ─────────────────────────────────────────────────────────
_AUTO = object()


def _add_movie(db, *, mid, title, tmdb_id=None, server_id=_AUTO, year=2000, rating=7.0,
               studio=None, content_rating=None, franchise=None, release_date=None,
               genres=(), director=None, resolution=None):
    # (server_source, server_id) is UNIQUE, so give each movie a distinct id
    # unless the test pins one (e.g. None for a not-on-server wishlist item).
    sid = f"srv{mid}" if server_id is _AUTO else server_id
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, tmdb_id, title, year, rating, "
            "studio, content_rating, tmdb_collection_id, release_date, has_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
            (mid, "plex", sid, tmdb_id, title, year, rating, studio, content_rating,
             franchise, release_date))
        for g in genres:
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (g,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (g,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        if director:
            conn.execute("INSERT INTO people (name) VALUES (?)", (director,))
            pid = conn.execute("SELECT id FROM people WHERE name=?", (director,)).fetchone()[0]
            conn.execute(
                "INSERT INTO credits (person_id, movie_id, department, job) VALUES (?,?, 'crew', 'Director')",
                (pid, mid))
        if resolution:
            conn.execute(
                "INSERT INTO media_files (movie_id, relative_path, resolution) VALUES (?,?,?)",
                (mid, f"{title}.mkv", resolution))
        conn.commit()
    finally:
        conn.close()


# ── definition CRUD ─────────────────────────────────────────────────────────
def test_definition_crud_roundtrip(db):
    cid = db.create_collection_definition(
        "80s Action", kind="smart", media_type="movie",
        definition={"match": "all", "rules": [{"field": "genre", "op": "in", "value": ["Action"]}]},
        summary="the good stuff", pinned=True)
    assert cid

    got = db.get_collection_definition(cid)
    assert got["name"] == "80s Action"
    assert got["pinned"] == 1
    assert got["definition"]["rules"][0]["field"] == "genre"   # parsed to dict

    assert db.update_collection_definition(cid, name="Renamed", enabled=False) is True
    got = db.get_collection_definition(cid)
    assert got["name"] == "Renamed" and got["enabled"] == 0

    listed = db.list_collection_definitions()
    assert any(r["id"] == cid for r in listed)
    assert "definition" not in listed[0]   # light rows omit the blob

    dup = db.duplicate_collection_definition(cid)
    assert dup and dup != cid
    assert db.get_collection_definition(dup)["name"] == "Renamed (copy)"

    assert db.delete_collection_definition(cid) is True
    assert db.get_collection_definition(cid) is None


# ── smart resolution ────────────────────────────────────────────────────────
def test_resolve_smart_members_matches_rules(db):
    _add_movie(db, mid=1, title="Die Hard", genres=["Action"], resolution="2160p", year=1988, rating=8.2)
    _add_movie(db, mid=2, title="Mad Max", genres=["Action"], resolution="1080p", year=1979, rating=7.5)
    _add_movie(db, mid=3, title="Airplane", genres=["Comedy"], resolution="2160p", year=1980, rating=7.7)

    defn = {"match": "all", "rules": [
        {"field": "genre", "op": "in", "value": ["Action"]},
        {"field": "resolution", "op": "in", "value": ["2160p"]},
    ]}
    rows = db.resolve_smart_members("movie", defn)
    assert {r["title"] for r in rows} == {"Die Hard"}


def test_resolve_smart_excludes_items_not_on_server(db):
    _add_movie(db, mid=1, title="Owned", genres=["Action"], server_id="x1")
    _add_movie(db, mid=2, title="WishlistOnly", genres=["Action"], server_id=None)
    rows = db.resolve_smart_members("movie", {"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    assert {r["title"] for r in rows} == {"Owned"}


def test_resolve_collection_smart_error_does_not_raise(db):
    # empty rules -> SmartFilterError, surfaced as .error, not an exception
    res = resolve_collection(db, {"media_type": "movie", "kind": "smart", "definition": {"rules": []}})
    assert res.ok is False
    assert "no rules" in (res.error or "")
    assert res.owned == []


def test_resolve_collection_smart_ok(db):
    _add_movie(db, mid=1, title="Nolan Film", director="Christopher Nolan")
    _add_movie(db, mid=2, title="Other", director="Someone Else")
    res = resolve_collection(db, {
        "media_type": "movie", "kind": "smart",
        "definition": {"rules": [{"field": "director", "op": "is", "value": "Christopher Nolan"}]},
    })
    assert res.ok
    assert [m["title"] for m in res.owned] == ["Nolan Film"]
    assert res.server_ids == ["srv1"]


# ── franchise / list resolution ─────────────────────────────────────────────
def test_franchise_owned_plus_injected_missing(db):
    _add_movie(db, mid=1, title="Matrix", tmdb_id=603, franchise=2344, server_id="a")
    _add_movie(db, mid=2, title="Reloaded", tmdb_id=604, franchise=2344, server_id="b")

    def fetcher(source, ref):
        assert source == "tmdb_collection" and ref == 2344
        return [
            {"tmdb_id": 603, "title": "Matrix"},
            {"tmdb_id": 604, "title": "Reloaded"},
            {"tmdb_id": 605, "title": "Revolutions"},   # not owned
        ]

    res = resolve_collection(
        db, {"media_type": "movie", "kind": "list",
             "definition": {"source": "tmdb_collection", "collection_id": 2344}},
        list_fetcher=fetcher)
    assert res.ok
    assert {m["title"] for m in res.owned} == {"Matrix", "Reloaded"}
    assert [m["tmdb_id"] for m in res.missing] == [605]


def test_franchise_works_without_fetcher_no_missing(db):
    _add_movie(db, mid=1, title="Matrix", tmdb_id=603, franchise=2344)
    res = resolve_collection(db, {"media_type": "movie", "kind": "list",
                                  "definition": {"source": "tmdb_collection", "collection_id": 2344}})
    assert res.ok and len(res.owned) == 1 and res.missing == []


def test_tmdb_list_intersects_owned(db):
    _add_movie(db, mid=1, title="A", tmdb_id=1)
    _add_movie(db, mid=2, title="B", tmdb_id=2)

    def fetcher(source, ref):
        assert source == "tmdb_list"
        return [{"tmdb_id": 1, "title": "A"}, {"tmdb_id": 2, "title": "B"}, {"tmdb_id": 9, "title": "Z"}]

    res = resolve_collection(
        db, {"media_type": "movie", "kind": "list",
             "definition": {"source": "tmdb_list", "list_id": "abc"}},
        list_fetcher=fetcher)
    assert {m["title"] for m in res.owned} == {"A", "B"}
    assert [m["tmdb_id"] for m in res.missing] == [9]


def test_list_without_fetcher_errors_gracefully(db):
    res = resolve_collection(db, {"media_type": "movie", "kind": "list",
                                  "definition": {"source": "tmdb_list", "list_id": "abc"}})
    assert res.ok is False and "fetcher" in (res.error or "")


def test_collection_sync_ledger_roundtrip(db):
    cid = db.create_collection_definition("X", definition={"rules": [{"field": "genre", "op": "in", "value": ["A"]}]})
    assert db.get_collection_sync(cid) is None
    db.record_collection_sync(cid, server_source="plex", server_id="col-1", members_sig="sig1", member_count=3)
    row = db.get_collection_sync(cid)
    assert row["server_id"] == "col-1" and row["member_count"] == 3 and row["synced_at"]
    # list rows carry the joined sync info
    listed = {r["id"]: r for r in db.list_collection_definitions()}
    assert listed[cid]["member_count"] == 3
    db.record_collection_sync(cid, server_source="plex", server_id="col-1", members_sig="sig2", member_count=5)
    assert db.get_collection_sync(cid)["member_count"] == 5   # upsert
