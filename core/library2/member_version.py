"""A counter that moves whenever "which artists are in the library" can change.

The artist grid works out library membership (files, monitoring, wanted
tracks, through album and track credits) on every page and every scroll: ~1s
on a 29k-artist, 311k-track library (Oct 31 2026). The answer only changes
when one of the tables below does, so triggers bump one counter on any such
change and the page keeps its member list until the counter moves.

Never stale by design: a write that could change membership bumps the counter
in the same transaction as the write. A bump that changes nothing costs one
extra recompute, never a wrong page.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.member_version")

STATE = "lib2_member_state"

_BUMP = f"UPDATE {STATE} SET version = version + 1 WHERE id = 1"

# (table, event) pairs whose rows feed library_artist_ids_sql
_WATCHED = (
    ("lib2_track_files", "INSERT"),
    ("lib2_track_files", "DELETE"),
    ("lib2_track_files", "UPDATE OF path, file_state, owner_profile_id, track_id"),
    ("lib2_tracks", "INSERT"),
    ("lib2_tracks", "DELETE"),
    ("lib2_tracks", "UPDATE OF album_id, monitored"),
    ("lib2_albums", "INSERT"),
    ("lib2_albums", "DELETE"),
    ("lib2_albums", "UPDATE OF primary_artist_id, monitored"),
    ("lib2_artists", "INSERT"),
    ("lib2_artists", "DELETE"),
    ("lib2_artists", "UPDATE OF monitored, canonical_artist_id"),
    ("lib2_track_artists", "INSERT"),
    ("lib2_track_artists", "DELETE"),
    ("lib2_track_artists", "UPDATE"),
    ("lib2_monitor_rules", "INSERT"),
    ("lib2_monitor_rules", "DELETE"),
    ("lib2_monitor_rules", "UPDATE"),
    ("lib2_wanted_tracks", "INSERT"),
    ("lib2_wanted_tracks", "DELETE"),
    ("lib2_wanted_tracks", "UPDATE"),
)


def _trigger_name(table: str, event: str) -> str:
    return f"trg_member_version_{table}_{event.split()[0].lower()}"


def ensure_schema(cursor: Any) -> None:
    """The counter row and its triggers. Bounded DDL, safe at every start.
    Never raises: without it the grid computes membership every time, as it
    always did."""
    try:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {STATE} ("
                       "id INTEGER PRIMARY KEY CHECK (id = 1), "
                       "version INTEGER NOT NULL DEFAULT 0)")
        cursor.execute(f"INSERT OR IGNORE INTO {STATE}(id) VALUES (1)")
        for table, event in _WATCHED:
            exists = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue
            cursor.execute(
                f"CREATE TRIGGER IF NOT EXISTS {_trigger_name(table, event)} "
                f"AFTER {event} ON {table} BEGIN {_BUMP}; END")
    except Exception as exc:  # noqa: BLE001
        logger.error("library member counter failed (will retry next start): %s", exc)


def current_version(conn: Any) -> Optional[int]:
    """The counter, or None when it isn't there (membership is then computed
    every time)."""
    try:
        row = conn.execute(f"SELECT version FROM {STATE} WHERE id = 1").fetchone()
    except Exception:  # noqa: BLE001 - no table yet
        return None
    return int(row[0]) if row else None


_cache_lock = threading.Lock()
# (database file, membership sql) -> (counter it was computed at, member ids)
_cache: Dict[Tuple[str, str], Tuple[int, List[int]]] = {}  # (file identity, sql)


def _db_file(conn: Any) -> str:
    try:
        for row in conn.execute("PRAGMA database_list"):
            if row[1] == "main":
                return str(row[2] or "")
    except Exception:  # noqa: BLE001, S110 - unknown file: never shared
        pass
    return ""


def cached_member_ids(conn: Any, member_sql: str) -> Optional[List[int]]:
    """The ids ``member_sql`` selects, reused while the counter stands still,
    or None when there is no counter (or no file to key it by): the caller
    then runs the SQL itself. The counter is read BEFORE computing, so a write
    landing mid-compute leaves an older number behind and the next read
    recomputes."""
    version = current_version(conn)
    db_file = _db_file(conn)
    if version is None or not db_file:
        return None
    # the file's identity too: a restored or replaced database restarts the
    # counter, and must not match ids cached from the old file
    try:
        import os
        st = os.stat(db_file)
        ident = f"{db_file}|{st.st_dev}|{st.st_ino}"  # as the connection pool tells files apart
    except OSError:
        return None
    key = (ident, member_sql)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == version:
            return hit[1]
    ids = [int(row[0]) for row in conn.execute(member_sql) if row[0] is not None]
    with _cache_lock:
        _cache[key] = (version, ids)
    return ids


def clear() -> None:
    with _cache_lock:
        _cache.clear()


__all__ = ["STATE", "cached_member_ids", "clear", "current_version", "ensure_schema"]
