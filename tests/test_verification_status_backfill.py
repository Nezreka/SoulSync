"""#845: the migration backfills verification_status for library_history rows
written before the column existed, from the acoustid_result they recorded. Must
map correctly, never overwrite an existing status, leave no-acoustid rows alone,
and be idempotent (it runs on every fresh DB init).

The backfill runs inside _initialize_database, which is guarded to run once per
path per process — so the tests clear that guard to re-trigger it on the seeded
rows (exercising the real production code path, not a copy of the SQL)."""

from __future__ import annotations

import database.music_database as mdb
from database.music_database import MusicDatabase


def _reinit(path):
    """Force _initialize_database (incl. the backfill) to run again on `path`."""
    mdb._database_initialized_paths.clear()
    return MusicDatabase(path)


def _statuses(db):
    with db._get_connection() as conn:
        return {r['title']: r['verification_status']
                for r in conn.execute("SELECT title, verification_status FROM library_history")}


def test_backfill_maps_and_preserves(tmp_path):
    p = str(tmp_path / "m.db")
    db = MusicDatabase(p)
    db.add_library_history_entry('import', 'A', acoustid_result='pass')
    db.add_library_history_entry('import', 'B', acoustid_result='skip')
    db.add_library_history_entry('import', 'C', acoustid_result='fail')
    db.add_library_history_entry('import', 'D', acoustid_result='pass',
                                 verification_status='human_verified')  # pre-set, must NOT change
    db.add_library_history_entry('import', 'E')                         # no acoustid → stays NULL

    _reinit(p)   # run the backfill migration over the seeded rows

    s = _statuses(db)
    assert s['A'] == 'verified'
    assert s['B'] == 'unverified'
    assert s['C'] == 'force_imported'
    assert s['D'] == 'human_verified'   # existing status preserved (NULL-only)
    assert s['E'] is None               # no acoustid_result → untouched


def test_backfill_is_idempotent(tmp_path):
    p = str(tmp_path / "m2.db")
    db = MusicDatabase(p)
    db.add_library_history_entry('import', 'A', acoustid_result='pass')
    _reinit(p); _reinit(p)              # run the migration two more times
    assert _statuses(db)['A'] == 'verified'
