"""A small thread-safe TTL + LRU cache for the video enrichment engine.

The engine is a process-wide singleton hit concurrently by Flask request threads
AND the worker threads, so the cache must be locked. Entries expire after a TTL;
when the cache is full the LEAST-RECENTLY-USED entry is evicted (not the whole
cache wholesale). Isolated: imports only the stdlib; no music, no DB.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, maxsize: int = 256, ttl: float = 1800.0, clock=time.monotonic):
        self._max = max(1, int(maxsize))
        self._ttl = float(ttl)
        self._clock = clock                      # injectable for tests
        self._lock = threading.Lock()
        self._data: OrderedDict = OrderedDict()  # key -> (expires_at, value)

    def get(self, key):
        now = self._clock()
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            if hit[0] <= now:                    # expired
                del self._data[key]
                return None
            self._data.move_to_end(key)          # mark recently used
            return hit[1]

    def put(self, key, value, ttl: float | None = None) -> None:
        expires_at = self._clock() + (self._ttl if ttl is None else float(ttl))
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:   # evict LRU (oldest), not everything
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
