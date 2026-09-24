"""#1290: every tidal search 400'd and got recorded as not_found.

the one-time requeue hands those misses back to the worker instead of leaving
them in the 30-day retry, and must never touch a real match. On Library v2 a
provider miss lives in ``lib2_provider_attempts``.
"""

from pathlib import Path

from database.music_database import MusicDatabase


def _seed(conn):
    conn.executemany(
        "INSERT INTO lib2_provider_attempts (entity_type, entity_id, service, status) "
        "VALUES (?, ?, ?, ?)",
        [('artist', 1, 'tidal', 'not_found'),
         ('artist', 2, 'tidal', 'matched'),
         ('album', 1, 'tidal', 'not_found'),
         ('track', 1, 'tidal', 'not_found'),
         ('track', 2, 'tidal', 'error'),
         ('track', 3, 'deezer', 'not_found')])
    conn.commit()


def _statuses(conn):
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT entity_type, entity_id, service, status FROM lib2_provider_attempts")}


def _rerun(db, conn):
    conn.execute("DROP TABLE IF EXISTS _tidal_search_1290_requeued")
    db._requeue_tidal_search_misses(conn.cursor())
    conn.commit()


def test_not_found_rows_go_back_to_the_worker(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)
        left = _statuses(conn)
        assert ('artist', 1, 'tidal') not in left
        assert ('album', 1, 'tidal') not in left
        assert ('track', 1, 'tidal') not in left


def test_a_real_match_an_error_and_other_services_are_left_alone(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)
        left = _statuses(conn)
        assert left[('artist', 2, 'tidal')] == 'matched'
        assert left[('track', 2, 'tidal')] == 'error'
        assert left[('track', 3, 'deezer')] == 'not_found'


def test_it_runs_once(tmp_path: Path):
    """a not_found recorded after the fix is a real miss; the next start keeps it."""
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        _seed(conn)
        _rerun(db, conn)
        conn.execute("INSERT INTO lib2_provider_attempts (entity_type, entity_id, service, status) "
                     "VALUES ('artist', 9, 'tidal', 'not_found')")
        conn.commit()

        db._requeue_tidal_search_misses(conn.cursor())
        conn.commit()

        assert _statuses(conn)[('artist', 9, 'tidal')] == 'not_found'
