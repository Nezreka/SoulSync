"""Reliable synchronous-to-async bridge shared by background workers.

Python 3.14.6 can lose the selector wake-up behind
``run_coroutine_threadsafe`` in a long-lived cross-thread loop. Creating the
loop in its owner thread narrows that race but does not remove it on every
runtime/kernel combination. Submissions therefore cross a regular thread-safe
queue, while a small loop-owned pump schedules them as concurrent tasks.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import threading


logger = logging.getLogger(__name__)

_loop = None
_thread = None
_lock = threading.Lock()
_jobs = queue.Queue()
_cancellations = queue.Queue()
_active_tasks = set()
_future_tasks = {}
_pump_task = None
_accepting = False

# Short enough not to impose the former 10 ms per-call floor, but still a
# bounded selector timeout that makes a lost cross-thread wake-up harmless.
_PUMP_INTERVAL_SECONDS = 0.001


class AsyncCallTimeout(TimeoutError):
    """A ``run_async`` call exceeded the caller's explicit budget (dd28-17)."""


async def _finish_job(coro, future):
    """Run one queued coroutine without serializing unrelated submissions."""
    if not future.set_running_or_notify_cancel():
        coro.close()
        return
    try:
        result = await coro
    except BaseException as exc:
        if not future.done():
            future.set_exception(exc)
    else:
        if not future.done():
            future.set_result(result)


def _forget_task(task, future):
    _active_tasks.discard(task)
    _future_tasks.pop(future, None)


async def _pump_jobs():
    """Move cross-thread jobs onto the owner loop as concurrent tasks."""
    while True:
        while True:
            try:
                future = _cancellations.get_nowait()
            except queue.Empty:
                break
            task = _future_tasks.get(future)
            if task is not None and not task.done():
                task.cancel()

        while True:
            try:
                coro, future = _jobs.get_nowait()
            except queue.Empty:
                break
            if future.cancelled():
                coro.close()
                continue
            task = asyncio.create_task(_finish_job(coro, future))
            _active_tasks.add(task)
            _future_tasks[future] = task
            task.add_done_callback(
                lambda finished, submitted=future: _forget_task(finished, submitted)
            )

        await asyncio.sleep(_PUMP_INTERVAL_SECONDS)


def _fail_queued(exc):
    """Resolve jobs a dying loop never got a chance to schedule."""
    while True:
        try:
            coro, future = _jobs.get_nowait()
        except queue.Empty:
            return
        try:
            coro.close()
        except Exception:  # noqa: S110 - best-effort ownership cleanup
            pass
        if not future.done():
            future.set_exception(exc)


def _drain_pending(loop):
    """Cancel and settle tasks still owned by a loop that is stopping."""
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
    except Exception:  # noqa: S110 - the loop is already on its way down
        pass


def _run_loop(ready, holder):
    global _accepting, _pump_task

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    holder["loop"] = loop
    try:
        _pump_task = loop.create_task(_pump_jobs())
        # This fires only after run_forever actually starts processing work.
        loop.call_soon(ready.set)
        loop.run_forever()
    except BaseException as exc:
        holder["error"] = exc
        ready.set()
        raise
    finally:
        # Serialize shutdown against enqueue: anything accepted before this
        # boundary is either settled by the loop or failed explicitly.
        with _lock:
            _accepting = False
            _fail_queued(RuntimeError("Async event loop stopped before this job ran"))
        try:
            _drain_pending(loop)
        finally:
            _pump_task = None
            _active_tasks.clear()
            _future_tasks.clear()
            try:
                asyncio.set_event_loop(None)
                loop.close()
            except Exception:  # noqa: S110 - the owner thread is terminating
                pass


def _ensure_loop_locked():
    """Return a live loop while ``_lock`` prevents the enqueue/shutdown race."""
    global _accepting, _loop, _thread

    if (
        _loop is None
        or _loop.is_closed()
        or _thread is None
        or not _thread.is_alive()
        or not _accepting
    ):
        ready = threading.Event()
        holder = {}
        _accepting = True
        _thread = threading.Thread(
            target=_run_loop,
            args=(ready, holder),
            daemon=True,
            name="SoulSyncAsyncLoop",
        )
        _thread.start()
        if not ready.wait(timeout=5):
            _accepting = False
            raise RuntimeError("Async event loop thread failed to start")
        if holder.get("error") is not None:
            _accepting = False
            raise RuntimeError("Async event loop thread failed") from holder["error"]
        _loop = holder.get("loop")
        if _loop is None:
            _accepting = False
            raise RuntimeError("Async event loop thread started without a loop")
    return _loop


def _get_loop():
    with _lock:
        return _ensure_loop_locked()


def _submit(coro):
    """Atomically select the live loop generation and enqueue one coroutine."""
    with _lock:
        _ensure_loop_locked()
        future = concurrent.futures.Future()
        _jobs.put((coro, future))
        return future


def run_async(coro, *, timeout=None):
    """Run ``coro`` on the process-wide async loop and return its result.

    Different callers interleave on the same event loop. ``timeout`` bounds only
    the synchronous wait; when it expires, the pump also cancels the underlying
    asyncio task so it cannot continue unnoticed.
    """
    future = _submit(coro)
    if timeout is None:
        return future.result()
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        _cancellations.put(future)
        raise AsyncCallTimeout(
            f"async call did not finish within {timeout}s"
        ) from exc
