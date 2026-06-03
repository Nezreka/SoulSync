"""Full public-playlist fetch via the optional SpotipyFree library.

The library is GPL-3.0 and user-installed, so it's never imported in tests —
a fake spotipy-compatible client is injected to exercise normalisation +
pagination, and the embed fallback orchestration is tested separately. So a
missing/broken library can never make the link path worse than the embed ≤100.
"""

from __future__ import annotations

import pytest

import core.spotify_public_api as papi
import core.spotify_public_scraper as scraper


# --------------------------------------------------------------------------
# Track normalisation
# --------------------------------------------------------------------------

def test_normalize_api_track_shape():
    item = {'track': {'id': 't1', 'name': 'Song', 'artists': [{'name': 'A'}, {'name': 'B'}],
                      'duration_ms': 1000, 'explicit': True}}
    assert papi.normalize_api_track(item, 4) == {
        'id': 't1', 'name': 'Song', 'artists': [{'name': 'A'}, {'name': 'B'}],
        'duration_ms': 1000, 'is_explicit': True, 'track_number': 5,
    }


def test_normalize_api_track_skips_unusable():
    assert papi.normalize_api_track({'track': {'id': None}}, 0) is None   # local/removed
    assert papi.normalize_api_track({}, 0) is None
    t = papi.normalize_api_track({'track': {'id': 'x', 'name': 'N'}}, 0)
    assert t['artists'] == [{'name': 'Unknown Artist'}]                   # fallback


# --------------------------------------------------------------------------
# Full fetch with an injected fake SpotipyFree client (spotipy-shaped)
# --------------------------------------------------------------------------

class _FakeClient:
    """Minimal spotipy-compatible client: playlist() + playlist_items() + next()."""
    def __init__(self, total, *, fail_items=False):
        self.total, self.fail_items = total, fail_items

    def playlist(self, pid):
        return {'name': 'My Playlist', 'owner': {'display_name': 'Owner'}}

    def _page(self, offset):
        n = min(100, max(0, self.total - offset))
        items = [{'track': {'id': f't{offset + i}', 'name': f'S{offset + i}',
                            'artists': [{'name': 'A'}], 'duration_ms': 1000, 'explicit': False}}
                 for i in range(n)]
        nxt = offset + 100
        return {'items': items, 'next': ('u' if nxt < self.total else None), '_next': nxt}

    def playlist_items(self, pid):
        if self.fail_items:
            raise RuntimeError('boom')
        return self._page(0)

    def next(self, results):
        return self._page(results['_next'])


def test_full_fetch_paginates_past_100():
    result = papi.fetch_public_playlist_full('pl1', client_factory=lambda: _FakeClient(250))
    assert result['name'] == 'My Playlist'
    assert result['subtitle'] == 'Owner'
    assert len(result['tracks']) == 250          # 100+100+50, not capped at 100
    assert result['tracks'][0]['track_number'] == 1
    assert result['tracks'][-1]['id'] == 't249'
    assert result['type'] == 'playlist' and result['id'] == 'pl1'


def test_full_fetch_single_page():
    result = papi.fetch_public_playlist_full('pl1', client_factory=lambda: _FakeClient(30))
    assert len(result['tracks']) == 30


def test_full_fetch_raises_when_library_missing():
    # _default_client would raise ImportError; simulate via the factory.
    def missing():
        raise ImportError("No module named 'SpotipyFree'")
    with pytest.raises(Exception):
        papi.fetch_public_playlist_full('pl1', client_factory=missing)


def test_full_fetch_raises_when_no_tracks():
    with pytest.raises(Exception):
        papi.fetch_public_playlist_full('pl1', client_factory=lambda: _FakeClient(0))


# --------------------------------------------------------------------------
# Fallback orchestration (the safety net) — full path vs embed scraper
# --------------------------------------------------------------------------

def test_fetch_public_uses_full_when_it_succeeds(monkeypatch):
    calls = {'embed': 0}
    monkeypatch.setattr(papi, 'fetch_public_playlist_full',
                        lambda pid, **kw: {'name': 'Full', 'tracks': [{'id': 'a'}] * 200})
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: calls.__setitem__('embed', calls['embed'] + 1) or {'tracks': []})
    out = scraper.fetch_spotify_public('playlist', 'pl1')
    assert len(out['tracks']) == 200 and calls['embed'] == 0   # full won, embed not called


def test_fetch_public_falls_back_to_embed_on_failure(monkeypatch):
    def boom(pid, **kw):
        raise RuntimeError('library not installed / spotify changed')
    monkeypatch.setattr(papi, 'fetch_public_playlist_full', boom)
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: {'name': 'Embed', 'tracks': [{'id': 'e'}]})
    out = scraper.fetch_spotify_public('playlist', 'pl1')
    assert out['name'] == 'Embed'                              # graceful fallback


def test_fetch_public_album_uses_embed_directly(monkeypatch):
    full_called = {'n': 0}
    monkeypatch.setattr(papi, 'fetch_public_playlist_full',
                        lambda pid, **kw: full_called.__setitem__('n', 1) or {})
    monkeypatch.setattr(scraper, 'scrape_spotify_embed',
                        lambda *a, **k: {'name': 'Album', 'tracks': [{'id': 'x'}]})
    out = scraper.fetch_spotify_public('album', 'al1')
    assert out['name'] == 'Album' and full_called['n'] == 0    # albums skip full-fetch
