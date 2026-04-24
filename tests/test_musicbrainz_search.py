"""Tests for the MusicBrainz search adapter (core/musicbrainz_search.py).

Covers the behavior changes from the search-overhaul PR:
- Artist search is re-enabled and score-filtered
- Bare name queries route through artist-first → browse
- Structured 'Artist - Title' queries stay on text search
- Top-artist resolution is memoized per instance
- Cover Art URLs are constructed, not probed
"""

from unittest.mock import MagicMock, patch

import pytest

from core.musicbrainz_search import (
    MusicBrainzSearchClient,
    _cover_art_url,
    _extract_title_hint,
)


# ---------------------------------------------------------------------------
# Cover art URL construction
# ---------------------------------------------------------------------------

def test_cover_art_url_release_scope():
    assert _cover_art_url('abc-123') == 'https://coverartarchive.org/release/abc-123/front-250'


def test_cover_art_url_release_group_scope():
    assert _cover_art_url('abc-123', scope='release-group') == \
        'https://coverartarchive.org/release-group/abc-123/front-250'


def test_cover_art_url_empty_mbid_returns_none():
    assert _cover_art_url('') is None
    assert _cover_art_url(None) is None


def test_cover_art_url_unknown_scope_falls_back_to_release():
    assert _cover_art_url('abc', scope='garbage') == 'https://coverartarchive.org/release/abc/front-250'


# ---------------------------------------------------------------------------
# Structured query splitting
# ---------------------------------------------------------------------------

def test_split_structured_query_hyphen():
    client = MusicBrainzSearchClient()
    assert client._split_structured_query('Metallica - Master of Puppets') == ('Metallica', 'Master of Puppets')


def test_split_structured_query_en_dash():
    client = MusicBrainzSearchClient()
    assert client._split_structured_query('Metallica – One') == ('Metallica', 'One')


def test_split_structured_query_em_dash():
    client = MusicBrainzSearchClient()
    assert client._split_structured_query('Metallica — Battery') == ('Metallica', 'Battery')


def test_split_structured_query_bare_name():
    client = MusicBrainzSearchClient()
    assert client._split_structured_query('metallica') == (None, 'metallica')


def test_split_structured_query_no_separator_with_hyphens_in_word():
    # A hyphen inside a word (no surrounding spaces) should not split.
    client = MusicBrainzSearchClient()
    assert client._split_structured_query('t-pain') == (None, 't-pain')


# ---------------------------------------------------------------------------
# Artist search — score filtering and shape
# ---------------------------------------------------------------------------

def _mk_artist(name, mbid, score=100, tags=None):
    return {
        'id': mbid,
        'name': name,
        'score': score,
        'tags': tags or [],
    }


def test_search_artists_filters_by_score_threshold():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [
        _mk_artist('Metallica', 'mb-real', score=100),
        _mk_artist('Metallica Tribute', 'mb-tribute', score=60),
        _mk_artist('Metallica Jam', 'mb-jam', score=58),
    ]
    results = client.search_artists('metallica', limit=10)
    assert len(results) == 1
    assert results[0].name == 'Metallica'
    assert results[0].id == 'mb-real'


def test_search_artists_uses_strict_false_for_fuzzy_match():
    """The adapter must use strict=False so MusicBrainz searches
    alias+artist+sortname together — strict mode would miss aliased names."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = []
    client.search_artists('metallica')
    client._client.search_artist.assert_called_once_with('metallica', limit=10, strict=False)


def test_search_artists_returns_empty_on_exception():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.side_effect = RuntimeError('network down')
    assert client.search_artists('metallica') == []


def test_search_artists_extracts_tags_as_genres():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [
        _mk_artist('Metallica', 'mb-real', score=100,
                   tags=[{'name': 'thrash metal', 'count': 20},
                         {'name': 'heavy metal', 'count': 15}]),
    ]
    results = client.search_artists('metallica')
    assert results[0].genres == ['thrash metal', 'heavy metal']


def test_search_artists_skips_entries_without_mbid_or_name():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [
        {'id': 'mb-1', 'name': 'Good', 'score': 100},
        {'id': '', 'name': 'Missing MBID', 'score': 100},
        {'id': 'mb-2', 'name': '', 'score': 100},
    ]
    results = client.search_artists('x')
    assert [r.name for r in results] == ['Good']


# ---------------------------------------------------------------------------
# Top-artist resolution — memoization
# ---------------------------------------------------------------------------

def test_resolve_top_artist_memoizes_by_normalized_query():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]

    first = client._resolve_top_artist('metallica')
    second = client._resolve_top_artist('  Metallica  ')  # Whitespace / case variant

    assert first is not None
    assert first['id'] == 'mb-1'
    assert first is second
    # HTTP call happens once despite two resolve calls.
    assert client._client.search_artist.call_count == 1


def test_resolve_top_artist_returns_none_below_threshold():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Tribute', 'mb-trib', score=50)]
    assert client._resolve_top_artist('obscure') is None


def test_resolve_top_artist_caches_negative_result():
    """After a lookup finds no good match, subsequent calls don't refetch."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = []
    first = client._resolve_top_artist('nonexistent band')
    second = client._resolve_top_artist('nonexistent band')
    assert first is None
    assert second is None
    assert client._client.search_artist.call_count == 1


def test_resolve_top_artist_empty_query_returns_none_without_http():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    assert client._resolve_top_artist('') is None
    client._client.search_artist.assert_not_called()


# ---------------------------------------------------------------------------
# Album search — routing
# ---------------------------------------------------------------------------

def test_search_albums_bare_query_uses_browse_path():
    """When a bare name resolves to an artist, we browse their release-groups
    instead of text-searching release titles."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-1', 'title': 'Master of Puppets', 'primary-type': 'Album',
         'first-release-date': '1986-03-03', 'secondary-types': []},
        {'id': 'rg-2', 'title': 'Ride the Lightning', 'primary-type': 'Album',
         'first-release-date': '1984-07-27', 'secondary-types': []},
    ]

    albums = client.search_albums('metallica', limit=10)

    client._client.browse_artist_release_groups.assert_called_once()
    # Text-search path must NOT be taken.
    client._client.search_release.assert_not_called()
    # Chronological ASC — debut first, so the album list reads like a
    # standard discography (Wikipedia-style: earliest release on top).
    assert [a.name for a in albums] == ['Ride the Lightning', 'Master of Puppets']
    assert all(a.artists == ['Metallica'] for a in albums)


def test_search_albums_structured_query_uses_text_path():
    """'Artist - Title' shape should text-search the title rather than
    browsing all of the artist's discography."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_release.return_value = [
        {'id': 'rel-1', 'title': 'Master of Puppets', 'score': 100,
         'date': '1986', 'media': [{'track-count': 8}],
         'release-group': {'id': 'rg-1', 'primary-type': 'Album'},
         'artist-credit': [{'name': 'Metallica'}]},
    ]

    albums = client.search_albums('Metallica - Master of Puppets', limit=10)

    client._client.search_release.assert_called_once()
    # Artist-first path must NOT be taken.
    client._client.search_artist.assert_not_called()
    client._client.browse_artist_release_groups.assert_not_called()
    assert len(albums) == 1
    assert albums[0].name == 'Master of Puppets'


def test_search_albums_falls_back_to_text_when_no_artist_match():
    """No artist above threshold → text-search the whole query."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    # Artist lookup returns nothing above threshold.
    client._client.search_artist.return_value = [_mk_artist('X', 'mb-x', score=40)]
    client._client.search_release.return_value = []

    client.search_albums('very obscure band')

    client._client.search_release.assert_called_once_with('very obscure band', artist_name=None, limit=10)
    client._client.browse_artist_release_groups.assert_not_called()


def test_search_albums_filters_live_and_compilation_secondary_types():
    """Mega-artists' browse results are dominated by live bootlegs and
    best-of compilations — they should be filtered out so the studio
    discography surfaces."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-live-1', 'title': 'Live Bootleg 2019', 'primary-type': 'Album',
         'first-release-date': '2019-01-01', 'secondary-types': ['Live']},
        {'id': 'rg-studio-1', 'title': 'Kill Em All', 'primary-type': 'Album',
         'first-release-date': '1983-07-25', 'secondary-types': []},
        {'id': 'rg-comp-1', 'title': 'Greatest Hits', 'primary-type': 'Album',
         'first-release-date': '2010-01-01', 'secondary-types': ['Compilation']},
        {'id': 'rg-studio-2', 'title': 'Master of Puppets', 'primary-type': 'Album',
         'first-release-date': '1986-03-03', 'secondary-types': []},
    ]

    albums = client.search_albums('metallica', limit=10)

    titles = [a.name for a in albums]
    assert titles == ['Kill Em All', 'Master of Puppets']
    assert 'Live Bootleg 2019' not in titles
    assert 'Greatest Hits' not in titles


def test_search_albums_falls_back_to_all_when_no_studio():
    """Niche live-only artist: if no studio releases exist, show live ones
    rather than returning empty."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('LiveBand', 'mb-1', score=100)]
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-live-1', 'title': 'Live at X', 'primary-type': 'Album',
         'first-release-date': '2019-01-01', 'secondary-types': ['Live']},
        {'id': 'rg-live-2', 'title': 'Live at Y', 'primary-type': 'Album',
         'first-release-date': '2020-01-01', 'secondary-types': ['Live']},
    ]

    albums = client.search_albums('liveband', limit=10)

    assert len(albums) == 2


def test_search_tracks_prefers_studio_release_in_album_field():
    """When a recording has both a studio release and a live release, the
    Track.album should reflect the studio release (canonical album),
    regardless of the order MB returned them in."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.search_recordings_by_artist_mbid.return_value = [
        {
            'id': 'rec-master',
            'title': 'Master of Puppets',
            'length': 516000,
            'artist-credit': [{'name': 'Metallica'}],
            # Live release first (what MB often returns), studio second.
            'releases': [
                {'id': 'rel-live', 'title': 'Live Bootleg', 'date': '2023-01-01',
                 'release-group': {'id': 'rg-live', 'primary-type': 'Album',
                                   'secondary-types': ['Live']}},
                {'id': 'rel-studio', 'title': 'Master of Puppets', 'date': '1986-03-03',
                 'release-group': {'id': 'rg-studio', 'primary-type': 'Album',
                                   'secondary-types': []}},
            ],
        },
    ]

    tracks = client.search_tracks('metallica', limit=10)

    assert len(tracks) == 1
    # Album must be the studio release, not the live bootleg.
    assert tracks[0].album == 'Master of Puppets'
    assert tracks[0].release_date == '1986-03-03'


def test_search_tracks_filters_recordings_without_studio_releases():
    """A recording that only exists on live/compilation releases should be
    dropped when we have studio alternatives."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.search_recordings_by_artist_mbid.return_value = [
        {'id': 'rec-studio', 'title': 'Seek and Destroy', 'length': 390000,
         'artist-credit': [{'name': 'Metallica'}],
         'releases': [
             {'id': 'rel-studio', 'title': 'Kill Em All', 'date': '1983-07-25',
              'release-group': {'id': 'rg-studio', 'primary-type': 'Album',
                                'secondary-types': []}},
         ]},
        {'id': 'rec-live-only', 'title': 'Fight Fire With Fire', 'length': 450000,
         'artist-credit': [{'name': 'Metallica'}],
         'releases': [
             {'id': 'rel-live', 'title': 'Live Shit', 'date': '1993-01-01',
              'release-group': {'id': 'rg-live', 'primary-type': 'Album',
                                'secondary-types': ['Live']}},
         ]},
    ]

    tracks = client.search_tracks('metallica', limit=10)

    titles = [t.name for t in tracks]
    assert 'Seek and Destroy' in titles
    assert 'Fight Fire With Fire' not in titles


def test_search_albums_text_path_filters_by_score():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    # Force text-search path by using a structured query.
    client._client.search_release.return_value = [
        {'id': 'rel-good', 'title': 'Good', 'score': 95,
         'release-group': {'id': 'rg-1', 'primary-type': 'Album'},
         'artist-credit': [{'name': 'Foo'}]},
        {'id': 'rel-bad', 'title': 'Bad', 'score': 40,
         'release-group': {'id': 'rg-2', 'primary-type': 'Album'},
         'artist-credit': [{'name': 'Foo'}]},
    ]

    albums = client.search_albums('Foo - Good', limit=10)

    titles = [a.name for a in albums]
    assert 'Good' in titles
    assert 'Bad' not in titles


# ---------------------------------------------------------------------------
# Track search — routing
# ---------------------------------------------------------------------------

def test_search_tracks_bare_query_uses_browse_path():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.search_recordings_by_artist_mbid.return_value = [
        {'id': 'rec-1', 'title': 'One', 'length': 446000,
         'releases': [{'id': 'rel-1', 'title': '...And Justice for All', 'date': '1988',
                       'release-group': {'id': 'rg-1', 'primary-type': 'Album'}}],
         'artist-credit': [{'name': 'Metallica'}]},
        {'id': 'rec-2', 'title': 'Battery', 'length': 312000,
         'releases': [{'id': 'rel-2', 'title': 'Master of Puppets', 'date': '1986',
                       'release-group': {'id': 'rg-2', 'primary-type': 'Album'}}],
         'artist-credit': [{'name': 'Metallica'}]},
    ]

    tracks = client.search_tracks('metallica', limit=10)

    client._client.search_recordings_by_artist_mbid.assert_called_once()
    client._client.search_recording.assert_not_called()
    assert len(tracks) == 2
    assert {t.name for t in tracks} == {'One', 'Battery'}


def test_search_tracks_dedupes_by_title():
    """MusicBrainz has many live/compilation variants of the same song.
    Browse results should be deduped by normalized title so we don't show
    'One' three times."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('Metallica', 'mb-1', score=100)]
    client._client.search_recordings_by_artist_mbid.return_value = [
        {'id': 'rec-1', 'title': 'One', 'length': 446000,
         'releases': [{'id': 'rel-1', 'title': '...And Justice for All', 'date': '1988'}],
         'artist-credit': [{'name': 'Metallica'}]},
        {'id': 'rec-1-live', 'title': 'One', 'length': 490000,
         'releases': [{'id': 'rel-live', 'title': 'Live Shit', 'date': '1993'}],
         'artist-credit': [{'name': 'Metallica'}]},
    ]

    tracks = client.search_tracks('metallica', limit=10)

    assert len(tracks) == 1
    assert tracks[0].name == 'One'


def test_search_tracks_structured_query_uses_text_path():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_recording.return_value = [
        {'id': 'rec-1', 'title': 'One', 'score': 100,
         'releases': [{'id': 'rel-1', 'title': '...And Justice for All', 'date': '1988'}],
         'artist-credit': [{'name': 'Metallica'}]},
    ]

    tracks = client.search_tracks('Metallica - One', limit=10)

    client._client.search_recording.assert_called_once()
    client._client.search_artist.assert_not_called()
    client._client.search_recordings_by_artist_mbid.assert_not_called()
    assert len(tracks) == 1


def test_get_album_resolves_release_group_mbid_to_release():
    """When the album ID is a release-group MBID (from the browse path),
    get_album must look up the release-group, pick a canonical release,
    and fetch that release's tracklist. Fetching /release/<rg-mbid>
    directly 404s."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    # Release-group lookup returns two editions — an Official release and
    # a promo. The Official earlier release should win.
    client._client.get_release_group.return_value = {
        'id': 'rg-damn',
        'title': 'DAMN.',
        'primary-type': 'Album',
        'secondary-types': [],
        'first-release-date': '2017-04-14',
        'artist-credit': [{'name': 'Kendrick Lamar'}],
        'releases': [
            {'id': 'rel-promo', 'status': 'Promotion', 'date': '2017-04-01',
             'media': [{'track-count': 14, 'tracks': []}]},
            {'id': 'rel-official', 'status': 'Official', 'date': '2017-04-14',
             'media': [{'track-count': 14, 'tracks': []}]},
        ],
    }
    # Release lookup returns a full release with tracklist.
    client._client.get_release.return_value = {
        'id': 'rel-official',
        'title': 'DAMN.',
        'date': '2017-04-14',
        'artist-credit': [{'name': 'Kendrick Lamar'}],
        'release-group': {'id': 'rg-damn', 'primary-type': 'Album', 'secondary-types': []},
        'media': [
            {'position': 1, 'tracks': [
                {'id': 't1', 'number': '1', 'position': 1, 'length': 50000,
                 'recording': {'id': 'rec-1', 'title': 'BLOOD.',
                               'artist-credit': [{'name': 'Kendrick Lamar'}], 'length': 50000}},
            ]},
        ],
    }

    album = client.get_album('rg-damn')

    # Must have called release-group first, then release for the picked edition.
    client._client.get_release_group.assert_called_once_with(
        'rg-damn', includes=['releases', 'artist-credits']
    )
    client._client.get_release.assert_called_once_with(
        'rel-official', includes=['recordings', 'artist-credits', 'release-groups']
    )
    assert album is not None
    assert album['id'] == 'rg-damn'  # Canonical ID stays the release-group MBID.
    assert album['name'] == 'DAMN.'
    assert len(album['tracks']) == 1
    assert album['tracks'][0]['name'] == 'BLOOD.'
    assert 'release-group' in album['external_urls']['musicbrainz']


def test_get_album_falls_back_to_release_lookup_on_rg_miss():
    """When the MBID is a release (from the text-search fallback path) the
    release-group lookup 404s, but the direct release lookup works."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    # Release-group lookup returns None (simulating 404).
    client._client.get_release_group.return_value = None
    client._client.get_release.return_value = {
        'id': 'rel-abc',
        'title': 'Some Album',
        'date': '2020-01-01',
        'artist-credit': [{'name': 'Some Artist'}],
        'release-group': {'id': 'rg-abc', 'primary-type': 'Album', 'secondary-types': []},
        'media': [{'position': 1, 'tracks': []}],
    }

    album = client.get_album('rel-abc')

    client._client.get_release_group.assert_called_once()
    client._client.get_release.assert_called_once()
    assert album is not None
    assert album['id'] == 'rel-abc'  # Falls back to release MBID since rg lookup missed.


# ---------------------------------------------------------------------------
# Title-hint extraction — for "Artist Album Title" bare queries
# ---------------------------------------------------------------------------

def test_extract_title_hint_basic():
    assert _extract_title_hint('The Beatles Abbey Road', 'The Beatles') == 'Abbey Road'


def test_extract_title_hint_case_insensitive():
    assert _extract_title_hint('the beatles abbey road', 'The Beatles') == 'abbey road'


def test_extract_title_hint_preserves_original_casing():
    # Query slicing should return the original casing of the title portion.
    assert _extract_title_hint('The Beatles Abbey Road', 'The Beatles') == 'Abbey Road'


def test_extract_title_hint_whitespace_tolerant():
    assert _extract_title_hint('The Beatles   Abbey Road', 'The Beatles') == 'Abbey Road'


def test_extract_title_hint_bare_artist_returns_none():
    assert _extract_title_hint('The Beatles', 'The Beatles') is None


def test_extract_title_hint_artist_not_prefix_returns_none():
    # Query where the artist name isn't the prefix — nothing to extract.
    assert _extract_title_hint('Abbey Road', 'The Beatles') is None


def test_extract_title_hint_word_boundary_required():
    # "Metallicasomething" shouldn't split as artist=Metallica + hint=something
    assert _extract_title_hint('Metallicasomething', 'Metallica') is None


def test_search_albums_filters_browse_results_by_title_hint():
    """Regression: 'The Beatles Abbey Road' used to return the whole
    discography; should now narrow to Abbey Road specifically."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('The Beatles', 'mb-1', score=100)]
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-abbey', 'title': 'Abbey Road', 'primary-type': 'Album',
         'first-release-date': '1969-09-26', 'secondary-types': []},
        {'id': 'rg-white', 'title': 'The Beatles', 'primary-type': 'Album',
         'first-release-date': '1968-11-22', 'secondary-types': []},
        {'id': 'rg-revolver', 'title': 'Revolver', 'primary-type': 'Album',
         'first-release-date': '1966-08-05', 'secondary-types': []},
    ]

    albums = client.search_albums('The Beatles Abbey Road', limit=10)

    # Filtered to only the album whose title matches the hint.
    assert [a.name for a in albums] == ['Abbey Road']


def test_search_albums_falls_back_to_text_when_hint_matches_nothing():
    """If the title hint doesn't match any browse result, fall back to
    text-search rather than returning the full discography or nothing."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('The Beatles', 'mb-1', score=100)]
    # Browse returns albums that don't match the hint.
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-1', 'title': 'Some Other Album', 'primary-type': 'Album',
         'first-release-date': '1965-01-01', 'secondary-types': []},
    ]
    # Text-search fallback (_search_albums_text → search_release) returns the album.
    client._client.search_release.return_value = [
        {'id': 'rel-abbey', 'title': 'Abbey Road', 'score': 100,
         'release-group': {'id': 'rg-abbey', 'primary-type': 'Album'},
         'artist-credit': [{'name': 'The Beatles'}]},
    ]

    albums = client.search_albums('The Beatles Totally Fake Album Name', limit=10)

    # Browse had no hit for the title hint, then fallback kicks in when
    # the filter results are also empty (after studio-pref filter etc.).
    # NOTE: in this test the hint filter returns empty, so we fall through
    # to search_release.
    client._client.search_release.assert_called_once()
    assert any(a.name == 'Abbey Road' for a in albums)


def test_search_albums_bare_artist_no_hint_no_filter():
    """Bare artist name (no title hint) returns full discography — the
    filter only kicks in when the user types extra words."""
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_artist.return_value = [_mk_artist('The Beatles', 'mb-1', score=100)]
    client._client.browse_artist_release_groups.return_value = [
        {'id': 'rg-abbey', 'title': 'Abbey Road', 'primary-type': 'Album',
         'first-release-date': '1969-09-26', 'secondary-types': []},
        {'id': 'rg-revolver', 'title': 'Revolver', 'primary-type': 'Album',
         'first-release-date': '1966-08-05', 'secondary-types': []},
    ]

    albums = client.search_albums('the beatles', limit=10)

    # No filter — full discography.
    titles = {a.name for a in albums}
    assert 'Abbey Road' in titles
    assert 'Revolver' in titles


def test_recording_to_track_total_tracks_matches_media_count():
    """Regression: total_tracks was initialized at 1 and summed with media
    track-counts, producing an off-by-one. An 11-track album reported 12."""
    client = MusicBrainzSearchClient()
    recording = {
        'id': 'rec-1',
        'title': 'Song',
        'length': 300000,
        'artist-credit': [{'name': 'Band'}],
        'releases': [{
            'id': 'rel-1',
            'title': 'Album',
            'date': '2020-01-01',
            'release-group': {'id': 'rg-1', 'primary-type': 'Album', 'secondary-types': []},
            'media': [{'track-count': 11}],
        }],
    }
    track = client._recording_to_track(recording, 'Band')
    assert track is not None
    assert track.total_tracks == 11


def test_recording_to_track_multi_disc_sums_media():
    """Two-disc album with 14 tracks total should report 14, not 15 (off by one)
    or 3 (missing the sum)."""
    client = MusicBrainzSearchClient()
    recording = {
        'id': 'rec-1',
        'title': 'Song',
        'artist-credit': [{'name': 'Band'}],
        'releases': [{
            'id': 'rel-1', 'title': 'Album',
            'release-group': {'id': 'rg-1', 'primary-type': 'Album'},
            'media': [{'track-count': 7}, {'track-count': 7}],
        }],
    }
    track = client._recording_to_track(recording, 'Band')
    assert track.total_tracks == 14


def test_recording_to_track_no_release_defaults_total_tracks_to_one():
    """A recording with no release info is a standalone track — report 1."""
    client = MusicBrainzSearchClient()
    recording = {
        'id': 'rec-1',
        'title': 'Standalone',
        'artist-credit': [{'name': 'Band'}],
        'releases': [],
    }
    track = client._recording_to_track(recording, 'Band')
    assert track.total_tracks == 1


def test_pick_representative_release_prefers_official_with_media():
    """The release picker should skip stub releases (no media) and pick
    Official over Promotion status."""
    client = MusicBrainzSearchClient()
    releases = [
        {'id': 'stub', 'status': 'Official', 'date': '2020-01-01'},  # No media
        {'id': 'promo', 'status': 'Promotion', 'date': '2019-12-01',
         'media': [{'track-count': 10}]},
        {'id': 'official', 'status': 'Official', 'date': '2020-01-05',
         'media': [{'track-count': 10}]},
    ]
    picked = client._pick_representative_release(releases)
    assert picked['id'] == 'official'


def test_search_tracks_text_path_filters_by_score():
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.search_recording.return_value = [
        {'id': 'rec-good', 'title': 'Good', 'score': 95,
         'releases': [{'id': 'rel-1', 'title': 'X', 'date': '2020'}],
         'artist-credit': [{'name': 'Foo'}]},
        {'id': 'rec-bad', 'title': 'Bad', 'score': 40,
         'releases': [{'id': 'rel-2', 'title': 'Y', 'date': '2021'}],
         'artist-credit': [{'name': 'Foo'}]},
    ]

    tracks = client.search_tracks('Foo - Good', limit=10)

    titles = [t.name for t in tracks]
    assert 'Good' in titles
    assert 'Bad' not in titles
