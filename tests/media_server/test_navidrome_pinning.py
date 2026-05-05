"""Phase A pinning tests for NavidromeClient.

Pin the OBSERVABLE BEHAVIOR the engine will dispatch through. Auth
shape uses base_url + username + password (no token like Plex).
Both get_all_artists + get_all_album_ids return empty when the
client isn't connected.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.navidrome_client import NavidromeClient


@pytest.fixture
def nav_client():
    """A bare NavidromeClient with no real connection."""
    client = NavidromeClient.__new__(NavidromeClient)
    client.base_url = None
    client.username = None
    client.password = None
    client._connection_attempted = False
    client._is_connecting = False
    client._artist_cache = {}
    client._album_cache = {}
    client._track_cache = {}
    client._folder_album_ids = None
    client._progress_callback = None
    client.music_folder_id = None
    return client


def test_is_connected_returns_false_without_credentials(nav_client):
    with patch.object(nav_client, 'ensure_connection', return_value=False):
        assert nav_client.is_connected() is False


def test_is_connected_returns_true_when_all_three_set(nav_client):
    """Pinning: requires base_url AND username AND password.
    No token model (vs Plex). Salt is generated per-request."""
    nav_client._connection_attempted = True
    nav_client.base_url = 'http://nav'
    nav_client.username = 'u'
    nav_client.password = 'p'
    assert nav_client.is_connected() is True


def test_get_all_artists_returns_empty_when_not_connected(nav_client):
    with patch.object(nav_client, 'ensure_connection', return_value=False):
        assert nav_client.get_all_artists() == []


def test_get_all_album_ids_returns_set(nav_client):
    """Pinning: returns a set of string Navidrome ids. Same shape
    semantic as Plex/Jellyfin (set of strings) — engine extraction
    depends on uniform set type."""
    nav_client._connection_attempted = True
    nav_client.base_url = 'http://nav'
    nav_client.username = 'u'
    nav_client.password = 'p'

    # Navidrome uses paginated getAlbumList2 — first page returns
    # 2 albums, second returns empty (terminates loop).
    # _make_request unwraps the subsonic-response envelope and
    # returns the body. get_all_album_ids reads response.albumList2.album.
    page_responses = [
        {'albumList2': {'album': [{'id': 'nav-1'}, {'id': 'nav-2'}]}},
        {'albumList2': {'album': []}},
    ]

    with patch.object(nav_client, 'ensure_connection', return_value=True), \
         patch.object(nav_client, '_make_request', side_effect=page_responses):
        result = nav_client.get_all_album_ids()

    assert isinstance(result, set)
    assert result == {'nav-1', 'nav-2'}
