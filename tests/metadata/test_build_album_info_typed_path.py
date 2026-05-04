"""Pin the typed-path migration of `_build_album_info`.

`core/metadata/album_tracks.py:_build_album_info` historically did
duck-typed extraction with fallback chains. Step 2 of the typed
metadata migration routes it through `Album.from_<source>_dict()`
when the caller provides a recognized `source` argument; legacy
duck-typing kicks in as fallback.

These tests pin:
- Typed path is taken when `source` is a known provider.
- Output matches the legacy path on the fields the legacy code
  produced (the real concern — downstream consumers must not break).
- Legacy path still runs unchanged when `source` is empty/unknown,
  or when the typed converter raises.
- Caller-provided `album_id` / `album_name` / `artist_name`
  fallbacks apply on the typed path the same way they did on the
  legacy path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.metadata import album_tracks


# ---------------------------------------------------------------------------
# Typed path is exercised when source is recognized
# ---------------------------------------------------------------------------


SAMPLE_SPOTIFY_ALBUM = {
    'id': 'sp123',
    'name': 'GNX',
    'artists': [{'id': 'kdot', 'name': 'Kendrick Lamar'}],
    'release_date': '2024-11-22',
    'total_tracks': 12,
    'album_type': 'album',
    'images': [
        {'url': 'https://i.scdn.co/640.jpg', 'height': 640, 'width': 640},
        {'url': 'https://i.scdn.co/300.jpg', 'height': 300, 'width': 300},
    ],
    'genres': ['hip hop'],
    'label': 'pgLang',
}


def test_typed_path_used_for_known_source():
    info = album_tracks._build_album_info(
        SAMPLE_SPOTIFY_ALBUM, album_id='sp123',
        album_name='', artist_name='', source='spotify',
    )
    # Typed converter populates `source` field — legacy path doesn't.
    assert info['source'] == 'spotify'
    # Typed converter exposes label / genres — legacy doesn't.
    assert info['label'] == 'pgLang'
    assert info['genres'] == ['hip hop']
    # Core fields match expected values.
    assert info['id'] == 'sp123'
    assert info['name'] == 'GNX'
    assert info['artist'] == 'Kendrick Lamar'
    assert info['artist_name'] == 'Kendrick Lamar'
    assert info['artist_id'] == 'kdot'
    assert info['release_date'] == '2024-11-22'
    assert info['album_type'] == 'album'
    assert info['total_tracks'] == 12


def test_typed_path_preserves_full_images_list():
    """Legacy code passed the source's full multi-resolution images
    list through verbatim. Some downstream consumers iterate it to
    pick a different size. Typed path must preserve this."""
    info = album_tracks._build_album_info(
        SAMPLE_SPOTIFY_ALBUM, album_id='sp123', source='spotify',
    )
    assert len(info['images']) == 2
    assert info['images'][0]['url'] == 'https://i.scdn.co/640.jpg'
    assert info['images'][1]['url'] == 'https://i.scdn.co/300.jpg'


def test_typed_path_applies_caller_fallbacks_for_missing_fields():
    """When the raw response lacks id/name/artist, the legacy code
    used the caller-provided defaults. Typed path must do the same."""
    minimal = {'name': 'X'}  # no id, no artists
    info = album_tracks._build_album_info(
        minimal, album_id='fallback_id', album_name='fallback_name',
        artist_name='Fallback Artist', source='spotify',
    )
    assert info['id'] == 'fallback_id'
    assert info['artist'] == 'Fallback Artist'
    assert info['artist_name'] == 'Fallback Artist'
    # artists list reflects the caller-provided name (id may be None on
    # the typed path since no id was discoverable in raw data — legacy
    # used '' but no consumer differentiates None vs '' here).
    assert info['artists'][0]['name'] == 'Fallback Artist'


# ---------------------------------------------------------------------------
# Legacy path still kicks in for unknown / missing source
# ---------------------------------------------------------------------------


def test_legacy_path_used_when_no_source_provided():
    """No source → legacy duck-typed extraction. Backward-compat for
    every existing caller that hasn't been migrated yet."""
    info = album_tracks._build_album_info(
        SAMPLE_SPOTIFY_ALBUM, album_id='sp123',
    )
    # Legacy path doesn't populate `source` field.
    assert 'source' not in info or not info.get('source')
    # Core fields still resolved correctly via duck-typing.
    assert info['id'] == 'sp123'
    assert info['name'] == 'GNX'
    assert info['artist'] == 'Kendrick Lamar'


def test_legacy_path_used_for_unknown_source():
    """Source that doesn't match any registered converter → legacy."""
    info = album_tracks._build_album_info(
        SAMPLE_SPOTIFY_ALBUM, album_id='sp123', source='made_up_provider',
    )
    assert 'source' not in info or not info.get('source')


def test_legacy_path_used_when_album_data_not_dict():
    """Defensive: if the raw input isn't a dict (rare but possible —
    some clients return objects), typed path can't apply."""
    class _Obj:
        id = 'x'
        name = 'Y'
    info = album_tracks._build_album_info(_Obj(), album_id='x', source='spotify')
    # Falls through to legacy path which uses _extract_lookup_value
    # with getattr fallbacks. Result still has core fields.
    assert info['id'] == 'x'
    assert info['name'] == 'Y'


def test_legacy_path_used_when_typed_converter_raises():
    """If the typed converter throws, fall back to legacy. A converter
    bug must NOT break album resolution."""
    bad_input = {'id': 'sp123', 'name': 'GNX'}

    def _exploding_converter(_):
        raise RuntimeError('simulated converter bug')

    with patch.dict(album_tracks._TYPED_ALBUM_CONVERTERS,
                    {'spotify': _exploding_converter}):
        info = album_tracks._build_album_info(
            bad_input, album_id='sp123', source='spotify',
        )
    # Legacy path resolved core fields successfully.
    assert info['id'] == 'sp123'
    assert info['name'] == 'GNX'
    # Source field NOT set (legacy path doesn't add it).
    assert 'source' not in info or not info.get('source')


# ---------------------------------------------------------------------------
# Cross-provider: typed path works for every registered source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('source,raw,expected_name,expected_artist', [
    ('spotify', SAMPLE_SPOTIFY_ALBUM, 'GNX', 'Kendrick Lamar'),
    ('itunes', {
        'collectionId': 1, 'collectionName': 'GNX',
        'artistName': 'Kendrick Lamar', 'trackCount': 12,
    }, 'GNX', 'Kendrick Lamar'),
    ('deezer', {
        'id': 1, 'title': 'GNX',
        'artist': {'id': 2, 'name': 'Kendrick Lamar'},
        'nb_tracks': 12,
    }, 'GNX', 'Kendrick Lamar'),
    ('discogs', {
        'id': 1, 'title': 'GNX',
        'artists': [{'name': 'Kendrick Lamar'}],
        'year': 2024,
    }, 'GNX', 'Kendrick Lamar'),
    ('musicbrainz', {
        'id': 'mbid', 'title': 'GNX',
        'artist-credit': [{'artist': {'name': 'Kendrick Lamar'}}],
    }, 'GNX', 'Kendrick Lamar'),
    ('hydrabase', {
        'id': 'hb', 'name': 'GNX',
        'artists': [{'name': 'Kendrick Lamar'}],
    }, 'GNX', 'Kendrick Lamar'),
    ('qobuz', {
        'id': 1, 'title': 'GNX',
        'artist': {'id': 2, 'name': 'Kendrick Lamar'},
        'tracks_count': 12,
    }, 'GNX', 'Kendrick Lamar'),
])
def test_typed_path_works_for_every_registered_source(source, raw, expected_name, expected_artist):
    """Each of the seven registered providers should round-trip through
    the typed path producing usable output."""
    info = album_tracks._build_album_info(
        raw, album_id='whatever', source=source,
    )
    assert info['name'] == expected_name
    assert info['artist'] == expected_artist
    assert info['source'] == source
