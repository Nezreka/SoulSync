"""LOWER(title) = ? lookups search an expression index instead of reading
every track.

discover's playable check, curated playlists, exports and the history
importers all ask `LOWER(t.title) = LOWER(?)` or `LOWER(t.title) IN (...)`.
LOWER() on the column skips idx_tracks_title, so each call read all 300k
tracks (330 ms). an index on the same expression answers it in 0.1 ms and
can't change what matches.
"""

import sqlite3

import pytest

from core.discovery.playable import resolve_playable_tracks
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    from tests import lib2_seed
    lib2_seed.track(c, 'Oasis', 'Definitely Maybe', 'Live Forever', path='/m/oasis/live forever.flac')
    c.commit()
    c.close()
    return d


def _plan(db, sql, params=()):
    c = sqlite3.connect(str(db.database_path))
    try:
        return [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, params)]
    finally:
        c.close()


def test_init_creates_the_lower_indexes(db):
    c = sqlite3.connect(str(db.database_path))
    sqls = {name: sql for name, sql in c.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index'")}
    c.close()
    for name, table, column in (
        ('idx_lib2_tracks_title_lower', 'lib2_tracks', 'title'),
        ('idx_lib2_albums_title_lower', 'lib2_albums', 'title'),
        ('idx_lib2_artists_name_lower', 'lib2_artists', 'name')):
        assert name in sqls, name
        assert f"ON {table}(LOWER({column}))" in sqls[name]


@pytest.mark.parametrize("sql, index", [
    ("SELECT * FROM lib2_tracks WHERE LOWER(title) = LOWER(?) LIMIT 1", "idx_lib2_tracks_title_lower"),
    ("SELECT t.id FROM lib2_tracks t WHERE LOWER(t.title) IN (?, ?)", "idx_lib2_tracks_title_lower"),
    ("SELECT id FROM lib2_albums al WHERE LOWER(al.title) = LOWER(?)", "idx_lib2_albums_title_lower"),
    ("SELECT id FROM lib2_artists WHERE LOWER(name) = LOWER(?)", "idx_lib2_artists_name_lower"),
])
def test_lower_lookups_search_the_index(db, sql, index):
    params = tuple("x" for _ in range(sql.count("?")))
    plan = _plan(db, sql, params)
    assert any(index in step and "SEARCH" in step for step in plan), plan


def test_playable_resolver_searches_titles_instead_of_scanning(db, monkeypatch):
    """the call site, not just the shape: capture the resolver's own query."""
    plans = []

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if "LOWER(t.title) IN" in sql:
                plans.append([r[3] for r in super().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()])
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    def conn(self):
        c = sqlite3.connect(str(self.database_path), factory=Conn)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(MusicDatabase, "_get_connection", conn)
    out = resolve_playable_tracks(db, [{"title": "LIVE FOREVER", "artist": "oasis"}])
    assert out["matched"] == 1
    assert plans, "the resolver's title query never ran"
    for plan in plans:
        assert any("USING INDEX idx_lib2_tracks_title_lower" in s for s in plan), plan
        assert not any(s.startswith("SCAN t") for s in plan), plan


# ---------------------------------------------------------------------------
# tracks never had an index on file_path. imports, verification, the
# missing-track import and the repair jobs look tracks up by exact path,
# often once per track, and each lookup read the whole table (110-170 ms
# warm, seconds cold).
# ---------------------------------------------------------------------------

def test_init_creates_the_file_path_index(db):
    c = sqlite3.connect(str(db.database_path))
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name = 'idx_lib2_track_files_path'").fetchone()
    c.close()
    assert sql and "ON lib2_track_files(path)" in sql[0]


@pytest.mark.parametrize("sql", [
    "SELECT id FROM lib2_track_files WHERE path = ?",                     # imports, repair
    "SELECT 1 FROM lib2_track_files WHERE path = ? LIMIT 1",              # rematch hints
    "UPDATE lib2_track_files SET path = ? WHERE path = ?",           # atomic recovery, repair moves
    "UPDATE lib2_track_files SET verification_status = ? WHERE path = ?",  # verification
    "DELETE FROM lib2_track_files WHERE path = ?",
])
def test_exact_path_lookups_search_the_index(db, sql):
    params = tuple("x" for _ in range(sql.count("?")))
    plan = _plan(db, sql, params)
    assert any("idx_lib2_track_files_path" in step for step in plan), plan
    assert not any(step == "SCAN lib2_track_files" for step in plan), plan
