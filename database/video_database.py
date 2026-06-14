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

    # ── library mapping (which server library is Movies / TV) ─────────────────
    def get_library_selection(self, server: str) -> dict:
        return {
            "movies": self.get_setting(server + ".movies_library"),
            "tv": self.get_setting(server + ".tv_library"),
        }

    def set_library_selection(self, server: str, movies, tv) -> None:
        self.set_setting(server + ".movies_library", movies or "")
        self.set_setting(server + ".tv_library", tv or "")

    # ── dashboard ─────────────────────────────────────────────────────────────
    def dashboard_stats(self) -> dict:
        """Live counts for the video dashboard, straight from video.db.

        Shape is stable so the frontend can map it directly; with an empty
        database every number is a real 0 (not a stub).
        """
        conn = self._get_connection()
        try:
            def scalar(sql: str):
                return conn.execute(sql).fetchone()[0]

            return {
                "library": {
                    "movies": scalar("SELECT COUNT(*) FROM movies"),
                    "shows": scalar("SELECT COUNT(*) FROM shows"),
                    "episodes": scalar("SELECT COUNT(*) FROM episodes"),
                    "size_bytes": scalar("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files"),
                },
                "downloads": {
                    "active": scalar(
                        "SELECT COUNT(*) FROM downloads "
                        "WHERE status IN ('queued','downloading','importing')"),
                    "finished": scalar("SELECT COUNT(*) FROM downloads WHERE status = 'completed'"),
                    "speed_bps": scalar(
                        "SELECT COALESCE(SUM(download_speed_bps), 0) FROM downloads "
                        "WHERE status = 'downloading'"),
                },
                "watchlist": scalar("SELECT COUNT(*) FROM v_watchlist"),
                "wishlist": scalar("SELECT COUNT(*) FROM v_wishlist"),
            }
        finally:
            conn.close()

    # ── scan upserts (server is the source of truth) ──────────────────────────
    # The scanner passes normalized, server-agnostic dicts (a Plex/Jellyfin
    # adapter produces them) so this layer never touches a media-server SDK.
    @staticmethod
    def _set_media_file(conn, owner_col: str, owner_id: int, file: dict | None) -> None:
        """Replace the media_files row(s) for one owner. owner_col is internal
        ('movie_id'|'episode_id'|'video_id'), never user input."""
        conn.execute(f"DELETE FROM media_files WHERE {owner_col} = ?", (owner_id,))
        if not file:
            return
        conn.execute(
            f"INSERT INTO media_files ({owner_col}, relative_path, size_bytes, resolution, "
            "video_codec, audio_codec, release_source, quality, runtime_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (owner_id,
             file.get("relative_path") or file.get("path") or "",
             file.get("size_bytes"), file.get("resolution"), file.get("video_codec"),
             file.get("audio_codec"), file.get("release_source"), file.get("quality"),
             file.get("runtime_seconds")),
        )

    def upsert_movie(self, server_source: str, item: dict) -> int:
        """Insert/update one movie (keyed on server id) and its file. Returns row id."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO movies (server_source, server_id, title, year, overview, "
                "runtime_minutes, content_rating, studio, poster_url, has_file, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(server_source, server_id) DO UPDATE SET "
                "title=excluded.title, year=excluded.year, overview=excluded.overview, "
                "runtime_minutes=excluded.runtime_minutes, content_rating=excluded.content_rating, "
                "studio=excluded.studio, poster_url=excluded.poster_url, "
                "has_file=excluded.has_file, updated_at=CURRENT_TIMESTAMP",
                (server_source, item["server_id"], item.get("title"), item.get("year"),
                 item.get("overview"), item.get("runtime_minutes"), item.get("content_rating"),
                 item.get("studio"), item.get("poster_url"), 1 if item.get("file") else 0),
            )
            movie_id = conn.execute(
                "SELECT id FROM movies WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            self._set_media_file(conn, "movie_id", movie_id, item.get("file"))
            conn.commit()
            return movie_id
        finally:
            conn.close()

    def upsert_show_tree(self, server_source: str, item: dict) -> int:
        """Insert/update a show with its seasons + episodes (and files) in one
        transaction. Episodes/seasons no longer present on the server for this
        show are pruned. Returns the show row id."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO shows (server_source, server_id, title, year, overview, status, "
                "network, runtime_minutes, content_rating, poster_url, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(server_source, server_id) DO UPDATE SET "
                "title=excluded.title, year=excluded.year, overview=excluded.overview, "
                "status=excluded.status, network=excluded.network, "
                "runtime_minutes=excluded.runtime_minutes, content_rating=excluded.content_rating, "
                "poster_url=excluded.poster_url, updated_at=CURRENT_TIMESTAMP",
                (server_source, item["server_id"], item.get("title"), item.get("year"),
                 item.get("overview"), item.get("status"), item.get("network"),
                 item.get("runtime_minutes"), item.get("content_rating"), item.get("poster_url")),
            )
            show_id = conn.execute(
                "SELECT id FROM shows WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]

            seen_seasons: set[int] = set()
            seen_eps: set[tuple[int, int]] = set()
            for season in item.get("seasons", []):
                snum = season["season_number"]
                seen_seasons.add(snum)
                conn.execute(
                    "INSERT INTO seasons (show_id, server_id, season_number, title, overview, poster_url) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(show_id, season_number) DO UPDATE SET "
                    "server_id=excluded.server_id, title=excluded.title, "
                    "overview=excluded.overview, poster_url=excluded.poster_url",
                    (show_id, season.get("server_id"), snum, season.get("title"),
                     season.get("overview"), season.get("poster_url")),
                )
                season_id = conn.execute(
                    "SELECT id FROM seasons WHERE show_id=? AND season_number=?", (show_id, snum)
                ).fetchone()["id"]

                for ep in season.get("episodes", []):
                    enum = ep.get("episode_number")
                    if enum is None or snum is None:
                        continue  # can't key an episode without season+episode numbers
                    seen_eps.add((snum, enum))
                    conn.execute(
                        "INSERT INTO episodes (show_id, season_id, server_source, server_id, "
                        "season_number, episode_number, title, overview, air_date, "
                        "runtime_minutes, has_file) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(show_id, season_number, episode_number) DO UPDATE SET "
                        "season_id=excluded.season_id, server_source=excluded.server_source, "
                        "server_id=excluded.server_id, title=excluded.title, "
                        "overview=excluded.overview, air_date=excluded.air_date, "
                        "runtime_minutes=excluded.runtime_minutes, has_file=excluded.has_file",
                        (show_id, season_id, server_source, ep.get("server_id"), snum, enum,
                         ep.get("title"), ep.get("overview"), ep.get("air_date"),
                         ep.get("runtime_minutes"), 1 if ep.get("file") else 0),
                    )
                    ep_id = conn.execute(
                        "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, snum, enum),
                    ).fetchone()["id"]
                    self._set_media_file(conn, "episode_id", ep_id, ep.get("file"))

            # Prune episodes/seasons that vanished from the server for this show.
            for row in conn.execute(
                "SELECT season_number, episode_number FROM episodes WHERE show_id=?", (show_id,)
            ).fetchall():
                if (row["season_number"], row["episode_number"]) not in seen_eps:
                    conn.execute(
                        "DELETE FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, row["season_number"], row["episode_number"]),
                    )
            for row in conn.execute(
                "SELECT season_number FROM seasons WHERE show_id=?", (show_id,)
            ).fetchall():
                if row["season_number"] not in seen_seasons:
                    conn.execute("DELETE FROM seasons WHERE show_id=? AND season_number=?",
                                 (show_id, row["season_number"]))
            conn.commit()
            return show_id
        finally:
            conn.close()

    def prune_missing(self, table: str, server_source: str, seen_ids) -> int:
        """Delete top-level rows for a server that the scan no longer saw.
        ``table`` is internal ('movies'|'shows'); cascades clean children."""
        if table not in ("movies", "shows"):
            raise ValueError(f"prune_missing: unexpected table {table!r}")
        seen = {str(s) for s in seen_ids}
        conn = self._get_connection()
        try:
            existing = [r["server_id"] for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=?", (server_source,)
            ).fetchall()]
            stale = [sid for sid in existing if str(sid) not in seen]
            for sid in stale:
                conn.execute(f"DELETE FROM {table} WHERE server_source=? AND server_id=?",
                             (server_source, sid))
            conn.commit()
            return len(stale)
        finally:
            conn.close()

    # ── library listing ───────────────────────────────────────────────────────
    @staticmethod
    def _with_poster_flag(row: dict) -> dict:
        # Don't leak the raw server thumb path; just say whether a poster exists
        # (the frontend hits /api/video/poster/<kind>/<id> when true).
        d = dict(row)
        d["has_poster"] = bool(d.pop("poster_url", None))
        return d

    def list_movies(self) -> list[dict]:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, title, year, poster_url, has_file, monitored "
                "FROM movies ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, title"
            ).fetchall()
            return [self._with_poster_flag(r) for r in rows]
        finally:
            conn.close()

    def list_shows(self) -> list[dict]:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.year, s.poster_url, s.monitored, "
                "(SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id) AS episode_count, "
                "(SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id AND e.has_file = 1) AS owned_count "
                "FROM shows s ORDER BY COALESCE(s.sort_title, s.title) COLLATE NOCASE, s.title"
            ).fetchall()
            return [self._with_poster_flag(r) for r in rows]
        finally:
            conn.close()

    def get_poster_ref(self, kind: str, item_id: int) -> dict | None:
        """Server source/id/poster path for one movie or show, for the poster proxy."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT server_source, server_id, poster_url FROM {table} WHERE id=?",
                (item_id,)).fetchone()
            return dict(row) if row else None
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
