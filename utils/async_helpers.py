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
_active_tasks = set()
# dd28-47: the pump task must stay referenced (asyncio only holds weak refs)
# AND its death must be observable. Without either, a pump that raised left the
# thread apparently alive while every subsequent run_async blocked forever,
# with nothing in the logs but an eventual GC warning.
_pump_task = None


class AsyncCallTimeout(TimeoutError):
    """A ``run_async`` call exceeded the caller's explicit budget (dd28-17)."""


async def _finish_job(coro, future):
    """Run one submitted coroutine without serializing unrelated jobs."""
    if not future.set_running_or_notify_cancel():
        coro.close()
        return
    try:
        result = await coro
    except BaseException as exc:
        future.set_exception(exc)
    else:
        future.set_result(result)


async def _pump_jobs():
    """Move cross-thread queue entries onto the owner loop as concurrent tasks.

    Python 3.14.6 can lose the selector wakeup used by
    ``run_coroutine_threadsafe`` in long-lived pytest/application processes.
    The queue boundary avoids that runtime bug. A short idle poll is bounded
    to one lightweight loop wake per 10 ms; once jobs are present they are all
    scheduled in the same turn and interleave normally.
    """
    while True:
        while True:
            try:
                coro, future = _jobs.get_nowait()
            except queue.Empty:
                break
            task = asyncio.create_task(_finish_job(coro, future))
            # asyncio only keeps weak references to tasks. A submitted network
            # job must remain alive while it is suspended, otherwise an
            # opportunistic GC cycle can destroy it and leave Future.result()
            # blocked forever.
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)
        await asyncio.sleep(0.01)


def _on_pump_done(task):
    """Surface a dead pump instead of letting callers hang forever (dd28-47)."""
    global _pump_task
    _pump_task = None
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        logger.error(
            "Async loop pump exited unexpectedly; the shared loop will be "
            "rebuilt on the next call."
        )
    else:
        logger.error("Async loop pump crashed: %r", exc)
    # Stop the loop so ``_get_loop`` rebuilds the whole thread on the next
    # call. Leaving it running would keep _thread.is_alive() True while
    # nothing drains the queue — exactly the silent hang this guards against.
    loop = task.get_loop()
    try:
        loop.call_soon_threadsafe(loop.stop)
    except RuntimeError:
        pass
    # Fail everything already queued rather than leaving those callers blocked.
    while True:
        try:
            coro, future = _jobs.get_nowait()
        except queue.Empty:
            break
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        if not future.done():
            future.set_exception(
                RuntimeError("Async loop pump died before this job ran")
            )


def _run_loop(ready, holder):
    # Python 3.14.6 requires the selector loop to be CREATED in the thread
    # that owns/runs it. Creating it in the caller and merely installing it
    # here can leave run_coroutine_threadsafe() unable to wake the selector:
    # the loop sits in select() forever while callers block on Future.result().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    holder["loop"] = loop
    # Scheduled via call_soon (same-thread), not set as the first statement:
    # this only fires once run_forever() actually starts pumping the loop's
    # ready queue, so _get_loop() can safely hand the loop to
    # run_coroutine_threadsafe() the instant `ready` is set, closing the
    # startup race where a caller submits before the loop is truly running.
    try:
        global _pump_task
        _pump_task = loop.create_task(_pump_jobs())
        _pump_task.add_done_callback(_on_pump_done)
        loop.call_soon(ready.set)
        loop.run_forever()
    except BaseException as exc:
        holder["error"] = exc
        ready.set()
        raise


def _get_loop():
    global _loop, _thread
    if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
        with _lock:
            if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
                ready = threading.Event()
                holder = {}
                _thread = threading.Thread(
                    target=_run_loop,
                    args=(ready, holder),
                    daemon=True,
                    name="SoulSyncAsyncLoop",
                )
                _thread.start()
                if not ready.wait(timeout=5):
                    raise RuntimeError("Async event loop thread failed to start")
                if holder.get("error") is not None:
                    raise RuntimeError("Async event loop thread failed") from holder["error"]
                _loop = holder.get("loop")
                if _loop is None:
                    raise RuntimeError("Async event loop thread started without a loop")
    return _loop


def run_async(coro, *, timeout=None):
    """Drop-in replacement for asyncio.run() that uses a single shared event loop.

    A dedicated daemon thread runs one event loop for the entire process.
    Callers submit coroutines and block until the result is ready.
    This avoids creating/destroying event loops per call (FD leak),
    works correctly with both long-lived and short-lived threads, and lets
    concurrently-submitted coroutines interleave at their own await points
    instead of fully serializing one caller behind another.

    ``timeout`` (seconds) bounds the wait and raises :class:`AsyncCallTimeout`.
    dd28-17: the default stays unbounded, because legitimate long jobs (album
    polling, transfers) run through here — but a caller holding a lock while it
    waits on an external client MUST pass one, or a single hung HTTP call
    freezes that subsystem permanently with no error anywhere.
    """
    _get_loop()
    future = concurrent.futures.Future()
    _jobs.put((coro, future))
    if timeout is None:
        return future.result()
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise AsyncCallTimeout(
            f"async call did not finish within {timeout}s"
        ) from exc
