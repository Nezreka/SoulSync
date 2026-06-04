"""Migration test for the canonical-album-version columns (#765 Stage 1).

Additive + nullable, so it must: appear on a fresh DB, be idempotent (re-running
the migration is a no-op, not an error), and ALTER them onto an older albums
table that lacks them. NULL = unresolved = tools fall back to today's behavior.
"""

from __future__ import annotations

import sqlite3

from database.music_database import MusicDatabase

_CANONICAL_COLS = {
    'canonical_source', 'canonical_album_id', 'canonical_score', 'canonical_resolved_at',
}


def _album_cols(cur):
    cur.execute("PRAGMA table_info(albums)")
    return {c[1] for c in cur.fetchall()}


def test_fresh_db_has_canonical_columns(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    assert _CANONICAL_COLS <= _album_cols(cur)


def test_canonical_columns_default_null(tmp_path):
    # Unresolved by default -> every consumer falls back. Verify each canonical
    # column declares DEFAULT NULL and is nullable (notnull flag == 0).
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    cur.execute("PRAGMA table_info(albums)")
    info = {c[1]: c for c in cur.fetchall()}  # name -> (cid, name, type, notnull, dflt, pk)
    for col in _CANONICAL_COLS:
        assert col in info, f"{col} missing"
        assert info[col][3] == 0, f"{col} must be nullable"
        dflt = info[col][4]
        assert dflt is None or str(dflt).upper() == 'NULL', f"{col} default should be NULL"


def test_migration_is_idempotent(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cur = db._get_connection().cursor()
    before = _album_cols(cur)
    # Re-running must not raise (the PRAGMA guard skips existing columns).
    db._ensure_core_media_schema_columns(cur)
    db._ensure_core_media_schema_columns(cur)
    assert _album_cols(cur) == before
    assert _CANONICAL_COLS <= _album_cols(cur)


def test_migration_adds_columns_to_old_albums_table(tmp_path):
    # Simulate an upgraded DB whose albums table predates these columns.
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT)")
    conn.commit()
    cur = conn.cursor()
    assert not (_CANONICAL_COLS & _album_cols(cur))  # none present yet

    # Run the real migration against this old cursor.
    db = MusicDatabase(str(tmp_path / "scratch.db"))
    db._ensure_core_media_schema_columns(cur)
    conn.commit()

    assert _CANONICAL_COLS <= _album_cols(cur)
