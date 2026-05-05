"""Tests for core/search/stream.py — single-track stream search."""

from __future__ import annotations

from core.search import stream


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


class _FakeMatchResult:
    def __init__(self, filename='match.flac', quality='Lossless'):
        self.username = 'u'
        self.filename = filename
        self.size = 100
        self.bitrate = 1411
        self.duration = 180
        self.quality = quality
        self.free_upload_slots = 1
        self.upload_speed = 1000
        self.queue_length = 0


class _FakeMatchingEngine:
    def __init__(self, match_for_query=None):
        self._match = match_for_query

    def find_best_slskd_matches_enhanced(self, temp, results, max_peer_queue=0):
        if self._match is None:
            return []
        return self._match


class _FakeStreamClient:
    def __init__(self, results_per_query=None):
        # Map query -> ([results], [])
        self._results = results_per_query or {}
        self.calls = []

    async def search(self, query, timeout=15):
        self.calls.append(query)
        return self._results.get(query, ([], []))


class _FakeSoulseek:
    def __init__(self, youtube=None, tidal=None, qobuz=None, hifi=None, deezer_dl=None, lidarr=None, results_per_query=None):
        self._clients = {
            'youtube': youtube,
            'tidal': tidal,
            'qobuz': qobuz,
            'hifi': hifi,
            'deezer_dl': deezer_dl,
            'lidarr': lidarr,
        }
        self._results = results_per_query or {}
        self.search_calls = []

    def client(self, name):
        return self._clients.get(name)

    async def search(self, query, timeout=15):
        self.search_calls.append(query)
        return self._results.get(query, ([], []))


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# _resolve_effective_stream_mode
# ---------------------------------------------------------------------------

def test_stream_source_youtube_returns_youtube():
    cfg = _FakeConfig({'download_source.stream_source': 'youtube'})
    assert stream._resolve_effective_stream_mode(cfg) == 'youtube'


def test_stream_source_active_with_hybrid_first_returns_first():
    cfg = _FakeConfig({
        'download_source.stream_source': 'active',
        'download_source.mode': 'hybrid',
        'download_source.hybrid_order': ['hifi', 'youtube', 'soulseek'],
    })
    assert stream._resolve_effective_stream_mode(cfg) == 'hifi'


def test_stream_source_active_with_soulseek_primary_falls_back_to_youtube():
    cfg = _FakeConfig({
        'download_source.stream_source': 'active',
        'download_source.mode': 'soulseek',
    })
    assert stream._resolve_effective_stream_mode(cfg) == 'youtube'


def test_stream_source_active_with_hybrid_soulseek_first_falls_back_to_youtube():
    cfg = _FakeConfig({
        'download_source.stream_source': 'active',
        'download_source.mode': 'hybrid',
        'download_source.hybrid_order': ['soulseek', 'youtube'],
    })
    assert stream._resolve_effective_stream_mode(cfg) == 'youtube'


def test_stream_source_active_non_hybrid_uses_mode_directly():
    cfg = _FakeConfig({
        'download_source.stream_source': 'active',
        'download_source.mode': 'tidal',
    })
    assert stream._resolve_effective_stream_mode(cfg) == 'tidal'


# ---------------------------------------------------------------------------
# _build_stream_queries
# ---------------------------------------------------------------------------

def test_build_queries_streaming_mode_includes_artist():
    qs = stream._build_stream_queries('Money', 'Pink Floyd', 'youtube')
    assert qs[0] == 'Pink Floyd Money'


def test_build_queries_streaming_mode_adds_cleaned_variant():
    qs = stream._build_stream_queries('Money (Remastered)', 'Pink Floyd', 'youtube')
    assert qs == ['Pink Floyd Money (Remastered)', 'Pink Floyd Money']


def test_build_queries_soulseek_mode_strips_artist():
    qs = stream._build_stream_queries('Money', 'Pink Floyd', 'soulseek')
    assert qs == ['Money']


def test_build_queries_soulseek_mode_adds_cleaned_variant():
    qs = stream._build_stream_queries('Money [Live]', 'Pink Floyd', 'soulseek')
    assert qs == ['Money [Live]', 'Money']


def test_build_queries_dedupes_case_insensitive():
    qs = stream._build_stream_queries('Money', 'Pink Floyd', 'soulseek')
    # Cleaned == original → dedup → only one entry
    assert qs == ['Money']


# ---------------------------------------------------------------------------
# stream_search_track
# ---------------------------------------------------------------------------

def test_stream_finds_match_on_first_query():
    youtube = _FakeStreamClient(results_per_query={
        'Pink Floyd Money': ([object()], []),
    })
    soul = _FakeSoulseek(youtube=youtube)
    cfg = _FakeConfig({'download_source.stream_source': 'youtube'})
    engine = _FakeMatchingEngine(match_for_query=[_FakeMatchResult()])

    result = stream.stream_search_track(
        track_name='Money', artist_name='Pink Floyd', album_name=None,
        duration_ms=180000,
        config_manager=cfg, soulseek_client=soul, matching_engine=engine,
        run_async=_run_async,
    )
    assert result is not None
    assert result['filename'] == 'match.flac'
    assert result['quality'] == 'Lossless'
    assert result['result_type'] == 'track'


def test_stream_walks_to_second_query_on_no_match():
    youtube = _FakeStreamClient(results_per_query={
        'Pink Floyd Money (Remastered)': ([], []),  # no results
        'Pink Floyd Money': ([object()], []),
    })
    soul = _FakeSoulseek(youtube=youtube)
    cfg = _FakeConfig({'download_source.stream_source': 'youtube'})
    engine = _FakeMatchingEngine(match_for_query=[_FakeMatchResult()])

    result = stream.stream_search_track(
        track_name='Money (Remastered)', artist_name='Pink Floyd', album_name=None,
        duration_ms=180000,
        config_manager=cfg, soulseek_client=soul, matching_engine=engine,
        run_async=_run_async,
    )
    assert result is not None
    # 2 queries tried
    assert len(youtube.calls) == 2


def test_stream_returns_none_when_no_matches():
    youtube = _FakeStreamClient(results_per_query={
        'Pink Floyd Money': ([object()], []),
    })
    soul = _FakeSoulseek(youtube=youtube)
    cfg = _FakeConfig({'download_source.stream_source': 'youtube'})
    engine = _FakeMatchingEngine(match_for_query=None)

    result = stream.stream_search_track(
        track_name='Money', artist_name='Pink Floyd', album_name=None,
        duration_ms=180000,
        config_manager=cfg, soulseek_client=soul, matching_engine=engine,
        run_async=_run_async,
    )
    assert result is None


def test_stream_falls_back_to_default_soulseek_when_no_direct_client():
    # Effective mode = 'youtube' (coerced from soulseek), but soul.youtube is None
    # → code falls through to soulseek_client.search directly. Streaming-mode
    # query gen → "Pink Floyd Money".
    soul = _FakeSoulseek(results_per_query={'Pink Floyd Money': ([object()], [])})
    cfg = _FakeConfig({
        'download_source.stream_source': 'active',
        'download_source.mode': 'soulseek',
    })
    engine = _FakeMatchingEngine(match_for_query=[_FakeMatchResult()])

    result = stream.stream_search_track(
        track_name='Money', artist_name='Pink Floyd', album_name=None,
        duration_ms=180000,
        config_manager=cfg, soulseek_client=soul, matching_engine=engine,
        run_async=_run_async,
    )
    assert result is not None
    assert 'Pink Floyd Money' in soul.search_calls


def test_stream_continues_past_per_query_exception():
    class _BoomFirst(_FakeStreamClient):
        def __init__(self):
            super().__init__()
            self._n = 0

        async def search(self, query, timeout=15):
            self.calls.append(query)
            self._n += 1
            if self._n == 1:
                raise RuntimeError("boom")
            return ([object()], [])

    youtube = _BoomFirst()
    soul = _FakeSoulseek(youtube=youtube)
    cfg = _FakeConfig({'download_source.stream_source': 'youtube'})
    engine = _FakeMatchingEngine(match_for_query=[_FakeMatchResult()])

    result = stream.stream_search_track(
        track_name='Money (Live)', artist_name='Pink Floyd', album_name=None,
        duration_ms=180000,
        config_manager=cfg, soulseek_client=soul, matching_engine=engine,
        run_async=_run_async,
    )
    # First query raised, second succeeded
    assert result is not None
    assert len(youtube.calls) == 2
