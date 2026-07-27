"""Regression tests for the shared synchronous-to-async bridge."""

from __future__ import annotations

import asyncio
import gc
import subprocess
import sys
import threading
import time
from pathlib import Path

from utils import async_helpers
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
    started = threading.Event()
    release = threading.Event()
    baseline = set(async_helpers._active_tasks)

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

    gc.collect()
    retained = set(async_helpers._active_tasks) - baseline
    assert retained
    release.set()

    worker.join(2)
    assert not worker.is_alive()
    assert result == {"value": "finished"}
    deadline = time.monotonic() + 1
    while retained.intersection(async_helpers._active_tasks) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not retained.intersection(async_helpers._active_tasks)
