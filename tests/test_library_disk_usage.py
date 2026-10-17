"""Tests for the Library Disk Usage stat.

Discord request (Samuel [KC]): show how much disk space the library
takes on the System Statistics page. Implementation piggybacks on the
existing deep scan — Plex/Jellyfin/Navidrome all return file size in
their track API responses, so we read it during the deep scan and
aggregate via SQL on demand. No filesystem walk involved.

Tests pin:
- Aggregator returns the empty-shape dict for fresh installs and
  walks/sums correctly when populated.
- Per-format breakdown handles mixed extensions correctly.
- Defensive: empty / NULL / malformed paths don't crash.
"""

from __future__ import annotations

import os
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
    """Seed a v2 track and its file row.

    The size lives on the FILE in v2 (ADR-03), not on the track — the track
    itself has no path and no bytes.
    """
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO lib2_artists (id, name, name_key)"
                " VALUES (1, 'Test Artist', 'test artist')")
    cur.execute("INSERT OR IGNORE INTO lib2_albums (id, primary_artist_id, title,"
                "                                   origin)"
                " VALUES (1, 1, 'Test Album', 'library')")
    new_track = cur.execute(
        "INSERT INTO lib2_tracks (album_id, title) VALUES (1, ?)",
        (f'track-{track_id}',),
    ).lastrowid
    cur.execute(
        "INSERT INTO lib2_track_files (track_id, path, size, is_primary)"
        " VALUES (?, ?, ?, 1)",
        (new_track, file_path, file_size),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


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
    # No configured path means no physical ownership evidence.
    assert result['total_bytes'] == 10_000_000
    # But by_format only has the one with a real extension
    assert result['by_format'] == {'flac': 10_000_000}


def test_aggregator_skips_implausibly_long_extension(db: MusicDatabase) -> None:
    """Extensions over 6 chars are filtered (would be junk from an
    unusual filename like 'song.somethingweird')."""
    _insert_track(db, track_id='t1', file_path='/x/song.somethingweird', file_size=5_000_000)
    _insert_track(db, track_id='t2', file_path='/x/song.flac', file_size=10_000_000)

    result = db.get_library_disk_usage()
    assert result['by_format'] == {'flac': 10_000_000}


def test_provider_only_catalogue_is_not_owned_or_missing_size(db: MusicDatabase) -> None:
    with db._get_connection() as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Provider')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,origin) "
            "VALUES(?,'Catalog','discography')", (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'No File')", (album,))

    assert db.get_library_disk_usage()['tracks_without_size'] == 0
    assert db.get_statistics_for_server() == {'artists': 0, 'albums': 0, 'tracks': 0}


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
    artist = cur.execute(
        "INSERT INTO lib2_artists (name, name_key, server_source, server_id)"
        " VALUES ('Artist', 'artist', 'jellyfin', 'ar2')").lastrowid
    album = cur.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, origin, server_source, server_id)"
        " VALUES (?, 'Album', 'library', 'jellyfin', 'al2')", (artist,)
    ).lastrowid
    track = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,'Test Track',1)",
        (album,),
    ).lastrowid
    cur.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES(?,?,1)",
        (track, '/library/Artist/Album/01 - track.flac'),
    )
    conn.commit()
    conn.close()

    result = db.insert_or_update_media_track(
        _FakeTrack(), album_id='al2', artist_id='ar2', server_source='jellyfin')

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT f.size FROM lib2_track_files f"
                " JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE t.server_id = 'fake_track_id_1'")
    row = cur.fetchone()
    history = cur.execute(
        "SELECT COUNT(*) FROM library_history WHERE event_type='import'"
    ).fetchone()[0]
    conn.close()
    assert row[0] == 42_000_000
    # 'inserted' = the run created the server MAPPING, not a catalogue row —
    # nothing here creates tracks. This is the first sight of this server id,
    # so the post-scan reconcile will read its tags
    # (tests/test_post_scan_reconcile_v2.py). What this test cares about is
    # that the write succeeded and imported nothing.
    assert result == 'inserted' and history == 0


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
    artist = cur.execute(
        "INSERT INTO lib2_artists (name, name_key, server_source, server_id)"
        " VALUES ('Artist', 'artist', 'jellyfin', 'ar3')").lastrowid
    album = cur.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, origin, server_source, server_id)"
        " VALUES (?, 'Album', 'library', 'jellyfin', 'al3')", (artist,)
    ).lastrowid
    track = cur.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number) VALUES(?,'Test',1)",
        (album,),
    ).lastrowid
    cur.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary) VALUES(?,?,1)",
        (track, '/library/Artist/Album/02 - track.flac'),
    )
    conn.commit()
    conn.close()

    # First sync — server reports 30 MB
    db.insert_or_update_media_track(_FakeTrack(size=30_000_000), album_id='al3',
                                    artist_id='ar3', server_source='jellyfin')

    # Second sync — server reports None (didn't include Size in MediaSources this time)
    result = db.insert_or_update_media_track(_FakeTrack(size=None), album_id='al3',
                                             artist_id='ar3', server_source='jellyfin')

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT f.size FROM lib2_track_files f"
                " JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE t.server_id = 'fake_track_id_2'")
    row = cur.fetchone()
    conn.close()
    # Original size preserved
    assert row[0] == 30_000_000
    assert result == 'updated'
