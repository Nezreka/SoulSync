"""Tests for core/downloads/task_worker.py — per-task download worker."""

from __future__ import annotations

import pytest

from core.downloads import task_worker as tw
from core.runtime_state import download_tasks


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    yield
    download_tasks.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def _inner(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _inner


class _FakeClient:
    """Stub soulseek client. `mode` defaults to non-hybrid."""
    def __init__(self, results=None, mode='soulseek', subclients=None):
        self._results = results if results is not None else []
        self.mode = mode
        self.search_calls = []
        for k, v in (subclients or {}).items():
            setattr(self, k, v)

    async def search(self, query, timeout=30):
        self.search_calls.append((query, timeout))
        return (self._results, None)


class _FakeMatchEngine:
    def __init__(self, queries=None):
        self._queries = queries or []

    def generate_download_queries(self, track):
        return list(self._queries)


def _sync_run_async(coro):
    """Drain a coroutine on a fresh loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_deps(
    *,
    soulseek=None,
    matching=None,
    try_source_reuse=lambda *a, **kw: False,
    store_batch_source=None,
    try_staging_match=lambda *a, **kw: False,
    get_valid_candidates=lambda r, t, q: [],
    attempt_download_with_candidates=lambda *a, **kw: False,
    on_download_completed=None,
    recover_worker_slot=None,
):
    rec = _Recorder()
    return tw.TaskWorkerDeps(
        download_orchestrator=soulseek or _FakeClient(),
        matching_engine=matching or _FakeMatchEngine(),
        run_async=_sync_run_async,
        try_source_reuse=try_source_reuse,
        store_batch_source=store_batch_source or rec('store_batch_source'),
        try_staging_match=try_staging_match,
        get_valid_candidates=get_valid_candidates,
        attempt_download_with_candidates=attempt_download_with_candidates,
        on_download_completed=on_download_completed or rec('on_download_completed'),
        recover_worker_slot=recover_worker_slot or rec('recover_worker_slot'),
    ), rec


def _seed_task(task_id='t1', status='pending', track_info=None, **extra):
    download_tasks[task_id] = {
        'status': status,
        'track_info': track_info if track_info is not None else {
            'id': 'sp-1', 'name': 'Money', 'artists': ['Pink Floyd'],
            'album': 'DSOTM', 'duration_ms': 383000,
        },
        **extra,
    }


# ---------------------------------------------------------------------------
# Early-return guards
# ---------------------------------------------------------------------------

def test_missing_task_returns_silently():
    deps, rec = _build_deps()
    tw.download_track_worker('absent', 'b1', deps)
    assert rec.calls == []


def test_cancelled_v2_task_returns_without_completion_callback():
    """V2 tasks (with playlist_id) handle worker slot freeing themselves."""
    _seed_task(status='cancelled', playlist_id='pl1')
    deps, rec = _build_deps()
    tw.download_track_worker('t1', 'b1', deps)
    # NOT called — V2 system frees its own slots
    assert ('on_download_completed', ('b1', 't1', False), {}) not in rec.calls


def test_cancelled_legacy_task_calls_completion_callback():
    """Legacy tasks (no playlist_id) need on_download_completed to free slot."""
    _seed_task(status='cancelled')  # no playlist_id
    deps, rec = _build_deps()
    tw.download_track_worker('t1', 'b1', deps)
    assert ('on_download_completed', ('b1', 't1', False), {}) in rec.calls


def test_cancelled_no_batch_id_just_returns():
    _seed_task(status='cancelled')
    deps, rec = _build_deps()
    tw.download_track_worker('t1', None, deps)
    # Nothing called — no batch to notify
    assert rec.calls == []


# ---------------------------------------------------------------------------
# Source reuse + staging shortcuts
# ---------------------------------------------------------------------------

def test_source_reuse_hit_records_filename_and_returns():
    _seed_task()

    def _reuse_hit(task_id, batch_id, track):
        download_tasks[task_id]['filename'] = 'reused.flac'
        download_tasks[task_id]['username'] = 'u1'
        return True

    rec = _Recorder()
    deps, _ = _build_deps(
        try_source_reuse=_reuse_hit,
        store_batch_source=rec('store_batch_source'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert ('store_batch_source', ('b1', 'u1', 'reused.flac'), {}) in rec.calls


def test_source_reuse_hit_skips_store_when_no_filename():
    """Reuse returns True but doesn't write filename → no store call."""
    _seed_task()
    rec = _Recorder()
    deps, _ = _build_deps(
        try_source_reuse=lambda *a, **kw: True,
        store_batch_source=rec('store_batch_source'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert rec.calls == []  # neither store nor on_complete


def test_staging_match_hit_returns_immediately():
    _seed_task()
    rec = _Recorder()
    deps, _ = _build_deps(
        try_staging_match=lambda *a, **kw: True,
        store_batch_source=rec('store_batch_source'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert rec.calls == []


# ---------------------------------------------------------------------------
# Search loop happy path
# ---------------------------------------------------------------------------

def test_first_query_success_returns_after_storing_source():
    _seed_task()
    rec = _Recorder()

    def _attempt_success(task_id, candidates, track, batch_id):
        download_tasks[task_id]['filename'] = 'song.flac'
        download_tasks[task_id]['username'] = 'u1'
        return True

    deps, _ = _build_deps(
        soulseek=_FakeClient(results=['raw1', 'raw2']),
        matching=_FakeMatchEngine(queries=['Pink Floyd Money']),
        get_valid_candidates=lambda r, t, q: [{'username': 'u1', 'filename': 'song.flac'}],
        attempt_download_with_candidates=_attempt_success,
        store_batch_source=rec('store'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert ('store', ('b1', 'u1', 'song.flac'), {}) in rec.calls
    assert download_tasks['t1']['status'] == 'searching'


def test_no_results_marks_not_found_and_calls_completion():
    _seed_task()
    rec = _Recorder()
    deps, _ = _build_deps(
        soulseek=_FakeClient(results=[]),
        matching=_FakeMatchEngine(queries=['q1']),
        on_download_completed=rec('done'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'not_found'
    assert 'No match found' in download_tasks['t1']['error_message']
    assert ('done', ('b1', 't1', False), {}) in rec.calls


def test_results_but_no_valid_candidates_stores_raw_for_review():
    """Each query that returns results contributes top 20 to cached_candidates.
    With legacy fallback queries (track-only, cleaned), multiple queries fire."""
    _seed_task()
    deps, _ = _build_deps(
        soulseek=_FakeClient(results=[f'raw{i}' for i in range(30)]),
        matching=_FakeMatchEngine(queries=['q1']),
        get_valid_candidates=lambda r, t, q: [],  # nothing passes filter
    )
    tw.download_track_worker('t1', 'b1', deps)
    # Status: not_found
    assert download_tasks['t1']['status'] == 'not_found'
    # Raw results stored (top 20 PER query that returned results)
    assert len(download_tasks['t1']['cached_candidates']) >= 20
    assert len(download_tasks['t1']['cached_candidates']) % 20 == 0  # multiple of 20


def test_attempt_download_failure_falls_through_to_next_query():
    _seed_task()
    deps, _ = _build_deps(
        soulseek=_FakeClient(results=['r1']),
        matching=_FakeMatchEngine(queries=['q1', 'q2']),
        get_valid_candidates=lambda r, t, q: [{'x': 1}],  # both queries get candidates
        attempt_download_with_candidates=lambda *a, **kw: False,  # but download fails
    )
    tw.download_track_worker('t1', 'b1', deps)
    # Both queries tried, failed → not_found
    assert download_tasks['t1']['status'] == 'not_found'


# ---------------------------------------------------------------------------
# Cancellation mid-flight
# ---------------------------------------------------------------------------

def test_cancellation_mid_query_returns_without_completion():
    _seed_task()
    rec = _Recorder()

    def _cancel_during_search(query, timeout=30):
        download_tasks['t1']['status'] = 'cancelled'

        async def _empty():
            return ([], None)
        return _empty()

    sk = _FakeClient(results=[])
    sk.search = _cancel_during_search

    deps, _ = _build_deps(
        soulseek=sk,
        matching=_FakeMatchEngine(queries=['q1']),
        on_download_completed=rec('done'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    # No completion callback (cancellation prevents it)
    assert ('done', ('b1', 't1', False), {}) not in rec.calls


# ---------------------------------------------------------------------------
# Hybrid fallback
# ---------------------------------------------------------------------------

def test_hybrid_fallback_tries_secondary_sources():
    _seed_task()
    youtube_client = _FakeClient(results=['yt-r1'])
    sk = _FakeClient(
        results=[],  # primary source returns nothing
        mode='hybrid',
        subclients={
            'hybrid_order': ['soulseek', 'youtube'],
            'soulseek': _FakeClient(results=[]),
            'youtube': youtube_client,
            'tidal': None, 'qobuz': None, 'hifi': None, 'deezer_dl': None,
        },
    )

    def _attempt_yt_success(task_id, candidates, track, batch_id):
        return True

    deps, _ = _build_deps(
        soulseek=sk,
        matching=_FakeMatchEngine(queries=['q1']),
        get_valid_candidates=lambda r, t, q: [{'x': 1}] if r else [],
        attempt_download_with_candidates=_attempt_yt_success,
    )
    tw.download_track_worker('t1', 'b1', deps)
    # YouTube was searched
    assert len(youtube_client.search_calls) >= 1


def test_hybrid_fallback_skipped_when_mode_not_hybrid():
    _seed_task()
    yt = _FakeClient(results=['r1'])
    sk = _FakeClient(
        results=[], mode='soulseek',  # not hybrid
        subclients={'youtube': yt},
    )
    deps, _ = _build_deps(
        soulseek=sk,
        matching=_FakeMatchEngine(queries=['q1']),
    )
    tw.download_track_worker('t1', 'b1', deps)
    # Fallback didn't run — youtube never searched
    assert yt.search_calls == []


# ---------------------------------------------------------------------------
# Top-level exception path
# ---------------------------------------------------------------------------

def test_critical_exception_marks_failed_and_calls_completion():
    _seed_task()
    rec = _Recorder()

    def _broken_engine(track):
        raise RuntimeError("matching engine dead")

    me = _FakeMatchEngine()
    me.generate_download_queries = _broken_engine

    deps, _ = _build_deps(
        matching=me,
        on_download_completed=rec('done'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert download_tasks['t1']['status'] == 'failed'
    assert 'Unexpected error during download' in download_tasks['t1']['error_message']
    assert ('done', ('b1', 't1', False), {}) in rec.calls


def test_critical_exception_with_completion_failure_attempts_recovery():
    _seed_task()
    rec = _Recorder()

    def _broken_engine(track):
        raise RuntimeError("dead")

    def _broken_completion(*a, **kw):
        raise RuntimeError("completion dead")

    me = _FakeMatchEngine()
    me.generate_download_queries = _broken_engine

    deps, _ = _build_deps(
        matching=me,
        on_download_completed=_broken_completion,
        recover_worker_slot=rec('recover'),
    )
    tw.download_track_worker('t1', 'b1', deps)
    # Recovery attempted after completion callback failed
    assert ('recover', ('b1', 't1'), {}) in rec.calls


# ---------------------------------------------------------------------------
# Query generation edge cases
# ---------------------------------------------------------------------------

def test_artist_starting_with_the_uses_second_word():
    """Legacy fallback: 'The Beatles' → first_word becomes 'Beatles'."""
    _seed_task(track_info={
        'id': 'sp1', 'name': 'Help', 'artists': ['The Beatles'],
        'album': 'Help', 'duration_ms': 100000,
    })
    sk = _FakeClient(results=[])
    deps, _ = _build_deps(soulseek=sk, matching=_FakeMatchEngine(queries=[]))
    tw.download_track_worker('t1', 'b1', deps)
    # Searched queries should contain 'Help Beatles' (track + second word)
    queries = [q for q, _ in sk.search_calls]
    assert any('Beatles' in q for q in queries)


def test_track_with_parens_generates_cleaned_variant():
    """`Money (Remastered)` → also tries `Money` as fallback query."""
    _seed_task(track_info={
        'id': 'sp1', 'name': 'Money (Remastered)', 'artists': ['Pink Floyd'],
        'album': 'DSOTM', 'duration_ms': 100000,
    })
    sk = _FakeClient(results=[])
    deps, _ = _build_deps(soulseek=sk, matching=_FakeMatchEngine(queries=[]))
    tw.download_track_worker('t1', 'b1', deps)
    queries = [q for q, _ in sk.search_calls]
    # Cleaned variant included
    assert 'Money' in queries


def test_duplicate_queries_deduplicated_case_insensitive():
    """Generated + legacy queries dedupe by lowercase."""
    _seed_task(track_info={
        'id': 'sp1', 'name': 'X', 'artists': ['Y'],
        'album': '', 'duration_ms': 0,
    })
    sk = _FakeClient(results=[])
    deps, _ = _build_deps(
        soulseek=sk,
        # Engine generates same query as legacy 'track-only' fallback
        matching=_FakeMatchEngine(queries=['x', 'X']),
    )
    tw.download_track_worker('t1', 'b1', deps)
    # 'x' and 'X' dedupe to one search per case-insensitive match
    queries_lower = [q.lower() for q, _ in sk.search_calls]
    assert queries_lower.count('x') == 1
