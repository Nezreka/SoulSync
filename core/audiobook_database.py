"""Audiobook database — wishlist, download history and owned titles.

Its OWN SQLite file, not a set of tables inside music_library.db.

That is the whole point. The music database is 9GB of irreplaceable library and
enrichment data on Boulder's install, and every schema change to it is a change
to something the entire music side depends on. The video subsystem already
solved this by living in database/video_library.db, and audiobooks follow the
same rule: a new file, a new connection, nothing shared. A bug in here cannot
corrupt, lock, or migrate anything the music side reads.

ASIN is the identity throughout, the same key the catalog client uses.

Schema changes ride _COLUMN_MIGRATIONS rather than being edited into the CREATE
TABLE statements, because an existing install has already run the CREATE and
will never run it again — a column added only to the CREATE arrives for fresh
installs and silently never appears for anyone who has been running the app.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_database")

DEFAULT_DB_PATH = os.path.join("database", "audiobooks.db")

# Wishlist row states.
STATUS_WANTED = "wanted"        # waiting for the next search pass
STATUS_SEARCHING = "searching"  # a search is running right now
STATUS_GRABBED = "grabbed"      # handed to a download client, not yet imported
STATUS_DONE = "done"            # imported into the library
STATUS_FAILED = "failed"        # last attempt failed; retried on a later pass

_STATUSES = (STATUS_WANTED, STATUS_SEARCHING, STATUS_GRABBED, STATUS_DONE, STATUS_FAILED)

# Whether a wishlisted book must be downloaded in the narrator's reading it was
# wished for. On Audible the narrator is baked into the ASIN, so picking a book
# already picks a reading; this only says how strictly to hold the download to
# it. Never a list — a book is always exactly one narrator.
NARRATOR_EXACT = "exact"
NARRATOR_ANY = "any"
_NARRATOR_MODES = (NARRATOR_EXACT, NARRATOR_ANY)

# (table, column, DDL type/default). Applied on every open, in order. A column
# added only to CREATE TABLE arrives for fresh installs and silently never
# appears for anyone already running.
_COLUMN_MIGRATIONS = (
    ("audiobook_wishlist", "narrator_mode", f"TEXT DEFAULT '{NARRATOR_EXACT}'"),
)


def _now() -> float:
    return time.time()


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(value or [])
    except (TypeError, ValueError):
        return "[]"


def _json_load(raw: Any) -> List[Any]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, list) else []
    except (TypeError, ValueError):
        return []


class AudiobookDatabase:
    """Thin SQLite wrapper for the audiobook subsystem.

    One connection per thread. sqlite3 objects cannot be shared across threads,
    and this is read from request handlers and written from a background search
    worker, so a thread-local is the simplest correct answer.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            directory = os.path.dirname(self.db_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            # WAL so a long search pass writing rows never blocks the page
            # reading them. Same reason the rest of the app uses it.
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error as exc:
                logger.debug("Could not set pragmas on the audiobook db: %s", exc)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audiobook_wishlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin TEXT NOT NULL,
                    profile_id INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL,
                    subtitle TEXT DEFAULT '',
                    authors TEXT DEFAULT '[]',
                    narrators TEXT DEFAULT '[]',
                    series_title TEXT DEFAULT '',
                    series_sequence TEXT DEFAULT '',
                    cover_url TEXT DEFAULT '',
                    runtime_minutes INTEGER DEFAULT 0,
                    release_date TEXT DEFAULT '',
                    language TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'wanted',
                    narrator_mode TEXT DEFAULT 'exact',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at REAL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    added_at REAL NOT NULL,
                    UNIQUE (asin, profile_id)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ab_wishlist_status "
                "ON audiobook_wishlist (profile_id, status, last_attempt_at)"
            )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audiobook_downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    download_id TEXT NOT NULL UNIQUE,
                    asin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT DEFAULT '',
                    source TEXT NOT NULL,
                    release_title TEXT DEFAULT '',
                    indexer TEXT DEFAULT '',
                    client_id TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress REAL NOT NULL DEFAULT 0,
                    bytes_done INTEGER NOT NULL DEFAULT 0,
                    bytes_total INTEGER NOT NULL DEFAULT 0,
                    save_path TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL DEFAULT 0
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ab_downloads_status "
                "ON audiobook_downloads (status, created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ab_downloads_asin "
                "ON audiobook_downloads (asin)"
            )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audiobook_library (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    author TEXT DEFAULT '',
                    narrator TEXT DEFAULT '',
                    series_title TEXT DEFAULT '',
                    series_sequence TEXT DEFAULT '',
                    path TEXT NOT NULL,
                    file_count INTEGER DEFAULT 0,
                    size_bytes INTEGER DEFAULT 0,
                    audio_format TEXT DEFAULT '',
                    runtime_minutes INTEGER DEFAULT 0,
                    imported_at REAL NOT NULL
                )
            """)

            self._apply_column_migrations(cursor)
            conn.commit()
            self._initialized = True

    def _apply_column_migrations(self, cursor: sqlite3.Cursor) -> None:
        """Add any column an existing install is missing.

        A column added only to CREATE TABLE arrives for fresh installs and never
        for anyone already running, because CREATE TABLE IF NOT EXISTS is a
        no-op the second time.
        """
        for table, column, ddl in _COLUMN_MIGRATIONS:
            try:
                existing = {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    logger.info("Added %s.%s to the audiobook database", table, column)
            except sqlite3.Error as exc:
                logger.warning("Audiobook column migration %s.%s failed: %s", table, column, exc)

    # ------------------------------------------------------------------
    # Wishlist
    # ------------------------------------------------------------------

    def add_to_wishlist(
        self,
        book: Dict[str, Any],
        profile_id: int = 1,
        narrator_mode: str = NARRATOR_EXACT,
    ) -> bool:
        """Want a book. Returns True when a row was created.

        Idempotent: wanting something already wanted is not an error and does
        not reset the attempt count, so re-adding from the UI cannot be used to
        dodge the retry backoff.
        """
        asin = str(book.get("asin") or "").strip()
        title = str(book.get("title") or "").strip()
        if not asin or not title:
            return False
        if narrator_mode not in _NARRATOR_MODES:
            narrator_mode = NARRATOR_EXACT

        series = (book.get("series") or [{}])[0] if book.get("series") else {}
        conn = self._connect()
        try:
            conn.execute("""
                INSERT INTO audiobook_wishlist
                    (asin, profile_id, title, subtitle, authors, narrators,
                     series_title, series_sequence, cover_url, runtime_minutes,
                     release_date, language, status, narrator_mode, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asin, int(profile_id), title,
                str(book.get("subtitle") or ""),
                _json_dump(book.get("author_names")),
                _json_dump(book.get("narrator_names")),
                str(series.get("title") or ""),
                str(series.get("sequence") or ""),
                str(book.get("cover_url") or ""),
                int(book.get("runtime_minutes") or 0),
                str(book.get("release_date") or ""),
                str(book.get("language") or ""),
                STATUS_WANTED, narrator_mode, _now(),
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as exc:
            logger.warning("Could not add %s to the audiobook wishlist: %s", asin, exc)
            return False

    def remove_from_wishlist(self, asin: str, profile_id: int = 1) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM audiobook_wishlist WHERE asin = ? AND profile_id = ?",
                (str(asin or "").strip(), int(profile_id)),
            )
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("Could not remove %s from the audiobook wishlist: %s", asin, exc)
            return False

    def is_wishlisted(self, asin: str, profile_id: int = 1) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM audiobook_wishlist WHERE asin = ? AND profile_id = ?",
            (str(asin or "").strip(), int(profile_id)),
        ).fetchone()
        return row is not None

    def get_wishlist(self, profile_id: int = 1, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        sql = "SELECT * FROM audiobook_wishlist WHERE profile_id = ?"
        params: List[Any] = [int(profile_id)]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY added_at DESC"
        return [self._wishlist_row(row) for row in conn.execute(sql, params)]

    def get_wishlist_due(
        self,
        profile_id: int = 1,
        retry_after_seconds: float = 6 * 3600,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Rows the next search pass should try.

        Anything wanted or previously failed, whose last attempt is older than
        the backoff. Rows already grabbed or done are never retried, and rows
        marked searching are skipped so two passes cannot both claim one.
        """
        conn = self._connect()
        cutoff = _now() - max(0.0, float(retry_after_seconds))
        rows = conn.execute("""
            SELECT * FROM audiobook_wishlist
            WHERE profile_id = ?
              AND status IN (?, ?)
              AND last_attempt_at <= ?
            ORDER BY last_attempt_at ASC, added_at ASC
            LIMIT ?
        """, (int(profile_id), STATUS_WANTED, STATUS_FAILED, cutoff, max(1, int(limit))))
        return [self._wishlist_row(row) for row in rows]

    def mark_wishlist_status(
        self,
        asin: str,
        status: str,
        profile_id: int = 1,
        error: str = "",
        count_attempt: bool = False,
    ) -> bool:
        """Move a row to a new state.

        ``count_attempt`` is what drives the backoff, and it is deliberately
        separate from the status: a pass that finds nothing must increment it,
        while a user re-adding a book must not.
        """
        if status not in _STATUSES:
            return False
        conn = self._connect()
        try:
            if count_attempt:
                cursor = conn.execute("""
                    UPDATE audiobook_wishlist
                    SET status = ?, last_error = ?, last_attempt_at = ?,
                        attempt_count = attempt_count + 1
                    WHERE asin = ? AND profile_id = ?
                """, (status, str(error or ""), _now(), str(asin or "").strip(), int(profile_id)))
            else:
                cursor = conn.execute("""
                    UPDATE audiobook_wishlist
                    SET status = ?, last_error = ?
                    WHERE asin = ? AND profile_id = ?
                """, (status, str(error or ""), str(asin or "").strip(), int(profile_id)))
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("Could not update wishlist status for %s: %s", asin, exc)
            return False

    def wishlist_counts(self, profile_id: int = 1) -> Dict[str, int]:
        conn = self._connect()
        counts = {status: 0 for status in _STATUSES}
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM audiobook_wishlist WHERE profile_id = ? GROUP BY status",
            (int(profile_id),),
        ):
            counts[row["status"]] = row["n"]
        counts["total"] = sum(counts[status] for status in _STATUSES)
        return counts

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def record_download(
        self,
        download_id: str,
        asin: str,
        title: str,
        source: str,
        client_id: str = "",
        release_title: str = "",
        indexer: str = "",
        author: str = "",
        bytes_total: int = 0,
    ) -> bool:
        """Remember that a release was handed to a download client.

        Without a row here a grab is fire-and-forget: the client is downloading
        something the app has no idea about, so nothing can ever notice it
        finished and file it into the library.
        """
        download_id = str(download_id or "").strip()
        if not download_id:
            return False
        now = _now()
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO audiobook_downloads
                    (download_id, asin, title, author, source, release_title, indexer,
                     client_id, status, progress, bytes_done, bytes_total,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloading', 0, 0, ?, ?, ?)
            """, (download_id, str(asin or ""), str(title or ""), str(author or ""),
                  str(source or ""), str(release_title or ""), str(indexer or ""),
                  str(client_id or ""), int(bytes_total or 0), now, now))
            conn.commit()
            return True
        except sqlite3.Error as exc:
            logger.warning("Could not record the audiobook download %s: %s", download_id, exc)
            return False

    def get_downloads(self, active_only: bool = False) -> List[Dict[str, Any]]:
        conn = self._connect()
        sql = "SELECT * FROM audiobook_downloads"
        if active_only:
            sql += " WHERE status IN ('queued', 'downloading', 'importing')"
        sql += " ORDER BY created_at DESC"
        return [dict(row) for row in conn.execute(sql)]

    def update_download(
        self,
        download_id: str,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        bytes_done: Optional[int] = None,
        bytes_total: Optional[int] = None,
        save_path: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Patch whatever changed. Only the fields given are written.

        Partial by design: the monitor learns the save path and the byte totals
        at different moments, and a full-row update would blank whichever it did
        not have yet.
        """
        fields: List[str] = ["updated_at = ?"]
        params: List[Any] = [_now()]
        for column, value in (
            ("status", status), ("progress", progress), ("bytes_done", bytes_done),
            ("bytes_total", bytes_total), ("save_path", save_path), ("error", error),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if status in ("completed", "failed"):
            fields.append("completed_at = ?")
            params.append(_now())
        params.append(str(download_id or "").strip())

        conn = self._connect()
        try:
            cursor = conn.execute(
                f"UPDATE audiobook_downloads SET {', '.join(fields)} WHERE download_id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            logger.warning("Could not update the audiobook download %s: %s", download_id, exc)
            return False

    def add_to_library(self, book: Dict[str, Any], path: str, **extra: Any) -> bool:
        """Record an imported book so the UI can say "you already have this"."""
        asin = str(book.get("asin") or "").strip()
        if not asin or not path:
            return False
        authors = book.get("author_names") or book.get("authors") or []
        narrators = book.get("narrator_names") or book.get("narrators") or []
        series = (book.get("series") or [{}])[0] if book.get("series") else {}
        conn = self._connect()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO audiobook_library
                    (asin, title, author, narrator, series_title, series_sequence,
                     path, file_count, size_bytes, audio_format, runtime_minutes, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asin, str(book.get("title") or ""),
                str(authors[0]) if authors else "",
                str(narrators[0]) if narrators else "",
                str(series.get("title") or ""), str(series.get("sequence") or ""),
                str(path), int(extra.get("file_count") or 0),
                int(extra.get("size_bytes") or 0), str(extra.get("audio_format") or ""),
                int(book.get("runtime_minutes") or 0), _now(),
            ))
            conn.commit()
            return True
        except sqlite3.Error as exc:
            logger.warning("Could not record %s in the audiobook library: %s", asin, exc)
            return False

    def is_owned(self, asin: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM audiobook_library WHERE asin = ?", (str(asin or "").strip(),),
        ).fetchone()
        return row is not None

    def get_library(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        return [dict(row) for row in conn.execute(
            "SELECT * FROM audiobook_library ORDER BY imported_at DESC")]

    @staticmethod
    def _wishlist_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "asin": row["asin"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "authors": _json_load(row["authors"]),
            "narrators": _json_load(row["narrators"]),
            "series_title": row["series_title"],
            "series_sequence": row["series_sequence"],
            "cover_url": row["cover_url"],
            "runtime_minutes": row["runtime_minutes"],
            "release_date": row["release_date"],
            "language": row["language"],
            "status": row["status"],
            # Older rows predate the column; "exact" is the safe reading of a
            # book wished for before the choice existed.
            "narrator_mode": (
                row["narrator_mode"] if "narrator_mode" in row.keys() else NARRATOR_EXACT
            ) or NARRATOR_EXACT,
            "attempt_count": row["attempt_count"],
            "last_attempt_at": row["last_attempt_at"],
            "last_error": row["last_error"],
            "added_at": row["added_at"],
        }


_default_db: Optional[AudiobookDatabase] = None
_db_lock = threading.Lock()


def subsystem_in_use(db_path: str = DEFAULT_DB_PATH) -> bool:
    """True when this install has actually used audiobooks.

    The database file is only created on first real use, so its absence is a
    reliable "this user has never opened the audiobooks page". The background
    threads check this before starting, which keeps an install that never
    touches the feature completely unchanged by it — no file, no threads, no
    polling.
    """
    return os.path.exists(db_path)


def get_audiobook_db() -> AudiobookDatabase:
    """The process-wide audiobook database.

    Built lazily under a lock. Its file is created on first use, so an install
    that never opens the audiobooks page never grows one.
    """
    global _default_db
    if _default_db is None:
        with _db_lock:
            if _default_db is None:
                _default_db = AudiobookDatabase()
    return _default_db


def _reset_for_tests(db_path: Optional[str] = None) -> AudiobookDatabase:
    """Point the singleton at a temp file. Tests only."""
    global _default_db
    with _db_lock:
        if _default_db is not None:
            _default_db.close()
        _default_db = AudiobookDatabase(db_path or DEFAULT_DB_PATH)
    return _default_db
