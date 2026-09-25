"""Track Number Repair canonical lookup (#765 Stage 4, read side).

lib2 records the pin as the album's DEFAULT release edition, not a
``canonical_source``/``canonical_album_id`` column pair on ``albums`` — the same
idea one level down, release group as the album and edition as the concrete
release the files were matched to. These tests used to seed the legacy pair via
``set_album_canonical``, so they described a shape the lookup no longer reads.
"""

from __future__ import annotations

import types

from core.repair_jobs.track_number_repair import _lookup_canonical_from_db
from database.music_database import MusicDatabase


def _ctx(db):
    return types.SimpleNamespace(db=db)


def _seed(db, *, with_canonical: bool, file_path: str = "/music/Evolve/01 - Believer.flac"):
    from core.library2.schema import ensure_library_v2_schema

    conn = db._get_connection()
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'Imagine Dragons', 'Imagine Dragons')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Evolve')")
    conn.execute(
        "INSERT INTO lib2_tracks (id, album_id, title, track_number, duration) "
        "VALUES (1, 1, 'Believer', 1, 204000)")
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (1, ?, 'flac', 1)", (file_path,))
    if with_canonical:
        conn.execute(
            "INSERT INTO lib2_release_editions (release_group_id, title, spotify_id, is_default) "
            "VALUES (1, 'Evolve', 'sp_evolve', 1)")
    conn.commit()
    conn.close()


def test_returns_canonical_when_pinned(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    fp = "/music/Evolve/01 - Believer.flac"
    _seed(db, with_canonical=True, file_path=fp)
    assert _lookup_canonical_from_db([(fp, "01 - Believer.flac", 1)], _ctx(db)) == ("spotify", "sp_evolve")


def test_none_when_unresolved(tmp_path):
    """The album is known but nothing is pinned — no edition to prefer."""
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
