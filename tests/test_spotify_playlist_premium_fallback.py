"""Premium-gated Spotify app owner: playlist endpoint falls back to public fetch.

`GET /api/spotify/playlist/<id>` calls the official Spotify API first. When
Spotify 403s with "Active premium subscription required for the owner of the
app", the endpoint must fall back to the no-auth public path (the same one
SoulSync's own playlist-link import uses) instead of failing. Every other
error must propagate exactly as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402


class _PremiumRequired(Exception):
    """Spotipy-style 403 for an app whose owner lacks Premium."""

    def __init__(self):
        self.http_status = 403
        self.code = -1
        super().__init__(
            "http status: 403, code: -1 - "
            "https://api.spotify.com/v1/playlists/abc: "
            "Active premium subscription required for the owner of the app. "
            "When the subscription status changes, it can take a few hours "
            "before requests are allowed again., reason: None"
        )


class _NotFound(Exception):
    """Spotipy-style 404 — must NOT trigger the public fallback."""

    def __init__(self):
        self.http_status = 404
        super().__init__("http status: 404, code: -1 - No such playlist")


def _public_playlist():
    return {
        'id': 'abc',
        'type': 'playlist',
        'name': 'Public Mix',
        'subtitle': 'Some Curator',
        'tracks': [
            {
                'id': 'trk1',
                'name': 'Song One',
                'artists': [{'name': 'Artist A'}],
                'duration_ms': 180000,
                'is_explicit': False,
                'track_number': 1,
            },
            {
                'id': 'trk2',
                'name': 'Song Two',
                'artists': [{'name': 'Artist B'}],
                'duration_ms': 200000,
                'is_explicit': False,
                'track_number': 2,
            },
        ],
        'url': 'https://open.spotify.com/playlist/abc',
        'url_hash': 'deadbeef1234',
    }


def _authed_client(monkeypatch, playlist_side_effect=None, playlist_return=None):
    client = SimpleNamespace()
    client.is_authenticated = lambda: True
    sp = MagicMock()
    if playlist_side_effect is not None:
        sp.playlist.side_effect = playlist_side_effect
    else:
        sp.playlist.return_value = playlist_return
    client.sp = sp
    monkeypatch.setattr(web_server, 'get_spotify_client_for_profile', lambda: client)
    monkeypatch.setattr(web_server, '_spotify_rate_limited', lambda: False)
    return client


def _stub_public(monkeypatch, *, full=None, embed=None):
    """stub the two public sources the fallback actually calls, so nothing
    reaches spotify. full=None makes the full fetch raise, like it does when
    spotify blocks it, and the embed scraper answers instead."""
    import core.spotify_public_api as public_api
    import core.spotify_public_scraper as scraper

    def _full(*a, **k):
        if full is None:
            raise RuntimeError('full public fetch unavailable')
        return full

    monkeypatch.setattr(public_api, 'fetch_public_playlist_full', _full)
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a: embed if embed is not None else {'error': 'embed down'})


def _call(monkeypatch):
    with web_server.app.test_request_context():
        resp = web_server.get_playlist_tracks('abc')
    # Flask view may return (response, status); normalize.
    if isinstance(resp, tuple):
        resp, status = resp
        assert status == 200
    return resp.get_json()


def test_premium_403_falls_back_to_public(monkeypatch):
    _authed_client(monkeypatch, playlist_side_effect=_PremiumRequired())
    _stub_public(monkeypatch, full=_public_playlist())

    data = _call(monkeypatch)

    assert data['name'] == 'Public Mix'
    assert data['owner'] == 'Some Curator'
    assert data['track_count'] == 2
    assert [t['name'] for t in data['tracks']] == ['Song One', 'Song Two']
    assert data['tracks'][0]['spotify_track_id'] == 'trk1'
    assert data['tracks'][0]['artists'] == [{'name': 'Artist A'}]
    # Shape parity with the official path.
    for key in ('id', 'description', 'public', 'collaborative', 'image_url',
                'snapshot_id', 'incomplete', 'expected_total'):
        assert key in data


def test_non_premium_error_propagates(monkeypatch):
    """A 404 must not trigger the public fallback — it surfaces as an error."""
    _authed_client(monkeypatch, playlist_side_effect=_NotFound())

    with web_server.app.test_request_context():
        resp = web_server.get_playlist_tracks('abc')
    # The view's outer except renders {"error": ...}, 500.
    assert isinstance(resp, tuple)
    body, status = resp
    assert status == 500
    assert 'error' in body.get_json()


def test_official_path_unchanged_on_success(monkeypatch):
    """When the official API works, behavior is byte-for-byte the old path."""
    official = {
        'id': 'abc', 'name': 'Official Mix', 'description': 'desc',
        'owner': {'display_name': 'Owner'}, 'public': True,
        'collaborative': False, 'images': [{'url': 'http://img'}],
        'snapshot_id': 'snap1', 'tracks': {'total': 1},
    }
    client = _authed_client(monkeypatch, playlist_return=official)
    items = {'items': [{
        'track': {
            'id': 'trk9', 'name': 'Hit',
            'artists': [{'name': 'Star'}],
            'album': {'name': 'LP', 'images': []},
            'duration_ms': 210000, 'popularity': 80,
        },
    }], 'next': None}
    client._get_playlist_items_page = lambda pid, limit=100: items

    data = _call(monkeypatch)

    assert data['name'] == 'Official Mix'
    assert data['owner'] == 'Owner'
    assert data['image_url'] == 'http://img'
    assert data['tracks'][0]['spotify_track_id'] == 'trk9'
    assert data['tracks'][0]['album']['name'] == 'LP'
    assert data['incomplete'] is False


def test_public_fallback_failure_still_errors(monkeypatch):
    """Premium 403 + broken public fetch = honest error, not a fake playlist."""
    _authed_client(monkeypatch, playlist_side_effect=_PremiumRequired())
    _stub_public(monkeypatch, embed={'error': 'No tracks found in this Spotify link'})

    with web_server.app.test_request_context():
        resp = web_server.get_playlist_tracks('abc')
    assert isinstance(resp, tuple)
    body, status = resp
    assert status == 500
    assert 'error' in body.get_json()


def test_premium_403_full_fetch_down_uses_embed(monkeypatch):
    """full public fetch fails -> the embed scraper answers, and a 100 track
    result from it is flagged as possibly cut off."""
    _authed_client(monkeypatch, playlist_side_effect=_PremiumRequired())
    embed = _public_playlist()
    embed['tracks'] = [dict(embed['tracks'][0], id=f'trk{i}') for i in range(100)]
    _stub_public(monkeypatch, embed=embed)

    data = _call(monkeypatch)

    assert data['name'] == 'Public Mix'
    assert data['track_count'] == 100
    assert data['incomplete'] is True
