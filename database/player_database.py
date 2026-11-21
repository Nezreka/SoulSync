"""the player's own database (player.db): what each profile has watched and
how far, its watchlist, its favorite guide channels, its home page layout. kept apart from
the library databases because playback writes often (a position every few
seconds per screen) and none of it should wait on, or hold, the music
library's write lock.

the path: PLAYER_DATABASE_PATH when set (docker sets it on the persisted
volume), else beside the music database, so a setup that moved that one
moves this one with it.

no foreign keys reach the profiles table (it lives in another file), so a
deleted profile is swept here by delete_profile_data.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

TABLES_WITH_PROFILES = ("watch_progress", "player_favorite_channels", "player_home_layout",
                        "player_watchlist")


def default_path() -> str:
    explicit = os.environ.get("PLAYER_DATABASE_PATH")
    if explicit:
        return explicit
    music = os.environ.get("DATABASE_PATH", "database/music_library.db")
    return str(Path(music).parent / "player.db")


class PlayerDatabase:
    def __init__(self, database_path: Optional[str] = None):
        self.database_path = Path(database_path or default_path())
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """a fresh connection, closed on the way out (callers commit)"""
        conn = sqlite3.connect(str(self.database_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    def delete_profile_data(self, profile_id: int) -> int:
        """everything a deleted profile left here. tables not made yet are skipped."""
        removed = 0
        with self._get_connection() as conn:
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES_WITH_PROFILES:
                if table in present:
                    removed += conn.execute(
                        f"DELETE FROM {table} WHERE profile_id = ?", (int(profile_id),)).rowcount  # noqa: S608
            conn.commit()
        return removed


_instance: Optional[PlayerDatabase] = None
_lock = threading.Lock()


def get_player_database() -> PlayerDatabase:
    global _instance
    with _lock:
        if _instance is None or str(_instance.database_path) != str(Path(default_path())):
            _instance = PlayerDatabase()
        return _instance
