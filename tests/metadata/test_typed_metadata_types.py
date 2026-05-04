"""Pin the per-provider Album converter contracts.

Each provider returns its own response shape. The
``Album.from_<provider>_dict()`` classmethods are the SINGLE place
that knows that shape. Consumers must be able to trust that an
``Album`` instance has the same field semantics regardless of which
provider it came from.

These tests use realistic sample payloads (truncated from real API
responses) and pin:
- Required fields are always populated even when source data is
  partial or messy (defaults applied uniformly).
- Cross-provider field semantics match — e.g. ``release_date`` is
  always 'YYYY' or 'YYYY-MM-DD' regardless of whether Spotify gave
  us 'YYYY-MM-DD', iTunes gave us '2024-01-15T00:00:00Z', or
  Discogs gave us a bare year integer.
- Provider-specific quirks are normalized at the converter boundary
  (Discogs `(N)` disambiguation suffix, iTunes `100x100bb` artwork
  URLs, Deezer's nested `artist` object).
- ``to_context_dict()`` produces the canonical SoulSync-internal
  shape consumers currently expect.

When a future PR adds a new provider, this file is where the
contract test goes.
"""

from __future__ import annotations

import pytest

from core.metadata.types import Album, Artist, Track


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------


def test_album_from_spotify_dict_full_response():
    """A typical /albums/{id} response — populated fields, full track list."""
    raw = {
        'id': '0hvT3yIEysuuvkK73vgdcW',
        'name': 'GNX',
        'artists': [
            {'id': '2YZyLoL8N0Wb9xBt1NhZWg', 'name': 'Kendrick Lamar'},
        ],
        'release_date': '2024-11-22',
        'total_tracks': 12,
        'album_type': 'album',
        'images': [
            {'url': 'https://i.scdn.co/image/abc123', 'height': 640, 'width': 640},
        ],
        'genres': ['hip hop', 'rap'],
        'label': 'pgLang/Interscope',
        'external_ids': {'upc': '00602465123456'},
        'external_urls': {'spotify': 'https://open.spotify.com/album/0hvT3yIEysuuvkK73vgdcW'},
    }

    album = Album.from_spotify_dict(raw)

    assert album.id == '0hvT3yIEysuuvkK73vgdcW'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    assert album.artist_id == '2YZyLoL8N0Wb9xBt1NhZWg'
    assert album.release_date == '2024-11-22'
    assert album.total_tracks == 12
    assert album.album_type == 'album'
    assert album.image_url == 'https://i.scdn.co/image/abc123'
    assert album.genres == ['hip hop', 'rap']
    assert album.label == 'pgLang/Interscope'
    assert album.barcode == '00602465123456'
    assert album.source == 'spotify'
    assert album.external_ids == {'spotify': '0hvT3yIEysuuvkK73vgdcW', 'upc': '00602465123456'}


def test_album_from_spotify_dict_handles_missing_fields():
    """Defensive: minimal payload still produces a valid Album."""
    raw = {'id': 'x', 'name': 'Y'}
    album = Album.from_spotify_dict(raw)
    assert album.id == 'x'
    assert album.name == 'Y'
    assert album.artists == ['Unknown Artist']
    assert album.release_date == ''
    assert album.total_tracks == 0
    assert album.album_type == 'album'
    assert album.image_url is None
    assert album.label is None


def test_album_from_spotify_dict_multi_artist():
    """Featured artists / collabs — all names captured, primary artist
    id is the first one."""
    raw = {
        'id': 'a1',
        'name': 'Luther',
        'artists': [
            {'id': 'kdot', 'name': 'Kendrick Lamar'},
            {'id': 'sza', 'name': 'SZA'},
        ],
        'total_tracks': 1,
    }
    album = Album.from_spotify_dict(raw)
    assert album.artists == ['Kendrick Lamar', 'SZA']
    assert album.artist_id == 'kdot'


# ---------------------------------------------------------------------------
# iTunes
# ---------------------------------------------------------------------------


def test_album_from_itunes_dict_full_response():
    raw = {
        'collectionId': 1782145638,
        'collectionName': 'GNX',
        'artistName': 'Kendrick Lamar',
        'artistId': 368183298,
        'releaseDate': '2024-11-22T08:00:00Z',
        'trackCount': 12,
        'collectionType': 'Album',
        'artworkUrl100': 'https://is1.mzstatic.com/image/100x100bb.jpg',
        'collectionViewUrl': 'https://music.apple.com/album/gnx/1782145638',
        'primaryGenreName': 'Hip-Hop/Rap',
    }
    album = Album.from_itunes_dict(raw)
    assert album.id == '1782145638'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    # iTunes ISO timestamp truncated to date
    assert album.release_date == '2024-11-22'
    assert album.total_tracks == 12
    assert album.album_type == 'album'
    # 100x100bb upgraded to 3000x3000bb
    assert album.image_url == 'https://is1.mzstatic.com/image/3000x3000bb.jpg'
    assert album.artist_id == '368183298'
    assert album.genres == ['Hip-Hop/Rap']
    assert album.source == 'itunes'
    assert album.external_ids['itunes'] == '1782145638'
    assert album.external_ids['itunes_artist'] == '368183298'


def test_album_from_itunes_dict_infers_album_type_from_track_count():
    """iTunes doesn't tag album type — convert per the existing
    heuristic (1-3 single, 4-6 EP, 7+ album)."""
    base = {'collectionId': 1, 'collectionName': 'X', 'artistName': 'A',
            'collectionType': 'Album'}
    assert Album.from_itunes_dict({**base, 'trackCount': 1}).album_type == 'single'
    assert Album.from_itunes_dict({**base, 'trackCount': 5}).album_type == 'ep'
    assert Album.from_itunes_dict({**base, 'trackCount': 12}).album_type == 'album'


def test_album_from_itunes_dict_detects_compilation():
    raw = {'collectionId': 1, 'collectionName': 'Best Of', 'artistName': 'V/A',
           'collectionType': 'Compilation', 'trackCount': 20}
    assert Album.from_itunes_dict(raw).album_type == 'compilation'


def test_album_from_itunes_dict_strips_single_ep_suffix():
    """iTunes appends ' - Single' / ' - EP' to single/EP collection
    names. Strip so cross-provider matching works on the actual title."""
    raw = {'collectionId': 1, 'collectionName': 'Track Name - Single',
           'artistName': 'A', 'trackCount': 1}
    assert Album.from_itunes_dict(raw).name == 'Track Name'


# ---------------------------------------------------------------------------
# Deezer
# ---------------------------------------------------------------------------


def test_album_from_deezer_dict_full_response():
    raw = {
        'id': 12345,
        'title': 'GNX',
        'artist': {'id': 67890, 'name': 'Kendrick Lamar'},
        'release_date': '2024-11-22',
        'nb_tracks': 12,
        'record_type': 'album',
        'cover_xl': 'https://e-cdns-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg',
        'genres': {'data': [{'id': 116, 'name': 'Rap/Hip Hop'}]},
        'label': 'pgLang',
        'upc': '00602465123456',
        'link': 'https://www.deezer.com/album/12345',
    }
    album = Album.from_deezer_dict(raw)
    assert album.id == '12345'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    assert album.artist_id == '67890'
    assert album.release_date == '2024-11-22'
    assert album.total_tracks == 12
    assert album.album_type == 'album'
    assert 'cover/abc' in album.image_url
    assert album.genres == ['Rap/Hip Hop']
    assert album.label == 'pgLang'
    assert album.barcode == '00602465123456'
    assert album.source == 'deezer'


def test_album_from_deezer_dict_falls_back_through_cover_sizes():
    """Deezer cover URLs come in xl/big/medium/small variants. Prefer xl."""
    base = {'id': 1, 'title': 'X', 'artist': {'name': 'A'}}
    # xl present
    a = Album.from_deezer_dict({**base, 'cover_xl': 'XL', 'cover_big': 'BIG'})
    assert a.image_url == 'XL'
    # only big
    b = Album.from_deezer_dict({**base, 'cover_big': 'BIG'})
    assert b.image_url == 'BIG'
    # nothing
    c = Album.from_deezer_dict(base)
    assert c.image_url is None


# ---------------------------------------------------------------------------
# Discogs
# ---------------------------------------------------------------------------


def test_album_from_discogs_dict_full_response():
    raw = {
        'id': 33445566,
        'title': 'GNX',
        'artists': [
            {'id': 1234, 'name': 'Kendrick Lamar'},
        ],
        'year': 2024,
        'tracklist': [
            {'position': 'A1', 'title': 'wacced out murals', 'type_': 'track'},
            {'position': 'A2', 'title': 'squabble up', 'type_': 'track'},
            {'position': 'B1', 'title': 'luther', 'type_': 'track'},
        ],
        'images': [
            {'type': 'primary', 'uri': 'https://img.discogs.com/abc.jpg', 'uri150': 'https://img.discogs.com/abc-150.jpg'},
        ],
        'genres': ['Hip Hop'],
        'styles': ['Conscious'],
        'labels': [{'name': 'pgLang', 'catno': 'PG001'}],
        'identifiers': [
            {'type': 'Barcode', 'value': '00602465123456'},
            {'type': 'Other', 'value': 'XYZ'},
        ],
        'uri': 'https://www.discogs.com/release/33445566',
    }
    album = Album.from_discogs_dict(raw)
    assert album.id == '33445566'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    assert album.artist_id == '1234'
    assert album.release_date == '2024'
    assert album.total_tracks == 3
    assert album.album_type == 'album'
    # uri preferred over uri150
    assert album.image_url == 'https://img.discogs.com/abc.jpg'
    # Discogs genres + styles merged
    assert 'Hip Hop' in album.genres and 'Conscious' in album.genres
    assert album.label == 'pgLang'
    assert album.barcode == '00602465123456'
    assert album.source == 'discogs'


def test_album_from_discogs_dict_strips_disambiguation_suffix():
    """`Madonna (3)` → `Madonna` so cross-provider matches work."""
    raw = {'id': 1, 'title': 'Y', 'artists': [{'name': 'Madonna (3)'}]}
    album = Album.from_discogs_dict(raw)
    assert album.artists == ['Madonna']


def test_album_from_discogs_dict_year_zero_means_unknown():
    """Discogs `year=0` is the sentinel for unknown — empty release_date."""
    raw = {'id': 1, 'title': 'Y', 'artists': [{'name': 'X'}], 'year': 0}
    assert Album.from_discogs_dict(raw).release_date == ''


def test_album_from_discogs_dict_counts_only_track_type_entries():
    """Discogs tracklists include heading rows, indices, etc (type_='heading').
    Only count actual tracks (type_='track')."""
    raw = {
        'id': 1, 'title': 'Y', 'artists': [{'name': 'X'}],
        'tracklist': [
            {'title': 'Side A', 'type_': 'heading'},
            {'title': 'Track 1', 'type_': 'track'},
            {'title': 'Track 2', 'type_': 'track'},
            {'title': 'Side B', 'type_': 'heading'},
            {'title': 'Track 3', 'type_': 'track'},
        ],
    }
    assert Album.from_discogs_dict(raw).total_tracks == 3


# ---------------------------------------------------------------------------
# MusicBrainz
# ---------------------------------------------------------------------------


def test_album_from_musicbrainz_dict_full_response():
    raw = {
        'id': 'abc-123-mbid',
        'title': 'GNX',
        'artist-credit': [
            {'artist': {'id': 'kdot-mbid', 'name': 'Kendrick Lamar'}},
        ],
        'date': '2024-11-22',
        'media': [{'track-count': 12}],
        'release-group': {
            'id': 'rg-mbid',
            'primary-type': 'Album',
        },
        'label-info': [{'label': {'name': 'pgLang'}}],
        'barcode': '00602465123456',
    }
    album = Album.from_musicbrainz_dict(raw)
    assert album.id == 'abc-123-mbid'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    assert album.artist_id == 'kdot-mbid'
    assert album.release_date == '2024-11-22'
    assert album.total_tracks == 12
    assert album.album_type == 'album'
    assert album.label == 'pgLang'
    assert album.barcode == '00602465123456'
    assert album.external_ids['musicbrainz'] == 'abc-123-mbid'
    assert album.external_ids['musicbrainz_release_group'] == 'rg-mbid'


def test_album_from_musicbrainz_dict_sums_multi_disc_tracks():
    """MB stores per-disc track counts; total = sum across media."""
    raw = {
        'id': 'x', 'title': 'Multi Disc',
        'artist-credit': [{'artist': {'name': 'A'}}],
        'media': [{'track-count': 14}, {'track-count': 5}],
    }
    assert Album.from_musicbrainz_dict(raw).total_tracks == 19


def test_album_from_musicbrainz_dict_release_group_type_overrides_default():
    raw = {
        'id': 'x', 'title': 'X',
        'artist-credit': [{'artist': {'name': 'A'}}],
        'release-group': {'id': 'rg', 'primary-type': 'Single'},
        'media': [{'track-count': 1}],
    }
    assert Album.from_musicbrainz_dict(raw).album_type == 'single'


# ---------------------------------------------------------------------------
# Hydrabase
# ---------------------------------------------------------------------------


def test_album_from_hydrabase_dict_full_response():
    raw = {
        'id': 'soul-12345',
        'name': 'GNX',
        'artists': [{'id': 'soul-artist-1', 'name': 'Kendrick Lamar'}],
        'release_date': '2024-11-22',
        'total_tracks': 12,
        'album_type': 'album',
        'image_url': 'https://hydrabase.example/cover.jpg',
        'soul_id': 'soul-12345',
        'artist_id': 'soul-artist-1',
    }
    album = Album.from_hydrabase_dict(raw)
    assert album.id == 'soul-12345'
    assert album.name == 'GNX'
    assert album.artists == ['Kendrick Lamar']
    assert album.artist_id == 'soul-artist-1'
    assert album.image_url == 'https://hydrabase.example/cover.jpg'
    assert album.source == 'hydrabase'
    assert album.external_ids['hydrabase'] == 'soul-12345'
    assert album.external_ids['soul'] == 'soul-12345'


def test_album_from_hydrabase_dict_handles_string_artists():
    """Hydrabase responses sometimes return artists as a flat list of
    name strings, sometimes as dicts. Both shapes work."""
    raw_str = {'id': '1', 'name': 'X', 'artists': ['Artist A']}
    assert Album.from_hydrabase_dict(raw_str).artists == ['Artist A']

    raw_dict = {'id': '1', 'name': 'X', 'artists': [{'name': 'Artist B'}]}
    assert Album.from_hydrabase_dict(raw_dict).artists == ['Artist B']


# ---------------------------------------------------------------------------
# Cross-provider invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('factory,raw', [
    ('from_spotify_dict', {'id': 'x', 'name': 'X'}),
    ('from_itunes_dict', {'collectionId': 1, 'collectionName': 'X', 'artistName': 'A'}),
    ('from_deezer_dict', {'id': 1, 'title': 'X', 'artist': {'name': 'A'}}),
    ('from_discogs_dict', {'id': 1, 'title': 'X', 'artists': [{'name': 'A'}]}),
    ('from_musicbrainz_dict', {'id': 'x', 'title': 'X',
                                'artist-credit': [{'artist': {'name': 'A'}}]}),
    ('from_hydrabase_dict', {'id': 'x', 'name': 'X', 'artists': [{'name': 'A'}]}),
])
def test_every_converter_produces_required_fields(factory, raw):
    """Every converter MUST populate the required fields with sensible
    defaults even on minimal input. This is the contract consumers
    rely on to drop their fallback chains."""
    album = getattr(Album, factory)(raw)
    assert isinstance(album.id, str) and album.id
    assert isinstance(album.name, str) and album.name
    assert isinstance(album.artists, list) and len(album.artists) >= 1
    assert isinstance(album.release_date, str)  # may be empty
    assert isinstance(album.total_tracks, int)
    assert isinstance(album.album_type, str) and album.album_type
    assert isinstance(album.genres, list)
    assert isinstance(album.external_ids, dict)
    assert isinstance(album.external_urls, dict)
    assert album.source  # always set by converter


@pytest.mark.parametrize('factory,raw', [
    ('from_spotify_dict', {'id': 'x', 'name': 'X'}),
    ('from_itunes_dict', {'collectionId': 1, 'collectionName': 'X', 'artistName': 'A'}),
    ('from_deezer_dict', {'id': 1, 'title': 'X', 'artist': {'name': 'A'}}),
    ('from_discogs_dict', {'id': 1, 'title': 'X', 'artists': [{'name': 'A'}]}),
    ('from_musicbrainz_dict', {'id': 'x', 'title': 'X',
                                'artist-credit': [{'artist': {'name': 'A'}}]}),
    ('from_hydrabase_dict', {'id': 'x', 'name': 'X', 'artists': [{'name': 'A'}]}),
])
def test_to_context_dict_shape_is_uniform_across_providers(factory, raw):
    """The bridge dict every consumer currently expects has the same
    shape regardless of provider. Pin so a future converter change
    can't subtly break consumer expectations."""
    album = getattr(Album, factory)(raw)
    ctx = album.to_context_dict()

    expected_keys = {
        'id', 'name', 'artist', 'artist_name', 'artist_id', 'artists',
        'image_url', 'images', 'release_date', 'album_type',
        'total_tracks', 'source', 'genres', 'label', 'barcode',
        'external_ids', 'external_urls',
    }
    assert set(ctx.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Track / Artist — light coverage; full converters land in a follow-up PR
# ---------------------------------------------------------------------------


def test_track_dataclass_required_fields():
    t = Track(id='1', name='X', artists=['A'], album='Y', duration_ms=1000)
    assert t.id == '1'
    assert t.popularity == 0  # default
    assert t.external_ids == {}


def test_artist_dataclass_required_fields():
    a = Artist(id='1', name='X')
    assert a.id == '1'
    assert a.followers == 0  # default
    assert a.genres == []
