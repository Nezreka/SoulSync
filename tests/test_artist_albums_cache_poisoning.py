"""Artist album-list cache poisoning (Boulder: 'Taylor Swift has 8 albums,
nothing before 2022').

get_artist_albums caches its result under an UNQUALIFIED key (no limit/page
info). The watchlist's new-release probe (limit=5, max_pages=1) stored its
truncated page in that slot, so the artist detail page — which reads the
cache — showed only the newest handful of releases for every watchlist
artist. The writer must never cache a fetch that stopped while more pages
existed; complete fetches (even small real discographies) stay cacheable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import core.spotify_client as sc
from core.spotify_client import SpotifyClient


def _album(i):
    return {
        'id': f'al{i}', 'name': f'Album {i}', 'album_type': 'album',
        'artists': [{'id': 'ar1', 'name': 'Taylor Swift'}],
        'release_date': '2024-01-01', 'total_tracks': 12, 'images': [],
        'external_urls': {},
    }


def _client(monkeypatch, pages):
    """Fake sp.artist_albums + sp.next over a list of page dicts."""
    client = SpotifyClient.__new__(SpotifyClient)
    fake = MagicMock()
    fake.artist_albums.return_value = pages[0]
    fake.next.side_effect = pages[1:]
    client.sp = fake
    monkeypatch.setattr(client, 'is_spotify_authenticated', lambda: True)
    monkeypatch.setattr(sc, '_last_api_call_time', 0)
    store_calls = []
    cache = MagicMock()
    cache.get_entity.return_value = None
    cache.store_entity.side_effect = lambda *a, **k: store_calls.append(a)
    monkeypatch.setattr(sc, 'get_metadata_cache', lambda: cache)
    return client, store_calls


def test_truncated_fetch_is_not_cached(monkeypatch):
    # Two pages exist; max_pages=1 stops with a 'next' pending -> truncated.
    pages = [
        {'items': [_album(1), _album(2)], 'next': 'page2-url'},
        {'items': [_album(3)], 'next': None},
    ]
    client, stores = _client(monkeypatch, pages)

    albums = client.get_artist_albums('ar1', limit=5, skip_cache=True, max_pages=1)

    assert len(albums) == 2                      # the probe still works
    album_list_stores = [s for s in stores if s[1] == 'artist']
    assert album_list_stores == []               # but never poisons the slot


def test_complete_fetch_is_cached(monkeypatch):
    pages = [
        {'items': [_album(1), _album(2)], 'next': 'page2-url'},
        {'items': [_album(3)], 'next': None},
    ]
    client, stores = _client(monkeypatch, pages)

    albums = client.get_artist_albums('ar1', limit=50, skip_cache=True, max_pages=0)

    assert len(albums) == 3
    album_list_stores = [s for s in stores if s[1] == 'artist']
    assert len(album_list_stores) == 1           # full discography cached


def test_small_real_discography_with_page_cap_still_cached(monkeypatch):
    # Artist genuinely has one page; max_pages=1 didn't truncate anything.
    pages = [{'items': [_album(1)], 'next': None}]
    client, stores = _client(monkeypatch, pages)

    albums = client.get_artist_albums('ar1', limit=5, skip_cache=True, max_pages=1)

    assert len(albums) == 1
    album_list_stores = [s for s in stores if s[1] == 'artist']
    assert len(album_list_stores) == 1           # complete -> cacheable
