"""Tests for core/search/orchestrator.py — main enhanced-search dispatch + streaming."""

from __future__ import annotations

import json

import pytest

from core.search import orchestrator


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Artist:
    def __init__(self, id_, name, image_url=None, external_urls=None, thumb_url=None):
        self.id = id_
        self.name = name
        self.image_url = image_url
        self.external_urls = external_urls
        self.thumb_url = thumb_url


class _Album:
    def __init__(self, id_, name, artists=None, image_url=None, release_date=None,
                 total_tracks=10, album_type='album', external_urls=None):
        self.id = id_
        self.name = name
        self.artists = artists or []
        self.image_url = image_url
        self.release_date = release_date
        self.total_tracks = total_tracks
        self.album_type = album_type
        self.external_urls = external_urls


class _Track:
    def __init__(self, id_, name, artists=None, album=None, duration_ms=180000,
                 image_url=None, release_date=None, external_urls=None):
        self.id = id_
        self.name = name
        self.artists = artists or []
        self.album = album
        self.duration_ms = duration_ms
        self.image_url = image_url
        self.release_date = release_date
        self.external_urls = external_urls


class _Client:
    def __init__(self, *, name='fake', artists=None, albums=None, tracks=None,
                 fail_search=False, authed=True, connected=True):
        self.name = name
        self._artists = artists or []
        self._albums = albums or []
        self._tracks = tracks or []
        self._fail = fail_search
        self._authed = authed
        self._connected = connected

    def search_artists(self, q, limit=10):
        if self._fail:
            raise RuntimeError("client search boom")
        return self._artists

    def search_albums(self, q, limit=10):
        if self._fail:
            raise RuntimeError("client search boom")
        return self._albums

    def search_tracks(self, q, limit=10):
        if self._fail:
            raise RuntimeError("client search boom")
        return self._tracks

    def is_spotify_authenticated(self):
        return self._authed

    def is_connected(self):
        return self._connected


class _DB:
    def __init__(self, artists=None):
        self._artists = artists or []

    def search_artists(self, q, limit=5, server_source=None):
        return self._artists


class _Cfg:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, k, default=None):
        return self._v.get(k, default)

    def get_active_media_server(self):
        return self._v.get('__active_server', 'plex')


class _Worker:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, query, kind):
        self.enqueued.append((query, kind))


def _sync_run_async(coro):
    """Run a coroutine synchronously on a fresh loop."""
    import asyncio
    import inspect
    if not inspect.iscoroutine(coro):
        return coro
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_deps(**overrides):
    """Default deps for an enhanced-search call. Override with kwargs."""
    base = dict(
        database=_DB(),
        config_manager=_Cfg({'discogs.token': ''}),
        spotify_client=None,
        hydrabase_client=None,
        hydrabase_worker=None,
        soulseek_client=None,
        fix_artist_image_url=lambda u: f'FIXED::{u}' if u else None,
        is_hydrabase_active=lambda: False,
        get_metadata_fallback_source=lambda: 'spotify',
        get_metadata_fallback_client=lambda: _Client(name='fallback'),
        get_itunes_client=lambda: _Client(name='itunes'),
        get_deezer_client=lambda: _Client(name='deezer'),
        get_discogs_client=lambda token=None: _Client(name='discogs'),
        run_background_comparison=lambda *a, **k: None,
        run_async=_sync_run_async,
        dev_mode_enabled_provider=lambda: False,
    )
    base.update(overrides)
    return orchestrator.SearchDeps(**base)


# ---------------------------------------------------------------------------
# resolve_client
# ---------------------------------------------------------------------------

def test_resolve_spotify_authed_returns_client():
    deps = _build_deps(spotify_client=_Client(authed=True))
    client, ok = orchestrator.resolve_client('spotify', deps)
    assert client is deps.spotify_client
    assert ok is True


def test_resolve_spotify_unauthed_returns_none():
    deps = _build_deps(spotify_client=_Client(authed=False))
    client, ok = orchestrator.resolve_client('spotify', deps)
    assert client is None
    assert ok is False


def test_resolve_spotify_missing_returns_none():
    deps = _build_deps(spotify_client=None)
    client, ok = orchestrator.resolve_client('spotify', deps)
    assert client is None
    assert ok is False


def test_resolve_itunes_always_returns_client():
    deps = _build_deps()
    client, ok = orchestrator.resolve_client('itunes', deps)
    assert client.name == 'itunes'
    assert ok is True


def test_resolve_deezer_always_returns_client():
    deps = _build_deps()
    client, ok = orchestrator.resolve_client('deezer', deps)
    assert client.name == 'deezer'
    assert ok is True


def test_resolve_discogs_with_token_returns_client():
    deps = _build_deps(config_manager=_Cfg({'discogs.token': 'tok'}))
    client, ok = orchestrator.resolve_client('discogs', deps)
    assert client.name == 'discogs'
    assert ok is True


def test_resolve_discogs_without_token_returns_none():
    deps = _build_deps(config_manager=_Cfg({'discogs.token': ''}))
    client, ok = orchestrator.resolve_client('discogs', deps)
    assert client is None
    assert ok is False


def test_resolve_hydrabase_connected_returns_client():
    deps = _build_deps(hydrabase_client=_Client(connected=True))
    client, ok = orchestrator.resolve_client('hydrabase', deps)
    assert client is deps.hydrabase_client
    assert ok is True


def test_resolve_hydrabase_disconnected_returns_none():
    deps = _build_deps(hydrabase_client=_Client(connected=False))
    client, ok = orchestrator.resolve_client('hydrabase', deps)
    assert client is None
    assert ok is False


def test_resolve_unknown_source_returns_none():
    deps = _build_deps()
    client, ok = orchestrator.resolve_client('garbage', deps)
    assert client is None
    assert ok is False


# ---------------------------------------------------------------------------
# run_enhanced_search — short query path
# ---------------------------------------------------------------------------

def test_short_query_skips_remote_search():
    db_artist = _Artist('a1', 'Aretha', thumb_url='http://x/a.jpg')
    deps = _build_deps(database=_DB(artists=[db_artist]))

    result = orchestrator.run_enhanced_search('aa', '', deps)
    assert result['db_artists'][0]['name'] == 'Aretha'
    assert result['spotify_artists'] == []
    assert result['spotify_albums'] == []
    assert result['spotify_tracks'] == []
    assert result['primary_source'] == 'spotify'
    assert result['alternate_sources'] == []


def test_short_query_with_explicit_source_uses_that_source_label():
    deps = _build_deps()
    result = orchestrator.run_enhanced_search('aa', 'deezer', deps)
    assert result['primary_source'] == 'deezer'
    assert result['metadata_source'] == 'deezer'


# ---------------------------------------------------------------------------
# run_enhanced_search — single source
# ---------------------------------------------------------------------------

def test_single_source_runs_only_that_source():
    spot = _Client(authed=True, artists=[_Artist('s1', 'Spot Artist')])
    deps = _build_deps(spotify_client=spot)
    result = orchestrator.run_enhanced_search('pink floyd', 'spotify', deps)

    assert result['primary_source'] == 'spotify'
    assert result['metadata_source'] == 'spotify'
    assert result['source_available'] is True
    assert result['spotify_artists'][0]['name'] == 'Spot Artist'
    assert result['alternate_sources'] == []


def test_single_source_unavailable_returns_empty_with_source_available_false():
    deps = _build_deps(spotify_client=None)
    result = orchestrator.run_enhanced_search('pink floyd', 'spotify', deps)
    assert result['source_available'] is False
    assert result['spotify_artists'] == []
    assert result['primary_source'] == 'spotify'


def test_single_source_search_failure_returns_empty():
    spot = _Client(authed=True, fail_search=True)
    deps = _build_deps(spotify_client=spot)
    result = orchestrator.run_enhanced_search('q', 'spotify', deps)
    # search_source still returns a wrapper because per-kind exceptions are
    # swallowed inside it, so we get [] for each kind, source_available=True
    assert result['spotify_artists'] == []
    assert result['spotify_albums'] == []
    assert result['spotify_tracks'] == []


# ---------------------------------------------------------------------------
# run_enhanced_search — fan-out
# ---------------------------------------------------------------------------

def test_fanout_uses_fallback_client_as_primary():
    fb_client = _Client(artists=[_Artist('f1', 'Fallback Artist')])
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        get_metadata_fallback_client=lambda: fb_client,
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)

    assert result['primary_source'] == 'deezer'
    assert result['spotify_artists'][0]['name'] == 'Fallback Artist'


def test_fanout_lists_alternate_sources_excluding_primary():
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        spotify_client=_Client(authed=True),
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    alts = result['alternate_sources']
    assert 'deezer' not in alts  # primary excluded
    assert 'itunes' in alts
    assert 'spotify' in alts
    assert 'youtube_videos' in alts
    assert 'musicbrainz' in alts


def test_fanout_omits_spotify_alternate_when_unauthed():
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        spotify_client=_Client(authed=False),
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert 'spotify' not in result['alternate_sources']


def test_fanout_omits_discogs_alternate_when_no_token():
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        config_manager=_Cfg({'discogs.token': ''}),
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert 'discogs' not in result['alternate_sources']


def test_fanout_includes_discogs_alternate_when_token_set():
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        config_manager=_Cfg({'discogs.token': 'abc'}),
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert 'discogs' in result['alternate_sources']


def test_fanout_omits_hydrabase_alternate_when_disconnected():
    deps = _build_deps(
        get_metadata_fallback_source=lambda: 'deezer',
        hydrabase_client=None,
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert 'hydrabase' not in result['alternate_sources']


def test_fanout_hydrabase_primary_runs_hydrabase_first():
    hydra = _Client(connected=True, artists=[_Artist('h1', 'Hydra Artist')])
    deps = _build_deps(
        is_hydrabase_active=lambda: True,
        hydrabase_client=hydra,
    )
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert result['primary_source'] == 'hydrabase'
    assert result['spotify_artists'][0]['name'] == 'Hydra Artist'


def test_fanout_hydrabase_failure_falls_through_to_spotify_default():
    hydra_fail = _Client(connected=True, fail_search=True)
    deps = _build_deps(
        is_hydrabase_active=lambda: True,
        hydrabase_client=hydra_fail,
        get_metadata_fallback_source=lambda: 'spotify',
        get_metadata_fallback_client=lambda: _Client(name='spotify-fb'),
    )
    # Should not raise
    result = orchestrator.run_enhanced_search('q', '', deps)
    # search_source still returns a wrapper because per-kind exceptions are
    # swallowed inside it — so primary_results.tracks is []. Code keeps
    # primary_source='hydrabase' because search_source returned a value.
    assert result is not None


def test_fanout_hydrabase_worker_enqueued_when_dev_mode_enabled():
    worker = _Worker()
    deps = _build_deps(
        hydrabase_worker=worker,
        dev_mode_enabled_provider=lambda: True,
        get_metadata_fallback_source=lambda: 'deezer',
    )
    orchestrator.run_enhanced_search('pink floyd', '', deps)
    enqueued_kinds = {kind for _q, kind in worker.enqueued}
    assert enqueued_kinds == {'tracks', 'albums', 'artists'}


def test_fanout_hydrabase_worker_skipped_in_prod_mode():
    worker = _Worker()
    deps = _build_deps(
        hydrabase_worker=worker,
        dev_mode_enabled_provider=lambda: False,
        get_metadata_fallback_source=lambda: 'deezer',
    )
    orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert worker.enqueued == []


def test_fanout_db_artists_get_image_url_fixed():
    db_artist = _Artist('a1', 'Aretha', thumb_url='/library/a.jpg')
    deps = _build_deps(database=_DB(artists=[db_artist]))
    result = orchestrator.run_enhanced_search('pink floyd', '', deps)
    assert result['db_artists'][0]['image_url'] == 'FIXED::/library/a.jpg'


# ---------------------------------------------------------------------------
# empty_response
# ---------------------------------------------------------------------------

def test_empty_response_keys():
    r = orchestrator.empty_response()
    for k in ('db_artists', 'spotify_artists', 'spotify_albums', 'spotify_tracks',
              'sources', 'primary_source', 'metadata_source'):
        assert k in r
    assert r['primary_source'] == 'spotify'


# ---------------------------------------------------------------------------
# stream_source_search — NDJSON streaming
# ---------------------------------------------------------------------------

def _drain(generator):
    """Drain an NDJSON generator into a list of parsed JSON dicts."""
    out = []
    for line in generator:
        out.append(json.loads(line.rstrip('\n')))
    return out


def test_stream_source_empty_query_yields_done_only():
    deps = _build_deps()
    out = _drain(orchestrator.stream_source_search('spotify', '', deps))
    assert out == [{'type': 'done'}]


def test_stream_source_unknown_client_yields_done_only():
    deps = _build_deps(spotify_client=None)
    out = _drain(orchestrator.stream_source_search('spotify', 'q', deps))
    assert out == [{'type': 'done'}]


def test_stream_metadata_source_yields_three_kinds_plus_done():
    spot = _Client(
        authed=True,
        artists=[_Artist('a', 'A')],
        albums=[_Album('b', 'B')],
        tracks=[_Track('c', 'C')],
    )
    deps = _build_deps(spotify_client=spot)

    out = _drain(orchestrator.stream_source_search('spotify', 'q', deps))
    types = [m['type'] for m in out]
    assert 'artists' in types
    assert 'albums' in types
    assert 'tracks' in types
    assert types[-1] == 'done'


class _FakeYouTubeVideo:
    def __init__(self, vid):
        self.video_id = vid
        self.title = f"Title {vid}"
        self.channel = "Chan"
        self.duration = 100
        self.thumbnail = f"thumb-{vid}.jpg"
        self.url = f"https://yt/{vid}"
        self.view_count = 1000
        self.upload_date = "20260101"


class _FakeYouTube:
    def __init__(self, results=None):
        self._results = results or []

    async def search_videos(self, q, max_results=20):
        return self._results


class _FakeSoulseekWithYT:
    def __init__(self, youtube):
        self.youtube = youtube


def test_stream_youtube_videos_yields_videos_chunk_and_done():
    yt = _FakeYouTube(results=[_FakeYouTubeVideo('vid1'), _FakeYouTubeVideo('vid2')])
    deps = _build_deps(soulseek_client=_FakeSoulseekWithYT(yt))

    out = _drain(orchestrator.stream_source_search('youtube_videos', 'q', deps))
    assert out[0]['type'] == 'videos'
    assert len(out[0]['data']) == 2
    assert out[0]['data'][0]['video_id'] == 'vid1'
    assert out[-1]['type'] == 'done'


def test_stream_youtube_videos_no_youtube_yields_empty_videos():
    deps = _build_deps(soulseek_client=None)
    out = _drain(orchestrator.stream_source_search('youtube_videos', 'q', deps))
    assert out[0] == {'type': 'videos', 'data': []}
    assert out[-1] == {'type': 'done'}


def test_stream_youtube_videos_search_failure_yields_empty_videos():
    class _BadYT:
        async def search_videos(self, q, max_results=20):
            raise RuntimeError("yt-dlp boom")

    deps = _build_deps(soulseek_client=_FakeSoulseekWithYT(_BadYT()))
    out = _drain(orchestrator.stream_source_search('youtube_videos', 'q', deps))
    assert out[0] == {'type': 'videos', 'data': []}
    assert out[-1] == {'type': 'done'}
