"""Regression tests for the post-processing → provenance → tracks ID flow.

Companion to test_library_track_identity.py. The watchlist external-ID
match (PR #470) closed the demand side: when the watchlist asks "do we
have this track?", it queries by Spotify/iTunes/Deezer/etc. IDs before
falling back to fuzzy. But for users on Plex / Jellyfin / Navidrome,
the ``tracks.spotify_track_id`` column only gets populated by
asynchronous enrichment workers — sometimes hours after the file is
written. During that window the ID match falls through to fuzzy and
the bug returns.

This PR closes the supply side: the IDs we already collect at
post-processing time get persisted to ``track_downloads``, and the
media-server sync code copies them onto the new ``tracks`` row
immediately. These tests pin:

1. Schema migration adds the new ID columns + indexes
2. ``record_track_download`` accepts and persists the new kwargs
3. ``get_provenance_by_file_path`` finds rows by exact + suffix match
4. ``backfill_track_external_ids_from_provenance`` copies IDs onto a
   tracks row idempotently (COALESCE — preserves existing values)
5. ``find_provenance_by_external_id`` queries the new columns
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest


@pytest.fixture
def db_path(tmp_path: Path):
    return tmp_path / "test_music.db"


@pytest.fixture
def db(db_path: Path, monkeypatch):
    """Real MusicDatabase against a tmp SQLite file so the schema
    migration runs end-to-end (validates the ALTER TABLE additions)."""
    monkeypatch.setenv('DATABASE_PATH', str(db_path))
    # MusicDatabase is heavy; isolate to a fresh import each test so
    # other tests don't get our env-var pollution.
    import importlib
    import database.music_database as music_db_module
    importlib.reload(music_db_module)
    db = music_db_module.MusicDatabase(str(db_path))
    yield db


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def test_track_downloads_has_new_external_id_columns(self, db):
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(track_downloads)")
        cols = {row[1] for row in cursor.fetchall()}

        assert 'spotify_track_id' in cols
        assert 'itunes_track_id' in cols
        assert 'deezer_track_id' in cols
        assert 'tidal_track_id' in cols
        assert 'qobuz_track_id' in cols
        assert 'musicbrainz_recording_id' in cols
        assert 'audiodb_id' in cols
        assert 'soul_id' in cols
        assert 'isrc' in cols

    def test_track_downloads_has_external_id_indexes(self, db):
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='track_downloads'"
        )
        idx_names = {row[0] for row in cursor.fetchall()}

        assert 'idx_td_spotify_id' in idx_names
        assert 'idx_td_itunes_id' in idx_names
        assert 'idx_td_deezer_id' in idx_names
        assert 'idx_td_isrc' in idx_names


# ---------------------------------------------------------------------------
# record_track_download persists IDs
# ---------------------------------------------------------------------------


class TestRecordTrackDownloadPersistsIds:
    def test_persists_all_external_ids(self, db):
        rec_id = db.record_track_download(
            file_path='/lib/Artist/Album/Track.mp3',
            source_service='soulseek',
            source_username='user1',
            source_filename='Track.mp3',
            track_title='Track',
            spotify_track_id='sp1',
            itunes_track_id='it1',
            deezer_track_id='dz1',
            tidal_track_id='td1',
            qobuz_track_id='qb1',
            musicbrainz_recording_id='mb-uuid-1',
            audiodb_id='adb1',
            soul_id='hyd-soul-1',
            isrc='USRC17607839',
        )
        assert rec_id is not None

        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT spotify_track_id, itunes_track_id, deezer_track_id, "
            "tidal_track_id, qobuz_track_id, musicbrainz_recording_id, "
            "audiodb_id, soul_id, isrc FROM track_downloads WHERE id = ?",
            (rec_id,),
        )
        row = tuple(cursor.fetchone())
        assert row == (
            'sp1', 'it1', 'dz1', 'td1', 'qb1', 'mb-uuid-1',
            'adb1', 'hyd-soul-1', 'USRC17607839',
        )

    def test_omitted_ids_persist_as_null(self, db):
        """Backward compat — callers that don't pass the new kwargs
        still work, columns just stay NULL."""
        rec_id = db.record_track_download(
            file_path='/lib/Artist/Album/Track.mp3',
            source_service='soulseek',
            source_username='user1',
            source_filename='Track.mp3',
            track_title='Track',
        )
        assert rec_id is not None

        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT spotify_track_id FROM track_downloads WHERE id = ?", (rec_id,))
        assert cursor.fetchone()[0] is None


# ---------------------------------------------------------------------------
# get_provenance_by_file_path
# ---------------------------------------------------------------------------


class TestGetProvenanceByFilePath:
    def test_exact_match(self, db):
        db.record_track_download(
            file_path='/lib/Artist/Album/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp1',
        )
        result = db.get_provenance_by_file_path('/lib/Artist/Album/Track.mp3')
        assert result is not None
        assert result['spotify_track_id'] == 'sp1'

    def test_returns_none_when_no_match(self, db):
        result = db.get_provenance_by_file_path('/nonexistent/path.mp3')
        assert result is None

    def test_returns_none_for_empty_path(self, db):
        assert db.get_provenance_by_file_path('') is None
        assert db.get_provenance_by_file_path(None) is None

    def test_basename_suffix_fallback(self, db):
        """Recorded path differs from queried path by mount root —
        common when SoulSync container writes under /app/Transfer
        but Plex container reports the same file as /media/Music."""
        db.record_track_download(
            file_path='/app/Transfer/Artist/Album/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp1',
        )
        result = db.get_provenance_by_file_path('/media/Music/Artist/Album/Track.mp3')
        assert result is not None
        assert result['spotify_track_id'] == 'sp1'

    def test_returns_most_recent_when_multiple(self, db):
        """Same file_path can have multiple download records (re-downloads,
        retries). Most recent wins."""
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp-old',
        )
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp-new',
        )
        result = db.get_provenance_by_file_path('/lib/Track.mp3')
        assert result['spotify_track_id'] == 'sp-new'


# ---------------------------------------------------------------------------
# backfill_track_external_ids_from_provenance
# ---------------------------------------------------------------------------


class TestBackfillTrackExternalIdsFromProvenance:
    def _seed_artist_album_and_track(self, db, *, track_id, file_path):
        """Insert a minimal artists/albums/tracks chain so backfill has
        a row to update."""
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'plex')",
            ('artist-1', 'Test Artist'),
        )
        cursor.execute(
            "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'plex')",
            ('album-1', 'artist-1', 'Test Album'),
        )
        cursor.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
            "VALUES (?, ?, ?, ?, ?, 'plex')",
            (track_id, 'album-1', 'artist-1', 'Test Track', file_path),
        )
        conn.commit()

    def test_copies_all_ids_when_tracks_columns_empty(self, db):
        self._seed_artist_album_and_track(db, track_id='t1', file_path='/lib/Track.mp3')
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp1',
            deezer_track_id='dz1',
            isrc='USRC17607839',
        )

        updated = db.backfill_track_external_ids_from_provenance('t1', '/lib/Track.mp3')
        assert updated > 0

        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT spotify_track_id, deezer_id, isrc FROM tracks WHERE id = ?",
            ('t1',),
        )
        assert tuple(cursor.fetchone()) == ('sp1', 'dz1', 'USRC17607839')

    def test_preserves_existing_ids(self, db):
        """COALESCE-update — if the enrichment worker already wrote a
        spotify_track_id, the provenance backfill must NOT overwrite it
        (enrichment is generally more authoritative for late binding)."""
        self._seed_artist_album_and_track(db, track_id='t1', file_path='/lib/Track.mp3')

        # Pre-populate spotify_track_id with the enrichment-worker value
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE tracks SET spotify_track_id = 'sp-from-enrichment' WHERE id = ?", ('t1',))
        conn.commit()

        # Provenance has a different value
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp-from-provenance',
            deezer_track_id='dz1',  # This one IS missing on tracks, should backfill
        )

        db.backfill_track_external_ids_from_provenance('t1', '/lib/Track.mp3')

        cursor.execute("SELECT spotify_track_id, deezer_id FROM tracks WHERE id = ?", ('t1',))
        row = cursor.fetchone()
        assert row[0] == 'sp-from-enrichment', "Existing spotify_track_id must be preserved"
        assert row[1] == 'dz1', "Empty deezer_id should be filled from provenance"

    def test_returns_zero_when_no_provenance(self, db):
        self._seed_artist_album_and_track(db, track_id='t1', file_path='/lib/Track.mp3')
        # No record_track_download call — no provenance row exists
        updated = db.backfill_track_external_ids_from_provenance('t1', '/lib/Track.mp3')
        assert updated == 0

    def test_returns_zero_for_empty_inputs(self, db):
        assert db.backfill_track_external_ids_from_provenance(None, '/lib/Track.mp3') == 0
        assert db.backfill_track_external_ids_from_provenance('t1', None) == 0
        assert db.backfill_track_external_ids_from_provenance('t1', '') == 0


# ---------------------------------------------------------------------------
# find_provenance_by_external_id
# ---------------------------------------------------------------------------


class TestFindProvenanceByExternalId:
    def test_match_by_spotify_id(self, db):
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp1',
        )

        from core.library.track_identity import find_provenance_by_external_id
        result = find_provenance_by_external_id(db, external_ids={'spotify_id': 'sp1'})
        assert result is not None
        assert result['file_path'] == '/lib/Track.mp3'
        assert result['spotify_track_id'] == 'sp1'

    def test_match_by_isrc(self, db):
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            isrc='USRC17607839',
        )

        from core.library.track_identity import find_provenance_by_external_id
        result = find_provenance_by_external_id(db, external_ids={'isrc': 'USRC17607839'})
        assert result is not None

    def test_returns_none_when_no_match(self, db):
        from core.library.track_identity import find_provenance_by_external_id
        result = find_provenance_by_external_id(db, external_ids={'spotify_id': 'sp-other'})
        assert result is None

    def test_returns_none_for_empty_external_ids(self, db):
        from core.library.track_identity import find_provenance_by_external_id
        assert find_provenance_by_external_id(db, external_ids={}) is None

    def test_returns_most_recent_when_multiple_matches(self, db):
        """Re-downloads create multiple rows. Newest wins."""
        db.record_track_download(
            file_path='/lib/Track-v1.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            spotify_track_id='sp1',
        )
        db.record_track_download(
            file_path='/lib/Track-v2.mp3',
            source_service='tidal', source_username='tidal', source_filename='Track.flac',
            spotify_track_id='sp1',
        )

        from core.library.track_identity import find_provenance_by_external_id
        result = find_provenance_by_external_id(db, external_ids={'spotify_id': 'sp1'})
        assert result['file_path'] == '/lib/Track-v2.mp3'

    def test_or_semantics_across_id_types(self, db):
        """Provenance has only ISRC; source asks with multiple IDs incl. ISRC.
        Match should fire on ISRC."""
        db.record_track_download(
            file_path='/lib/Track.mp3',
            source_service='soulseek', source_username='u', source_filename='Track.mp3',
            isrc='USRC17607839',
        )

        from core.library.track_identity import find_provenance_by_external_id
        result = find_provenance_by_external_id(db, external_ids={
            'spotify_id': 'sp-mismatch',
            'isrc': 'USRC17607839',
        })
        assert result is not None
