import asyncio
import concurrent.futures
import queue
import threading

_loop = None
_thread = None
_lock = threading.Lock()
_jobs = queue.Queue()
_active_tasks = set()


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
        loop.create_task(_pump_jobs())
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


def run_async(coro):
    """Drop-in replacement for asyncio.run() that uses a single shared event loop.

    A dedicated daemon thread runs one event loop for the entire process.
    Callers submit coroutines and block until the result is ready.
    This avoids creating/destroying event loops per call (FD leak),
    works correctly with both long-lived and short-lived threads, and lets
    concurrently-submitted coroutines interleave at their own await points
    instead of fully serializing one caller behind another.
    """
    _get_loop()
    future = concurrent.futures.Future()
    _jobs.put((coro, future))
    return future.result()
