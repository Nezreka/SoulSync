"""Tests for the provider-neutral external-ID match helper.

Discord-reported (CAL): the watchlist scanner re-downloaded a track
already on disk because the library DB had stale album metadata. The
album fuzzy correctly said the names didn't match and the scanner
declared the track missing. The track's stable external IDs (Spotify
ID, Deezer ID, MusicBrainz recording ID, ISRC, etc.) were available on
both sides but never consulted.

These tests pin the new ID-extraction helper + the library SELECT so
the regression doesn't return.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict

import pytest

from core.library.track_identity import (
    EXTERNAL_ID_COLUMNS,
    extract_external_ids,
    find_library_track_by_external_id,
)


# ---------------------------------------------------------------------------
# extract_external_ids
# ---------------------------------------------------------------------------


class TestExtractExternalIdsFromDirectFields:
    def test_spotify_track_with_spotify_id_field(self):
        track = {'spotify_id': 'sp1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'spotify_id': 'sp1'}

    def test_track_with_alias_spotify_track_id(self):
        track = {'spotify_track_id': 'sp1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'spotify_id': 'sp1'}

    def test_track_with_uppercase_tag_name(self):
        track = {'SPOTIFY_TRACK_ID': 'sp1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'spotify_id': 'sp1'}

    def test_itunes_via_trackId_alias(self):
        track = {'trackId': 12345, 'name': 'Hello'}
        assert extract_external_ids(track) == {'itunes_id': '12345'}

    def test_deezer_via_provider_native_id(self):
        track = {'id': 'dz1', 'provider': 'deezer', 'name': 'Hello'}
        assert extract_external_ids(track) == {'deezer_id': 'dz1'}

    def test_isrc_extracted(self):
        track = {'isrc': 'USRC17607839', 'name': 'Hello'}
        assert extract_external_ids(track) == {'isrc': 'USRC17607839'}

    def test_musicbrainz_recording_id_extracted(self):
        track = {'musicbrainz_recording_id': 'mb-uuid-1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'mbid': 'mb-uuid-1'}

    def test_audiodb_id_via_idTrack_alias(self):
        track = {'idTrack': 'adb1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'audiodb_id': 'adb1'}

    def test_soul_id_extracted(self):
        track = {'soul_id': 'soul-abc', 'name': 'Hello'}
        assert extract_external_ids(track) == {'soul_id': 'soul-abc'}


class TestExtractExternalIdsFromProviderField:
    """The provider field disambiguates a track's native ``id`` field."""

    def test_provider_spotify_with_native_id(self):
        track = {'provider': 'spotify', 'id': 'sp1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'spotify_id': 'sp1'}

    def test_provider_itunes_with_native_id(self):
        track = {'provider': 'itunes', 'id': 'it1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'itunes_id': 'it1'}

    def test_provider_tidal_with_native_id(self):
        track = {'provider': 'tidal', 'id': 'td1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'tidal_id': 'td1'}

    def test_provider_qobuz_with_native_id(self):
        track = {'provider': 'qobuz', 'id': 'qb1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'qobuz_id': 'qb1'}

    def test_provider_musicbrainz_with_native_id(self):
        track = {'provider': 'musicbrainz', 'id': 'mb-uuid', 'name': 'Hello'}
        assert extract_external_ids(track) == {'mbid': 'mb-uuid'}

    def test_provider_hydrabase_with_native_id(self):
        track = {'provider': 'hydrabase', 'id': 'hyd1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'soul_id': 'hyd1'}

    def test_source_field_treated_same_as_provider(self):
        track = {'source': 'deezer', 'id': 'dz1', 'name': 'Hello'}
        assert extract_external_ids(track) == {'deezer_id': 'dz1'}

    def test_native_id_without_provider_is_ignored(self):
        """Without a provider field we can't tell which source 'id' belongs to."""
        track = {'id': 'unknown', 'name': 'Hello'}
        assert extract_external_ids(track) == {}


class TestExtractExternalIdsMixedAndDefensive:
    def test_track_with_multiple_provider_specific_fields(self):
        track = {
            'spotify_id': 'sp1',
            'itunes_id': 'it1',
            'isrc': 'USRC17607839',
            'name': 'Hello',
        }
        assert extract_external_ids(track) == {
            'spotify_id': 'sp1',
            'itunes_id': 'it1',
            'isrc': 'USRC17607839',
        }

    def test_direct_field_takes_precedence_over_provider_native_id(self):
        """If both 'spotify_id' and provider/'id' are set, the direct
        field wins (already collected first)."""
        track = {
            'spotify_id': 'direct-sp',
            'provider': 'spotify',
            'id': 'native-sp',
        }
        assert extract_external_ids(track) == {'spotify_id': 'direct-sp'}

    def test_object_style_track_supported(self):
        class _Track:
            def __init__(self):
                self.spotify_id = 'sp1'
                self.isrc = 'USRC17607839'
                self.name = 'Hello'

        assert extract_external_ids(_Track()) == {
            'spotify_id': 'sp1',
            'isrc': 'USRC17607839',
        }

    def test_empty_strings_treated_as_missing(self):
        track = {'spotify_id': '', 'itunes_id': '   ', 'isrc': None}
        assert extract_external_ids(track) == {}

    def test_no_ids_returns_empty_dict(self):
        track = {'name': 'Hello', 'duration_ms': 1000}
        assert extract_external_ids(track) == {}

    def test_none_track_returns_empty_dict(self):
        assert extract_external_ids(None) == {}

    def test_numeric_ids_coerced_to_string(self):
        track = {'spotify_id': 12345, 'name': 'Hello'}
        assert extract_external_ids(track) == {'spotify_id': '12345'}


# ---------------------------------------------------------------------------
# find_library_track_by_external_id
# ---------------------------------------------------------------------------


class _FakeDatabase:
    """Minimal DB stand-in exposing ``_get_connection()`` like MusicDatabase."""

    def __init__(self):
        self._conn = sqlite3.connect(':memory:')
        self._conn.row_factory = sqlite3.Row
        # Schema mirrors the columns the helper reads — only the ones we
        # actually use need to exist.
        self._conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                title TEXT,
                spotify_track_id TEXT,
                itunes_track_id TEXT,
                deezer_id TEXT,
                tidal_id TEXT,
                qobuz_id TEXT,
                musicbrainz_recording_id TEXT,
                audiodb_id TEXT,
                soul_id TEXT,
                isrc TEXT,
                server_source TEXT
            )
        """)
        self._conn.commit()

    def _get_connection(self):
        # Mirror MusicDatabase's pattern: caller closes the returned
        # connection. Use a thin wrapper that no-ops close so the in-
        # memory DB isn't dropped between calls.
        class _NoCloseConn:
            def __init__(_self, real):
                _self._real = real

            def __getattr__(_self, name):
                return getattr(_self._real, name)

            def close(_self):
                pass

        return _NoCloseConn(self._conn)

    def insert(self, **kwargs):
        cols = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' * len(kwargs))
        self._conn.execute(
            f"INSERT INTO tracks ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        self._conn.commit()


@pytest.fixture
def db():
    return _FakeDatabase()


class TestFindLibraryTrackBySpotifyId:
    def test_match_by_spotify_id(self, db):
        db.insert(title='Hello', spotify_track_id='sp1')
        result = find_library_track_by_external_id(db, external_ids={'spotify_id': 'sp1'})
        assert result is not None
        assert result['title'] == 'Hello'
        assert result['spotify_track_id'] == 'sp1'

    def test_no_match_returns_none(self, db):
        db.insert(title='Hello', spotify_track_id='sp-other')
        result = find_library_track_by_external_id(db, external_ids={'spotify_id': 'sp1'})
        assert result is None

    def test_null_column_is_skipped(self, db):
        """A library row with NULL spotify_track_id must NOT match an
        empty/missing source ID — the IS NOT NULL guard prevents that."""
        db.insert(title='NoIDs')  # all IDs NULL
        # Empty external_ids → no match
        assert find_library_track_by_external_id(db, external_ids={}) is None


class TestFindLibraryTrackProviderNeutral:
    def test_match_by_itunes_id_when_spotify_missing(self, db):
        db.insert(title='Hello', itunes_track_id='it1')
        result = find_library_track_by_external_id(
            db, external_ids={'itunes_id': 'it1'},
        )
        assert result is not None
        assert result['itunes_track_id'] == 'it1'

    def test_match_by_deezer_id(self, db):
        db.insert(title='Hello', deezer_id='dz1')
        result = find_library_track_by_external_id(
            db, external_ids={'deezer_id': 'dz1'},
        )
        assert result is not None

    def test_match_by_tidal_id(self, db):
        db.insert(title='Hello', tidal_id='td1')
        result = find_library_track_by_external_id(
            db, external_ids={'tidal_id': 'td1'},
        )
        assert result is not None

    def test_match_by_qobuz_id(self, db):
        db.insert(title='Hello', qobuz_id='qb1')
        result = find_library_track_by_external_id(
            db, external_ids={'qobuz_id': 'qb1'},
        )
        assert result is not None

    def test_match_by_musicbrainz_recording_id(self, db):
        db.insert(title='Hello', musicbrainz_recording_id='mb-uuid')
        result = find_library_track_by_external_id(
            db, external_ids={'mbid': 'mb-uuid'},
        )
        assert result is not None

    def test_match_by_isrc_across_providers(self, db):
        """ISRC is the cross-source identity — a library track imported
        from Deezer can be matched against a Spotify scan if both carry
        the same ISRC."""
        db.insert(title='Hello', deezer_id='dz1', isrc='USRC17607839')
        # Source track has Spotify ID + ISRC; library only has Deezer + ISRC.
        # The ISRC bridges them.
        result = find_library_track_by_external_id(
            db, external_ids={'spotify_id': 'sp-different', 'isrc': 'USRC17607839'},
        )
        assert result is not None
        assert result['isrc'] == 'USRC17607839'

    def test_match_by_soul_id(self, db):
        db.insert(title='Hello', soul_id='hyd-soul-1')
        result = find_library_track_by_external_id(
            db, external_ids={'soul_id': 'hyd-soul-1'},
        )
        assert result is not None


class TestFindLibraryTrackOrSemantics:
    def test_any_one_matching_id_is_enough(self, db):
        db.insert(title='Hello', spotify_track_id='sp1')
        result = find_library_track_by_external_id(
            db,
            external_ids={
                'spotify_id': 'sp1',
                'itunes_id': 'wrong',
                'deezer_id': 'wrong',
            },
        )
        assert result is not None

    def test_no_matching_id_returns_none(self, db):
        db.insert(title='Hello', spotify_track_id='sp1', itunes_track_id='it1')
        result = find_library_track_by_external_id(
            db,
            external_ids={'deezer_id': 'dz-other'},
        )
        assert result is None

    def test_empty_external_ids_returns_none(self, db):
        db.insert(title='Hello', spotify_track_id='sp1')
        assert find_library_track_by_external_id(db, external_ids={}) is None


class TestFindLibraryTrackServerSourceFilter:
    def test_server_source_match(self, db):
        db.insert(title='Hello', spotify_track_id='sp1', server_source='plex')
        result = find_library_track_by_external_id(
            db, external_ids={'spotify_id': 'sp1'}, server_source='plex',
        )
        assert result is not None

    def test_server_source_mismatch_with_filter(self, db):
        db.insert(title='Hello', spotify_track_id='sp1', server_source='jellyfin')
        result = find_library_track_by_external_id(
            db, external_ids={'spotify_id': 'sp1'}, server_source='plex',
        )
        # Filter excludes jellyfin, so no match.
        assert result is None

    def test_null_server_source_passes_filter(self, db):
        """Older library rows may have NULL server_source — those should
        still match when a filter is applied (defensive)."""
        db.insert(title='Hello', spotify_track_id='sp1', server_source=None)
        result = find_library_track_by_external_id(
            db, external_ids={'spotify_id': 'sp1'}, server_source='plex',
        )
        assert result is not None

    def test_no_filter_matches_any_server_source(self, db):
        db.insert(title='Hello', spotify_track_id='sp1', server_source='jellyfin')
        result = find_library_track_by_external_id(
            db, external_ids={'spotify_id': 'sp1'}, server_source=None,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# EXTERNAL_ID_COLUMNS map sanity
# ---------------------------------------------------------------------------


class TestExternalIdColumnsMap:
    def test_every_known_id_name_has_a_column(self):
        """If extract_external_ids ever adds a new ID name, the map needs
        a column entry too — otherwise find_library_track_by_external_id
        silently ignores it."""
        # Sample of ID names extract_external_ids can return; keep in sync.
        known_id_names = {
            'spotify_id', 'itunes_id', 'deezer_id', 'tidal_id', 'qobuz_id',
            'mbid', 'audiodb_id', 'soul_id', 'isrc',
        }
        assert set(EXTERNAL_ID_COLUMNS.keys()) == known_id_names

    def test_column_names_are_unique(self):
        cols = list(EXTERNAL_ID_COLUMNS.values())
        assert len(cols) == len(set(cols)), \
            f"Duplicate column targets in EXTERNAL_ID_COLUMNS: {cols}"
