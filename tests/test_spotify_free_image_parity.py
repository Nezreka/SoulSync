"""Spotify-Free must hand back album/artist art at the same resolution as Premium.

Reported as "Match Spotify Free and Paid album art resolution for parity".

The official Spotify Web API documents `images` as ordered largest-first, and
this codebase depends on that everywhere — amazon_worker, artist_source_detail,
auto_import_worker and others all take `images[0]` as THE display URL. The
no-auth web-player payload carries the same 64/300/640 set but promises no
order, and the free normalizers passed `coverArt.sources` straight through. So
whichever size happened to be first won: a free user could get the 64px
thumbnail exactly where a Premium user got 640px.
"""

from __future__ import annotations

from core.spotify_free_metadata import images_largest_first, normalize_artist

# The web player commonly returns SMALLEST first — the case that broke parity.
WEB_PLAYER_SOURCES = [
    {'url': 'https://i.scdn.co/image/small', 'width': 64, 'height': 64},
    {'url': 'https://i.scdn.co/image/medium', 'width': 300, 'height': 300},
    {'url': 'https://i.scdn.co/image/large', 'width': 640, 'height': 640},
]


def test_largest_first():
    out = images_largest_first(WEB_PLAYER_SOURCES)
    assert [s['width'] for s in out] == [640, 300, 64]


def test_images_zero_is_the_biggest():
    """The contract every consumer actually relies on."""
    assert images_largest_first(WEB_PLAYER_SOURCES)[0]['url'].endswith('large')


def test_already_largest_first_is_untouched():
    ordered = list(reversed(WEB_PLAYER_SOURCES))
    assert images_largest_first(ordered) == ordered


def test_entries_without_url_are_dropped():
    out = images_largest_first([{'width': 640}, {'url': 'u', 'width': 64}])
    assert out == [{'url': 'u', 'width': 64}]


def test_missing_dimensions_sort_last_instead_of_winning():
    out = images_largest_first([
        {'url': 'unknown'},
        {'url': 'big', 'width': 640, 'height': 640},
    ])
    assert out[0]['url'] == 'big'


def test_height_used_when_width_absent():
    out = images_largest_first([
        {'url': 'small', 'height': 64},
        {'url': 'big', 'height': 640},
    ])
    assert [s['url'] for s in out] == ['big', 'small']


def test_junk_input_is_survivable():
    assert images_largest_first(None) == []
    assert images_largest_first([]) == []
    assert images_largest_first(['not a dict', None, 42]) == []


def test_artist_avatar_is_largest_first():
    artist = normalize_artist({
        'uri': 'spotify:artist:abc',
        'profile': {'name': 'Lenka'},
        'visuals': {'avatarImage': {'sources': WEB_PLAYER_SOURCES}},
    })
    assert artist['images'][0]['width'] == 640
    # Shape preserved for Album.from_spotify_album / consumers.
    assert set(artist['images'][0]) == {'url', 'width', 'height'}


def test_artist_without_visuals_still_normalizes():
    artist = normalize_artist({'uri': 'spotify:artist:abc', 'profile': {'name': 'X'}})
    assert artist['images'] == []
