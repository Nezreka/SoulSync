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
    monkeypatch.setattr(client, '_get_playlist_tracks', lambda pid: [])

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
