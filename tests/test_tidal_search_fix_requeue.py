"""#1290: every tidal search 400'd and got recorded as not_found.

the one-time requeue puts those rows back for the worker instead of leaving
them in the 30-day retry, and must never touch a real match.
"""

from pathlib import Path

from database.music_database import MusicDatabase


def _seed(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name, tidal_match_status, tidal_last_attempted) "
                "VALUES ('a1', 'Crazy Lixx', 'not_found', '2026-09-20')")
    cur.execute("INSERT INTO artists (id, name, tidal_id, tidal_match_status, tidal_last_attempted) "
                "VALUES ('a2', 'Scorpions', '123', 'matched', '2026-09-20')")
    cur.execute("INSERT INTO albums (id, artist_id, title, tidal_match_status, tidal_last_attempted) "
                "VALUES ('b1', 'a1', 'Street Lethal', 'not_found', '2026-09-20')")
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, tidal_match_status, tidal_last_attempted) "
                "VALUES ('t1', 'b1', 'a1', 'Hunt For Danger', 'not_found', '2026-09-20')")
    cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, tidal_match_status) "
                "VALUES ('t2', 'b1', 'a1', 'Other', 'error')")
    conn.commit()


def _status(conn, table, row_id):
    return conn.execute(
        f"SELECT tidal_match_status, tidal_last_attempted FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()


def _rerun(db, conn):
    conn.execute("DROP TABLE IF EXISTS _tidal_search_fix_applied")
    db._add_tidal_qobuz_enrichment_columns(conn.cursor())
    conn.commit()


def test_not_found_rows_go_back_to_the_worker(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)

        assert tuple(_status(conn, 'artists', 'a1')) == (None, None)
        assert tuple(_status(conn, 'albums', 'b1')) == (None, None)
        assert tuple(_status(conn, 'tracks', 't1')) == (None, None)


def test_a_real_match_and_an_error_are_left_alone(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)

        assert tuple(_status(conn, 'artists', 'a2')) == ('matched', '2026-09-20')
        assert conn.execute("SELECT tidal_id FROM artists WHERE id = 'a2'").fetchone()[0] == '123'
        assert _status(conn, 'tracks', 't2')[0] == 'error'


def test_it_runs_once(tmp_path: Path):
    """a not_found recorded after the fix is a real miss; the next start keeps it."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)
        conn.execute("UPDATE artists SET tidal_match_status = 'not_found' WHERE id = 'a1'")
        conn.commit()

        db._add_tidal_qobuz_enrichment_columns(conn.cursor())
        conn.commit()

        assert _status(conn, 'artists', 'a1')[0] == 'not_found'
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert '_tidal_search_fix_applied' in names
