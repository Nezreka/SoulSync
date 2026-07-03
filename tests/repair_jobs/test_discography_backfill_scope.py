"""Regression for #977 — the discography backfill only backfills artists you
actually OWN music by. The `artists` table also carries bare rows for featured/
guest and re-identified artists; without an ownership filter the backfill pulled
their entire discography and wishlisted it ("artists which are not in my library").
"""
import types

from database.music_database import MusicDatabase
from core.repair_jobs.discography_backfill import DiscographyBackfillJob


def _seed(db):
    with db._get_connection() as conn:
        # Owned artist — has an owned track (+ album).
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Owned Artist', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (1, 'Alb', 1, 'test')")
        conn.execute("INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                     "VALUES (1, 'T', '/music/Owned/a.flac', 1, 1, 'test')")
        # Artist known via an owned album but no individual track rows — still in library.
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (2, 'Album Only Artist', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (2, 'Alb2', 2, 'test')")
        # Orphan feat/guest/re-identified row — bare (id, name), no owned track, no album.
        conn.execute("INSERT INTO artists (id, name) VALUES (3, 'Feat Guest')")
        conn.commit()


def test_backfill_scope_excludes_non_library_artists(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db)
    job = DiscographyBackfillJob()
    names = {a['name'] for a in job._get_library_artists(types.SimpleNamespace(db=db))}

    assert 'Owned Artist' in names          # owned track → in library
    assert 'Album Only Artist' in names     # owned album → in library
    assert 'Feat Guest' not in names        # bare orphan row → NOT backfilled (the #977 fix)
