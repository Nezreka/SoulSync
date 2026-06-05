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
    def __init__(self, album=None, track=None, name='fake'):
        self._album = album
        self._track = track
        self._name = name
        self.album_calls: list[str] = []
        self.track_calls: list[str] = []

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
# parse_metadata_identifier — bare IDs
# ---------------------------------------------------------------------------

def test_parse_bare_uuid_is_musicbrainz_album_then_track():
    mbid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    out = by_id.parse_metadata_identifier(mbid)
    assert out == [
        LookupTarget('musicbrainz', 'album', mbid),
        LookupTarget('musicbrainz', 'track', mbid),
    ]


def test_parse_bare_base62_is_spotify_album_then_track():
    sid = '4aawyAB9vmqN3uQ7FjRGTy'  # 22 chars, has letters
    out = by_id.parse_metadata_identifier(sid)
    assert out == [
        LookupTarget('spotify', 'album', sid),
        LookupTarget('spotify', 'track', sid),
    ]


def test_parse_bare_numeric_fans_out_deezer_then_itunes():
    out = by_id.parse_metadata_identifier('302127')
    assert out == [
        LookupTarget('deezer', 'album', '302127'),
        LookupTarget('deezer', 'track', '302127'),
        LookupTarget('itunes', 'album', '302127'),
        LookupTarget('itunes', 'track', '302127'),
    ]


def test_parse_bare_numeric_biases_preferred_source_first():
    out = by_id.parse_metadata_identifier('302127', preferred_source='itunes')
    # iTunes pulled to the front of the fan-out.
    assert out[0] == LookupTarget('itunes', 'album', '302127')
    assert out[1] == LookupTarget('itunes', 'track', '302127')


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


def test_resolve_kind_unknown_falls_back_album_then_track():
    # Bare base62 → spotify, kind unknown. Album returns None, track resolves.
    client = _FakeClient(album=None, track={
        'id': 't1', 'name': 'Creep', 'artists': ['Radiohead'],
        'album': {'name': 'Pablo Honey'}, 'duration_ms': 238000,
    })
    sid = '4aawyAB9vmqN3uQ7FjRGTy'
    res = by_id.resolve_identifier(
        sid, deps=None, client_resolver=_resolver_from({'spotify': client}),
    )
    assert res['available'] is True
    assert res['tracks'][0]['name'] == 'Creep'
    assert res['albums'] == []
    assert client.album_calls == [sid]   # tried album first
    assert client.track_calls == [sid]   # then track


def test_resolve_numeric_fanout_first_hit_wins():
    # Deezer has no such id (returns None), iTunes does — resolver moves on.
    deezer = _FakeClient(album=None, track=None)
    itunes = _FakeClient(album={'id': '99', 'name': 'Thriller',
                                'artists': ['Michael Jackson'], 'images': [],
                                'release_date': '1982', 'total_tracks': 9,
                                'album_type': 'album', 'external_urls': {}})
    res = by_id.resolve_identifier(
        '99', deps=None,
        client_resolver=_resolver_from({'deezer': deezer, 'itunes': itunes}),
    )
    assert res['available'] is True
    assert res['source'] == 'itunes'
    assert res['albums'][0]['name'] == 'Thriller'
    # Deezer was tried (album + track) before iTunes resolved.
    assert deezer.album_calls == ['99']
    assert deezer.track_calls == ['99']


def test_resolve_unavailable_client_is_skipped():
    # Spotify client is None (unauthed) — resolver returns not-found, no crash.
    res = by_id.resolve_identifier(
        'https://open.spotify.com/album/abc', deps=None,
        client_resolver=_resolver_from({'spotify': None}),
    )
    assert res['available'] is False
    assert res['albums'] == [] and res['tracks'] == []
    assert res['source'] == ''


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
