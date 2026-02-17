import asyncio
import threading

_loop = None
_thread = None
_lock = threading.Lock()


def _run_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_loop():
    global _loop, _thread
    if _loop is None or _loop.is_closed():
        with _lock:
            if _loop is None or _loop.is_closed():
                _loop = asyncio.new_event_loop()
                _thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
                _thread.start()
    return _loop


def run_async(coro):
    """Drop-in replacement for asyncio.run() that uses a single shared event loop.

    A dedicated daemon thread runs one event loop for the entire process.
    Callers submit coroutines and block until the result is ready.
    This avoids creating/destroying event loops per call (FD leak)
    and works correctly with both long-lived and short-lived threads.
    """
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
