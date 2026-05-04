"""Tests for the Library Disk Usage stat.

Discord request (Samuel [KC]): show how much disk space the library
takes on the System Statistics page. Implementation piggybacks on the
existing deep scan — Plex/Jellyfin/Navidrome all return file size in
their track API responses, so we read it during the deep scan and
aggregate via SQL on demand. No filesystem walk involved.

Tests pin:
- Schema migration is idempotent and backward-compatible (existing
  rows get NULL file_size; new column doesn't break old inserts).
- Aggregator returns the empty-shape dict for fresh installs and
  walks/sums correctly when populated.
- Per-format breakdown handles mixed extensions correctly.
- Defensive: empty / NULL / malformed paths don't crash.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path: Path) -> MusicDatabase:
    """Build a fresh isolated MusicDatabase against a temp file."""
    db_path = tmp_path / 'test_library_size.db'
    return MusicDatabase(database_path=str(db_path))


def _insert_track(db: MusicDatabase, *, track_id: str, file_path: str,
                  file_size, album_id: str = 'a1', artist_id: str = 'ar1') -> None:
    """Helper: seed an artist+album+track row with the given size."""
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)",
                (artist_id, 'Test Artist'))
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES (?, ?, ?)",
                (album_id, artist_id, 'Test Album'))
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path, file_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (track_id, album_id, artist_id, f'track-{track_id}', file_path, file_size),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_file_size_column_exists_after_init(db: MusicDatabase) -> None:
    """Fresh install should have the column from the canonical
    CREATE TABLE."""
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tracks)")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    assert 'file_size' in cols


def test_existing_tracks_have_null_file_size_after_migration(db: MusicDatabase) -> None:
    """Backward-compat: rows inserted via the OLD schema (no file_size)
    must still be readable, and querying file_size returns NULL — not
    an error. Simulated by inserting a track without specifying
    file_size (relies on column default = NULL)."""
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'A')")
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('a1', 'ar1', 'Al')")
    # Note: NOT specifying file_size — should default to NULL
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
        "VALUES ('legacy_t', 'a1', 'ar1', 'L', '/x/legacy.flac')"
    )
    conn.commit()
    cur.execute("SELECT file_size FROM tracks WHERE id = 'legacy_t'")
    row = cur.fetchone()
    conn.close()
    # Could be sqlite3.Row or tuple; both index by 0
    assert row[0] is None


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def test_aggregator_returns_empty_shape_for_fresh_install(db: MusicDatabase) -> None:
    """No tracks inserted → has_data=False, total=0, no formats."""
    result = db.get_library_disk_usage()
    assert result == {
        'total_bytes': 0,
        'tracks_with_size': 0,
        'tracks_without_size': 0,
        'by_format': {},
        'has_data': False,
    }


def test_aggregator_sums_known_sizes(db: MusicDatabase) -> None:
    _insert_track(db, track_id='t1', file_path='/x/song1.flac', file_size=10_000_000)
    _insert_track(db, track_id='t2', file_path='/x/song2.flac', file_size=5_000_000)
    _insert_track(db, track_id='t3', file_path='/x/song3.mp3', file_size=3_000_000)

    result = db.get_library_disk_usage()
    assert result['total_bytes'] == 18_000_000
    assert result['tracks_with_size'] == 3
    assert result['tracks_without_size'] == 0
    assert result['has_data'] is True


def test_aggregator_excludes_null_sizes_from_sum(db: MusicDatabase) -> None:
    """Tracks without size are counted but don't contribute to total_bytes."""
    _insert_track(db, track_id='t1', file_path='/x/sized.flac', file_size=10_000_000)
    _insert_track(db, track_id='t2', file_path='/x/null.flac', file_size=None)

    result = db.get_library_disk_usage()
    assert result['total_bytes'] == 10_000_000
    assert result['tracks_with_size'] == 1
    assert result['tracks_without_size'] == 1
    # Has data — at least one track was measured
    assert result['has_data'] is True


def test_aggregator_per_format_breakdown(db: MusicDatabase) -> None:
    _insert_track(db, track_id='t1', file_path='/x/song.flac', file_size=10_000_000)
    _insert_track(db, track_id='t2', file_path='/x/other.flac', file_size=5_000_000)
    _insert_track(db, track_id='t3', file_path='/x/song.mp3', file_size=3_000_000)
    _insert_track(db, track_id='t4', file_path='/x/song.m4a', file_size=2_000_000)

    result = db.get_library_disk_usage()
    assert result['by_format'] == {
        'flac': 15_000_000,
        'mp3': 3_000_000,
        'm4a': 2_000_000,
    }


def test_aggregator_handles_mixed_case_extensions(db: MusicDatabase) -> None:
    """Extensions get lowercased so .FLAC and .flac group together."""
    _insert_track(db, track_id='t1', file_path='/x/song.FLAC', file_size=5_000_000)
    _insert_track(db, track_id='t2', file_path='/x/other.flac', file_size=5_000_000)

    result = db.get_library_disk_usage()
    assert result['by_format'] == {'flac': 10_000_000}


def test_aggregator_handles_paths_with_dots_in_album_name(db: MusicDatabase) -> None:
    """Albums like 'M.A.A.D City' have dots in the path. Extension
    extraction must use the LAST dot, not the first."""
    _insert_track(
        db, track_id='t1',
        file_path='/music/Kendrick Lamar/M.A.A.D City/01 - track.flac',
        file_size=10_000_000,
    )
    result = db.get_library_disk_usage()
    assert result['by_format'] == {'flac': 10_000_000}


def test_aggregator_skips_paths_without_extension(db: MusicDatabase) -> None:
    """Defensive: files without an extension don't show up in
    by_format (would otherwise produce an empty-string key or junk)."""
    _insert_track(db, track_id='t1', file_path='/x/no_extension', file_size=5_000_000)
    _insert_track(db, track_id='t2', file_path='/x/song.flac', file_size=10_000_000)

    result = db.get_library_disk_usage()
    assert result['total_bytes'] == 15_000_000
    assert result['by_format'] == {'flac': 10_000_000}
    assert '' not in result['by_format']


def test_aggregator_skips_empty_file_path(db: MusicDatabase) -> None:
    """Empty string file_path → shouldn't appear in by_format."""
    _insert_track(db, track_id='t1', file_path='', file_size=5_000_000)
    _insert_track(db, track_id='t2', file_path='/x/song.flac', file_size=10_000_000)

    result = db.get_library_disk_usage()
    # Total still includes the empty-path track (it was measured)
    assert result['total_bytes'] == 15_000_000
    # But by_format only has the one with a real extension
    assert result['by_format'] == {'flac': 10_000_000}


def test_aggregator_skips_implausibly_long_extension(db: MusicDatabase) -> None:
    """Extensions over 6 chars are filtered (would be junk from an
    unusual filename like 'song.somethingweird')."""
    _insert_track(db, track_id='t1', file_path='/x/song.somethingweird', file_size=5_000_000)
    _insert_track(db, track_id='t2', file_path='/x/song.flac', file_size=10_000_000)

    result = db.get_library_disk_usage()
    assert result['by_format'] == {'flac': 10_000_000}


# ---------------------------------------------------------------------------
# Backward compatibility — schema column ordering / NULL writes
# ---------------------------------------------------------------------------


def test_insert_or_update_media_track_persists_size_for_object_with_file_size(db: MusicDatabase) -> None:
    """The Jellyfin/Navidrome/SoulSync track wrappers expose
    `track_obj.file_size`. Verify insert_or_update_media_track reads
    it and persists to the new column."""

    class _FakeTrack:
        def __init__(self):
            self.ratingKey = 'fake_track_id_1'
            self.title = 'Test Track'
            self.trackNumber = 1
            self.duration = 200000
            self.path = '/library/Artist/Album/01 - track.flac'
            self.bitRate = 1411
            self.file_size = 42_000_000

    # Seed parent rows so FK constraints are satisfied
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar2', 'Artist')")
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('al2', 'ar2', 'Album')")
    conn.commit()
    conn.close()

    db.insert_or_update_media_track(_FakeTrack(), album_id='al2', artist_id='ar2',
                                    server_source='jellyfin')

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT file_size FROM tracks WHERE id = 'fake_track_id_1'")
    row = cur.fetchone()
    conn.close()
    assert row[0] == 42_000_000


def test_insert_or_update_media_track_preserves_size_on_null_re_sync(db: MusicDatabase) -> None:
    """If a subsequent deep scan returns no file_size for a track that
    previously had one (e.g. server hiccup, rare Jellyfin response),
    the COALESCE on UPDATE preserves the existing value rather than
    blanking it. Pin the regression — losing data on every scan would
    be worse than the original problem."""

    class _FakeTrack:
        def __init__(self, size):
            self.ratingKey = 'fake_track_id_2'
            self.title = 'Test'
            self.trackNumber = 1
            self.duration = 200000
            self.path = '/library/Artist/Album/02 - track.flac'
            self.bitRate = 1411
            self.file_size = size

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar3', 'Artist')")
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('al3', 'ar3', 'Album')")
    conn.commit()
    conn.close()

    # First sync — server reports 30 MB
    db.insert_or_update_media_track(_FakeTrack(size=30_000_000), album_id='al3',
                                    artist_id='ar3', server_source='jellyfin')

    # Second sync — server reports None (didn't include Size in MediaSources this time)
    db.insert_or_update_media_track(_FakeTrack(size=None), album_id='al3',
                                    artist_id='ar3', server_source='jellyfin')

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT file_size FROM tracks WHERE id = 'fake_track_id_2'")
    row = cur.fetchone()
    conn.close()
    # Original size preserved
    assert row[0] == 30_000_000
