"""Atomic album publish moves the files — the catalogue has to hear about it.

#999 stages a whole album in a private mirror so the media server never sees a
partial release, then moves it into the live library at the end. The tracks were
already imported *from staging*, so ``lib2_track_files`` holds the staging path;
publishing rewrote only the legacy ``tracks`` row. The Library-v2 catalogue was
then pointing at a path inside a tree the very next step deletes — and
``path_drift_reconcile`` looks a file up by exactly that stored path, so it
cannot repair it either.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.library2.track_files import repoint_file_path


def _conn(tmp_path: Path, stored: str, *, legacy_schema=False) -> sqlite3.Connection:
    from core.library2.schema import ensure_library_v2_schema

    if legacy_schema:
        from database.music_database import MusicDatabase

        MusicDatabase(str(tmp_path / 'm.db'))
    conn = sqlite3.connect(str(tmp_path / 'm.db'))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'A', 'A')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Alb')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (1, 1, 'T')")
    conn.execute(
        "INSERT INTO lib2_track_files (id, track_id, path, format, is_primary) "
        "VALUES (5, 1, ?, 'flac', 1)", (stored,))
    conn.commit()
    return conn


def _stored(conn) -> str:
    return conn.execute("SELECT path FROM lib2_track_files WHERE id=5").fetchone()[0]


def test_the_stored_path_follows_the_file(tmp_path: Path):
    conn = _conn(tmp_path, '/staging/b1/A/Alb/01.flac')

    assert repoint_file_path(conn, '/staging/b1/A/Alb/01.flac', '/music/A/Alb/01.flac') == 1

    assert _stored(conn) == '/music/A/Alb/01.flac'


def test_a_path_the_catalogue_does_not_hold_changes_nothing(tmp_path: Path):
    conn = _conn(tmp_path, '/music/A/Alb/01.flac')

    assert repoint_file_path(conn, '/staging/other.flac', '/music/other.flac') == 0

    assert _stored(conn) == '/music/A/Alb/01.flac'


def test_publishing_a_batch_repoints_the_catalogue(tmp_path: Path, monkeypatch):
    """End to end through ``_publish_atomic_album``'s own db callback."""
    from core.downloads import lifecycle

    staging = tmp_path / 'staging' / 'b1' / 'A' / 'Alb'
    staging.mkdir(parents=True)
    staged_file = staging / '01.flac'
    staged_file.write_bytes(b'audio')
    transfer = tmp_path / 'music'
    transfer.mkdir()

    conn = _conn(tmp_path, str(staged_file), legacy_schema=True)

    class _Keep:
        """The callback closes what it opens; keep this one open to read after."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def close(self):
            pass

    class _DB:
        def _get_connection(self):
            return _Keep(conn)

    monkeypatch.setattr('database.music_database.MusicDatabase', lambda *a, **k: _DB())

    lifecycle._publish_atomic_album('b1', {
        '_atomic_active': True,
        '_atomic_staging_root': str(tmp_path / 'staging' / 'b1'),
        '_atomic_transfer_dir': str(transfer),
    })

    assert _stored(conn) == str(transfer / 'A' / 'Alb' / '01.flac')
