"""Preserve durable user references when legacy and native IDs overlap."""

from typing import Any


def _columns(conn: Any, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def ensure_reference_id_kinds(conn: Any) -> None:
    # A completed native installation already writes native IDs. An upstream
    # upgrade (including a partly imported one) still has legacy references.
    done = False
    if _columns(conn, 'lib2_bootstrap_state'):
        row = conn.execute('SELECT status FROM lib2_bootstrap_state WHERE id=1').fetchone()
        done = bool(row and row[0] == 'done')
    kind = 'untyped' if done else 'legacy'
    for table, column in (('manual_library_track_matches', 'library_track_id_kind'),
                          ('sample_stash', 'track_id_kind')):
        columns = _columns(conn, table)
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{kind}'")


def _native_id(conn: Any, legacy_id: Any, path: str | None = None) -> int | None:
    if path:
        ids = {int(row[0]) for row in conn.execute(
            'SELECT track_id FROM lib2_track_files WHERE path=? AND track_id IS NOT NULL', (path,))}
        if len(ids) == 1:
            return ids.pop()
    ids = {int(row[0]) for row in conn.execute(
        'SELECT id FROM lib2_tracks WHERE legacy_track_id=? UNION '
        'SELECT track_id FROM lib2_track_files WHERE legacy_track_id=? AND track_id IS NOT NULL',
        (legacy_id, legacy_id))}
    return ids.pop() if len(ids) == 1 else None


def migrate_legacy_track_references(conn: Any) -> None:
    """Remap once per row; keep unresolved legacy references explicitly typed.

    The caller commits this alongside the import checkpoint, so retrying cannot
    translate an already translated ID for a second time.
    """
    ensure_reference_id_kinds(conn)
    if _columns(conn, 'manual_library_track_matches'):
        rows = conn.execute("SELECT * FROM manual_library_track_matches WHERE library_track_id_kind IN ('legacy','untyped')").fetchall()
        for row in rows:
            row = dict(row)
            if row['library_track_id_kind'] == 'untyped':
                paths = {int(r[0]) for r in conn.execute(
                    'SELECT track_id FROM lib2_track_files WHERE path=? AND track_id IS NOT NULL',
                    (row.get('library_file_path'),))}
                track_id = paths.pop() if len(paths) == 1 else None
            else:
                track_id = _native_id(conn, row['library_track_id'], row.get('library_file_path'))
            if track_id is not None:
                conn.execute("UPDATE manual_library_track_matches SET library_track_id=?, library_track_id_kind='lib2' WHERE id=?",
                             (track_id, row['id']))
    if 'track_id' in _columns(conn, 'sample_stash'):
        for row in conn.execute("SELECT id,track_id FROM sample_stash WHERE track_id_kind='legacy'").fetchall():
            track_id = _native_id(conn, row[1])
            if track_id is not None:
                conn.execute("UPDATE sample_stash SET track_id=?, track_id_kind='lib2' WHERE id=?", (track_id, row[0]))
    # Analysis and stems are derived caches. Their IDs and on-disk cache names
    # were legacy IDs; invalidate the rows, preserving saved chops and recipes.
    conn.execute('CREATE TABLE IF NOT EXISTS lib2_user_reference_migrations(name TEXT PRIMARY KEY)')
    inserted = conn.execute("INSERT OR IGNORE INTO lib2_user_reference_migrations VALUES('sample_track_ids_v1')").rowcount
    if inserted:
        for table in ('sample_analysis', 'sample_stems'):
            if _columns(conn, table):
                conn.execute(f'DELETE FROM {table}')
