"""Album-bundle serialization wait (#740 / Sokhi "too many searching").

_wait_for_batch_drain holds the album-pool worker until the batch's tasks all
reach a terminal state — so only a few albums are in flight at once instead of
every album flooding the shared download pool. It's a passive wait that must
also bail on shutdown / a removed batch / a safety cap.
"""

import threading
import time

import pytest

from core.runtime_state import download_batches, download_tasks, tasks_lock
from core.downloads import master, monitor


def _set_batch(bid, task_statuses):
    with tasks_lock:
        download_batches[bid] = {'queue': list(task_statuses.keys())}
        for tid, st in task_statuses.items():
            download_tasks[tid] = {'status': st}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with tasks_lock:
        for bid in ('b1', 'b2', 'b3', 'b4'):
            download_batches.pop(bid, None)
        for tid in ('t1', 't2', 't3'):
            download_tasks.pop(tid, None)


def test_returns_immediately_when_all_terminal():
    _set_batch('b1', {'t1': 'completed', 't2': 'failed', 't3': 'not_found'})
    start = time.time()
    master._wait_for_batch_drain('b1', poll_seconds=0.05, max_wait_seconds=5)
    assert time.time() - start < 1.0          # nothing in flight → no block


def test_returns_when_batch_missing():
    master._wait_for_batch_drain('nope', poll_seconds=0.05, max_wait_seconds=5)  # no hang


def test_waits_until_tasks_go_terminal():
    _set_batch('b2', {'t1': 'searching', 't2': 'downloading'})

    def finish():
        time.sleep(0.25)
        with tasks_lock:
            download_tasks['t1']['status'] = 'completed'
            download_tasks['t2']['status'] = 'failed'

    threading.Thread(target=finish, daemon=True).start()
    start = time.time()
    master._wait_for_batch_drain('b2', poll_seconds=0.05, max_wait_seconds=5)
    assert 0.2 < time.time() - start < 3.0     # held the slot until they finished


def test_bails_on_shutdown(monkeypatch):
    _set_batch('b3', {'t1': 'searching'})       # never terminal
    monkeypatch.setattr(monitor, 'IS_SHUTTING_DOWN', True)
    start = time.time()
    master._wait_for_batch_drain('b3', poll_seconds=0.05, max_wait_seconds=10)
    assert time.time() - start < 1.0            # didn't block app shutdown


def test_respects_safety_cap():
    _set_batch('b4', {'t1': 'searching'})       # never terminal
    start = time.time()
    master._wait_for_batch_drain('b4', poll_seconds=0.05, max_wait_seconds=0.3)
    assert 0.3 <= time.time() - start < 2.0     # released the slot after the cap
