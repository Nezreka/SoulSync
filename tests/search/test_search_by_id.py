"""Tests for core/search/by_id.py — paste-a-link/ID metadata resolution (#775).

Covers the three seams:

- ``parse_metadata_identifier`` — provider URLs, the ``spotify:`` URI, and
  bare IDs (UUID → MusicBrainz, base62 → Spotify, numeric → Deezer/iTunes
  fan-out with active-source bias).
- the shaping adapters — projecting each source's get-by-id dict (which
  differ in their ``artists`` field shape) onto the common card shape.
- ``resolve_identifier`` — first-resolving-target-wins, kind fallback
  (album→track), source fan-out, and the not-found regression.

Clients are injected via ``client_resolver`` so nothing touches the network
or real config.
"""

from __future__ import annotations

from core.search import by_id
from core.search.by_id import LookupTarget


# ---------------------------------------------------------------------------
# Fakes — a client exposing only the get-by-id methods the resolver calls.
# Each "source" can return album/track dicts in its native shape.
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, album=None, track=None, artist=None, name='fake'):
        self._album = album
        self._track = track
        self._artist = artist
        self._name = name
        self.album_calls: list[str] = []
        self.track_calls: list[str] = []
        self.artist_calls: list[str] = []

    # Spotify / iTunes / MusicBrainz album-by-id
    def get_album(self, identifier, include_tracks=True):
        self.album_calls.append(identifier)
        return self._album

    # Deezer album-by-id (different method name)
    def get_album_metadata(self, identifier, include_tracks=True):
        self.album_calls.append(identifier)
        return self._album

    # Uniform track-by-id
    def get_track_details(self, identifier):
        self.track_calls.append(identifier)
        return self._track

    # Spotify / iTunes / MusicBrainz artist-by-id
    def get_artist(self, identifier):
        self.artist_calls.append(identifier)
        return self._artist

    # Deezer artist-by-id (different method name)
    def get_artist_info(self, identifier):
        self.artist_calls.append(identifier)
        return self._artist


def _resolver_from(mapping):
    """Build a client_resolver from {source: client}."""
    return lambda source: mapping.get(source)


# ---------------------------------------------------------------------------
# parse_metadata_identifier — URLs
# ---------------------------------------------------------------------------

def test_parse_spotify_album_url():
    out = by_id.parse_metadata_identifier(
        'https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy'
    )
    assert out == [LookupTarget('spotify', 'album', '4aawyAB9vmqN3uQ7FjRGTy')]


def test_parse_spotify_track_url_with_intl_prefix():
    out = by_id.parse_metadata_identifier(
        'https://open.spotify.com/intl-de/track/11dFghVXANMlKmJXsNCbNl'
    )
    assert out == [LookupTarget('spotify', 'track', '11dFghVXANMlKmJXsNCbNl')]


def test_parse_spotify_uri():
    assert by_id.parse_metadata_identifier('spotify:album:ABC') == [
        LookupTarget('spotify', 'album', 'ABC')
    ]
    assert by_id.parse_metadata_identifier('spotify:track:XYZ') == [
        LookupTarget('spotify', 'track', 'XYZ')
    ]


def test_parse_apple_album_url():
    out = by_id.parse_metadata_identifier(
        'https://music.apple.com/us/album/in-rainbows/1109714933'
    )
    assert out == [LookupTarget('itunes', 'album', '1109714933')]


def test_parse_apple_track_url_uses_i_param():
    out = by_id.parse_metadata_identifier(
        'https://music.apple.com/us/album/in-rainbows/1109714933?i=1109714934'
    )
    assert out == [LookupTarget('itunes', 'track', '1109714934')]


def test_parse_apple_song_url():
    out = by_id.parse_metadata_identifier(
        'https://music.apple.com/us/song/15-step/1109714938'
    )
    assert out == [LookupTarget('itunes', 'track', '1109714938')]


def test_parse_musicbrainz_release_group_url():
    mbid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    out = by_id.parse_metadata_identifier(
        f'https://musicbrainz.org/release-group/{mbid}'
    )
    assert out == [LookupTarget('musicbrainz', 'album', mbid)]


def test_parse_musicbrainz_recording_url_is_track():
    mbid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    out = by_id.parse_metadata_identifier(
        f'https://musicbrainz.org/recording/{mbid}'
    )
    assert out == [LookupTarget('musicbrainz', 'track', mbid)]


def test_parse_deezer_album_url_with_locale():
    out = by_id.parse_metadata_identifier('https://www.deezer.com/en/album/302127')
    assert out == [LookupTarget('deezer', 'album', '302127')]


def test_parse_deezer_track_url_no_scheme():
    out = by_id.parse_metadata_identifier('www.deezer.com/track/3135556')
    assert out == [LookupTarget('deezer', 'track', '3135556')]


def test_parse_spotify_url_without_scheme_or_www():
    # Known host detected even without a scheme.
    out = by_id.parse_metadata_identifier('open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy')
    assert out == [LookupTarget('spotify', 'album', '4aawyAB9vmqN3uQ7FjRGTy')]


# ---------------------------------------------------------------------------
# parse_metadata_identifier — artist links
# ---------------------------------------------------------------------------

def test_parse_spotify_artist_url():
    out = by_id.parse_metadata_identifier(
        'https://open.spotify.com/artist/3TVXtAsR1Inumwj472S9r4'
    )
    assert out == [LookupTarget('spotify', 'artist', '3TVXtAsR1Inumwj472S9r4')]


def test_parse_spotify_artist_uri():
    assert by_id.parse_metadata_identifier('spotify:artist:ABC') == [
        LookupTarget('spotify', 'artist', 'ABC')
    ]


def test_parse_apple_artist_url():
    out = by_id.parse_metadata_identifier(
        'https://music.apple.com/us/artist/kendrick-lamar/368183298'
    )
    assert out == [LookupTarget('itunes', 'artist', '368183298')]


def test_parse_musicbrainz_artist_url():
    mbid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    out = by_id.parse_metadata_identifier(f'https://musicbrainz.org/artist/{mbid}')
    assert out == [LookupTarget('musicbrainz', 'artist', mbid)]


def test_parse_deezer_artist_url():
    out = by_id.parse_metadata_identifier('https://www.deezer.com/artist/13')
    assert out == [LookupTarget('deezer', 'artist', '13')]


# ---------------------------------------------------------------------------
# parse_metadata_identifier — bare IDs are rejected (links only)
# ---------------------------------------------------------------------------

def test_parse_bare_numeric_id_rejected():
    # The footgun case (#775 follow-up): a bare number has no source/type, so
    # it must NOT resolve to whatever album happens to own that id.
    assert by_id.parse_metadata_identifier('525046') == []


def test_parse_bare_uuid_rejected():
    assert by_id.parse_metadata_identifier(
        'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    ) == []


def test_parse_bare_base62_rejected():
    assert by_id.parse_metadata_identifier('4aawyAB9vmqN3uQ7FjRGTy') == []


def test_parse_empty_and_garbage_return_empty():
    assert by_id.parse_metadata_identifier('') == []
    assert by_id.parse_metadata_identifier('   ') == []
    assert by_id.parse_metadata_identifier('not an id!!') == []
    # Unknown domain → no targets.
    assert by_id.parse_metadata_identifier('https://example.com/album/1') == []


# ---------------------------------------------------------------------------
# Shaping adapters
# ---------------------------------------------------------------------------

def test_album_card_from_spotify_shaped_dict():
    d = {
        'id': 'abc',
        'name': 'OK Computer',
        'artists': [{'name': 'Radiohead', 'id': 'r1'}],
        'images': [{'url': 'http://img/big.jpg', 'height': 640, 'width': 640}],
        'release_date': '1997-05-21',
        'total_tracks': 12,
        'album_type': 'album',
        'external_urls': {'spotify': 'http://spot/abc'},
    }
    card = by_id.album_dict_to_card(d)
    assert card['id'] == 'abc'
    assert card['name'] == 'OK Computer'
    assert card['artist'] == 'Radiohead'
    assert card['image_url'] == 'http://img/big.jpg'
    assert card['total_tracks'] == 12
    assert card['external_urls'] == {'spotify': 'http://spot/abc'}


def test_album_card_carries_optional_musicbrainz_fields():
    d = {
        'id': 'mbid', 'name': 'Kid A', 'artists': [{'name': 'Radiohead'}],
        'images': [], 'release_date': '2000', 'total_tracks': 10,
        'album_type': 'album', 'country': 'GB', 'label': 'Parlophone',
        'release_group_id': 'rg1', 'external_urls': {},
    }
    card = by_id.album_dict_to_card(d)
    assert card['country'] == 'GB'
    assert card['label'] == 'Parlophone'
    assert card['release_group_id'] == 'rg1'


def test_track_card_handles_list_of_string_artists():
    # Spotify/iTunes shape: artists is a list of plain strings.
    d = {
        'id': 't1', 'name': 'Paranoid Android',
        'artists': ['Radiohead'],
        'album': {'name': 'OK Computer', 'release_date': '1997'},
        'duration_ms': 387000,
    }
    card = by_id.track_dict_to_card(d)
    assert card['artist'] == 'Radiohead'
    assert card['album'] == 'OK Computer'
    assert card['duration_ms'] == 387000
    assert card['release_date'] == '1997'


def test_track_card_handles_list_of_dict_artists_and_album_image():
    # MusicBrainz shape: artists is a list of dicts; album carries images.
    d = {
        'id': 't2', 'name': 'Idioteque',
        'artists': [{'name': 'Radiohead', 'id': ''}],
        'album': {
            'name': 'Kid A',
            'images': [{'url': 'http://img/kida.jpg', 'height': 250, 'width': 250}],
            'release_date': '2000',
        },
        'duration_ms': 300000,
        'external_urls': {'musicbrainz': 'http://mb/t2'},
    }
    card = by_id.track_dict_to_card(d)
    assert card['artist'] == 'Radiohead'
    assert card['album'] == 'Kid A'
    assert card['image_url'] == 'http://img/kida.jpg'
    assert card['external_urls'] == {'musicbrainz': 'http://mb/t2'}


def test_join_artists_empty_is_unknown():
    assert by_id._join_artists([]) == 'Unknown Artist'
    assert by_id._join_artists(None) == 'Unknown Artist'


def test_artist_card_from_spotify_shaped_dict():
    d = {
        'id': 'a1', 'name': 'Radiohead',
        'images': [{'url': 'http://img/rh.jpg', 'height': 640, 'width': 640}],
        'external_urls': {'spotify': 'http://spot/a1'},
        'genres': ['rock'], 'popularity': 80,
    }
    card = by_id.artist_dict_to_card(d)
    assert card == {
        'id': 'a1',
        'name': 'Radiohead',
        'image_url': 'http://img/rh.jpg',
        'external_urls': {'spotify': 'http://spot/a1'},
    }


# ---------------------------------------------------------------------------
# resolve_identifier — end-to-end with fake clients
# ---------------------------------------------------------------------------

_SPOTIFY_ALBUM = {
    'id': 'abc', 'name': 'OK Computer',
    'artists': [{'name': 'Radiohead'}],
    'images': [{'url': 'http://i/a.jpg'}],
    'release_date': '1997', 'total_tracks': 12, 'album_type': 'album',
    'external_urls': {'spotify': 'http://s/abc'},
}


def test_resolve_spotify_album_link():
    client = _FakeClient(album=_SPOTIFY_ALBUM)
    res = by_id.resolve_identifier(
        'https://open.spotify.com/album/abc', deps=None,
        client_resolver=_resolver_from({'spotify': client}),
    )
    assert res['available'] is True
    assert res['source'] == 'spotify'
    assert len(res['albums']) == 1
    assert res['albums'][0]['name'] == 'OK Computer'
    assert res['tracks'] == []
    assert client.album_calls == ['abc']
    assert client.track_calls == []  # kind pinned to album — no track probe


def test_resolve_deezer_album_uses_get_album_metadata():
    client = _FakeClient(album={'id': '302127', 'name': 'Discovery',
                                'artists': [{'name': 'Daft Punk'}], 'images': [],
                                'release_date': '2001', 'total_tracks': 14,
                                'album_type': 'album', 'external_urls': {}})
    res = by_id.resolve_identifier(
        'https://www.deezer.com/album/302127', deps=None,
        client_resolver=_resolver_from({'deezer': client}),
    )
    assert res['available'] is True
    assert res['source'] == 'deezer'
    assert res['albums'][0]['name'] == 'Discovery'
    assert client.album_calls == ['302127']


def test_resolve_track_link():
    # A track URL pins kind=track — only get_track_details is called.
    client = _FakeClient(album=None, track={
        'id': 't1', 'name': 'Creep', 'artists': ['Radiohead'],
        'album': {'name': 'Pablo Honey'}, 'duration_ms': 238000,
    })
    res = by_id.resolve_identifier(
        'https://open.spotify.com/track/t1', deps=None,
        client_resolver=_resolver_from({'spotify': client}),
    )
    assert res['available'] is True
    assert res['tracks'][0]['name'] == 'Creep'
    assert res['albums'] == []
    assert client.album_calls == []      # kind pinned to track — no album probe
    assert client.track_calls == ['t1']


def test_resolve_artist_link():
    # An artist URL pins kind=artist — only get_artist is called.
    client = _FakeClient(artist={
        'id': 'a1', 'name': 'Radiohead',
        'images': [{'url': 'http://i/rh.jpg'}],
        'external_urls': {'spotify': 'http://s/a1'},
    })
    res = by_id.resolve_identifier(
        'https://open.spotify.com/artist/a1', deps=None,
        client_resolver=_resolver_from({'spotify': client}),
    )
    assert res['available'] is True
    assert res['source'] == 'spotify'
    assert res['artists'][0]['name'] == 'Radiohead'
    assert res['albums'] == [] and res['tracks'] == []
    assert client.artist_calls == ['a1']
    assert client.album_calls == [] and client.track_calls == []


def test_resolve_deezer_artist_uses_get_artist_info():
    client = _FakeClient(artist={'id': '13', 'name': 'Daft Punk',
                                 'images': [], 'external_urls': {}})
    res = by_id.resolve_identifier(
        'https://www.deezer.com/artist/13', deps=None,
        client_resolver=_resolver_from({'deezer': client}),
    )
    assert res['available'] is True
    assert res['source'] == 'deezer'
    assert res['artists'][0]['name'] == 'Daft Punk'
    assert client.artist_calls == ['13']


def test_resolve_bare_id_rejected_with_hint():
    # The #775 follow-up regression: a bare number must not resolve; it
    # returns not-found with a link hint instead of an unrelated album.
    called = []
    res = by_id.resolve_identifier(
        '525046', deps=None,
        client_resolver=lambda s: called.append(s),  # must never be invoked
    )
    assert res['available'] is False
    assert res['albums'] == [] and res['tracks'] == []
    assert 'link' in res['message'].lower()
    assert called == []  # no source was even probed


def test_resolve_unavailable_client_is_skipped():
    # Spotify client is None (unauthed) — resolver returns not-found, no crash.
    res = by_id.resolve_identifier(
        'https://open.spotify.com/album/abc', deps=None,
        client_resolver=_resolver_from({'spotify': None}),
    )
    assert res['available'] is False
    assert res['albums'] == [] and res['tracks'] == []
    # The source we tried is reported even on miss.
    assert res['source'] == 'spotify'
    assert res['message']


def test_resolve_client_exception_does_not_propagate():
    def boom(_source):
        raise RuntimeError('client init failed')
    res = by_id.resolve_identifier(
        'https://open.spotify.com/album/abc', deps=None, client_resolver=boom,
    )
    assert res['available'] is False


def test_resolve_unrecognized_identifier_returns_empty():
    res = by_id.resolve_identifier(
        'definitely not a link', deps=None,
        client_resolver=_resolver_from({}),
    )
    assert res['available'] is False
    assert res['query'] == 'definitely not a link'


def test_resolve_get_album_returning_none_yields_not_found():
    # Regression: a pinned-kind link whose lookup returns None must report
    # not-found, not raise or fabricate a card.
    client = _FakeClient(album=None)
    res = by_id.resolve_identifier(
        'https://open.spotify.com/album/missing', deps=None,
        client_resolver=_resolver_from({'spotify': client}),
    )
    assert res['available'] is False
    assert res['albums'] == []


# ── Discogs (#813 — extend paste-link to Discogs) ──────────────────────────

def test_parse_discogs_release_url_strips_slug():
    out = by_id.parse_metadata_identifier(
        'https://www.discogs.com/release/678910-Some-Album-Title')
    assert out == [by_id.LookupTarget('discogs', 'album', '678910')]


def test_parse_discogs_master_url_is_album():
    out = by_id.parse_metadata_identifier(
        'https://www.discogs.com/master/555-A-Master')
    assert out == [by_id.LookupTarget('discogs', 'album', '555')]


def test_parse_discogs_artist_url():
    out = by_id.parse_metadata_identifier(
        'https://www.discogs.com/artist/12345-Some-Artist')
    assert out == [by_id.LookupTarget('discogs', 'artist', '12345')]


def test_parse_discogs_url_no_scheme():
    out = by_id.parse_metadata_identifier('discogs.com/release/999-X')
    assert out == [by_id.LookupTarget('discogs', 'album', '999')]


def test_resolve_discogs_release_uses_get_album_with_numeric_id():
    client = _FakeClient(album={'id': '678910', 'name': 'Some Album',
                                'artists': [{'name': 'Some Artist'}]})
    res = by_id.resolve_identifier(
        'https://www.discogs.com/release/678910-Some-Album-Title', deps=None,
        client_resolver=_resolver_from({'discogs': client}))
    assert res['available'] is True and res['source'] == 'discogs'
    assert res['albums'] and res['albums'][0]['name'] == 'Some Album'
    assert client.album_calls == ['678910']      # numeric id, slug stripped


def test_resolve_discogs_artist():
    client = _FakeClient(artist={'id': '12345', 'name': 'Some Artist'})
    res = by_id.resolve_identifier(
        'https://www.discogs.com/artist/12345-Some-Artist', deps=None,
        client_resolver=_resolver_from({'discogs': client}))
    assert res['available'] is True and res['source'] == 'discogs'
    assert res['artists'] and res['artists'][0]['name'] == 'Some Artist'
    assert client.artist_calls == ['12345']


def test_discogs_in_supported_sources():
    assert 'discogs' in by_id.SUPPORTED_SOURCES
