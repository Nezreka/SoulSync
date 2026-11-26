"""#2253: long-lived pools give their threads back when there is nothing to do."""

from __future__ import annotations

import threading
import time
from concurrent.futures import CancelledError, wait

import pytest

from core.idle_executor import IdleThreadPool


def _settle(pool, want, limit=3.0):
    end = time.monotonic() + limit
    while time.monotonic() < end and pool.thread_count != want:
        time.sleep(0.01)
    return pool.thread_count


def test_results_and_exceptions_come_back():
    pool = IdleThreadPool(max_workers=3)
    try:
        assert pool.submit(lambda a, b=0: a + b, 2, b=3).result(2) == 5
        with pytest.raises(ZeroDivisionError):
            pool.submit(lambda: 1 / 0).result(2)
        assert list(pool.map(lambda x: x * 2, range(5))) == [0, 2, 4, 6, 8]
    finally:
        pool.shutdown()


def test_never_more_threads_than_max_workers():
    pool = IdleThreadPool(max_workers=3, idle_timeout=5)
    gate = threading.Event()
    running = []
    try:
        futs = [pool.submit(lambda: (running.append(1), gate.wait(5))) for _ in range(10)]
        time.sleep(0.2)
        assert pool.thread_count == 3
        assert len(running) == 3
        gate.set()
        wait(futs, timeout=5)
        assert all(f.done() for f in futs)
    finally:
        gate.set()
        pool.shutdown()


def test_idle_threads_end_and_come_back_on_demand():
    pool = IdleThreadPool(max_workers=4, idle_timeout=0.1)
    try:
        wait([pool.submit(time.sleep, 0.05) for _ in range(4)], timeout=5)
        assert _settle(pool, 0) == 0
        assert pool.submit(lambda: 7).result(2) == 7
    finally:
        pool.shutdown()


def test_repeated_bursts_do_not_grow_the_process_thread_count():
    pool = IdleThreadPool(max_workers=4, idle_timeout=0.05)
    try:
        wait([pool.submit(time.sleep, 0.01) for _ in range(8)], timeout=5)
        _settle(pool, 0)
        baseline = threading.active_count()
        for _ in range(20):
            wait([pool.submit(time.sleep, 0.005) for _ in range(8)], timeout=5)
        _settle(pool, 0)
        time.sleep(0.05)
        assert threading.active_count() <= baseline
    finally:
        pool.shutdown()


def test_work_submitted_as_a_thread_times_out_still_runs():
    pool = IdleThreadPool(max_workers=1, idle_timeout=0.01)
    try:
        for _ in range(300):
            assert pool.submit(lambda: 1).result(2) == 1
            time.sleep(0.009)
    finally:
        pool.shutdown()


def test_shutdown_cancels_queued_work_and_refuses_more():
    pool = IdleThreadPool(max_workers=1)
    gate = threading.Event()
    started = threading.Event()
    first = pool.submit(lambda: (started.set(), gate.wait(5))[1])
    assert started.wait(2)
    queued = pool.submit(lambda: 1)
    pool.shutdown(wait=False, cancel_futures=True)
    gate.set()
    assert first.result(2) is True
    with pytest.raises(CancelledError):
        queued.result(2)
    with pytest.raises(RuntimeError):
        pool.submit(lambda: 1)
    assert _settle(pool, 0) == 0


def test_long_lived_pools_are_idle_releasing():
    from api import request as request_api  # noqa: F401 - the notify pool is lazy
    from api import source_playlists as sp
    from core import async_utils
    from core.metadata import multi_source_search

    for pool in (async_utils._SLOW_IO_EXECUTOR, async_utils._CONTROL_IO_EXECUTOR,
                 async_utils._CLIENT_ADD_EXECUTOR, multi_source_search._search_pool,
                 sp.tidal_discovery_executor, sp.ytmusic_discovery_executor):
        assert isinstance(pool, IdleThreadPool)
