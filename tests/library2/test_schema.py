"""Schema creation + idempotency for Library v2."""

from __future__ import annotations

import sqlite3

from core.library2.schema import ensure_library_v2_schema

_EXPECTED_TABLES = {
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


def test_ensures_app_wide_quality_profiles():
    """lib2 depends on the app-wide quality_profiles table (the same rows the
    wishlist/download pipeline resolves) — ensuring the lib2 schema must make
    it available for standalone (test-harness) use."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    rows = conn.execute(
        "SELECT name, upgrade_policy FROM quality_profiles ORDER BY id"
    ).fetchall()
    assert [r["name"] for r in rows] == ["Balanced", "Upgrade until top quality"]
    assert rows[1]["upgrade_policy"] in ("until_cutoff", "until_top")


def test_idempotent_rerun():
    conn = sqlite3.connect(":memory:")
    ensure_library_v2_schema(conn)
    # Second run must not raise and must leave the schema unchanged.
    ensure_library_v2_schema(conn)
    assert _tables(conn) == _EXPECTED_TABLES
    idx = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_lib2_%'"
    ).fetchone()[0]
    assert idx >= 16


def test_migrates_parallel_profile_table_to_app_wide():
    """Old installs carried a parallel lib2_quality_profiles table whose ids
    never reached the pipeline. Ensure remaps assignments by profile name onto
    the app-wide table and drops the old one."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Simulate the old install: parallel table with a custom profile id 7.
    cur.execute("""CREATE TABLE lib2_quality_profiles(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)""")
    cur.executemany(
        "INSERT INTO lib2_quality_profiles(id, name) VALUES(?,?)",
        [(1, "Balanced"), (2, "Upgrade until top quality"), (7, "My Hi-Res")])
    # The app-wide table has the same names but a different id for the custom one.
    from core.quality.schema import ensure_quality_profiles_schema
    ensure_quality_profiles_schema(conn)
    cur.execute(
        "INSERT INTO quality_profiles(id, name, ranked_targets) VALUES(4, 'My Hi-Res', '[]')")
    # lib2 rows that point at the OLD ids (same shape the real DDL creates,
    # so the ensure pass can build its indexes over this pre-existing table).
    cur.execute("""CREATE TABLE lib2_artists(
        id INTEGER PRIMARY KEY, name TEXT, sort_name TEXT, spotify_id TEXT,
        musicbrainz_id TEXT, legacy_artist_id INTEGER,
        quality_profile_id INTEGER NOT NULL DEFAULT 1)""")
    cur.execute("INSERT INTO lib2_artists(name, quality_profile_id) VALUES('A', 7)")
    cur.execute("INSERT INTO lib2_artists(name, quality_profile_id) VALUES('B', 2)")
    cur.execute("INSERT INTO lib2_artists(name, quality_profile_id) VALUES('C', 99)")
    conn.commit()

    ensure_library_v2_schema(conn)

    rows = {r["name"]: r["quality_profile_id"]
            for r in conn.execute("SELECT name, quality_profile_id FROM lib2_artists")}
    assert rows["A"] == 4      # custom profile remapped by name
    assert rows["B"] == 2      # same-name same-id stays
    assert rows["C"] == 1      # dangling pointer → default
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='lib2_quality_profiles'"
    ).fetchone()[0] == 0
    # Re-run is a no-op (table gone).
    ensure_library_v2_schema(conn)


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
