"""TTL'd in-memory cache for enhanced-search responses.

The cache key blends the normalized query with the active media server,
configured fallback metadata source, hydrabase-active flag, and the
explicit single-source request (if any). This prevents responses from
colliding when a user changes settings or switches single-source mode.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any, Callable, Optional, Tuple

CacheKey = Tuple[str, str, str, bool, str]

CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 100


class EnhancedSearchCache:
    """Thread-safe LRU+TTL cache for enhanced-search response payloads.

    A single shared instance lives in this module (`_cache`). The module-level
    helpers (`get_cache_key`, `get_cached_response`, `set_cached_response`)
    operate on it.
    """

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES):
        self._ttl = ttl
        self._max_entries = max_entries
        self._store: "collections.OrderedDict[CacheKey, dict]" = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: CacheKey) -> Optional[dict]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            if now - entry['timestamp'] < self._ttl:
                self._store.move_to_end(key)
                return entry['data']
            self._store.pop(key, None)
            return None

    def set(self, key: CacheKey, data: dict) -> None:
        with self._lock:
            self._store[key] = {'timestamp': time.time(), 'data': data}
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = EnhancedSearchCache()


def get_cache_key(
    query: str,
    requested_source: Optional[str],
    *,
    active_server_provider: Callable[[], str],
    fallback_source_provider: Callable[[], str],
    hydrabase_active_provider: Callable[[], bool],
) -> CacheKey:
    """Build a cache key for an enhanced-search query.

    Each provider arg is a zero-arg callable so the cache key reflects the
    LIVE config state at lookup time, not the state at app startup. Each
    provider is wrapped in try/except: failures resolve to a sentinel value
    so a misconfigured client never breaks search.
    """
    normalized_query = (query or '').strip().lower()

    try:
        active_server = active_server_provider() or 'unknown'
    except Exception:
        active_server = 'unknown'

    try:
        fallback_source = fallback_source_provider() or 'unknown'
    except Exception:
        fallback_source = 'unknown'

    try:
        hydrabase_active = bool(hydrabase_active_provider())
    except Exception:
        hydrabase_active = False

    source_tag = (requested_source or '').strip().lower() or 'auto'
    return (normalized_query, active_server, fallback_source, hydrabase_active, source_tag)


def get_cached_response(key: CacheKey) -> Optional[dict]:
    return _cache.get(key)


def set_cached_response(key: CacheKey, data: Any) -> None:
    _cache.set(key, data)


def clear_cache() -> None:
    _cache.clear()
