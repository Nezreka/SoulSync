"""Migration + regression test for the per-track ``year`` column (#910).

Full Refresh INSERTs a per-track ``year`` (read from file tags), but the column was
only ever present in that live INSERT — never in CREATE TABLE and never in a
migration. So on every DB (old *and* current) the Full Refresh track insert
hard-failed with ``table tracks has no column named year``, importing 0 tracks
while artists/albums imported fine.

The repair backstop (``_ensure_core_media_schema_columns``) must ALTER ``year``
onto any tracks table that lacks it. Additive + nullable; nothing reads it except
the writer, so backfilling it on every existing DB is safe.
"""

from __future__ import annotations

import sqlite3

import pytest

from database.music_database import MusicDatabase


def _track_cols(cur):
    cur.execute("PRAGMA table_info(tracks)")
    return {c[1] for c in cur.fetchall()}


# An upgraded tracks table that has every Full Refresh insert column EXCEPT year
# (mirrors the #910 reporter exactly: "64 columns, only year absent"). Text ids
# match the live schema after the id->TEXT migration.
_OLD_TRACKS = (
    "CREATE TABLE tracks (id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, "
    "title TEXT, track_number INTEGER, disc_number INTEGER, duration INTEGER, "
    "file_path TEXT, bitrate INTEGER, server_source TEXT, "
    "created_at TIMESTAMP, updated_at TIMESTAMP)"
)

# The exact Full Refresh insert from web_server.py (the statement that failed).
_FULL_REFRESH_INSERT = (
    "INSERT OR IGNORE INTO tracks (id, album_id, artist_id, title, track_number, disc_number, "
    "duration, file_path, bitrate, year, server_source, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'soulsync', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)
_ROW = ('t1::soulsync', 'a1', 'ar1', 'Song', 1, 1, 200000, '/music/song.flac', 1000, 2009)


def test_fresh_db_has_year_column(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    assert 'year' in _track_cols(cur)


def test_year_column_is_nullable(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    cur.execute("PRAGMA table_info(tracks)")
    info = {c[1]: c for c in cur.fetchall()}  # name -> (cid, name, type, notnull, dflt, pk)
    assert info['year'][3] == 0  # nullable -> safe to add to a populated table


def test_migration_is_idempotent(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    before = _track_cols(cur)
    # Re-running must not raise (the PRAGMA guard skips the existing column).
    db._ensure_core_media_schema_columns(cur)
    db._ensure_core_media_schema_columns(cur)
    assert _track_cols(cur) == before
    assert 'year' in _track_cols(cur)


def test_migration_adds_year_to_old_tracks_table(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute(_OLD_TRACKS)
    conn.commit()
    cur = conn.cursor()
    assert 'year' not in _track_cols(cur)

    db = MusicDatabase(str(tmp_path / "scratch.db"))
    db._ensure_core_media_schema_columns(cur)
    conn.commit()

    assert 'year' in _track_cols(cur)


def test_full_refresh_insert_fails_before_repair_and_succeeds_after(tmp_path):
    """Regression for #910: the real Full Refresh track insert hard-fails on a
    year-less tracks table, then succeeds once the repair has added the column."""
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute(_OLD_TRACKS)
    conn.commit()
    cur = conn.cursor()

    # Before the fix: the live insert blows up exactly as the issue reports.
    with pytest.raises(sqlite3.OperationalError, match="no column named year"):
        cur.execute(_FULL_REFRESH_INSERT, _ROW)

    # Apply the repair backstop, then the same insert must succeed and persist year.
    db = MusicDatabase(str(tmp_path / "scratch.db"))
    db._ensure_core_media_schema_columns(cur)
    cur.execute(_FULL_REFRESH_INSERT, _ROW)
    conn.commit()
    cur.execute("SELECT year FROM tracks WHERE id = 't1::soulsync'")
    assert cur.fetchone()[0] == 2009
