"""#1312: deezer 30s previews as a stream source.

the clip plays straight from deezer through /stream/audio, with nothing
downloaded. the url is looked up fresh at /api/stream/start because deezer
signs preview urls and they expire. a missing queue song with auto-download
off asks for preview_only and plays the clip instead of being skipped.
"""

from __future__ import annotations

import pytest

from core.search import stream as search_stream
from core.streaming import deezer_preview

HIT = {
    'id': 3135556,
    'title': 'Harder, Better, Faster, Stronger',
    'preview': 'https://cdnt-preview.dzcdn.net/stale-from-search.mp3',
    'artist': {'name': 'Daft Punk'},
    'album': {'title': 'Discovery', 'cover_xl': 'https://cdn/cover.jpg'},
}


class _Deezer:
    def __init__(self, hit=HIT, fresh='https://cdnt-preview.dzcdn.net/fresh.mp3'):
        self.hit = hit
        self.fresh = fresh
        self.searches = []
        self.lookups = []

    def search_track(self, artist, title):
        self.searches.append((artist, title))
        return self.hit

    def get_track_preview_url(self, track_id):
        self.lookups.append(track_id)
        return self.fresh


class _Config:
    def __init__(self, source):
        self.source = source

    def get(self, key, default=None):
        return self.source if key == 'download_source.stream_source' else default


class _NoSearch:
    """the download stack: a preview must never reach it"""
    def client(self, name):
        raise AssertionError(f'searched {name}')

    async def search(self, *a, **k):
        raise AssertionError('searched the orchestrator')


class _YoutubeOnly:
    def __init__(self):
        self.queries = []

    def client(self, name):
        assert name == 'youtube'
        return self

    async def search(self, query, timeout=15):
        self.queries.append(query)
        return [], []


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _search(source, deezer, orchestrator, preview_only=False):
    return search_stream.stream_search_track(
        track_name='Harder, Better, Faster, Stronger', artist_name='Daft Punk',
        album_name='', duration_ms=0, config_manager=_Config(source),
        download_orchestrator=orchestrator, matching_engine=None, run_async=_run,
        deezer_client=deezer, preview_only=preview_only,
    )


# ── the search ───────────────────────────────────────────────────────────


def test_preview_source_answers_with_the_clip_and_never_searches_downloads():
    result = _search('deezer_preview', _Deezer(), _NoSearch())
    assert result['username'] == deezer_preview.PREVIEW_USERNAME
    assert result['preview'] is True
    assert deezer_preview.preview_track_id(result) == '3135556'
    assert result['title'] == 'Harder, Better, Faster, Stronger'
    assert result['image_url'] == 'https://cdn/cover.jpg'


def test_a_song_deezer_has_no_clip_for_falls_back_to_youtube():
    yt = _YoutubeOnly()
    assert _search('deezer_preview', _Deezer(hit=None), yt) is None
    assert yt.queries, 'no clip should still try youtube'


def test_a_hit_without_a_preview_counts_as_no_clip():
    yt = _YoutubeOnly()
    _search('deezer_preview', _Deezer(hit={**HIT, 'preview': ''}), yt)
    assert yt.queries


def test_preview_only_never_starts_a_real_search():
    assert _search('deezer_preview', _Deezer(hit=None), _NoSearch(), preview_only=True) is None


def test_preview_only_is_none_when_the_source_is_not_deezer_preview():
    dz = _Deezer()
    assert _search('youtube', dz, _NoSearch(), preview_only=True) is None
    assert dz.searches == []


def test_a_deezer_failure_is_no_clip_not_a_crash():
    class Boom(_Deezer):
        def search_track(self, artist, title):
            raise RuntimeError('deezer down')
    assert deezer_preview.find_preview(Boom(), 'Daft Punk', 'x') is None


def test_track_id_must_be_numeric():
    assert deezer_preview.preview_track_id({'filename': '3135556||Song'}) == '3135556'
    assert deezer_preview.preview_track_id({'filename': '../etc||Song'}) is None
    assert deezer_preview.preview_track_id({'deezer_id': '42', 'filename': 'x'}) == '42'


# ── the routes ───────────────────────────────────────────────────────────

web_server = pytest.importorskip('web_server')


def _admin():
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = 1
    return c


def _session(c):
    with c.session_transaction() as s:
        sid = s['stream_sid']
    return web_server.stream_state_store.get(sid)


@pytest.fixture
def deezer(monkeypatch):
    dz = _Deezer()
    monkeypatch.setattr(web_server, '_get_deezer_client', lambda: dz)
    return dz


@pytest.fixture
def no_prep(monkeypatch):
    """a real stream start hands off to the prep executor. record instead."""
    submitted = []

    class _Fut:
        def done(self):
            return True

    def submit(fn, *args):
        submitted.append(args)
        return _Fut()
    monkeypatch.setattr(web_server.stream_executor, 'submit', submit)
    return submitted


def test_starting_a_preview_is_ready_at_once_with_a_fresh_url(deezer, no_prep, monkeypatch):
    result = deezer_preview.find_preview(deezer, 'Daft Punk', 'Harder')
    c = _admin()
    r = c.post('/api/stream/start', json=result)
    assert r.status_code == 200 and r.get_json()['preview'] is True
    assert no_prep == [], 'a preview must not download anything'
    assert deezer.lookups == ['3135556']

    status = c.get('/api/stream/status').get_json()
    assert status['status'] == 'ready'
    assert status['track_info']['is_preview'] is True

    served = []
    monkeypatch.setattr(web_server, '_proxy_stream_url_with_range',
                        lambda url: (served.append(url), ('clip', 206))[1])
    assert c.get('/stream/audio').status_code == 206
    # the fresh url, never the signed one the search carried
    assert served == ['https://cdnt-preview.dzcdn.net/fresh.mp3']


def test_no_fresh_url_is_a_404_and_the_session_stays_empty(monkeypatch, no_prep):
    dz = _Deezer(fresh=None)
    monkeypatch.setattr(web_server, '_get_deezer_client', lambda: dz)
    c = _admin()
    r = c.post('/api/stream/start', json=deezer_preview.find_preview(dz, 'Daft Punk', 'x'))
    assert r.status_code == 404
    sess = _session(c)
    with sess.lock:
        assert sess['status'] == 'stopped' and sess.get('stream_url') is None


def test_a_normal_stream_after_a_preview_does_not_play_the_preview(deezer, no_prep):
    """/stream/audio serves stream_url first. stream start never cleared it,
    so the next track kept playing whatever set it last (a navidrome library
    track, #809, or now a preview)."""
    c = _admin()
    c.post('/api/stream/start', json=deezer_preview.find_preview(deezer, 'Daft Punk', 'x'))
    assert c.post('/api/stream/start', json={'username': 'peer', 'filename': 'a.flac'}).status_code == 200
    sess = _session(c)
    with sess.lock:
        assert sess.get('stream_url') is None


def test_stop_clears_the_preview_url(deezer, no_prep):
    c = _admin()
    c.post('/api/stream/start', json=deezer_preview.find_preview(deezer, 'Daft Punk', 'x'))
    c.post('/api/stream/stop')
    sess = _session(c)
    with sess.lock:
        assert sess.get('stream_url') is None


def test_stream_track_preview_only_route(deezer, monkeypatch):
    monkeypatch.setattr(web_server.config_manager, 'get',
                        _Config('deezer_preview').get)
    c = _admin()
    body = {'track_name': 'Harder', 'artist_name': 'Daft Punk', 'preview_only': True}
    r = c.post('/api/enhanced-search/stream-track', json=body)
    assert r.status_code == 200 and r.get_json()['result']['preview'] is True

    deezer.hit = None
    r = c.post('/api/enhanced-search/stream-track', json=body)
    assert r.status_code == 404 and r.get_json()['success'] is False
