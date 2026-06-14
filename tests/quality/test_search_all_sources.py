"""Engine.search_all_sources pools candidates from EVERY configured source
(best-quality mode), instead of stopping at the first satisfying one like
search_with_fallback. Ranking happens later in the orchestrator — this just
combines raw tracks. Excluded/exhausted and raising sources are skipped.
"""

import asyncio


class _Cand:
    def __init__(self, name):
        self.name = name


class _FakePlugin:
    def __init__(self, tracks, configured=True, raises=False):
        self._tracks = tracks
        self._configured = configured
        self._raises = raises
        self.searched = False

    def is_configured(self):
        return self._configured

    async def search(self, query, timeout=None, progress_callback=None):
        self.searched = True
        if self._raises:
            raise RuntimeError("boom")
        return (list(self._tracks), [])


def _engine_with(plugins):
    from core.download_engine.engine import DownloadEngine
    eng = object.__new__(DownloadEngine)
    eng._plugins = plugins
    return eng


def test_pools_candidates_from_all_sources():
    a = _FakePlugin([_Cand('a1'), _Cand('a2')])
    b = _FakePlugin([_Cand('b1')])
    eng = _engine_with({'a': a, 'b': b})

    tracks, albums = asyncio.run(eng.search_all_sources('q', ['a', 'b']))

    assert {t.name for t in tracks} == {'a1', 'a2', 'b1'}
    assert a.searched and b.searched
    assert albums == []


def test_skips_excluded_sources():
    a = _FakePlugin([_Cand('a1')])
    b = _FakePlugin([_Cand('b1')])
    eng = _engine_with({'a': a, 'b': b})

    tracks, _ = asyncio.run(
        eng.search_all_sources('q', ['a', 'b'], exclude_sources=['b'])
    )

    assert {t.name for t in tracks} == {'a1'}
    assert b.searched is False


def test_skips_unconfigured_and_swallows_raising_source():
    a = _FakePlugin([_Cand('a1')], configured=False)
    b = _FakePlugin([_Cand('b1')], raises=True)
    c = _FakePlugin([_Cand('c1')])
    eng = _engine_with({'a': a, 'b': b, 'c': c})

    tracks, _ = asyncio.run(eng.search_all_sources('q', ['a', 'b', 'c']))

    assert {t.name for t in tracks} == {'c1'}  # a skipped, b raised, c survives


def test_empty_when_all_sources_empty():
    a = _FakePlugin([])
    eng = _engine_with({'a': a})

    tracks, albums = asyncio.run(eng.search_all_sources('q', ['a']))

    assert tracks == []
    assert albums == []
