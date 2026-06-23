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

import hashlib
import json
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
SCHEMA_VERSION = 18

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
               "tmdb_collection_id", "tmdb_collection_name",
               "imdb_id", "tmdb_id"},
    "shows": {"overview", "backdrop_url", "logo_url", "status", "network", "content_rating",
              "tagline", "rating", "first_air_date", "last_air_date", "airs_time",
              "imdb_id", "tmdb_id", "tvdb_id"},
}

# Backfill-worker plumbing (parallels _ENRICH, but for services that enrich an
# already-identified library item BY id rather than matching a title). Maps a
# service + kind to (table, status_col, attempted_col, where_has_required_id).
_BACKFILL = {
    "fanart": {
        "movie": ("movies", "fanart_status", "fanart_attempted",
                  "(tmdb_id IS NOT NULL OR imdb_id IS NOT NULL)"),
        "show": ("shows", "fanart_status", "fanart_attempted", "tvdb_id IS NOT NULL"),
    },
    "opensubtitles": {
        "movie": ("movies", "subs_status", "subs_attempted",
                  "(imdb_id IS NOT NULL OR tmdb_id IS NOT NULL)"),
        "show": ("shows", "subs_status", "subs_attempted",
                 "(imdb_id IS NOT NULL OR tmdb_id IS NOT NULL)"),
    },
    "trakt": {
        "movie": ("movies", "trakt_status", "trakt_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "trakt_status", "trakt_attempted", "imdb_id IS NOT NULL"),
    },
    "tvmaze": {  # TV-only: TVmaze has no movie database
        "show": ("shows", "tvmaze_status", "tvmaze_attempted",
                 "(imdb_id IS NOT NULL OR tvdb_id IS NOT NULL)"),
    },
    "anilist": {  # anime-only, matched by title (shows only)
        "show": ("shows", "anilist_status", "anilist_attempted",
                 "title IS NOT NULL AND title <> ''"),
    },
    "wikidata": {  # official website lookup by imdb id (movies + shows)
        "movie": ("movies", "wikidata_status", "wikidata_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "wikidata_status", "wikidata_attempted", "imdb_id IS NOT NULL"),
    },
}
# Columns each backfill service may gap-fill (whitelist; never clobbers server data).
# A worker visits each item once (status IS NULL), so these NULL columns are written
# on that single pass.
_BACKFILL_COLS = {
    "fanart": {"logo_url", "backdrop_url", "poster_url", "clearart_url", "banner_url"},
    "opensubtitles": {"subtitle_langs"},
    "trakt": {"trakt_rating", "trakt_votes"},
    "tvmaze": {"tvmaze_rating"},
    "anilist": {"anilist_score"},
    "wikidata": {"wikidata_url"},
}

# Columns ensured on existing DBs (ALTER TABLE ADD COLUMN; idempotent).
_COLUMN_MIGRATIONS = [
    # video_downloads — media identity for the Downloads page cards (poster + open).
    ("video_downloads", "media_id", "TEXT"),
    ("video_downloads", "media_source", "TEXT"),
    ("video_downloads", "year", "INTEGER"),
    ("video_downloads", "poster_url", "TEXT"),
    # video_downloads — auto-retry state (remaining candidates + requery context).
    ("video_downloads", "candidates", "TEXT"),
    ("video_downloads", "search_ctx", "TEXT"),
    ("video_downloads", "tried_queries", "TEXT"),
    ("video_downloads", "tried_files", "TEXT"),
    ("video_downloads", "attempts", "INTEGER"),
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
    ("movies", "tmdb_collection_id", "INTEGER"),
    ("movies", "tmdb_collection_name", "TEXT"),
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
    ("video_wishlist", "still_url", "TEXT"),   # episode still thumbnail (captured at add time)
    ("video_wishlist", "season_poster_url", "TEXT"),   # the episode's season poster
    ("video_wishlist", "episode_overview", "TEXT"),    # episode synopsis
    # generic source bridge (YouTube channels/videos ride the existing tables)
    ("video_watchlist", "source", "TEXT NOT NULL DEFAULT 'tmdb'"),
    ("video_watchlist", "source_id", "TEXT"),
    ("video_wishlist", "source", "TEXT NOT NULL DEFAULT 'tmdb'"),
    ("video_wishlist", "source_id", "TEXT"),
    ("video_wishlist", "parent_source_id", "TEXT"),   # owning channel youtube id (video rows)
    # which source produced a channel's dates — NULL on legacy (pre-InnerTube) rows
    # so they re-enrich once and upgrade to the full InnerTube catalog.
    ("youtube_channel_enrichment", "method", "TEXT"),
    # per-video duration + approximate view count on the remembered catalog
    ("youtube_channel_videos", "duration", "TEXT"),
    ("youtube_channel_videos", "view_count", "INTEGER"),
    # fanart.tv artwork backfill (gap-fill only; logo/backdrop/poster live already)
    ("movies", "clearart_url", "TEXT"), ("movies", "banner_url", "TEXT"),
    ("movies", "fanart_status", "TEXT"), ("movies", "fanart_attempted", "TEXT"),
    ("shows", "clearart_url", "TEXT"), ("shows", "banner_url", "TEXT"),
    ("shows", "fanart_status", "TEXT"), ("shows", "fanart_attempted", "TEXT"),
    # OpenSubtitles availability backfill (which languages exist for a title)
    ("movies", "subtitle_langs", "TEXT"),            # JSON array of language codes
    ("movies", "subs_status", "TEXT"), ("movies", "subs_attempted", "TEXT"),
    ("shows", "subtitle_langs", "TEXT"),
    ("shows", "subs_status", "TEXT"), ("shows", "subs_attempted", "TEXT"),
    # Trakt community rating backfill (by imdb id) — a distinct audience score + vote count
    ("movies", "trakt_rating", "REAL"), ("movies", "trakt_votes", "INTEGER"),
    ("movies", "trakt_status", "TEXT"), ("movies", "trakt_attempted", "TEXT"),
    ("shows", "trakt_rating", "REAL"), ("shows", "trakt_votes", "INTEGER"),
    ("shows", "trakt_status", "TEXT"), ("shows", "trakt_attempted", "TEXT"),
    # TVmaze community rating backfill (TV only)
    ("shows", "tvmaze_rating", "REAL"),
    ("shows", "tvmaze_status", "TEXT"), ("shows", "tvmaze_attempted", "TEXT"),
    # AniList anime average score backfill (TV only, 0-100)
    ("shows", "anilist_score", "INTEGER"),
    ("shows", "anilist_status", "TEXT"), ("shows", "anilist_attempted", "TEXT"),
    # Wikidata official-website backfill (movies + shows)
    ("movies", "wikidata_url", "TEXT"),
    ("movies", "wikidata_status", "TEXT"), ("movies", "wikidata_attempted", "TEXT"),
    ("shows", "wikidata_url", "TEXT"),
    ("shows", "wikidata_status", "TEXT"), ("shows", "wikidata_attempted", "TEXT"),
    # DeArrow crowd-sourced better titles for cached YouTube videos
    ("youtube_video_stats", "dearrow_title", "TEXT"),
    ("youtube_video_stats", "dearrow_status", "TEXT"),
    ("youtube_video_stats", "dearrow_attempted", "TEXT"),
    # TMDB details backfill: the server pre-matches shows/movies (so the matcher
    # skips them) but never supplies details-only fields like `status` (airing vs
    # ended) — which the watchlist's airing-default depends on. This marker drives a
    # one-time per-item detail re-fetch that fills those gaps. Starts 0 = needs it.
    ("shows", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("movies", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
]


def _subtitle_langs_list(raw) -> list:
    """Parse the stored OpenSubtitles ``subtitle_langs`` JSON array into a list of
    language codes for the detail payload. Returns [] for null/garbage so the UI
    can simply hide the row when empty."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v if x] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def youtube_surrogate_id(source_id: str) -> int:
    """A stable positive 60-bit int derived from a YouTube id, used as the
    NOT NULL ``tmdb_id`` surrogate for non-tmdb rows so the existing
    UNIQUE(kind, tmdb_id) dedup + group-by machinery keeps working unchanged.
    Collision probability across realistic channel counts is negligible."""
    h = hashlib.sha1((source_id or "").encode("utf-8")).hexdigest()
    return int(h[:15], 16)  # 60 bits — comfortably inside SQLite's signed 64-bit INTEGER


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
            self._ensure_indexes(conn)
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

    # Partial indexes that reference migration-added columns. They MUST run after
    # _ensure_columns (the schema executescript runs first, before the ALTERs, so
    # these would fail with "no such column" on an upgraded DB if placed there).
    _POST_INDEXES = (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_video_wishlist_video "
        "ON video_wishlist(source_id) WHERE kind = 'video'",
        "CREATE INDEX IF NOT EXISTS idx_video_wishlist_channel "
        "ON video_wishlist(parent_source_id) WHERE kind = 'video'",
        # Index on movies.tmdb_collection_id — a migration-added column, so it must be
        # created AFTER _ensure_columns (the schema executescript runs before the ALTERs).
        "CREATE INDEX IF NOT EXISTS idx_movies_collection ON movies(tmdb_collection_id)",
    )

    @classmethod
    def _ensure_indexes(cls, conn) -> None:
        """Create indexes that depend on migration-added columns (after columns exist)."""
        for stmt in cls._POST_INDEXES:
            conn.execute(stmt)

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
        if service == "ryd":
            return self.youtube_enrich_breakdown("ryd_status")
        if service == "sponsorblock":
            return self.youtube_enrich_breakdown("sb_status")
        if service in _BACKFILL:
            return self.backfill_breakdown(service)
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

    # ── backfill-worker plumbing (artwork / subtitles, by id) ─────────────────
    def backfill_next(self, service: str) -> dict | None:
        """Next library item needing a backfill service: a row that already has the
        id the service needs and no status yet. Returns
        {kind, id, title, tmdb_id, imdb_id[, tvdb_id]} or None."""
        kinds = _BACKFILL.get(service)
        if not kinds:
            return None
        conn = self._get_connection()
        try:
            for kind, (tbl, sc, _ac, has_id) in kinds.items():
                cols = "id, title, tmdb_id, imdb_id" + (", tvdb_id" if tbl == "shows" else "")
                row = conn.execute(
                    f"SELECT {cols} FROM {tbl} WHERE {sc} IS NULL AND {has_id} "
                    f"ORDER BY id LIMIT 1").fetchone()
                if row:
                    d = dict(row)
                    d["kind"] = kind
                    return d
            return None
        finally:
            conn.close()

    def backfill_mark(self, service: str, kind: str, item_id: int, status: str,
                      columns: dict | None = None) -> None:
        """Record a backfill result (status + attempted) and gap-fill whitelisted
        columns (COALESCE — never clobbers). status: 'ok'|'not_found'|'error'."""
        spec = _BACKFILL.get(service, {}).get(kind)
        if not spec:
            return
        tbl, sc, ac, _has = spec
        allowed = _BACKFILL_COLS.get(service, set())
        sets = [f"{sc}=?", f"{ac}=CURRENT_TIMESTAMP"]
        params: list = [status]
        for col, val in (columns or {}).items():
            if val is None or col not in allowed:
                continue
            sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
            params.append(val)
        params.append(item_id)
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {tbl} SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()

    def backfill_breakdown(self, service: str) -> dict:
        kinds = _BACKFILL.get(service, {})
        out = {}
        conn = self._get_connection()
        try:
            for kind, (tbl, sc, _ac, has_id) in kinds.items():
                base = f"FROM {tbl} WHERE {has_id}"
                out[kind] = {
                    "matched": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='ok'").fetchone()[0],
                    "not_found": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='not_found'").fetchone()[0],
                    "errors": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='error'").fetchone()[0],
                    "pending": conn.execute(f"SELECT COUNT(*) {base} AND {sc} IS NULL").fetchone()[0],
                }
            return out
        finally:
            conn.close()

    # ── per-video YouTube enrichment (no-key: RYD votes + SponsorBlock) ────────
    def youtube_enrich_next(self, status_col: str) -> dict | None:
        """Next cached YouTube video missing a per-video enrichment. status_col is
        'ryd_status' or 'sb_status'. Distinct by youtube_id (a video shared across
        playlists is enriched once). Returns {kind:'video', id, name, youtube_id}."""
        if status_col not in ("ryd_status", "sb_status", "dearrow_status"):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT cv.youtube_id AS youtube_id, MIN(cv.title) AS title "
                "FROM youtube_channel_videos cv "
                "LEFT JOIN youtube_video_stats s ON s.youtube_id = cv.youtube_id "
                f"WHERE s.youtube_id IS NULL OR s.{status_col} IS NULL "
                "GROUP BY cv.youtube_id LIMIT 1").fetchone()
            if not row:
                return None
            return {"kind": "video", "id": row["youtube_id"],
                    "name": row["title"], "youtube_id": row["youtube_id"]}
        finally:
            conn.close()

    def apply_youtube_votes(self, youtube_id, like_count, dislike_count, status: str) -> None:
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats "
                "(youtube_id, like_count, dislike_count, ryd_status, ryd_attempted) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "like_count=COALESCE(excluded.like_count, like_count), "
                "dislike_count=COALESCE(excluded.dislike_count, dislike_count), "
                "ryd_status=excluded.ryd_status, ryd_attempted=CURRENT_TIMESTAMP",
                (yid, like_count, dislike_count, status))
            conn.commit()
        finally:
            conn.close()

    def apply_youtube_segments(self, youtube_id, segments, status: str) -> None:
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats (youtube_id, sb_status, sb_attempted) "
                "VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "sb_status=excluded.sb_status, sb_attempted=CURRENT_TIMESTAMP", (yid, status))
            if segments:
                conn.execute("DELETE FROM youtube_video_segments WHERE youtube_id=?", (yid,))
                rows = [(yid, s.get("category"), s.get("start_sec"), s.get("end_sec"),
                         s.get("votes"), s.get("uuid"))
                        for s in segments if s.get("uuid") and s.get("category")]
                if rows:
                    conn.executemany(
                        "INSERT OR IGNORE INTO youtube_video_segments "
                        "(youtube_id, category, start_sec, end_sec, votes, uuid) "
                        "VALUES (?,?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()

    def apply_youtube_dearrow(self, youtube_id, title, status: str) -> None:
        """Record DeArrow's crowd-sourced better title (+ status) for a video."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats (youtube_id, dearrow_title, dearrow_status, dearrow_attempted) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "dearrow_title=COALESCE(excluded.dearrow_title, dearrow_title), "
                "dearrow_status=excluded.dearrow_status, dearrow_attempted=CURRENT_TIMESTAMP",
                (yid, title, status))
            conn.commit()
        finally:
            conn.close()

    def youtube_video_dearrow_title(self, youtube_id) -> str | None:
        """The DeArrow crowd title for a video, if one was recorded (detail UI)."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT dearrow_title FROM youtube_video_stats WHERE youtube_id=?", (yid,)).fetchone()
            return row["dearrow_title"] if row and row["dearrow_title"] else None
        finally:
            conn.close()

    def youtube_enrich_breakdown(self, status_col: str) -> dict:
        if status_col not in ("ryd_status", "sb_status", "dearrow_status"):
            return {}
        conn = self._get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(DISTINCT youtube_id) FROM youtube_channel_videos").fetchone()[0]

            def c(st):
                return conn.execute(
                    f"SELECT COUNT(*) FROM youtube_video_stats WHERE {status_col}=?", (st,)).fetchone()[0]

            matched, nf, err = c("ok"), c("not_found"), c("error")
            return {"video": {"matched": matched, "not_found": nf, "errors": err,
                              "pending": max(0, total - matched - nf - err)}}
        finally:
            conn.close()

    def youtube_video_segments(self, youtube_id) -> list:
        """SponsorBlock segments for a video (detail UI / skip logic)."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT category, start_sec, end_sec, votes FROM youtube_video_segments "
                "WHERE youtube_id=? ORDER BY start_sec", (yid,)).fetchall()
            return [dict(r) for r in rows]
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

    def owned_movie_tmdb_ids(self, server_source=None) -> set:
        """Set of TMDB ids the user OWNS (movies with a file) — for diffing against
        franchise/filmography lists in the gap engine."""
        sql = "SELECT DISTINCT tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL"
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        conn = self._get_connection()
        try:
            return {r["tmdb_id"] for r in conn.execute(sql, args)}
        except sqlite3.Error:
            return set()
        finally:
            conn.close()

    def owned_movie_collections(self, server_source=None, limit: int = 12) -> list:
        """Franchises the user has STARTED (owns >=1 movie in), most-invested first —
        drives the 'Complete your collections' gap rails. Returns
        [{collection_id, name, owned_count}]."""
        sql = ("SELECT tmdb_collection_id AS cid, "
               "MAX(tmdb_collection_name) AS name, COUNT(*) AS c "
               "FROM movies WHERE has_file=1 AND tmdb_collection_id IS NOT NULL")
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        sql += " GROUP BY tmdb_collection_id ORDER BY c DESC, name LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"collection_id": r["cid"], "name": r["name"], "owned_count": r["c"]}
                    for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def top_owned_people(self, jobs=("Director", "Creator"), min_titles: int = 2,
                         limit: int = 8, server_source=None) -> list:
        """People the user owns the most titles from (e.g. directors), busiest first —
        drives the 'More from <person>' gap rails. Returns
        [{person_id, tmdb_id, name, owned_count}] for people with a TMDB id and at
        least ``min_titles`` owned movies in the given crew ``jobs``."""
        job_list = [j for j in (jobs or []) if j]
        if not job_list:
            return []
        placeholders = ",".join("?" for _ in job_list)
        sql = (f"SELECT p.id AS pid, p.tmdb_id AS tmdb_id, p.name AS name, "
               f"COUNT(DISTINCT c.movie_id) AS c "
               f"FROM credits c JOIN people p ON p.id = c.person_id "
               f"JOIN movies m ON m.id = c.movie_id "
               f"WHERE m.has_file=1 AND c.department='crew' AND c.job IN ({placeholders}) "
               f"AND p.tmdb_id IS NOT NULL")
        args: list = list(job_list)
        if server_source:
            sql += " AND m.server_source=?"
            args.append(server_source)
        sql += " GROUP BY p.id HAVING c >= ? ORDER BY c DESC, p.name LIMIT ?"
        args.append(int(min_titles))
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"person_id": r["pid"], "tmdb_id": r["tmdb_id"],
                     "name": r["name"], "owned_count": r["c"]}
                    for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
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

    # ── TMDB details backfill (status / network / tagline / rating …) ──────────
    # The media server pre-matches items (tmdb_id set), so the matcher skips them and
    # never fetches details-only fields. This one-time pass re-fetches details for a
    # matched item and gap-fills, then marks it done so it isn't re-picked.
    _DETAIL_TBL = {"show": "shows", "movie": "movies"}

    def detail_backfill_next(self, kind: str) -> dict | None:
        """Next matched show/movie (has tmdb_id) whose details haven't been
        backfilled yet. Returns {kind, id, title, year, tmdb_id} or None."""
        tbl = self._DETAIL_TBL.get(kind)
        if not tbl:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT id, title, year, tmdb_id FROM {tbl} "
                f"WHERE tmdb_id IS NOT NULL AND details_synced=0 ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            d = dict(row)
            d["kind"] = kind
            return d
        finally:
            conn.close()

    def mark_details_synced(self, kind: str, item_id: int) -> None:
        """Flag that an item's TMDB details were backfilled (attempted once), so the
        background pass doesn't re-pick it even if a field stayed empty."""
        tbl = self._DETAIL_TBL.get(kind)
        if not tbl:
            return
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {tbl} SET details_synced=1 WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()

    def detail_backfill_pending_count(self) -> int:
        conn = self._get_connection()
        try:
            n = 0
            for tbl in ("shows", "movies"):
                n += conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE tmdb_id IS NOT NULL AND details_synced=0").fetchone()[0]
            return n
        finally:
            conn.close()

    def _ratings_breakdown(self) -> dict:
        """OMDb 'coverage' breakdown: matched = ratings present, pending = has an
        imdb_id but not fetched, not_found = fetched but OMDb had no rating."""
        conn = self._get_connection()
        out = {}
        try:
            for kind, tbl in (("movie", "movies"), ("show", "shows")):
                def c(where, _tbl=tbl):   # bind tbl per-iteration (ruff B023)
                    return conn.execute(f"SELECT COUNT(*) FROM {_tbl} WHERE {where}").fetchone()[0]
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
        if service in ("ryd", "sponsorblock", "dearrow"):
            col = {"ryd": "ryd_status", "sponsorblock": "sb_status", "dearrow": "dearrow_status"}[service]
            att = {"ryd": "ryd_attempted", "sponsorblock": "sb_attempted", "dearrow": "dearrow_attempted"}[service]
            conn = self._get_connection()
            try:
                if scope == "item" and item_id is not None:
                    cur = conn.execute(
                        f"UPDATE youtube_video_stats SET {col}=NULL, {att}=NULL WHERE youtube_id=?",
                        (str(item_id),))
                else:
                    cur = conn.execute(
                        f"UPDATE youtube_video_stats SET {col}=NULL, {att}=NULL "
                        f"WHERE {col} IN ('not_found','error')")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
        if service in _BACKFILL:
            spec = _BACKFILL[service].get(kind)
            if not spec:
                return 0
            tbl, sc, ac, _has = spec
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

    def retry_all_failed(self) -> int:
        """Re-queue every failed/not_found item across ALL enrichment services and
        their kinds (the modal's GLOBAL 'Retry all failed'). Derives the service+kind
        set from the same maps the workers use, so it stays in sync. Returns the
        total number of items re-queued."""
        pairs = [(svc, k) for svc, kinds in _ENRICH.items() for k in kinds]   # tmdb, tvdb
        pairs += [("omdb", "movie"), ("omdb", "show")]                        # ratings (special-cased)
        pairs += [(svc, k) for svc, kinds in _BACKFILL.items() for k in kinds]  # fanart/trakt/…
        pairs += [("ryd", "video"), ("sponsorblock", "video"), ("dearrow", "video")]  # YouTube video stats
        total = 0
        for svc, kind in pairs:
            try:
                total += self.enrichment_retry(svc, kind, scope="failed")
            except Exception:
                logger.exception("retry_all_failed: %s/%s failed", svc, kind)
        return total

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

    # ── video downloads (the grab → transfer pipeline) ────────────────────────
    _DL_FIELDS = ("kind", "title", "release_title", "source", "username", "filename",
                  "size_bytes", "quality_label", "target_dir", "status",
                  "media_id", "media_source", "year", "poster_url",
                  "candidates", "search_ctx", "tried_queries", "tried_files", "attempts")

    def add_video_download(self, rec: dict) -> int:
        """Insert a download row (status defaults to 'downloading'); returns its id."""
        rec = rec or {}
        cols = [f for f in self._DL_FIELDS if f in rec]
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_downloads (" + ", ".join(cols) + ") VALUES (" +
                ", ".join("?" for _ in cols) + ")",
                tuple(rec[c] for c in cols),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def list_video_downloads(self, limit: int = 100) -> list:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads ORDER BY "
                "CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
                "id DESC LIMIT ?", (int(limit),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_video_download(self, dl_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM video_downloads WHERE id = ?", (int(dl_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_active_video_downloads(self) -> list:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads WHERE status IN ('queued', 'downloading', 'searching') ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def media_tmdb_id(self, kind: str, media_id) -> tuple:
        """(tmdb_id, imdb_id) for a library movie/show row — used to resolve sidecar /
        subtitle metadata for an owned re-grab (whose media_id is the library id, not a
        TMDB id). (None, None) if the row is gone."""
        table = "movies" if str(kind or "").lower() == "movie" else "shows"
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT tmdb_id, imdb_id FROM %s WHERE id = ?" % table, (media_id,)
            ).fetchone()
            return (row["tmdb_id"], row["imdb_id"]) if row else (None, None)
        finally:
            conn.close()

    def get_import_failed_video_downloads(self) -> list:
        """Downloads that finished but couldn't be auto-placed (sample / wrong episode /
        not-an-upgrade / corrupt / pack / parse fail). Their file is still on disk at
        ``dest_path`` — the Import page surfaces these for manual placement."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads WHERE status = 'import_failed' ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_video_download(self, dl_id: int, **fields) -> None:
        """Patch a download row; ``updated_at`` is always bumped."""
        if not fields:
            return
        keys = list(fields.keys())
        sets = ", ".join(k + " = ?" for k in keys) + ", updated_at = datetime('now')"
        conn = self._get_connection()
        try:
            conn.execute("UPDATE video_downloads SET " + sets + " WHERE id = ?",
                         tuple(fields[k] for k in keys) + (int(dl_id),))
            conn.commit()
        finally:
            conn.close()

    def clear_finished_video_downloads(self) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_downloads WHERE status IN ('completed', 'failed', 'cancelled', 'import_failed')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ── download history (permanent archive; survives the queue cleanup) ───────
    @staticmethod
    def _parse_resolution(*texts) -> str | None:
        """Best-effort resolution/codec sniff from a release/file name."""
        import re
        blob = " ".join(t for t in texts if t)
        for pat, label in ((r"\b2160p\b|\b4k\b|\buhd\b", "2160p"), (r"\b1080p\b", "1080p"),
                           (r"\b720p\b", "720p"), (r"\b480p\b", "480p")):
            if re.search(pat, blob, re.I):
                return label
        return None

    @staticmethod
    def _codec(*texts) -> str | None:
        import re
        blob = " ".join(t for t in texts if t)
        for pat, label in ((r"\b[xh]?265\b|\bhevc\b", "x265"), (r"\b[xh]?264\b|\bavc\b", "x264"),
                           (r"\bav1\b", "AV1"), (r"\bxvid\b", "XviD")):
            if re.search(pat, blob, re.I):
                return label
        return None

    def record_download_history(self, row: dict) -> int:
        """Snapshot a finished download (the merged final ``video_downloads`` record)
        into the permanent history. ``outcome`` is derived from its status. Idempotent
        per (download_id, outcome, dest_path) so a re-persist / restart never dupes.
        Returns the new history id, or 0 if it was a duplicate / not worth recording."""
        if not row:
            return 0
        status = row.get("status") or "completed"
        outcome = {"completed": "completed", "import_failed": "import_failed",
                   "failed": "failed", "cancelled": "cancelled"}.get(status, status)
        kind = row.get("kind") or "movie"
        media_type = "show" if kind == "show" else ("movie" if kind == "movie" else kind)
        # season/episode live in the retry search_ctx JSON for episode grabs.
        sn = en = None
        ctx = row.get("search_ctx")
        if ctx:
            try:
                ctx = json.loads(ctx) if isinstance(ctx, str) else (ctx or {})
                sn, en = ctx.get("season"), ctx.get("episode")
            except (ValueError, TypeError):
                pass
        rel, fn = row.get("release_title"), row.get("filename")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO video_download_history
                       (download_id, kind, media_type, title, year, season_number, episode_number,
                        release_title, source, username, filename, dest_path, size_bytes,
                        quality_label, resolution, video_codec, media_id, media_source, poster_url,
                        outcome, error, grabbed_at, completed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get("id"), kind, media_type, row.get("title"), row.get("year"), sn, en,
                 rel, row.get("source"), row.get("username"), fn, row.get("dest_path"),
                 int(row.get("size_bytes") or 0), row.get("quality_label"),
                 self._parse_resolution(rel, fn, row.get("quality_label")), self._codec(rel, fn),
                 row.get("media_id"), row.get("media_source"), row.get("poster_url"),
                 outcome, row.get("error"), row.get("created_at"),
                 row.get("completed_at")))
            conn.commit()
            return cur.lastrowid or 0
        except Exception:
            logger.exception("record_download_history failed for download %s", row.get("id"))
            conn.rollback()
            return 0
        finally:
            conn.close()

    def query_download_history(self, *, kind=None, search=None, outcome=None,
                               page=1, limit=40) -> dict:
        """Paged history slice for the modal. ``kind`` ∈ movie|show (None=all);
        ``outcome`` filters by result. Newest first. {items, pagination}."""
        try:
            page = max(1, int(page or 1)); limit = max(1, min(200, int(limit or 40)))
        except (TypeError, ValueError):
            page, limit = 1, 40
        where, args = ["1=1"], []
        if kind in ("movie", "show"):
            where.append("kind = ?"); args.append(kind)
        if outcome:
            where.append("outcome = ?"); args.append(outcome)
        s = (search or "").strip()
        if s:
            where.append("(title LIKE ? OR release_title LIKE ?) COLLATE NOCASE")
            args += ["%" + s + "%", "%" + s + "%"]
        wsql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM video_download_history" + wsql, args).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM video_download_history" + wsql +
                " ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
            items = [dict(r) for r in rows]
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        return {"items": items, "pagination": {
            "page": min(page, total_pages), "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    def download_history_detail(self, history_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM video_download_history WHERE id=?", (int(history_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def download_history_counts(self) -> dict:
        """{movie, show, total} of completed grabs (for the modal tabs/badge)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, COUNT(*) c FROM video_download_history "
                "WHERE outcome='completed' GROUP BY kind").fetchall()
            by = {r["kind"]: r["c"] for r in rows}
            movie, show = by.get("movie", 0), by.get("show", 0)
            return {"movie": movie, "show": show, "total": movie + show}
        finally:
            conn.close()

    def latest_completed_download(self, media_type: str = "all") -> dict | None:
        """The most recently completed grab of a type — the probe target for the smart
        post-download scan. ``media_type`` ∈ movie|show|all."""
        where, args = ["outcome = 'completed'"], []
        if media_type in ("movie", "show"):
            where.append("kind = ?"); args.append(media_type)
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM video_download_history WHERE " + " AND ".join(where) +
                " ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT 1", args).fetchone()
            return dict(row) if row else None
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
                # Curated wishlist (movies + wanted episodes), NOT the old
                # v_wishlist view that auto-listed every missing item.
                "wishlist": self.wishlist_counts()["total"],
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
    def _resilient_upsert(conn, table: str, base: dict, id_cols: dict,
                          preserve_enrichment: bool = True) -> None:
        """INSERT…ON CONFLICT(server_source, server_id) for a movie/show row.

        Resilient to a LEGACY UNIQUE on tmdb_id/tvdb_id/imdb_id (old DBs created
        before those were made non-unique — SQLite can't drop an inline UNIQUE):
        on IntegrityError we retry WITHOUT the id columns, so the row is still
        stored (same film in >1 library) instead of being dropped by the scan.
        ``base`` holds the always-written cols; ``id_cols`` the droppable ids.

        ``preserve_enrichment`` (default) keeps enrichment-owned fields (``status``,
        network, ratings, air dates…) that the SERVER left blank — so an incremental
        or deep re-scan never wipes the TMDB-backfilled ``status`` (which the airing
        watchlist depends on). A FULL scan passes False = a clean overwrite / reset."""
        protect = (_ENRICH_META_COLS.get(table, set()) if preserve_enrichment else set())

        def _set(c):
            # On a conflict UPDATE, an enrichment-owned column only takes the server
            # value when it's non-blank; otherwise it keeps what's already stored.
            if c in protect:
                return f"{c}=COALESCE(NULLIF(excluded.{c}, ''), {table}.{c})"
            return f"{c}=excluded.{c}"

        def run(include_ids):
            cols = list(base.keys()) + (list(id_cols.keys()) if include_ids else [])
            vals = list(base.values()) + (list(id_cols.values()) if include_ids else [])
            updates = [c for c in cols if c not in ("server_source", "server_id")]
            set_clause = ", ".join(_set(c) for c in updates) + ", updated_at=CURRENT_TIMESTAMP"
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

    def upsert_movie(self, server_source: str, item: dict, preserve_enrichment: bool = True) -> int:
        """Insert/update one movie (keyed on server id) and its file. Returns row id.
        ``preserve_enrichment`` keeps enrichment-owned fields the server left blank
        (default); a FULL scan passes False for a clean reset."""
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
            }, {"tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
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

    def upsert_show_tree(self, server_source: str, item: dict, preserve_enrichment: bool = True) -> int:
        """Insert/update a show with its seasons + episodes (and files) in one
        transaction. Episodes/seasons no longer present on the server for this
        show are pruned. Returns the show row id. ``preserve_enrichment`` keeps
        enrichment-owned fields (status etc.) the server left blank (default); a FULL
        scan passes False for a clean reset."""
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
            }, {"tvdb_id": item.get("tvdb_id"), "tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
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

    def calendar_upcoming(self, start_date: str, end_date: str, server_source=None,
                          watchlist_only: bool = False) -> list[dict]:
        """Episodes airing in [start_date, end_date] (ISO) for shows on the active
        video server (``server_source``) — the Calendar feed. Scoped to one server
        so Plex and Jellyfin never commingle. Each row carries owned/missing
        (has_file), a still flag, and show network/airs_time/year for the card.

        ``watchlist_only`` restricts to the EFFECTIVE watchlist — explicit show
        follows ∪ airing library shows (not muted), mirroring _effective_shows /
        the Shows watchlist tab — so the calendar tracks what you follow."""
        # server_source given → that server only; None → all owned shows.
        if server_source:
            srv_where, pre = "s.server_source = ?", [server_source]
        else:
            srv_where, pre = "s.server_source IS NOT NULL", []
        wl_where = ""
        if watchlist_only:
            active = self._ACTIVE_SHOW_SQL.replace("status", "s.status")
            wl_where = (
                " AND (s.tmdb_id IN (SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='follow')"
                " OR (" + active + " AND s.tmdb_id NOT IN "
                "(SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute')))")
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, "
                "e.overview, e.air_date, e.runtime_minutes, e.rating, e.has_file, e.monitored, "
                "e.still_url, (e.still_url IS NOT NULL AND e.still_url<>'') AS has_still, "
                "s.tmdb_id AS show_tmdb_id, "
                "s.title AS show_title, s.network, s.airs_time, s.year AS show_year, s.status AS show_status, "
                "(s.poster_url IS NOT NULL AND s.poster_url<>'') AS show_has_poster, "
                "(s.backdrop_url IS NOT NULL AND s.backdrop_url<>'') AS show_has_backdrop "
                "FROM episodes e JOIN shows s ON s.id = e.show_id "
                "WHERE " + srv_where + " "
                "AND e.air_date IS NOT NULL AND e.air_date >= ? AND e.air_date <= ?" + wl_where + " "
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
            "trakt_rating": show["trakt_rating"], "trakt_votes": show["trakt_votes"],
            "tvmaze_rating": show["tvmaze_rating"],
            "anilist_score": show["anilist_score"],
            "wikidata_url": show["wikidata_url"],
            "genres": genres, "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": show["tmdb_id"], "tvdb_id": show["tvdb_id"], "imdb_id": show["imdb_id"],
            "has_poster": bool(show["poster_url"]), "has_backdrop": bool(show["backdrop_url"]),
            "logo": show["logo_url"],
            "subtitle_langs": _subtitle_langs_list(show["subtitle_langs"]),
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

    def followed_shows(self) -> list[dict]:
        """Explicitly-followed shows (state='follow') with their library status when
        owned (NULL for tmdb-only follows). Used by the watchlist-prune pass to drop
        shows that have since ended/been canceled."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.library_id, s.status "
                "FROM video_watchlist w LEFT JOIN shows s ON s.id = w.library_id "
                "WHERE w.kind='show' AND w.state='follow'")]
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
        if sort == "added":   # opt-in: explicit follows (have a date) newest-first
            items.sort(key=lambda it: (it.get("date_added") or ""), reverse=True)
        else:   # "default" / "title": alphabetical by name — a manual follow is no more
                # special than an auto-added airing show, so they sort together A–Z.
                # .strip() guards against dirty titles (e.g. a leading space from the
                # scan, which would otherwise sort before 'a').
            items.sort(key=lambda it: (it.get("sort_title") or it.get("title") or "").strip().lower())
        total = len(items)
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        start = (page - 1) * limit
        return {"items": items[start:start + limit], "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    # ── Wishlist (curated 'get this': movies + episodes) ──────────────────────
    # Atomic units are movies and episodes. Adding a whole show/season expands
    # into episode rows (the caller supplies the explicit episodes); show/season
    # are just bulk add/remove operations over those rows.
    def add_movie_to_wishlist(self, tmdb_id, title, *, year=None, poster_url=None,
                              library_id=None, server_source=None) -> bool:
        """Wish for a movie. Idempotent upsert on its tmdb id."""
        if not tmdb_id or not title:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_wishlist (kind, tmdb_id, title, poster_url, year, library_id, server_source)
                   VALUES ('movie', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tmdb_id) WHERE kind='movie' DO UPDATE SET
                       title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                       year=COALESCE(excluded.year, video_wishlist.year),
                       library_id=COALESCE(excluded.library_id, video_wishlist.library_id)""",
                (int(tmdb_id), title, poster_url, year, library_id, server_source))
            conn.commit()
            return True
        except Exception:
            logger.exception("add_movie_to_wishlist failed (%s)", tmdb_id)
            return False
        finally:
            conn.close()

    def add_episodes_to_wishlist(self, show_tmdb_id, show_title, episodes, *,
                                 poster_url=None, library_id=None, server_source=None) -> int:
        """Wish for one or more episodes of a show (the show's tmdb id keys them).
        ``episodes`` = [{season_number, episode_number, title?, air_date?}, …].
        Idempotent per (show, season, episode). Returns the count written."""
        if not show_tmdb_id or not show_title or not episodes:
            return 0
        conn = self._get_connection()
        n = 0
        try:
            for e in episodes:
                sn, en = e.get("season_number"), e.get("episode_number")
                if sn is None or en is None:
                    continue
                conn.execute(
                    """INSERT INTO video_wishlist
                           (kind, tmdb_id, title, poster_url, season_number, episode_number,
                            episode_title, still_url, episode_overview, season_poster_url,
                            air_date, library_id, server_source)
                       VALUES ('episode', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(tmdb_id, season_number, episode_number) WHERE kind='episode' DO UPDATE SET
                           title=excluded.title,
                           poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                           episode_title=COALESCE(excluded.episode_title, video_wishlist.episode_title),
                           still_url=COALESCE(excluded.still_url, video_wishlist.still_url),
                           episode_overview=COALESCE(excluded.episode_overview, video_wishlist.episode_overview),
                           season_poster_url=COALESCE(excluded.season_poster_url, video_wishlist.season_poster_url),
                           air_date=COALESCE(excluded.air_date, video_wishlist.air_date),
                           library_id=COALESCE(excluded.library_id, video_wishlist.library_id)""",
                    (int(show_tmdb_id), show_title, poster_url, int(sn), int(en),
                     e.get("title"), e.get("still_url"), e.get("overview"), e.get("season_poster_url"),
                     e.get("air_date"), library_id, server_source))
                n += 1
            conn.commit()
            return n
        except Exception:
            logger.exception("add_episodes_to_wishlist failed (%s)", show_tmdb_id)
            conn.rollback()
            return 0
        finally:
            conn.close()

    def remove_from_wishlist(self, scope, *, tmdb_id, season_number=None, episode_number=None) -> int:
        """Remove at any granularity: 'movie' | 'show' (all its episodes) |
        'season' | 'episode'. Returns the number of rows removed."""
        if not tmdb_id:
            return 0
        if scope == "movie":
            sql, args = "DELETE FROM video_wishlist WHERE kind='movie' AND tmdb_id=?", (int(tmdb_id),)
        elif scope == "show":
            sql, args = "DELETE FROM video_wishlist WHERE kind='episode' AND tmdb_id=?", (int(tmdb_id),)
        elif scope == "season":
            if season_number is None:
                return 0
            sql = "DELETE FROM video_wishlist WHERE kind='episode' AND tmdb_id=? AND season_number=?"
            args = (int(tmdb_id), int(season_number))
        elif scope == "episode":
            if season_number is None or episode_number is None:
                return 0
            sql = ("DELETE FROM video_wishlist WHERE kind='episode' AND tmdb_id=? "
                   "AND season_number=? AND episode_number=?")
            args = (int(tmdb_id), int(season_number), int(episode_number))
        else:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def clear_wishlist(self, kind: str) -> int:
        """Empty an entire wishlist tab in one go. ``kind`` is the user-facing tab:
        'movie' | 'show' (TV) | 'youtube'. Returns the number of rows removed."""
        dbkind = {"movie": "movie", "show": "episode", "youtube": "video"}.get(kind)
        if not dbkind:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_wishlist WHERE kind=?", (dbkind,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def wishlist_counts(self) -> dict:
        """{'movie': n, 'show': n, 'episode': n, 'total': movies+episodes}."""
        conn = self._get_connection()
        try:
            movie = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='movie'").fetchone()["c"]
            episode = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='episode'").fetchone()["c"]
            shows = conn.execute("SELECT COUNT(DISTINCT tmdb_id) c FROM video_wishlist WHERE kind='episode'").fetchone()["c"]
            return {"movie": movie, "show": shows, "episode": episode, "total": movie + episode}
        finally:
            conn.close()

    def query_wishlist(self, kind: str, *, search=None, sort="added", page=1, limit=60) -> dict:
        """One paged slice of the wishlist. kind='movie' → movie cards; kind='show'
        → shows grouped show→season→episode with wanted/done roll-ups. ``sort`` ∈
        added | title | wanted (wanted = most episodes, shows only). {items,
        pagination} like the other paged queries."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        s = (search or "").strip()
        conn = self._get_connection()
        try:
            if kind == "movie":
                where, args = ["kind='movie'"], []
                if s:
                    where.append("title LIKE ? COLLATE NOCASE"); args.append("%" + s + "%")
                wsql = " WHERE " + " AND ".join(where)
                order = {"title": "title COLLATE NOCASE", "oldest": "date_added ASC, id ASC",
                         "added": "date_added DESC, id DESC"}.get(sort, "date_added DESC, id DESC")
                total = conn.execute("SELECT COUNT(*) c FROM video_wishlist" + wsql, args).fetchone()["c"]
                rows = conn.execute(
                    "SELECT tmdb_id, title, poster_url, year, status, library_id, date_added "
                    "FROM video_wishlist" + wsql + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                    args + [limit, (page - 1) * limit]).fetchall()
                items = [{"kind": "movie", "tmdb_id": r["tmdb_id"], "title": r["title"],
                          "poster_url": r["poster_url"], "year": r["year"], "status": r["status"],
                          "library_id": r["library_id"]} for r in rows]
            else:   # shows (grouped from episode rows)
                where, args = ["kind='episode'"], []
                if s:
                    where.append("title LIKE ? COLLATE NOCASE"); args.append("%" + s + "%")
                wsql = " WHERE " + " AND ".join(where)
                total = conn.execute(
                    "SELECT COUNT(DISTINCT tmdb_id) c FROM video_wishlist" + wsql, args).fetchone()["c"]
                order = {"title": "title COLLATE NOCASE", "wanted": "wanted DESC, last_added DESC",
                         "oldest": "last_added ASC", "added": "last_added DESC"}.get(sort, "last_added DESC")
                show_rows = conn.execute(
                    "SELECT tmdb_id, MAX(title) AS title, MAX(poster_url) AS poster_url, "
                    "MAX(library_id) AS library_id, COUNT(*) AS wanted, "
                    "SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) AS done, "
                    "MAX(date_added) AS last_added "
                    "FROM video_wishlist" + wsql +
                    " GROUP BY tmdb_id ORDER BY " + order + " LIMIT ? OFFSET ?",
                    args + [limit, (page - 1) * limit]).fetchall()
                items = []
                for sr in show_rows:
                    eps = conn.execute(
                        "SELECT season_number, episode_number, episode_title, still_url, "
                        "episode_overview, season_poster_url, air_date, status "
                        "FROM video_wishlist WHERE kind='episode' AND tmdb_id=? "
                        "ORDER BY season_number, episode_number", (sr["tmdb_id"],)).fetchall()
                    by_season: dict = {}
                    season_poster: dict = {}
                    for e in eps:
                        by_season.setdefault(e["season_number"], []).append({
                            "episode_number": e["episode_number"], "title": e["episode_title"],
                            "still_url": e["still_url"], "overview": e["episode_overview"],
                            "air_date": e["air_date"], "status": e["status"]})
                        if e["season_poster_url"] and e["season_number"] not in season_poster:
                            season_poster[e["season_number"]] = e["season_poster_url"]
                    seasons = [{"season_number": sn, "poster_url": season_poster.get(sn),
                                "episodes": by_season[sn]} for sn in sorted(by_season)]
                    items.append({"kind": "show", "tmdb_id": sr["tmdb_id"], "title": sr["title"],
                                  "poster_url": sr["poster_url"], "library_id": sr["library_id"],
                                  "wanted": sr["wanted"], "done": sr["done"] or 0, "seasons": seasons})
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        return {"items": items, "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    def wishlist_keys_for_shows(self, show_tmdb_ids) -> dict:
        """{show_tmdb_id: set('S_E')} of episodes already wishlisted — lets the
        calendar's 'add missing' button skip what's already queued."""
        out: dict = {}
        ids = [int(x) for x in (show_tmdb_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id, season_number, episode_number FROM video_wishlist "
                        f"WHERE kind='episode' AND tmdb_id IN ({ph})", chunk):
                    out.setdefault(r["tmdb_id"], set()).add("%s_%s" % (r["season_number"], r["episode_number"]))
            return out
        finally:
            conn.close()

    def wishlist_art_backfill_targets(self) -> list:
        """Distinct (show_tmdb_id, season) with episode rows missing a still OR a
        season poster — one tmdb_season fetch per group fills both (cheap backfill
        for rows added before art-capture existed)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT tmdb_id, season_number FROM video_wishlist "
                "WHERE kind='episode' AND tmdb_id IS NOT NULL AND season_number IS NOT NULL "
                "AND ((still_url IS NULL OR still_url='') OR "
                "     (season_poster_url IS NULL OR season_poster_url='') OR "
                "     (episode_overview IS NULL OR episode_overview=''))").fetchall()
            return [{"tmdb_id": r["tmdb_id"], "season_number": r["season_number"]} for r in rows]
        finally:
            conn.close()

    def set_wishlist_still(self, show_tmdb_id, season_number, episode_number, still_url) -> bool:
        """Fill a single episode's still (only if it doesn't already have one)."""
        if not still_url:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET still_url=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND episode_number=? AND (still_url IS NULL OR still_url='')",
                (still_url, int(show_tmdb_id), int(season_number), int(episode_number)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_episode_overview(self, show_tmdb_id, season_number, episode_number, overview) -> bool:
        """Fill a single episode's synopsis (only if it doesn't already have one)."""
        if not overview:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET episode_overview=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND episode_number=? AND (episode_overview IS NULL OR episode_overview='')",
                (overview, int(show_tmdb_id), int(season_number), int(episode_number)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_season_poster(self, show_tmdb_id, season_number, poster_url) -> int:
        """Fill the season poster on every episode row of a season that lacks one."""
        if not poster_url:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET season_poster_url=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND (season_poster_url IS NULL OR season_poster_url='')",
                (poster_url, int(show_tmdb_id), int(season_number)))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def wishlist_state(self, *, movie_ids=None, show_tmdb_id=None) -> dict:
        """Hydration: which of ``movie_ids`` are wishlisted, and which episode
        keys ('S_E') of ``show_tmdb_id`` are. Returns {movies:set, episodes:set}."""
        out = {"movies": set(), "episodes": set()}
        conn = self._get_connection()
        try:
            ids = [int(x) for x in (movie_ids or []) if x]
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id FROM video_wishlist WHERE kind='movie' AND tmdb_id IN ({ph})", chunk):
                    out["movies"].add(r["tmdb_id"])
            if show_tmdb_id:
                for r in conn.execute(
                        "SELECT season_number, episode_number FROM video_wishlist "
                        "WHERE kind='episode' AND tmdb_id=?", (int(show_tmdb_id),)):
                    out["episodes"].add("%s_%s" % (r["season_number"], r["episode_number"]))
            return out
        finally:
            conn.close()

    # ── YouTube channels (bridged onto the watchlist/wishlist tables) ─────────
    # A followed CHANNEL is a video_watchlist row (kind='channel', source='youtube',
    # source_id=channel id). Its wished VIDEOS are video_wishlist rows
    # (kind='video', source_id=video id, parent_source_id=channel id). tmdb_id on
    # both carries the channel's surrogate so existing dedup/grouping just works.
    def add_channel_to_watchlist(self, channel: dict) -> bool:
        """Follow a YouTube channel. ``channel`` = {youtube_id, title, avatar_url?}.
        Idempotent upsert on the channel surrogate. Returns True on success."""
        cid = (channel or {}).get("youtube_id")
        title = (channel or {}).get("title")
        if not cid or not title:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('channel', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(cid), title, channel.get("avatar_url"), cid))
            conn.commit()
            return True
        except Exception:
            logger.exception("add_channel_to_watchlist failed (%s)", cid)
            return False
        finally:
            conn.close()

    def remove_channel_from_watchlist(self, youtube_id: str) -> bool:
        """Un-follow a channel (hard delete — channels have no airing-default to
        guard against, so no tombstone). Its already-wished videos are left alone."""
        if not youtube_id:
            return False
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM video_watchlist WHERE kind='channel' AND source_id=?", (youtube_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def list_watchlist_channels(self) -> list[dict]:
        """Followed channels (newest first): ``video_count`` is the REMEMBERED catalog
        size (from the cache, fills in as the channel is enriched/opened), plus how
        many of its videos are wished."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT w.title, w.poster_url, w.source_id, w.date_added, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv WHERE cv.channel_id = w.source_id) AS video_count, "
                "(SELECT COUNT(*) FROM video_wishlist v WHERE v.kind='video' "
                " AND v.parent_source_id = w.source_id) AS wished_count "
                "FROM video_watchlist w WHERE w.kind='channel' AND w.state='follow' "
                "ORDER BY w.date_added DESC, w.id DESC").fetchall()
            return [{"kind": "channel", "youtube_id": r["source_id"], "title": r["title"],
                     "poster_url": r["poster_url"], "video_count": r["video_count"],
                     "wished_count": r["wished_count"], "date_added": r["date_added"]} for r in rows]
        finally:
            conn.close()

    def channel_watch_state(self, youtube_ids) -> dict:
        """{youtube_id: True} for followed channels — hydrates the Follow button."""
        out: dict = {}
        ids = [str(x) for x in (youtube_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_watchlist WHERE kind='channel' "
                        f"AND state='follow' AND source_id IN ({ph})", chunk):
                    out[r["source_id"]] = True
            return out
        finally:
            conn.close()

    # A followed PLAYLIST mirrors a channel: a video_watchlist row (kind='playlist',
    # source='youtube', source_id=PL id). Same surrogate scheme so dedup just works.
    def add_playlist_to_watchlist(self, playlist: dict) -> bool:
        """Follow a YouTube playlist. ``playlist`` = {playlist_id, title, thumbnail_url?}."""
        pid = (playlist or {}).get("playlist_id")
        title = (playlist or {}).get("title")
        if not pid or not title:
            return False
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('playlist', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(pid), title, (playlist or {}).get("thumbnail_url"), pid))
            conn.commit()
            return True
        except Exception:
            logger.exception("add_playlist_to_watchlist failed (%s)", pid)
            return False
        finally:
            conn.close()

    def remove_playlist_from_watchlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM video_watchlist WHERE kind='playlist' AND source_id=?", (playlist_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def list_watchlist_playlists(self) -> list[dict]:
        """Followed playlists (newest first), each with its remembered video count
        (cached when the playlist is followed/opened)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT w.title, w.poster_url, w.source_id, w.date_added, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv WHERE cv.channel_id = w.source_id) AS video_count "
                "FROM video_watchlist w WHERE w.kind='playlist' AND w.state='follow' "
                "ORDER BY w.date_added DESC, w.id DESC").fetchall()
            return [{"kind": "playlist", "playlist_id": r["source_id"], "title": r["title"],
                     "poster_url": r["poster_url"], "video_count": r["video_count"],
                     "date_added": r["date_added"]} for r in rows]
        finally:
            conn.close()

    def playlist_watch_state(self, playlist_ids) -> dict:
        """{playlist_id: True} for followed playlists — hydrates the Follow button."""
        out: dict = {}
        ids = [str(x) for x in (playlist_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_watchlist WHERE kind='playlist' "
                        f"AND state='follow' AND source_id IN ({ph})", chunk):
                    out[r["source_id"]] = True
            return out
        finally:
            conn.close()

    def add_videos_to_wishlist(self, channel: dict, videos: list, *, server_source=None) -> int:
        """Wish for a channel's videos. ``channel`` = {youtube_id, title, avatar_url?};
        ``videos`` = [{youtube_id, title, published_at?, thumbnail_url?, description?}, …].
        Idempotent per video id. Returns the count written."""
        cid = (channel or {}).get("youtube_id")
        ctitle = (channel or {}).get("title")
        if not cid or not ctitle or not videos:
            return 0
        avatar = (channel or {}).get("avatar_url")
        surrogate = youtube_surrogate_id(cid)
        conn = self._get_connection()
        n = 0
        try:
            for v in videos:
                vid = v.get("youtube_id")
                if not vid:
                    continue
                conn.execute(
                    """INSERT INTO video_wishlist
                           (kind, tmdb_id, title, poster_url, episode_title, still_url,
                            episode_overview, air_date, source, source_id, parent_source_id, server_source)
                       VALUES ('video', ?, ?, ?, ?, ?, ?, ?, 'youtube', ?, ?, ?)
                       ON CONFLICT(source_id) WHERE kind='video' DO UPDATE SET
                           title=excluded.title,
                           poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                           episode_title=COALESCE(excluded.episode_title, video_wishlist.episode_title),
                           still_url=COALESCE(excluded.still_url, video_wishlist.still_url),
                           episode_overview=COALESCE(excluded.episode_overview, video_wishlist.episode_overview),
                           air_date=COALESCE(excluded.air_date, video_wishlist.air_date)""",
                    (surrogate, ctitle, avatar, v.get("title"), v.get("thumbnail_url"),
                     v.get("description"), v.get("published_at"), vid, cid, server_source))
                n += 1
            conn.commit()
            return n
        except Exception:
            logger.exception("add_videos_to_wishlist failed (%s)", cid)
            conn.rollback()
            return 0
        finally:
            conn.close()

    def remove_youtube_from_wishlist(self, scope: str, source_id: str) -> int:
        """Remove wished videos: scope 'channel' (all of a channel, source_id=channel
        id) or 'video' (one, source_id=video id). Returns rows removed."""
        if not source_id:
            return 0
        if scope == "channel":
            sql = "DELETE FROM video_wishlist WHERE kind='video' AND parent_source_id=?"
        elif scope == "video":
            sql = "DELETE FROM video_wishlist WHERE kind='video' AND source_id=?"
        else:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, (source_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def youtube_video_wish_state(self, video_ids) -> set:
        """Which of ``video_ids`` (youtube ids) are already wished — hydrates the
        per-video buttons on the channel detail page."""
        out: set = set()
        ids = [str(x) for x in (video_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_wishlist WHERE kind='video' "
                        f"AND source_id IN ({ph})", chunk):
                    out.add(r["source_id"])
            return out
        finally:
            conn.close()

    def remove_one_video_from_wishlist(self, video_id) -> int:
        """Remove a single wished video by its youtube id. (Thin alias kept explicit
        for the detail page's per-video toggle.)"""
        return self.remove_youtube_from_wishlist("video", video_id)

    def set_wishlist_video_overview(self, video_id, overview) -> bool:
        """Persist a video's lazily-fetched description onto its wishlist row (only
        if it doesn't already have one) so re-opening is instant — mirrors the
        episode-overview backfill. Returns True if a row was updated."""
        if not overview or not video_id:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET episode_overview=? WHERE kind='video' AND source_id=? "
                "AND (episode_overview IS NULL OR episode_overview='')", (overview, str(video_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_channel_poster(self, channel_id, poster_url) -> int:
        """Refresh the channel avatar on all of a channel's wished video rows — used
        to backfill avatars that flat listing didn't surface (channel page resolves
        the real avatar). Returns rows updated."""
        if not poster_url or not channel_id:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET poster_url=? WHERE kind='video' AND parent_source_id=?",
                (poster_url, str(channel_id)))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def cache_video_dates(self, pairs) -> int:
        """Persist learned YouTube upload dates ([{youtube_id, published_at}, …]).
        Idempotent; only stores non-empty dates. Returns rows written."""
        rows = [(p.get("youtube_id"), p.get("published_at")) for p in (pairs or [])
                if p.get("youtube_id") and p.get("published_at")]
        if not rows:
            return 0
        conn = self._get_connection()
        try:
            conn.executemany(
                "INSERT INTO youtube_video_dates (youtube_id, published_at) VALUES (?, ?) "
                "ON CONFLICT(youtube_id) DO UPDATE SET published_at=excluded.published_at", rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_video_dates(self, video_ids) -> dict:
        """{youtube_id: published_at} for cached ids — hydrates channel year-seasons."""
        out: dict = {}
        ids = [str(x) for x in (video_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT youtube_id, published_at FROM youtube_video_dates WHERE youtube_id IN ({ph})", chunk):
                    if r["published_at"]:
                        out[r["youtube_id"]] = r["published_at"]
            return out
        finally:
            conn.close()

    def wishlisted_video_ids_for_channel(self, channel_id) -> list:
        """The youtube video ids wished under a channel (the per-video date fallback set)."""
        if not channel_id:
            return []
        conn = self._get_connection()
        try:
            return [r["source_id"] for r in conn.execute(
                "SELECT source_id FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (str(channel_id),))]
        finally:
            conn.close()

    def mark_channel_dates_enriched(self, channel_id, date_count=0, method="innertube") -> None:
        """Record that the background enricher swept this channel (skip re-sweeps).
        ``method`` tags which source produced the dates; legacy rows have NULL and
        get re-enriched once so they upgrade to the InnerTube catalog."""
        if not channel_id:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_channel_enrichment (channel_id, enriched_at, date_count, method) "
                "VALUES (?, CURRENT_TIMESTAMP, ?, ?) ON CONFLICT(channel_id) DO UPDATE SET "
                "enriched_at=CURRENT_TIMESTAMP, date_count=excluded.date_count, method=excluded.method",
                (str(channel_id), int(date_count or 0), method or None))
            conn.commit()
        finally:
            conn.close()

    def channel_dates_enriched_recently(self, channel_id, within_hours=24) -> bool:
        """True if the channel was date-enriched within the window (don't re-sweep).
        Coverage-aware: a run that produced FEW dates (proxies were down) retries
        soon instead of being locked out for the full window — so the catalog
        actually fills in once a source works."""
        if not channel_id:
            return False
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT enriched_at, date_count, method FROM youtube_channel_enrichment WHERE channel_id=?",
                (str(channel_id),)).fetchone()
            if not row or not row["enriched_at"]:
                return False
            # Legacy rows (enriched before InnerTube, method NULL) → always re-enrich
            # once so they upgrade to the full catalog, then settle under the window.
            if not row["method"]:
                return False
            try:
                when = datetime.strptime(row["enriched_at"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return False
            now = datetime.now(timezone.utc).replace(tzinfo=None)   # naive UTC, matches CURRENT_TIMESTAMP
            # Good coverage → skip for the full window; thin result → retry in 15 min.
            window = within_hours if (row["date_count"] or 0) >= 15 else 0.25
            return (now - when) < timedelta(hours=window)
        finally:
            conn.close()

    # ── Remembered channel catalog + metadata (cache-first channel pages) ──────
    def cache_channel_videos(self, channel_id, videos) -> int:
        """Remember a channel's videos (id/title/thumbnail). Upsert — refreshes
        title/thumbnail, never deletes (older pages stay remembered)."""
        cid = str(channel_id or "").strip()
        rows = [(cid, v.get("youtube_id"), v.get("title"), v.get("thumbnail_url"),
                 v.get("duration"), v.get("view_count"))
                for v in (videos or []) if isinstance(v, dict) and v.get("youtube_id")]
        if not cid or not rows:
            return 0
        conn = self._get_connection()
        try:
            conn.executemany(
                "INSERT INTO youtube_channel_videos (channel_id, youtube_id, title, thumbnail_url, "
                "duration, view_count) VALUES (?,?,?,?,?,?) ON CONFLICT(channel_id, youtube_id) DO UPDATE SET "
                "title=COALESCE(excluded.title, title), "
                "thumbnail_url=COALESCE(excluded.thumbnail_url, thumbnail_url), "
                "duration=COALESCE(excluded.duration, duration), "
                "view_count=COALESCE(excluded.view_count, view_count), "
                "cached_at=CURRENT_TIMESTAMP", rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_channel_videos(self, channel_id) -> list:
        """The remembered video list with dates merged from youtube_video_dates,
        newest first (undated last). [] if nothing is cached for the channel."""
        cid = str(channel_id or "").strip()
        if not cid:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT v.youtube_id, v.title, v.thumbnail_url, v.duration, v.view_count, "
                "d.published_at, s.like_count, s.dislike_count "
                "FROM youtube_channel_videos v "
                "LEFT JOIN youtube_video_dates d ON d.youtube_id = v.youtube_id "
                "LEFT JOIN youtube_video_stats s ON s.youtube_id = v.youtube_id "
                "WHERE v.channel_id=? "
                "ORDER BY (d.published_at IS NULL), d.published_at DESC, v.rowid",
                (cid,)).fetchall()
            return [{"youtube_id": r["youtube_id"], "title": r["title"], "thumbnail_url": r["thumbnail_url"],
                     "duration": r["duration"], "view_count": r["view_count"], "published_at": r["published_at"],
                     "like_count": r["like_count"], "dislike_count": r["dislike_count"]}
                    for r in rows]
        finally:
            conn.close()

    def cache_channel_meta(self, channel_id, meta) -> None:
        """Remember a channel's header metadata (avatar/subs/tags/…) for instant re-open."""
        cid = str(channel_id or "").strip()
        if not cid or not isinstance(meta, dict):
            return
        import json
        tags = meta.get("tags")
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_channel_meta (channel_id, title, handle, description, "
                "avatar_url, banner_url, subscriber_count, view_count, tags, cached_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(channel_id) DO UPDATE SET "
                "title=COALESCE(excluded.title, title), handle=COALESCE(excluded.handle, handle), "
                "description=COALESCE(excluded.description, description), "
                "avatar_url=COALESCE(excluded.avatar_url, avatar_url), "
                "banner_url=COALESCE(excluded.banner_url, banner_url), "
                "subscriber_count=COALESCE(excluded.subscriber_count, subscriber_count), "
                "view_count=COALESCE(excluded.view_count, view_count), "
                "tags=COALESCE(excluded.tags, tags), cached_at=CURRENT_TIMESTAMP",
                (cid, meta.get("title"), meta.get("handle"), meta.get("description"),
                 meta.get("avatar_url"), meta.get("banner_url"),
                 meta.get("subscriber_count"), meta.get("view_count"),
                 json.dumps(tags) if tags else None))
            conn.commit()
        finally:
            conn.close()

    def get_channel_meta(self, channel_id):
        """The remembered channel metadata dict (tags decoded), or None."""
        cid = str(channel_id or "").strip()
        if not cid:
            return None
        import json
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM youtube_channel_meta WHERE channel_id=?", (cid,)).fetchone()
            if not r:
                return None
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
            except (ValueError, TypeError):
                d["tags"] = []
            return d
        finally:
            conn.close()

    def youtube_wishlist_counts(self) -> dict:
        """{'channel': n distinct channels, 'video': n videos} in the wishlist."""
        conn = self._get_connection()
        try:
            video = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='video'").fetchone()["c"]
            channel = conn.execute(
                "SELECT COUNT(DISTINCT parent_source_id) c FROM video_wishlist WHERE kind='video'").fetchone()["c"]
            return {"channel": channel, "video": video}
        finally:
            conn.close()

    def query_youtube_wishlist(self, *, search=None, sort="added", page=1, limit=60) -> dict:
        """Wished YouTube videos shaped exactly like the TV nebula: channel = show,
        YEAR = season, video = episode. Each channel returns ``seasons`` grouped by
        upload year (newest first), videos as episodes (newest first within a year).
        ``sort`` ∈ added | oldest | title | wanted. {items, pagination} like query_wishlist."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        s = (search or "").strip()
        conn = self._get_connection()
        try:
            where, args = ["kind='video'"], []
            if s:
                where.append("(title LIKE ? COLLATE NOCASE OR episode_title LIKE ? COLLATE NOCASE)")
                args += ["%" + s + "%", "%" + s + "%"]
            wsql = " WHERE " + " AND ".join(where)
            total = conn.execute(
                "SELECT COUNT(DISTINCT parent_source_id) c FROM video_wishlist" + wsql, args).fetchone()["c"]
            order = {"title": "title COLLATE NOCASE", "wanted": "video_count DESC, last_added DESC",
                     "oldest": "last_added ASC", "added": "last_added DESC"}.get(sort, "last_added DESC")
            chan_rows = conn.execute(
                "SELECT parent_source_id, MAX(tmdb_id) AS surrogate, MAX(title) AS title, "
                "MAX(poster_url) AS poster_url, COUNT(*) AS video_count, "
                "SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) AS done, "
                "MAX(date_added) AS last_added "
                "FROM video_wishlist" + wsql +
                " GROUP BY parent_source_id ORDER BY " + order + " LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
            items = []
            for cr in chan_rows:
                vids = conn.execute(
                    "SELECT source_id, episode_title, still_url, episode_overview, air_date, status "
                    "FROM video_wishlist WHERE kind='video' AND parent_source_id=? "
                    "ORDER BY (air_date IS NULL), air_date DESC, id DESC", (cr["parent_source_id"],)).fetchall()
                # group by upload year → "seasons"; newest video in a year = episode 1
                by_year: dict = {}
                for v in vids:
                    ad = v["air_date"]
                    yr = int(ad[:4]) if ad and len(ad) >= 4 and ad[:4].isdigit() else 0
                    by_year.setdefault(yr, []).append(v)
                seasons = []
                for yr in sorted(by_year, reverse=True):   # newest year first
                    eps = []
                    for i, v in enumerate(by_year[yr]):
                        eps.append({"episode_number": i + 1, "title": v["episode_title"],
                                    "still_url": v["still_url"], "overview": v["episode_overview"],
                                    "air_date": v["air_date"], "status": v["status"],
                                    "source_id": v["source_id"]})
                    poster = next((e["still_url"] for e in eps if e["still_url"]), cr["poster_url"])
                    seasons.append({"season_number": yr, "year": yr, "poster_url": poster, "episodes": eps})
                items.append({
                    "kind": "channel", "source": "youtube",
                    "tmdb_id": cr["surrogate"], "youtube_id": cr["parent_source_id"],
                    "title": cr["title"], "poster_url": cr["poster_url"],
                    "wanted": cr["video_count"], "done": cr["done"] or 0, "seasons": seasons})
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        return {"items": items, "pagination": {
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
            "trakt_rating": m["trakt_rating"], "trakt_votes": m["trakt_votes"],
            "wikidata_url": m["wikidata_url"],
            "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": m["tmdb_id"], "imdb_id": m["imdb_id"],
            "has_poster": bool(m["poster_url"]), "has_backdrop": bool(m["backdrop_url"]),
            "logo": m["logo_url"],
            "subtitle_langs": _subtitle_langs_list(m["subtitle_langs"]),
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
