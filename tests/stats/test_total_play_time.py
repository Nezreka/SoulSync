"""the overview's total listening time looks each played track up once.

play_duration_sql falls back to the library track's length per play, so the
all-time overview ran 150k track lookups for 11k distinct tracks. the total
now counts fallback plays per track first. it has to be the same number in
every case, so this checks it against the per-play form row shape by row
shape.
"""

import inspect
import sqlite3

import pytest

from core.listening_scope import play_duration_sql, total_play_ms_sql
from database.music_database import MusicDatabase


@pytest.fixture()
def conn(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name) VALUES ('1', 'A')")
    c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('10', '1', 'B')")
    for tid, dur in (('100', 200_000), ('101', None), ('102', 0), ('103', 90_000)):
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, duration) VALUES (?, '10', '1', 't', ?)",
                  (tid, dur))
    c.commit()
    yield c
    c.close()


# (duration_ms, db_track_id): every shape a play row comes in
SHAPES = [
    (180_000, 100),     # own duration wins over the track's
    (0, 100),           # no duration: the track's length
    (None, 100),        # null duration: the track's length too
    (-5, 100),          # garbage duration: falls back like 0
    (0, 101),           # track has no length: 0
    (0, 102),           # track length 0: 0
    (0, 999),           # linked to a track that's gone: 0
    (0, None),          # never linked: 0
    (None, None),
    (120_000, None),    # own duration, unlinked
    ('5000', 103),      # text duration, sqlite's affinity rules apply alike
    (0, '103'),         # id stored as text
]


@pytest.mark.parametrize("shapes", [SHAPES, SHAPES * 3, SHAPES[:1], SHAPES[1:2], []])
def test_matches_the_per_play_sum(conn, shapes):
    conn.execute("DELETE FROM listening_history")
    for i, (dur, tid) in enumerate(shapes):
        conn.execute(
            "INSERT INTO listening_history (title, artist, played_at, duration_ms, db_track_id, profile_id) "
            "VALUES ('t', 'a', datetime('now', ?), ?, ?, 1)", (f"-{i} minutes", dur, tid))
    conn.commit()
    per_play = conn.execute(
        f"SELECT COALESCE(SUM({play_duration_sql()}), 0) FROM listening_history").fetchone()[0]
    grouped = conn.execute(
        f"WITH r AS MATERIALIZED (SELECT duration_ms, db_track_id FROM listening_history) "
        f"SELECT {total_play_ms_sql('r')} FROM r").fetchone()[0]
    assert grouped == per_play


def test_overview_uses_it_over_one_read_of_the_plays():
    src = inspect.getsource(MusicDatabase._listening_overview)
    assert "{total_play_ms_sql('plays')} as total_time_ms" in src
    assert "WITH plays AS MATERIALIZED" in src


def test_overview_looks_tracks_up_once_per_track_not_per_play(conn, tmp_path, monkeypatch):
    plans = []

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, params=()):
            if "total_plays" in sql:
                plans.append([r[3] for r in super().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()])
            return super().execute(sql, params)

    class Conn(sqlite3.Connection):
        def cursor(self, *a, **k):
            return super().cursor(Cursor)

    db = MusicDatabase(database_path=str(tmp_path / "music.db"))

    def connect(self):
        c = sqlite3.connect(str(self.database_path), factory=Conn)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(MusicDatabase, "_get_connection", connect)
    db.get_listening_stats('all')
    assert plans, "the overview query never ran"
    for plan in plans:
        assert not any("CORRELATED" in step for step in plan), plan
        assert any(step.startswith("MATERIALIZE plays") for step in plan), plan
