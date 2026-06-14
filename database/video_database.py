"""SoulSync — VIDEO side database (database/video_library.db).

ISOLATION CONTRACT: this module owns a SEPARATE SQLite file from the music
library and imports NOTHING from the music database layer. Music code never
imports this; this never imports music. A migration bug, corruption, or reset
here cannot touch music data, and the two never contend for the same write lock.

Conventions mirror database/music_database.py on purpose (so the two feel the
same operationally) — WAL journal, foreign keys ON, a 30s busy timeout, Row
factory, a once-per-process init guard, and PRAGMA user_version as a schema
backstop — but the implementations are independent.

The schema itself lives alongside this file in video_schema.sql and is executed
verbatim on first init.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("video_database")

# Bump when video_schema.sql changes in a way worth recording. Stored in
# PRAGMA user_version as a backstop indicator (nothing gates on it yet).
SCHEMA_VERSION = 1

_DEFAULT_DB_PATH = "database/video_library.db"
_SCHEMA_FILE = Path(__file__).resolve().parent / "video_schema.sql"

# Init runs once per database path per process (same guard style as music).
_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


class VideoDatabase:
    """Connection + schema manager for the isolated video library DB."""

    def __init__(self, database_path: str | None = None):
        # Honour the env override (Docker mounts) the same way music does, but
        # under a DISTINCT variable so the two databases never collide.
        if database_path is None or database_path == _DEFAULT_DB_PATH:
            database_path = os.environ.get("VIDEO_DATABASE_PATH", _DEFAULT_DB_PATH)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_once()

    # ── connection ──────────────────────────────────────────────────────────
    def _get_connection(self) -> sqlite3.Connection:
        """A fresh connection with the standard pragmas applied."""
        conn = sqlite3.connect(str(self.database_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")  # 30s
        return conn

    def connect(self) -> sqlite3.Connection:
        """Public connection factory — caller owns closing it.

        Prefer using ``with db.connect() as conn:`` so commits/rollbacks and
        close happen automatically.
        """
        return self._get_connection()

    # ── init ────────────────────────────────────────────────────────────────
    def _initialize_once(self) -> None:
        key = str(self.database_path.resolve())
        with _init_lock:
            if key in _initialized_paths:
                return
            self._initialize_database()
            _initialized_paths.add(key)

    def _initialize_database(self) -> None:
        schema = _SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self._get_connection()
        try:
            conn.executescript(schema)
            conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
            conn.commit()
            logger.info(
                "Video database ready at %s (schema v%d)",
                self.database_path, SCHEMA_VERSION,
            )
        except Exception:
            conn.rollback()
            logger.exception("Failed to initialize video database at %s", self.database_path)
            raise
        finally:
            conn.close()

    @property
    def schema_version(self) -> int:
        conn = self._get_connection()
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    # ── video_settings KV (temporary home until the settings.db move) ────────
    def get_setting(self, key: str, default=None):
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM video_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row is not None else default
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO video_settings(key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = CURRENT_TIMESTAMP",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    # ── health ───────────────────────────────────────────────────────────────
    def health_check(self) -> bool:
        """True when the DB opens and passes a quick integrity check."""
        conn = self._get_connection()
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        except Exception:
            logger.exception("Video database health check failed")
            return False
        finally:
            conn.close()
