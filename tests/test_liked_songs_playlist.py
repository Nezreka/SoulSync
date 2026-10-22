"""Liked Songs virtual-playlist resolution.

wolf39us: the mirrored "Liked" playlist silently failed every refresh with
``Error fetching playlist spotify:liked-songs: http status: 400 ... Unsupported
URL / URI``. There is no real playlist URI behind a user's liked songs — the
web UI invents the virtual id ``spotify:liked-songs`` and Spotify serves the
collection via the saved-tracks endpoint. ``get_playlist_by_id`` (what the
mirrored refresh path resolves stored ids through) must special-case it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.spotify_client import LIKED_SONGS_PLAYLIST_ID, SpotifyClient


def _track(i):
    return SimpleNamespace(
        id=f'trk{i}', name=f'Song {i}', artists=['Artist'],
        album='Album', duration_ms=200_000, image_url=None,
        popularity=10, external_urls=None, preview_url=None,
    )


def _client(monkeypatch, saved):
    client = SpotifyClient.__new__(SpotifyClient)
    client.sp = MagicMock()
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)
    monkeypatch.setattr(client, 'get_saved_tracks', lambda: list(saved))
    monkeypatch.setattr(client, 'get_user_info', lambda: {'display_name': 'Wolf'})
    return client


def test_virtual_liked_songs_id_resolves_from_saved_tracks(monkeypatch):
    client = _client(monkeypatch, [_track(1), _track(2)])
    client.sp.playlist.side_effect = AssertionError(
        'sp.playlist() must not be called for the virtual Liked Songs id')

    pl = client.get_playlist_by_id(LIKED_SONGS_PLAYLIST_ID)

    assert pl is not None
    assert pl.id == LIKED_SONGS_PLAYLIST_ID
    assert pl.name == 'Liked Songs' and pl.owner == 'Wolf'
    assert pl.total_tracks == 2 and len(pl.tracks) == 2
    client.sp.playlist.assert_not_called()


def test_real_playlist_id_still_uses_playlist_endpoint(monkeypatch):
    """Regression guard: normal playlists keep going through sp.playlist()."""
    client = _client(monkeypatch, [])
    client.sp.playlist.return_value = {
        'id': 'pl1', 'name': 'Mix', 'description': '', 'public': True,
        'collaborative': False, 'owner': {'display_name': 'Wolf'},
        'tracks': {'total': 0},
    }
    monkeypatch.setattr(client, '_get_playlist_tracks', lambda pid, **kw: [])

    pl = client.get_playlist_by_id('pl1')

    assert pl is not None and pl.id == 'pl1' and pl.name == 'Mix'
    client.sp.playlist.assert_called_once_with('pl1')


def test_mirrored_adapter_resolves_liked_songs(monkeypatch):
    """The seam that actually failed: SpotifyPlaylistSource.get_playlist — the
    mirrored-playlist refresh path — with the stored virtual id."""
    from core.playlists.sources.spotify import SpotifyPlaylistSource

    client = _client(monkeypatch, [_track(1)])
    client.sp.playlist.side_effect = AssertionError('must not hit the playlist endpoint')
    src = SpotifyPlaylistSource(lambda: client)

    detail = src.get_playlist(LIKED_SONGS_PLAYLIST_ID)

    assert detail is not None
    assert detail.meta.name == 'Liked Songs'
    assert detail.meta.source_playlist_id == LIKED_SONGS_PLAYLIST_ID
    assert len(detail.tracks) == 1
    assert detail.tracks[0].track_name == 'Song 1'
    assert detail.tracks[0].artist_name == 'Artist'


def test_empty_saved_tracks_is_a_failed_refresh_not_an_empty_playlist(monkeypatch):
    """get_saved_tracks swallows fetch errors into [] — indistinguishable from
    'no likes'. A valid-looking EMPTY playlist could make a mirror sync clear
    the server-side copy, so empty must resolve as a failed refresh (None)."""
    client = _client(monkeypatch, [])

    assert client.get_playlist_by_id(LIKED_SONGS_PLAYLIST_ID) is None


def test_virtual_liked_songs_request_list_id_resolves_from_saved_tracks(monkeypatch):
    """wolf39us: request list clone of Liked Songs gets an id like
    ``spotify:liked-songs_req_9c9ed7ff``. get_playlist_by_id must strip
    the _req_ suffix and serve it from saved tracks without calling sp.playlist()."""
    client = _client(monkeypatch, [_track(1), _track(2)])
    client.sp.playlist.side_effect = AssertionError(
        'sp.playlist() must not be called for Liked Songs request list id')

    req_id = f"{LIKED_SONGS_PLAYLIST_ID}_req_9c9ed7ff"
    pl = client.get_playlist_by_id(req_id)

    assert pl is not None
    assert pl.id == LIKED_SONGS_PLAYLIST_ID
    assert pl.name == 'Liked Songs' and pl.owner == 'Wolf'
    assert pl.total_tracks == 2 and len(pl.tracks) == 2
    client.sp.playlist.assert_not_called()


def test_real_playlist_request_list_id_strips_suffix_for_playlist_endpoint(monkeypatch):
    """When a regular playlist is cloned into a request list, its ID has a _req_
    suffix. get_playlist_by_id must clean the suffix before querying Spotify."""
    client = _client(monkeypatch, [])
    client.sp.playlist.return_value = {
        'id': 'pl1', 'name': 'Mix', 'description': '', 'public': True,
        'collaborative': False, 'owner': {'display_name': 'Wolf'},
        'tracks': {'total': 0},
    }
    monkeypatch.setattr(client, '_get_playlist_tracks', lambda pid, **kw: [])

    pl = client.get_playlist_by_id('pl1_req_abcdef12')

    assert pl is not None and pl.id == 'pl1' and pl.name == 'Mix'
    client.sp.playlist.assert_called_once_with('pl1')


def test_mirrored_adapter_resolves_liked_songs_request_list(monkeypatch):
    """SpotifyPlaylistSource.get_playlist with a Liked Songs request list id."""
    from core.playlists.sources.spotify import SpotifyPlaylistSource

    client = _client(monkeypatch, [_track(1)])
    client.sp.playlist.side_effect = AssertionError('must not hit the playlist endpoint')
    src = SpotifyPlaylistSource(lambda: client)

    req_id = f"{LIKED_SONGS_PLAYLIST_ID}_req_9c9ed7ff"
    detail = src.get_playlist(req_id)

    assert detail is not None
    assert detail.meta.name == 'Liked Songs'
    assert len(detail.tracks) == 1
    assert detail.tracks[0].track_name == 'Song 1'


def test_refresh_mirrored_uses_upstream_or_clean_id(monkeypatch):
    """_fetch_detail in refresh_mirrored passes the upstream/clean id to the adapter."""
    from core.automation.handlers.refresh_mirrored import _fetch_detail
    from unittest.mock import MagicMock

    called_with = []
    mock_adapter = MagicMock()
    mock_adapter.refresh_playlist.side_effect = lambda pid: called_with.append(pid) or None

    mock_registry = MagicMock()
    mock_registry.get_source.return_value = mock_adapter

    deps = MagicMock()
    deps.playlist_source_registry = mock_registry

    # Case 1: upstream_playlist_id is set
    pl1 = {
        'source': 'spotify',
        'source_playlist_id': 'spotify:liked-songs_req_9c9ed7ff',
        'upstream_playlist_id': 'spotify:liked-songs',
        'is_request_list': 1,
    }
    _fetch_detail('spotify', pl1['source_playlist_id'], pl1, deps, None)
    assert called_with[-1] == 'spotify:liked-songs'

    # Case 2: upstream_playlist_id is missing (older row) but _req_ suffix exists
    pl2 = {
        'source': 'spotify',
        'source_playlist_id': 'spotify:liked-songs_req_9c9ed7ff',
        'upstream_playlist_id': None,
        'is_request_list': 1,
    }
    _fetch_detail('spotify', pl2['source_playlist_id'], pl2, deps, None)
    assert called_with[-1] == 'spotify:liked-songs'

