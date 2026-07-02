"""Tests for core.bandcamp_client.

Bandcamp has no general-purpose public API, so this client uses two
endpoints Bandcamp itself serves unauthenticated: the public autocomplete
search API (JSON) and each release page's embedded schema.org JSON-LD
block. Both technique's exact shapes were verified against live Bandcamp
pages during development (see PR description) — the fixtures below are
trimmed copies of that real response data, not guesses.

No live network calls here: `requests.Session.get`/`.post` are mocked, per
repo convention (no `responses`/`requests-mock` dependency is installed).
"""

from __future__ import annotations

import time
from unittest.mock import Mock, patch

import pytest
import requests

import core.bandcamp_client as bc
from core.bandcamp_client import (
    BandcampClient,
    BandcampRateLimitedError,
    _best_match,
    _extract_jsonld,
    _normalize_for_match,
    _parse_bandcamp_date,
    _parse_bandcamp_duration,
)
from core.metadata.types import Album, Artist, Track


def _fresh_rate_limit_state(monkeypatch):
    monkeypatch.setattr(bc, '_rate_limit_until', 0)
    monkeypatch.setattr(bc, '_rate_limit_backoff', 0)
    monkeypatch.setattr(bc, '_last_call_time', 0)


@pytest.fixture
def client() -> BandcampClient:
    return BandcampClient()


# ---------------------------------------------------------------------------
# Duration / date parsing — Bandcamp's formats are non-standard.
# ---------------------------------------------------------------------------


class TestDurationParsing:
    def test_hours_minutes_seconds(self):
        assert _parse_bandcamp_duration('P00H03M57S') == 237000

    def test_with_hours(self):
        assert _parse_bandcamp_duration('P01H02M03S') == 3723000

    def test_none_returns_zero(self):
        assert _parse_bandcamp_duration(None) == 0

    def test_empty_string_returns_zero(self):
        assert _parse_bandcamp_duration('') == 0

    def test_malformed_returns_zero(self):
        assert _parse_bandcamp_duration('not a duration') == 0


class TestDateParsing:
    def test_full_datetime(self):
        assert _parse_bandcamp_date('28 Dec 2007 00:00:00 GMT') == '2007-12-28'

    def test_none_returns_empty(self):
        assert _parse_bandcamp_date(None) == ''

    def test_malformed_returns_empty(self):
        assert _parse_bandcamp_date('not a date') == ''


# ---------------------------------------------------------------------------
# JSON-LD extraction — Bandcamp renders the whole block on one physical
# line, which is what the regex relies on.
# ---------------------------------------------------------------------------


class TestExtractJsonLd:
    def test_extracts_single_line_jsonld(self):
        html = (
            '<html><body>\n'
            '<script>var x = 1;</script>\n'
            '{"@context":"https://schema.org","@type":"MusicAlbum","@id":"https://x.bandcamp.com/album/y","name":"Y"}\n'
            '</body></html>'
        )
        data = _extract_jsonld(html)
        assert data is not None
        assert data['@id'] == 'https://x.bandcamp.com/album/y'
        assert data['name'] == 'Y'

    def test_falls_back_to_script_tag_when_no_bare_line_matches(self):
        html = (
            '<html><body>'
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@id":"https://x.bandcamp.com/album/y"}'
            '</script>'
            '</body></html>'
        )
        data = _extract_jsonld(html)
        assert data is not None
        assert data['@id'] == 'https://x.bandcamp.com/album/y'

    def test_no_jsonld_returns_none(self):
        html = '<html><body><p>Nothing here</p></body></html>'
        assert _extract_jsonld(html) is None

    def test_malformed_json_returns_none(self):
        html = '{"@id": "unterminated'
        assert _extract_jsonld(html) is None


# ---------------------------------------------------------------------------
# Release normalization — trimmed copies of real JSON-LD shapes fetched
# from radiohead.bandcamp.com/album/in-rainbows and
# spotlights.bandcamp.com/track/all-i-need-radiohead-cover.
# ---------------------------------------------------------------------------


_ALBUM_JSONLD = {
    "@type": "MusicAlbum",
    "@id": "https://radiohead.bandcamp.com/album/in-rainbows",
    "name": "In Rainbows",
    "byArtist": {"@type": "MusicGroup", "name": "Radiohead", "@id": "https://radiohead.bandcamp.com"},
    "publisher": {"@type": "MusicGroup", "@id": "https://radiohead.bandcamp.com", "name": "Radiohead"},
    "numTracks": 2,
    "keywords": ["Alternative", "Oxford"],
    "datePublished": "28 Dec 2007 00:00:00 GMT",
    "image": "https://f4.bcbits.com/img/a0552435637_10.jpg",
    "creditText": "2007, LLLP LLP under exclusive license to XL Recordings Ltd",
    "track": {
        "@type": "ItemList",
        "numberOfItems": 2,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "item": {
                    "@type": "MusicRecording",
                    "@id": "https://radiohead.bandcamp.com/track/15-step",
                    "name": "15 Step",
                    "duration": "P00H03M57S",
                },
            },
            {
                "@type": "ListItem",
                "position": 2,
                "item": {
                    "@type": "MusicRecording",
                    "@id": "https://radiohead.bandcamp.com/track/bodysnatchers",
                    "name": "Bodysnatchers",
                    "duration": "P00H04M02S",
                },
            },
        ],
    },
}

_TRACK_JSONLD = {
    "@type": "MusicRecording",
    "@id": "https://spotlights.bandcamp.com/track/all-i-need-radiohead-cover",
    "name": "All I Need (Radiohead Cover)",
    "byArtist": {"@type": "MusicGroup", "name": "Spotlights", "@id": "https://spotlights.bandcamp.com"},
    "publisher": {"@type": "MusicGroup", "@id": "https://spotlights.bandcamp.com", "name": "Spotlights"},
    "duration": "P00H05M18S",
    "inAlbum": {"@type": "MusicAlbum", "name": "All I Need (Radiohead Cover)"},
    "keywords": ["Rock", "doom-gaze", "post-rock"],
    "datePublished": "3 Apr 2020 00:00:00 GMT",
    "image": "https://f4.bcbits.com/img/a3207173692_10.jpg",
    "creditText": "Mario Quintero - Drums, Guitar",
}


class TestNormalizeRelease:
    def test_album_shape(self, client):
        result = client._normalize_release(_ALBUM_JSONLD, 'https://radiohead.bandcamp.com/album/in-rainbows')

        assert result['is_track'] is False
        assert result['title'] == 'In Rainbows'
        assert result['artist'] == 'Radiohead'
        assert result['label'] == 'Radiohead'
        assert result['tags'] == ['Alternative', 'Oxford']
        assert result['release_date'] == '2007-12-28'
        assert result['image_url'] == 'https://f4.bcbits.com/img/a0552435637_10.jpg'
        assert result['total_tracks'] == 2
        assert len(result['tracks']) == 2
        assert result['tracks'][0] == {
            'position': 1, 'title': '15 Step',
            'url': 'https://radiohead.bandcamp.com/track/15-step',
            'duration_ms': 237000,
        }

    def test_track_shape(self, client):
        result = client._normalize_release(
            _TRACK_JSONLD, 'https://spotlights.bandcamp.com/track/all-i-need-radiohead-cover',
        )

        assert result['is_track'] is True
        assert result['title'] == 'All I Need (Radiohead Cover)'
        assert result['artist'] == 'Spotlights'
        assert result['duration_ms'] == 318000
        assert result['album'] == 'All I Need (Radiohead Cover)'
        assert result['tags'] == ['Rock', 'doom-gaze', 'post-rock']

    def test_missing_url_falls_back_to_provided_url(self, client):
        data = dict(_TRACK_JSONLD)
        del data['@id']
        result = client._normalize_release(data, 'https://fallback.example/track/x')
        assert result['url'] == 'https://fallback.example/track/x'


# ---------------------------------------------------------------------------
# Typed dataclass converters — real autocomplete API result shapes.
# ---------------------------------------------------------------------------


_BAND_RESULT = {
    "type": "b", "id": 3957198221, "name": "Radiohead",
    "item_url_root": "https://radiohead.bandcamp.com",
    "img": "https://f4.bcbits.com/img/0040867508_23.jpg",
    "tag_names": ["Alternative"],
}
_ALBUM_RESULT = {
    "type": "a", "id": 3317386587, "name": "KID A MNESIA", "band_id": 3957198221,
    "band_name": "Radiohead", "item_url_root": "https://radiohead.bandcamp.com",
    "item_url_path": "https://radiohead.bandcamp.com/album/kid-a-mnesia",
    "img": "https://f4.bcbits.com/img/3185643660_3.jpg", "tag_names": None,
}
_TRACK_RESULT = {
    "type": "t", "id": 3131312045, "name": "All I Need (Radiohead Cover)",
    "band_id": 1516729353, "band_name": "Spotlights", "album_name": None,
    "item_url_root": "https://spotlights.bandcamp.com",
    "item_url_path": "https://spotlights.bandcamp.com/track/all-i-need-radiohead-cover",
    "img": "https://f4.bcbits.com/img/3207173692_3.jpg",
}


class TestFromBandcampDict:
    def test_artist(self):
        artist = Artist.from_bandcamp_dict(_BAND_RESULT)
        assert artist.id == '3957198221'
        assert artist.name == 'Radiohead'
        assert artist.image_url == 'https://f4.bcbits.com/img/0040867508_23.jpg'
        assert artist.genres == ['Alternative']
        assert artist.source == 'bandcamp'
        assert artist.external_urls == {'bandcamp': 'https://radiohead.bandcamp.com'}

    def test_album(self):
        album = Album.from_bandcamp_dict(_ALBUM_RESULT)
        assert album.id == '3317386587'
        assert album.name == 'KID A MNESIA'
        assert album.artists == ['Radiohead']
        assert album.artist_id == '3957198221'
        assert album.genres == []
        assert album.external_urls == {'bandcamp': 'https://radiohead.bandcamp.com/album/kid-a-mnesia'}

    def test_track(self):
        track = Track.from_bandcamp_dict(_TRACK_RESULT)
        assert track.id == '3131312045'
        assert track.name == 'All I Need (Radiohead Cover)'
        assert track.artists == ['Spotlights']
        assert track.album == ''
        assert track.duration_ms == 0
        assert track.external_urls == {'bandcamp': 'https://spotlights.bandcamp.com/track/all-i-need-radiohead-cover'}

    def test_missing_name_falls_back_to_unknown_artist(self):
        raw = dict(_ALBUM_RESULT)
        raw['band_name'] = None
        album = Album.from_bandcamp_dict(raw)
        assert album.artists == ['Unknown Artist']


# ---------------------------------------------------------------------------
# search_artists / search_albums / search_tracks — mocked HTTP.
# ---------------------------------------------------------------------------


def _mock_search_response(results):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {'auto': {'results': results}}
    return resp


class TestSearchMethods:
    def test_search_artists_filters_by_type(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'post', return_value=_mock_search_response(
            [_BAND_RESULT, _ALBUM_RESULT, _TRACK_RESULT],
        )):
            artists = client.search_artists('radiohead', limit=10)
        assert len(artists) == 1
        assert isinstance(artists[0], Artist)
        assert artists[0].name == 'Radiohead'

    def test_search_albums_filters_by_type(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'post', return_value=_mock_search_response(
            [_BAND_RESULT, _ALBUM_RESULT, _TRACK_RESULT],
        )):
            albums = client.search_albums('radiohead', limit=10)
        assert len(albums) == 1
        assert isinstance(albums[0], Album)

    def test_search_tracks_filters_by_type(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'post', return_value=_mock_search_response(
            [_BAND_RESULT, _ALBUM_RESULT, _TRACK_RESULT],
        )):
            tracks = client.search_tracks('radiohead', limit=10)
        assert len(tracks) == 1
        assert isinstance(tracks[0], Track)

    def test_empty_results(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'post', return_value=_mock_search_response([])):
            assert client.search_tracks('nonexistent query xyz') == []

    def test_request_exception_returns_empty_list(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'post', side_effect=requests.exceptions.ConnectionError('boom')):
            assert client.search_tracks('radiohead') == []


# ---------------------------------------------------------------------------
# _best_match — title AND artist similarity must both clear their bar.
# ---------------------------------------------------------------------------


class TestBestMatch:
    def test_picks_highest_scoring_candidate(self):
        candidates = [
            Track.from_bandcamp_dict(_TRACK_RESULT),
            Track(id='2', name='All I Need (Radiohead Cover)', artists=['Spotlights'], album='', duration_ms=0),
        ]
        best = _best_match(candidates, 'Spotlights', 'All I Need (Radiohead Cover)')
        assert best is not None
        assert best.artists == ['Spotlights']

    def test_rejects_low_title_similarity(self):
        candidates = [Track(id='1', name='Completely Different Song', artists=['Spotlights'], album='', duration_ms=0)]
        assert _best_match(candidates, 'Spotlights', 'All I Need') is None

    def test_rejects_low_artist_similarity_despite_perfect_title(self):
        candidates = [Track(id='1', name='All I Need', artists=['Some Unrelated Band'], album='', duration_ms=0)]
        assert _best_match(candidates, 'Spotlights', 'All I Need') is None

    def test_empty_candidates(self):
        assert _best_match([], 'Artist', 'Title') is None


class TestNormalizeForMatch:
    def test_strips_punctuation_and_lowercases(self):
        assert _normalize_for_match("All I Need (Radiohead Cover)!") == 'all i need radiohead cover'

    def test_none_and_empty(self):
        assert _normalize_for_match('') == ''
        assert _normalize_for_match(None) == ''


# ---------------------------------------------------------------------------
# search_track / search_album — mocked at the _search_raw / get_release_metadata
# boundary so these exercise the merge logic without touching HTTP directly.
# ---------------------------------------------------------------------------


class TestSearchTrackConvenience:
    def test_merges_release_metadata_on_confident_match(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_tracks', lambda q, limit=10: [Track.from_bandcamp_dict(_TRACK_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {
            'tags': ['Rock', 'post-rock'], 'label': 'Spotlights', 'credits': 'x', 'release_date': '2020-04-03',
        })

        result = client.search_track('Spotlights', 'All I Need (Radiohead Cover)')

        assert result is not None
        assert result['url'] == 'https://spotlights.bandcamp.com/track/all-i-need-radiohead-cover'
        assert result['tags'] == ['Rock', 'post-rock']
        assert result['label'] == 'Spotlights'

    def test_no_candidates_returns_none(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_tracks', lambda q, limit=10: [])
        assert client.search_track('Nobody', 'Nothing') is None

    def test_no_confident_match_returns_none(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_tracks', lambda q, limit=10: [
            Track(id='1', name='Totally Unrelated', artists=['Someone Else'], album='', duration_ms=0),
        ])
        assert client.search_track('Spotlights', 'All I Need (Radiohead Cover)') is None

    def test_empty_query_returns_none(self, client):
        assert client.search_track('', '') is None


class TestSearchAlbumConvenience:
    def test_merges_release_metadata_and_tracklist(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_albums', lambda q, limit=10: [Album.from_bandcamp_dict(_ALBUM_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {
            'tags': ['Alternative'], 'label': 'Radiohead', 'tracks': [{'title': '15 Step'}], 'total_tracks': 10,
        })

        result = client.search_album('Radiohead', 'KID A MNESIA')

        assert result is not None
        assert result['label'] == 'Radiohead'
        assert result['total_tracks'] == 10
        assert len(result['tracks']) == 1


# ---------------------------------------------------------------------------
# Rate limiting — mirrors tests/test_genius_backoff.py: fail-fast, no sleeping.
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_backoff_window_fails_fast_without_sleeping(self, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        monkeypatch.setattr(bc, '_rate_limit_until', time.time() + 120)

        @bc.rate_limited
        def call():
            raise AssertionError('must not reach the API during a backoff window')

        started = time.time()
        with pytest.raises(BandcampRateLimitedError):
            call()
        assert time.time() - started < 0.5

    def test_429_opens_the_gate_without_sleeping_and_escalates(self, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)

        response = Mock(status_code=429)

        @bc.rate_limited
        def call():
            raise requests.exceptions.HTTPError(response=response)

        started = time.time()
        with pytest.raises(requests.exceptions.HTTPError):
            call()
        assert time.time() - started < 0.5
        assert bc._rate_limit_until > time.time()

    def test_non_rate_limit_error_does_not_open_gate(self, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)

        response = Mock(status_code=404)

        @bc.rate_limited
        def call():
            raise requests.exceptions.HTTPError(response=response)

        with pytest.raises(requests.exceptions.HTTPError):
            call()
        assert bc._rate_limit_until == 0
