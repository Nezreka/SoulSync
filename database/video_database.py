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
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("video_database")

# Bump when video_schema.sql changes in a way worth recording. Stored in
# PRAGMA user_version as a backstop indicator (nothing gates on it yet).
SCHEMA_VERSION = 8

_DEFAULT_DB_PATH = "database/video_library.db"
_SCHEMA_FILE = Path(__file__).resolve().parent / "video_schema.sql"

# Init runs once per database path per process (same guard style as music).
_init_lock = threading.Lock()
_initialized_paths: set[str] = set()

# Sort/letter key: title without a leading article, lowercased (so "The Matrix"
# files under M, like music's library).
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _sort_title(title) -> str:
    return _ARTICLE_RE.sub("", (title or "").strip()).lower()


# Enrichment plumbing (parallels music's per-source columns). Maps a service +
# content kind to (table, id_col, match_status_col, last_attempted_col).
_ENRICH = {
    "tmdb": {
        "movie": ("movies", "tmdb_id", "tmdb_match_status", "tmdb_last_attempted"),
        "show": ("shows", "tmdb_id", "tmdb_match_status", "tmdb_last_attempted"),
    },
    "tvdb": {
        "show": ("shows", "tvdb_id", "tvdb_match_status", "tvdb_last_attempted"),
    },
}

# Whitelist of metadata columns enrichment may write per table (guards against
# arbitrary keys; backfill semantics applied by the caller).
_ENRICH_META_COLS = {
    "movies": {"overview", "backdrop_url", "logo_url", "release_date", "status", "content_rating",
               "runtime_minutes", "studio", "tagline", "rating", "rating_critic",
               "imdb_id", "tmdb_id"},
    "shows": {"overview", "backdrop_url", "logo_url", "status", "network", "content_rating",
              "tagline", "rating", "first_air_date", "last_air_date", "airs_time",
              "imdb_id", "tmdb_id", "tvdb_id"},
}

# Columns ensured on existing DBs (ALTER TABLE ADD COLUMN; idempotent).
_COLUMN_MIGRATIONS = [
    ("movies", "tmdb_match_status", "TEXT"),
    ("movies", "tmdb_last_attempted", "TEXT"),
    ("shows", "tmdb_match_status", "TEXT"),
    ("shows", "tmdb_last_attempted", "TEXT"),
    ("shows", "tvdb_match_status", "TEXT"),
    ("shows", "tvdb_last_attempted", "TEXT"),
    # "capture everything" — richer metadata from the server.
    ("movies", "tagline", "TEXT"),
    ("movies", "rating", "REAL"),
    ("movies", "rating_critic", "REAL"),
    ("shows", "tagline", "TEXT"),
    ("shows", "rating", "REAL"),
    ("shows", "first_air_date", "TEXT"),
    ("shows", "last_air_date", "TEXT"),
    ("episodes", "still_url", "TEXT"),
    ("episodes", "rating", "REAL"),
    ("movies", "logo_url", "TEXT"),
    ("shows", "logo_url", "TEXT"),
    ("shows", "episodes_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("movies", "imdb_rating", "REAL"), ("movies", "rt_rating", "INTEGER"),
    ("movies", "metacritic", "INTEGER"),
    ("shows", "imdb_rating", "REAL"), ("shows", "rt_rating", "INTEGER"),
    ("shows", "metacritic", "INTEGER"),
    ("movies", "ratings_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("shows", "ratings_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("shows", "airs_time", "TEXT"),   # TVDB show air time, e.g. "21:00" (network local)
    ("video_watchlist", "state", "TEXT NOT NULL DEFAULT 'follow'"),  # follow | mute (tombstone)
]


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
            self._ensure_columns(conn)
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

    @staticmethod
    def _ensure_columns(conn) -> None:
        """Add any new columns to an existing DB (idempotent ALTER TABLE)."""
        for table, col, coltype in _COLUMN_MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    # ── enrichment plumbing (per-source match status, like music) ─────────────
    def enrichment_next(self, service: str, retry_days: int = 30, priority=None) -> dict | None:
        """Next item that needs enrichment for a service: pending (never tried)
        first, then a not_found item older than retry_days. Returns
        {kind, id, title, year, known_id} or None. ``known_id`` is the provider
        id the media server already supplied (e.g. tmdb_id/tvdb_id) so the worker
        can enrich BY ID instead of re-searching by title.

        ``priority`` ('movie'/'show') pins a kind to be processed first across the
        queue — drives the modal's 'Process first everywhere' control."""
        kinds = _ENRICH.get(service)
        if not kinds:
            return None
        items = list(kinds.items())
        if priority in kinds:
            items.sort(key=lambda kv: 0 if kv[0] == priority else 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retry_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()

        def _row(row, kind, idc):
            return {"kind": kind, "id": row["id"], "title": row["title"],
                    "year": row["year"], "known_id": row[idc]}

        try:
            for kind, (tbl, idc, sc, _ac) in items:
                row = conn.execute(
                    f"SELECT id, title, year, {idc} FROM {tbl} WHERE {sc} IS NULL ORDER BY id LIMIT 1").fetchone()
                if row:
                    return _row(row, kind, idc)
            for kind, (tbl, idc, sc, ac) in items:
                row = conn.execute(
                    f"SELECT id, title, year, {idc} FROM {tbl} "
                    f"WHERE {sc} IN ('not_found','error') "
                    f"AND ({ac} IS NULL OR {ac} < ?) ORDER BY {ac} LIMIT 1", (cutoff,)).fetchone()
                if row:
                    return _row(row, kind, idc)
            return None
        finally:
            conn.close()

    def enrichment_apply(self, service: str, kind: str, item_id: int, matched: bool,
                         external_id=None, metadata: dict | None = None,
                         error: bool = False) -> None:
        """Record a match result: set match_status + last_attempted, the external
        id (when matched), and any whitelisted metadata columns.

        Status is one of 'matched' / 'not_found' / 'error'. 'error' means the
        lookup CALL failed (network/rate-limit/timeout) — distinct from a genuine
        'not_found' so a transient blip isn't permanently recorded as "no match"
        (mirrors the music workers). Both 'not_found' and 'error' are retried by
        enrichment_next after retry_days."""
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return
        tbl, idc, sc, ac = spec
        allowed = _ENRICH_META_COLS.get(tbl, set())
        status = "matched" if matched else "error" if error else "not_found"
        # On legacy DBs tmdb_id/tvdb_id may still carry a UNIQUE index; if a match
        # would collide with another row's id we drop the id columns and keep the
        # existing (authoritative) id, still recording status + metadata.
        id_cols = {"tmdb_id", "tvdb_id", "imdb_id"}

        def build(include_ids):
            sets = [f"{sc}=?", f"{ac}=CURRENT_TIMESTAMP"]
            params = [status]
            if matched and external_id is not None and include_ids:
                sets.append(f"{idc}=?")
                params.append(external_id)
            for col, val in (metadata or {}).items():
                if val is None or col not in allowed:
                    continue
                if not include_ids and col in id_cols:
                    continue
                # BACKFILL: only fill a column the server left empty — enrichment
                # fills gaps, it never clobbers data the media server provided.
                sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
                params.append(val)
            params.append(item_id)
            return f"UPDATE {tbl} SET {', '.join(sets)} WHERE id=?", params

        conn = self._get_connection()
        try:
            sql, params = build(True)
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError:
                conn.rollback()
                sql, params = build(False)   # keep existing id, just record status/metadata
                conn.execute(sql, params)
            # Genres backfill — only when the item has none yet (enrichment fills
            # the gap the server didn't). Written to the normalised link tables.
            genres = (metadata or {}).get("genres")
            link = {"movies": ("movie_genres", "movie_id"),
                    "shows": ("show_genres", "show_id")}.get(tbl)
            if matched and genres and link:
                lt, oc = link
                has = conn.execute(f"SELECT 1 FROM {lt} WHERE {oc}=? LIMIT 1", (item_id,)).fetchone()
                if not has:
                    self._set_genres(conn, lt, oc, item_id, genres)
            # Cast/crew backfill — only when the item has none yet (gap-fill).
            cast = (metadata or {}).get("cast")
            crew = (metadata or {}).get("crew")
            if matched and (cast or crew) and tbl in ("movies", "shows"):
                oc = "movie_id" if tbl == "movies" else "show_id"
                has = conn.execute(f"SELECT 1 FROM credits WHERE {oc}=? LIMIT 1", (item_id,)).fetchone()
                if not has:
                    self._set_credits(conn, oc, item_id, cast or [], crew or [])
            # Per-season poster backfill (TMDB) — fills only seasons the server
            # left without art.
            seasons_meta = (metadata or {}).get("seasons")
            if matched and seasons_meta and tbl == "shows":
                for s in seasons_meta:
                    sn, purl = s.get("season_number"), s.get("poster_url")
                    if sn is None or not purl:
                        continue
                    conn.execute(
                        "UPDATE seasons SET poster_url=COALESCE(NULLIF(poster_url, ''), ?) "
                        "WHERE show_id=? AND season_number=?", (purl, item_id, sn))
            conn.commit()
        finally:
            conn.close()

    def enrichment_breakdown(self, service: str) -> dict:
        if service == "omdb":
            return self._ratings_breakdown()
        kinds = _ENRICH.get(service, {})
        out = {}
        conn = self._get_connection()
        try:
            for kind, (tbl, _idc, sc, _ac) in kinds.items():
                out[kind] = {
                    "matched": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='matched'").fetchone()[0],
                    "not_found": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='not_found'").fetchone()[0],
                    "errors": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='error'").fetchone()[0],
                    "pending": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc} IS NULL").fetchone()[0],
                }
            # TMDB also cascades episode art (still) backfill from the show worker,
            # so the manager sees episode coverage. Not a queue (matched = has a
            # still; the rest are "pending" art) — kept out of the idle/pending
            # calc by the worker so it never blocks "Complete".
            if service == "tmdb":
                total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                with_still = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE still_url IS NOT NULL AND still_url<>''").fetchone()[0]
                out["episode"] = {"matched": with_still, "not_found": 0, "errors": 0,
                                  "pending": total - with_still, "coverage_only": True}
            return out
        finally:
            conn.close()

    def show_match_info(self, show_id: int) -> dict | None:
        """Title/year/tmdb_id for one show — for on-demand (lazy) art refresh."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT title, year, tmdb_id FROM shows WHERE id=?",
                               (show_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def movie_match_info(self, movie_id: int) -> dict | None:
        """Title/year/tmdb_id for one movie — for on-demand (lazy) refresh."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT title, year, tmdb_id FROM movies WHERE id=?",
                               (movie_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def library_id_for_tmdb(self, kind: str, tmdb_id, server_source=None) -> int | None:
        """The library row id for a TMDB id if it's owned on the active video
        server (``server_source``), else None. Lets the search → detail flow link
        owned titles to their real library detail — scoped per server so an item
        owned only on the inactive server doesn't read as owned here."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or tmdb_id is None:
            return None
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            if server_source:
                row = conn.execute(
                    f"SELECT id FROM {table} WHERE tmdb_id=? AND server_source=? LIMIT 1",
                    (tmdb_id, server_source)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT id FROM {table} WHERE tmdb_id=? LIMIT 1", (tmdb_id,)).fetchone()
            return row["id"] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def library_ids_for_tmdb(self, kind: str, tmdb_ids, server_source=None) -> dict:
        """{tmdb_id: library_row_id} for the owned subset of ``tmdb_ids`` on the
        active server. Batched (chunked IN) so a whole Discover rail costs one
        query per kind instead of one connection+query per item."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        out: dict = {}
        if not table:
            return out
        ids = []
        for x in (tmdb_ids or []):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):   # stay under SQLite's variable cap
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                sql = f"SELECT id, tmdb_id FROM {table} WHERE tmdb_id IN ({ph})"
                args = list(chunk)
                if server_source:
                    sql += " AND server_source=?"
                    args.append(server_source)
                for row in conn.execute(sql, args):
                    out.setdefault(row["tmdb_id"], row["id"])   # first match wins
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    def top_owned_genres(self, kind: str, server_source=None, limit: int = 6) -> list:
        """The user's most-owned genre names for movies/shows, busiest first —
        drives Discover's personalized 'Because you like …' rails."""
        if kind == "movie":
            link, owner, tbl, owned = "movie_genres", "movie_id", "movies", "t.has_file=1"
        elif kind == "show":
            link, owner, tbl, owned = "show_genres", "show_id", "shows", "1=1"   # any library show counts
        else:
            return []
        sql = (f"SELECT g.name AS name, COUNT(*) AS c FROM {link} lt "
               f"JOIN genres g ON g.id = lt.genre_id "
               f"JOIN {tbl} t ON t.id = lt.{owner} WHERE {owned}")
        args: list = []
        if server_source:
            sql += " AND t.server_source=?"
            args.append(server_source)
        sql += " GROUP BY g.name ORDER BY c DESC, g.name LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [r["name"] for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def random_owned_titles(self, limit: int = 2, server_source=None) -> list:
        """A few random owned titles (with a tmdb_id) to seed 'More like …' rails —
        up to ``limit`` movies and ``limit`` shows."""
        out = []
        conn = self._get_connection()
        try:
            for kind, tbl, alias, owned in (("movie", "movies", "m", "m.has_file=1"),
                                            ("show", "shows", "s", "1=1")):
                sql = (f"SELECT {alias}.id AS id, {alias}.tmdb_id AS tmdb_id, {alias}.title AS title "
                       f"FROM {tbl} {alias} WHERE {alias}.tmdb_id IS NOT NULL AND {owned}")
                args: list = []
                if server_source:
                    sql += f" AND {alias}.server_source=?"
                    args.append(server_source)
                sql += " ORDER BY RANDOM() LIMIT ?"
                args.append(int(limit))
                for r in conn.execute(sql, args):
                    out.append({"kind": kind, "tmdb_id": r["tmdb_id"],
                                "title": r["title"], "library_id": r["id"]})
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    def apply_ratings(self, kind: str, item_id: int, ratings: dict) -> None:
        """Store IMDb / RT / Metacritic scores (from OMDb) + mark ratings_synced.
        Ratings are dynamic, so these overwrite (unlike gap-only metadata)."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        cols = {"imdb_rating", "rt_rating", "metacritic"}
        sets, params = ["ratings_synced=1"], []
        for c, v in (ratings or {}).items():
            if c in cols and v is not None:
                sets.append(f"{c}=?")
                params.append(v)
        if not table:
            return
        params.append(item_id)
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()

    def ratings_next(self) -> dict | None:
        """Next library item that needs OMDb ratings (has an imdb_id, not synced).
        Drives the OMDb worker's background pass. Returns {kind, id, title, imdb_id}."""
        conn = self._get_connection()
        try:
            for kind, tbl in (("movie", "movies"), ("show", "shows")):
                row = conn.execute(
                    f"SELECT id, title, imdb_id FROM {tbl} "
                    "WHERE imdb_id IS NOT NULL AND imdb_id<>'' AND ratings_synced=0 "
                    "ORDER BY id LIMIT 1").fetchone()
                if row:
                    return {"kind": kind, "id": row["id"], "title": row["title"], "imdb_id": row["imdb_id"]}
            return None
        finally:
            conn.close()

    def mark_ratings_synced(self, kind: str, item_id: int) -> None:
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET ratings_synced=1 WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()

    def mark_episodes_synced(self, show_id: int) -> None:
        """Flag that the show's FULL episode list has been pulled from metadata
        (so the lazy on-view refresh doesn't re-cascade every visit)."""
        conn = self._get_connection()
        try:
            conn.execute("UPDATE shows SET episodes_synced=1 WHERE id=?", (show_id,))
            conn.commit()
        finally:
            conn.close()

    def episode_sync_next(self) -> dict | None:
        """A matched show (has tmdb_id) whose FULL episode list hasn't been pulled
        yet — for the TMDB worker's background episode-sync pass, so library cards
        show real owned/total without the user opening each one."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, title, year, tmdb_id FROM shows "
                "WHERE tmdb_id IS NOT NULL AND episodes_synced=0 ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def episode_sync_pending_count(self) -> int:
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM shows WHERE tmdb_id IS NOT NULL AND episodes_synced=0").fetchone()[0]
        finally:
            conn.close()

    def _ratings_breakdown(self) -> dict:
        """OMDb 'coverage' breakdown: matched = ratings present, pending = has an
        imdb_id but not fetched, not_found = fetched but OMDb had no rating."""
        conn = self._get_connection()
        out = {}
        try:
            for kind, tbl in (("movie", "movies"), ("show", "shows")):
                def c(where):
                    return conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {where}").fetchone()[0]
                out[kind] = {
                    "matched": c("imdb_rating IS NOT NULL"),
                    "not_found": c("ratings_synced=1 AND imdb_rating IS NULL AND imdb_id IS NOT NULL"),
                    "errors": 0,
                    "pending": c("imdb_id IS NOT NULL AND imdb_id<>'' AND ratings_synced=0"),
                }
            return out
        finally:
            conn.close()

    def show_season_numbers(self, show_id: int) -> list:
        conn = self._get_connection()
        try:
            return [r["season_number"] for r in conn.execute(
                "SELECT season_number FROM seasons WHERE show_id=? ORDER BY season_number",
                (show_id,)).fetchall()]
        finally:
            conn.close()

    def backfill_episodes(self, show_id: int, season_number: int, episodes: list,
                          season_overview: str | None = None, season_poster: str | None = None) -> int:
        """UPSERT a season's episodes from the metadata provider so the show's
        FULL episode list is represented — owned episodes (from the server) keep
        has_file=1, and episodes the server doesn't have are inserted as MISSING
        (has_file=0). Existing rows get gap-only metadata fills (never clobbered);
        the season row is created if it didn't exist (a fully-missing season).
        Returns the number of episode rows touched."""
        conn = self._get_connection()
        touched = 0
        try:
            conn.execute("INSERT OR IGNORE INTO seasons (show_id, season_number) VALUES (?, ?)",
                         (show_id, season_number))
            season_id = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                                     (show_id, season_number)).fetchone()["id"]
            if season_overview or season_poster:
                conn.execute("UPDATE seasons SET overview=COALESCE(NULLIF(overview, ''), ?), "
                             "poster_url=COALESCE(NULLIF(poster_url, ''), ?) WHERE id=?",
                             (season_overview, season_poster, season_id))
            for e in (episodes or []):
                en = e.get("episode_number")
                if en is None:
                    continue
                row = conn.execute(
                    "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                    (show_id, season_number, en)).fetchone()
                if row:
                    sets, params = [], []
                    for col in ("title", "still_url", "overview", "air_date", "rating", "runtime_minutes"):
                        if e.get(col) is None:
                            continue
                        sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
                        params.append(e[col])
                    if sets:
                        params += [row["id"]]
                        conn.execute(f"UPDATE episodes SET {', '.join(sets)} WHERE id=?", params)
                        touched += 1
                else:
                    conn.execute(
                        "INSERT INTO episodes (show_id, season_id, season_number, episode_number, title, "
                        "overview, air_date, runtime_minutes, still_url, rating, has_file) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (show_id, season_id, season_number, en, e.get("title"), e.get("overview"),
                         e.get("air_date"), e.get("runtime_minutes"), e.get("still_url"), e.get("rating")))
                    touched += 1
            conn.commit()
            return touched
        finally:
            conn.close()

    def enrichment_unmatched(self, service: str, kind: str, status: str = "not_found",
                             search=None, limit: int = 50, offset: int = 0) -> dict:
        if kind == "episode" and service == "tmdb":
            return self._episodes_missing_art(search, limit, offset)
        if service == "omdb" and kind in ("movie", "show"):
            return self._ratings_unmatched(kind, search, limit, offset)
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return {"items": [], "total": 0}
        tbl, _idc, sc, ac = spec
        where, params = [], []
        if status == "pending":
            where.append(f"{sc} IS NULL")
        elif status == "unmatched":
            where.append(f"({sc} IS NULL OR {sc} IN ('not_found','error'))")
        else:
            where.append(f"{sc}='not_found'")
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}{where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, title, year, {ac} AS last_attempted, "
                f"(poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                f"FROM {tbl}{where_sql} ORDER BY COALESCE(sort_title, title) COLLATE NOCASE "
                f"LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["has_poster"] = bool(d.get("has_poster"))
                items.append(d)
            return {"items": items, "total": total}
        finally:
            conn.close()

    def _episodes_missing_art(self, search, limit, offset) -> dict:
        """Episodes still lacking a still image (for the manager's Episodes view).
        Read-only: episode art is backfilled as a cascade, not a retry queue."""
        where = ["(e.still_url IS NULL OR e.still_url='')"]
        params: list = []
        if search:
            where.append("(e.title LIKE ? COLLATE NOCASE OR sh.title LIKE ? COLLATE NOCASE)")
            params += ["%" + search + "%", "%" + search + "%"]
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM episodes e JOIN shows sh ON sh.id=e.show_id{where_sql}",
                params).fetchone()[0]
            rows = conn.execute(
                "SELECT e.id, sh.title || ' · S' || e.season_number || 'E' || e.episode_number "
                "|| COALESCE(' · ' || e.title, '') AS title, e.air_date AS year, "
                "0 AS has_poster, NULL AS last_attempted "
                f"FROM episodes e JOIN shows sh ON sh.id=e.show_id{where_sql} "
                "ORDER BY sh.sort_title, e.season_number, e.episode_number LIMIT ? OFFSET ?",
                params + [limit, offset]).fetchall()
            return {"items": [dict(r) | {"has_poster": False} for r in rows], "total": total}
        finally:
            conn.close()

    def _ratings_unmatched(self, kind: str, search, limit: int, offset: int) -> dict:
        tbl = {"movie": "movies", "show": "shows"}[kind]
        where = ["imdb_rating IS NULL", "imdb_id IS NOT NULL", "imdb_id<>''"]
        params: list = []
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}{where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, title, year, (poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                f"FROM {tbl}{where_sql} ORDER BY COALESCE(sort_title, title) COLLATE NOCASE "
                "LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            return {"items": [dict(r) | {"has_poster": bool(r["has_poster"])} for r in rows], "total": total}
        finally:
            conn.close()

    def enrichment_retry(self, service: str, kind: str, scope: str = "failed", item_id=None) -> int:
        """Re-queue items by resetting status/last_attempted to NULL."""
        if service == "omdb":
            tbl = {"movie": "movies", "show": "shows"}.get(kind)
            if not tbl:
                return 0
            conn = self._get_connection()
            try:
                if scope == "item" and item_id is not None:
                    cur = conn.execute(f"UPDATE {tbl} SET ratings_synced=0 WHERE id=?", (item_id,))
                else:
                    cur = conn.execute(f"UPDATE {tbl} SET ratings_synced=0 WHERE imdb_rating IS NULL")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return 0
        tbl, _idc, sc, ac = spec
        conn = self._get_connection()
        try:
            if scope == "item" and item_id is not None:
                cur = conn.execute(f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE id=?", (item_id,))
            else:
                cur = conn.execute(
                    f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE {sc} IN ('not_found','error')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def requeue_shows_for_airtime(self) -> int:
        """One-time backfill: re-queue TVDB enrichment for shows that have a
        tvdb_id but no air time yet, so the worker re-fetches `airsTime`. Only
        touches shows missing the time — idempotent, fills in the background."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE shows SET tvdb_match_status=NULL, tvdb_last_attempted=NULL "
                "WHERE tvdb_id IS NOT NULL AND (airs_time IS NULL OR airs_time='')")
            conn.commit()
            return cur.rowcount
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
    def dashboard_stats(self, server_source=None) -> dict:
        """Live counts for the video dashboard, straight from video.db. Library
        counts are scoped to the active video server (``server_source``) so Plex
        and Jellyfin never commingle.

        Shape is stable so the frontend can map it directly; with an empty
        database every number is a real 0 (not a stub).
        """
        # server_source given → scope to that server; None → all servers.
        mw = " WHERE server_source=?" if server_source else ""
        sw = " WHERE s.server_source=?" if server_source else ""
        sv = (server_source,) if server_source else ()
        size_sql = ("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files mf "
                    "WHERE mf.movie_id IN (SELECT id FROM movies WHERE server_source=?) "
                    "OR mf.episode_id IN (SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id "
                    "WHERE s.server_source=?)") if server_source else \
                   "SELECT COALESCE(SUM(size_bytes), 0) FROM media_files"
        conn = self._get_connection()
        try:
            def scalar(sql: str, params=()):
                return conn.execute(sql, params).fetchone()[0]

            return {
                "library": {
                    "movies": scalar("SELECT COUNT(*) FROM movies" + mw, sv),
                    "shows": scalar("SELECT COUNT(*) FROM shows" + mw, sv),
                    "episodes": scalar(
                        "SELECT COUNT(*) FROM episodes e JOIN shows s ON s.id=e.show_id" + sw, sv),
                    "size_bytes": scalar(size_sql, (sv + sv) if server_source else ()),
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
                # Curated watchlist (explicit follows + actively-airing library
                # shows), NOT the old monitored-based v_watchlist view.
                "watchlist": self.watchlist_counts(server_source=server_source)["total"],
                # Wishlist is intentionally 0 for now: the old v_wishlist view
                # auto-listed EVERY missing movie/episode (monitored defaults to
                # 1), which isn't the intended curated wishlist. Repoint this at
                # the curated source once "add to wishlist" population lands.
                "wishlist": 0,
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

    @staticmethod
    def _set_genres(conn, link_table: str, owner_col: str, owner_id: int, names) -> None:
        """Replace the genre links for one owner (normalised; dedup names in the
        shared genres table). owner_col/link_table are internal, never user input."""
        conn.execute(f"DELETE FROM {link_table} WHERE {owner_col}=?", (owner_id,))
        for raw in (names or []):
            name = (raw or "").strip()
            if not name:
                continue
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (name,))
            gid = conn.execute("SELECT id FROM genres WHERE name=? COLLATE NOCASE", (name,)).fetchone()["id"]
            conn.execute(f"INSERT OR IGNORE INTO {link_table} ({owner_col}, genre_id) VALUES (?, ?)",
                         (owner_id, gid))

    @staticmethod
    def _resilient_upsert(conn, table: str, base: dict, id_cols: dict) -> None:
        """INSERT…ON CONFLICT(server_source, server_id) for a movie/show row.

        Resilient to a LEGACY UNIQUE on tmdb_id/tvdb_id/imdb_id (old DBs created
        before those were made non-unique — SQLite can't drop an inline UNIQUE):
        on IntegrityError we retry WITHOUT the id columns, so the row is still
        stored (same film in >1 library) instead of being dropped by the scan.
        ``base`` holds the always-written cols; ``id_cols`` the droppable ids."""
        def run(include_ids):
            cols = list(base.keys()) + (list(id_cols.keys()) if include_ids else [])
            vals = list(base.values()) + (list(id_cols.values()) if include_ids else [])
            updates = [c for c in cols if c not in ("server_source", "server_id")]
            set_clause = ", ".join(f"{c}=excluded.{c}" for c in updates) + ", updated_at=CURRENT_TIMESTAMP"
            sql = (f"INSERT INTO {table} ({', '.join(cols)}, updated_at) "
                   f"VALUES ({', '.join(['?'] * len(cols))}, CURRENT_TIMESTAMP) "
                   f"ON CONFLICT(server_source, server_id) DO UPDATE SET {set_clause}")
            conn.execute(sql, vals)
        try:
            run(True)
        except sqlite3.IntegrityError:
            conn.rollback()                 # legacy UNIQUE on an id — keep the row, drop the id
            run(False)

    @staticmethod
    def _set_credits(conn, owner_col: str, owner_id: int, cast, crew) -> None:
        """Replace the cast+crew for one owner (deduped people in the shared
        people table). owner_col is internal ('movie_id'|'show_id')."""
        conn.execute(f"DELETE FROM credits WHERE {owner_col}=?", (owner_id,))

        def person_id(p):
            tid = p.get("tmdb_id")
            if tid is not None:
                conn.execute("INSERT OR IGNORE INTO people (name, tmdb_id, photo_url) VALUES (?, ?, ?)",
                             (p["name"], tid, p.get("photo_url")))
                row = conn.execute("SELECT id FROM people WHERE tmdb_id=?", (tid,)).fetchone()
            else:
                conn.execute("INSERT INTO people (name, photo_url) VALUES (?, ?)",
                             (p["name"], p.get("photo_url")))
                row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
            pid = row["id"] if row else None
            if pid and p.get("photo_url"):
                conn.execute("UPDATE people SET photo_url=COALESCE(NULLIF(photo_url, ''), ?) WHERE id=?",
                             (p["photo_url"], pid))
            return pid

        def add(group, department, job_default):
            for i, c in enumerate(group or []):
                if not c.get("name"):
                    continue
                pid = person_id(c)
                if not pid:
                    continue
                conn.execute(
                    f"INSERT INTO credits (person_id, {owner_col}, department, job, character, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (pid, owner_id, department, c.get("job") or job_default, c.get("character"), i))
        add(cast, "cast", "Actor")
        add(crew, "crew", None)

    @staticmethod
    def _credits_for(conn, owner_col: str, owner_id: int, cast_limit: int = 18) -> dict:
        rows = conn.execute(
            "SELECT p.name, p.photo_url, p.tmdb_id, c.department, c.job, c.character "
            f"FROM credits c JOIN people p ON p.id = c.person_id WHERE c.{owner_col}=? "
            "ORDER BY c.department, c.sort_order", (owner_id,)).fetchall()
        cast = [{"name": r["name"], "character": r["character"], "photo": r["photo_url"],
                 "tmdb_id": r["tmdb_id"]}
                for r in rows if r["department"] == "cast"][:cast_limit]
        crew = [{"name": r["name"], "job": r["job"], "tmdb_id": r["tmdb_id"]}
                for r in rows if r["department"] == "crew"]
        return {"cast": cast, "crew": crew}

    def upsert_movie(self, server_source: str, item: dict) -> int:
        """Insert/update one movie (keyed on server id) and its file. Returns row id."""
        conn = self._get_connection()
        try:
            self._resilient_upsert(conn, "movies", {
                "server_source": server_source, "server_id": item["server_id"],
                "title": item.get("title"), "sort_title": _sort_title(item.get("title")),
                "year": item.get("year"), "overview": item.get("overview"),
                "runtime_minutes": item.get("runtime_minutes"), "content_rating": item.get("content_rating"),
                "studio": item.get("studio"), "tagline": item.get("tagline"),
                "rating": item.get("rating"), "rating_critic": item.get("rating_critic"),
                "poster_url": item.get("poster_url"), "has_file": 1 if item.get("file") else 0,
            }, {"tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")})
            movie_id = conn.execute(
                "SELECT id FROM movies WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            self._set_media_file(conn, "movie_id", movie_id, item.get("file"))
            self._set_genres(conn, "movie_genres", "movie_id", movie_id, item.get("genres"))
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
            self._resilient_upsert(conn, "shows", {
                "server_source": server_source, "server_id": item["server_id"],
                "title": item.get("title"), "sort_title": _sort_title(item.get("title")),
                "year": item.get("year"), "overview": item.get("overview"),
                "status": item.get("status"), "network": item.get("network"),
                "runtime_minutes": item.get("runtime_minutes"), "content_rating": item.get("content_rating"),
                "tagline": item.get("tagline"), "rating": item.get("rating"),
                "first_air_date": item.get("first_air_date"), "last_air_date": item.get("last_air_date"),
                "poster_url": item.get("poster_url"),
            }, {"tvdb_id": item.get("tvdb_id"), "tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")})
            show_id = conn.execute(
                "SELECT id FROM shows WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            self._set_genres(conn, "show_genres", "show_id", show_id, item.get("genres"))

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
                        "runtime_minutes, still_url, rating, tvdb_id, has_file) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(show_id, season_number, episode_number) DO UPDATE SET "
                        "season_id=excluded.season_id, server_source=excluded.server_source, "
                        "server_id=excluded.server_id, title=excluded.title, "
                        "overview=excluded.overview, air_date=excluded.air_date, "
                        "runtime_minutes=excluded.runtime_minutes, still_url=excluded.still_url, "
                        "rating=excluded.rating, tvdb_id=excluded.tvdb_id, has_file=excluded.has_file",
                        (show_id, season_id, server_source, ep.get("server_id"), snum, enum,
                         ep.get("title"), ep.get("overview"), ep.get("air_date"),
                         ep.get("runtime_minutes"), ep.get("still_url"), ep.get("rating"),
                         ep.get("tvdb_id"), 1 if ep.get("file") else 0),
                    )
                    ep_id = conn.execute(
                        "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, snum, enum),
                    ).fetchone()["id"]
                    self._set_media_file(conn, "episode_id", ep_id, ep.get("file"))

            # Prune only SERVER-originated rows that vanished (server_id set) — the
            # full episode/season list now includes enrichment-added MISSING items
            # (server_id NULL), which the scan must never remove.
            for row in conn.execute(
                "SELECT season_number, episode_number FROM episodes "
                "WHERE show_id=? AND server_id IS NOT NULL", (show_id,)
            ).fetchall():
                if (row["season_number"], row["episode_number"]) not in seen_eps:
                    conn.execute(
                        "DELETE FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, row["season_number"], row["episode_number"]),
                    )
            for row in conn.execute(
                "SELECT season_number FROM seasons WHERE show_id=? AND server_id IS NOT NULL", (show_id,)
            ).fetchall():
                if row["season_number"] not in seen_seasons:
                    conn.execute("DELETE FROM seasons WHERE show_id=? AND season_number=?",
                                 (show_id, row["season_number"]))
            conn.commit()
            return show_id
        finally:
            conn.close()

    def server_ids(self, table: str, server_source: str) -> set:
        """All server_ids already stored for a server (for incremental early-stop)."""
        if table not in ("movies", "shows"):
            return set()
        conn = self._get_connection()
        try:
            return {str(r[0]) for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=?", (server_source,)).fetchall()}
        finally:
            conn.close()

    def table_count(self, table: str) -> int:
        if table not in ("movies", "shows", "episodes"):
            return 0
        conn = self._get_connection()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def prune_missing(self, table: str, server_source: str, seen_ids) -> int:
        """Delete top-level rows for a server that the scan no longer saw.
        ``table`` is internal ('movies'|'shows'); cascades clean children.

        Safety (mirrors music's deep scan): if removal would wipe >50% of a
        >100-row library, assume a partial server failure and skip it."""
        if table not in ("movies", "shows"):
            raise ValueError(f"prune_missing: unexpected table {table!r}")
        seen = {str(s) for s in seen_ids}
        conn = self._get_connection()
        try:
            existing = [r["server_id"] for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=?", (server_source,)
            ).fetchall()]
            stale = [sid for sid in existing if str(sid) not in seen]
            if len(stale) > len(existing) * 0.5 and len(existing) > 100:
                logger.warning(
                    "Video deep scan: %d/%d %s stale (>50%%) — skipping removal (likely a "
                    "partial server response)", len(stale), len(existing), table)
                return 0
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

    def calendar_upcoming(self, start_date: str, end_date: str, server_source=None) -> list[dict]:
        """Episodes airing in [start_date, end_date] (ISO) for shows on the active
        video server (``server_source``) — the Calendar feed. Scoped to one server
        so Plex and Jellyfin never commingle. Each row carries owned/missing
        (has_file), a still flag, and show network/airs_time/year for the card."""
        # server_source given → that server only; None → all owned shows.
        if server_source:
            srv_where, pre = "s.server_source = ?", [server_source]
        else:
            srv_where, pre = "s.server_source IS NOT NULL", []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, "
                "e.overview, e.air_date, e.runtime_minutes, e.rating, e.has_file, e.monitored, "
                "(e.still_url IS NOT NULL AND e.still_url<>'') AS has_still, "
                "s.title AS show_title, s.network, s.airs_time, s.year AS show_year, s.status AS show_status, "
                "(s.poster_url IS NOT NULL AND s.poster_url<>'') AS show_has_poster, "
                "(s.backdrop_url IS NOT NULL AND s.backdrop_url<>'') AS show_has_backdrop "
                "FROM episodes e JOIN shows s ON s.id = e.show_id "
                "WHERE " + srv_where + " "
                "AND e.air_date IS NOT NULL AND e.air_date >= ? AND e.air_date <= ? "
                "ORDER BY e.air_date, COALESCE(s.sort_title, s.title) COLLATE NOCASE, "
                "e.season_number, e.episode_number",
                pre + [start_date, end_date]).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_poster_ref(self, kind: str, item_id: int) -> dict | None:
        """Server source/id/poster path for one movie or show, for the poster proxy."""
        return self.get_art_ref(kind, item_id, "poster")

    def get_art_ref(self, kind: str, item_id: int, art: str = "poster") -> dict | None:
        """Server source/id + artwork path for one movie/show/season, for the image
        proxy. ``art`` is 'poster' or 'backdrop'. Returns the path under
        'poster_url' so the proxy is artwork-agnostic."""
        conn = self._get_connection()
        try:
            if kind == "season":
                if art != "poster":
                    return None
                # Seasons don't carry server_source — inherit the parent show's.
                row = conn.execute(
                    "SELECT sh.server_source, se.server_id, se.poster_url "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (item_id,)).fetchone()
                return dict(row) if row else None
            if kind == "episode":
                if art != "poster":
                    return None
                # Episode still; episodes carry their own server_source + the still path.
                row = conn.execute(
                    "SELECT server_source, server_id, still_url AS poster_url "
                    "FROM episodes WHERE id=?", (item_id,)).fetchone()
                return dict(row) if row else None
            table = {"movie": "movies", "show": "shows"}.get(kind)
            col = {"poster": "poster_url", "backdrop": "backdrop_url"}.get(art)
            if not table or not col:
                return None
            row = conn.execute(
                f"SELECT server_source, server_id, {col} AS poster_url FROM {table} WHERE id=?",
                (item_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── detail payloads (drill-in pages) ──────────────────────────────────────
    @staticmethod
    def _genres_for(conn, link_table: str, owner_col: str, owner_id: int) -> list:
        rows = conn.execute(
            f"SELECT g.name FROM {link_table} lt JOIN genres g ON g.id = lt.genre_id "
            f"WHERE lt.{owner_col}=? ORDER BY g.name", (owner_id,)).fetchall()
        return [r["name"] for r in rows]

    def show_detail(self, show_id: int) -> dict | None:
        """Full TV-show detail: the show + its seasons → episodes tree, with
        owned/total roll-ups. Drives the (isolated) video show-detail page."""
        conn = self._get_connection()
        try:
            show = conn.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
            if not show:
                return None
            genres = self._genres_for(conn, "show_genres", "show_id", show_id)
            credits = self._credits_for(conn, "show_id", show_id)
            seasons = conn.execute(
                "SELECT id, season_number, title, overview, "
                "(poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                "FROM seasons WHERE show_id=? ORDER BY season_number", (show_id,)).fetchall()
            eps = conn.execute(
                "SELECT id, season_number, episode_number, title, overview, air_date, "
                "runtime_minutes, rating, monitored, has_file, "
                "(still_url IS NOT NULL AND still_url<>'') AS has_still FROM episodes WHERE show_id=? "
                "ORDER BY season_number, episode_number", (show_id,)).fetchall()
        finally:
            conn.close()

        by_season: dict = {}
        for e in eps:
            by_season.setdefault(e["season_number"], []).append({
                "id": e["id"], "episode_number": e["episode_number"],
                "title": e["title"], "overview": e["overview"], "air_date": e["air_date"],
                "runtime_minutes": e["runtime_minutes"], "rating": e["rating"],
                "has_still": bool(e["has_still"]),
                "monitored": bool(e["monitored"]), "owned": bool(e["has_file"]),
            })

        # Seasons declared in the seasons table, plus any season numbers that only
        # exist via episodes (defensive — a show with episodes but no season row).
        season_nums = [s["season_number"] for s in seasons]
        season_meta = {s["season_number"]: s for s in seasons}
        for num in by_season:
            if num not in season_meta:
                season_nums.append(num)
        out_seasons = []
        for num in sorted(set(season_nums)):
            ep_list = by_season.get(num, [])
            owned = sum(1 for e in ep_list if e["owned"])
            meta = season_meta.get(num)
            out_seasons.append({
                "id": meta["id"] if meta else None,    # needed for the season poster proxy
                "season_number": num,
                "title": (meta["title"] if meta else None) or (
                    "Specials" if num == 0 else "Season %d" % num),
                "overview": meta["overview"] if meta else None,
                "has_poster": bool(meta["has_poster"]) if meta else False,
                "episode_total": len(ep_list),
                "episode_owned": owned,
                "episodes": ep_list,
            })

        total = len(eps)
        owned_total = sum(1 for e in eps if e["has_file"])
        return {
            "kind": "show", "id": show["id"], "title": show["title"], "year": show["year"],
            "overview": show["overview"], "status": show["status"], "network": show["network"],
            "content_rating": show["content_rating"], "runtime_minutes": show["runtime_minutes"],
            "tagline": show["tagline"], "rating": show["rating"],
            "first_air_date": show["first_air_date"], "last_air_date": show["last_air_date"],
            "imdb_rating": show["imdb_rating"], "rt_rating": show["rt_rating"],
            "metacritic": show["metacritic"],
            "genres": genres, "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": show["tmdb_id"], "tvdb_id": show["tvdb_id"], "imdb_id": show["imdb_id"],
            "has_poster": bool(show["poster_url"]), "has_backdrop": bool(show["backdrop_url"]),
            "logo": show["logo_url"],
            "episodes_synced": bool(show["episodes_synced"]),
            "monitored": bool(show["monitored"]),
            "season_count": len(out_seasons),
            "episode_total": total, "episode_owned": owned_total,
            "seasons": out_seasons,
        }

    def set_monitored(self, kind: str, item_id: int, monitored: bool) -> bool:
        """Toggle the 'follow/watchlist' flag on a movie or show. Returns True if a
        row was updated."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET monitored=? WHERE id=?",
                               (1 if monitored else 0, item_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── User watchlist (curated follow-list: shows + people) ──────────────────
    # Mirrors the music watchlist_artists model: an explicit follow-list that may
    # include shows/people not in the library yet. Keyed on (kind, tmdb_id). The
    # monitoring/discovery engine is a later phase — these just manage membership.
    # An actively-airing library show (status present and not finished) is on the
    # watchlist BY DEFAULT — owning a still-running show means you want its new
    # episodes. The `state` rows store only explicit user decisions; this default
    # is computed at read time so it always tracks the library + a show's status.
    _ACTIVE_SHOW_SQL = ("status IS NOT NULL AND TRIM(status) <> '' "
                        "AND LOWER(status) NOT IN ('ended', 'canceled', 'cancelled', 'completed')")

    def add_to_watchlist(self, kind: str, tmdb_id: int, title: str,
                         poster_url: str | None = None, library_id: int | None = None) -> bool:
        """Explicitly follow a show/person (state='follow'). Idempotent upsert on
        (kind, tmdb_id) — re-adding refreshes title/poster/library_id and clears
        any 'mute' tombstone. Returns True on success."""
        if kind not in ("show", "person") or not tmdb_id or not title:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, library_id, state)
                   VALUES (?, ?, ?, ?, ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       library_id=COALESCE(excluded.library_id, video_watchlist.library_id)""",
                (kind, int(tmdb_id), title, poster_url, library_id))
            conn.commit()
            return True
        except Exception:
            logger.exception("add_to_watchlist failed (%s %s)", kind, tmdb_id)
            return False
        finally:
            conn.close()

    def remove_from_watchlist(self, kind: str, tmdb_id: int) -> bool:
        """Un-follow. Stored as a 'mute' tombstone (not a delete) so an
        actively-airing library show — watched by default — is not silently
        re-added. Returns True."""
        if kind not in ("show", "person") or not tmdb_id:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                   VALUES (?, ?, '', 'mute')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET state='mute'""",
                (kind, int(tmdb_id)))
            conn.commit()
            return True
        finally:
            conn.close()

    # owned/total episode counts — joined off s.id in both queries below.
    _EPS_COLS = ("(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id) AS episode_count, "
                 "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) AS owned_count")

    def _effective_shows(self, conn, server_source) -> list[dict]:
        """Explicit show follows ∪ actively-airing library shows (not muted),
        each carrying status + owned/total episode counts for the card chrome."""
        out, seen = [], set()
        for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.poster_url, w.library_id, w.date_added, s.status, "
                + self._EPS_COLS +
                " FROM video_watchlist w LEFT JOIN shows s ON s.id = w.library_id "
                "WHERE w.kind='show' AND w.state='follow' ORDER BY w.date_added DESC, w.id DESC"):
            d = dict(r); d["kind"] = "show"; out.append(d); seen.add(r["tmdb_id"])
        muted = {r["tmdb_id"] for r in conn.execute(
            "SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute'")}
        sql = ("SELECT s.tmdb_id, s.title, s.id AS library_id, s.status, " + self._EPS_COLS +
               " FROM shows s WHERE s.tmdb_id IS NOT NULL AND " + self._ACTIVE_SHOW_SQL)
        args: list = []
        if server_source:
            sql += " AND s.server_source = ?"; args.append(server_source)
        sql += " ORDER BY COALESCE(s.sort_title, s.title) COLLATE NOCASE"
        for r in conn.execute(sql, args):
            tid = r["tmdb_id"]
            if tid in seen or tid in muted:
                continue
            seen.add(tid)
            out.append({"kind": "show", "tmdb_id": tid, "title": r["title"],
                        "poster_url": "/api/video/poster/show/%d" % r["library_id"],
                        "library_id": r["library_id"], "status": r["status"],
                        "episode_count": r["episode_count"], "owned_count": r["owned_count"],
                        "date_added": None, "auto": True})
        return out

    def list_watchlist(self, kind: str | None = None, server_source=None) -> list[dict]:
        """Effective watchlist. Shows include the airing-library default; people
        are explicit follows only."""
        conn = self._get_connection()
        try:
            people = []
            if kind in (None, "person"):
                for r in conn.execute(
                        "SELECT tmdb_id, title, poster_url, library_id, date_added FROM video_watchlist "
                        "WHERE kind='person' AND state='follow' ORDER BY date_added DESC, id DESC"):
                    d = dict(r); d["kind"] = "person"; people.append(d)
            shows = self._effective_shows(conn, server_source) if kind in (None, "show") else []
            if kind == "person":
                return people
            if kind == "show":
                return shows
            return shows + people
        finally:
            conn.close()

    def watchlist_state(self, kind: str, tmdb_ids, server_source=None) -> dict:
        """{tmdb_id: True} for ids that are watched — explicit follow OR (for
        shows) an actively-airing library show that isn't muted. Hydrates buttons."""
        out: dict = {}
        ids = [int(x) for x in (tmdb_ids or []) if x]
        if kind not in ("show", "person") or not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):  # stay under SQLite's variable cap
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id FROM video_watchlist WHERE kind=? AND state='follow' "
                        f"AND tmdb_id IN ({ph})", [kind] + chunk):
                    out[r["tmdb_id"]] = True
                if kind == "show":
                    muted = {r["tmdb_id"] for r in conn.execute(
                        f"SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute' "
                        f"AND tmdb_id IN ({ph})", chunk)}
                    ssql = f"SELECT tmdb_id FROM shows WHERE tmdb_id IN ({ph}) AND " + self._ACTIVE_SHOW_SQL
                    sargs = list(chunk)
                    if server_source:
                        ssql += " AND server_source = ?"; sargs.append(server_source)
                    for r in conn.execute(ssql, sargs):
                        if r["tmdb_id"] not in muted:
                            out[r["tmdb_id"]] = True
            return out
        finally:
            conn.close()

    def watchlist_counts(self, server_source=None) -> dict:
        """{'show': n, 'person': n, 'total': n} over the EFFECTIVE watchlist."""
        shows = self.list_watchlist("show", server_source=server_source)
        people = self.list_watchlist("person")
        return {"show": len(shows), "person": len(people), "total": len(shows) + len(people)}

    def query_watchlist(self, kind: str, *, search=None, sort="default", page=1, limit=60,
                        server_source=None) -> dict:
        """One searched/sorted/paged slice of the effective watchlist for a kind —
        mirrors query_library's {items, pagination} shape so the page can paginate
        like the library. The effective list is bounded (follows + airing library
        shows), so it's computed then filtered/sorted/sliced rather than via SQL."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        items = self.list_watchlist(kind, server_source=server_source) if kind in ("show", "person") else []
        s = (search or "").strip().lower()
        if s:
            items = [it for it in items if s in (it.get("title") or "").lower()]
        if sort == "title":
            items.sort(key=lambda it: (it.get("title") or "").lower())
        elif sort == "added":   # explicit follows (have a date) newest-first; airing defaults last
            items.sort(key=lambda it: (it.get("date_added") or ""), reverse=True)
        # "default": keep the natural effective order (follows first, then airing A–Z)
        total = len(items)
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        start = (page - 1) * limit
        return {"items": items[start:start + limit], "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    def movie_detail(self, movie_id: int) -> dict | None:
        """Full movie detail: the movie + owned/file info. Drives the (isolated)
        video movie-detail page."""
        conn = self._get_connection()
        try:
            m = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
            if not m:
                return None
            genres = self._genres_for(conn, "movie_genres", "movie_id", movie_id)
            credits = self._credits_for(conn, "movie_id", movie_id)
            files = conn.execute(
                "SELECT resolution, quality, video_codec, audio_codec, release_source, size_bytes "
                "FROM media_files WHERE movie_id=? ORDER BY size_bytes DESC",
                (movie_id,)).fetchall()
        finally:
            conn.close()
        return {
            "kind": "movie", "id": m["id"], "title": m["title"], "year": m["year"],
            "overview": m["overview"], "status": m["status"], "studio": m["studio"],
            "release_date": m["release_date"], "runtime_minutes": m["runtime_minutes"],
            "content_rating": m["content_rating"], "tagline": m["tagline"],
            "rating": m["rating"], "rating_critic": m["rating_critic"], "genres": genres,
            "imdb_rating": m["imdb_rating"], "rt_rating": m["rt_rating"], "metacritic": m["metacritic"],
            "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": m["tmdb_id"], "imdb_id": m["imdb_id"],
            "has_poster": bool(m["poster_url"]), "has_backdrop": bool(m["backdrop_url"]),
            "logo": m["logo_url"],
            "owned": bool(m["has_file"]), "monitored": bool(m["monitored"]),
            "file": (dict(files[0]) if files else None),       # best version (compat)
            "files": [dict(x) for x in files],                 # all versions/editions
        }

    # ── paged/filtered/sorted library query (server-side, like music) ─────────
    def query_library(self, kind: str, *, search=None, letter=None, sort="title",
                      status="all", page=1, limit=75, server_source=None) -> dict:
        """One page of movies/shows with search + A–Z + sort + owned/wanted
        filtering done in SQL. Scoped to ``server_source`` (the active video
        server) so Plex and Jellyfin libraries never commingle — mirrors how the
        music side keeps servers separate. Returns {items, pagination:{...}}."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(500, int(limit or 75)))
        except (TypeError, ValueError):
            page, limit = 1, 75
        is_shows = kind == "shows"
        alias = "s" if is_shows else "m"
        tbl = "shows" if is_shows else "movies"

        where, params = [], []
        if server_source:
            where.append(f"{alias}.server_source = ?")
            params.append(server_source)
        if search:
            where.append(f"{alias}.title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        if letter and letter != "all":
            col = f"COALESCE({alias}.sort_title, {alias}.title)"
            if letter == "#":
                where.append(f"substr(UPPER({col}), 1, 1) NOT BETWEEN 'A' AND 'Z'")
            else:
                where.append(f"{col} LIKE ? COLLATE NOCASE")
                params.append(letter + "%")
        if not is_shows:
            if status == "owned":
                where.append("m.has_file = 1")
            elif status == "wanted":
                where.append("m.has_file = 0")
        else:
            if status == "owned":
                where.append("EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            elif status == "wanted":
                where.append("NOT EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        title_key = f"COALESCE({alias}.sort_title, {alias}.title) COLLATE NOCASE ASC"
        order_sql = {
            "title": title_key,
            "year": f"{alias}.year DESC, " + title_key,
            "added": f"{alias}.added_at DESC",
        }.get(sort, title_key)

        if is_shows:
            select = ("SELECT s.id, s.title, s.year, s.tmdb_id, s.status, "
                      "(s.poster_url IS NOT NULL AND s.poster_url <> '') AS has_poster, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id) AS episode_count, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) AS owned_count "
                      "FROM shows s")
        else:
            select = ("SELECT m.id, m.title, m.year, m.has_file, "
                      "(m.poster_url IS NOT NULL AND m.poster_url <> '') AS has_poster, "
                      "(SELECT mf.resolution FROM media_files mf WHERE mf.movie_id=m.id LIMIT 1) AS resolution "
                      "FROM movies m")

        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl} {alias}{where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"{select}{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                params + [limit, (page - 1) * limit]).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["has_poster"] = bool(d.get("has_poster"))
                if not is_shows:
                    d["has_file"] = bool(d.get("has_file"))
                items.append(d)
            total_pages = max(1, (total + limit - 1) // limit)
            return {"items": items, "pagination": {
                "page": page, "total_pages": total_pages, "total_count": total,
                "has_prev": page > 1, "has_next": page < total_pages}}
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
