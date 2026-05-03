"""Regression tests for ``core/lidarr_download_client.py``.

The original implementation had three bugs that prevented Lidarr from
being a viable download source:

1. **File misfiling.** Lidarr grabs whole albums; the user requested a
   specific track. Old code copied every track in the album and reported
   ``imported_files[0]`` as ``file_path`` — almost always pointing to
   track 1, not the user's actual track. Post-processing then tagged
   track 1 with the wrong metadata.
2. **Hardcoded ``metadataProfileId=1``.** On Lidarr installs where the
   user deleted/recreated metadata profiles, that id no longer exists
   and the artist-add API call fails with HTTP 400.
3. **Polling never broke the outer loop on completion.** The inner
   ``break`` only exited the queue iteration, so the outer poll loop
   kept spinning until the 600-poll timeout even after the album was
   imported.

These tests pin the fixed behavior in isolation: pure-function helpers
(title similarity, title extraction, normalization) plus integration
tests of the file-picker that go through ``_api_get`` mocked at the
client boundary so we don't need a live Lidarr instance.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.lidarr_download_client import LidarrDownloadClient


# ---------------------------------------------------------------------------
# Pure-function helpers (no mocking needed)
# ---------------------------------------------------------------------------


def test_extract_wanted_track_title_parses_three_part_display() -> None:
    title = LidarrDownloadClient._extract_wanted_track_title(
'Kendrick Lamar - GNX - wacced out murals',
    )
    assert title == 'wacced out murals'


def test_extract_wanted_track_title_handles_dashes_in_track_name() -> None:
    """Track titles can contain ' - ' themselves (live versions, mixes,
    etc). Rejoin parts[2:] so 'Artist - Album - Track - Live Version'
    returns 'Track - Live Version' as the wanted title."""
    title = LidarrDownloadClient._extract_wanted_track_title(
        'Artist - Album - Some Track - Live Version',
    )
    assert title == 'Some Track - Live Version'


def test_extract_wanted_track_title_returns_empty_for_album_dispatch() -> None:
    """Album-level dispatch (no track in display) → empty string,
    caller falls back to first-file behavior."""
    title = LidarrDownloadClient._extract_wanted_track_title(
'Kendrick Lamar - GNX',
    )
    assert title == ''


def test_extract_wanted_track_title_handles_empty_input() -> None:
    title = LidarrDownloadClient._extract_wanted_track_title('')
    assert title == ''


def test_normalize_for_match_strips_punctuation_and_lowercases() -> None:
    norm = LidarrDownloadClient._normalize_for_match
    assert norm("M.A.A.D City") == 'maad city'
    assert norm("Don't Kill My Vibe") == 'dont kill my vibe'
    assert norm("HUMBLE.") == 'humble'
    assert norm("  Multiple   Spaces  ") == 'multiple spaces'
    assert norm('') == ''


def test_title_similarity_exact_match() -> None:
    sim = LidarrDownloadClient._title_similarity
    assert sim('humble', 'humble') == 1.0


def test_title_similarity_substring_match() -> None:
    sim = LidarrDownloadClient._title_similarity
    # 'mine' fully contained in 'mine taylors version'
    assert sim('mine', 'mine taylors version') == 0.85
    # Reverse direction
    assert sim('mine taylors version', 'mine') == 0.85


def test_title_similarity_token_overlap() -> None:
    """Token overlap ratio for partial matches: shared tokens / union."""
    sim = LidarrDownloadClient._title_similarity
    # 2 shared tokens, 3 total → 2/3 ≈ 0.67
    score = sim('hello world', 'hello cruel world')
    assert 0.6 < score < 0.7


def test_title_similarity_no_overlap() -> None:
    sim = LidarrDownloadClient._title_similarity
    assert sim('completely different', 'unrelated track') == 0.0


def test_title_similarity_empty_inputs() -> None:
    sim = LidarrDownloadClient._title_similarity
    assert sim('', 'something') == 0.0
    assert sim('something', '') == 0.0
    assert sim('', '') == 0.0


# ---------------------------------------------------------------------------
# File-picker (mocks Lidarr API responses)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> LidarrDownloadClient:
    """Construct a client without touching real Lidarr config."""
    c = LidarrDownloadClient(download_path=str(tmp_path / 'downloads'))
    return c


def test_pick_track_file_for_wanted_returns_matching_path(client, tmp_path: Path) -> None:
    """Happy path: tracks list includes the wanted title, trackfile API
    returns a real on-disk path. Picker returns it."""
    real_file = tmp_path / 'wacced.flac'
    real_file.write_bytes(b'audio')

    tracks_response = [
        {'title': 'wacced out murals', 'trackFileId': 42},
        {'title': 'squabble up', 'trackFileId': 43},
    ]
    trackfile_response = {'id': 42, 'path': str(real_file)}

    def _api_get_stub(endpoint, params=None):
        if endpoint == 'track':
            return tracks_response
        if endpoint == 'trackfile/42':
            return trackfile_response
        return None

    with patch.object(client, '_api_get', side_effect=_api_get_stub):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='wacced out murals',
        )
    assert result == str(real_file)


def test_pick_track_file_for_wanted_handles_punctuation_difference(client, tmp_path: Path) -> None:
    """Lidarr says 'm.A.A.d city', user dispatched as 'maad city'.
    Normalization should match them via token-equality after stripping
    punctuation."""
    real_file = tmp_path / 'maad.flac'
    real_file.write_bytes(b'audio')

    tracks_response = [
        {'title': 'm.A.A.d city', 'trackFileId': 7},
    ]

    def _api_get_stub(endpoint, params=None):
        if endpoint == 'track':
            return tracks_response
        if endpoint == 'trackfile/7':
            return {'id': 7, 'path': str(real_file)}
        return None

    with patch.object(client, '_api_get', side_effect=_api_get_stub):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='maad city',
        )
    assert result == str(real_file)


def test_pick_track_file_for_wanted_returns_none_below_threshold(client) -> None:
    """If no track in the album is similar enough to the wanted title,
    return None so caller falls back to first-imported behavior."""
    tracks_response = [
        {'title': 'completely unrelated song', 'trackFileId': 1},
        {'title': 'another unrelated', 'trackFileId': 2},
    ]
    with patch.object(client, '_api_get', return_value=tracks_response):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='wacced out murals',
        )
    assert result is None


def test_pick_track_file_for_wanted_returns_none_for_empty_wanted(client) -> None:
    """Empty wanted_title → return None (album-level dispatch path)."""
    with patch.object(client, '_api_get') as mock_api:
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='',
        )
    assert result is None
    # API never called — saves a roundtrip when we know we don't need it
    mock_api.assert_not_called()


def test_pick_track_file_for_wanted_returns_none_when_track_api_fails(client) -> None:
    """Defensive: if Lidarr's track API returns None or non-list,
    don't crash — return None and let caller fall back."""
    with patch.object(client, '_api_get', return_value=None):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='anything',
        )
    assert result is None


def test_pick_track_file_for_wanted_skips_tracks_without_trackfileid(client, tmp_path: Path) -> None:
    """Tracks not yet downloaded (no trackFileId) must be skipped — only
    consider tracks that actually have an imported file."""
    real_file = tmp_path / 'real.flac'
    real_file.write_bytes(b'audio')

    tracks_response = [
        {'title': 'wacced out murals', 'trackFileId': None},  # not imported
        {'title': 'wacced out murals (alt)', 'trackFileId': 99},
    ]

    def _api_get_stub(endpoint, params=None):
        if endpoint == 'track':
            return tracks_response
        if endpoint == 'trackfile/99':
            return {'id': 99, 'path': str(real_file)}
        return None

    with patch.object(client, '_api_get', side_effect=_api_get_stub):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='wacced out murals',
        )
    # Picked the second track because the first has no trackFileId
    assert result == str(real_file)


def test_pick_track_file_for_wanted_returns_none_when_file_missing_on_disk(client, tmp_path: Path) -> None:
    """Lidarr might claim a path exists but the file was moved/deleted
    between Lidarr's import and our copy. Return None defensively so
    caller falls back."""
    tracks_response = [{'title': 'humble', 'trackFileId': 1}]
    trackfile_response = {'id': 1, 'path': str(tmp_path / 'does_not_exist.flac')}

    def _api_get_stub(endpoint, params=None):
        if endpoint == 'track':
            return tracks_response
        if endpoint == 'trackfile/1':
            return trackfile_response
        return None

    with patch.object(client, '_api_get', side_effect=_api_get_stub):
        result = client._pick_track_file_for_wanted(
            lidarr_album_id=999, wanted_title='humble',
        )
    assert result is None


# ---------------------------------------------------------------------------
# Metadata profile id resolution
# ---------------------------------------------------------------------------


def test_get_metadata_profile_id_returns_first_available(client) -> None:
    """When Lidarr returns a list, pick the first id (matches the
    behavior of `_get_quality_profile_id`)."""
    profiles = [
        {'id': 5, 'name': 'Standard'},
        {'id': 7, 'name': 'Custom'},
    ]
    with patch.object(client, '_api_get', return_value=profiles):
        result = client._get_metadata_profile_id()
    assert result == 5


def test_get_metadata_profile_id_falls_back_to_one_on_api_failure(client) -> None:
    """If the API call returns None (network error / endpoint missing),
    return 1 — preserves the previous hardcode as a safety net."""
    with patch.object(client, '_api_get', return_value=None):
        result = client._get_metadata_profile_id()
    assert result == 1


def test_get_metadata_profile_id_falls_back_when_no_id_field(client) -> None:
    """Defensive against malformed responses (profile dicts without id)."""
    with patch.object(client, '_api_get',
                      return_value=[{'name': 'No Id Field'}]):
        result = client._get_metadata_profile_id()
    assert result == 1


def test_get_metadata_profile_id_falls_back_for_non_list(client) -> None:
    """Lidarr API quirk: some endpoints return dicts instead of lists.
    Don't crash — fall back to 1."""
    with patch.object(client, '_api_get', return_value={'totalCount': 0}):
        result = client._get_metadata_profile_id()
    assert result == 1
