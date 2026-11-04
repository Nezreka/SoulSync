"""Regression tests for the shared synchronous-to-async bridge."""

from __future__ import annotations

import asyncio
import gc
import subprocess
import sys
import threading
import time
from pathlib import Path

from utils.async_helpers import run_async


def test_concurrent_run_async_calls_interleave_instead_of_serializing():
    """Two run_async calls from different threads must run concurrently on
    the shared loop (each yielding at its own await points) rather than
    fully serialize one behind the other. A slow, unrelated coroutine must
    not head-of-line-block a fast one queued shortly after it."""
    results = {}

    def slow():
        run_async(asyncio.sleep(0.5))

    def fast():
        time.sleep(0.1)  # start once `slow` is already in flight
        start = time.monotonic()
        run_async(asyncio.sleep(0.01))
        results['fast_duration'] = time.monotonic() - start

    t_slow = threading.Thread(target=slow)
    t_fast = threading.Thread(target=fast)
    t_slow.start()
    t_fast.start()
    t_slow.join()
    t_fast.join()

    # Fully serialized behind `slow` would take ~0.4s (0.5 - 0.1 already
    # elapsed); real concurrency keeps it close to its own 0.01s sleep.
    assert results['fast_duration'] < 0.3, (
        f"fast run_async call took {results['fast_duration']}s -- "
        "it was serialized behind an unrelated slow call instead of "
        "running concurrently"
    )


def test_cross_thread_submissions_never_lose_a_wakeup():
    """The failure this bridge exists to prevent: a submission that never wakes
    the loop's selector, so the caller blocks on Future.result() forever with
    nothing in the logs. The queue/pump boundary makes progress independent of
    that runtime wake-up — this pins it under real cross-thread load."""
    errors = []

    def submitter(base):
        try:
            for index in range(60):
                assert run_async(
                    asyncio.sleep(0, result=base + index), timeout=10,
                ) == base + index
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(repr(exc))

    threads = [threading.Thread(target=submitter, args=(k * 1000,)) for k in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(t.is_alive() for t in threads), "a run_async caller hung"
    assert errors == []


def test_run_async_keeps_the_polling_fallback_below_the_old_latency_floor():
    """The reliable pump must not restore the old 10 ms per-call floor."""
    calls = 50
    start = time.monotonic()
    for _ in range(calls):
        run_async(asyncio.sleep(0))
    elapsed = time.monotonic() - start
    assert elapsed < 0.20, f"{calls} trivial run_async calls took {elapsed:.3f}s"


def test_a_stopped_loop_is_closed_so_no_submission_can_hang(isolated_async_loop):
    """``run_forever()`` returning leaves the loop OPEN — only the thread dies.
    ``is_closed()`` then stays False, so ``_get_loop`` hands the loop back and
    ``run_coroutine_threadsafe`` happily accepts a callback nothing will ever
    pump: the caller blocks on ``result()`` with the default ``timeout=None``
    forever, with nothing in the logs. Closing on the way out turns that into
    an immediate RuntimeError (which ``run_async`` recovers from) and releases
    the loop's epoll + self-pipe FDs."""
    helpers = isolated_async_loop
    loop, thread = helpers._loop, helpers._thread

    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)

    assert not thread.is_alive(), "the loop thread outlived its loop"
    assert loop.is_closed(), "a stopped loop must not stay open to submissions"


def test_run_async_recovers_when_the_loop_dies_between_lookup_and_submit(
    isolated_async_loop,
):
    """``run_async`` captures the loop and submits OUTSIDE the lock, so the
    loop can be torn down in that gap. The submission must rebuild rather than
    raise 'Event loop is closed' at a caller that did nothing wrong."""
    helpers = isolated_async_loop
    dead = helpers._get_loop()
    dead.call_soon_threadsafe(dead.stop)
    helpers._thread.join(5)
    assert dead.is_closed()

    # The stale loop is what a caller in the race window would be holding.
    assert helpers.run_async(asyncio.sleep(0, result="alive"), timeout=10) == "alive"
    assert helpers._get_loop() is not dead


def test_first_run_async_call_waits_for_event_loop_startup():
    repo_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio; "
                "from utils.async_helpers import run_async; "
                "assert run_async(asyncio.sleep(0, result='ready')) == 'ready'"
            ),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_submitted_task_is_retained_until_it_finishes():
    """asyncio keeps only WEAK references to tasks, so a suspended job has to
    be held by the submission machinery — otherwise an opportunistic GC cycle
    destroys it mid-flight and the caller blocks on result() forever. Asserted
    through the public behaviour (the call still returns after a collection),
    not through whichever internal happens to hold the reference."""
    started = threading.Event()
    release = threading.Event()

    async def suspended_job():
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "finished"

    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("value", run_async(suspended_job())),
    )
    worker.start()
    assert started.wait(2)

    for _ in range(3):
        gc.collect()
    release.set()

    worker.join(5)
    assert not worker.is_alive(), "the submitted job was collected mid-flight"
    assert result == {"value": "finished"}
