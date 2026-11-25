"""favorite guide channels, per profile, kept on the server so a phone, a TV
and a browser all show the same favorites. a channel is its guide id (a
string the guide makes from the library: 'genre-comedy', 'network-hbo')."""

from __future__ import annotations

import re
from typing import Any, List

TABLE = "player_favorite_channels"
MAX = 100
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,95}$")

_ready: set = set()


class FavoritesError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                profile_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (profile_id, channel_id)
            )""")
        conn.commit()
    _ready.add(key)


def clean(channels: Any) -> List[str]:
    if not isinstance(channels, list):
        raise FavoritesError("channels must be a list")
    out: List[str] = []
    for value in channels:
        text = str(value or "").strip().lower()
        if not CHANNEL_RE.match(text):
            raise FavoritesError(f"not a channel id: {str(value)[:40]!r}")
        if text not in out:
            out.append(text)
    if len(out) > MAX:
        raise FavoritesError(f"at most {MAX} favorites")
    return out


def get(db: Any, profile_id: int) -> List[str]:
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT channel_id FROM {TABLE} WHERE profile_id = ? ORDER BY position, channel_id",
            (int(profile_id),)).fetchall()
    return [r[0] for r in rows]


def put(db: Any, profile_id: int, channels: Any) -> List[str]:
    """replace this profile's favorites with the list, in its order"""
    ids = clean(channels)
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ?", (int(profile_id),))
        conn.executemany(
            f"INSERT INTO {TABLE} (profile_id, channel_id, position) VALUES (?, ?, ?)",
            [(int(profile_id), cid, i) for i, cid in enumerate(ids)])
        conn.commit()
    return ids
