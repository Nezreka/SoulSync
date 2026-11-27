"""hybrid priority mode runs every query on a source before the next (#2330).

the worker looped the queries and each query ran the whole source chain, so
when soulseek found nothing for query 1, youtube got query 1 and its download
started before soulseek ever saw queries 2..n.
"""

from __future__ import annotations

import pytest

from core.downloads import task_worker as tw
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


class _Hit:
    def __init__(self, source, query):
        self.username = source
        self.filename = f'{source}/{query}.flac'


class _Source:
    """one download source. answers only the queries in ``finds``."""

    def __init__(self, name, log, finds=()):
        self.name = name
        self.log = log
        self.finds = set(finds)

    async def search(self, query, timeout=None, progress_callback=None):
        self.log.append((self.name, query))
        if query in self.finds:
            return [_Hit(self.name, query)], []
        return [], []


class _HybridOrchestrator:
    """runs the chain the way engine.search_with_fallback does: first source
    with any results wins, exclude_sources leave the chain."""

    mode = 'hybrid'

    def __init__(self, sources):
        self._sources = {s.name: s for s in sources}
        self.hybrid_order = [s.name for s in sources]
        self.search_modes = []

    def client(self, name):
        return self._sources.get(name)

    async def search(self, query, timeout=30, exclude_sources=None,
                     progress_callback=None, search_mode=None,
                     quality_profile_id=None, track_hint=None):
        self.search_modes.append(search_mode)
        blocked = {s.lower() for s in (exclude_sources or [])}
        chain = [n for n in self.hybrid_order if n.lower() not in blocked]
        if search_mode == 'best_quality':
            pooled = []
            for name in chain:
                tracks, _ = await self._sources[name].search(query)
                pooled.extend(tracks)
            return pooled, []
        for name in chain:
            tracks, albums = await self._sources[name].search(query)
            if tracks:
                return tracks, albums
        return [], []


class _Engine:
    def __init__(self, queries):
        self._queries = queries

    def generate_download_queries(self, track):
        return list(self._queries)

    @staticmethod
    def _title_is_distinctive_enough_to_broadcast(title):
        from core.matching_engine import MusicMatchingEngine
        return MusicMatchingEngine._title_is_distinctive_enough_to_broadcast(title)


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# no artist and the name equals the first query, so the query list is exactly
# the engine's
_TRACK = {'id': 'sp-1', 'name': 'Solo', 'artists': [], 'album': 'A',
          'duration_ms': 1000}
_QUERIES = ['Solo', 'q2', 'q3']


def _run(orch, started):
    download_tasks['t1'] = {'status': 'pending', 'track_info': dict(_TRACK)}

    def _attempt(task_id, candidates, track, batch_id, **kwargs):
        started.append(candidates[0].username)
        return True

    deps = tw.TaskWorkerDeps(
        download_orchestrator=orch,
        matching_engine=_Engine(_QUERIES),
        run_async=_run_async,
        try_source_reuse=lambda *a, **kw: False,
        store_batch_source=lambda *a, **kw: None,
        try_staging_match=lambda *a, **kw: False,
        get_valid_candidates=lambda r, t, q, p=None: list(r or []),
        attempt_download_with_candidates=_attempt,
        on_download_completed=lambda *a, **kw: None,
        recover_worker_slot=lambda *a, **kw: None,
    )
    tw.download_track_worker('t1', 'b1', deps)


def _priority(monkeypatch, mode='priority'):
    from core.quality import selection
    monkeypatch.setattr(selection, 'load_search_mode', lambda *a, **kw: mode)


def test_soulseek_gets_query_two_before_youtube_gets_query_one(monkeypatch):
    """the issue's order: soulseek empty for query 1, has the file for
    query 2. youtube has something for every query."""
    _priority(monkeypatch)
    log, started = [], []
    orch = _HybridOrchestrator([
        _Source('soulseek', log, finds={'q2'}),
        _Source('youtube', log, finds=set(_QUERIES)),
    ])
    _run(orch, started)
    assert log == [('soulseek', 'Solo'), ('soulseek', 'q2')]
    assert started == ['soulseek']


def test_next_source_runs_only_after_soulseek_had_every_query(monkeypatch):
    _priority(monkeypatch)
    log, started = [], []
    orch = _HybridOrchestrator([
        _Source('soulseek', log),
        _Source('youtube', log, finds=set(_QUERIES)),
    ])
    _run(orch, started)
    # soulseek all three, then youtube from query 1, and the first good
    # youtube hit ends it
    assert log == [('soulseek', 'Solo'), ('soulseek', 'q2'), ('soulseek', 'q3'),
                   ('youtube', 'Solo')]
    assert started == ['youtube']


def test_next_source_gets_every_query_not_just_two(monkeypatch):
    _priority(monkeypatch)
    log, started = [], []
    orch = _HybridOrchestrator([
        _Source('soulseek', log),
        _Source('youtube', log, finds={'q3'}),
    ])
    _run(orch, started)
    assert [q for s, q in log if s == 'youtube'] == ['Solo', 'q2', 'q3']
    assert started == ['youtube']


def test_three_sources_each_exhausted_in_order(monkeypatch):
    _priority(monkeypatch)
    log, started = [], []
    orch = _HybridOrchestrator([
        _Source('soulseek', log),
        _Source('hifi', log),
        _Source('youtube', log, finds={'q2'}),
    ])
    _run(orch, started)
    assert log == [('soulseek', q) for q in _QUERIES] + \
        [('hifi', q) for q in _QUERIES] + [('youtube', 'Solo'), ('youtube', 'q2')]
    assert started == ['youtube']


def test_unconfigured_first_source_hands_the_loop_to_the_next(monkeypatch):
    """soulseek not set up: youtube is first in the loop and is not searched
    a second time by the fallback."""
    _priority(monkeypatch)
    log, started = [], []
    sk = _Source('soulseek', log)
    sk.is_configured = lambda: False
    orch = _HybridOrchestrator([sk, _Source('youtube', log)])
    _run(orch, started)
    assert log == [('youtube', q) for q in _QUERIES]
    assert started == []


def test_best_quality_mode_still_pools_every_source_per_query(monkeypatch):
    _priority(monkeypatch, 'best_quality')
    log, started = [], []
    orch = _HybridOrchestrator([
        _Source('soulseek', log),
        _Source('youtube', log, finds={'q2'}),
    ])
    _run(orch, started)
    assert log == [('soulseek', 'Solo'), ('youtube', 'Solo'),
                   ('soulseek', 'q2'), ('youtube', 'q2')]
    assert started == ['youtube']
