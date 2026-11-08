"""Say why HTTP stopped answering when every request thread is taken.

Gunicorn's gthread worker serves requests on a fixed number of threads. A
request that never finishes (a socket.io websocket holds its thread for as
long as the browser tab is open, an event stream for as long as it streams,
a call stuck on a dead server) keeps its thread, and when all of them are
held every new request waits in the queue: /, /api/... and the healthcheck
alike, at 2% cpu. Nothing errors, so the log said nothing.

This keeps a list of the requests in flight. When every thread has been busy
for a minute it logs what each one is serving and every thread's stack, once
per five minutes, so the next hang names its cause.
"""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("request_watchdog")

FULL_FOR_SECONDS = 60
CHECK_EVERY_SECONDS = 10
COOLDOWN_SECONDS = 300

_lock = threading.Lock()
_active: Dict[int, Dict[str, Any]] = {}
_started = False


def capacity() -> int:
    try:
        return max(1, int(os.environ.get("SOULSYNC_REQUEST_THREADS") or 8))
    except ValueError:
        return 8


class _Tracked:
    """The response iterable, dropped from the list only once it is closed:
    a streamed response holds its thread until then."""

    def __init__(self, iterable, key):
        self._iterable, self._key = iterable, key

    def __iter__(self):
        return iter(self._iterable)

    def close(self):
        try:
            close = getattr(self._iterable, "close", None)
            if close:
                close()
        finally:
            _finish(self._key)


def _hook_close(result, key):
    """Leave the response object itself in place: gunicorn sends a file
    through sendfile only when it gets its own FileWrapper back. The request
    leaves the list when the response is closed."""
    original = getattr(result, "close", None)
    if original is None and isinstance(result, (list, tuple)):
        _finish(key)  # already complete; written out right after
        return result
    if original is not None:
        def close():
            try:
                original()
            finally:
                _finish(key)
        try:
            result.close = close
            return result
        except (AttributeError, TypeError):
            pass
    return _Tracked(result, key)


def _finish(key) -> None:
    with _lock:
        _active.pop(key, None)


def track_requests(app: Callable) -> Callable:
    """WSGI middleware: record each request while it holds a thread."""
    counter = iter(range(1, 1 << 62))

    def wrapped(environ, start_response):
        _ensure_watchdog()
        key = next(counter)
        path = environ.get("PATH_INFO") or "/"
        query = environ.get("QUERY_STRING") or ""
        if "transport=" in query:  # socket.io: polling or websocket says a lot
            path += "?" + "&".join(p for p in query.split("&") if p.startswith("transport="))
        with _lock:
            _active[key] = {"path": path, "method": environ.get("REQUEST_METHOD", ""),
                            "since": time.monotonic(),
                            "thread": threading.current_thread().name}
        try:
            result = app(environ, start_response)
        except BaseException:
            _finish(key)
            raise
        return _hook_close(result, key)

    return wrapped


def snapshot() -> list:
    now = time.monotonic()
    with _lock:
        return sorted(({**info, "age_s": round(now - info["since"], 1)} for info in _active.values()),
                      key=lambda r: -r["age_s"])


def describe(requests: list) -> str:
    kinds = Counter(f'{r["method"]} {r["path"]}' for r in requests)
    lines = [f"  {count} x {what}" for what, count in kinds.most_common()]
    oldest = requests[0] if requests else None
    tail = (f"\n  oldest: {oldest['method']} {oldest['path']} for {oldest['age_s']:.0f}s "
            f"on {oldest['thread']}") if oldest else ""
    return "\n".join(lines) + tail


def check(now: Optional[float] = None, state: Optional[dict] = None,
          emit: Optional[Callable[[str], None]] = None) -> bool:
    """One look; True when it reported. ``state`` carries the full-since and
    last-report clocks between looks."""
    now = time.monotonic() if now is None else now
    state = state if state is not None else _state
    requests = snapshot()
    if len(requests) < capacity():
        state["full_since"] = None
        return False
    if state.get("full_since") is None:
        state["full_since"] = now
        return False
    if now - state["full_since"] < FULL_FOR_SECONDS:
        return False
    if state.get("reported") is not None and now - state["reported"] < COOLDOWN_SECONDS:
        return False
    state["reported"] = now
    from core.db_lock_watchdog import format_thread_stacks
    message = (f"All {capacity()} request threads have been busy for "
               f"{now - state['full_since']:.0f}s, so new requests (the pages, the api, "
               f"the healthcheck) wait in line. What they are serving:\n"
               + describe(requests) + "\nEvery thread's stack:\n" + format_thread_stacks())
    (emit or (lambda text: logger.error("%s", text)))(message)
    return True


_state: dict = {"full_since": None, "reported": None}


def _ensure_watchdog() -> None:
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _started = True

    def run():
        while True:
            time.sleep(CHECK_EVERY_SECONDS)
            try:
                check()
            except Exception as exc:  # noqa: BLE001 - the watchdog never takes the server down
                logger.debug("request watchdog look failed: %s", exc)

    threading.Thread(target=run, name="RequestWatchdog", daemon=True).start()


__all__ = ["track_requests", "check", "snapshot", "capacity"]
