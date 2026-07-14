"""User metadata edits + field locks: an edited field belongs to the user —
scan upserts (every mode, including FULL) and enrichment leave it alone until
the lock is released, at which point the next scan re-adopts the server value."""

from __future__ import annotations

import pytest

from database import video_database as vd
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _scan_movie(db, preserve=True, **over):
    item = {"server_id": "m1", "title": "Server Title", "year": 1999,
            "overview": "server overview", "tagline": "server tagline",
            "content_rating": "PG", "studio": "Server Studio",
            "genres": ["Action", "Sci-Fi"], "file": {"path": "/x.mkv"}}
    item.update(over)
    return db.upsert_movie("plex", item, preserve_enrichment=preserve)


def _scan_show(db, preserve=True, **over):
    item = {"server_id": "s1", "title": "Server Show", "year": 2010,
            "overview": "server overview", "network": "HBO",
            "genres": ["Drama"], "seasons": []}
    item.update(over)
    return db.upsert_show_tree("plex", item, preserve_enrichment=preserve)


def _movie(db, mid):
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone()
        genres = [r["name"] for r in conn.execute(
            "SELECT g.name FROM movie_genres mg JOIN genres g ON g.id=mg.genre_id "
            "WHERE mg.movie_id=? ORDER BY g.name", (mid,)).fetchall()]
        return dict(row) | {"genres": genres}
    finally:
        conn.close()


# ── update_item_fields ────────────────────────────────────────────────────────
def test_edit_writes_locks_and_derives_sort_title(db):
    mid = _scan_movie(db)
    res = db.update_item_fields("movie", mid, {"title": "The User Cut", "year": 2001})
    assert sorted(res["applied"]) == ["sort_title", "title", "year"]
    m = _movie(db, mid)
    assert (m["title"], m["sort_title"], m["year"]) == ("The User Cut", "user cut", 2001)
    assert db.get_locked_fields("movie", mid) == ["sort_title", "title", "year"]


def test_edit_validation(db):
    mid = _scan_movie(db)
    with pytest.raises(ValueError):
        db.update_item_fields("movie", mid, {"poster_url": "https://x"})  # not editable
    with pytest.raises(ValueError):
        db.update_item_fields("movie", mid, {"title": "  "})              # empty title
    with pytest.raises(ValueError):
        db.update_item_fields("movie", mid, {"year": "next year"})
    with pytest.raises(ValueError):
        db.update_item_fields("movie", mid, {"genres": "Action"})         # not a list
    assert db.get_locked_fields("movie", mid) == []                       # nothing applied
    assert db.update_item_fields("movie", 999999, {"title": "X"}) is None


def test_explicit_sort_title_not_overridden_by_title_edit(db):
    mid = _scan_movie(db)
    db.update_item_fields("movie", mid, {"sort_title": "zzz custom"})
    db.update_item_fields("movie", mid, {"title": "The New Name"})
    assert _movie(db, mid)["sort_title"] == "zzz custom"                  # locked → not re-derived


# ── scan upserts honor locks (every mode) ────────────────────────────────────
def test_locked_fields_survive_incremental_and_full_scans(db):
    mid = _scan_movie(db)
    db.update_item_fields("movie", mid, {"title": "The User Cut", "tagline": "mine"})
    for preserve in (True, False):                                        # incremental AND full
        assert _scan_movie(db, preserve=preserve) == mid
        m = _movie(db, mid)
        assert m["title"] == "The User Cut" and m["tagline"] == "mine"
        # Unlocked fields still track the server.
        assert m["studio"] == "Server Studio" and m["content_rating"] == "PG"


def test_lock_is_per_field_not_substring(db):
    # Locking "title" must NOT shield "sort_title" (quoted-name instr matching).
    mid = _scan_movie(db)
    conn = db._get_connection()
    conn.execute("UPDATE movies SET locked_fields='[\"title\"]' WHERE id=?", (mid,))
    conn.commit(); conn.close()
    _scan_movie(db, title="Server Rename")
    m = _movie(db, mid)
    assert m["title"] == "Server Title"                # locked → kept
    assert m["sort_title"] == "server rename"          # unlocked → follows server title


def test_locked_genres_survive_scan_and_unlocked_get_replaced(db):
    mid = _scan_movie(db)
    db.update_item_fields("movie", mid, {"genres": ["Comfort Films", "Action"]})
    _scan_movie(db, genres=["Horror"])
    assert _movie(db, mid)["genres"] == ["Action", "Comfort Films"]
    db.set_field_lock("movie", mid, "genres", False)
    _scan_movie(db, genres=["Horror"])
    assert _movie(db, mid)["genres"] == ["Horror"]


def test_show_tree_scan_honors_locks(db):
    sid = _scan_show(db)
    db.update_item_fields("show", sid, {"title": "My Show Name", "genres": ["Anime"]})
    assert _scan_show(db, title="Server Rename", genres=["Drama", "Crime"]) == sid
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT title FROM shows WHERE id=?", (sid,)).fetchone()
        genres = [r["name"] for r in conn.execute(
            "SELECT g.name FROM show_genres sg JOIN genres g ON g.id=sg.genre_id "
            "WHERE sg.show_id=?", (sid,)).fetchall()]
    finally:
        conn.close()
    assert row["title"] == "My Show Name" and genres == ["Anime"]


def test_released_lock_readopts_server_value_on_next_scan(db):
    mid = _scan_movie(db)
    db.update_item_fields("movie", mid, {"title": "The User Cut"})
    assert db.set_field_lock("movie", mid, "title", False) == ["sort_title"]
    db.set_field_lock("movie", mid, "sort_title", False)
    _scan_movie(db)
    assert _movie(db, mid)["title"] == "Server Title"


# ── enrichment honors locks ──────────────────────────────────────────────────
def test_enrichment_never_touches_locked_fields(db):
    mid = _scan_movie(db, tagline="", genres=[])
    db.update_item_fields("movie", mid, {"tagline": ""})       # deliberately blanked + locked
    db.set_field_lock("movie", mid, "genres", True)
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=603,
                        metadata={"tagline": "tmdb tagline", "genres": ["Action"],
                                  "studio": "TMDB Studio"})
    m = _movie(db, mid)
    assert m["tagline"] == "" and m["genres"] == []             # locked: even blanks stay
    assert m["studio"] == "Server Studio"                       # unlocked non-blank: untouched (gap-fill)
    assert m["tmdb_id"] == 603                                  # match itself still recorded


def test_enrichment_still_gapfills_unlocked_blanks(db):
    mid = _scan_movie(db, tagline="", genres=[])
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=603,
                        metadata={"tagline": "tmdb tagline", "genres": ["Action"]})
    m = _movie(db, mid)
    assert m["tagline"] == "tmdb tagline" and m["genres"] == ["Action"]


# ── lock plumbing ────────────────────────────────────────────────────────────
def test_set_field_lock_validates(db):
    mid = _scan_movie(db)
    assert db.set_field_lock("movie", mid, "poster_url", True) is None    # not an editable field
    assert db.set_field_lock("movie", 999999, "title", True) is None      # no such row
    assert db.set_field_lock("album", mid, "title", True) is None         # bad kind
    assert db.set_field_lock("movie", mid, "title", True) == ["title"]
    assert db.set_field_lock("movie", mid, "title", False) == []
    assert db.get_locked_fields("movie", mid) == []


def test_locked_fields_columns_migrate_onto_old_dbs(tmp_path):
    # Boulder runs the live server from the working tree — the column must ride
    # _COLUMN_MIGRATIONS so a pre-v29 DB is repaired on boot.
    import sqlite3
    path = tmp_path / "video_library.db"
    VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE movies DROP COLUMN locked_fields")
    conn.execute("ALTER TABLE shows DROP COLUMN locked_fields")
    conn.commit(); conn.close()
    vd._initialized_paths.discard(str(path.resolve()))
    db = VideoDatabase(database_path=str(path))                 # boot → migration repairs
    mid = _scan_movie(db)
    assert db.update_item_fields("movie", mid, {"title": "Edited"})["locked"]
