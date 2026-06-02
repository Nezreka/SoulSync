"""Track Number Repair canonical lookup (#765 Stage 4, read side)."""

from __future__ import annotations

import types

from core.repair_jobs.track_number_repair import _lookup_canonical_from_db
from database.music_database import MusicDatabase


def _ctx(db):
    return types.SimpleNamespace(db=db)


def _seed(db, *, with_canonical: bool, file_path: str = "/music/Evolve/01 - Believer.flac"):
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('art1', 'Imagine Dragons')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES ('alb1', 'Evolve', 'art1')")
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path) "
        "VALUES ('t1', 'alb1', 'art1', 'Believer', 1, 204000, ?)",
        (file_path,),
    )
    conn.commit()
    conn.close()
    if with_canonical:
        db.set_album_canonical("alb1", "spotify", "sp_evolve", 0.96)


def test_returns_canonical_when_pinned(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    fp = "/music/Evolve/01 - Believer.flac"
    _seed(db, with_canonical=True, file_path=fp)
    assert _lookup_canonical_from_db([(fp, "01 - Believer.flac", 1)], _ctx(db)) == ("spotify", "sp_evolve")


def test_none_when_unresolved(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    fp = "/music/Evolve/01 - Believer.flac"
    _seed(db, with_canonical=False, file_path=fp)
    assert _lookup_canonical_from_db([(fp, "01 - Believer.flac", 1)], _ctx(db)) is None


def test_none_when_file_not_tracked(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db, with_canonical=True)
    assert _lookup_canonical_from_db([("/some/other/path.flac", "x.flac", 1)], _ctx(db)) is None


def test_none_when_no_db():
    assert _lookup_canonical_from_db([("/p.flac", "p.flac", 1)], types.SimpleNamespace(db=None)) is None
