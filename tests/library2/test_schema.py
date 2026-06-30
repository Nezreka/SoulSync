"""Schema creation + idempotency for Library v2."""

from __future__ import annotations

import sqlite3

from core.library2.schema import ensure_library_v2_schema

_EXPECTED_TABLES = {
    "lib2_quality_profiles",
    "lib2_artists", "lib2_albums", "lib2_album_artists",
    "lib2_tracks", "lib2_track_artists", "lib2_track_files",
    "lib2_manual_skips",
}


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'lib2_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_creates_all_tables():
    conn = sqlite3.connect(":memory:")
    ensure_library_v2_schema(conn)
    assert _tables(conn) == _EXPECTED_TABLES


def test_idempotent_rerun():
    conn = sqlite3.connect(":memory:")
    ensure_library_v2_schema(conn)
    # Second run must not raise and must leave the schema unchanged.
    ensure_library_v2_schema(conn)
    assert _tables(conn) == _EXPECTED_TABLES
    idx = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_lib2_%'"
    ).fetchone()[0]
    assert idx >= 17


def test_seeds_quality_profiles():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    rows = conn.execute(
        "SELECT name, upgrade_policy, repair_job_id, repair_settings "
        "FROM lib2_quality_profiles ORDER BY id"
    ).fetchall()

    assert [r["name"] for r in rows] == ["Balanced", "Upgrade until top quality"]
    assert rows[1]["upgrade_policy"] == "until_top"
    assert rows[1]["repair_job_id"] == "quality_upgrade"
    assert "require_top_target" in rows[1]["repair_settings"]


def test_foreign_keys_and_inserts():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    aid = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Alb')", (aid,))
    alb = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'T')", (alb,))
    tid = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/x.flac')", (tid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1

    # Deleting the artist cascades to album/track (ON DELETE CASCADE).
    cur.execute("DELETE FROM lib2_artists WHERE id=?", (aid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 0
