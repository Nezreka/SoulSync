"""folded text columns, kept in memory, for the contains-searches.

`unidecode_lower(col) LIKE '%x%'` can't use an index, so sqlite calls the
python fold once per row on every search: 72k album titles, 77k per-track
credits, 29k artist names. one sync runs it a few times per playlist track,
and a playlist took minutes (Oct 31 2026, 0.2-0.6s per call on a 312k-track
library, every call holding the GIL the rest of the app needs).

this folds each row once and keeps (id, folded) per table and database. a
search scans the folded strings in python, which is a plain substring test.
rows added since the last look are folded on their own (ids only grow);
anything else (a delete, or the refresh interval passing, which covers an
edited title) folds the table again. callers still put the id list back
through sql, so the rest of their filters apply as before.
"""

from __future__ import annotations

import re
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


_FOLDS = {"fold": lambda v: fold(v), "key": _key}

# (table, id column, text column, extra where, fold) the searches use
ALBUM_TITLES = ("lib2_albums", "id", "title", "", "fold")
ARTIST_NAMES = ("lib2_artists", "id", "name", "", "fold")
TRACK_CREDITS = ("lib2_tracks", "id", "track_artist", "track_artist IS NOT NULL", "fold")
# "ACDC" for "AC/DC": artists by their punctuation-free key, asked by equality
ARTIST_KEYS = ("lib2_artists", "id", "name", "", "key")

_lock = threading.Lock()
# (database path, spec) -> entry
_entries: Dict[Tuple[str, Tuple[str, str, str, str]], dict] = {}


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
    return conn.execute(sql, (after_id,) if after_id is not None else ()).fetchall()


def _signature(conn, spec) -> Tuple[int, int]:
    table, id_col, _text, where, _fold = spec
    sql = f"SELECT count(*), COALESCE(max({id_col}), 0) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    count, top = conn.execute(sql).fetchone()
    return int(count or 0), int(top or 0)


def _current(conn, db_key: str, spec) -> dict:
    key = (db_key, spec)
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry and now - entry["checked"] < CHECK_SECONDS \
                and now - entry["built"] < REFRESH_SECONDS:
            return entry
    count, top = _signature(conn, spec)
    with _lock:
        entry = _entries.get(key)
        if entry and entry["count"] == count and entry["top"] == top \
                and now - entry["built"] < REFRESH_SECONDS:
            entry["checked"] = now
            return entry
        grows = (entry is not None and now - entry["built"] < REFRESH_SECONDS
                 and top > entry["top"] and count > entry["count"])
    folder = _FOLDS[spec[4]]
    if grows:
        added = [(int(i), folder(v)) for i, v in _rows(conn, spec, after_id=entry["top"])]
        # only rows added since: the count says nothing went in between
        if entry["count"] + len(added) == count:
            with _lock:
                fresh = {"ids": entry["ids"] + [i for i, _ in added],
                         "folded": entry["folded"] + [f for _, f in added],
                         "count": count, "top": top, "built": entry["built"],
                         "checked": now, "blob": None, "by_key": None}
                _entries[key] = fresh
            return fresh
    rows = _rows(conn, spec)
    fresh = {"ids": [int(i) for i, _ in rows], "folded": [folder(v) for _, v in rows],
             "count": count, "top": top, "built": now, "checked": now, "blob": None,
             "by_key": None}
    with _lock:
        _entries[key] = fresh
    return fresh


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
    with _lock:
        _entries.clear()


__all__ = ["ALBUM_TITLES", "ARTIST_KEYS", "ARTIST_NAMES", "CHECK_SECONDS", "REFRESH_SECONDS",
           "TRACK_CREDITS", "clear", "fold", "ids_with_key", "matching_ids"]
