"""orchestrator.search routes to the right engine method per search_mode:

- priority (default)         → engine.search_with_fallback (first source wins)
- best_quality + hybrid      → engine.search_all_sources (pool every source)
- single-source mode         → the single client's search (toggle is a no-op)
"""

import asyncio

import core.download_orchestrator as orch_mod
from core.download_orchestrator import DownloadOrchestrator


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _SpyEngine:
    def __init__(self):
        self.calls = []

    async def search_with_fallback(self, query, chain, timeout=None, progress_callback=None):
        self.calls.append(('fallback', tuple(chain)))
        return (['fb'], [])

    async def search_all_sources(self, query, chain, timeout=None,
                                 progress_callback=None, exclude_sources=None):
        self.calls.append(('all', tuple(chain)))
        return (['pool'], [])


def _hybrid_orch():
    orch = DownloadOrchestrator.__new__(DownloadOrchestrator)
    orch.mode = 'hybrid'
    orch.hybrid_order = ['soulseek', 'hifi']
    orch.hybrid_primary = 'soulseek'
    orch.hybrid_secondary = 'hifi'
    orch.engine = _SpyEngine()
    # _resolve_source_chain normalizes names through the registry; stub it so the
    # test doesn't need a full registry.
    orch._resolve_source_chain = lambda: ['soulseek', 'hifi']
    return orch


def test_priority_mode_uses_search_with_fallback(monkeypatch):
    monkeypatch.setattr(orch_mod, 'load_search_mode', lambda: 'priority')
    orch = _hybrid_orch()

    tracks, _ = _run(orch.search('q'))

    assert orch.engine.calls == [('fallback', ('soulseek', 'hifi'))]
    assert tracks == ['fb']


def test_best_quality_mode_uses_search_all_sources(monkeypatch):
    monkeypatch.setattr(orch_mod, 'load_search_mode', lambda: 'best_quality')
    orch = _hybrid_orch()

    tracks, _ = _run(orch.search('q'))

    assert orch.engine.calls == [('all', ('soulseek', 'hifi'))]
    assert tracks == ['pool']
