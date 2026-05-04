"""Pin the typed-path migration in `core/metadata/discography.py`.

`_build_discography_release_dict` and `_build_artist_detail_release_card`
historically did duck-typed extraction with fallback chains. This pr
routes them through `Album.from_<source>_dict()` when caller supplies
a known source. Legacy duck-typing kicks in as fallback.

These tests pin:
- Typed path used when source is recognized.
- Typed path output matches expected fields the legacy path produced.
- Legacy path runs unchanged when source is empty/unknown OR when
  the typed converter raises.
- Cross-provider parametrized smoke for every registered source.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.metadata import discography


SAMPLE_SPOTIFY_RELEASE = {
    'id': 'sp123',
    'name': 'GNX',
    'artists': [{'id': 'kdot', 'name': 'Kendrick Lamar'}],
    'release_date': '2024-11-22',
    'total_tracks': 12,
    'album_type': 'album',
    'images': [{'url': 'https://i.scdn.co/640.jpg', 'height': 640}],
    'external_urls': {'spotify': 'https://open.spotify.com/album/sp123'},
}


# ---------------------------------------------------------------------------
# _build_discography_release_dict
# ---------------------------------------------------------------------------


def test_typed_path_used_for_known_source():
    out = discography._build_discography_release_dict(
        SAMPLE_SPOTIFY_RELEASE, artist_id='kdot', source='spotify',
    )
    assert out['id'] == 'sp123'
    assert out['name'] == 'GNX'
    assert out['artist_name'] == 'Kendrick Lamar'
    assert out['release_date'] == '2024-11-22'
    assert out['album_type'] == 'album'
    assert out['total_tracks'] == 12
    assert out['image_url'] == 'https://i.scdn.co/640.jpg'
    assert out['external_urls'] == {'spotify': 'https://open.spotify.com/album/sp123'}


def test_legacy_path_used_when_no_source():
    out = discography._build_discography_release_dict(
        SAMPLE_SPOTIFY_RELEASE, artist_id='kdot',
    )
    assert out['id'] == 'sp123'
    assert out['name'] == 'GNX'
    assert out['artist_name'] == 'Kendrick Lamar'


def test_legacy_path_used_for_unknown_source():
    out = discography._build_discography_release_dict(
        SAMPLE_SPOTIFY_RELEASE, artist_id='kdot', source='made_up',
    )
    assert out['id'] == 'sp123'


def test_legacy_path_used_when_typed_converter_raises():
    def _explode(_):
        raise RuntimeError('simulated converter bug')

    with patch.dict(discography._TYPED_ALBUM_CONVERTERS,
                    {'spotify': _explode}):
        out = discography._build_discography_release_dict(
            SAMPLE_SPOTIFY_RELEASE, artist_id='kdot', source='spotify',
        )
    # Legacy path still produced a result.
    assert out['id'] == 'sp123'
    assert out['name'] == 'GNX'


def test_release_with_no_id_returns_none():
    raw = dict(SAMPLE_SPOTIFY_RELEASE)
    raw.pop('id')
    out = discography._build_discography_release_dict(
        raw, artist_id='kdot', source='spotify',
    )
    assert out is None


@pytest.mark.parametrize('source,raw', [
    ('itunes', {
        'collectionId': 1, 'collectionName': 'GNX',
        'artistName': 'Kendrick Lamar', 'trackCount': 12,
    }),
    ('deezer', {
        'id': 1, 'title': 'GNX',
        'artist': {'name': 'Kendrick Lamar'}, 'nb_tracks': 12,
    }),
    ('discogs', {
        'id': 1, 'title': 'GNX',
        'artists': [{'name': 'Kendrick Lamar'}], 'year': 2024,
    }),
    ('musicbrainz', {
        'id': 'mbid', 'title': 'GNX',
        'artist-credit': [{'artist': {'name': 'Kendrick Lamar'}}],
    }),
    ('hydrabase', {
        'id': 'hb', 'name': 'GNX',
        'artists': [{'name': 'Kendrick Lamar'}],
    }),
    ('qobuz', {
        'id': 1, 'title': 'GNX',
        'artist': {'name': 'Kendrick Lamar'}, 'tracks_count': 12,
    }),
])
def test_typed_path_works_for_every_registered_source(source, raw):
    out = discography._build_discography_release_dict(
        raw, artist_id='whatever', source=source,
    )
    assert out is not None
    assert out['name'] == 'GNX'


# ---------------------------------------------------------------------------
# _build_artist_detail_release_card — typed dispatch on raw input
# ---------------------------------------------------------------------------


def test_artist_detail_card_typed_path():
    card = discography._build_artist_detail_release_card(
        SAMPLE_SPOTIFY_RELEASE, source='spotify',
    )
    assert card['id'] == 'sp123'
    assert card['name'] == 'GNX'
    assert card['album_type'] == 'album'
    assert card['year'] == '2024'
    assert card['release_date'] == '2024-11-22'
    assert card['image_url'] == 'https://i.scdn.co/640.jpg'
    assert card['track_count'] == 12


def test_artist_detail_card_legacy_path_no_source():
    """Existing canonical-dict input (no source) takes legacy path."""
    canonical = {
        'id': 'sp123',
        'name': 'GNX',
        'album_type': 'album',
        'release_date': '2024-11-22',
        'image_url': 'https://i.scdn.co/640.jpg',
        'total_tracks': 12,
    }
    card = discography._build_artist_detail_release_card(canonical)
    assert card['id'] == 'sp123'
    assert card['name'] == 'GNX'
    assert card['year'] == '2024'
