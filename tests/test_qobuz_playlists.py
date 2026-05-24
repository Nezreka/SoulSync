"""Unit tests for QobuzClient playlist + favorites methods.

Covers the Sync-page parity added for github issue #677:
- `get_user_playlists` paginates + normalizes the playlist list
- `get_playlist` paginates the tracklist + normalizes track shape
- `get_playlist` recognizes the virtual `qobuz-favorites` ID and
  dispatches to `get_user_favorite_tracks` (same pattern as Tidal's
  COLLECTION_PLAYLIST_ID)
- `get_user_favorite_tracks_count` reads the cheap count-only path
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List

import pytest


@pytest.fixture
def qobuz_client_module():
    """Import core.qobuz_client with config_manager stubbed to a mutable
    in-memory dict. Snapshots and restores sys.modules entries on
    teardown so downstream tests still see the real config.
    """
    config_state: Dict[str, Any] = {}

    class _StubConfigManager:
        def get(self, key, default=None):
            cur: Any = config_state
            for part in key.split('.'):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return default
            return cur

        def set(self, key, value):
            cur: Any = config_state
            parts = key.split('.')
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value

    original_modules = {
        name: sys.modules.get(name)
        for name in ('config', 'config.settings', 'core.qobuz_client')
    }

    if 'config' not in sys.modules:
        sys.modules['config'] = types.ModuleType('config')
    settings_mod = types.ModuleType('config.settings')
    settings_mod.config_manager = _StubConfigManager()
    sys.modules['config.settings'] = settings_mod

    sys.modules.pop('core.qobuz_client', None)
    try:
        import core.qobuz_client as qobuz_client_module
        yield qobuz_client_module, config_state
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture
def authed_client(qobuz_client_module):
    """A QobuzClient with stub credentials so is_authenticated() returns True."""
    module, config = qobuz_client_module
    config['qobuz'] = {
        'session': {
            'app_id': 'APP-1',
            'app_secret': 'SECRET-1',
            'user_auth_token': 'TOKEN-1',
        }
    }
    client = module.QobuzClient()
    client.reload_credentials()
    assert client.is_authenticated() is True
    return client


def _install_api_responder(client, responder):
    """Replace `_api_request` with a deterministic responder for the test."""
    client._api_request = responder  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# get_user_playlists — pagination + normalization
# ---------------------------------------------------------------------------


def test_get_user_playlists_returns_normalized_metadata(authed_client):
    calls: List[Dict[str, Any]] = []

    def responder(endpoint, params=None):
        calls.append({'endpoint': endpoint, 'params': params})
        return {
            'playlists': {
                'items': [
                    {
                        'id': 1001,
                        'name': 'My Mix',
                        'description': 'on repeat',
                        'is_public': True,
                        'tracks_count': 12,
                        'images': ['https://qobuz.example/cover.jpg'],
                    },
                ],
                'total': 1,
            }
        }

    _install_api_responder(authed_client, responder)
    playlists = authed_client.get_user_playlists()

    assert calls == [{
        'endpoint': 'playlist/getUserPlaylists',
        'params': {'limit': 100, 'offset': 0},
    }]
    assert playlists == [{
        'id': '1001',
        'name': 'My Mix',
        'description': 'on repeat',
        'public': True,
        'track_count': 12,
        'image_url': 'https://qobuz.example/cover.jpg',
        'external_urls': {'qobuz': 'https://play.qobuz.com/playlist/1001'},
    }]


def test_get_user_playlists_paginates_until_total_reached(authed_client):
    # Two pages of 100 each, third page returns empty to verify the loop
    # terminates on `total` rather than waiting for an empty page.
    page_one = [{'id': i, 'name': f'P{i}', 'tracks_count': 0} for i in range(100)]
    page_two = [{'id': 100 + i, 'name': f'P{100 + i}', 'tracks_count': 0} for i in range(50)]
    calls: List[int] = []

    def responder(endpoint, params=None):
        calls.append(params['offset'])
        if params['offset'] == 0:
            return {'playlists': {'items': page_one, 'total': 150}}
        if params['offset'] == 100:
            return {'playlists': {'items': page_two, 'total': 150}}
        return {'playlists': {'items': [], 'total': 150}}

    _install_api_responder(authed_client, responder)
    playlists = authed_client.get_user_playlists()

    assert len(playlists) == 150
    assert calls == [0, 100]  # No third request needed


def test_get_user_playlists_returns_empty_when_unauthenticated(qobuz_client_module):
    module, _ = qobuz_client_module
    client = module.QobuzClient()  # no credentials configured
    assert client.is_authenticated() is False

    def responder(endpoint, params=None):
        raise AssertionError('should not hit the API when unauthenticated')

    _install_api_responder(client, responder)
    assert client.get_user_playlists() == []


# ---------------------------------------------------------------------------
# get_playlist — track pagination + normalization
# ---------------------------------------------------------------------------


def test_get_playlist_normalizes_tracks(authed_client):
    def responder(endpoint, params=None):
        assert endpoint == 'playlist/get'
        return {
            'id': 2002,
            'name': 'Deep Cuts',
            'description': '',
            'is_public': False,
            'tracks_count': 1,
            'images': ['https://qobuz.example/dc.jpg'],
            'tracks': {
                'items': [
                    {
                        'id': 555,
                        'title': 'Forgotten Track',
                        'duration': 240,
                        'parental_warning': True,
                        'performer': {'name': 'Some Artist'},
                        'album': {
                            'title': 'Some Album',
                            'image': {'large': 'https://qobuz.example/art.jpg'},
                        },
                    },
                ],
                'total': 1,
            },
        }

    _install_api_responder(authed_client, responder)
    playlist = authed_client.get_playlist('2002')

    assert playlist is not None
    assert playlist['id'] == '2002'
    assert playlist['name'] == 'Deep Cuts'
    assert playlist['track_count'] == 1
    assert playlist['tracks'] == [{
        'id': '555',
        'name': 'Forgotten Track',
        'artists': ['Some Artist'],
        'album': 'Some Album',
        'duration_ms': 240_000,
        'image_url': 'https://qobuz.example/art.jpg',
        'external_urls': {'qobuz': 'https://play.qobuz.com/track/555'},
        'explicit': True,
    }]


def test_get_playlist_routes_favorites_virtual_id(authed_client):
    """The virtual `qobuz-favorites` ID must dispatch to the favorites
    endpoint rather than the playlist/get endpoint — mirrors Tidal's
    COLLECTION_PLAYLIST_ID pattern."""
    seen_endpoints: List[str] = []

    def responder(endpoint, params=None):
        seen_endpoints.append(endpoint)
        # favorite/getUserFavorites is the only endpoint that should fire
        return {
            'tracks': {
                'items': [
                    {
                        'id': 777,
                        'title': 'Liked Song',
                        'duration': 180,
                        'performer': {'name': 'Loved Artist'},
                        'album': {'title': 'Heart Album', 'image': {'large': 'https://q.example/h.jpg'}},
                    },
                ],
                'total': 1,
            }
        }

    _install_api_responder(authed_client, responder)
    playlist = authed_client.get_playlist(authed_client.QOBUZ_FAVORITES_ID)

    assert playlist is not None
    assert playlist['id'] == authed_client.QOBUZ_FAVORITES_ID
    assert playlist['name'] == authed_client.QOBUZ_FAVORITES_NAME
    assert playlist['track_count'] == 1
    assert playlist['tracks'][0]['name'] == 'Liked Song'
    # Only the favorites endpoint should have been hit — no playlist/get.
    assert seen_endpoints == ['favorite/getUserFavorites']


def test_get_playlist_paginates_track_list(authed_client):
    page_one_tracks = [
        {'id': i, 'title': f'T{i}', 'duration': 100, 'performer': {'name': 'A'}, 'album': {'title': 'Alb', 'image': {}}}
        for i in range(100)
    ]
    page_two_tracks = [
        {'id': 100 + i, 'title': f'T{100 + i}', 'duration': 100, 'performer': {'name': 'A'}, 'album': {'title': 'Alb', 'image': {}}}
        for i in range(25)
    ]
    offsets: List[int] = []

    def responder(endpoint, params=None):
        offsets.append(params['offset'])
        if params['offset'] == 0:
            return {
                'id': 'X', 'name': 'Long', 'description': '', 'is_public': False,
                'tracks_count': 125, 'images': [],
                'tracks': {'items': page_one_tracks, 'total': 125},
            }
        if params['offset'] == 100:
            return {
                'id': 'X', 'name': 'Long', 'description': '', 'is_public': False,
                'tracks_count': 125, 'images': [],
                'tracks': {'items': page_two_tracks, 'total': 125},
            }
        return {'tracks': {'items': [], 'total': 125}}

    _install_api_responder(authed_client, responder)
    playlist = authed_client.get_playlist('X')

    assert playlist is not None
    assert len(playlist['tracks']) == 125
    assert playlist['track_count'] == 125
    assert offsets == [0, 100]


def test_get_playlist_returns_none_when_unauthenticated(qobuz_client_module):
    module, _ = qobuz_client_module
    client = module.QobuzClient()
    assert client.get_playlist('whatever') is None


# ---------------------------------------------------------------------------
# get_user_favorite_tracks + get_user_favorite_tracks_count
# ---------------------------------------------------------------------------


def test_get_user_favorite_tracks_paginates(authed_client):
    def make_items(start, count):
        return [
            {'id': start + i, 'title': f'F{start + i}', 'duration': 200,
             'performer': {'name': 'Fav Artist'},
             'album': {'title': 'Fav Album', 'image': {}}}
            for i in range(count)
        ]

    offsets: List[int] = []

    def responder(endpoint, params=None):
        assert endpoint == 'favorite/getUserFavorites'
        assert params['type'] == 'tracks'
        offsets.append(params['offset'])
        if params['offset'] == 0:
            return {'tracks': {'items': make_items(0, 100), 'total': 130}}
        if params['offset'] == 100:
            return {'tracks': {'items': make_items(100, 30), 'total': 130}}
        return {'tracks': {'items': [], 'total': 130}}

    _install_api_responder(authed_client, responder)
    tracks = authed_client.get_user_favorite_tracks()

    assert len(tracks) == 130
    assert offsets == [0, 100]
    assert tracks[0]['name'] == 'F0'
    assert tracks[-1]['name'] == 'F129'


def test_get_user_favorite_tracks_count_uses_cheap_call(authed_client):
    captured: Dict[str, Any] = {}

    def responder(endpoint, params=None):
        captured['endpoint'] = endpoint
        captured['params'] = params
        return {'tracks': {'items': [], 'total': 4242}}

    _install_api_responder(authed_client, responder)
    count = authed_client.get_user_favorite_tracks_count()

    assert count == 4242
    # Single request with limit=1 — must not iterate the full list.
    assert captured == {
        'endpoint': 'favorite/getUserFavorites',
        'params': {'type': 'tracks', 'limit': 1, 'offset': 0},
    }


def test_get_user_favorite_tracks_count_returns_zero_when_unauthenticated(qobuz_client_module):
    module, _ = qobuz_client_module
    client = module.QobuzClient()
    assert client.get_user_favorite_tracks_count() == 0


# ---------------------------------------------------------------------------
# Track normalization fallbacks — artist resolution chain
# ---------------------------------------------------------------------------


def test_track_normalization_falls_back_to_album_artist(authed_client):
    """When `performer.name` is missing, album.artist.name should win
    over the bare 'Unknown Artist' default."""
    def responder(endpoint, params=None):
        return {
            'id': 'P', 'name': 'p', 'description': '', 'is_public': False,
            'tracks_count': 1, 'images': [],
            'tracks': {
                'items': [{
                    'id': 1, 'title': 'X', 'duration': 10,
                    'album': {
                        'title': 'A',
                        'artist': {'name': 'Album Artist'},
                        'image': {'large': ''},
                    },
                }],
                'total': 1,
            }
        }

    _install_api_responder(authed_client, responder)
    playlist = authed_client.get_playlist('P')
    assert playlist['tracks'][0]['artists'] == ['Album Artist']


def test_track_normalization_uses_unknown_artist_when_all_sources_empty(authed_client):
    def responder(endpoint, params=None):
        return {
            'id': 'P', 'name': 'p', 'description': '', 'is_public': False,
            'tracks_count': 1, 'images': [],
            'tracks': {
                'items': [{'id': 1, 'title': 'X', 'duration': 10}],
                'total': 1,
            }
        }

    _install_api_responder(authed_client, responder)
    playlist = authed_client.get_playlist('P')
    assert playlist['tracks'][0]['artists'] == ['Unknown Artist']
