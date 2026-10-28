"""Older video databases carry an inline UNIQUE on shows.tvdb_id and
movies.tmdb_id. Boulder, Oct 28 2026: setting TVDB 363769 on a second copy of
"90 Day Fiancé: The Other Way" (his Plex lists it fifteen times) failed with
"Failed to update the match" - UNIQUE constraint failed: shows.tvdb_id.

The current schema has no such constraint (the same series can sit in more
than one item or library). Boot rebuilds an old table without it, and the
rebuild must never cost a row: episodes, seasons and media files reference
these tables ON DELETE CASCADE.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from database.video_database import VideoDatabase

_SCHEMA = (Path(__file__).resolve().parents[2] / "database" / "video_schema.sql").read_text(encoding="utf-8")


def _legacy_db(path):
    """A database in the old shape: today's schema with the old inline UNIQUEs."""
    legacy = re.sub(r"(\n\s*tvdb_id\s+INTEGER),(\s*--[^\n]*)?", r"\1 UNIQUE,", _SCHEMA, count=1)
    legacy = re.sub(r"(CREATE TABLE IF NOT EXISTS movies \([\s\S]*?\n\s*tmdb_id\s+INTEGER)", r"\1 UNIQUE", legacy, count=1)
    assert legacy.count(" UNIQUE,") >= 1
    conn = sqlite3.connect(str(path))
    conn.executescript(legacy)
    conn.execute("INSERT INTO shows (id, server_source, server_id, title, tvdb_id, tmdb_id) "
                 "VALUES (1, 'plex', 'a', 'Split Show', 363769, 90046)")
    conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                 "VALUES (2, 'plex', 'b', 'Split Show', 90046)")
    for sid in (1, 2):
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (?, ?, 1)", (sid, sid))
        for n in (1, 2, 3):
            conn.execute("INSERT INTO episodes (show_id, season_id, server_source, season_number, "
                         "episode_number, title) VALUES (?, ?, 'plex', 1, ?, 'E')", (sid, sid, n))
    conn.commit()
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='shows'").fetchone()[0]
    conn.close()
    assert re.search(r"tvdb_id\s+INTEGER UNIQUE", sql), "fixture must have the legacy constraint"


def _unique_lines(path, table):
    conn = sqlite3.connect(str(path))
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
    conn.close()
    return [l for l in sql.splitlines() if re.search(r"\b(tvdb|tmdb|imdb)_id\b.*UNIQUE", l)]


def test_boot_lifts_the_legacy_unique_without_losing_a_row(tmp_path):
    path = tmp_path / "video_library.db"
    _legacy_db(path)
    db = VideoDatabase(database_path=str(path))
    assert _unique_lines(path, "shows") == []
    assert _unique_lines(path, "movies") == []
    conn = sqlite3.connect(str(path))
    # nothing cascaded away with the dropped table
    assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM shows").fetchone()[0] == 2
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # the views and indexes are back
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('view','index')")}
    assert {"idx_shows_tvdb", "ux_shows_server"} <= names
    conn.close()
    # and the second copy takes the id that used to be refused
    assert db.rematch_item("show", 2, "tvdb", 363769)
    conn = sqlite3.connect(str(path))
    assert sorted(r[0] for r in conn.execute("SELECT id FROM shows WHERE tvdb_id=363769")) == [1, 2]
    conn.close()


def test_a_current_database_is_left_alone(tmp_path):
    path = tmp_path / "video_library.db"
    VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(str(path))
    before = conn.execute("SELECT sql FROM sqlite_master WHERE name='shows'").fetchone()[0]
    conn.close()
    VideoDatabase(database_path=str(path))._drop_legacy_id_uniques()
    conn = sqlite3.connect(str(path))
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name='shows'").fetchone()[0] == before
    conn.close()


def test_a_refused_rematch_says_who_has_the_id(tmp_path, monkeypatch):
    """If the rebuild couldn't run, the editor names the other row instead of
    'Failed to update the match'."""
    import api.video as videoapi
    from flask import Flask
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.upsert_show_tree("plex", {"server_id": "a", "title": "Split Show", "tmdb_id": 90046, "seasons": []})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET tvdb_id=363769, year=2019")
    other = conn.execute("SELECT id FROM shows").fetchone()[0]
    conn.commit(); conn.close()
    videoapi._video_db = db

    def refuse(*a, **k):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: shows.tvdb_id")

    monkeypatch.setattr(db, "rematch_item", refuse)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().post("/api/video/enrichment/matches/show/9999/apply",
                                   json={"service": "tvdb", "external_id": 363769})
        assert r.status_code == 409
        body = r.get_json()
        assert "TVDB 363769 is already on “Split Show” (2019)" in body["error"]
        assert body["conflict"]["id"] == other
    finally:
        videoapi._video_db = None
