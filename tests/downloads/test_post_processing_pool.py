"""Post-processing must not queue behind Soulseek searches.

Sokhi: "after a while the songs start getting stuck on processing and that
prevents other songs from being downloaded."

Nothing was hung. ``missing_download_executor`` is a 3-worker pool that ran
BOTH the search/download workers and post-processing, and the monitor sets a
task to ``post_processing`` the moment the transfer finishes — before the work
is queued:

    task['status'] = 'post_processing'                      # UI says "Processing"
    ...
    executor.submit(_run_post_processing_worker, ...)       # may just sit in line

So a finished download waited behind searches that take 25-60s each and usually
find nothing. From his log, task 66f9ed16:

    13:28:32  [Monitor] Submitting post-processing worker
    13:29:24  Waiting for file to stabilise      <- worker finally starts
    13:29:27  quarantined, requeued

52 seconds in line, 3 seconds of actual work. And ``post_processing`` counts as
active in batch healing, so the batch would not start its next song for the
whole 52s. That is the reported symptom exactly.

The fix is the one this codebase already made for #740, when album-bundle
downloads were starving the per-track flow off the same pool: give the work its
own bounded executor. Same reasoning, same shape.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.downloads import monitor as dm
from core.runtime_state import download_batches, download_tasks


@pytest.fixture
def wired(monkeypatch):
    """A monitor with real thread pools, so starvation is real and not mocked."""
    search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TestSearch")
    pp_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TestPostProcess")

    ran = threading.Event()
    submitted_to = []

    def _fake_post_processing_worker(task_id, batch_id):
        submitted_to.append(('post_processing', task_id))
        ran.set()

    monkeypatch.setattr(dm, 'missing_download_executor', search_pool)
    monkeypatch.setattr(dm, 'post_processing_executor', pp_pool, raising=False)
    monkeypatch.setattr(dm, '_run_post_processing_worker', _fake_post_processing_worker)
    monkeypatch.setattr(dm, '_download_track_worker',
                        lambda task_id, batch_id: submitted_to.append(('search', task_id)))
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    monkeypatch.setattr(dm, '_on_download_completed', lambda *a, **k: None)

    download_tasks.clear()
    download_batches.clear()
    download_tasks['t1'] = {
        'track_info': {'name': 'Track'}, 'username': 'peer', 'filename': 'x.flac',
        'status': 'downloading', 'download_id': 'dl-1',
        'status_change_time': time.time(), 'track_index': 0, 'batch_id': 'b1',
    }
    download_batches['b1'] = {'queue': ['t1'], 'queue_index': 1, 'active_count': 1,
                              'max_concurrent': 3, 'phase': 'downloading'}

    monitor = dm.WebUIDownloadMonitor()
    monitor.monitored_batches = {'b1'}
    # the post-lock half of _check_all_downloads returns early when the monitor
    # isn't running, and that half is where post-processing gets submitted
    monitor.monitoring = True
    # keyed the way _lookup_live_info looks it up for a soulseek task
    monkeypatch.setattr(monitor, '_get_live_transfers', lambda: {
        'peer::x.flac': {'state': 'Completed, Succeeded', 'size': 100,
                         'bytesTransferred': 100, 'username': 'peer',
                         'percentComplete': 100},
    })

    try:
        yield monitor, search_pool, pp_pool, ran, submitted_to
    finally:
        search_pool.shutdown(wait=False)
        pp_pool.shutdown(wait=False)
        download_tasks.clear()
        download_batches.clear()


def test_a_finished_download_is_processed_while_a_search_is_still_running(wired):
    """The regression. One search thread, held busy — exactly Sokhi's state, where
    every worker was mid-search. Post-processing must still start.

    On the shared pool this hangs until the search finishes, which in his case
    was 52 seconds and in a bad case is the full search timeout."""
    monitor, search_pool, _pp_pool, ran, submitted_to = wired

    release = threading.Event()
    search_pool.submit(release.wait)          # every search worker now busy
    try:
        monitor._check_all_downloads()
        assert ran.wait(timeout=5), (
            "post-processing never started while a search held the search pool"
        )
        assert ('post_processing', 't1') in submitted_to
    finally:
        release.set()


def test_the_task_still_moves_to_post_processing(wired):
    """The status transition itself is untouched — this fix is about which pool
    runs the work, not about when the task changes state."""
    monitor, _s, _p, ran, _sub = wired
    monitor._check_all_downloads()
    assert ran.wait(timeout=5)
    assert download_tasks['t1']['status'] == 'post_processing'


def test_search_work_still_goes_to_the_search_pool():
    """Only post-processing moves. Every place that queues a DOWNLOAD worker —
    the retry-after-quarantine path and the monitor's restart path — keeps using
    the shared pool it always did."""
    src = _source('core/downloads/monitor.py')
    assert src.count('missing_download_executor.submit(_download_track_worker') == 2


# ── the wiring, which no unit test can see ──────────────────────────────────

def _source(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def test_web_server_creates_a_separate_post_processing_pool():
    src = _source('web_server.py')
    assert 'post_processing_executor = ThreadPoolExecutor(' in src, (
        "the pool has to exist, or the fallback silently keeps the old behaviour")


def test_web_server_hands_the_pool_to_the_monitor_and_the_status_path():
    """Two submit sites, both had to move. The status path is the browser-driven
    one, the monitor path is the headless one."""
    src = _source('web_server.py')
    assert 'post_processing_executor_obj=post_processing_executor' in src
    assert ('submit_post_processing=lambda task_id, batch_id: post_processing_executor.submit'
            in src)
    assert 'submit_post_processing=lambda task_id, batch_id: missing_download_executor.submit' \
        not in src, "the status path still queues post-processing behind searches"


def test_the_monitor_no_longer_submits_post_processing_to_the_search_pool():
    src = _source('core/downloads/monitor.py')
    assert 'missing_download_executor.submit(_run_post_processing_worker' not in src
    assert '_post_processing_pool().submit(_run_post_processing_worker' in src


def test_init_actually_keeps_the_pool_it_is_handed():
    """The fallback below is a safety net, and a safety net can hide a snapped
    wire: drop the assignment in ``init`` and every finished download quietly
    goes back to queueing behind searches with nothing failing. So pin that the
    pool passed in is the pool used."""
    saved = {name: getattr(dm, name) for name in
             ('post_processing_executor', 'missing_download_executor',
              '_make_context_key', '_on_download_completed', '_download_track_worker',
              '_run_post_processing_worker', '_start_next_batch_of_downloads',
              '_orphaned_download_keys', 'download_orchestrator')}
    dedicated, shared = object(), object()
    try:
        dm.init(
            make_context_key=lambda u, f: '', on_download_completed=lambda *a, **k: None,
            download_track_worker=lambda *a: None, run_post_processing_worker=lambda *a: None,
            start_next_batch_of_downloads=lambda *a: None, orphaned_download_keys=set(),
            missing_download_executor_obj=shared, download_orchestrator_obj=None,
            post_processing_executor_obj=dedicated,
        )
        assert dm._post_processing_pool() is dedicated
        assert dm.missing_download_executor is shared
    finally:
        for name, value in saved.items():
            setattr(dm, name, value)


def test_only_the_shared_pool_wired_still_works():
    """Older init callers and tests patch the shared pool alone. That has to keep
    working — handing None to .submit() would turn every finished download into
    'Post-processing could not be scheduled'."""
    shared = object()
    dm_pp, dm_shared = dm.post_processing_executor, dm.missing_download_executor
    try:
        dm.post_processing_executor = None
        dm.missing_download_executor = shared
        assert dm._post_processing_pool() is shared
        dedicated = object()
        dm.post_processing_executor = dedicated
        assert dm._post_processing_pool() is dedicated
    finally:
        dm.post_processing_executor, dm.missing_download_executor = dm_pp, dm_shared


def test_the_new_pool_is_shut_down_with_the_others():
    """A pool left out of the shutdown list keeps its threads alive and the
    process never exits — the reason that list exists."""
    src = _source('web_server.py')
    assert '(post_processing_executor, "post processing executor"),' in src
