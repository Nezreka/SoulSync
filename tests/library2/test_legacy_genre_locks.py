"""3.6.1's genre-cleanup locks survive the upgrade.

found in the second #1062 review: 3.6.1 set genres_locked on what its genre
cleanup fixed, so a later scan kept the clean genres. the Library v2 import
carried art_locked but not this one, and lib2 writers honour the lock only
when it is 1: the first scan after the upgrade put the removed genres back.
"""

from __future__ import annotations

from core.library2.legacy_locks import carry_legacy_genre_locks


def _lock(conn, table, legacy_id):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "genres_locked" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN genres_locked INTEGER DEFAULT 0")
    conn.execute(f"UPDATE {table} SET genres_locked=1 WHERE id=?", (legacy_id,))


def _locked(conn, table, back_ref):
    return {r[0] for r in conn.execute(
        f"SELECT {back_ref} FROM {table} WHERE genres_locked=1")}


def test_a_locked_album_and_artist_keep_their_lock(imported_conn):
    conn = imported_conn
    album = conn.execute("SELECT id FROM albums ORDER BY id LIMIT 1").fetchone()[0]
    artist = conn.execute("SELECT id FROM artists ORDER BY id LIMIT 1").fetchone()[0]
    _lock(conn, "albums", album)
    _lock(conn, "artists", artist)
    conn.commit()

    stats = carry_legacy_genre_locks(conn)

    assert stats == {"artists": 1, "albums": 1}
    assert _locked(conn, "lib2_albums", "legacy_album_id") == {album}
    assert _locked(conn, "lib2_artists", "legacy_artist_id") == {artist}


def test_it_reads_the_retired_legacy_tables_too_and_runs_once(imported_conn):
    """an install that already upgraded has legacy_albums, not albums"""
    conn = imported_conn
    album = conn.execute("SELECT id FROM albums ORDER BY id LIMIT 1").fetchone()[0]
    _lock(conn, "albums", album)
    conn.execute("ALTER TABLE albums RENAME TO legacy_albums")
    conn.commit()

    assert carry_legacy_genre_locks(conn)["albums"] == 1
    conn.execute("UPDATE lib2_albums SET genres_locked=0")
    conn.commit()
    assert carry_legacy_genre_locks(conn) == {"artists": 0, "albums": 0}  # marker: once
    assert _locked(conn, "lib2_albums", "legacy_album_id") == set()


def test_no_lock_column_is_a_quiet_no_op(imported_conn):
    assert carry_legacy_genre_locks(imported_conn) == {"artists": 0, "albums": 0}


def test_the_deferred_convergence_runs_it():
    import inspect
    from core.library2 import bootstrap
    assert "carry_legacy_genre_locks(conn)" in inspect.getsource(bootstrap.run_deferred_backfills)
