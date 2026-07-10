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
    _best_name_match,
    _extract_jsonld,
    _normalize_for_match,
    _parse_bandcamp_date,
    _parse_bandcamp_duration,
    release_to_spotify_shape,
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

    def test_prefers_release_page_image_over_search_thumbnail(self, client, monkeypatch):
        """The autocomplete search index's cached thumbnail can point at a
        since-removed CDN size variant (confirmed 404 in production, e.g.
        .../img/1811014619_3.jpg — a Full Body Recordings track). The release
        page's own JSON-LD image is live-verified, so it must win when present."""
        monkeypatch.setattr(client, 'search_tracks', lambda q, limit=10: [Track.from_bandcamp_dict(_TRACK_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {
            'image_url': 'https://f4.bcbits.com/img/a3207173692_10.jpg',
        })

        result = client.search_track('Spotlights', 'All I Need (Radiohead Cover)')

        assert result['image_url'] == 'https://f4.bcbits.com/img/a3207173692_10.jpg'

    def test_falls_back_to_search_thumbnail_when_release_page_has_no_image(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_tracks', lambda q, limit=10: [Track.from_bandcamp_dict(_TRACK_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {'tags': []})

        result = client.search_track('Spotlights', 'All I Need (Radiohead Cover)')

        assert result['image_url'] == _TRACK_RESULT['img']


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

    def test_prefers_release_page_image_over_search_thumbnail(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_albums', lambda q, limit=10: [Album.from_bandcamp_dict(_ALBUM_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {
            'image_url': 'https://f4.bcbits.com/img/a3185643660_10.jpg',
        })

        result = client.search_album('Radiohead', 'KID A MNESIA')

        assert result['image_url'] == 'https://f4.bcbits.com/img/a3185643660_10.jpg'

    def test_falls_back_to_search_thumbnail_when_release_page_has_no_image(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_albums', lambda q, limit=10: [Album.from_bandcamp_dict(_ALBUM_RESULT)])
        monkeypatch.setattr(client, 'get_release_metadata', lambda url: {'tags': []})

        result = client.search_album('Radiohead', 'KID A MNESIA')

        assert result['image_url'] == _ALBUM_RESULT['img']


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

    def test_inter_call_wait_does_not_hold_the_lock(self, monkeypatch):
        # PR #968 review: sleeping under _call_lock stalls a foreground request
        # ~1s behind the background worker. The inter-call wait must happen with
        # the lock released.
        _fresh_rate_limit_state(monkeypatch)
        monkeypatch.setattr(bc, '_last_call_time', time.time())  # force a wait
        observed = {}

        def fake_sleep(_seconds):
            got = bc._call_lock.acquire(blocking=False)
            observed['lock_free'] = got
            if got:
                bc._call_lock.release()

        monkeypatch.setattr(bc.time, 'sleep', fake_sleep)

        @bc.rate_limited
        def call():
            return 'ok'

        assert call() == 'ok'
        assert observed.get('lock_free') is True, "lock must be released before the inter-call sleep"

    def test_concurrent_calls_reserve_spaced_slots(self, monkeypatch):
        # Reservation-based spacing: two back-to-back calls must still be
        # scheduled MIN_CALL_INTERVAL apart even though the sleep is outside the
        # lock (otherwise releasing the lock would break rate limiting).
        _fresh_rate_limit_state(monkeypatch)
        monkeypatch.setattr(bc.time, 'sleep', lambda _s: None)  # don't actually wait

        @bc.rate_limited
        def call():
            return bc._last_call_time

        first = call()
        second = call()
        assert second - first >= bc.MIN_CALL_INTERVAL - 1e-6


# ---------------------------------------------------------------------------
# _best_name_match — pure artist-name resolution (no second field to
# cross-check, unlike _best_match).
# ---------------------------------------------------------------------------


class TestBestNameMatch:
    def test_picks_closest_name(self):
        candidates = [
            Artist(id='1', name='Radiohead'),
            Artist(id='2', name='Radio Head Tribute Band'),
        ]
        best = _best_name_match(candidates, 'Radiohead')
        assert best is not None
        assert best.id == '1'

    def test_no_close_match_returns_none(self):
        candidates = [Artist(id='1', name='Completely Unrelated Artist')]
        assert _best_name_match(candidates, 'Radiohead') is None

    def test_empty_candidates(self):
        assert _best_name_match([], 'Radiohead') is None


# ---------------------------------------------------------------------------
# get_artist — resolve-by-name convenience wrapping search_artists.
# ---------------------------------------------------------------------------


class TestGetArtist:
    def test_resolves_confident_match(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_artists', lambda q, limit=5: [Artist.from_bandcamp_dict(_BAND_RESULT)])
        artist = client.get_artist('Radiohead')
        assert artist is not None
        assert artist.name == 'Radiohead'

    def test_empty_name_returns_none(self, client):
        assert client.get_artist('') is None

    def test_no_candidates_returns_none(self, client, monkeypatch):
        monkeypatch.setattr(client, 'search_artists', lambda q, limit=5: [])
        assert client.get_artist('Nobody') is None


# ---------------------------------------------------------------------------
# get_artist_releases — /music discography grid scraping, plus the
# single-release redirect fallback (Bandcamp redirects /music straight to
# the one release instead of rendering a grid — confirmed live).
# ---------------------------------------------------------------------------


_MUSIC_GRID_HTML = """
<html><body>
<ol id="music-grid" class="editable-grid music-grid columns-4 public">
    <li data-item-id="album-365742988" data-band-id="3957198221" class="music-grid-item square first-four">
        <a href="https://radiohead.bandcamp.com/album/hail-to-the-thief-live-recordings-2003-2009">
            <div class="art"><img src="https://f4.bcbits.com/img/a0454733928_2.jpg" alt="" /></div>
            <p class="title">
                Hail to the Thief (Live Recordings 2003-2009)
            </p>
        </a>
    </li>
    <li data-item-id="album-3317386587" data-band-id="3957198221" class="music-grid-item square first-four">
        <a href="https://radiohead.bandcamp.com/album/kid-a-mnesia">
            <div class="art"><img src="https://f4.bcbits.com/img/a3185643660_2.jpg" alt="" /></div>
            <p class="title">
                KID A MNESIA
            </p>
        </a>
    </li>
    <li data-item-id="track-1234567890" data-band-id="3957198221" class="music-grid-item square">
        <a href="https://radiohead.bandcamp.com/track/a-standalone-track">
            <div class="art"><img src="https://f4.bcbits.com/img/a9999999999_2.jpg" alt="" /></div>
            <p class="title">
                A Standalone Track
            </p>
        </a>
    </li>
</ol>
</body></html>
"""

_SINGLE_RELEASE_REDIRECT_HTML = (
    '<html><body>\n'
    '<script>var x = 1;</script>\n'
    '{"@context":"https://schema.org","@type":"MusicAlbum","@id":"https://fullbodyrecordings.bandcamp.com/album/full-body-recordings-episode-1","name":"Full Body Recordings, Episode 1","image":"https://f4.bcbits.com/img/a1811014619_10.jpg"}\n'
    '</body></html>'
)


def _mock_page_response(html, url='https://example.bandcamp.com/music'):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.text = html
    resp.url = url
    return resp


class TestGetArtistReleases:
    def test_parses_music_grid(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'get', return_value=_mock_page_response(_MUSIC_GRID_HTML)):
            releases = client.get_artist_releases('https://radiohead.bandcamp.com')

        assert len(releases) == 3
        assert releases[0] == {
            'id': 'album-365742988',
            'type': 'album',
            'title': 'Hail to the Thief (Live Recordings 2003-2009)',
            'url': 'https://radiohead.bandcamp.com/album/hail-to-the-thief-live-recordings-2003-2009',
            'image_url': 'https://f4.bcbits.com/img/a0454733928_2.jpg',
        }
        assert releases[2]['type'] == 'track'

    def test_single_release_redirect_fallback(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        redirect_url = 'https://fullbodyrecordings.bandcamp.com/album/full-body-recordings-episode-1'
        with patch.object(
            client.session, 'get',
            return_value=_mock_page_response(_SINGLE_RELEASE_REDIRECT_HTML, url=redirect_url),
        ):
            releases = client.get_artist_releases('https://fullbodyrecordings.bandcamp.com')

        assert len(releases) == 1
        assert releases[0]['title'] == 'Full Body Recordings, Episode 1'
        assert releases[0]['url'] == redirect_url
        assert releases[0]['type'] == 'album'

    def test_no_grid_and_no_jsonld_returns_empty(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'get', return_value=_mock_page_response('<html><body>nothing</body></html>')):
            assert client.get_artist_releases('https://example.bandcamp.com') == []

    def test_empty_url_returns_empty(self, client):
        assert client.get_artist_releases('') == []

    def test_fetch_failure_returns_empty(self, client, monkeypatch):
        _fresh_rate_limit_state(monkeypatch)
        with patch.object(client.session, 'get', side_effect=requests.exceptions.ConnectionError('boom')):
            assert client.get_artist_releases('https://example.bandcamp.com') == []


# ---------------------------------------------------------------------------
# get_artist_albums — duck-typed interface for
# core.metadata.album_tracks.get_artist_albums_for_source.
# ---------------------------------------------------------------------------


class TestGetArtistAlbums:
    def test_resolves_by_name_and_lists_releases(self, client, monkeypatch):
        monkeypatch.setattr(client, 'get_artist', lambda name: Artist.from_bandcamp_dict(_BAND_RESULT))
        monkeypatch.setattr(client, 'get_artist_releases', lambda url: [
            {'id': 'album-1', 'type': 'album', 'title': 'Album One', 'url': 'https://x/album/one', 'image_url': None},
            {'id': 'track-2', 'type': 'track', 'title': 'Track Two', 'url': 'https://x/track/two', 'image_url': None},
        ])

        albums = client.get_artist_albums('3957198221', artist_name='Radiohead')

        assert len(albums) == 2
        assert albums[0]['album_type'] == 'album'
        assert albums[1]['album_type'] == 'single'
        assert albums[0]['artists'] == ['Radiohead']

    def test_no_artist_name_returns_empty(self, client):
        assert client.get_artist_albums('3957198221') == []

    def test_unresolvable_artist_returns_empty(self, client, monkeypatch):
        monkeypatch.setattr(client, 'get_artist', lambda name: None)
        assert client.get_artist_albums('3957198221', artist_name='Nobody') == []

    def test_respects_limit(self, client, monkeypatch):
        monkeypatch.setattr(client, 'get_artist', lambda name: Artist.from_bandcamp_dict(_BAND_RESULT))
        monkeypatch.setattr(client, 'get_artist_releases', lambda url: [
            {'id': f'album-{i}', 'type': 'album', 'title': f'Album {i}', 'url': f'https://x/{i}', 'image_url': None}
            for i in range(10)
        ])
        albums = client.get_artist_albums('3957198221', artist_name='Radiohead', limit=3)
        assert len(albums) == 3


# ---------------------------------------------------------------------------
# release_to_spotify_shape — reshapes a get_release_metadata()/search_album()
# result into the 'Spotify-shaped' dict core.metadata.album_tracks expects.
# Bandcamp's own field names (title/position) don't match the alias chains
# _extract_lookup_value checks (name/track_name/trackName), so results must
# be relabeled here — this was the root cause of the artist-detail
# discography-grid album click 404ing even after the release was found.
# ---------------------------------------------------------------------------


class TestReleaseToSpotifyShape:
    def test_reshapes_album_and_tracks(self):
        release = {
            'title': 'Hail to the Thief (Live Recordings 2003-2009)',
            'artist': 'Radiohead',
            'release_date': '2025-08-13',
            'image_url': 'https://f4.bcbits.com/img/0454733928_3.jpg',
            'total_tracks': 2,
            'tracks': [
                {'position': 1, 'title': '2 + 2 = 5 (Live)', 'url': 'https://x/track/1', 'duration_ms': 216000},
                {'position': 2, 'title': 'Sit Down. Stand Up. (Live)', 'url': 'https://x/track/2', 'duration_ms': 240000},
            ],
        }

        shaped = release_to_spotify_shape(release, album_id='album-365742988')

        assert shaped['name'] == 'Hail to the Thief (Live Recordings 2003-2009)'
        assert shaped['artists'] == [{'name': 'Radiohead'}]
        assert shaped['total_tracks'] == 2
        assert len(shaped['tracks']) == 2
        assert shaped['tracks'][0]['name'] == '2 + 2 = 5 (Live)'
        assert shaped['tracks'][0]['track_number'] == 1
        assert shaped['tracks'][0]['duration_ms'] == 216000
        assert shaped['tracks'][1]['track_number'] == 2

    def test_falls_back_to_provided_id_and_names(self):
        shaped = release_to_spotify_shape(
            {}, album_id='album-1', fallback_name='Fallback Album', fallback_artist='Fallback Artist',
        )
        assert shaped['id'] == 'album-1'
        assert shaped['name'] == 'Fallback Album'
        assert shaped['artists'] == [{'name': 'Fallback Artist'}]
        assert shaped['tracks'] == []

    def test_missing_position_falls_back_to_index(self):
        release = {'tracks': [{'title': 'Untitled', 'url': 'https://x/1'}]}
        shaped = release_to_spotify_shape(release)
        assert shaped['tracks'][0]['track_number'] == 1
