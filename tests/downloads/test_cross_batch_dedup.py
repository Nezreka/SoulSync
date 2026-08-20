"""Cross-batch download dedup.

When the same song sits in two concurrently-running batches and the faster
batch has already obtained the file, the slower batch must NOT re-download and
re-import it (which produced a confusing duplicate Completed row with no
AcoustID badge — the second copy imports as "already owned"). Instead the
slower task short-circuits to ``already_owned`` before searching, inheriting the
owner's verification so its row stays consistent.

We only short-circuit against a sibling that has already SUCCEEDED
(completed / post-processing / already_owned) — never one still in flight, so a
failed peer can never strand this track undownloaded.
"""

from __future__ import annotations

import threading

import pytest

from core.downloads import task_worker as tw
from core.downloads import lifecycle as lc
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


# ---------------------------------------------------------------------------
# Identity / sibling-finder unit tests
# ---------------------------------------------------------------------------

_TI = {'id': 'sp-1', 'name': 'Money', 'artists': ['Pink Floyd'],
       'album': 'DSOTM', 'duration_ms': 383000}


def _track():
    from core.spotify_client import Track as SpotifyTrack
    return SpotifyTrack(id='sp-1', name='Money', artists=['Pink Floyd'],
                        album='DSOTM', duration_ms=383000, popularity=0)


def test_finds_completed_sibling_with_same_identity():
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'verification_status': 'verified', 'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    owner_id, owner = tw._find_owning_sibling('dup', _track())
    assert owner_id == 'owner'
    assert owner['verification_status'] == 'verified'


def test_ignores_sibling_still_in_flight():
    # A sibling that is only searching/downloading hasn't obtained the file yet —
    # skipping against it could strand this track if that sibling later fails.
    download_tasks['owner'] = {'status': 'searching', 'track_info': _TI}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI}
    owner_id, owner = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_ignores_different_track():
    other = dict(_TI, name='Time')
    download_tasks['owner'] = {'status': 'completed', 'track_info': other}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


def test_excludes_self():
    download_tasks['dup'] = {'status': 'completed', 'track_info': _TI}
    owner_id, _ = tw._find_owning_sibling('dup', _track())
    assert owner_id is None


# ---------------------------------------------------------------------------
# Worker integration: dedup short-circuit
# ---------------------------------------------------------------------------

class _Rec:
    def __init__(self):
        self.calls = []

    def __call__(self, name):
        def _inner(*a, **kw):
            self.calls.append((name, a, kw))
        return _inner


class _FakeClient:
    def __init__(self):
        self.mode = 'soulseek'
        self.search_calls = []

    def client(self, name):
        return None

    async def search(self, query, timeout=30, exclude_sources=None):
        self.search_calls.append(query)
        return ([], None)


def _deps(rec):
    return tw.TaskWorkerDeps(
        download_orchestrator=_FakeClient(),
        matching_engine=type('M', (), {'generate_download_queries': lambda self, t: []})(),
        run_async=lambda coro: coro.close(),
        try_source_reuse=lambda *a, **kw: False,
        store_batch_source=rec('store_batch_source'),
        try_staging_match=lambda *a, **kw: False,
        get_valid_candidates=lambda *a, **kw: [],
        attempt_download_with_candidates=lambda *a, **kw: False,
        on_download_completed=rec('on_download_completed'),
        recover_worker_slot=rec('recover_worker_slot'),
    )


def test_worker_skips_redownload_when_sibling_already_owns():
    download_tasks['owner'] = {'status': 'completed', 'track_info': _TI,
                               'verification_status': 'verified', 'quality': 'FLAC',
                               'batch_id': 'bA'}
    download_tasks['dup'] = {'status': 'pending', 'track_info': _TI, 'batch_id': 'bB'}
    rec = _Rec()
    deps = _deps(rec)
    tw.download_track_worker('dup', 'bB', deps)

    assert download_tasks['dup']['status'] == 'already_owned'
    assert download_tasks['dup']['verification_status'] == 'verified'  # inherited
    assert download_tasks['dup']['quality'] == 'FLAC'
    assert download_tasks['dup']['_dedup_owned_by'] == 'owner'
    # Completion signalled as success, and NO download/search attempted.
    assert ('on_download_completed', ('bB', 'dup', True), {}) in rec.calls
    assert deps.download_orchestrator.search_calls == []


# ---------------------------------------------------------------------------
# Lifecycle: already_owned must count toward batch completion
# ---------------------------------------------------------------------------

class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeMonitor:
    def __getattr__(self, name):
        return lambda *a, **kw: None


def _lc_deps():
    rec = _Rec()
    return lc.LifecycleDeps(
        config_manager=_FakeConfig(),
        automation_engine=None,
        download_monitor=_FakeMonitor(),
        repair_worker=None,
        mb_worker=None,
        is_shutting_down=lambda: False,
        get_batch_lock=lambda bid: threading.Lock(),
        submit_download_track_worker=rec('submit_dl'),
        submit_failed_to_wishlist=rec('sf'),
        submit_failed_to_wishlist_with_auto_completion=rec('sfa'),
        process_failed_to_wishlist=rec('pf'),
        process_failed_to_wishlist_with_auto_completion=rec('pfa'),
        ensure_wishlist_track_format=lambda t: t,
        get_track_artist_name=lambda t: 'Artist',
        check_and_remove_from_wishlist=rec('cw'),
        regenerate_batch_m3u=rec('regen'),
        youtube_playlist_states={},
        tidal_discovery_states={},
        deezer_discovery_states={},
        spotify_public_discovery_states={},
    )


def test_already_owned_task_counts_as_finished_and_completes_batch():
    download_tasks['t1'] = {'status': 'already_owned', 'track_info': {'name': 'X'}}
    download_batches['b1'] = {
        'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
        'max_concurrent': 1, 'permanently_failed_tracks': [],
        'cancelled_tracks': set(), 'playlist_name': 'P',
    }
    lc.on_download_completed('b1', 't1', True, _lc_deps())
    # Batch must reach 'complete' — before the fix, already_owned wasn't counted
    # as finished so the batch hung forever.
    assert download_batches['b1'].get('phase') == 'complete'
