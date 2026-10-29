from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.reconciliation.discovery import (
    search_release_candidates,
    supported_search_context,
)


@pytest.mark.parametrize('artist', ['LIVE', 'Live', 'Lïve'])
def test_generic_local_artist_uses_fitting_provider_and_separate_cache_key(artist):
    album = {'album_title': 'Awake The Best Of Live', 'artist_name': 'Various Artists'}
    files = [{'acoustid': {'recordings': [{'artist': 'Lïve', 'score': .97}]}} for _ in range(19)]
    candidate = (17, 19, 0, 'deezer', '2315791', [], {})
    metadata = {'name': 'Awake: The Best Of Live', 'artists': [{'name': artist}]}
    corrected = supported_search_context(album, files, candidate, metadata)
    assert corrected['artist_name'] == artist
    assert album['artist_name'] == 'Various Artists'
    calls = []

    def search(title, artist_name, limit):
        calls.append((title, artist_name, limit))
        return [] if artist_name == 'Various Artists' else [
            {'id': 'matching-release', 'title': title, 'score': 100},
            {'id': 'unrelated-release', 'title': 'Awake Live in Concert', 'score': 100}]

    context = SimpleNamespace(mb_client=SimpleNamespace(search_release=search))
    cache = {}
    assert search_release_candidates(album, context, cache) == []
    assert search_release_candidates(corrected, context, cache) == [('musicbrainz', 'matching-release')]
    assert search_release_candidates(corrected, context, cache) == [('musicbrainz', 'matching-release')]
    assert len(calls) == 2
    assert all(call[2] == 4 for call in calls)


@pytest.mark.parametrize('problem', ['compilation', 'partial_fit', 'no_clear_pairs', 'different_album', 'conflicting_artist'])
def test_unsupported_provider_context_never_replaces_library_context(problem):
    album = {'album_title': 'Album', 'artist_name': 'Various Artists'}
    files = [{}, {}]
    candidate = (2, 2, 0, 'deezer', 'release', [], {})
    metadata = {'name': 'Album', 'artists': [{'name': 'Artist'}]}
    if problem == 'compilation': metadata['artists'].append({'name': 'Other Artist'})
    if problem == 'partial_fit': candidate = (1, 1, *candidate[2:])
    if problem == 'no_clear_pairs': candidate = (0, *candidate[1:])
    if problem == 'different_album': metadata['name'] = 'Album Live'
    if problem == 'conflicting_artist': album['artist_name'] = 'Different Artist'
    previous = deepcopy(album)
    assert supported_search_context(album, files, candidate, metadata) is album
    assert album == previous


def test_audio_can_corroborate_provider_when_local_artist_is_wrong():
    album = {'album_title': 'Album', 'artist_name': 'Wrong Artist'}
    files = [{'acoustid': {'recordings': [{'artist': 'Artist', 'score': .99}]}} for _ in range(3)]
    candidate = (3, 3, 0, 'deezer', 'release', [], {})
    context = supported_search_context(album, files, candidate, {'name': 'Album', 'artists': [{'name': 'Artist'}]})
    assert context['artist_name'] == 'Artist'
