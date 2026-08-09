import asyncio
import concurrent.futures
import threading

_loop = None
_thread = None
_lock = threading.Lock()


class AsyncCallTimeout(TimeoutError):
    """A ``run_async`` call exceeded the caller's explicit budget (dd28-17)."""


def _run_loop(ready, holder):
    # The loop must be CREATED in the thread that owns and runs it. Creating it
    # in the caller and merely installing it here can leave
    # run_coroutine_threadsafe() unable to wake the selector on Python 3.14.6:
    # the loop sits in select() forever while callers block on Future.result(),
    # with no error anywhere. This was the whole cause of the silent-hang class
    # of bugs — an earlier revision routed every job through a 100 Hz polling
    # pump to sidestep it, which cost ~10 ms of latency per call on every
    # interpreter; with the loop created here, run_coroutine_threadsafe is
    # reliable again (verified on 3.14.6 under 3200 cross-thread submissions).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    holder["loop"] = loop
    # Scheduled via call_soon (same-thread), not set as the first statement:
    # this only fires once run_forever() actually starts pumping the loop's
    # ready queue, so _get_loop() can safely hand the loop to
    # run_coroutine_threadsafe() the instant `ready` is set, closing the
    # startup race where a caller submits before the loop is truly running.
    try:
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
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    if timeout is None:
        return future.result()
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        # Unlike a queued job, this reaches the running task: the coroutine is
        # actually cancelled instead of being left to finish unobserved.
        future.cancel()
        raise AsyncCallTimeout(
            f"async call did not finish within {timeout}s"
        ) from exc
