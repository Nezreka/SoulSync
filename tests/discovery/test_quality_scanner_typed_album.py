"""Pin the typed-path migration of `_normalize_track_album` in
`core/discovery/quality_scanner.py`.

Quality scanner result normalization now routes the embedded
`track.album` blob through `Album.from_<source>_dict()` when provider
is known. Falls back to legacy duck-typed extraction below.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.discovery import quality_scanner


SPOTIFY_TRACK = {
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
        'images': [{'url': 'https://i.scdn.co/640.jpg'}],
    },
}


def test_typed_path_seeds_album_fields_from_known_provider():
    out = quality_scanner._normalize_track_album(SPOTIFY_TRACK, provider='spotify')
    assert out['name'] == 'DAMN.'
    assert out['album_type'] == 'album'
    assert out['total_tracks'] == 14
    assert out['release_date'] == '2017-04-14'
    assert out['id'] == 'sp_album'


def test_legacy_path_used_when_no_provider():
    out = quality_scanner._normalize_track_album(SPOTIFY_TRACK)
    # Legacy path still resolves album from raw `album` dict.
    assert out['name'] == 'DAMN.'
    assert out['album_type'] == 'album'
    assert out['total_tracks'] == 14


def test_legacy_path_used_for_unknown_provider():
    out = quality_scanner._normalize_track_album(SPOTIFY_TRACK, provider='made_up')
    assert out['name'] == 'DAMN.'


def test_legacy_path_used_when_typed_converter_raises():
    def _explode(_):
        raise RuntimeError('simulated converter bug')

    with patch.dict(quality_scanner._TYPED_ALBUM_CONVERTERS,
                    {'spotify': _explode}):
        out = quality_scanner._normalize_track_album(SPOTIFY_TRACK, provider='spotify')
    # Fell back to legacy — still has the album fields.
    assert out['name'] == 'DAMN.'
    assert out['total_tracks'] == 14


@pytest.mark.parametrize('provider,track', [
    ('itunes', {
        'id': 1, 'name': 'X',
        'album': {'collectionId': 99, 'collectionName': 'iTunes Album',
                  'artistName': 'Artist', 'trackCount': 10},
    }),
    ('deezer', {
        'id': 2, 'name': 'X',
        'album': {'id': 99, 'title': 'Deezer Album',
                  'artist': {'name': 'Artist'}, 'nb_tracks': 8},
    }),
    ('discogs', {
        'id': 3, 'name': 'X',
        'album': {'id': 99, 'title': 'Discogs Album',
                  'artists': [{'name': 'Artist'}], 'year': 2020},
    }),
])
def test_typed_path_works_for_other_providers(provider, track):
    out = quality_scanner._normalize_track_album(track, provider=provider)
    assert out['name']  # populated
