"""Real provider adapters; only their HTTP/cache boundaries are mocked."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import core.deezer_client as deezer
import core.metadata.album_tracks as adapters
from core.metadata.standard_completion import plan_standard_album


class MemoryCache:
    def __init__(self):
        self.entities = {}

    def get_entity(self, source, kind, ident):
        return self.entities.get((source, kind, ident))

    def store_entity(self, source, kind, ident, data):
        self.entities[source, kind, ident] = data

    def store_entities_bulk(self, source, kind, entries, **kwargs):
        for ident, data in entries:
            if not kwargs.get('skip_if_exists') or (source, kind, ident) not in self.entities:
                self.store_entity(source, kind, ident, data)


@pytest.fixture
def provider(monkeypatch):
    cache = MemoryCache()
    monkeypatch.setattr(deezer, 'get_metadata_cache', lambda: cache)
    client = deezer.DeezerClient.__new__(deezer.DeezerClient)
    client.search_artists = Mock(return_value=[SimpleNamespace(id='7', name='Fixture Artist')])
    # /artist/id/albums commonly omits nb_tracks and artist, but has release_date.
    catalogue = [{'id': 10, 'title': 'Fixture Album', 'record_type': 'album', 'release_date': '1990-01-01'},
                 {'id': 11, 'title': 'Fixture Album (Deluxe Edition)', 'record_type': 'album', 'release_date': '2015-01-01'}]
    details = {'id': 10, 'title': 'Fixture Album', 'nb_tracks': 3, 'record_type': 'album',
               'artist': {'id': 7, 'name': 'Fixture Artist'}, 'release_date': '1990-01-01'}
    songs = [{'id': i, 'title': title, 'duration': 180, 'track_position': i, 'disk_number': 1,
              'explicit_lyrics': True, 'artist': {'id': 7, 'name': 'Fixture Artist'}}
             for i, title in enumerate(('First', 'Second', 'Third'), 1)]

    def api(endpoint, *args, **kwargs):
        if endpoint == 'artist/7/albums':
            return {'data': deepcopy(catalogue)}
        if endpoint == 'album/10':
            return deepcopy(details)
        if endpoint == 'album/10/tracks':
            return {'data': deepcopy(songs)}
        raise AssertionError(f'Unexpected provider request: {endpoint}')
    client._api_get = Mock(side_effect=api)
    client.get_album_tracks = Mock(wraps=client.get_album_tracks)
    monkeypatch.setattr(adapters.metadata_registry, 'get_client_for_source', lambda source: client)
    return client, catalogue, details, songs, cache


def plan():
    return plan_standard_album(
        {'artist_name': 'Fixture Artist', 'album_title': 'Fixture Album (Deluxe Edition)'},
        [{'title': 'First', 'duration_ms': 180000}], 'deezer')


def test_deezer_catalogue_dataclasses_and_full_details_are_supported(provider):
    client, _, _, _, _ = provider
    result = plan()
    assert result['status'] == 'ready', result
    assert result['album_id'] == '10'
    assert result['expected_track_count'] == 3
    assert [t['name'] for t in result['missing_tracks']] == ['Second', 'Third']
    assert result['missing_tracks'][0]['duration_ms'] == 180000
    assert result['missing_tracks'][0]['disc_number'] == 1
    assert result['missing_tracks'][0]['artists'] == [{'name': 'Fixture Artist'}]
    client.get_album_tracks.assert_called_once_with('10')  # embedded full list is reused
    assert any(c.args[0] == 'album/10' for c in client._api_get.call_args_list)
    assert not any('11' in c.args[0] for c in client._api_get.call_args_list)  # deluxe never fetched


@pytest.mark.parametrize('mutation,reason', [
    ('missing_count', 'Full expected tracklist unavailable'),
    ('wrong_artist', 'Release artist cannot be verified'),
    ('missing_positions', 'Incomplete or ambiguous track metadata'),
    ('partial_tracks', 'Full expected tracklist unavailable'),
    ('duplicate_ids', 'Duplicate source track IDs'),
])
def test_real_deezer_shape_failures_skip_safely(provider, mutation, reason):
    _, _, details, songs, _ = provider
    if mutation == 'missing_count':
        details.pop('nb_tracks')
    elif mutation == 'wrong_artist':
        details['artist'] = {'id': 8, 'name': 'Other Artist'}
    elif mutation == 'missing_positions':
        for song in songs:
            song.pop('track_position')
    elif mutation == 'partial_tracks':
        songs.pop()
    elif mutation == 'duplicate_ids':
        songs[-1]['id'] = songs[0]['id']
    result = plan()
    assert result['status'] == 'skipped'
    assert reason in result['reason']
    assert not result['proposed_downloads']


def test_deezer_multidisc_track_positions_are_preserved(provider):
    _, _, _, songs, _ = provider
    songs[-1].update(disk_number=2, track_position=1)
    result = plan()
    assert result['status'] == 'ready'
    assert [(t['disc_number'], t['track_number']) for t in result['missing_tracks']] == [(1, 2), (2, 1)]


def test_partial_catalogue_cache_is_not_used_as_full_deezer_album_details(provider):
    client, catalogue, _, _, cache = provider
    cache.store_entity('deezer', 'album', '10', dict(catalogue[0], artist={'name': 'Unknown Artist'}))
    assert plan()['status'] == 'ready'
    assert any(c.args[0] == 'album/10' for c in client._api_get.call_args_list)


def test_offline_deezer_cannot_turn_partial_catalogue_cache_into_standard_reference(provider):
    client, _, _, _, _ = provider
    original = client._api_get.side_effect
    client._api_get.side_effect = lambda endpoint, *a, **k: None if endpoint == 'album/10' else original(endpoint, *a, **k)
    result = plan()
    assert result['status'] == 'skipped'
    assert not result['proposed_downloads']


@pytest.mark.parametrize('source', ['spotify', 'itunes', 'hydrabase'])
@pytest.mark.parametrize('embedded_full', [True, False])
def test_shared_normalized_responses_and_paginated_embedded_tracks(source, embedded_full):
    songs = [{'id': str(i), 'name': n, 'track_number': i, 'duration_ms': 180000}
             for i, n in enumerate(('First', 'Second', 'Third'), 1)]
    album = {'id': 'std', 'name': 'Fixture Album', 'artists': [{'name': 'Fixture Artist'}],
             'album_type': 'album', 'total_tracks': 3, '_source': source,
             'tracks': {'items': songs if embedded_full else songs[:1], 'next': None if embedded_full else 'next-page'}}
    fetch = Mock(return_value={'items': songs, 'next': None})
    result = plan_standard_album({'artist_name': 'Fixture Artist', 'album_title': 'Fixture Album'}, [], source,
                                 fetch_releases=lambda *a, **k: [album], fetch_album=lambda *a: album, fetch_tracks=fetch)
    assert result['status'] == 'ready'
    assert result['expected_track_count'] == 3
    if embedded_full:
        fetch.assert_not_called()
    else:
        fetch.assert_called_once_with(source, 'std')


def test_full_deezer_album_details_still_use_cache(provider):
    client, _, details, _, cache = provider
    cache.store_entity('deezer', 'album', '10', deepcopy(details))
    assert client.get_album('10', include_tracks=False)['total_tracks'] == 3
    client._api_get.assert_not_called()
