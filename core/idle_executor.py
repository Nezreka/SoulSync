"""A thread pool whose threads go away when there is nothing to do.

concurrent.futures.ThreadPoolExecutor starts a thread per submit up to
max_workers and keeps every one of them until shutdown. a long-lived pool
used once at its busiest keeps all its threads for the life of the process:
an idle worker had 140-175 threads, most of them parked in pools that had
nothing to run (#2253). each one keeps a stack and, on glibc, a malloc arena
that holds on to whatever it once allocated.

IdleThreadPool behaves like a ThreadPoolExecutor to its callers (submit, map,
shutdown, ``with``), and a thread that has waited ``idle_timeout`` seconds
with nothing to run ends. the next submit starts one again.

threads are daemons, so a pool never holds up the process exiting; use it for
work that can be dropped at exit (lookups, fetches, status calls), not for
work that must finish (moving a file).
"""

from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import Executor, Future
from typing import Any, Callable, Optional, Set

DEFAULT_IDLE_TIMEOUT = 60.0

_counter = itertools.count()


class IdleThreadPool(Executor):
    def __init__(self, max_workers: int, thread_name_prefix: str = "",
                 idle_timeout: float = DEFAULT_IDLE_TIMEOUT):
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than 0")
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix or f"IdleThreadPool-{next(_counter)}"
        self._idle_timeout = idle_timeout
        self._queue: "queue.SimpleQueue[Optional[tuple]]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._threads: Set[threading.Thread] = set()
        self._idle = 0
        self._shutdown = False
        self._names = itertools.count()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future = Future()
            self._queue.put((future, fn, args, kwargs))
            # a thread waiting in _work will take it; otherwise start one.
            # two submits at once can both lean on the same waiting thread,
            # which only means the second waits its turn, as in the stdlib
            if self._idle == 0 and len(self._threads) < self._max_workers:
                t = threading.Thread(
                    target=self._work, daemon=True,
                    name=f"{self._thread_name_prefix}_{next(self._names)}")
                self._threads.add(t)
                t.start()
            return future

    def _work(self) -> None:
        me = threading.current_thread()
        while True:
            with self._lock:
                self._idle += 1
            try:
                item = self._queue.get(timeout=self._idle_timeout)
            except queue.Empty:
                with self._lock:
                    if not self._queue.empty():
                        # work arrived as we timed out. submit saw us
                        # waiting and started no one for it, so stay
                        self._idle -= 1
                        continue
                    self._idle -= 1
                    self._threads.discard(me)
                    return
            with self._lock:
                self._idle -= 1
            if item is None:  # shutdown
                with self._lock:
                    self._threads.discard(me)
                return
            future, fn, args, kwargs = item
            if future.set_running_or_notify_cancel():
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - handed to the caller
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            del item, future, fn, args, kwargs

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._shutdown = True
            if cancel_futures:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is not None:
                        item[0].cancel()
            threads = list(self._threads)
            for _ in threads:
                self._queue.put(None)
        if wait:
            for t in threads:
                if t is not threading.current_thread():
                    t.join()

    @property
    def thread_count(self) -> int:
        with self._lock:
            return len(self._threads)
