import asyncio
import concurrent.futures
import threading
import time

import pytest

import core.async_utils as async_utils
from core.async_utils import run_blocking, run_control


def test_run_blocking_returns_without_creating_a_loop_default_executor():
    async def probe():
        loop = asyncio.get_running_loop()
        result = await run_blocking(lambda left, right: left + right, 2, 3)
        return result, getattr(loop, "_default_executor", None)

    assert asyncio.run(probe()) == (5, None)


def test_run_blocking_propagates_worker_exception():
    def fail():
        raise ValueError("worker failed")

    with pytest.raises(ValueError, match="worker failed"):
        asyncio.run(run_blocking(fail))


def test_run_blocking_cancellation_does_not_create_a_default_executor():
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        release.wait(2)

    async def probe():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(run_blocking(block))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return getattr(loop, "_default_executor", None)

    try:
        assert asyncio.run(probe()) is None
    finally:
        release.set()


def test_control_calls_are_not_starved_by_slow_calls(monkeypatch):
    slow = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    control = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(async_utils, "_SLOW_IO_EXECUTOR", slow)
    monkeypatch.setattr(async_utils, "_CONTROL_IO_EXECUTOR", control)
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        release.wait(2)

    async def probe():
        task = asyncio.create_task(run_blocking(block))
        while not started.is_set():
            await asyncio.sleep(0)
        assert await asyncio.wait_for(run_control(lambda: "ready"), 0.5) == "ready"
        release.set()
        await task

    try:
        asyncio.run(probe())
    finally:
        release.set()
        slow.shutdown()
        control.shutdown()


def test_run_blocking_has_no_polling_latency_floor():
    """PR #1121 review: the old ``while not done: await sleep(0.05)`` put a
    50 ms floor under every call. The reliable Python-3.14 fallback must remain
    well below that old floor.

    Five sequential calls under the former 50 ms loop necessarily cost at
    least 250 ms. Keep a generous budget below that regression boundary; a
    direct ``asyncio.wrap_future`` control cannot be used on Python 3.14.6
    because the lost wake-up this helper avoids can hang the control itself.
    """
    calls = 5

    async def probe():
        start = time.perf_counter()
        for _ in range(calls):
            await run_blocking(lambda: None)
        return time.perf_counter() - start

    elapsed = asyncio.run(probe())

    assert elapsed < 0.10, (
        f"{calls} run_blocking calls took {elapsed:.3f}s; the short fallback "
        "appears to have regained the old 50 ms polling floor"
    )
