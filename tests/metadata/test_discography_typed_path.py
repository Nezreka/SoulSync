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


def test_typed_path_keeps_every_collaborative_album_artist():
    raw = dict(SAMPLE_SPOTIFY_RELEASE)
    raw['artists'] = [
        {'id': 'kdot', 'name': 'Kendrick Lamar'},
        {'id': 'sza', 'name': 'SZA'},
    ]

    normalized = discography._build_discography_release_dict(
        raw, artist_id='kdot', source='spotify',
    )
    card = discography._build_artist_detail_release_card(normalized)

    assert normalized['artist_name'] == 'Kendrick Lamar'
    assert normalized['artists'] == ['Kendrick Lamar', 'SZA']
    assert normalized['artist_credits'] == [
        {'name': 'Kendrick Lamar', 'id': 'kdot'},
        {'name': 'SZA', 'id': 'sza'},
    ]
    assert card['artists'] == ['Kendrick Lamar', 'SZA']
    assert card['artist_credits'] == normalized['artist_credits']


def test_musicbrainz_collaborative_credit_keeps_each_artist_mbid():
    raw = {
        'id': 'mb-release',
        'title': 'Shared Score',
        'artist-credit': [
            {'artist': {'id': 'mb-composer-a', 'name': 'Composer A'}},
            {'artist': {'id': 'mb-composer-b', 'name': 'Composer B'}},
        ],
        'release-group': {'primary-type': 'Album'},
    }

    normalized = discography._build_discography_release_dict(
        raw, artist_id='mb-composer-a', source='musicbrainz',
    )

    assert normalized['artist_credits'] == [
        {'name': 'Composer A', 'id': 'mb-composer-a'},
        {'name': 'Composer B', 'id': 'mb-composer-b'},
    ]


def test_spotify_album_object_rejoins_parallel_artist_ids():
    from core.spotify_client import Album as SpotifyAlbum

    raw = SpotifyAlbum(
        id='sp-release',
        name='Shared Score',
        artists=['Composer A', 'Composer B'],
        artist_ids=['sp-composer-a', 'sp-composer-b'],
        release_date='2026-01-01',
        total_tracks=12,
        album_type='album',
    )

    normalized = discography._build_discography_release_dict(
        raw, artist_id='sp-composer-a', source='spotify',
    )

    assert normalized['artist_credits'] == [
        {'name': 'Composer A', 'id': 'sp-composer-a'},
        {'name': 'Composer B', 'id': 'sp-composer-b'},
    ]


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
    assert card['external_urls'] == {'spotify': 'https://open.spotify.com/album/sp123'}


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


def test_artist_detail_card_legacy_path_carries_external_urls():
    """Regression: the legacy duck-typed branch used to drop `external_urls`
    entirely, so a Discogs album's card had nothing but the internal
    `m`/`r`-tagged `id` to build a "View on Discogs" link from -- producing a
    404 (discogs.com/release/r5743831 instead of .../release/5743831). This
    is the branch that actually handles Discogs releases (a `discogs_client.Album`
    instance isn't a dict, so it skips the typed path above and lands here)."""
    legacy_discogs_release = {
        'id': 'r5743831',
        'name': 'Watermelon Dandies',
        'album_type': 'album',
        'year': '1981',
        'external_urls': {
            'discogs': 'https://www.discogs.com/release/5743831-Naoya-Matsuoka-Watermelon-Dandies',
        },
    }
    card = discography._build_artist_detail_release_card(legacy_discogs_release)
    assert card['id'] == 'r5743831'  # id itself is untouched by this fix
    assert card['external_urls'] == {
        'discogs': 'https://www.discogs.com/release/5743831-Naoya-Matsuoka-Watermelon-Dandies',
    }


def test_artist_detail_card_legacy_path_missing_external_urls_defaults_empty():
    """A release with no external_urls at all must not crash the card build."""
    canonical = {'id': 'sp123', 'name': 'GNX', 'album_type': 'album'}
    card = discography._build_artist_detail_release_card(canonical)
    assert card['external_urls'] == {}


# ---------------------------------------------------------------------------
# release_group_id survives the two-stage build
#
# MusicBrainz's discography browse projects release GROUPS, so the card's `id`
# is a group mbid rather than a release one. Library v2 needs to know which it
# has; the id alone cannot say, since both are uuids. The value therefore has
# to survive `_build_discography_release_dict` AND the artist-detail card built
# from its output — dropping it in either stage loses it for good.
# ---------------------------------------------------------------------------

MB_GROUP = 'f17d521f-f8e9-41d8-9b0e-e270d5d905ed'


def test_release_group_id_survives_the_canonical_dict():
    from core.musicbrainz_search import Album as MBAlbum

    album = MBAlbum(
        id=MB_GROUP, name='Ultra', artists=['Depeche Mode'],
        release_date='1997-04-14', total_tracks=10, album_type='album',
        release_group_id=MB_GROUP,
    )
    out = discography._build_discography_release_dict(
        album, artist_id='dm', source='musicbrainz')
    assert out['release_group_id'] == MB_GROUP


def test_release_group_id_survives_the_artist_detail_card():
    canonical = {
        'id': MB_GROUP, 'name': 'Ultra', 'album_type': 'album',
        'release_group_id': MB_GROUP,
    }
    card = discography._build_artist_detail_release_card(canonical)
    assert card['release_group_id'] == MB_GROUP


def test_a_provider_without_release_groups_reports_none():
    card = discography._build_artist_detail_release_card(
        {'id': 'sp123', 'name': 'GNX', 'album_type': 'album'})
    assert card['release_group_id'] is None
