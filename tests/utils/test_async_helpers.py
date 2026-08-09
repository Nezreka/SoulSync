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
    nothing in the logs. Creating the loop inside its own thread is what fixes
    it — this pins that under real cross-thread load."""
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


def test_run_async_adds_no_polling_latency_floor():
    """PR #1121 review: the queue+pump workaround polled at 100 Hz, so every
    call paid up to 10 ms of scheduling latency on every interpreter."""
    start = time.monotonic()
    for _ in range(50):
        run_async(asyncio.sleep(0))
    elapsed = time.monotonic() - start

    # 50 calls × up to 10 ms of pump latency each ≈ 0.25-0.5 s.
    assert elapsed < 0.15, f"50 trivial run_async calls took {elapsed:.3f}s"


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
