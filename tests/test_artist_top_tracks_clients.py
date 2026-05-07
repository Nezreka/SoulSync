"""Tests for the new artist top-tracks client methods.

Issue #513: surface an artist's "top X popular songs" for one-click download
without pulling the entire discography. Spotify and Deezer expose this via
native APIs; iTunes / Discogs / MusicBrainz don't, so the frontend falls
back to the existing Last.fm display-only sidebar.

Scope: client methods only. The Flask endpoint that wraps them is small
enough (source dispatch + DB id resolution + JSON response) that
exercising the underlying client methods is the load-bearing test layer.
A full-app Flask test client wasn't worth pulling in here — importing
``web_server`` at test-collection time spins up worker threads that race
with caplog-using tests elsewhere in the suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.deezer_client import DeezerClient
from core.spotify_client import SpotifyClient


# ---------------------------------------------------------------------------
# Spotify client method
# ---------------------------------------------------------------------------


def test_spotify_get_artist_top_tracks_returns_track_list(monkeypatch):
    """Wraps spotipy's `artist_top_tracks` and returns the `tracks` array."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake_sp = MagicMock()
    fake_sp.artist_top_tracks.return_value = {
        'tracks': [
            {'id': 't1', 'name': 'Song A', 'artists': [{'name': 'Artist'}]},
            {'id': 't2', 'name': 'Song B', 'artists': [{'name': 'Artist'}]},
            {'id': 't3', 'name': 'Song C', 'artists': [{'name': 'Artist'}]},
        ]
    }
    client.sp = fake_sp
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)

    result = client.get_artist_top_tracks('artist_id', country='US', limit=10)

    assert len(result) == 3
    assert [t['id'] for t in result] == ['t1', 't2', 't3']
    fake_sp.artist_top_tracks.assert_called_once_with('artist_id', country='US')


def test_spotify_get_artist_top_tracks_honors_ui_limit(monkeypatch):
    """Spotify always returns up to 10 tracks; the limit param is a UI trim only."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake_sp = MagicMock()
    fake_sp.artist_top_tracks.return_value = {
        'tracks': [{'id': f't{i}', 'name': f'Song {i}'} for i in range(10)]
    }
    client.sp = fake_sp
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)

    result = client.get_artist_top_tracks('artist_id', limit=3)
    assert len(result) == 3


def test_spotify_get_artist_top_tracks_returns_empty_when_unauthed(monkeypatch):
    """No API call should fire when Spotify isn't authenticated. Lets the
    endpoint return `success=False, reason=spotify_not_authenticated`
    instead of throwing."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake_sp = MagicMock()
    client.sp = fake_sp
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: False)

    result = client.get_artist_top_tracks('artist_id')
    assert result == []
    fake_sp.artist_top_tracks.assert_not_called()


def test_spotify_get_artist_top_tracks_returns_empty_when_artist_id_missing(monkeypatch):
    """Defensive guard — no API call for empty/None artist id."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake_sp = MagicMock()
    client.sp = fake_sp
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)

    assert client.get_artist_top_tracks('') == []
    assert client.get_artist_top_tracks(None) == []
    fake_sp.artist_top_tracks.assert_not_called()


def test_spotify_get_artist_top_tracks_swallows_api_errors(monkeypatch):
    """Network/auth exceptions surface as empty list, not a crash —
    the endpoint relies on this to fall through gracefully."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake_sp = MagicMock()
    fake_sp.artist_top_tracks.side_effect = RuntimeError("boom")
    client.sp = fake_sp
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)

    result = client.get_artist_top_tracks('artist_id')
    assert result == []


# ---------------------------------------------------------------------------
# Deezer client method
# ---------------------------------------------------------------------------


def test_deezer_get_artist_top_tracks_returns_spotify_compatible_shape(monkeypatch):
    """Deezer's raw shape gets converted to the same dict layout the
    Spotify endpoint produces (id, name, artists, album, duration_ms,
    track_number, etc) so downstream code doesn't need to branch."""
    client = DeezerClient()

    raw_response = {
        'data': [
            {
                'id': 1001,
                'title': 'Some Hit',
                'duration': 200,  # seconds
                'rank': 850000,
                'preview': 'https://example/preview.mp3',
                'link': 'https://deezer.com/track/1001',
                'track_position': 3,
                'disk_number': 1,
                'explicit_lyrics': False,
                'artist': {'id': 50, 'name': 'Test Artist'},
                'album': {
                    'id': 200, 'title': 'Greatest Hits',
                    'cover_xl': 'https://example/cover_xl.jpg',
                    'cover_big': 'https://example/cover_big.jpg',
                },
            },
        ]
    }
    monkeypatch.setattr(client, '_api_get', lambda path, params=None: raw_response)

    tracks = client.get_artist_top_tracks('50', limit=5)

    assert len(tracks) == 1
    t = tracks[0]
    assert t['id'] == '1001'
    assert t['name'] == 'Some Hit'
    assert t['duration_ms'] == 200_000  # converted to ms
    assert t['track_number'] == 3
    assert t['disc_number'] == 1
    assert t['artists'] == [{'id': '50', 'name': 'Test Artist'}]
    assert t['album']['id'] == '200'
    assert t['album']['name'] == 'Greatest Hits'
    assert t['album']['album_type'] == 'album'
    assert any(img['url'] == 'https://example/cover_xl.jpg' for img in t['album']['images'])
    assert t['_source'] == 'deezer'


def test_deezer_get_artist_top_tracks_empty_when_no_data(monkeypatch):
    """Missing artist or empty response → empty list. Endpoint relies on
    this to report `success=False, reason=no_tracks_found`."""
    client = DeezerClient()
    monkeypatch.setattr(client, '_api_get', lambda path, params=None: None)

    assert client.get_artist_top_tracks('50') == []


def test_deezer_get_artist_top_tracks_empty_when_artist_id_missing(monkeypatch):
    """Defensive guard — no API call for empty artist id."""
    client = DeezerClient()
    called = {'count': 0}

    def fake_api(*args, **kwargs):
        called['count'] += 1
        return {'data': []}

    monkeypatch.setattr(client, '_api_get', fake_api)

    assert client.get_artist_top_tracks('') == []
    assert called['count'] == 0


def test_deezer_get_artist_top_tracks_clamps_limit(monkeypatch):
    """Limit param gets clamped at the upper bound (Deezer's max ~100)
    and falls back to the default when the caller passes 0/None."""
    client = DeezerClient()
    captured = {}

    def fake_api(path, params=None):
        captured['params'] = params
        return {'data': []}

    monkeypatch.setattr(client, '_api_get', fake_api)

    # Excessive limit clamped down to 100
    client.get_artist_top_tracks('50', limit=10000)
    assert captured['params']['limit'] == 100

    # 0 → falsy, falls back to default 10 (better than 1 — caller probably
    # wanted "give me a sensible top-N", not "give me a single track")
    client.get_artist_top_tracks('50', limit=0)
    assert captured['params']['limit'] == 10

    # Small valid limit passes through
    client.get_artist_top_tracks('50', limit=3)
    assert captured['params']['limit'] == 3


def test_deezer_get_artist_top_tracks_skips_malformed_entries(monkeypatch):
    """Defensive — non-dict entries in the response array get filtered out
    rather than crashing the loop."""
    client = DeezerClient()
    monkeypatch.setattr(client, '_api_get', lambda path, params=None: {
        'data': [
            None,  # malformed: skipped
            {'id': 1, 'title': 'Real', 'artist': {'name': 'A'}, 'album': {}},
            'not a dict',  # malformed: skipped
        ]
    })

    tracks = client.get_artist_top_tracks('50')
    assert len(tracks) == 1
    assert tracks[0]['name'] == 'Real'
