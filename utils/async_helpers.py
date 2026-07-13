import asyncio
import concurrent.futures
import queue
import threading

_loop = None
_thread = None
_jobs = queue.Queue()
_lock = threading.Lock()


def _run_loop(loop, ready):
    asyncio.set_event_loop(loop)
    ready.set()
    while True:
        coro, future = _jobs.get()
        if not future.set_running_or_notify_cancel():
            coro.close()
            continue
        try:
            result = loop.run_until_complete(coro)
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)


def _get_loop():
    global _loop, _thread
    if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
        with _lock:
            if _loop is None or _loop.is_closed() or _thread is None or not _thread.is_alive():
                _loop = asyncio.new_event_loop()
                ready = threading.Event()
                _thread = threading.Thread(target=_run_loop, args=(_loop, ready), daemon=True)
                _thread.start()
                if not ready.wait(timeout=5):
                    raise RuntimeError("Async event loop thread failed to start")
    return _loop


def run_async(coro):
    """Drop-in replacement for asyncio.run() that uses a single shared event loop.

    A dedicated daemon thread runs one event loop for the entire process.
    Callers submit coroutines and block until the result is ready.
    This avoids creating/destroying event loops per call (FD leak)
    and works correctly with both long-lived and short-lived threads.
    """
    _get_loop()
    future = concurrent.futures.Future()
    _jobs.put((coro, future))
    return future.result()
