"""remembered answers from the public music apis (last.fm, listenbrainz, deezer).

one small table in the music database, keyed by provider + method + the
normalized arguments. a read answers from here while the row is fresh, asks
the network when it is not, and hands back the stale row when the network
has nothing (a rejected last.fm key, an outage, a timeout). a key that lapses
overnight then costs nothing the cache already knew.

every function degrades to a miss or a no-op on any database error: the
cache is an extra, never the reason a feature breaks.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("provider_cache")

DAY = 86400
# rows nobody refreshed in this long go on the next prune
MAX_KEEP_SECONDS = 180 * DAY
PRUNE_EVERY_SECONDS = DAY

SCHEMA = """
    CREATE TABLE IF NOT EXISTS provider_response_cache (
        provider TEXT NOT NULL,
        method TEXT NOT NULL,
        args_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        fetched_at REAL NOT NULL,
        PRIMARY KEY (provider, method, args_key)
    )
"""
INDEX = ("CREATE INDEX IF NOT EXISTS idx_provider_response_cache_fetched "
         "ON provider_response_cache (fetched_at)")


def ensure_schema(cursor) -> None:
    """the table + its prune index. music_database runs this on every boot."""
    cursor.execute(SCHEMA)
    cursor.execute(INDEX)


@dataclass
class Entry:
    payload: Any
    fetched_at: float

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.fetched_at)


def args_key(args: Optional[Dict[str, Any]]) -> str:
    """the arguments as one stable string: keys sorted, text folded to
    lowercase and trimmed, so "Radiohead " and "radiohead" share a row."""
    norm = {}
    for key, value in (args or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            value = " ".join(value.split()).lower()
        norm[str(key)] = value
    return json.dumps(norm, sort_keys=True, ensure_ascii=False, default=str)


class _SqliteStore:
    """the real store: the music database."""

    def __init__(self):
        self._last_prune = 0.0

    def _connect(self):
        from database.music_database import get_database
        return get_database()._get_connection()

    def get(self, provider, method, key):
        conn = None
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT payload, fetched_at FROM provider_response_cache "
                "WHERE provider = ? AND method = ? AND args_key = ?",
                (provider, method, key)).fetchone()
            if not row:
                return None
            return Entry(json.loads(row[0]), float(row[1]))
        except Exception as exc:  # noqa: BLE001 - a miss, never an error
            logger.debug("provider cache read failed: %s", exc)
            return None
        finally:
            _close(conn)

    def put(self, provider, method, key, payload, fetched_at):
        conn = None
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO provider_response_cache "
                "(provider, method, args_key, payload, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (provider, method, key, json.dumps(payload, ensure_ascii=False), fetched_at))
            if fetched_at - self._last_prune > PRUNE_EVERY_SECONDS:
                self._last_prune = fetched_at
                conn.execute("DELETE FROM provider_response_cache WHERE fetched_at < ?",
                             (fetched_at - MAX_KEEP_SECONDS,))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("provider cache write failed: %s", exc)
        finally:
            _close(conn)


class MemoryStore:
    """a dict. the tests use it, so nothing they cache outlives the test."""

    def __init__(self):
        self.rows: Dict[Tuple[str, str, str], Entry] = {}

    def get(self, provider, method, key):
        entry = self.rows.get((provider, method, key))
        if entry is None:
            return None
        return Entry(json.loads(json.dumps(entry.payload)), entry.fetched_at)

    def put(self, provider, method, key, payload, fetched_at):
        self.rows[(provider, method, key)] = Entry(json.loads(json.dumps(payload)), fetched_at)


def _close(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: S110 - cleanup only
            pass


_store_lock = threading.Lock()
_store: Any = None


def _get_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = _SqliteStore()
        return _store


def use_store(store) -> None:
    """swap the backing store (tests hand in a MemoryStore)."""
    global _store
    with _store_lock:
        _store = store


def lookup(provider: str, method: str, args: Optional[Dict[str, Any]] = None) -> Optional[Entry]:
    return _get_store().get(provider, method, args_key(args))


def store(provider: str, method: str, args: Optional[Dict[str, Any]], payload: Any) -> None:
    if payload is None:
        return
    _get_store().put(provider, method, args_key(args), payload, time.time())


def cached(provider: str, method: str, args: Optional[Dict[str, Any]], ttl: float,
           fetch: Callable[[], Any], *, keep: Callable[[Any], bool] = lambda v: v is not None,
           ) -> Tuple[Any, str]:
    """(value, how) where how is 'fresh' (cache), 'network', 'stale' (cache,
    because the network had nothing) or 'none'.

    ``fetch`` returns None (or anything ``keep`` turns down) when it could not
    answer; a stale row then beats nothing.
    """
    hit = lookup(provider, method, args)
    if hit is not None and hit.age < ttl:
        return hit.payload, "fresh"
    try:
        value = fetch()
    except Exception as exc:  # noqa: BLE001 - a failed fetch falls to the stale row
        logger.debug("%s %s fetch failed: %s", provider, method, exc)
        value = None
    if keep(value):
        store(provider, method, args, value)
        return value, "network"
    if hit is not None:
        return hit.payload, "stale"
    return value, "none"
