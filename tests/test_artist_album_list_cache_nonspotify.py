from __future__ import annotations

from unittest.mock import MagicMock

import core.deezer_client as deezer_mod
import core.discogs_client as discogs_mod
import core.itunes_client as itunes_mod
from core.metadata.artist_album_cache import (
    get_cached_artist_album_items,
    get_cached_artist_album_payload,
    make_artist_album_cache_key,
    store_artist_album_items,
)


class MemoryCache:
    def __init__(self):
        self.entities = {}

    def get_entity(self, source, entity_type, entity_id):
        return self.entities.get((source, entity_type, entity_id))

    def store_entity(self, source, entity_type, entity_id, raw_data):
        self.entities[(source, entity_type, entity_id)] = raw_data

    def store_entities_bulk(self, *args, **kwargs):
        return None


def test_artist_album_cache_helper_round_trips_items_and_payload():
    cache = MemoryCache()

    store_artist_album_items(
        cache,
        'deezer',
        'artist-1',
        [{'id': 'album-1'}],
        album_type='album,single',
        limit=200,
        extra_fields={'artist_name': 'Artist'},
    )

    assert make_artist_album_cache_key('artist-1', 'album,single', 200) == 'artist-1_albums_album_single_200'
    assert make_artist_album_cache_key('artist-1', 'album,single', 200, include_limit=False) == 'artist-1_albums_album_single'
    assert get_cached_artist_album_items(cache, 'deezer', 'artist-1', limit=200) == [{'id': 'album-1'}]
    assert get_cached_artist_album_payload(cache, 'deezer', 'artist-1', limit=200)['artist_name'] == 'Artist'


def test_deezer_artist_albums_reuses_list_cache(monkeypatch):
    cache = MemoryCache()
    monkeypatch.setattr(deezer_mod, 'get_metadata_cache', lambda: cache)
    client = deezer_mod.DeezerClient.__new__(deezer_mod.DeezerClient)
    client._api_get = MagicMock(return_value={
        'data': [{
            'id': 123,
            'title': 'Cached Deezer Album',
            'record_type': 'album',
            'release_date': '2024-01-01',
            'nb_tracks': 10,
            'artist': {'id': 7, 'name': 'Artist'},
        }]
    })

    first = client.get_artist_albums('7', limit=200)
    second = client.get_artist_albums('7', limit=200)

    assert [album.name for album in first] == ['Cached Deezer Album']
    assert [album.name for album in second] == ['Cached Deezer Album']
    client._api_get.assert_called_once()


def test_itunes_artist_albums_reuses_list_cache_and_skips_validation(monkeypatch):
    cache = MemoryCache()
    monkeypatch.setattr(itunes_mod, 'get_metadata_cache', lambda: cache)
    client = itunes_mod.iTunesClient.__new__(itunes_mod.iTunesClient)
    client._lookup = MagicMock(side_effect=[
        [{
            'wrapperType': 'collection',
            'collectionId': 456,
            'collectionName': 'Cached iTunes Album',
            'artistId': 8,
            'artistName': 'Artist',
            'trackCount': 10,
            'collectionExplicitness': 'notExplicit',
            'releaseDate': '2024-01-01T00:00:00Z',
        }]
    ])

    first = client.get_artist_albums('8', limit=200)
    second = client.get_artist_albums('8', limit=200)

    assert [album.name for album in first] == ['Cached iTunes Album']
    assert [album.name for album in second] == ['Cached iTunes Album']
    client._lookup.assert_called_once_with(id='8', entity='album', limit=200)


def test_discogs_artist_albums_reuses_list_cache(monkeypatch):
    cache = MemoryCache()
    monkeypatch.setattr(discogs_mod, 'get_metadata_cache', lambda: cache)
    client = discogs_mod.DiscogsClient.__new__(discogs_mod.DiscogsClient)
    client._api_get = MagicMock(side_effect=[
        {'name': 'Artist'},
        {'releases': [{
            'id': 789,
            'type': 'master',
            'role': 'Main',
            'title': 'Cached Discogs Album',
            'artist': 'Artist',
            'year': 2024,
        }]},
    ])

    first = client.get_artist_albums('9', limit=50)
    second = client.get_artist_albums('9', limit=50)

    assert [album.name for album in first] == ['Cached Discogs Album']
    assert [album.name for album in second] == ['Cached Discogs Album']
    assert client._api_get.call_count == 2


def _deezer_album(i):
    return {
        'id': i, 'title': f'Album {i}', 'record_type': 'album',
        'release_date': '2024-01-01', 'nb_tracks': 10,
        'artist': {'id': 7, 'name': 'Artist'},
    }


def test_deezer_partial_pagination_not_cached(monkeypatch):
    """#853 follow-up: a transient/malformed error mid-pagination must NOT cache a
    partial discography (mirrors Spotify's truncated-fetch guard) — otherwise an
    incomplete album list serves from cache until TTL."""
    cache = MemoryCache()
    monkeypatch.setattr(deezer_mod, 'get_metadata_cache', lambda: cache)
    client = deezer_mod.DeezerClient.__new__(deezer_mod.DeezerClient)

    full_page = {'data': [_deezer_album(i) for i in range(100)]}   # full → forces page 2
    # page 1 ok, page 2 errors (None) → incomplete, on BOTH attempts
    client._api_get = MagicMock(side_effect=[full_page, None, full_page, None])

    first = client.get_artist_albums('7', limit=200)
    assert len(first) == 100                          # page-1 albums still returned

    second = client.get_artist_albums('7', limit=200)
    assert len(second) == 100
    # No partial cache → the second call refetched (4 api calls total), instead of
    # serving a permanently-incomplete discography from cache (which would be 2).
    assert client._api_get.call_count == 4


def test_deezer_complete_multipage_is_cached(monkeypatch):
    """A clean multi-page pagination still caches (guard didn't break the happy path)."""
    cache = MemoryCache()
    monkeypatch.setattr(deezer_mod, 'get_metadata_cache', lambda: cache)
    client = deezer_mod.DeezerClient.__new__(deezer_mod.DeezerClient)

    page1 = {'data': [_deezer_album(i) for i in range(100)]}        # full
    page2 = {'data': [_deezer_album(i) for i in range(100, 150)]}   # short → clean end
    client._api_get = MagicMock(side_effect=[page1, page2])

    first = client.get_artist_albums('7', limit=200)
    assert len(first) == 150
    second = client.get_artist_albums('7', limit=200)               # served from cache
    assert len(second) == 150
    assert client._api_get.call_count == 2                          # no refetch → cached
