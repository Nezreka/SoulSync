"""A slow indexer must not discard the responsive ones (#1151, Zombiehamser).

Prowlarr's search endpoint takes every indexer id in ONE request and answers
once. So a single slow or unreachable indexer holds the whole response past
SoulSync's read timeout — and a timed-out request yields NOTHING, throwing
away results the responsive indexers had already produced.

Fanning the search out, one request per indexer, means a failure costs only
its own slot. Wall time is still the slowest indexer, deliberately: waiting is
the price of never dropping a result that was going to arrive.
"""

from __future__ import annotations

import asyncio

import pytest

from core.download_plugins.torrent import prowlarr_search_with_variants
from core.prowlarr_client import (
    ProwlarrIndexer,
    ProwlarrSearchError,
    ProwlarrSearchResult,
)


def _result(indexer_id=1, title='Album [FLAC]'):
    return ProwlarrSearchResult(
        guid=f'g{indexer_id}', title=title, indexer_id=indexer_id,
        indexer_name=f'indexer{indexer_id}', protocol='torrent',
        download_url='http://t/f.torrent',
    )


class _FanOutProwlarr:
    """Per-indexer double: `per_indexer` maps id -> results or an Exception."""

    def __init__(self, per_indexer, ids=(1, 2, 3)):
        self.per_indexer = per_indexer
        self.ids = list(ids)
        self.searched: list = []

    def indexer_ids_for_protocol(self, ids, protocol):
        return list(ids)

    async def resolve_search_indexers(self, ids, protocol):
        return list(ids) or self.ids

    async def search_each_indexer(self, query, indexer_ids, categories=None, timeout=None):
        results, failures = [], []
        for indexer_id in indexer_ids:
            self.searched.append((query, indexer_id))
            outcome = self.per_indexer.get(indexer_id, [])
            if isinstance(outcome, Exception):
                failures.append(f'indexer {indexer_id}: {outcome}')
            else:
                results.extend(outcome)
        return results, failures


def _run(client, query='Some Album'):
    return asyncio.run(prowlarr_search_with_variants(client, query, 'torrent'))


# ── the reported bug ─────────────────────────────────────────────────────────

def test_a_slow_indexer_no_longer_discards_the_responsive_ones():
    """The whole report. Indexer 2 times out; 1 and 3 answered."""
    client = _FanOutProwlarr({
        1: [_result(1)],
        2: ProwlarrSearchError('read timeout after 75s'),
        3: [_result(3)],
    })

    results = _run(client)

    assert [r.indexer_id for r in results] == [1, 3]


def test_every_indexer_is_asked_separately():
    client = _FanOutProwlarr({1: [_result(1)], 2: [_result(2)], 3: [_result(3)]})

    _run(client)

    assert sorted(i for _q, i in client.searched) == [1, 2, 3]


def test_a_total_failure_still_raises_rather_than_reporting_zero_hits():
    """dd28-02's guarantee has to survive the fan-out: a transport failure
    must never look like "nothing matched"."""
    client = _FanOutProwlarr({
        1: ProwlarrSearchError('down'),
        2: ProwlarrSearchError('down'),
        3: ProwlarrSearchError('down'),
    })

    with pytest.raises(ProwlarrSearchError):
        _run(client)


def test_one_failure_does_not_poison_responsive_zero_hit_indexers():
    """A successful empty response is materially different from a transport
    failure. The failing indexer is isolated and relaxed variants may continue;
    the overall search should simply return no hits when none are found."""
    client = _FanOutProwlarr({
        1: [],
        2: ProwlarrSearchError('gateway timeout'),
        3: [],
    })

    assert _run(client) == []
    assert {indexer_id for _query, indexer_id in client.searched} == {1, 2, 3}


def test_the_failing_indexer_is_named():
    """The aggregated request structurally could not say WHICH indexer was
    the problem. The report asks for exactly that."""
    client = _FanOutProwlarr({
        1: ProwlarrSearchError('gateway timeout'),
        2: ProwlarrSearchError('gateway timeout'),
        3: ProwlarrSearchError('gateway timeout'),
    })

    with pytest.raises(ProwlarrSearchError) as caught:
        _run(client)

    assert 'indexer 1' in str(caught.value)


def test_partial_success_does_not_retry_the_relaxed_variants():
    """The amplification this also fixes. A timeout used to yield nothing, so
    the variant ladder kept going — 75s PER variant. Once the responsive
    indexers answer, the first variant is a hit and the loop stops."""
    client = _FanOutProwlarr({
        1: [_result(1)],
        2: ProwlarrSearchError('slow'),
    }, ids=[1, 2])

    _run(client, 'Artist - Album (2024) [Deluxe]')

    queries = {q for q, _i in client.searched}
    assert len(queries) == 1, f'retried relaxed variants despite a hit: {queries}'


# ── falling back ─────────────────────────────────────────────────────────────

class _UnresolvableProwlarr(_FanOutProwlarr):
    """Prowlarr cannot list its indexers — an older version, or unreachable."""

    async def resolve_search_indexers(self, ids, protocol):
        return []

    async def search(self, query, categories=None, indexer_ids=None, timeout=None):
        self.searched.append((query, 'aggregated'))
        return [_result(1)]


def test_it_falls_back_to_one_aggregated_request_when_ids_cannot_be_resolved():
    """A Prowlarr that cannot enumerate its indexers can still search. Reading
    an empty list as "search nothing" would break every such install."""
    client = _UnresolvableProwlarr({})

    results = _run(client)

    assert [r.indexer_id for r in results] == [1]
    assert client.searched == [('Some Album', 'aggregated')]


# ── the client's own fan-out ─────────────────────────────────────────────────

def test_resolve_expands_an_empty_allowlist_to_the_protocol_s_indexers(monkeypatch):
    """No allowlist means "every enabled indexer" — fine for one aggregated
    request, useless for a fan-out, so it is expanded here. Usenet indexers
    are excluded: asking them for a torrent is a guaranteed zero."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _indexers():
        return [
            ProwlarrIndexer(id=1, name='a', protocol='torrent', enable=True, privacy='public'),
            ProwlarrIndexer(id=2, name='b', protocol='usenet', enable=True, privacy='public'),
            ProwlarrIndexer(id=3, name='c', protocol='torrent', enable=False, privacy='public'),
            ProwlarrIndexer(id=4, name='d', protocol='torrent', enable=True, privacy='public'),
        ]

    monkeypatch.setattr(client, 'get_indexers', _indexers)

    assert asyncio.run(client.resolve_search_indexers([], 'torrent')) == [1, 4]


def test_resolve_keeps_an_explicit_allowlist_untouched(monkeypatch):
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    assert asyncio.run(client.resolve_search_indexers([7, 9], 'torrent')) == [7, 9]


def test_resolve_returns_empty_when_enumeration_fails(monkeypatch):
    """Empty means "fall back to one aggregated request", never "search
    nothing" — a Prowlarr that cannot list its indexers can still search."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _boom():
        raise RuntimeError('unreachable')

    monkeypatch.setattr(client, 'get_indexers', _boom)

    assert asyncio.run(client.resolve_search_indexers([], 'torrent')) == []


def test_search_each_indexer_runs_them_concurrently(monkeypatch):
    """Sequential requests would make the fan-out SLOWER than the aggregated
    call it replaces — N timeouts back to back instead of one."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()
    started: list = []

    async def _slow(query, categories=None, indexer_ids=None, limit=100,
                    search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        started.append(indexer_ids[0])
        await asyncio.sleep(0.05)
        return [_result(indexer_ids[0])]

    monkeypatch.setattr(client, 'search', _slow)

    async def _go():
        loop = asyncio.get_running_loop()
        begin = loop.time()
        results, failures = await client.search_each_indexer('q', [1, 2, 3])
        return results, failures, loop.time() - begin

    results, failures, elapsed = asyncio.run(_go())

    assert len(results) == 3 and failures == []
    assert elapsed < 0.12, f'ran sequentially ({elapsed:.2f}s for 3 x 0.05s)'


def test_search_each_indexer_separates_successes_from_failures(monkeypatch):
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _mixed(query, categories=None, indexer_ids=None, limit=100,
                     search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        if indexer_ids[0] == 2:
            raise ProwlarrSearchError('read timeout')
        return [_result(indexer_ids[0])]

    monkeypatch.setattr(client, 'search', _mixed)

    results, failures = asyncio.run(client.search_each_indexer('q', [1, 2, 3]))

    assert [r.indexer_id for r in results] == [1, 3]
    assert len(failures) == 1 and 'indexer 2' in failures[0]


def test_search_each_indexer_with_no_ids_does_nothing(monkeypatch):
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    assert asyncio.run(client.search_each_indexer('q', [])) == ([], [])


# ── not starving the shared thread pool ──────────────────────────────────────

def test_the_fan_out_is_bounded(monkeypatch):
    """`search` runs on the SHARED slow-I/O pool (16 workers, used by every
    provider in the app). Unbounded, this would hold one worker per indexer
    for the whole timeout — and once the pool is full the requests QUEUE,
    making the fan-out slower than the single request it replaces."""
    from core.prowlarr_client import MAX_CONCURRENT_INDEXER_SEARCHES, ProwlarrClient

    client = ProwlarrClient()
    live = 0
    peak = 0

    async def _tracked(query, categories=None, indexer_ids=None, limit=100,
                       search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.02)
            return [_result(indexer_ids[0])]
        finally:
            live -= 1

    monkeypatch.setattr(client, 'search', _tracked)

    results, _ = asyncio.run(client.search_each_indexer('q', list(range(1, 21))))

    assert len(results) == 20, 'every indexer must still be searched'
    assert peak <= MAX_CONCURRENT_INDEXER_SEARCHES, f'ran {peak} at once'
    assert MAX_CONCURRENT_INDEXER_SEARCHES < 16, 'must stay under the shared pool size'


def test_cancellation_propagates_rather_than_becoming_a_failure(monkeypatch):
    """On shutdown the search is cancelled. Recording that as "indexer 1
    failed" would swallow the cancellation and hand back partial results."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _cancelled(query, categories=None, indexer_ids=None, limit=100,
                         search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        raise asyncio.CancelledError()

    monkeypatch.setattr(client, 'search', _cancelled)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.search_each_indexer('q', [1, 2]))


# ── the overall deadline ─────────────────────────────────────────────────────

def test_the_fan_out_never_takes_longer_than_the_single_request_it_replaces(monkeypatch):
    """With more indexers than the concurrency cap, batches run back to back —
    so without ONE overall deadline, N dead indexers cost ceil(N/cap) x timeout
    where the aggregated request cost timeout once. That would make this change
    a regression for the very case it is meant to help."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _hangs(query, categories=None, indexer_ids=None, limit=100,
                     search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(client, 'search', _hangs)
    monkeypatch.setattr(client, 'resolve_search_timeout', lambda t=None: 0.15)

    async def _go():
        loop = asyncio.get_running_loop()
        begin = loop.time()
        results, failures = await client.search_each_indexer('q', list(range(1, 21)))
        return results, failures, loop.time() - begin

    results, failures, elapsed = asyncio.run(_go())

    assert results == []
    assert len(failures) == 20
    assert elapsed < 0.6, f'took {elapsed:.2f}s for a 0.15s budget — batches ran serially'


def test_results_that_arrived_inside_the_budget_are_kept(monkeypatch):
    """The whole point: a hung indexer must not discard the fast ones."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()

    async def _mixed(query, categories=None, indexer_ids=None, limit=100,
                     search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        if indexer_ids[0] == 2:
            await asyncio.sleep(30)
        return [_result(indexer_ids[0])]

    monkeypatch.setattr(client, 'search', _mixed)
    monkeypatch.setattr(client, 'resolve_search_timeout', lambda t=None: 0.2)

    results, failures = asyncio.run(client.search_each_indexer('q', [1, 2, 3]))

    assert sorted(r.indexer_id for r in results) == [1, 3]
    assert len(failures) == 1 and 'no answer within' in failures[0]


def test_a_repeated_indexer_id_is_searched_once(monkeypatch):
    """`prowlarr.indexer_ids` is free text and is not deduped upstream. A
    repeat was harmless when every id went into ONE request; with a fan-out it
    means a second request to the same indexer and its results counted twice."""
    from core.prowlarr_client import ProwlarrClient

    client = ProwlarrClient()
    asked: list = []

    async def _record(query, categories=None, indexer_ids=None, limit=100,
                      search_type='search', extra_params=None, timeout=None,
                    throttle=True):
        asked.append(indexer_ids[0])
        return [_result(indexer_ids[0])]

    monkeypatch.setattr(client, 'search', _record)

    results, _ = asyncio.run(client.search_each_indexer('q', [1, 1, 2, 2, 2]))

    assert sorted(asked) == [1, 2]
    assert len(results) == 2, 'a duplicated id produced duplicate candidates'


def test_duplicate_only_allowlist_still_reports_total_failure():
    client = _FanOutProwlarr(
        {1: ProwlarrSearchError('down')},
        ids=[1, 1],
    )

    with pytest.raises(ProwlarrSearchError):
        _run(client)
