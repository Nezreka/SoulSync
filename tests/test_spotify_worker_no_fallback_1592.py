"""#1592: the spotify enrichment worker spent requests on deezer.

the worker searched with the client's default allow_fallback=True, so a miss
on spotify (or spotify free) went on to deezer/itunes. the worker then threw
the answer away because a deezer id isn't a spotify id. now it asks for
spotify only, and spotify free still serves since free IS spotify.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

import core.spotify_free_metadata as _sfm
from core.spotify_client import SpotifyClient
from core.spotify_free_metadata import SpotifyFreeMetadataClient


# ── the worker asks for spotify only ──────────────────────────────────────────

class _Client:
    def __init__(self):
        self.calls = []

    def _record(self, name):
        def fn(*a, **k):
            self.calls.append((name, k))
            return []
        return fn

    def __getattr__(self, name):
        if name.startswith('search_'):
            return self._record(name)
        # get_album / get_track_details: handed to honor_stored_match, stubbed out
        return lambda *a, **k: None


def _worker(monkeypatch):
    import core.spotify_worker as sw
    monkeypatch.setattr(sw, 'honor_stored_match', lambda **k: None)
    w = object.__new__(sw.SpotifyWorker)
    w.db = None
    w.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
    w.client = _Client()
    w._get_existing_id = lambda *a: None
    w._mark_status = lambda *a: None
    return w


def test_artist_search_never_falls_back(monkeypatch):
    w = _worker(monkeypatch)
    w._process_artist({'id': 1, 'name': 'Rone'})
    assert w.client.calls == [('search_artists', {'limit': 5, 'allow_fallback': False})]
    assert w.stats['not_found'] == 1


def test_album_search_never_falls_back(monkeypatch):
    w = _worker(monkeypatch)
    w._process_album_individual({'id': 1, 'name': 'Creatures', 'artist': 'Rone'})
    (name, kwargs), = w.client.calls
    assert name == 'search_albums' and kwargs['allow_fallback'] is False


def test_track_search_never_falls_back(monkeypatch):
    w = _worker(monkeypatch)
    w._process_track_individual({'id': 1, 'name': 'Bye Bye Macadam', 'artist': 'Rone'})
    assert w.client.calls == [('search_tracks', {'limit': 5, 'allow_fallback': False})]


# ── the client: free still serves, deezer is never asked ─────────────────────

class _NoFallback:
    def __getattr__(self, name):
        raise AssertionError(f"fallback source was asked: {name}")


@pytest.fixture
def client():
    """the worker's own client: authed or not, it prefers spotify free."""
    c = SpotifyClient.__new__(SpotifyClient)
    c._prefer_free = True
    c._free_meta_client = SpotifyFreeMetadataClient()
    fake_cache = type('C', (), {'get_search_results': lambda *a, **k: None})()
    with patch.object(SpotifyClient, 'is_spotify_authenticated', return_value=False), \
         patch.object(SpotifyClient, '_fallback', new=_NoFallback()), \
         patch('core.spotify_client.config_manager') as cm, \
         patch('core.spotify_client._is_globally_rate_limited', return_value=False), \
         patch('core.spotify_client.get_metadata_cache', return_value=fake_cache), \
         patch.object(_sfm, 'spotify_free_installed', return_value=True):
        cm.get_spotify_config.return_value = {}
        cm.get.side_effect = lambda k, d=None: d
        yield c


def test_free_still_serves_a_spotify_only_track_search(client):
    client._free_meta_client.search_tracks = lambda q, limit: [{
        'id': '0VjIjW4GlUZAMYd2vXMi3b', 'name': 'Blinding Lights',
        'artists': [{'name': 'The Weeknd', 'id': 'a1'}],
        'album': {'name': 'After Hours', 'id': 'al1', 'images': []},
        'duration_ms': 200040,
    }]
    results = client.search_tracks('The Weeknd Blinding Lights', limit=5, allow_fallback=False)
    assert [t.id for t in results] == ['0VjIjW4GlUZAMYd2vXMi3b']


def test_free_still_serves_a_spotify_only_artist_search(client):
    client._free_meta_client.search_artists = lambda q, limit: [
        {'id': '1Xyo4u8uXC1ZmMpatF05PJ', 'name': 'The Weeknd', 'images': [], 'genres': []}]
    results = client.search_artists('The Weeknd', limit=5, allow_fallback=False)
    assert [a.id for a in results] == ['1Xyo4u8uXC1ZmMpatF05PJ']


@pytest.mark.parametrize('method,kwargs', [
    ('search_tracks', {}),
    ('search_artists', {}),
    ('search_albums', {'artist': 'Rone', 'album': 'Creatures'}),
])
def test_a_free_miss_is_a_miss_not_a_deezer_request(client, method, kwargs):
    client._free_meta_client.search_tracks = lambda q, limit: []
    client._free_meta_client.search_artists = lambda q, limit: []
    client._free_meta_client.search_albums_via_artist = lambda a, b, limit: []
    assert getattr(client, method)('Rone Creatures', limit=5, allow_fallback=False, **kwargs) == []


def test_the_default_still_falls_back_for_everyone_else(client):
    """interactive search keeps its deezer/itunes fallback."""
    client._free_meta_client.search_tracks = lambda q, limit: []
    fallback = types.SimpleNamespace(search_tracks=lambda q, limit: ['deezer hit'])
    with patch.object(SpotifyClient, '_fallback', new=fallback):
        assert client.search_tracks('Rone', limit=5) == ['deezer hit']
