"""Schema creation + idempotency for Library v2."""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.schema import ensure_library_v2_schema

_EXPECTED_TABLES = {
    "lib2_artists", "lib2_albums", "lib2_album_artists",
    "lib2_tracks", "lib2_track_artists", "lib2_track_files",
    "lib2_manual_skips", "lib2_mirror_outbox", "lib2_monitor_rules",
    "lib2_release_editions", "lib2_recordings", "lib2_release_tracks",
    "lib2_recording_review", "lib2_wanted_tracks", "lib2_external_id_history",
    "lib2_entity_history", "lib2_metadata_overrides",
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
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        row = conn.execute(
            f"SELECT quality_profile_id FROM {table} LIMIT 1"
        ).fetchone()
        assert row[0] == 1
        info = {
            column[1]: column for column in conn.execute(
                f"PRAGMA table_info({table})")
        }
        assert info["quality_profile_id"][4] is None
        assert any(
            fk[2] == "quality_profiles" and fk[3] == "quality_profile_id"
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})")
        )

    # Deleting the artist cascades to album/track (ON DELETE CASCADE).
    cur.execute("DELETE FROM lib2_artists WHERE id=?", (aid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 0


def test_live_default_trigger_and_quality_reference_guards():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_library_v2_schema(conn)
    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
    conn.execute("DELETE FROM quality_profiles WHERE id=1")

    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('A')"
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
        (artist_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Track')",
        (album_id,),
    )
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        assert conn.execute(
            f"SELECT quality_profile_id FROM {table} LIMIT 1"
        ).fetchone()[0] == 2

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lib2_artists(name, quality_profile_id) VALUES('Bad', 999)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE lib2_artists SET quality_profile_id=NULL WHERE id=?",
            (artist_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="referenced by Library v2"):
        conn.execute("DELETE FROM quality_profiles WHERE id=2")


def test_migrates_default_one_columns_without_losing_graph_data():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_library_v2_schema(conn)
    artist_id = conn.execute("INSERT INTO lib2_artists(name) VALUES('A')").lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
        (artist_id,),
    ).lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Track')",
        (album_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?, ?)",
        (album_id, artist_id),
    )
    conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?, ?)",
        (track_id, artist_id),
    )

    conn.execute("DROP TRIGGER trg_quality_profiles_lib2_restrict")
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        for suffix in ("default", "insert", "update"):
            conn.execute(f"DROP TRIGGER trg_{table}_quality_profile_{suffix}")
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN quality_profile_id_old "
            "INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            f"UPDATE {table} SET quality_profile_id_old=quality_profile_id")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN quality_profile_id")
        conn.execute(
            f"ALTER TABLE {table} RENAME COLUMN quality_profile_id_old "
            "TO quality_profile_id")
    conn.execute("UPDATE lib2_artists SET quality_profile_id=999")
    conn.execute("UPDATE lib2_albums SET quality_profile_id=2")
    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
    conn.execute("DELETE FROM quality_profiles WHERE id=1")
    conn.commit()

    ensure_library_v2_schema(conn)

    assert conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT quality_profile_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM lib2_album_artists").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists").fetchone()[0] == 1
    for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
        info = {
            column[1]: column for column in conn.execute(
                f"PRAGMA table_info({table})")
        }
        assert info["quality_profile_id"][4] is None
        assert any(
            fk[2] == "quality_profiles" and fk[3] == "quality_profile_id"
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
