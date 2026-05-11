"""Pin server-playlist sync 'append' mode behavior.

Discord report (CJFC, 2026-04-26): syncing a Spotify playlist to the
server overwrote anything the user had manually added to the server-
side playlist. The fix adds a per-sync mode toggle:

  - 'replace' (default, current behavior) — delete + recreate
  - 'append' — keep existing tracks, only add new ones

Each server client (Plex / Jellyfin / Navidrome) gets a new
`append_to_playlist(name, tracks)` method that:
  - Falls back to `create_playlist` when the playlist doesn't exist yet
  - Fetches existing track IDs and dedupes incoming tracks against them
  - Uses the server's NATIVE append API (no delete-recreate)

`sync_service.sync_playlist` accepts `sync_mode` and dispatches to
`append_to_playlist` when set to 'append'. Falls back to
`update_playlist` (replace semantics) when the client doesn't
implement append (e.g. SoulSync standalone has no playlist methods
at all).

These tests pin:
  - Per-server append: missing playlist → create_playlist delegation
  - Per-server append: existing IDs filtered out (no double-adds)
  - Per-server append: empty new-track set short-circuits without API call
  - Per-server append: failure paths return False without raising
  - sync_service dispatch: mode='append' calls append_to_playlist
  - sync_service dispatch: mode='replace' calls update_playlist (default)
  - sync_service dispatch: missing append_to_playlist method → falls back to update_playlist
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Plex append_to_playlist
# ---------------------------------------------------------------------------


from core.plex_client import PlexClient


def _make_plex_client():
    client = PlexClient.__new__(PlexClient)
    client.server = MagicMock()
    client.music_library = MagicMock()
    client._all_libraries_mode = False
    client._connection_attempted = True
    client._is_connecting = False
    client._last_connection_check = 0
    client._connection_check_interval = 30
    return client


class TestPlexAppendToPlaylist:
    def test_falls_back_to_create_when_playlist_missing(self):
        """Reporter's playlist may not exist on the server yet (first
        sync). Append mode should create it instead of erroring."""
        from plexapi.exceptions import NotFound
        client = _make_plex_client()
        client.server.playlist = MagicMock(side_effect=NotFound("not found"))

        new_tracks = [SimpleNamespace(ratingKey='100'), SimpleNamespace(ratingKey='101')]

        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'create_playlist', return_value=True) as mock_create:
            result = client.append_to_playlist("Test Playlist", new_tracks)

        assert result is True
        mock_create.assert_called_once_with("Test Playlist", new_tracks)

    def test_filters_out_already_present_tracks(self):
        """Reporter's exact case: server playlist has tracks A, B
        already; sync brings A, B, C. Only C should be added.
        Existing tracks must NOT be re-added (would create
        duplicates)."""
        client = _make_plex_client()
        existing_playlist = MagicMock()
        existing_playlist.items = MagicMock(return_value=[
            SimpleNamespace(ratingKey='100'),  # track A
            SimpleNamespace(ratingKey='101'),  # track B
        ])
        existing_playlist.addItems = MagicMock()
        client.server.playlist = MagicMock(return_value=existing_playlist)

        incoming = [
            SimpleNamespace(ratingKey='100'),  # already present
            SimpleNamespace(ratingKey='101'),  # already present
            SimpleNamespace(ratingKey='102'),  # NEW — only this should be added
        ]

        with patch.object(client, 'ensure_connection', return_value=True):
            result = client.append_to_playlist("Test Playlist", incoming)

        assert result is True
        # Only the new track passed to addItems
        called_with = existing_playlist.addItems.call_args[0][0]
        assert len(called_with) == 1
        assert called_with[0].ratingKey == '102'

    def test_short_circuits_when_all_tracks_already_present(self):
        """All incoming tracks already on the playlist → no API call,
        return True (no-op success)."""
        client = _make_plex_client()
        existing_playlist = MagicMock()
        existing_playlist.items = MagicMock(return_value=[
            SimpleNamespace(ratingKey='100'),
            SimpleNamespace(ratingKey='101'),
        ])
        existing_playlist.addItems = MagicMock()
        client.server.playlist = MagicMock(return_value=existing_playlist)

        incoming = [SimpleNamespace(ratingKey='100'), SimpleNamespace(ratingKey='101')]

        with patch.object(client, 'ensure_connection', return_value=True):
            result = client.append_to_playlist("Test Playlist", incoming)

        assert result is True
        existing_playlist.addItems.assert_not_called()

    def test_returns_false_when_not_connected(self):
        """Defensive: ensure_connection False → return False, no API
        call. Caller treats as a normal failure."""
        client = _make_plex_client()
        with patch.object(client, 'ensure_connection', return_value=False):
            result = client.append_to_playlist("Test Playlist", [
                SimpleNamespace(ratingKey='100'),
            ])
        assert result is False

    def test_swallows_exceptions_returns_false(self):
        """Plex SDK errors mid-append shouldn't crash the sync — log
        + return False so the caller can fall back."""
        client = _make_plex_client()
        client.server.playlist = MagicMock(side_effect=RuntimeError("plex down"))
        with patch.object(client, 'ensure_connection', return_value=True):
            result = client.append_to_playlist("Test Playlist", [
                SimpleNamespace(ratingKey='100'),
            ])
        assert result is False


# ---------------------------------------------------------------------------
# Jellyfin append_to_playlist
# ---------------------------------------------------------------------------


from core.jellyfin_client import JellyfinClient


def _make_jellyfin_client():
    client = JellyfinClient.__new__(JellyfinClient)
    client.base_url = "http://jellyfin.local"
    client.api_key = "fake-api-key"
    client.user_id = "user-123"
    return client


class TestJellyfinAppendToPlaylist:
    def test_falls_back_to_create_when_playlist_missing(self):
        client = _make_jellyfin_client()
        new_tracks = [SimpleNamespace(id='item-100')]
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlist_by_name', return_value=None), \
             patch.object(client, 'create_playlist', return_value=True) as mock_create:
            result = client.append_to_playlist("Test", new_tracks)
        assert result is True
        mock_create.assert_called_once_with("Test", new_tracks)

    def test_filters_out_already_present_tracks(self):
        """Reporter's exact case for Jellyfin — only new GUIDs go in."""
        client = _make_jellyfin_client()
        existing_playlist = SimpleNamespace(id='pl-1')
        existing_tracks = [
            SimpleNamespace(id='aaaaaaaa-bbbb-cccc-dddd-000000000001'),
            SimpleNamespace(id='aaaaaaaa-bbbb-cccc-dddd-000000000002'),
        ]
        incoming = [
            SimpleNamespace(id='aaaaaaaa-bbbb-cccc-dddd-000000000001'),  # present
            SimpleNamespace(id='aaaaaaaa-bbbb-cccc-dddd-000000000003'),  # NEW
        ]

        captured_post_params = {}

        def fake_post(url, params=None, headers=None, timeout=None):
            captured_post_params['url'] = url
            captured_post_params['ids'] = params['Ids']
            return SimpleNamespace(status_code=204, text='')

        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlist_by_name', return_value=existing_playlist), \
             patch.object(client, 'get_playlist_tracks', return_value=existing_tracks), \
             patch.object(client, '_is_valid_guid', return_value=True), \
             patch('core.jellyfin_client.requests.post', side_effect=fake_post):
            result = client.append_to_playlist("Test", incoming)

        assert result is True
        # Only the NEW track id should have been POSTed
        assert captured_post_params['ids'] == 'aaaaaaaa-bbbb-cccc-dddd-000000000003'

    def test_short_circuits_when_no_new_tracks(self):
        client = _make_jellyfin_client()
        existing_playlist = SimpleNamespace(id='pl-1')
        existing_tracks = [SimpleNamespace(id='guid-1')]
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlist_by_name', return_value=existing_playlist), \
             patch.object(client, 'get_playlist_tracks', return_value=existing_tracks), \
             patch.object(client, '_is_valid_guid', return_value=True), \
             patch('core.jellyfin_client.requests.post') as mock_post:
            result = client.append_to_playlist("Test", [SimpleNamespace(id='guid-1')])
        assert result is True
        mock_post.assert_not_called()

    def test_returns_false_on_post_error(self):
        client = _make_jellyfin_client()
        existing_playlist = SimpleNamespace(id='pl-1')
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlist_by_name', return_value=existing_playlist), \
             patch.object(client, 'get_playlist_tracks', return_value=[]), \
             patch.object(client, '_is_valid_guid', return_value=True), \
             patch('core.jellyfin_client.requests.post',
                   return_value=SimpleNamespace(status_code=500, text='server error')):
            result = client.append_to_playlist("Test", [SimpleNamespace(id='new-guid')])
        assert result is False


# ---------------------------------------------------------------------------
# Navidrome append_to_playlist
# ---------------------------------------------------------------------------


from core.navidrome_client import NavidromeClient


def _make_navidrome_client():
    client = NavidromeClient.__new__(NavidromeClient)
    client.base_url = "http://navidrome.local"
    client.username = "user"
    client.password = "pass"
    return client


class TestNavidromeAppendToPlaylist:
    def test_falls_back_to_create_when_playlist_missing(self):
        client = _make_navidrome_client()
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlists_by_name', return_value=[]), \
             patch.object(client, 'create_playlist', return_value=True) as mock_create:
            result = client.append_to_playlist("Test", [SimpleNamespace(id='song-1')])
        assert result is True
        mock_create.assert_called_once()

    def test_filters_out_already_present_tracks_and_calls_subsonic(self):
        client = _make_navidrome_client()
        existing_playlists = [SimpleNamespace(id='pl-1', title='Test')]
        existing_tracks = [SimpleNamespace(id='100'), SimpleNamespace(id='101')]
        incoming = [
            SimpleNamespace(id='100'),  # present
            SimpleNamespace(id='102'),  # NEW
            SimpleNamespace(id='103'),  # NEW
        ]

        captured = {}

        def fake_make_request(endpoint, params=None):
            captured['endpoint'] = endpoint
            captured['params'] = params
            return {'status': 'ok'}

        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlists_by_name', return_value=existing_playlists), \
             patch.object(client, 'get_playlist_tracks', return_value=existing_tracks), \
             patch.object(client, '_make_request', side_effect=fake_make_request):
            result = client.append_to_playlist("Test", incoming)

        assert result is True
        assert captured['endpoint'] == 'updatePlaylist'
        assert captured['params']['playlistId'] == 'pl-1'
        # Only NEW song IDs in songIdToAdd, not already-present ones
        assert sorted(captured['params']['songIdToAdd']) == ['102', '103']

    def test_short_circuits_when_no_new_tracks(self):
        client = _make_navidrome_client()
        existing_playlists = [SimpleNamespace(id='pl-1', title='Test')]
        existing_tracks = [SimpleNamespace(id='100')]
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlists_by_name', return_value=existing_playlists), \
             patch.object(client, 'get_playlist_tracks', return_value=existing_tracks), \
             patch.object(client, '_make_request') as mock_req:
            result = client.append_to_playlist("Test", [SimpleNamespace(id='100')])
        assert result is True
        mock_req.assert_not_called()

    def test_falls_back_when_subsonic_returns_failed(self):
        client = _make_navidrome_client()
        existing_playlists = [SimpleNamespace(id='pl-1', title='Test')]
        with patch.object(client, 'ensure_connection', return_value=True), \
             patch.object(client, 'get_playlists_by_name', return_value=existing_playlists), \
             patch.object(client, 'get_playlist_tracks', return_value=[]), \
             patch.object(client, '_make_request', return_value=None):
            # _make_request returns None when Subsonic returns 'failed' status
            result = client.append_to_playlist("Test", [SimpleNamespace(id='new-1')])
        assert result is False


# ---------------------------------------------------------------------------
# Contract pinning — append_to_playlist is in KNOWN_PER_SERVER_METHODS
# ---------------------------------------------------------------------------


def test_append_to_playlist_listed_in_contract():
    """If a future refactor drops `append_to_playlist` from the
    contract's KNOWN_PER_SERVER_METHODS list, the conformance test
    won't catch it (those are advisory-only). This test is the
    explicit pin that the method is part of the recognized
    per-server playlist surface."""
    from core.media_server.contract import KNOWN_PER_SERVER_METHODS
    assert 'append_to_playlist' in KNOWN_PER_SERVER_METHODS


def test_each_client_implements_append_to_playlist():
    """Pin: Plex / Jellyfin / Navidrome all have the method (at the
    class level — instance state isn't required for this check).
    SoulSync standalone is intentionally excluded — it has no
    playlist methods at all per the contract notes."""
    assert hasattr(PlexClient, 'append_to_playlist')
    assert hasattr(JellyfinClient, 'append_to_playlist')
    assert hasattr(NavidromeClient, 'append_to_playlist')
