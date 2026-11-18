"""v74 (#2168): a video quality profile says what it's for, movie, tv or
NULL for both. an upgraded database keeps every profile as both, so
nothing disappears from anyone's request dialog."""

from __future__ import annotations

import json
import sqlite3

import pytest

from database import video_database as vdb
from database.video_database import VideoDatabase


def test_schema_version_and_column_ride_the_migrations():
    assert vdb.SCHEMA_VERSION >= 74
    assert ("quality_profiles", "media_type", "TEXT") in vdb._COLUMN_MIGRATIONS


def test_a_v73_database_upgrades_with_every_profile_left_as_both(tmp_path):
    path = tmp_path / "v73.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE quality_profiles (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, cutoff TEXT,
            items TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO quality_profiles (id, name, cutoff, items) VALUES (3, 'Movie 4K', '2160p', '{"library": "4k"}');
        PRAGMA user_version = 73;
    """)
    conn.commit()
    conn.close()
    db = VideoDatabase(database_path=str(path))
    rows = db.named_quality_profiles()
    assert rows == [{"id": 3, "name": "Movie 4K", "cutoff": "2160p", "items": '{"library": "4k"}',
                     "media_type": None}]
    assert db.get_named_quality_profile(3)["media_type"] is None
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == vdb.SCHEMA_VERSION
    finally:
        conn.close()


def test_media_type_is_set_and_checked(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    pid = db.upsert_named_quality_profile(None, "TV 1080p", json.dumps({}), "1080p")
    assert db.set_named_quality_profile_media_type(pid, "tv")
    assert db.get_named_quality_profile(pid)["media_type"] == "tv"
    assert db.set_named_quality_profile_media_type(pid, "both")
    assert db.get_named_quality_profile(pid)["media_type"] is None
    assert db.set_named_quality_profile_media_type(pid, "movie")
    for bad in ("film", "TV", 3):
        with pytest.raises(ValueError):
            db.set_named_quality_profile_media_type(pid, bad)
    assert db.get_named_quality_profile(pid)["media_type"] == "movie"
    assert not db.set_named_quality_profile_media_type(99999, "tv")
