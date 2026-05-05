"""Phase A pinning tests for PlexClient.

Pin the OBSERVABLE BEHAVIOR the engine will dispatch through after
Phase B/C. The web_server.py dispatch sites + DatabaseUpdateWorker
read these methods generically — they must keep their current shape
through the engine refactor.

Plex's surface is wider than the contract requires (additional
playlist / metadata writeback methods), but pinning ALL of them
turns into ~30 tests. Focused on the methods the dispatch sites
actually call: is_connected, is_fully_configured, ensure_connection,
get_all_artists, get_all_album_ids, search_tracks, trigger_library_scan,
is_library_scanning, get_library_stats.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.plex_client import PlexClient


@pytest.fixture
def plex_client():
    """A bare PlexClient with no real connection. Tests that need
    a connected state set client.server = MagicMock() directly."""
    client = PlexClient.__new__(PlexClient)
    client.server = None
    client.music_library = None
    client._connection_attempted = False
    client._is_connecting = False
    client._last_connection_check = 0
    client._connection_check_interval = 30
    return client


# ---------------------------------------------------------------------------
# is_connected
# ---------------------------------------------------------------------------


def test_is_connected_returns_false_when_no_server(plex_client):
    """Pinning: no server object → False. Dashboard status indicators
    + endpoint guards depend on this."""
    plex_client.server = None
    # Patch ensure_connection to avoid network call
    with patch.object(plex_client, 'ensure_connection', return_value=False):
        assert plex_client.is_connected() is False


def test_is_connected_returns_true_when_server_present(plex_client):
    """Pinning: a non-None server object → True (even if music_library
    is unset). is_fully_configured() is the stricter check."""
    plex_client.server = MagicMock()
    plex_client._connection_attempted = True
    assert plex_client.is_connected() is True


def test_is_fully_configured_requires_server_and_music_library(plex_client):
    """Pinning: is_fully_configured == True only when BOTH server AND
    music_library are set. Used by playlist-sync gating."""
    plex_client.server = MagicMock()
    plex_client.music_library = None
    assert plex_client.is_fully_configured() is False
    plex_client.music_library = MagicMock()
    assert plex_client.is_fully_configured() is True


# ---------------------------------------------------------------------------
# get_all_artists / get_all_album_ids
# ---------------------------------------------------------------------------


def test_get_all_artists_returns_empty_when_not_connected(plex_client):
    """Pinning: ensure_connection returning False → empty list (not
    exception). Caller iterates the result, never raising."""
    with patch.object(plex_client, 'ensure_connection', return_value=False):
        assert plex_client.get_all_artists() == []


def test_get_all_artists_iterates_searchartists_when_connected(plex_client):
    """Pinning: when connected, get_all_artists calls
    music_library.searchArtists() and returns the result list."""
    fake_artists = [MagicMock(title='A'), MagicMock(title='B')]
    plex_client.server = MagicMock()
    plex_client.music_library = MagicMock()
    plex_client.music_library.searchArtists.return_value = fake_artists

    with patch.object(plex_client, 'ensure_connection', return_value=True):
        result = plex_client.get_all_artists()

    assert result == fake_artists


def test_get_all_album_ids_returns_set_of_string_ratingkeys(plex_client):
    """Pinning: returns a set (not a list) of string ratingKey values.
    DatabaseUpdateWorker uses set membership for diff-detection.
    NOTE: Plex stores ratingKey as int but the method coerces to str
    so the set semantics match other servers (Jellyfin GUIDs, Navidrome
    string ids). Engine refactor must preserve."""
    fake_albums = [MagicMock(ratingKey=1), MagicMock(ratingKey=2),
                   MagicMock(ratingKey=3)]
    plex_client.server = MagicMock()
    plex_client.music_library = MagicMock()
    plex_client.music_library.albums.return_value = fake_albums

    with patch.object(plex_client, 'ensure_connection', return_value=True):
        result = plex_client.get_all_album_ids()

    assert isinstance(result, set)
    assert result == {'1', '2', '3'}
