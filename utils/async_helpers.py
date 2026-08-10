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
    finally:
        # run_forever() returning does NOT close the loop — only this thread
        # dies. A loop left open still answers is_closed() with False, so
        # _get_loop() hands it back and run_coroutine_threadsafe accepts a
        # callback nothing will ever pump: the caller blocks on result() with
        # the default timeout=None forever, with nothing in the logs. That is
        # the silent-hang class this module exists to prevent. Closing here
        # makes a submission into that window an immediate RuntimeError
        # (run_async rebuilds and retries) and releases the loop's epoll +
        # self-pipe FDs, which is the FD-leak avoidance run_async documents.
        try:
            _drain_pending(loop)
        finally:
            # Nested so the close happens even if the drain is interrupted —
            # a loop left open is the very failure this block exists for.
            try:
                asyncio.set_event_loop(None)
                loop.close()
            except Exception:  # noqa: S110 - this thread is dying and the loop
                pass           # it would log through is the one being closed.


def _drain_pending(loop):
    """Cancel and settle whatever was still in flight when the loop stopped.

    Without this, work already submitted resolves nowhere: the concurrent
    Future handed to the caller is chained to an asyncio task that will never
    run again, so the caller waits forever. Cancelling and pumping the loop one
    last time propagates a CancelledError to every such caller instead.
    """
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:  # noqa: S110 - best-effort teardown: the caller's own
        pass           # CancelledError is the signal that matters, not this.


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


def _submit(coro):
    """Schedule ``coro`` on the shared loop, rebuilding it once if it died.

    The loop is looked up and submitted to OUTSIDE ``_lock`` (holding it across
    a submission would serialise every caller), so the loop can be torn down in
    that gap. It is closed on the way out, so the submission raises instead of
    hanging — and since ``call_soon_threadsafe`` raises before it touches the
    coroutine, the same object can go straight onto the replacement loop. One
    retry is enough: ``_get_loop`` rebuilds on a closed loop, so a second
    failure is a real one and propagates.
    """
    loop = _get_loop()
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except RuntimeError:
        return asyncio.run_coroutine_threadsafe(coro, _get_loop())


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
    future = _submit(coro)
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
