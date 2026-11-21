"""a profile's watchlist: things to watch later, kept in player.db so every
screen of the profile has the same list.

an item is a kind and an id, like a progress row: a movie or a show by its
library id, a youtube video by its video id. meta (title, artwork) rides
along for kinds the player can't look up from its catalog (youtube), with
the same rules as progress meta (no expiring image-cache links).

the list holds what you put on it. what a profile may see is still the
catalog's call: a client draws only the items its catalog has (a kids
profile never sees a title over its cap, even one on its list).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from core.player.progress import ProgressError, clean_item_id, clean_item_type, clean_meta

TABLE = "player_watchlist"
KINDS = ("movie", "show", "youtube")
MAX_ITEMS = 1000

_ready: set = set()


class WatchlistError(ValueError):
    pass


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
                meta_json TEXT,
                added_at REAL NOT NULL,
                PRIMARY KEY (profile_id, item_type, item_id)
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_recent ON {TABLE} (profile_id, added_at DESC)")
        conn.commit()
    _ready.add(key)


def _clean(item_type: Any, item_id: Any) -> tuple:
    try:
        kind = clean_item_type(item_type)
        ident = clean_item_id(item_id)
    except ProgressError as e:
        raise WatchlistError(str(e)) from e
    if kind not in KINDS:
        raise WatchlistError(f"a watchlist holds {', '.join(KINDS)}")
    return kind, ident


def _row(r) -> Dict[str, Any]:
    meta = None
    if r[2]:
        try:
            meta = json.loads(r[2])
        except (TypeError, ValueError):
            meta = None
    return {"item_type": r[0], "item_id": r[1], "meta": meta, "added_at": r[3]}


def list_items(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    """newest first"""
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT item_type, item_id, meta_json, added_at FROM {TABLE} "
            "WHERE profile_id = ? ORDER BY added_at DESC LIMIT ?",
            (int(profile_id), MAX_ITEMS)).fetchall()
    return [_row(r) for r in rows]


def add(db: Any, profile_id: int, item_type: Any, item_id: Any, meta: Any = None) -> Dict[str, Any]:
    kind, ident = _clean(item_type, item_id)
    try:
        cleaned = clean_meta(meta)
    except ProgressError as e:
        raise WatchlistError(str(e)) from e
    ensure_schema(db)
    now = time.time()
    with db._get_connection() as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE profile_id = ?", (int(profile_id),)).fetchone()[0]
        exists = conn.execute(f"SELECT 1 FROM {TABLE} WHERE profile_id = ? AND item_type = ? AND item_id = ?",
                              (int(profile_id), kind, ident)).fetchone()
        if not exists and count >= MAX_ITEMS:
            raise WatchlistError(f"a watchlist holds at most {MAX_ITEMS} titles")
        # already on it: keep its place, refresh what it shows
        conn.execute(
            f"INSERT INTO {TABLE} (profile_id, item_type, item_id, meta_json, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_id, item_type, item_id) DO UPDATE SET meta_json = excluded.meta_json",
            (int(profile_id), kind, ident, json.dumps(cleaned) if cleaned else None, now))
        conn.commit()
        row = conn.execute(
            f"SELECT item_type, item_id, meta_json, added_at FROM {TABLE} "
            "WHERE profile_id = ? AND item_type = ? AND item_id = ?", (int(profile_id), kind, ident)).fetchone()
    return _row(row)


def remove(db: Any, profile_id: int, item_type: Any, item_id: Any) -> bool:
    kind, ident = _clean(item_type, item_id)
    ensure_schema(db)
    with db._get_connection() as conn:
        n = conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ? AND item_type = ? AND item_id = ?",
                         (int(profile_id), kind, ident)).rowcount
        conn.commit()
    return n > 0

