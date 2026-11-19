"""folded text columns, kept in memory, for the contains-searches.

`unidecode_lower(col) LIKE '%x%'` can't use an index, so sqlite calls the
python fold once per row on every search: 72k album titles, 77k per-track
credits, 29k artist names. one sync runs it a few times per playlist track,
and a playlist took minutes (Oct 31 2026, 0.2-0.6s per call on a 312k-track
library, every call holding the GIL the rest of the app needs).

this folds each row once and keeps (id, folded) per table and database. a
search scans the folded strings in python, which is a plain substring test.
rows added since the last look are folded on their own (ids only grow); a
delete reads the id list (no text) and folds only rows it hasn't seen. the
refresh interval passing, which covers an edited title, folds the table again
on a thread of its own. callers still put the id list back through sql, so the
rest of their filters apply as before.

one thread looks at a table at a time (#2205). the rest answer from the
snapshot it is replacing, or wait for it when there is none yet; before, every
request that found it stale read and folded the whole table itself, and on a
47 GB library 22 gunicorn threads sat in _rows together.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from bisect import bisect_right
import time
from typing import Dict, List, Optional, Tuple

try:
    from unidecode import unidecode as _ud

    def fold(value: Optional[str]) -> str:
        """the same fold as the connection's unidecode_lower()."""
        return _ud(value).lower() if value else ""
except ImportError:  # pragma: no cover - unidecode ships with soulsync
    def fold(value: Optional[str]) -> str:
        return value.lower() if value else ""

# a full refold at least this often, so an edited title is seen
REFRESH_SECONDS = 600.0
# the table is asked whether it changed at most this often. a sync looks a
# few times per playlist track, and the count alone cost ~20ms on 72k rows
CHECK_SECONDS = 2.0
# between rows in the joined text; never in a title
_SEP = "\x00"

def _key(value: Optional[str]) -> str:
    """the same key as the connection's norm_key(): punctuation dropped too."""
    from core.text.normalize import normalize_key
    return normalize_key(value) if value else ""


logger = logging.getLogger(__name__)

_FOLDS = {"fold": lambda v: fold(v), "key": _key}

# (table, id column, text column, extra where, fold) the searches use
ALBUM_TITLES = ("lib2_albums", "id", "title", "", "fold")
ARTIST_NAMES = ("lib2_artists", "id", "name", "", "fold")
TRACK_CREDITS = ("lib2_tracks", "id", "track_artist", "track_artist IS NOT NULL", "fold")
# a title-only library search (no artist to narrow it): 312k titles
TRACK_TITLES = ("lib2_tracks", "id", "title", "", "fold")
# "ACDC" for "AC/DC": artists by their punctuation-free key, asked by equality
ARTIST_KEYS = ("lib2_artists", "id", "name", "", "key")

_lock = threading.Lock()
# (database path, spec) -> entry
_entries: Dict[Tuple[str, Tuple[str, str, str, str]], dict] = {}
# (database path, spec) -> the one lock a look at the table takes (#2205).
# without it every request that found the entry stale read and folded the
# whole table itself: 22 gunicorn threads in _rows at once on a 47 GB db
_flights: Dict[Tuple[str, Tuple[str, str, str, str]], threading.Lock] = {}
# keys with a background refold queued or running
_refolding: set = set()
# bumped by clear(); a refold that started before it never publishes after it
_generation = 0
# rows fetched by id per statement when a resync reads only the new ones
_IN_CHUNK = 500


def _like_matcher(needle: str):
    """sql LIKE '%needle%' over an already lowercased haystack: % and _ keep
    their wildcard meaning, everything else is literal."""
    needle = (needle or "").lower()
    if "%" not in needle and "_" not in needle:
        return lambda hay: needle in hay
    pattern = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                      for ch in needle)
    rx = re.compile(pattern, re.DOTALL)
    return lambda hay: rx.search(hay) is not None


def _rows(conn, spec, after_id: Optional[int] = None):
    table, id_col, text_col, where, _fold = spec
    clauses = [c for c in (where, f"{id_col} > ?" if after_id is not None else "") if c]
    sql = f"SELECT {id_col}, {text_col} FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = conn.execute(sql, (after_id,) if after_id is not None else ()).fetchall()
    # id order whatever index the planner read, so an append keeps it sorted
    return sorted(((int(i), v) for i, v in rows), key=lambda r: r[0])


def _ids(conn, spec) -> List[int]:
    """every id the spec covers and no text: the smallest index answers it."""
    table, id_col, _text, where, _fold = spec
    sql = f"SELECT {id_col} FROM {table}" + (f" WHERE {where}" if where else "")
    return sorted(int(r[0]) for r in conn.execute(sql).fetchall())


def _rows_for(conn, spec, ids: List[int]):
    table, id_col, text_col, _where, _fold = spec
    out = []
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start:start + _IN_CHUNK]
        sql = (f"SELECT {id_col}, {text_col} FROM {table} "
               f"WHERE {id_col} IN ({','.join('?' for _ in chunk)})")
        out.extend((int(i), v) for i, v in conn.execute(sql, chunk).fetchall())
    return out


def _signature(conn, spec) -> Tuple[int, int]:
    table, id_col, _text, where, _fold = spec
    sql = f"SELECT count(*), COALESCE(max({id_col}), 0) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    count, top = conn.execute(sql).fetchone()
    return int(count or 0), int(top or 0)


def _entry(ids, folded, count, top, built, checked) -> dict:
    return {"ids": ids, "folded": folded, "count": count, "top": top, "built": built,
            "checked": checked, "blob": None, "by_key": None}


def _full(conn, spec, now: float, signature=None) -> dict:
    count, top = signature if signature is not None else _signature(conn, spec)
    folder = _FOLDS[spec[4]]
    rows = _rows(conn, spec)
    return _entry([i for i, _ in rows], [folder(v) for _, v in rows], count, top, now, now)


def _flight(key) -> threading.Lock:
    with _lock:
        flight = _flights.get(key)
        if flight is None:
            flight = _flights[key] = threading.Lock()
        return flight


def _publish(key, entry: dict, generation: int) -> None:
    with _lock:
        if generation == _generation:
            _entries[key] = entry


def _usable(entry: Optional[dict], now: float) -> bool:
    return bool(entry) and now - entry["checked"] < CHECK_SECONDS \
        and now - entry["built"] < REFRESH_SECONDS


def _current(conn, db_key: str, spec) -> dict:
    key = (db_key, spec)
    with _lock:
        entry = _entries.get(key)
    if _usable(entry, time.monotonic()):
        return entry
    flight = _flight(key)
    if entry is not None:
        # someone is already looking at the table: the snapshot they are
        # replacing answers this call. callers put the ids back through sql,
        # so a deleted row never comes back; a new one shows on the next look
        if not flight.acquire(blocking=False):
            return entry
    else:
        # nothing to answer with yet: wait for the one build, don't start another
        flight.acquire()
    try:
        with _lock:
            entry = _entries.get(key)
            generation = _generation
        now = time.monotonic()
        if _usable(entry, now):
            return entry
        return _look(conn, key, spec, entry, now, generation)
    finally:
        flight.release()


def _look(conn, key, spec, entry: Optional[dict], now: float, generation: int) -> dict:
    """bring ``entry`` up to the table. the caller holds the key's flight."""
    signature = _signature(conn, spec)
    if entry is None:
        fresh = _full(conn, spec, now, signature)
        _publish(key, fresh, generation)
        return fresh
    if now - entry["built"] >= REFRESH_SECONDS:
        # the periodic refold that catches an edited title reads and folds the
        # whole table. with a file to open it runs on its own thread and this
        # call keeps the snapshot; in memory there is nothing else to read with
        if _refold_in_background(key, spec, generation):
            entry = dict(entry, built=now)
        else:
            fresh = _full(conn, spec, now, signature)
            _publish(key, fresh, generation)
            return fresh
    count, top = signature
    if entry["count"] == count and entry["top"] == top:
        fresh = dict(entry, checked=now)
        _publish(key, fresh, generation)
        return fresh
    folder = _FOLDS[spec[4]]
    if top > entry["top"] and count > entry["count"]:
        added = [(i, folder(v)) for i, v in _rows(conn, spec, after_id=entry["top"])]
        # only rows added since: the count says nothing went in between
        if entry["count"] + len(added) == count:
            fresh = _entry(entry["ids"] + [i for i, _ in added],
                           entry["folded"] + [f for _, f in added],
                           count, top, entry["built"], now)
            _publish(key, fresh, generation)
            return fresh
    # a delete, or a delete and an add together. the ids alone say which rows
    # went and which came, and only the new ones are read and folded
    fresh = _resync(conn, spec, entry, signature, now)
    _publish(key, fresh, generation)
    return fresh


def _resync(conn, spec, entry: dict, signature, now: float) -> dict:
    live = _ids(conn, spec)
    known = dict(zip(entry["ids"], entry["folded"], strict=True))
    missing = [i for i in live if i not in known]
    if len(missing) > max(_IN_CHUNK * 20, len(live) // 4):
        # most of the table is new (a reimport): one read beats the id lists
        return _full(conn, spec, now, signature)
    folder = _FOLDS[spec[4]]
    for i, v in _rows_for(conn, spec, missing):
        known[i] = folder(v)
    ids = [i for i in live if i in known]
    count, top = signature
    return _entry(ids, [known[i] for i in ids], count, top, entry["built"], now)


def _db_file(db_key: str) -> bool:
    try:
        return bool(db_key) and os.path.isfile(db_key)
    except (TypeError, ValueError):
        return False


def _refold_in_background(key, spec, generation: int) -> bool:
    """queue one refold of ``key`` on its own thread and connection. False
    when there's no database file to open (an in-memory one); the caller
    refolds itself then."""
    db_key = key[0]
    if not _db_file(db_key):
        return False
    with _lock:
        if key in _refolding:
            return True
        _refolding.add(key)

    def run():
        try:
            conn = sqlite3.connect(db_key, timeout=30.0, check_same_thread=False)
            try:
                conn.execute("PRAGMA query_only = ON")
                with _flight(key):
                    _publish(key, _full(conn, spec, time.monotonic()), generation)
            finally:
                conn.close()
        except Exception as exc:  # the snapshot stays; the next refresh tries again
            logger.debug("fold_index refold of %s failed: %s", spec[0], exc)
        finally:
            with _lock:
                _refolding.discard(key)

    threading.Thread(target=run, name=f"fold-refold-{spec[0]}", daemon=True).start()
    return True


def matching_ids(conn, db_key: str, spec, needle: str, limit: Optional[int] = None) -> List[int]:
    """ids whose folded text contains ``needle`` (sql LIKE '%needle%' on
    unidecode_lower(text)). ``needle`` is expected folded already, as the
    callers' LIKE parameters were."""
    entry = _current(conn, db_key, spec)
    needle = (needle or "").lower()
    ids = entry["ids"]
    if not needle:
        return list(ids[:limit] if limit is not None else ids)
    out: List[int] = []
    if "%" in needle or "_" in needle or _SEP in needle:
        hit = _like_matcher(needle)
        for i, text in zip(ids, entry["folded"], strict=True):
            if hit(text):
                out.append(i)
                if limit is not None and len(out) >= limit:
                    break
        return out
    # a plain needle: one find over every row joined, in C, instead of a
    # python test per row. the separator never matches, so a hit is in one row
    blob, starts = _joined(entry)
    pos = blob.find(needle)
    while pos != -1:
        row = bisect_right(starts, pos) - 1
        out.append(ids[row])
        if limit is not None and len(out) >= limit:
            break
        if row + 1 >= len(starts):
            break
        pos = blob.find(needle, starts[row + 1])
    return out


def ids_with_key(conn, db_key: str, spec, key: str) -> List[int]:
    """ids whose folded text EQUALS ``key`` (``norm_key(name) = ?``)."""
    if not key:
        return []
    entry = _current(conn, db_key, spec)
    by_key = entry.get("by_key")
    if by_key is None:
        by_key = {}
        for i, text in zip(entry["ids"], entry["folded"], strict=True):
            if text:
                by_key.setdefault(text, []).append(i)
        entry["by_key"] = by_key
    return list(by_key.get(key, ()))


def _joined(entry: dict):
    joined = entry.get("blob")
    if joined is None:
        starts, offset = [], 0
        for text in entry["folded"]:
            starts.append(offset)
            offset += len(text) + 1
        joined = (_SEP.join(entry["folded"]), starts)
        entry["blob"] = joined
    return joined


def clear() -> None:
    """drop every cached table (tests)."""
    global _generation
    with _lock:
        _entries.clear()
        _generation += 1


__all__ = ["ALBUM_TITLES", "ARTIST_KEYS", "ARTIST_NAMES", "CHECK_SECONDS", "REFRESH_SECONDS",
           "TRACK_CREDITS", "TRACK_TITLES", "clear", "fold", "ids_with_key", "matching_ids"]
