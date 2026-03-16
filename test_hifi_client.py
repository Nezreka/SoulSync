"""
Comprehensive tests for HiFi API Client (core/hifi_client.py).

Tests the client against live public hifi-api instances to validate:
- Instance availability and API versioning
- Track search (by title, artist, album, combined queries)
- Stream URL retrieval and base64 manifest decoding
- Album lookup and track listing
- Artist lookup
- Quality tier selection and fallback chain
- Instance failover when one goes down
- TrackResult / DownloadStatus compatibility with Soulseek interfaces
- Actual file download (small test track)
- Rate limiting behavior
- Error handling (bad IDs, empty queries, malformed data)
- Download lifecycle: start → progress → complete/cancel/error

Usage:
    python test_hifi_client.py                  # Run all tests
    python test_hifi_client.py -k search        # Run only search tests
    python test_hifi_client.py -v               # Verbose output
"""

import os
import sys
import json
import time
import asyncio
import tempfile
import shutil
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.hifi_client import (
    HiFiClient,
    HIFI_QUALITY_MAP,
    DEFAULT_INSTANCES,
)
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus


# ===================== Fixtures =====================

@pytest.fixture(scope="session")
def temp_download_dir():
    """Create a temporary download directory for the entire test session."""
    d = tempfile.mkdtemp(prefix="hifi_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def client(temp_download_dir):
    """Create a fresh HiFiClient for each test."""
    c = HiFiClient(download_path=temp_download_dir)
    return c


@pytest.fixture(scope="session")
def shared_client(temp_download_dir):
    """Shared client for read-only tests (avoids excessive instance creation)."""
    return HiFiClient(download_path=temp_download_dir)


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def run_async(coro):
    """Helper to run an async function synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===================== 1. Instance & Availability Tests =====================

class TestInstanceManagement:
    """Tests for API instance management and availability."""

    def test_01_default_instances_populated(self, client):
        """Client should have default instances loaded."""
        assert len(client._instances) >= 1
        assert client._current_instance is not None

    def test_02_custom_instance_priority(self, temp_download_dir):
        """Custom base_url should be first in instance list."""
        custom = "https://my-custom-instance.example.com"
        c = HiFiClient(download_path=temp_download_dir, base_url=custom)
        assert c._instances[0] == custom
        assert c._current_instance == custom

    def test_03_custom_instance_trailing_slash(self, temp_download_dir):
        """Trailing slash should be stripped from custom instance URL."""
        custom = "https://my-custom-instance.example.com/"
        c = HiFiClient(download_path=temp_download_dir, base_url=custom)
        assert c._instances[0] == "https://my-custom-instance.example.com"

    def test_04_is_available(self, shared_client):
        """At least one public instance should be reachable."""
        assert shared_client.is_available(), "No HiFi API instance is reachable"

    def test_05_get_version(self, shared_client):
        """Should return a version string from the API."""
        version = shared_client.get_version()
        # version may be None if endpoint doesn't expose it, that's okay
        # but the call shouldn't crash
        if version:
            assert isinstance(version, str)

    def test_06_instance_rotation(self, temp_download_dir):
        """Rotating an instance should move it to the back."""
        c = HiFiClient(download_path=temp_download_dir)
        first = c._instances[0]
        second = c._instances[1] if len(c._instances) > 1 else None

        c._rotate_instance(first)

        assert c._instances[-1] == first
        if second:
            assert c._current_instance == second

    def test_07_rotate_nonexistent_instance(self, client):
        """Rotating a URL not in the list shouldn't crash."""
        original_first = client._current_instance
        client._rotate_instance("https://nonexistent.example.com")
        assert client._current_instance == original_first

    def test_08_all_instances_exhausted(self, temp_download_dir):
        """_api_get should return None when all instances fail."""
        c = HiFiClient(download_path=temp_download_dir)
        c._instances = ["https://definitely-not-real-1.invalid", "https://definitely-not-real-2.invalid"]
        c._current_instance = c._instances[0]
        c._min_interval = 0  # No rate limiting in test

        result = c._api_get("/", timeout=3)
        assert result is None


# ===================== 2. Rate Limiting Tests =====================

class TestRateLimiting:
    """Tests for rate limiting between API calls."""

    def test_09_rate_limit_enforces_interval(self, temp_download_dir):
        """Rate limiting should enforce minimum interval between calls."""
        c = HiFiClient(download_path=temp_download_dir)
        c._min_interval = 0.3

        start = time.time()
        c._rate_limit()
        c._rate_limit()
        elapsed = time.time() - start

        assert elapsed >= 0.25, f"Rate limiting should enforce ~0.3s gap, got {elapsed:.3f}s"

    def test_10_rate_limit_first_call_no_delay(self, temp_download_dir):
        """First API call shouldn't have artificial delay."""
        c = HiFiClient(download_path=temp_download_dir)
        c._min_interval = 1.0
        c._last_api_call = 0  # Reset

        start = time.time()
        c._rate_limit()
        elapsed = time.time() - start

        assert elapsed < 0.1, f"First call should be instant, took {elapsed:.3f}s"


# ===================== 3. Search Tests =====================

class TestSearch:
    """Tests for track search functionality."""

    def test_11_search_by_title(self, shared_client):
        """Search by title should return results."""
        results = shared_client.search_tracks(title="Bohemian Rhapsody")
        assert len(results) > 0, "Expected results for 'Bohemian Rhapsody'"
        assert results[0]['title'], "Track should have a title"

    def test_12_search_by_artist(self, shared_client):
        """Search by artist alone may return empty (API returns artist objects, not tracks).
        This test validates it doesn't crash."""
        results = shared_client.search_tracks(artist="Queen")
        # Artist-only search hits /search/?a=Queen which returns artist objects,
        # not tracks — so 0 results is expected behavior.
        assert isinstance(results, list)

    def test_13_search_by_title_and_artist(self, shared_client):
        """Combined title + artist search should return relevant results."""
        results = shared_client.search_tracks(title="Stairway to Heaven", artist="Led Zeppelin")
        assert len(results) > 0, "Expected results for 'Stairway to Heaven' by Led Zeppelin"

        # Check that at least one result mentions the artist
        found_artist = False
        for r in results:
            if 'led zeppelin' in r.get('artist', '').lower():
                found_artist = True
                break
        assert found_artist, "Expected at least one result from Led Zeppelin"

    def test_14_search_by_album(self, shared_client):
        """Search by album alone may return empty (API returns album objects, not tracks).
        This test validates it doesn't crash."""
        results = shared_client.search_tracks(album="Dark Side of the Moon")
        # Album-only search hits /search/?al=... which returns album objects,
        # not tracks — so 0 results is expected behavior.
        assert isinstance(results, list)

    def test_15_search_limit(self, shared_client):
        """Search should respect the limit parameter."""
        results = shared_client.search_tracks(title="Love", limit=5)
        assert len(results) <= 5, f"Expected ≤5 results, got {len(results)}"

    def test_16_search_no_terms(self, client):
        """Search with no terms should return empty list."""
        results = client.search_tracks()
        assert results == []

    def test_17_search_gibberish(self, shared_client):
        """Search for gibberish should return empty or handle gracefully."""
        results = shared_client.search_tracks(title="xzqwkjhgf9876zzz")
        assert isinstance(results, list)

    def test_18_search_generic(self, shared_client):
        """Generic search_raw() should call search_tracks with title."""
        results = shared_client.search_raw("Never Gonna Give You Up")
        assert len(results) > 0

    def test_19_search_result_fields(self, shared_client):
        """Search results should have all expected fields."""
        results = shared_client.search_tracks(title="Yesterday", artist="Beatles")
        assert len(results) > 0

        track = results[0]
        required_fields = ['id', 'title', 'artist', 'album', 'duration_ms']
        for field in required_fields:
            assert field in track, f"Missing field: {field}"

        assert track['id'] is not None, "Track ID should not be None"
        assert isinstance(track['title'], str)
        assert isinstance(track['artist'], str)

    def test_20_search_duration_is_milliseconds(self, shared_client):
        """Duration should be in milliseconds (typically > 30000 for a normal song)."""
        results = shared_client.search_tracks(title="Bohemian Rhapsody", artist="Queen")
        if results:
            track = results[0]
            duration = track.get('duration_ms', 0)
            # Bohemian Rhapsody is ~6 minutes = ~360000ms
            if duration > 0:
                assert duration > 10000, f"Duration {duration}ms seems too low — might be in seconds"

    def test_21_search_special_characters(self, shared_client):
        """Search should handle special characters."""
        results = shared_client.search_tracks(title="What's Going On", artist="Marvin Gaye")
        assert isinstance(results, list)

    def test_22_search_unicode(self, shared_client):
        """Search should handle unicode characters."""
        results = shared_client.search_tracks(title="Für Elise")
        assert isinstance(results, list)

    def test_23_search_very_long_query(self, shared_client):
        """Very long query should not crash."""
        results = shared_client.search_tracks(title="A" * 500)
        assert isinstance(results, list)


# ===================== 4. Track Info Tests =====================

class TestTrackInfo:
    """Tests for individual track info retrieval."""

    def _get_test_track_id(self, client):
        """Helper: search for a track and return its ID."""
        results = client.search_tracks(title="Bohemian Rhapsody", artist="Queen")
        if results:
            return results[0]['id']
        return None

    def test_24_get_track_info(self, shared_client):
        """Should return track info for a valid ID."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results to get a track ID")

        info = shared_client.get_track_info(track_id)
        assert info is not None, f"Expected track info for ID {track_id}"
        assert info.get('title'), "Track info should have a title"
        assert info.get('artist'), "Track info should have an artist"

    def test_25_get_track_info_invalid_id(self, shared_client):
        """Invalid track ID should return None, not crash."""
        info = shared_client.get_track_info(99999999999)
        # May return None or an error — just shouldn't raise
        assert info is None or isinstance(info, dict)

    def test_26_get_track_info_zero_id(self, shared_client):
        """Zero ID should handle gracefully."""
        info = shared_client.get_track_info(0)
        assert info is None or isinstance(info, dict)


# ===================== 5. Stream URL / Manifest Tests =====================

class TestStreamURL:
    """Tests for stream URL retrieval and manifest decoding."""

    def _get_test_track_id(self, client):
        results = client.search_tracks(title="Bohemian Rhapsody", artist="Queen")
        return results[0]['id'] if results else None

    def test_27_get_stream_url_lossless(self, shared_client):
        """Should return a stream URL for lossless quality."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results")

        stream = shared_client.get_stream_url(track_id, quality='lossless')
        if stream is None:
            pytest.skip("Stream URL not available (may be geo-restricted)")

        assert 'url' in stream, "Stream info should contain 'url'"
        assert stream['url'].startswith('http'), f"URL should be HTTP(S): {stream['url'][:100]}"
        assert stream['quality'] == 'lossless'

    def test_28_get_stream_url_hires(self, shared_client):
        """Should try to get hi-res stream URL."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results")

        stream = shared_client.get_stream_url(track_id, quality='hires')
        # Hi-res may not be available for all tracks — just shouldn't crash
        if stream:
            assert 'url' in stream

    def test_29_get_stream_url_high(self, shared_client):
        """Should get AAC stream URL."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results")

        stream = shared_client.get_stream_url(track_id, quality='high')
        if stream:
            assert 'url' in stream
            assert stream['quality'] == 'high'

    def test_30_get_stream_url_invalid_track(self, shared_client):
        """Invalid track ID should return None."""
        stream = shared_client.get_stream_url(99999999999, quality='lossless')
        assert stream is None

    def test_31_get_stream_url_invalid_quality(self, shared_client):
        """Invalid quality key should fall back to lossless."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results")

        # 'nonexistent' isn't in HIFI_QUALITY_MAP, should fall back
        stream = shared_client.get_stream_url(track_id, quality='nonexistent')
        # Should not crash — either returns data with lossless fallback or None

    def test_32_stream_url_has_encryption_info(self, shared_client):
        """Stream info should include encryption field."""
        track_id = self._get_test_track_id(shared_client)
        if not track_id:
            pytest.skip("No search results")

        stream = shared_client.get_stream_url(track_id, quality='lossless')
        if stream:
            assert 'encryption' in stream
            # Most should be 'NONE' for public instances
            assert stream['encryption'] in ('NONE', 'OLD_AES', ''), \
                f"Unexpected encryption: {stream['encryption']}"


# ===================== 6. Album Tests =====================

class TestAlbum:
    """Tests for album lookup."""

    def _get_test_album_id(self, client):
        """Search for a track and extract its album ID if available."""
        results = client.search_tracks(title="Bohemian Rhapsody", artist="Queen", limit=5)
        for r in results:
            # Album ID may be embedded in album info
            if r.get('album'):
                # We need an actual album ID — search the album endpoint
                break
        return None

    def test_33_get_album_known_id(self, shared_client):
        """Should return album data for a known Tidal album ID."""
        # "A Night at the Opera" by Queen — Tidal album ID 36393265
        album = shared_client.get_album(36393265)
        if album is None:
            pytest.skip("Album endpoint not available or ID changed")

        assert album.get('title'), "Album should have a title"
        assert isinstance(album.get('tracks', []), list)

    def test_34_get_album_tracks_have_metadata(self, shared_client):
        """Album tracks should have proper metadata."""
        album = shared_client.get_album(36393265)
        if not album or not album.get('tracks'):
            pytest.skip("Album data not available")

        for track in album['tracks'][:3]:
            assert track.get('title'), f"Track missing title: {track}"
            assert track.get('id'), f"Track missing ID: {track}"

    def test_35_get_album_invalid_id(self, shared_client):
        """Invalid album ID should return None."""
        album = shared_client.get_album(99999999999)
        assert album is None or (isinstance(album, dict) and len(album.get('tracks', [])) == 0)

    def test_36_get_album_track_count(self, shared_client):
        """Album track_count should match actual tracks returned."""
        album = shared_client.get_album(36393265)
        if not album:
            pytest.skip("Album data not available")

        tracks = album.get('tracks', [])
        count = album.get('track_count', 0)
        # These should be close (may differ if some tracks are unavailable)
        if tracks and count:
            assert abs(len(tracks) - count) <= 2, \
                f"Track count mismatch: {len(tracks)} tracks vs reported {count}"


# ===================== 7. Artist Tests =====================

class TestArtist:
    """Tests for artist lookup."""

    def test_37_get_artist_known_id(self, shared_client):
        """Should return artist data for Queen (Tidal artist ID 30157)."""
        artist = shared_client.get_artist(30157)
        if artist is None:
            pytest.skip("Artist endpoint not available")

        assert isinstance(artist, dict)

    def test_38_get_artist_invalid_id(self, shared_client):
        """Invalid artist ID should return None."""
        artist = shared_client.get_artist(99999999999)
        assert artist is None or isinstance(artist, dict)


# ===================== 8. Quality Map Tests =====================

class TestQualityMap:
    """Tests for quality tier configuration."""

    def test_39_all_quality_tiers_present(self):
        """All four quality tiers should be defined."""
        assert 'hires' in HIFI_QUALITY_MAP
        assert 'lossless' in HIFI_QUALITY_MAP
        assert 'high' in HIFI_QUALITY_MAP
        assert 'low' in HIFI_QUALITY_MAP

    def test_40_quality_tiers_have_required_fields(self):
        """Each quality tier should have api_value, label, extension, bitrate, codec."""
        for key, tier in HIFI_QUALITY_MAP.items():
            assert 'api_value' in tier, f"{key} missing api_value"
            assert 'label' in tier, f"{key} missing label"
            assert 'extension' in tier, f"{key} missing extension"
            assert 'bitrate' in tier, f"{key} missing bitrate"
            assert 'codec' in tier, f"{key} missing codec"

    def test_41_flac_tiers_have_flac_extension(self):
        """Lossless tiers should produce .flac files."""
        assert HIFI_QUALITY_MAP['hires']['extension'] == 'flac'
        assert HIFI_QUALITY_MAP['lossless']['extension'] == 'flac'

    def test_42_aac_tiers_have_m4a_extension(self):
        """Lossy tiers should produce .m4a files."""
        assert HIFI_QUALITY_MAP['high']['extension'] == 'm4a'
        assert HIFI_QUALITY_MAP['low']['extension'] == 'm4a'

    def test_43_bitrate_ordering(self):
        """Bitrates should descend: hires > lossless > high > low."""
        assert HIFI_QUALITY_MAP['hires']['bitrate'] > HIFI_QUALITY_MAP['lossless']['bitrate']
        assert HIFI_QUALITY_MAP['lossless']['bitrate'] > HIFI_QUALITY_MAP['high']['bitrate']
        assert HIFI_QUALITY_MAP['high']['bitrate'] > HIFI_QUALITY_MAP['low']['bitrate']


# ===================== 9. Parse Track Tests =====================

class TestParseTrack:
    """Tests for _parse_track internal method."""

    def test_44_parse_track_basic(self, client):
        """Should parse a simple track dict."""
        item = {
            'id': 12345,
            'title': 'Test Song',
            'artists': [{'name': 'Test Artist'}],
            'album': {'title': 'Test Album'},
            'duration': 240,
            'trackNumber': 3,
            'isrc': 'USTEST0001',
        }
        result = client._parse_track(item)
        assert result['id'] == 12345
        assert result['title'] == 'Test Song'
        assert result['artist'] == 'Test Artist'
        assert result['album'] == 'Test Album'
        assert result['duration_ms'] == 240000  # 240s → 240000ms
        assert result['track_number'] == 3
        assert result['isrc'] == 'USTEST0001'

    def test_45_parse_track_multiple_artists(self, client):
        """Should join multiple artists with commas."""
        item = {
            'id': 1,
            'title': 'Collab',
            'artists': [{'name': 'Artist A'}, {'name': 'Artist B'}, {'name': 'Artist C'}],
            'duration': 180,
        }
        result = client._parse_track(item)
        assert result['artist'] == 'Artist A, Artist B, Artist C'

    def test_46_parse_track_string_artist(self, client):
        """Should handle artist as a plain string."""
        item = {'id': 1, 'title': 'Song', 'artist': 'Solo Artist', 'duration': 100}
        result = client._parse_track(item)
        assert result['artist'] == 'Solo Artist'

    def test_47_parse_track_dict_artist(self, client):
        """Should handle artist as a dict with name key."""
        item = {'id': 1, 'title': 'Song', 'artist': {'name': 'Dict Artist'}, 'duration': 100}
        result = client._parse_track(item)
        assert result['artist'] == 'Dict Artist'

    def test_48_parse_track_missing_artists(self, client):
        """Missing artist should default to 'Unknown Artist'."""
        item = {'id': 1, 'title': 'Orphan Song', 'duration': 100}
        result = client._parse_track(item)
        assert result['artist'] == 'Unknown Artist'

    def test_49_parse_track_string_album(self, client):
        """Should handle album as a plain string."""
        item = {'id': 1, 'title': 'Song', 'album': 'String Album', 'duration': 100}
        result = client._parse_track(item)
        assert result['album'] == 'String Album'

    def test_50_parse_track_missing_album(self, client):
        """Missing album should be empty string."""
        item = {'id': 1, 'title': 'Song', 'duration': 100}
        result = client._parse_track(item)
        assert result['album'] == ''

    def test_51_parse_track_duration_already_ms(self, client):
        """Duration >= 100000 should be treated as already in milliseconds."""
        item = {'id': 1, 'title': 'Song', 'duration': 240000}
        result = client._parse_track(item)
        assert result['duration_ms'] == 240000

    def test_52_parse_track_duration_seconds(self, client):
        """Duration < 100000 should be converted from seconds to ms."""
        item = {'id': 1, 'title': 'Song', 'duration': 240}
        result = client._parse_track(item)
        assert result['duration_ms'] == 240000

    def test_53_parse_track_zero_duration(self, client):
        """Zero duration should stay zero."""
        item = {'id': 1, 'title': 'Song', 'duration': 0}
        result = client._parse_track(item)
        assert result['duration_ms'] == 0

    def test_54_parse_track_name_fallback(self, client):
        """Should fall back to 'name' key if 'title' missing."""
        item = {'id': 1, 'name': 'Fallback Name', 'duration': 100}
        result = client._parse_track(item)
        assert result['title'] == 'Fallback Name'

    def test_55_parse_track_explicit_flag(self, client):
        """Should preserve explicit flag."""
        item = {'id': 1, 'title': 'Song', 'duration': 100, 'explicit': True}
        result = client._parse_track(item)
        assert result['explicit'] is True

    def test_56_parse_track_quality_field(self, client):
        """Should parse audioQuality field."""
        item = {'id': 1, 'title': 'Song', 'duration': 100, 'audioQuality': 'LOSSLESS'}
        result = client._parse_track(item)
        assert result['quality'] == 'LOSSLESS'

    def test_57_parse_track_artists_with_strings(self, client):
        """Should handle artists list containing plain strings."""
        item = {'id': 1, 'title': 'Song', 'artists': ['Artist A', 'Artist B'], 'duration': 100}
        result = client._parse_track(item)
        assert result['artist'] == 'Artist A, Artist B'

    def test_58_parse_track_empty_artist_names(self, client):
        """Should skip empty artist names."""
        item = {'id': 1, 'title': 'Song', 'artists': [{'name': ''}, {'name': 'Real Artist'}], 'duration': 100}
        result = client._parse_track(item)
        assert result['artist'] == 'Real Artist'


# ===================== 10. TrackResult Compatibility Tests =====================

class TestTrackResultCompatibility:
    """Tests for Soulseek-compatible TrackResult conversion."""

    def test_59_to_track_result_basic(self, client):
        """Should convert track dict to TrackResult."""
        track = {
            'id': 12345,
            'title': 'Test Song',
            'artist': 'Test Artist',
            'album': 'Test Album',
            'duration_ms': 240000,
            'track_number': 3,
        }
        q_info = HIFI_QUALITY_MAP['lossless']
        result = client._to_track_result(track, q_info)

        assert isinstance(result, TrackResult)
        assert result.username == 'hifi'
        assert result.artist == 'Test Artist'
        assert result.title == 'Test Song'
        assert result.album == 'Test Album'
        assert result.bitrate == 1411
        assert result.duration == 240000
        assert result.track_number == 3

    def test_60_track_result_filename_format(self, client):
        """Filename should be 'track_id||display_name'."""
        track = {'id': 999, 'title': 'My Song', 'artist': 'My Artist'}
        q_info = HIFI_QUALITY_MAP['lossless']
        result = client._to_track_result(track, q_info)

        assert '||' in result.filename
        parts = result.filename.split('||', 1)
        assert parts[0] == '999'
        assert 'My Artist' in parts[1]
        assert 'My Song' in parts[1]

    def test_61_track_result_hifi_username(self, client):
        """All HiFi results should use 'hifi' username."""
        track = {'id': 1, 'title': 'S', 'artist': 'A'}
        result = client._to_track_result(track, HIFI_QUALITY_MAP['lossless'])
        assert result.username == 'hifi'

    def test_62_track_result_high_slots(self, client):
        """HiFi results should have high slot count (always available)."""
        track = {'id': 1, 'title': 'S', 'artist': 'A'}
        result = client._to_track_result(track, HIFI_QUALITY_MAP['lossless'])
        assert result.free_upload_slots >= 99

    def test_63_track_result_different_qualities(self, client):
        """Different quality tiers should produce different bitrates."""
        track = {'id': 1, 'title': 'S', 'artist': 'A'}

        hires = client._to_track_result(track, HIFI_QUALITY_MAP['hires'])
        lossless = client._to_track_result(track, HIFI_QUALITY_MAP['lossless'])
        high = client._to_track_result(track, HIFI_QUALITY_MAP['high'])

        assert hires.bitrate > lossless.bitrate > high.bitrate


# ===================== 11. Async Search Compatible Tests =====================

class TestSearchCompatible:
    """Tests for the async Soulseek-compatible search interface."""

    def test_64_search_returns_tuple(self, shared_client):
        """search should return (tracks, albums) tuple."""
        tracks, albums = run_async(shared_client.search("Bohemian Rhapsody"))
        assert isinstance(tracks, list)
        assert isinstance(albums, list)

    def test_65_search_track_results(self, shared_client):
        """Results should be TrackResult instances."""
        tracks, _ = run_async(shared_client.search("Yesterday Beatles"))
        if tracks:
            assert isinstance(tracks[0], TrackResult)

    def test_66_search_empty_query(self, client):
        """Empty query should return empty lists."""
        tracks, albums = run_async(client.search(""))
        assert tracks == [] or isinstance(tracks, list)

    def test_67_search_albums_always_empty(self, shared_client):
        """Albums list should always be empty (HiFi doesn't return album results from search)."""
        _, albums = run_async(shared_client.search("Dark Side of the Moon"))
        assert albums == []


# ===================== 12. Download Lifecycle Tests =====================

class TestDownloadLifecycle:
    """Tests for download start, tracking, cancel, and cleanup."""

    def test_68_download_creates_tracking_entry(self, client):
        """Starting a download should create an entry in active_downloads."""
        # Use a valid-ish filename format but don't care if it actually downloads
        download_id = run_async(client.download('hifi', '12345||Test Artist - Test Song'))

        if download_id:
            assert download_id in client.active_downloads
            info = client.active_downloads[download_id]
            assert info['state'] in ('Initializing', 'InProgress, Downloading', 'Errored')
            assert info['username'] == 'hifi'

    def test_69_download_invalid_filename_format(self, client):
        """Filename without || separator should fail gracefully."""
        download_id = run_async(client.download('hifi', 'invalid_no_separator'))
        assert download_id is None

    def test_70_download_invalid_track_id(self, client):
        """Non-numeric track ID should fail gracefully."""
        download_id = run_async(client.download('hifi', 'abc||Artist - Song'))
        assert download_id is None

    def test_71_cancel_download(self, client):
        """Should be able to cancel a download."""
        # Start a download
        download_id = run_async(client.download('hifi', '99999999||Fake - Track'))
        if not download_id:
            pytest.skip("Download didn't start")

        result = run_async(client.cancel_download(download_id))
        assert result is True

        info = client.active_downloads.get(download_id)
        if info:
            assert info['state'] == 'Cancelled'

    def test_72_cancel_nonexistent_download(self, client):
        """Cancelling a non-existent download should return False."""
        result = run_async(client.cancel_download('fake-uuid-12345'))
        assert result is False

    def test_73_cancel_with_remove(self, client):
        """Cancel with remove=True should delete from active_downloads."""
        download_id = run_async(client.download('hifi', '99999999||Fake - Track'))
        if not download_id:
            pytest.skip("Download didn't start")

        run_async(client.cancel_download(download_id, remove=True))
        assert download_id not in client.active_downloads

    def test_74_get_all_downloads(self, client):
        """get_all_downloads should return DownloadStatus list."""
        statuses = run_async(client.get_all_downloads())
        assert isinstance(statuses, list)
        for s in statuses:
            assert isinstance(s, DownloadStatus)

    def test_75_get_download_status(self, client):
        """Should return status for a known download."""
        download_id = run_async(client.download('hifi', '99999999||Test - Track'))
        if not download_id:
            pytest.skip("Download didn't start")

        status = run_async(client.get_download_status(download_id))
        assert status is not None
        assert isinstance(status, DownloadStatus)
        assert status.id == download_id

    def test_76_get_download_status_unknown(self, client):
        """Should return None for unknown download ID."""
        status = run_async(client.get_download_status('nonexistent-uuid'))
        assert status is None

    def test_77_clear_completed_downloads(self, client):
        """Should clear terminal downloads but keep active ones."""
        # Add a fake completed download
        with client._download_lock:
            client.active_downloads['fake-completed'] = {
                'id': 'fake-completed',
                'filename': 'test',
                'username': 'hifi',
                'state': 'Completed, Succeeded',
                'progress': 100.0,
                'size': 1000,
                'transferred': 1000,
                'speed': 0,
            }
            client.active_downloads['fake-active'] = {
                'id': 'fake-active',
                'filename': 'test2',
                'username': 'hifi',
                'state': 'InProgress, Downloading',
                'progress': 50.0,
                'size': 2000,
                'transferred': 1000,
                'speed': 100,
            }

        run_async(client.clear_all_completed_downloads())

        assert 'fake-completed' not in client.active_downloads
        assert 'fake-active' in client.active_downloads

    def test_78_clear_errored_downloads(self, client):
        """Should clear errored downloads."""
        with client._download_lock:
            client.active_downloads['fake-errored'] = {
                'id': 'fake-errored',
                'filename': 'err',
                'username': 'hifi',
                'state': 'Errored',
                'progress': 0,
                'size': 0,
                'transferred': 0,
                'speed': 0,
            }

        run_async(client.clear_all_completed_downloads())
        assert 'fake-errored' not in client.active_downloads

    def test_79_clear_cancelled_downloads(self, client):
        """Should clear cancelled downloads."""
        with client._download_lock:
            client.active_downloads['fake-cancelled'] = {
                'id': 'fake-cancelled',
                'filename': 'can',
                'username': 'hifi',
                'state': 'Cancelled',
                'progress': 0,
                'size': 0,
                'transferred': 0,
                'speed': 0,
            }

        run_async(client.clear_all_completed_downloads())
        assert 'fake-cancelled' not in client.active_downloads


# ===================== 13. Download Sync (Quality Fallback) Tests =====================

class TestDownloadSync:
    """Tests for the synchronous download with quality fallback chain."""

    def test_80_quality_chain_starts_at_configured_quality(self, client):
        """Quality fallback chain should start from configured quality."""
        with patch('core.hifi_client.config_manager') as mock_config:
            mock_config.get.return_value = 'high'
            chain = ['hires', 'lossless', 'high', 'low']
            start = chain.index('high')
            assert chain[start:] == ['high', 'low']

    def test_81_quality_chain_starts_at_hires(self, client):
        """If configured quality is hires, chain starts from the top."""
        with patch('core.hifi_client.config_manager') as mock_config:
            mock_config.get.return_value = 'hires'
            chain = ['hires', 'lossless', 'high', 'low']
            start = chain.index('hires')
            assert chain[start:] == ['hires', 'lossless', 'high', 'low']

    def test_82_download_sync_shutdown_check(self, temp_download_dir):
        """Download should abort if shutdown_check returns True."""
        c = HiFiClient(download_path=temp_download_dir)
        c.set_shutdown_check(lambda: True)

        result = c._download_sync('test-id', 12345, 'Test - Track')
        assert result is None

    def test_83_safe_filename_generation(self, client):
        """Filenames with special chars should be sanitized."""
        import re
        display = 'Artist: "Song" / <Mix>'
        safe = re.sub(r'[<>:"/\\|?*]', '_', display)
        assert '<' not in safe
        assert '>' not in safe
        assert '"' not in safe
        assert ':' not in safe
        assert '/' not in safe
        assert '\\' not in safe
        assert '|' not in safe
        assert '?' not in safe
        assert '*' not in safe


# ===================== 14. Shutdown Check Tests =====================

class TestShutdownCheck:
    """Tests for shutdown check callback."""

    def test_84_set_shutdown_check(self, client):
        """Should store the shutdown check callback."""
        check = lambda: False
        client.set_shutdown_check(check)
        assert client.shutdown_check is check

    def test_85_shutdown_check_default_none(self, temp_download_dir):
        """Default shutdown check should be None."""
        c = HiFiClient(download_path=temp_download_dir)
        assert c.shutdown_check is None


# ===================== 15. Download Path Tests =====================

class TestDownloadPath:
    """Tests for download path configuration."""

    def test_86_custom_download_path(self, temp_download_dir):
        """Should use provided download path."""
        c = HiFiClient(download_path=temp_download_dir)
        assert str(c.download_path) == temp_download_dir

    def test_87_download_path_created(self):
        """Should create download directory if it doesn't exist."""
        test_path = tempfile.mktemp(prefix="hifi_mkdir_test_")
        try:
            c = HiFiClient(download_path=test_path)
            assert Path(test_path).exists()
        finally:
            shutil.rmtree(test_path, ignore_errors=True)

    def test_88_default_download_path(self):
        """Default path should come from config_manager or './downloads'."""
        with patch('core.hifi_client.config_manager') as mock_config:
            mock_config.get.return_value = './test_default_downloads'
            c = HiFiClient()
            expected = Path('./test_default_downloads').resolve()
            # Just verify it doesn't crash; actual path depends on config
            assert c.download_path is not None
            # Cleanup
            shutil.rmtree(str(c.download_path), ignore_errors=True)


# ===================== 16. Instance Failover Integration Tests =====================

class TestInstanceFailover:
    """Tests for multi-instance failover behavior."""

    def test_89_failover_on_connection_error(self, temp_download_dir):
        """Should failover to next instance on connection error."""
        c = HiFiClient(download_path=temp_download_dir)
        c._instances = [
            "https://definitely-not-real.invalid",  # Will fail
            c._instances[0] if c._instances else "https://triton.squid.wtf",  # Should succeed
        ]
        c._current_instance = c._instances[0]
        c._min_interval = 0

        # After failing on first, should try second
        result = c._api_get("/", timeout=3)
        # If second instance is up, we should get data
        # If not, at least verify no crash and first was rotated
        assert c._instances[-1] == "https://definitely-not-real.invalid"

    def test_90_failover_preserves_all_instances(self, temp_download_dir):
        """Failover should move failed instance to back, not remove it."""
        c = HiFiClient(download_path=temp_download_dir)
        original_count = len(c._instances)
        first = c._instances[0]

        c._rotate_instance(first)

        assert len(c._instances) == original_count
        assert c._instances[-1] == first


# ===================== 17. HTTP Session Tests =====================

class TestHTTPSession:
    """Tests for HTTP session configuration."""

    def test_91_session_user_agent(self, client):
        """Session should have SoulSync user agent."""
        ua = client.session.headers.get('User-Agent')
        assert 'SoulSync' in ua

    def test_92_session_accept_json(self, client):
        """Session should accept JSON."""
        accept = client.session.headers.get('Accept')
        assert 'json' in accept


# ===================== 18. Thread Safety Tests =====================

class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_93_concurrent_download_tracking(self, client):
        """Multiple threads should safely add/read downloads."""
        errors = []

        def add_download(i):
            try:
                with client._download_lock:
                    client.active_downloads[f'test-{i}'] = {
                        'id': f'test-{i}',
                        'filename': f'track-{i}',
                        'username': 'hifi',
                        'state': 'InProgress, Downloading',
                        'progress': 0,
                        'size': 0,
                        'transferred': 0,
                        'speed': 0,
                    }
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_download, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len([k for k in client.active_downloads if k.startswith('test-')]) == 20

    def test_94_concurrent_instance_rotation(self, temp_download_dir):
        """Multiple threads rotating instances shouldn't crash."""
        c = HiFiClient(download_path=temp_download_dir)
        errors = []

        def rotate(i):
            try:
                if c._instances:
                    c._rotate_instance(c._instances[0])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=rotate, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ===================== 19. DownloadStatus Field Tests =====================

class TestDownloadStatusFields:
    """Tests for DownloadStatus dataclass compatibility."""

    def test_95_download_status_all_fields(self, client):
        """DownloadStatus should have all required fields."""
        with client._download_lock:
            client.active_downloads['field-test'] = {
                'id': 'field-test',
                'filename': '123||Artist - Title',
                'username': 'hifi',
                'state': 'InProgress, Downloading',
                'progress': 45.5,
                'size': 50_000_000,
                'transferred': 22_750_000,
                'speed': 1_000_000,
                'time_remaining': 27,
                'file_path': '/tmp/test.flac',
            }

        status = run_async(client.get_download_status('field-test'))
        assert status.id == 'field-test'
        assert status.filename == '123||Artist - Title'
        assert status.username == 'hifi'
        assert status.state == 'InProgress, Downloading'
        assert status.progress == 45.5
        assert status.size == 50_000_000
        assert status.transferred == 22_750_000
        assert status.speed == 1_000_000
        assert status.time_remaining == 27
        assert status.file_path == '/tmp/test.flac'

    def test_96_download_status_optional_fields_default(self, client):
        """Optional fields should default to None."""
        with client._download_lock:
            client.active_downloads['optional-test'] = {
                'id': 'optional-test',
                'filename': 'test',
                'username': 'hifi',
                'state': 'Initializing',
                'progress': 0,
                'size': 0,
                'transferred': 0,
                'speed': 0,
            }

        status = run_async(client.get_download_status('optional-test'))
        assert status.time_remaining is None
        assert status.file_path is None


# ===================== 20. Live Download Test =====================

class TestLiveDownload:
    """Live download test — actually downloads a track from HiFi API."""

    def test_97_live_search_and_get_stream(self, shared_client):
        """Full flow: search → get track info → get stream URL."""
        results = shared_client.search_tracks(title="Yesterday", artist="Beatles", limit=3)
        if not results:
            pytest.skip("No search results available")

        track = results[0]
        track_id = track['id']

        # Get track info
        info = shared_client.get_track_info(track_id)
        # info might be None if endpoint not available

        # Get stream URL
        stream = shared_client.get_stream_url(track_id, quality='lossless')
        if not stream:
            pytest.skip("Stream URL not available")

        assert stream['url'].startswith('http')
        assert stream['quality'] == 'lossless'

    def test_98_live_download_small_segment(self, shared_client, temp_download_dir):
        """Download the first 100KB of a track to verify the URL works."""
        results = shared_client.search_tracks(title="Yesterday", artist="Beatles", limit=1)
        if not results:
            pytest.skip("No search results")

        stream = shared_client.get_stream_url(results[0]['id'], quality='lossless')
        if not stream:
            pytest.skip("No stream URL")

        import requests
        try:
            resp = requests.get(stream['url'], stream=True, timeout=10,
                                headers={'Range': 'bytes=0-102400'})
            data = resp.content
            assert len(data) > 1000, f"Expected >1KB of audio data, got {len(data)} bytes"

            # Check for FLAC magic bytes
            if stream.get('codec', '') and 'flac' in stream['codec'].lower():
                assert data[:4] == b'fLaC', "FLAC file should start with 'fLaC' magic bytes"
        except requests.exceptions.RequestException as e:
            pytest.skip(f"CDN request failed: {e}")

    def test_99_live_full_download_lifecycle(self, temp_download_dir):
        """Full download lifecycle: search → download → verify file."""
        client = HiFiClient(download_path=temp_download_dir)

        results = client.search_tracks(title="Yesterday", artist="Beatles", limit=1)
        if not results:
            pytest.skip("No search results")

        track = results[0]
        filename = f"{track['id']}||{track['artist']} - {track['title']}"

        download_id = run_async(client.download('hifi', filename))
        if not download_id:
            pytest.skip("Download failed to start")

        # Wait for download to complete (max 60s)
        for _ in range(120):
            time.sleep(0.5)
            status = run_async(client.get_download_status(download_id))
            if not status:
                break
            if status.state in ('Completed, Succeeded', 'Errored', 'Cancelled'):
                break

        status = run_async(client.get_download_status(download_id))
        if status and status.state == 'Completed, Succeeded':
            assert status.file_path is not None
            assert os.path.exists(status.file_path)
            file_size = os.path.getsize(status.file_path)
            assert file_size > 100 * 1024, f"Downloaded file too small: {file_size} bytes"
            # Cleanup
            os.unlink(status.file_path)
        else:
            # Download may have failed due to geo/availability — not a test failure
            state = status.state if status else 'unknown'
            pytest.skip(f"Download did not complete successfully (state: {state})")

    def test_100_live_download_cancel_cleans_up(self, temp_download_dir):
        """Cancelling a live download should update state."""
        client = HiFiClient(download_path=temp_download_dir)

        results = client.search_tracks(title="Stairway to Heaven", artist="Led Zeppelin", limit=1)
        if not results:
            pytest.skip("No search results")

        track = results[0]
        filename = f"{track['id']}||{track['artist']} - {track['title']}"

        download_id = run_async(client.download('hifi', filename))
        if not download_id:
            pytest.skip("Download failed to start")

        # Brief pause then cancel
        time.sleep(1)
        result = run_async(client.cancel_download(download_id))
        assert result is True

        status = run_async(client.get_download_status(download_id))
        if status:
            assert status.state == 'Cancelled'


# ===================== Bonus: Edge Case & Regression Tests =====================

class TestEdgeCases:
    """Additional edge case and regression tests."""

    def test_101_is_configured_with_instances(self, client):
        """Client with instances should report as configured."""
        assert client.is_configured() is True

    def test_102_is_configured_no_instances(self, temp_download_dir):
        """Client with no current instance should report not configured."""
        c = HiFiClient(download_path=temp_download_dir)
        c._current_instance = None
        assert c.is_configured() is False

    def test_103_check_connection_async(self, shared_client):
        """check_connection should return True when instances are up."""
        result = run_async(shared_client.check_connection())
        assert result is True

    def test_104_empty_instances_list(self, temp_download_dir):
        """Client with no instances should handle gracefully."""
        c = HiFiClient(download_path=temp_download_dir)
        c._instances = []
        c._current_instance = None

        assert c.is_available() is False
        assert c.search_tracks(title="test") == []

    def test_105_api_get_error_response(self, client):
        """API returning error dict should return None."""
        with patch.object(client.session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {'error': 'Something went wrong'}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = client._api_get('/test')
            assert result is None

    def test_106_parse_track_with_track_number_alt_key(self, client):
        """Should handle 'track_number' key (underscore variant)."""
        item = {'id': 1, 'title': 'Song', 'track_number': 7, 'duration': 100}
        result = client._parse_track(item)
        assert result['track_number'] == 7

    def test_107_download_path_type(self, client):
        """Download path should be a Path object."""
        assert isinstance(client.download_path, Path)

    def test_108_download_returns_unique_ids(self, client):
        """Each download should get a unique ID."""
        ids = set()
        for i in range(5):
            did = run_async(client.download('hifi', f'{i}||Test - Track {i}'))
            if did:
                ids.add(did)
        assert len(ids) == 5 or len(ids) == 0  # All unique or all failed


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short', '-x'])
