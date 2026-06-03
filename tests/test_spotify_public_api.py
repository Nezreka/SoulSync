"""Anonymous full-playlist fetch for the public 'Spotify link' path.

Covers the testable seams (token extraction, track normalisation, paginated
fetch via an injected http_get) and — most importantly — the embed fallback
orchestration, so a broken anonymous path can never make the link worse than
today.
"""

from __future__ import annotations

import pytest

import core.spotify_public_api as papi
import core.spotify_public_scraper as scraper


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_extract_access_token_found():
    html = 'window.foo={"accessToken":"BQ_abcdefghijklmnopqrstuvwxyz","other":1}'
    assert papi.extract_access_token(html) == 'BQ_abcdefghijklmnopqrstuvwxyz'


def test_extract_access_token_absent_or_short():
    assert papi.extract_access_token('<html>no token here</html>') is None
    assert papi.extract_access_token('') is None
    assert papi.extract_access_token('{"accessToken":"short"}') is None  # too short to be real


def test_normalize_api_track_shape():
    item = {'track': {'id': 't1', 'name': 'Song', 'artists': [{'name': 'A'}, {'name': 'B'}],
                      'duration_ms': 1000, 'explicit': True}}
    t = papi.normalize_api_track(item, 4)
    assert t == {'id': 't1', 'name': 'Song', 'artists': [{'name': 'A'}, {'name': 'B'}],
                 'duration_ms': 1000, 'is_explicit': True, 'track_number': 5}


def test_normalize_api_track_skips_unusable():
    assert papi.normalize_api_track({'track': {'id': None}}, 0) is None   # local file / removed
    assert papi.normalize_api_track({}, 0) is None
    # missing artists -> Unknown Artist fallback
    t = papi.normalize_api_track({'track': {'id': 'x', 'name': 'N'}}, 0)
    assert t['artists'] == [{'name': 'Unknown Artist'}]


# --------------------------------------------------------------------------
# Paginated fetch with injected HTTP (no network)
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, *, text='', json_data=None, status=200):
        self.text, self._json, self.status_code = text, json_data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._json


def _make_items(start, count):
    return [{'track': {'id': f't{start + i}', 'name': f'Song {start + i}',
                       'artists': [{'name': 'Artist'}], 'duration_ms': 1000, 'explicit': False}}
            for i in range(count)]


def _fake_http(*, total, token='BQ_aaaaaaaaaaaaaaaaaaaaaaaa', no_token=False):
    """Build an http_get that serves the page, playlist meta, and track pages."""
    def http(url, headers=None, params=None, timeout=None):
        if 'open.spotify.com/playlist/' in url:
            return _Resp(text='' if no_token else f'x={{"accessToken":"{token}"}}')
        if url.endswith('/tracks'):
            offset = params['offset']
            remaining = max(0, total - offset)
            return _Resp(json_data={'items': _make_items(offset, min(100, remaining))})
        # playlist meta
        return _Resp(json_data={'name': 'My Playlist', 'owner': {'display_name': 'Owner'},
                                'tracks': {'total': total}})
    return http


def test_full_fetch_paginates_past_100():
    result = papi.fetch_public_playlist_full('pl1', http_get=_fake_http(total=250))
    assert result['name'] == 'My Playlist'
    assert result['subtitle'] == 'Owner'
    assert len(result['tracks']) == 250          # 3 pages (100+100+50), not capped at 100
    assert result['tracks'][0]['track_number'] == 1
    assert result['tracks'][-1]['id'] == 't249'
    assert result['type'] == 'playlist' and result['id'] == 'pl1'


def test_full_fetch_single_page():
    result = papi.fetch_public_playlist_full('pl1', http_get=_fake_http(total=30))
    assert len(result['tracks']) == 30


def test_full_fetch_raises_without_token():
    with pytest.raises(Exception):
        papi.fetch_public_playlist_full('pl1', http_get=_fake_http(total=10, no_token=True))


def test_full_fetch_raises_when_no_tracks():
    with pytest.raises(Exception):
        papi.fetch_public_playlist_full('pl1', http_get=_fake_http(total=0))


# --------------------------------------------------------------------------
# Fallback orchestration (the safety net)
# --------------------------------------------------------------------------

def test_fetch_public_uses_full_when_it_succeeds(monkeypatch):
    calls = {'embed': 0}
    monkeypatch.setattr(papi, 'fetch_public_playlist_full',
                        lambda pid, **kw: {'name': 'Full', 'tracks': [{'id': 'a'}] * 200})
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: calls.__setitem__('embed', calls['embed'] + 1) or {'tracks': []})
    out = scraper.fetch_spotify_public('playlist', 'pl1')
    assert len(out['tracks']) == 200
    assert calls['embed'] == 0          # full path won — embed never called


def test_fetch_public_falls_back_to_embed_on_failure(monkeypatch):
    def boom(pid, **kw):
        raise RuntimeError('spotify changed their page')
    monkeypatch.setattr(papi, 'fetch_public_playlist_full', boom)
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: {'name': 'Embed', 'tracks': [{'id': 'e'}]})
    out = scraper.fetch_spotify_public('playlist', 'pl1')
    assert out['name'] == 'Embed'       # gracefully fell back


def test_fetch_public_album_uses_embed_directly(monkeypatch):
    full_called = {'n': 0}
    monkeypatch.setattr(papi, 'fetch_public_playlist_full',
                        lambda pid, **kw: full_called.__setitem__('n', 1) or {})
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: {'name': 'Album', 'tracks': [{'id': 'x'}]})
    out = scraper.fetch_spotify_public('album', 'al1')
    assert out['name'] == 'Album'
    assert full_called['n'] == 0        # albums don't attempt the playlist full-fetch
