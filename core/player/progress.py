"""where each profile is in each thing it plays, for the /player app.

one row per (profile, item type, item id). item_type is a plain string
('track', 'episode', 'movie', 'audiobook', 'podcast_episode', 'youtube', ...)
so a new kind of thing plugs in without a schema change. item_id is text for
the same reason: some ids are ints, some are asins or youtube ids.

the table has a profile_id column on purpose: deleting a profile sweeps every
table that has one (MusicDatabase.delete_profile), so a gone profile's
positions go with it.

meta is a small display snapshot the player hands in (title, art, where the
card links to) so the continue rails can draw a card without a lookup per
type. artwork must be a source url: /api/image-cache links expire and are
dropped here rather than stored.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict, List, Optional

TABLE = "watch_progress"

ITEM_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ITEM_ID_MAX = 200
# ten days. nothing anyone plays is longer, and it keeps junk numbers out
SECONDS_MAX = 864000.0
CONTINUE_DEFAULT = 20
CONTINUE_MAX = 50

META_KEYS = ("title", "subtitle", "artwork", "backdrop", "href", "parent_id", "parent_title")
META_VALUE_MAX = 500

_ready: set = set()


class ProgressError(ValueError):
    """a request the store won't take. the message is safe to show."""


# ── validation ───────────────────────────────────────────────────────────

def clean_item_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not ITEM_TYPE_RE.match(text):
        raise ProgressError("item_type must be a short lowercase name like 'movie' or 'track'")
    return text


def clean_item_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ProgressError("item_id is required")
    text = str(value).strip()
    if not text:
        raise ProgressError("item_id is required")
    if len(text) > ITEM_ID_MAX:
        raise ProgressError("item_id is too long")
    return text


def clean_seconds(value: Any, name: str, *, allow_none: bool = False) -> Optional[float]:
    if value is None or value == "":
        if allow_none:
            return None
        raise ProgressError(f"{name} is required")
    if isinstance(value, bool):
        raise ProgressError(f"{name} must be a number of seconds")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProgressError(f"{name} must be a number of seconds") from None
    if not math.isfinite(number) or number < 0:
        raise ProgressError(f"{name} must be zero or more seconds")
    return min(number, SECONDS_MAX)


def clean_meta(value: Any) -> Optional[Dict[str, str]]:
    """keep the known display keys as short strings. image-cache links
    expire, so an artwork or backdrop pointing at one is dropped."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProgressError("meta must be an object")
    out: Dict[str, str] = {}
    for key in META_KEYS:
        raw = value.get(key)
        if raw is None or isinstance(raw, (dict, list, bool)):
            continue
        text = str(raw).strip()[:META_VALUE_MAX]
        if not text:
            continue
        if key in ("artwork", "backdrop") and "/api/image-cache" in text:
            continue
        out[key] = text
    return out or None


def is_effectively_finished(position: float, duration: Optional[float]) -> bool:
    """close enough to the end to count as done: the last 5%, but never more
    than five minutes of it (a 20 hour book isn't done an hour early)."""
    if not duration or duration <= 0:
        return False
    tail = min(duration * 0.05, 300.0)
    return position >= duration - tail


# ── schema ───────────────────────────────────────────────────────────────

def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                profile_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                position_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL,
                finished INTEGER NOT NULL DEFAULT 0,
                meta_json TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, item_type, item_id)
            )""")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_continue "
            f"ON {TABLE} (profile_id, finished, updated_at DESC)")
        conn.commit()
    _ready.add(key)


# ── rows ─────────────────────────────────────────────────────────────────

def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + "Z"


def row_to_dict(row: Any) -> Dict[str, Any]:
    meta = None
    if row["meta_json"]:
        try:
            meta = json.loads(row["meta_json"])
        except (TypeError, ValueError):
            meta = None
    return {
        "item_type": row["item_type"],
        "item_id": row["item_id"],
        "position_seconds": float(row["position_seconds"] or 0),
        "duration_seconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
        "finished": bool(row["finished"]),
        "meta": meta,
        "updated_at": _iso(row["updated_at"]),
    }


def _select(conn, profile_id: int, item_type: str, item_id: str):
    return conn.execute(
        f"SELECT * FROM {TABLE} WHERE profile_id = ? AND item_type = ? AND item_id = ?",
        (int(profile_id), item_type, item_id)).fetchone()


def get(db: Any, profile_id: int, item_type: Any, item_id: Any) -> Optional[Dict[str, Any]]:
    item_type, item_id = clean_item_type(item_type), clean_item_id(item_id)
    ensure_schema(db)
    with db._get_connection() as conn:
        row = _select(conn, profile_id, item_type, item_id)
    return row_to_dict(row) if row else None


LOOKUP_MAX = 500


def lookup(db: Any, profile_id: int, item_type: Any, item_ids: Any) -> Dict[str, Dict[str, Any]]:
    """this profile's rows for many items of one type at once (an album's
    tracks, a show's episodes), keyed by item id. unknown ids are left out."""
    item_type = clean_item_type(item_type)
    if not isinstance(item_ids, (list, tuple)):
        raise ProgressError("item_ids must be a list")
    if len(item_ids) > LOOKUP_MAX:
        raise ProgressError(f"at most {LOOKUP_MAX} item_ids at a time")
    ids = sorted({clean_item_id(i) for i in item_ids})
    if not ids:
        return {}
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE profile_id = ? AND item_type = ? "
            f"AND item_id IN (%s)" % ",".join("?" for _ in ids),
            [int(profile_id), item_type, *ids]).fetchall()
    return {r["item_id"]: row_to_dict(r) for r in rows}


def save(db: Any, profile_id: int, item_type: Any, item_id: Any, position: Any,
         duration: Any = None, *, finished: Optional[bool] = None, meta: Any = None,
         now: Optional[float] = None) -> Dict[str, Any]:
    """upsert this profile's position. finished follows the position unless
    the caller says otherwise, so scrubbing back from the end reopens it.
    a missing duration or meta keeps what was stored."""
    item_type, item_id = clean_item_type(item_type), clean_item_id(item_id)
    position = clean_seconds(position, "position_seconds")
    duration = clean_seconds(duration, "duration_seconds", allow_none=True)
    meta = clean_meta(meta)
    ensure_schema(db)
    stamp = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        old = _select(conn, profile_id, item_type, item_id)
        if duration is None and old is not None:
            duration = old["duration_seconds"]
        if duration:
            position = min(position, duration)
        done = is_effectively_finished(position, duration) if finished is None else bool(finished)
        meta_json = json.dumps(meta) if meta else (old["meta_json"] if old is not None else None)
        conn.execute(
            f"""INSERT INTO {TABLE} (profile_id, item_type, item_id, position_seconds,
                    duration_seconds, finished, meta_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, item_type, item_id) DO UPDATE SET
                    position_seconds = excluded.position_seconds,
                    duration_seconds = excluded.duration_seconds,
                    finished = excluded.finished,
                    meta_json = excluded.meta_json,
                    updated_at = excluded.updated_at""",
            (int(profile_id), item_type, item_id, position, duration, 1 if done else 0,
             meta_json, stamp))
        conn.commit()
        row = _select(conn, profile_id, item_type, item_id)
    return row_to_dict(row)


def finish(db: Any, profile_id: int, item_type: Any, item_id: Any, duration: Any = None,
           *, meta: Any = None, now: Optional[float] = None) -> Dict[str, Any]:
    """mark it played through. the position goes to the end when the length
    is known, so the next play starts over instead of in the credits."""
    item_type, item_id = clean_item_type(item_type), clean_item_id(item_id)
    duration = clean_seconds(duration, "duration_seconds", allow_none=True)
    ensure_schema(db)
    if duration is None:
        with db._get_connection() as conn:
            old = _select(conn, profile_id, item_type, item_id)
        if old is not None and old["duration_seconds"] is not None:
            duration = float(old["duration_seconds"])
    return save(db, profile_id, item_type, item_id, duration or 0, duration,
                finished=True, meta=meta, now=now)


def remove(db: Any, profile_id: int, item_type: Any, item_id: Any) -> bool:
    item_type, item_id = clean_item_type(item_type), clean_item_id(item_id)
    ensure_schema(db)
    with db._get_connection() as conn:
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE profile_id = ? AND item_type = ? AND item_id = ?",
            (int(profile_id), item_type, item_id))
        conn.commit()
        return bool(cur.rowcount)


def clamp_limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return CONTINUE_DEFAULT
    return max(1, min(number, CONTINUE_MAX))


def continue_list(db: Any, profile_id: int, item_types: Optional[List[str]] = None,
                  limit: Any = CONTINUE_DEFAULT) -> List[Dict[str, Any]]:
    """started and not finished, most recently touched first."""
    types = [clean_item_type(t) for t in (item_types or []) if str(t or "").strip()]
    ensure_schema(db)
    sql = (f"SELECT * FROM {TABLE} WHERE profile_id = ? AND finished = 0 "
           f"AND position_seconds > 0")
    params: List[Any] = [int(profile_id)]
    if types:
        sql += " AND item_type IN (%s)" % ",".join("?" for _ in types)
        params.extend(types)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(clamp_limit(limit))
    with db._get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]
