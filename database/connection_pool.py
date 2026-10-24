"""Reuse SQLite connections instead of opening a new one for every database call.

``MusicDatabase._get_connection`` used to open, set up and close a connection per
call (about 2.5k call sites). Every fresh connection parses the whole schema
before its first statement (about 2 ms with this schema), and closing the last
open connection checkpoints and deletes the -wal and -shm, which the next call
then creates again. A loop that makes one call per item pays for all of it every
time, against 0.004 ms for the query itself.

This keeps a few idle connections per database file and hands out a thin proxy
around one, so the callers' existing code (``conn.close()``, ``with conn:``,
``conn.cursor()``, never closing at all) keeps its meaning:

- close() ends the caller's use of the connection: pending cursors are closed, an
  open transaction is rolled back (what closing a connection did), and the
  connection goes back to the pool. Using the proxy afterwards raises
  ``sqlite3.ProgrammingError`` like a closed connection does.
- ``with conn:`` still only commits or rolls back; it never closed the connection.
- A proxy that is garbage collected without close() returns its connection only
  when no cursor made through it is still alive. A cursor that outlives the proxy
  keeps its connection exactly as it did before, and that connection is not
  reused.
- A connection is handed to one caller at a time. It is reused only while the
  database file is still the same file (a restored or replaced file is reopened),
  and only after what a caller can leave behind is undone: an open transaction,
  TEMP tables, views and triggers, row_factory, text_factory, foreign_keys,
  busy_timeout, analysis_limit, isolation_level, and the authorizer, progress
  and trace hooks. Anything else a caller changes on a connection it gets from
  here (an ATTACHed database, another PRAGMA) would outlive the call; nothing in
  the code base does. create_function re-registers the same function, so it is
  harmless.
- The pool is small and shared by all threads (MAX_IDLE per file, MAX_IDLE_TOTAL
  across files, least recently used file evicted first), so it costs a few open
  file descriptors and under 4 MB however many threads the server runs. Pooled
  connections are opened with check_same_thread=False because they move between
  threads; each is used by one thread at a time, which is what SQLite's serialized
  mode (``sqlite3.threadsafety == 3``) requires.

``SOULSYNC_DB_POOL=0`` turns it off. In-memory and URI databases are never pooled.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import weakref
from collections import OrderedDict
from typing import Any, Callable, List, Optional

ENABLED = os.environ.get("SOULSYNC_DB_POOL", "1").strip().lower() not in ("0", "false", "no", "off")
try:
    MAX_IDLE = max(1, int(os.environ.get("SOULSYNC_DB_POOL_MAX", "8")))
except ValueError:
    MAX_IDLE = 8
# across every file: a process that opens many databases (the test suite makes
# hundreds of temp ones) must not keep 8 idle connections to each of them
try:
    MAX_IDLE_TOTAL = max(1, int(os.environ.get("SOULSYNC_DB_POOL_MAX_TOTAL", "16")))
except ValueError:
    MAX_IDLE_TOTAL = 16

_lock = threading.Lock()
# least recently returned file first, so the cap evicts the one nobody is using
_idle: "OrderedDict[str, List[_Slot]]" = OrderedDict()

# PRAGMAs a caller sets on its connection that would otherwise carry over to the
# next one: insert_or_update_media_track drops busy_timeout to 10s, the planner
# stats refresh sets analysis_limit
_RESET_PRAGMAS = ("busy_timeout", "analysis_limit")

# plain counters for diagnostics and tests; not locked on purpose, approximate is fine
stats = {"hits": 0, "misses": 0, "discarded": 0, "kept_by_cursor": 0, "overflow_closed": 0}


class _Slot:
    """A connection and what it must still look like to be handed out again."""

    __slots__ = ("raw", "ident", "row_factory", "isolation_level", "text_factory", "pragmas")

    def __init__(self, raw: sqlite3.Connection, ident: Optional[tuple]):
        self.raw = raw
        self.ident = ident
        self.row_factory = raw.row_factory
        self.isolation_level = raw.isolation_level
        self.text_factory = raw.text_factory
        self.pragmas = {name: raw.execute(f"PRAGMA {name}").fetchone()[0] for name in _RESET_PRAGMAS}


def _file_ident(path: str) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _poolable_path(path: Any) -> Optional[str]:
    if not ENABLED:
        return None
    p = os.fspath(path) if path is not None else ""
    if not p or p == ":memory:" or p.startswith("file:") or p.startswith(":"):
        return None
    return p


def _close_quietly(raw: sqlite3.Connection) -> None:
    try:
        raw.close()
    except sqlite3.Error:
        pass


class PooledConnection:
    """What ``_get_connection`` returns. Looks like a sqlite3.Connection to callers."""

    __slots__ = ("_slot", "_path", "_cursors", "_released", "__weakref__")

    def __init__(self, slot: _Slot, path: str):
        object.__setattr__(self, "_slot", slot)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_cursors", [])
        object.__setattr__(self, "_released", False)

    # -- delegation ----------------------------------------------------------
    def _raw(self) -> sqlite3.Connection:
        if self._released:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return self._slot.raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._raw(), name, value)

    def _track(self, cur: sqlite3.Cursor) -> sqlite3.Cursor:
        self._cursors.append(weakref.ref(cur))
        if len(self._cursors) > 64:  # a long-lived connection must not grow this without bound
            self._cursors[:] = [r for r in self._cursors if r() is not None]
        return cur

    def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._track(self._raw().cursor(*args, **kwargs))

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._track(self._raw().execute(*args, **kwargs))

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._track(self._raw().executemany(*args, **kwargs))

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._track(self._raw().executescript(*args, **kwargs))

    # `with conn:` commits or rolls back and never closed the connection; keep that,
    # and hand back the proxy, not the raw connection sqlite3 would return.
    def __enter__(self) -> "PooledConnection":
        self._raw()
        return self

    def __exit__(self, *exc: Any) -> Optional[bool]:
        return self._raw().__exit__(*exc)

    # -- release ---------------------------------------------------------------
    def close(self) -> None:
        if self._released:
            return
        for ref in self._cursors:
            cur = ref()
            if cur is not None:
                try:
                    cur.close()
                except sqlite3.Error:
                    pass
        self._cursors.clear()
        self._give_back()

    def _live_cursor(self) -> bool:
        return any(ref() is not None for ref in self._cursors)

    def _give_back(self) -> None:
        object.__setattr__(self, "_released", True)
        slot = self._slot
        raw = slot.raw
        try:
            if raw.in_transaction:
                raw.rollback()
            # per-connection hooks a caller may have installed; a fresh connection had none
            raw.set_authorizer(None)
            raw.set_progress_handler(None, 0)
            raw.set_trace_callback(None)
            # TEMP tables, views and triggers live as long as their connection; callers
            # such as MusicDatabase._temp_key_set rely on them going away with it
            temp = raw.execute(
                "SELECT type, name FROM sqlite_temp_master WHERE type IN ('table', 'view', 'trigger')"
            ).fetchall()
            for kind, name in temp:
                if kind == "trigger":
                    raw.execute('DROP TRIGGER IF EXISTS temp."%s"' % name.replace('"', '""'))
            for kind, name in temp:
                if kind != "trigger":
                    raw.execute('DROP %s IF EXISTS temp."%s"' % (kind.upper(), name.replace('"', '""')))
            if raw.isolation_level != slot.isolation_level:
                raise sqlite3.ProgrammingError("isolation_level changed")
            if raw.row_factory is not slot.row_factory:
                raw.row_factory = slot.row_factory
            if raw.text_factory is not slot.text_factory:
                raw.text_factory = slot.text_factory
            if raw.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raw.execute("PRAGMA foreign_keys = ON")
            for name, value in slot.pragmas.items():
                if raw.execute(f"PRAGMA {name}").fetchone()[0] != value:
                    raw.execute(f"PRAGMA {name} = {int(value)}")
        except sqlite3.Error:
            stats["discarded"] += 1
            _close_quietly(raw)
            return
        evicted: List[_Slot] = []
        with _lock:
            bucket = _idle.setdefault(self._path, [])
            _idle.move_to_end(self._path)
            if len(bucket) >= MAX_IDLE:
                evicted.append(slot)
            else:
                bucket.append(slot)
                total = sum(len(b) for b in _idle.values())
                while total > MAX_IDLE_TOTAL:
                    oldest_path = next(iter(_idle))
                    oldest = _idle[oldest_path]
                    evicted.append(oldest.pop(0))
                    total -= 1
                    if not oldest:
                        del _idle[oldest_path]
        for gone in evicted:
            stats["overflow_closed"] += 1
            _close_quietly(gone.raw)

    def __del__(self) -> None:
        try:
            if self._released:
                return
            if self._live_cursor():
                # a cursor still needs this connection; it keeps it alive on its own
                object.__setattr__(self, "_released", True)
                stats["kept_by_cursor"] += 1
                return
            self._give_back()
        except Exception:  # noqa: BLE001, S110 - never raise from a finalizer
            pass


def acquire(path: Any, opener: Callable[[bool], sqlite3.Connection]):
    """A connection for ``path``: an idle one when it is still valid, else a new
    one from ``opener(check_same_thread)``. Paths that are not poolable get the
    plain connection, opened the way it always was."""
    p = _poolable_path(path)
    if p is None:
        return opener(True)
    ident = _file_ident(p)
    while True:
        with _lock:
            bucket = _idle.get(p)
            slot = bucket.pop() if bucket else None
            if bucket is not None and not bucket:
                del _idle[p]
        if slot is None:
            break
        if slot.ident is not None and slot.ident == ident:
            stats["hits"] += 1
            return PooledConnection(slot, p)
        stats["discarded"] += 1
        _close_quietly(slot.raw)
    stats["misses"] += 1
    raw = opener(False)
    return PooledConnection(_Slot(raw, ident), p)


def discard(path: Any) -> None:
    """Close the idle connections for ``path``."""
    p = _poolable_path(path)
    if p is None:
        return
    with _lock:
        bucket = _idle.pop(p, [])
    for slot in bucket:
        _close_quietly(slot.raw)
