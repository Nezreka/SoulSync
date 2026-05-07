"""Pin the typed-path migration of `_build_single_import_context_payload`.

Same pattern as the `_build_album_info` migration: when the caller
passes a known source, the embedded album blob inside the track
response is dispatched through `Album.from_<source>_dict()` and the
resulting typed Album drives the album_payload. Legacy duck-typed
extraction stays as the fallback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.imports import resolution


SAMPLE_SPOTIFY_TRACK = {
    'id': 'tr1',
    'name': 'HUMBLE.',
    'artists': [{'id': 'kdot', 'name': 'Kendrick Lamar'}],
    'album': {
        'id': 'sp_album',
        'name': 'DAMN.',
        'artists': [{'id': 'kdot', 'name': 'Kendrick Lamar'}],
        'release_date': '2017-04-14',
        'total_tracks': 14,
        'album_type': 'album',
        'images': [{'url': 'https://i.scdn.co/640.jpg', 'height': 640}],
    },
}


def test_typed_path_used_for_known_source():
    payload = resolution._build_single_import_context_payload(
        SAMPLE_SPOTIFY_TRACK,
        source='spotify',
        source_priority=['spotify'],
        requested_title='HUMBLE.',
        requested_artist='Kendrick Lamar',
    )
    album = payload['context']['album']
    assert album['id'] == 'sp_album'
    assert album['name'] == 'DAMN.'
    assert album['release_date'] == '2017-04-14'
    assert album['total_tracks'] == 14
    assert album['album_type'] == 'album'
    assert album['image_url'] == 'https://i.scdn.co/640.jpg'
    assert len(album['images']) == 1


def test_typed_path_preserves_full_images_list():
    track = dict(SAMPLE_SPOTIFY_TRACK)
    track['album'] = dict(SAMPLE_SPOTIFY_TRACK['album'])
    track['album']['images'] = [
        {'url': 'https://i.scdn.co/640.jpg', 'height': 640},
        {'url': 'https://i.scdn.co/300.jpg', 'height': 300},
    ]
    payload = resolution._build_single_import_context_payload(
        track, source='spotify', source_priority=['spotify'],
    )
    images = payload['context']['album']['images']
    assert len(images) == 2
    assert images[0]['url'] == 'https://i.scdn.co/640.jpg'
    assert images[1]['url'] == 'https://i.scdn.co/300.jpg'


def test_legacy_path_used_when_no_source():
    payload = resolution._build_single_import_context_payload(
        SAMPLE_SPOTIFY_TRACK,
        source=None,
        source_priority=[],
    )
    album = payload['context']['album']
    assert album['id'] == 'sp_album'
    assert album['name'] == 'DAMN.'


def test_legacy_path_used_for_unknown_source():
    payload = resolution._build_single_import_context_payload(
        SAMPLE_SPOTIFY_TRACK,
        source='made_up_provider',
        source_priority=['made_up_provider'],
    )
    album = payload['context']['album']
    assert album['id'] == 'sp_album'
    assert album['name'] == 'DAMN.'


def test_legacy_path_used_when_typed_converter_raises():
    def _exploding(_):
        raise RuntimeError('simulated converter bug')

    with patch.dict(resolution._TYPED_ALBUM_CONVERTERS,
                    {'spotify': _exploding}):
        payload = resolution._build_single_import_context_payload(
            SAMPLE_SPOTIFY_TRACK,
            source='spotify',
            source_priority=['spotify'],
        )
    album = payload['context']['album']
    # Legacy path resolves the album fields successfully.
    assert album['id'] == 'sp_album'
    assert album['name'] == 'DAMN.'


@pytest.mark.parametrize('source,track', [
    ('itunes', {
        'id': 1, 'name': 'X', 'artists': [{'name': 'Y'}],
        'album': {'collectionId': 99, 'collectionName': 'iTunes Album',
                  'artistName': 'Artist', 'trackCount': 10},
    }),
    ('deezer', {
        'id': 2, 'name': 'X', 'artists': [{'name': 'Y'}],
        'album': {'id': 99, 'title': 'Deezer Album',
                  'artist': {'name': 'Artist'}, 'nb_tracks': 8},
    }),
    ('discogs', {
        'id': 3, 'name': 'X', 'artists': [{'name': 'Y'}],
        'album': {'id': 99, 'title': 'Discogs Album',
                  'artists': [{'name': 'Artist'}], 'year': 2020},
    }),
])
def test_typed_path_works_for_other_providers(source, track):
    payload = resolution._build_single_import_context_payload(
        track, source=source, source_priority=[source],
    )
    album = payload['context']['album']
    assert album['name']  # populated by the typed converter
    assert album['id']
