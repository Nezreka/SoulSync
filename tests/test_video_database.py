"""Seam tests for the isolated video database (experimental branch).

These pin the contract that matters: the schema builds with all tables/views,
the no-polymorphic-id CHECK constraints actually reject bad rows, cascades fire,
the derived Watchlist/Wishlist/Calendar views return the right membership, the
settings KV roundtrips — and, critically, that the video DB layer imports
NOTHING from the music database (isolation).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.video_database import VideoDatabase, SCHEMA_VERSION

_MODULE = Path(__file__).resolve().parent.parent / "database" / "video_database.py"

EXPECTED_TABLES = {
    "meta", "root_folders", "quality_profiles", "video_settings",
    "movies", "shows", "seasons", "episodes", "channels", "channel_videos",
    "media_files", "downloads", "activity",
}
EXPECTED_VIEWS = {"v_watchlist", "v_wishlist", "v_calendar"}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── schema ──────────────────────────────────────────────────────────────────

def test_schema_builds_with_all_tables_and_views(db):
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
    assert EXPECTED_TABLES <= tables, f"missing tables: {EXPECTED_TABLES - tables}"
    assert views == EXPECTED_VIEWS


def test_schema_version_recorded(db):
    assert db.schema_version == SCHEMA_VERSION


def test_init_is_idempotent(tmp_path):
    path = str(tmp_path / "video_library.db")
    VideoDatabase(database_path=path)
    # Second construction on the same path must not raise or wipe data.
    db2 = VideoDatabase(database_path=path)
    assert db2.health_check()


def test_health_check_ok(db):
    assert db.health_check() is True


# ── no-polymorphic-id CHECK constraints ──────────────────────────────────────

def test_media_file_requires_exactly_one_owner(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number) "
                     "VALUES (1,1,1,1,1)")
        # zero owners -> reject
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_files(relative_path) VALUES ('x.mkv')")
        # two owners -> reject
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_files(movie_id,episode_id,relative_path) "
                         "VALUES (1,1,'x.mkv')")
        # exactly one -> ok
        conn.execute("INSERT INTO media_files(movie_id,relative_path) VALUES (1,'x.mkv')")


def test_download_requires_exactly_one_target(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO downloads(title) VALUES ('no target')")
        conn.execute("INSERT INTO downloads(movie_id,title) VALUES (1,'ok')")


def test_download_status_is_constrained(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO downloads(movie_id,title,status) VALUES (1,'x','bogus')")


# ── cascades ──────────────────────────────────────────────────────────────────

def test_deleting_show_cascades_to_seasons_and_episodes(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number) "
                     "VALUES (1,1,1,1,1)")
        conn.execute("DELETE FROM shows WHERE id=1")
        assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0


# ── derived views ─────────────────────────────────────────────────────────────

def test_watchlist_view_is_monitored_shows_and_channels(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO shows(id,title,monitored) VALUES (1,'Followed',1)")
        conn.execute("INSERT INTO shows(id,title,monitored) VALUES (2,'Unfollowed',0)")
        conn.execute("INSERT INTO channels(id,youtube_id,title,monitored) VALUES (1,'yt1','Chan',1)")
        rows = {(r["kind"], r["title"]) for r in conn.execute("SELECT kind,title FROM v_watchlist")}
    assert rows == {("show", "Followed"), ("channel", "Chan")}


def test_wishlist_view_is_wanted_but_missing(db):
    with db.connect() as conn:
        # wanted movie (monitored, no file) -> in wishlist
        conn.execute("INSERT INTO movies(id,title,monitored,has_file) VALUES (1,'Want',1,0)")
        # owned movie -> not in wishlist
        conn.execute("INSERT INTO movies(id,title,monitored,has_file) VALUES (2,'Own',1,1)")
        # aired, monitored, missing episode -> in wishlist; future episode -> not
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number,"
                     "monitored,has_file,air_date) VALUES (1,1,1,1,1,1,0,'2000-01-01')")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number,"
                     "monitored,has_file,air_date) VALUES (2,1,1,1,2,1,0,'2999-01-01')")
        rows = [(r["kind"], r["ref_id"]) for r in conn.execute("SELECT kind,ref_id FROM v_wishlist")]
    assert ("movie", 1) in rows        # wanted movie present
    assert ("movie", 2) not in rows    # owned movie absent
    assert ("episode", 1) in rows      # aired+missing episode present
    assert ("episode", 2) not in rows  # future episode absent


def test_settings_kv_roundtrip(db):
    assert db.get_setting("download_dir", "unset") == "unset"
    db.set_setting("download_dir", "/data/video")
    assert db.get_setting("download_dir") == "/data/video"
    db.set_setting("download_dir", "/data/video2")  # upsert
    assert db.get_setting("download_dir") == "/data/video2"


# ── isolation: the video DB imports nothing from music ───────────────────────

def test_video_db_module_imports_nothing_from_music():
    src = _MODULE.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "music" not in stripped.lower(), f"music import leaked in: {stripped!r}"


def test_video_db_uses_distinct_default_path_and_env():
    src = _MODULE.read_text(encoding="utf-8")
    assert "video_library.db" in src
    assert "VIDEO_DATABASE_PATH" in src      # distinct env var from music's DATABASE_PATH
    assert "music_library.db" not in src
