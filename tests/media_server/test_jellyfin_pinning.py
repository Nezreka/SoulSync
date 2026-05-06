"""Phase A pinning tests for JellyfinClient.

Pin the OBSERVABLE BEHAVIOR the engine will dispatch through after
Phase B/C. Jellyfin's connection-readiness check is stricter than
Plex (requires base_url + api_key + user_id + music_library_id all
present), and get_all_artists has a side effect of pre-populating
caches.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.jellyfin_client import JellyfinClient


@pytest.fixture
def jellyfin_client():
    """A bare JellyfinClient with no real connection."""
    client = JellyfinClient.__new__(JellyfinClient)
    client.base_url = None
    client.api_key = None
    client.user_id = None
    client.music_library_id = None
    client._connection_attempted = False
    client._is_connecting = False
    # Initialize the various caches the real __init__ sets up
    client._album_cache = {}
    client._track_cache = {}
    client._artist_cache = {}
    client._cache_populated = False
    client._metadata_only_mode = False
    return client


# ---------------------------------------------------------------------------
# is_connected
# ---------------------------------------------------------------------------


def test_is_connected_returns_false_when_no_credentials(jellyfin_client):
    """Pinning: any of base_url / api_key / user_id / music_library_id
    being None → False. All four must be set."""
    with patch.object(jellyfin_client, 'ensure_connection', return_value=False):
        assert jellyfin_client.is_connected() is False


def test_is_connected_returns_true_only_when_all_four_set(jellyfin_client):
    """Pinning: requires base_url AND api_key AND user_id AND
    music_library_id. Setting only some → still False."""
    jellyfin_client._connection_attempted = True

    # Only some — still False
    jellyfin_client.base_url = 'http://j'
    jellyfin_client.api_key = 'k'
    jellyfin_client.user_id = 'u'
    jellyfin_client.music_library_id = None
    assert jellyfin_client.is_connected() is False

    # All four — True
    jellyfin_client.music_library_id = 'lib'
    assert jellyfin_client.is_connected() is True


# ---------------------------------------------------------------------------
# get_all_artists / get_all_album_ids
# ---------------------------------------------------------------------------


def test_get_all_artists_returns_empty_when_not_connected(jellyfin_client):
    with patch.object(jellyfin_client, 'ensure_connection', return_value=False):
        assert jellyfin_client.get_all_artists() == []


def test_get_all_artists_returns_empty_when_no_music_library_id(jellyfin_client):
    """Pinning: even with ensure_connection True, no music_library_id
    selected → empty (don't crash)."""
    jellyfin_client.music_library_id = None
    with patch.object(jellyfin_client, 'ensure_connection', return_value=True):
        assert jellyfin_client.get_all_artists() == []


def test_get_all_album_ids_returns_set(jellyfin_client):
    """Pinning: returns a set of string Jellyfin GUID ids. Same shape
    semantic as Plex (set of strings) — engine extraction depends on
    uniform set type."""
    jellyfin_client._connection_attempted = True
    jellyfin_client.base_url = 'http://j'
    jellyfin_client.api_key = 'k'
    jellyfin_client.user_id = 'u'
    jellyfin_client.music_library_id = 'lib'

    fake_response_pages = [
        {'Items': [{'Id': 'guid-1'}, {'Id': 'guid-2'}], 'TotalRecordCount': 2},
    ]

    with patch.object(jellyfin_client, 'ensure_connection', return_value=True), \
         patch.object(jellyfin_client, '_make_request', side_effect=fake_response_pages):
        result = jellyfin_client.get_all_album_ids()

    assert isinstance(result, set)
    assert result == {'guid-1', 'guid-2'}
