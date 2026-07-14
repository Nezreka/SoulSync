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


def _publish_video_event(event_type: str, data: dict) -> None:
    """Relay a library change to the video automation event bus. The DB layer is
    the one spine every wishlist/watchlist write flows through, so publishing
    here means no caller can forget. Lazy import; a missing forwarder (tests,
    early startup) makes it a no-op — never disturbs the write itself."""
    try:
        from core.video.download_events import publish
        publish(event_type, data)
    except Exception:   # noqa: BLE001
        logger.debug("video event publish failed for %s", event_type, exc_info=True)

# Bump when video_schema.sql changes in a way worth recording. Stored in
# PRAGMA user_version as a backstop indicator (nothing gates on it yet).
SCHEMA_VERSION = 41   # v41: one-time details_synced heal (status-less burn victims); v40: video_watchlist.lookback_years; v39: video_wishlist.release_date

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

# Fields a USER may edit (Manage sidebar) per table. 'genres' is a pseudo-field
# routed to the link tables. An edited field is recorded in the row's
# ``locked_fields`` JSON list and from then on belongs to the user: scans
# (every mode, including FULL) and enrichment leave it alone until released.
_USER_EDITABLE = {
    "movies": {"title", "sort_title", "year", "overview", "tagline", "content_rating",
               "studio", "genres"},
    "shows": {"title", "sort_title", "year", "overview", "tagline", "content_rating",
              "network", "genres"},
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
    "streaming": {  # TMDB watch providers (movies + shows), by tmdb id
        "movie": ("movies", "streaming_status", "streaming_attempted", "tmdb_id IS NOT NULL"),
        "show": ("shows", "streaming_status", "streaming_attempted", "tmdb_id IS NOT NULL"),
    },
    "mediastinger": {  # TMDB keywords -> after/during-credits scene, by tmdb id
        "movie": ("movies", "mediastinger_status", "mediastinger_attempted", "tmdb_id IS NOT NULL"),
        "show": ("shows", "mediastinger_status", "mediastinger_attempted", "tmdb_id IS NOT NULL"),
    },
    "awards": {  # OMDb Awards string -> oscar/winner flag, by imdb id
        "movie": ("movies", "awards_status", "awards_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "awards_status", "awards_attempted", "imdb_id IS NOT NULL"),
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
    "streaming": {"streaming"},
    "mediastinger": {"mediastinger"},
    "awards": {"awards"},
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
    # live import sub-phase + progress so the card shows 'Merging…' then 'Moving X%'
    # instead of a blank 100% spinner during post-download processing.
    ("video_downloads", "import_phase", "TEXT"),
    ("video_downloads", "import_progress", "REAL DEFAULT 0"),
    # torrent/usenet client tracking id (qBittorrent hash / SAB nzo_id) so the monitor can
    # poll the shared torrent/usenet client for a non-Soulseek grab's progress + completion.
    ("video_downloads", "client_ref", "TEXT"),
    # the ratingKey of the overlay poster we last uploaded to Plex, so a re-apply can delete
    # THAT one before uploading the new render (Plex accumulates uploads otherwise).
    ("overlay_apply", "plex_poster_key", "TEXT"),
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
    ("episodes", "added_at", "TEXT"),   # server add-date, so a show ranks "recently added" on a new episode
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
    ("video_wishlist", "release_date", "TEXT"),   # movie release date — gate: don't search until near release
    ("video_watchlist", "lookback_years", "INTEGER"),   # per-person back-catalog window: NULL/0=forward-only, N=years, -1=everything
    ("video_wishlist", "still_url", "TEXT"),   # episode still thumbnail (captured at add time)
    ("video_wishlist", "season_poster_url", "TEXT"),   # the episode's season poster
    ("video_wishlist", "episode_overview", "TEXT"),    # episode synopsis
    # rich movie detail captured at add time (backdrop, overview, genres, runtime, rating,
    # top cast, director, release_date, + provenance) so the wishlist renders a full card
    # without re-fetching. JSON blob; NULL on rows added before this / by lean paths.
    ("video_wishlist", "detail_json", "TEXT"),
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
    # YouTube retention: owning channel + upload date (group + age episodes) and a prune
    # marker — retention deletes the FILE but keeps the row (so the scan won't re-download it).
    ("video_download_history", "channel_id", "TEXT"),
    ("video_download_history", "published_at", "TEXT"),
    ("video_download_history", "pruned_at", "TEXT"),
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
    # Streaming provider (TMDB watch providers) for the Streaming overlay badge
    ("movies", "streaming", "TEXT"),
    ("movies", "streaming_status", "TEXT"), ("movies", "streaming_attempted", "TEXT"),
    ("shows", "streaming", "TEXT"),
    ("shows", "streaming_status", "TEXT"), ("shows", "streaming_attempted", "TEXT"),
    # Mediastinger (TMDB keywords → after/during-credits scene) for its overlay badge
    ("movies", "mediastinger", "INTEGER"),
    ("movies", "mediastinger_status", "TEXT"), ("movies", "mediastinger_attempted", "TEXT"),
    ("shows", "mediastinger", "INTEGER"),
    ("shows", "mediastinger_status", "TEXT"), ("shows", "mediastinger_attempted", "TEXT"),
    # Aspect ratio (from the media file / server), for the Aspect overlay badge
    ("media_files", "aspect", "TEXT"),
    # Awards (OMDb Awards string → oscar/winner flag) for the Awards overlay badge
    ("movies", "awards", "TEXT"),
    ("movies", "awards_status", "TEXT"), ("movies", "awards_attempted", "TEXT"),
    ("shows", "awards", "TEXT"),
    ("shows", "awards_status", "TEXT"), ("shows", "awards_attempted", "TEXT"),
    # DeArrow crowd-sourced better titles for cached YouTube videos
    ("youtube_video_stats", "dearrow_title", "TEXT"),
    ("youtube_video_stats", "dearrow_status", "TEXT"),
    # Seasonal collection windows (MM-DD): in-window syncs, out-of-window removes ours
    ("collection_definitions", "window_start", "TEXT"),
    ("collection_definitions", "window_end", "TEXT"),
    # Plex collection mode (hide members in library view, etc.); NULL = leave alone
    ("collection_definitions", "collection_mode", "TEXT"),
    # Watch state from the server (drives 'watched' smart-collection rules)
    ("movies", "play_count", "INTEGER"),
    ("movies", "last_viewed_at", "TEXT"),
    ("shows", "watched_episodes", "INTEGER"),
    # imdb_tmdb_map poster art — the table briefly shipped without these, and
    # CREATE TABLE IF NOT EXISTS never upgrades an existing table's shape.
    ("imdb_tmdb_map", "movie_poster", "TEXT"),
    ("imdb_tmdb_map", "show_poster", "TEXT"),
    # Filtered overlay targeting: apply a scope's template only to items
    # matching a smart-rule definition (JSON, same language as collections)
    ("overlay_assignment", "filter", "TEXT"),
    ("youtube_video_stats", "dearrow_attempted", "TEXT"),
    # TMDB details backfill: the server pre-matches shows/movies (so the matcher
    # skips them) but never supplies details-only fields like `status` (airing vs
    # ended) — which the watchlist's airing-default depends on. This marker drives a
    # one-time per-item detail re-fetch that fills those gaps. Starts 0 = needs it.
    ("shows", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("movies", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
    # User-edited metadata locks (JSON list of field names, e.g. '["title","genres"]').
    # A locked field is owned by the user: scan upserts and enrichment skip it.
    ("movies", "locked_fields", "TEXT"),
    ("shows", "locked_fields", "TEXT"),
    # 'Cleared' download-history rows: hidden from the History modal but KEPT —
    # YouTube completed rows are the ownership ledger (scan dedup, retention,
    # Channels tab); a user clear must never delete the facts.
    ("video_download_history", "cleared_at", "TEXT"),
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
            prev_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.executescript(schema)
            self._ensure_columns(conn)
            self._ensure_indexes(conn)
            self._seed_named_links_from_scalar(conn)
            self._run_data_migrations(conn, prev_version)
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
        # Recently-Added ranks shows by their newest episode's add-date — MAX(added_at)
        # per show over a 200k-episode table, so index it.
        "CREATE INDEX IF NOT EXISTS idx_episodes_show_added ON episodes(show_id, added_at)",
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

    @staticmethod
    def _run_data_migrations(conn, prev_version: int) -> None:
        """One-time data fixes, gated on the PRAGMA user_version the DB carried
        BEFORE this boot (fresh DBs start at 0 with empty tables, so every step
        is a no-op there)."""
        if prev_version < 41:
            # v41 heal: the details backfill used to swallow failed TMDB calls
            # (429/5xx/timeout) into empty metadata and still mark
            # details_synced=1 — permanently, since the queue only picks
            # details_synced=0. Those burn victims sit matched-but-status-less
            # forever: no watchlist button (unknown status reads as ended), no
            # airing refresh, no calendar. The call-failure propagation is fixed
            # (clients.py raises now); this re-queues the already-burned rows for
            # one fresh attempt. Genuine TMDB 404s just settle back to synced.
            for tbl in ("shows", "movies"):
                conn.execute(
                    f"UPDATE {tbl} SET details_synced=0 "
                    f"WHERE tmdb_id IS NOT NULL AND details_synced=1 "
                    f"AND (status IS NULL OR TRIM(status) = '')")

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

        def build(include_ids, locked):
            sets = [f"{sc}=?", f"{ac}=CURRENT_TIMESTAMP"]
            params = [status]
            if matched and external_id is not None and include_ids:
                sets.append(f"{idc}=?")
                params.append(external_id)
            for col, val in (metadata or {}).items():
                if val is None or col not in allowed or col in locked:
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
            # User-locked fields belong to the user — enrichment never touches
            # them, not even to fill a deliberately-blanked value.
            locked = self._locked_fields_set(conn, tbl, item_id)
            sql, params = build(True, locked)
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError:
                conn.rollback()
                sql, params = build(False, locked)   # keep existing id, just record status/metadata
                conn.execute(sql, params)
            # Genres backfill — only when the item has none yet (enrichment fills
            # the gap the server didn't). Written to the normalised link tables.
            genres = (metadata or {}).get("genres")
            link = {"movies": ("movie_genres", "movie_id"),
                    "shows": ("show_genres", "show_id")}.get(tbl)
            if matched and genres and link and "genres" not in locked:
                lt, oc = link
                has = conn.execute(f"SELECT 1 FROM {lt} WHERE {oc}=? LIMIT 1", (item_id,)).fetchone()
                if not has:
                    self._set_genres(conn, lt, oc, item_id, genres)
            # Studios / networks — TMDB is the authority for the FULL company list (the media
            # server only exposes one), so REPLACE the link table on every match rather than
            # gap-fill. This is what makes studio/network collections complete (a title made by
            # several companies lands in every one). The scalar studio/network column is left as
            # the server/first value for display.
            if matched:
                studios = (metadata or {}).get("studios")
                if tbl == "movies" and studios and "studio" not in locked:
                    self._set_named_links(conn, "movie_studios", "movie_id", "studios", "studio_id",
                                          item_id, studios)
                networks = (metadata or {}).get("networks")
                if tbl == "shows" and networks and "network" not in locked:
                    self._set_named_links(conn, "show_networks", "show_id", "networks", "network_id",
                                          item_id, networks)
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
            # "title" is what the worker base class logs/displays (movie/show
            # feeds use it) — the old name-only shape made every YouTube
            # enrichment log as "Enriched video 'None'".
            return {"kind": "video", "id": row["youtube_id"], "title": row["title"],
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
        """Title/year/tmdb_id/tvdb_id for one show — for on-demand (lazy) art refresh
        (tvdb_id lets the refresh gap-fill episode metadata TMDB is missing)."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT title, year, tmdb_id, tvdb_id FROM shows WHERE id=?",
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

    def show_tmdb_id(self, show_id):
        """The TMDB id for a library show row (so a failed grab can be re-wishlisted)."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id FROM shows WHERE id=?", (int(show_id),)).fetchone()
            return r["tmdb_id"] if r and r["tmdb_id"] else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def movie_tmdb_id(self, movie_id):
        """The TMDB id for a library movie row (so a failed grab can be re-wishlisted)."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id FROM movies WHERE id=?", (int(movie_id),)).fetchone()
            return r["tmdb_id"] if r and r["tmdb_id"] else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def owned_episode_keys(self, show_id) -> set:
        """(season_number, episode_number) pairs already in the library for a show —
        so a season-pack grab can skip episodes you already own."""
        conn = self._get_connection()
        try:
            return {(r["season_number"], r["episode_number"]) for r in conn.execute(
                "SELECT season_number, episode_number FROM episodes WHERE show_id=? AND has_file=1",
                (int(show_id),))}
        except (sqlite3.Error, ValueError, TypeError):
            return set()
        finally:
            conn.close()

    # ── Discover ignore list ("Not interested") ──────────────────────────────
    def add_ignored(self, kind: str, tmdb_id, title=None, year=None, poster_url=None) -> bool:
        """Hide a title from Discover (movie/show level). Idempotent."""
        if kind not in ("movie", "show") or tmdb_id is None:
            return False
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO video_ignored (kind, tmdb_id, title, year, poster_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (kind, int(tmdb_id), title, int(year) if year else None, poster_url),
                )
                conn.commit()
                return True
        except (sqlite3.Error, TypeError, ValueError) as e:
            logger.debug(f"add_ignored failed: {e}")
            return False

    def remove_ignored(self, kind: str, tmdb_id) -> bool:
        """Un-hide a title."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM video_ignored WHERE kind=? AND tmdb_id=?",
                             (kind, int(tmdb_id)))
                conn.commit()
                return True
        except (sqlite3.Error, TypeError, ValueError) as e:
            logger.debug(f"remove_ignored failed: {e}")
            return False

    def list_ignored(self) -> list:
        """All ignored titles, most-recently-added first — drives the manage modal."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT kind, tmdb_id, title, year, poster_url FROM video_ignored "
                    "ORDER BY added_at DESC"
                ).fetchall()
                return [{"kind": r["kind"], "tmdb_id": r["tmdb_id"], "title": r["title"],
                         "year": r["year"], "poster": r["poster_url"]} for r in rows]
        except sqlite3.Error:
            return []

    def ignored_keys(self) -> set:
        """Set of 'kind:tmdb_id' strings for fast Discover filtering."""
        try:
            with self._get_connection() as conn:
                return {f"{r['kind']}:{r['tmdb_id']}"
                        for r in conn.execute("SELECT kind, tmdb_id FROM video_ignored")}
        except sqlite3.Error:
            return set()

    def movies_missing_collection(self, server_source=None, limit: int = 20) -> list:
        """Owned, TMDB-matched movies whose franchise (tmdb_collection_id) hasn't been
        backfilled yet — drives the lazy collection-id backfill. Returns [{id, tmdb_id}].

        Also re-checks rows the OLD buggy backfill zeroed: id=0 WITH a franchise name is a
        real franchise (e.g. Jurassic Park) that got mis-written as 0 — genuinely-no-franchise
        rows are id=0 with a NULL name, so they stay excluded and aren't re-fetched forever."""
        sql = ("SELECT id, tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL "
               "AND (tmdb_collection_id IS NULL "
               "     OR (tmdb_collection_id = 0 AND tmdb_collection_name IS NOT NULL))")
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        sql += " LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"id": r["id"], "tmdb_id": r["tmdb_id"]} for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def set_movie_collection(self, movie_id: int, collection_id, name) -> None:
        """Persist a movie's TMDB collection id/name (backfill). collection_id may be
        None — recorded as 0 so the row isn't re-checked forever (a movie with no
        franchise). 0 is excluded from the gap rails."""
        conn = self._get_connection()
        try:
            # A 0 (no-franchise) row must not carry a name — otherwise it looks like a
            # mis-zeroed franchise and gets re-fetched forever.
            conn.execute("UPDATE movies SET tmdb_collection_id=?, tmdb_collection_name=? WHERE id=?",
                         (int(collection_id) if collection_id else 0,
                          name if collection_id else None, int(movie_id)))
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def owned_movie_collections(self, server_source=None, limit: int = 12) -> list:
        """Franchises the user has STARTED (owns >=1 movie in), most-invested first —
        drives the 'Complete your collections' gap rails. Returns
        [{collection_id, name, owned_count}]."""
        sql = ("SELECT tmdb_collection_id AS cid, "
               "MAX(tmdb_collection_name) AS name, COUNT(*) AS c "
               "FROM movies WHERE has_file=1 AND tmdb_collection_id IS NOT NULL "
               "AND tmdb_collection_id != 0")
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

    def stale_enriched_items(self, limit: int = 500, movie_days: int = 30,
                             show_days: int = 30) -> list[dict]:
        """The N matched library items whose metadata is stalest — for the periodic
        re-enrichment automation. Oldest-refreshed first, and ONLY items last touched
        beyond their per-kind staleness floor (so a run never re-pulls something already
        fresh). ``tmdb_last_attempted`` is stamped by every enrichment_apply — initial
        match, lazy on-view refresh, or a prior re-enrich pass — so it doubles as the
        'last refreshed' cursor without a new column.

        Movies and shows each carry their own floor (both default 30 — roughly monthly;
        metadata drifts slowly, so once a month is plenty). An episode rides along when
        its show is refreshed (refresh_show_art cascades the episode list), so there's no
        separate episode queue — episodes stay as fresh as their show's monthly pass.

        Returns ``[{kind, id, title, last_attempted}]`` (kind ∈ movie/show). A NULL
        last_attempted (matched but never stamped — legacy rows) sorts first."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 500

        def _cutoff(days, default):
            try:
                d = max(0, int(days))
            except (TypeError, ValueError):
                d = default
            return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")

        m_cut = _cutoff(movie_days, 30)
        s_cut = _cutoff(show_days, 30)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, title, last_attempted FROM ("
                "  SELECT 'movie' AS kind, id, title, tmdb_last_attempted AS last_attempted"
                "    FROM movies"
                "   WHERE tmdb_match_status='matched'"
                "     AND (tmdb_last_attempted IS NULL OR tmdb_last_attempted < ?)"
                "  UNION ALL"
                "  SELECT 'show' AS kind, id, title, tmdb_last_attempted AS last_attempted"
                "    FROM shows"
                "   WHERE tmdb_match_status='matched'"
                "     AND (tmdb_last_attempted IS NULL OR tmdb_last_attempted < ?)"
                ") ORDER BY last_attempted ASC LIMIT ?",
                (m_cut, s_cut, limit)).fetchall()
            return [dict(r) for r in rows]
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

    def queue_detail_resync(self, kind: str = "all") -> int:
        """Reset ``details_synced=0`` for on-server, TMDB-matched titles so the detail-sync
        worker re-pulls their FULL TMDB metadata (studios/networks/overview/…) and re-applies
        it through enrichment_apply. This is how an existing library gets the complete
        multi-company studio/network data (the server scan only knows one). Returns the count
        queued; the already-running worker drains it in the background."""
        tables = []
        if kind in ("all", "movie"):
            tables.append("movies")
        if kind in ("all", "show"):
            tables.append("shows")
        total = 0
        conn = self._get_connection()
        try:
            for tbl in tables:
                cur = conn.execute(
                    f"UPDATE {tbl} SET details_synced=0 "
                    f"WHERE tmdb_id IS NOT NULL AND server_id IS NOT NULL AND TRIM(server_id) != ''")
                total += cur.rowcount
            conn.commit()
            return total
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
                  "candidates", "search_ctx", "tried_queries", "tried_files", "attempts",
                  "client_ref")   # torrent/usenet client tracking id

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
                "SELECT * FROM video_downloads WHERE status IN "
                "('queued', 'downloading', 'importing', 'searching') ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_active_youtube_downloads(self) -> int:
        """How many YouTube downloads are ACTIVELY in flight right now (fetching OR importing,
        not queued) — the concurrency gauge the wishlist pump checks before starting more.
        Counting 'importing' too keeps the worker's slot held while it moves into the library."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT COUNT(*) AS n FROM video_downloads "
                             "WHERE source='youtube' AND status IN ('downloading', 'importing')").fetchone()
            return int(r["n"]) if r else 0
        finally:
            conn.close()

    def claim_next_youtube_queued(self) -> dict | None:
        """Atomically take the oldest QUEUED YouTube download and flip it to
        'downloading', returning the row (or None if none are queued). The
        ``WHERE status='queued'`` guard makes the claim race-safe — two workers finishing
        at once can't grab the same row."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM video_downloads WHERE source='youtube' AND status='queued' "
                "ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            cur = conn.execute(
                "UPDATE video_downloads SET status='downloading', updated_at=datetime('now') "
                "WHERE id=? AND status='queued'", (row["id"],))
            conn.commit()
            return dict(row) if cur.rowcount else None   # rowcount 0 = lost the race
        finally:
            conn.close()

    def downloaded_youtube_video_ids(self) -> list:
        """YouTube video ids already successfully downloaded (from the permanent history).
        The watchlist scans exclude these — a completed video is removed from the wishlist
        on download, so without this it would be re-added (it's still in the channel's recent
        uploads / last-N net) and re-downloaded on every scan."""
        conn = self._get_connection()
        try:
            return [r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL")]
        finally:
            conn.close()

    def owned_youtube_video_ids(self) -> list:
        """YouTube video ids whose file is (believed) still ON DISK — pruned rows excluded.
        The UI-annotation variant of the above: a 'Downloaded' badge means *you have this
        now*, while the scan dedup deliberately keeps pruned rows (had-it-once must never
        re-download)."""
        conn = self._get_connection()
        try:
            return [r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL "
                "AND pruned_at IS NULL")]
        finally:
            conn.close()

    def youtube_ledger_rows(self) -> list:
        """Every still-on-disk (not pruned) completed YouTube grab with a recorded file
        path — the set the ghost-cleanup repair job path-checks. Rows without a
        dest_path can't be verified and are left out."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, media_id, title, channel_id, dest_path, published_at, "
                "completed_at FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND pruned_at IS NULL "
                "AND media_id IS NOT NULL "
                "AND dest_path IS NOT NULL AND dest_path != '' "
                "ORDER BY channel_id, published_at DESC")]
        finally:
            conn.close()

    def youtube_failed_counts(self) -> dict:
        """{video_id: failed-attempt count} from the permanent history — so the processor
        can stop re-grabbing a video that keeps failing (deleted / private / geo- or
        age-gated) instead of retrying it every run forever."""
        conn = self._get_connection()
        try:
            return {r["media_id"]: r["n"] for r in conn.execute(
                "SELECT media_id, COUNT(*) AS n FROM video_download_history "
                "WHERE source='youtube' AND outcome='failed' AND media_id IS NOT NULL "
                "GROUP BY media_id")}
        finally:
            conn.close()

    def youtube_video_detail(self, youtube_id) -> dict | None:
        """Cached metadata for one YouTube video (title / thumbnail / duration / views) — the
        extra detail the download drawer shows. None if it was never cached by a channel scan."""
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT channel_id, title, thumbnail_url, duration, view_count "
                "FROM youtube_channel_videos WHERE youtube_id=? LIMIT 1", (youtube_id,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    # ── YouTube retention (auto-clean old channel episodes) ──────────────────────
    def youtube_channels_with_downloads(self) -> list:
        """Channel ids that have at least one still-on-disk (not pruned) downloaded episode —
        the set the retention automation iterates."""
        conn = self._get_connection()
        try:
            return [r["channel_id"] for r in conn.execute(
                "SELECT DISTINCT channel_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND channel_id IS NOT NULL "
                "AND pruned_at IS NULL")]
        finally:
            conn.close()

    def youtube_channel_episodes(self, channel_id) -> list:
        """A channel's downloaded, still-on-disk episodes (newest upload first) for retention —
        each carries the history id, file path, upload date + filename (date fallback)."""
        if not channel_id:
            return []
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, media_id, title, dest_path, filename, published_at, completed_at, size_bytes "
                "FROM video_download_history WHERE source='youtube' AND outcome='completed' "
                "AND channel_id=? AND pruned_at IS NULL ORDER BY published_at DESC, completed_at DESC",
                (str(channel_id),))]
        finally:
            conn.close()

    def mark_download_pruned(self, history_id, when) -> bool:
        """Flag a history row as retention-pruned (file deleted) — kept so the scan's dedup
        still excludes it (no re-download). Returns True if a row was updated."""
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_download_history SET pruned_at=? "
                               "WHERE id=? AND pruned_at IS NULL", (when, int(history_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
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
        """Patch a download row; ``updated_at`` is always bumped. Underscore-
        prefixed keys are transient patch metadata (e.g. ``_upgraded`` for the
        event bus), not columns — stripped here so producers can ride the patch."""
        fields = {k: v for k, v in fields.items() if not k.startswith("_")}
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
        # season/episode + (youtube) channel/upload-date live in the search_ctx JSON.
        sn = en = channel_id = published_at = None
        ctx = row.get("search_ctx")
        if ctx:
            try:
                ctx = json.loads(ctx) if isinstance(ctx, str) else (ctx or {})
                sn, en = ctx.get("season"), ctx.get("episode")
                channel_id, published_at = ctx.get("channel_id"), ctx.get("published_at")
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
                        outcome, error, grabbed_at, completed_at, channel_id, published_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get("id"), kind, media_type, row.get("title"), row.get("year"), sn, en,
                 rel, row.get("source"), row.get("username"), fn, row.get("dest_path"),
                 int(row.get("size_bytes") or 0), row.get("quality_label"),
                 self._parse_resolution(rel, fn, row.get("quality_label")), self._codec(rel, fn),
                 row.get("media_id"), row.get("media_source"), row.get("poster_url"),
                 outcome, row.get("error"), row.get("created_at"),
                 row.get("completed_at"), channel_id, published_at))
            # Opportunistic LOG trim (the user-visible diary keeps a rolling year;
            # the YouTube ownership ledger is exempt and lives forever).
            conn.execute(
                f"DELETE FROM video_download_history WHERE NOT {self._YT_LEDGER} "
                "AND COALESCE(completed_at, grabbed_at) < datetime('now', '-365 days')")
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
        # Cleared ledger rows are hidden from the modal (the facts live on).
        where, args = ["cleared_at IS NULL"], []
        # Tabs classify by kind+source, not raw kind: episodes store kind='episode'
        # (not 'show'), and YouTube grabs carry source='youtube'. So 'TV' = anything
        # that isn't a movie or a YouTube grab; 'youtube' = source/kind youtube.
        _yt = "(COALESCE(source,'') = 'youtube' OR kind = 'youtube')"
        if kind == "movie":
            where.append("kind = 'movie' AND NOT " + _yt)
        elif kind in ("show", "tv"):
            where.append("kind <> 'movie' AND NOT " + _yt)
        elif kind == "youtube":
            where.append(_yt)
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
        """{movie, show(=TV: episodes + show/season packs), youtube, total} of completed
        grabs (for the modal tabs/badge). Classifies by kind+source so episodes (kind=
        'episode') land under TV and YouTube (source='youtube') gets its own bucket —
        the old version counted only kind='movie'/'show', so TV + YouTube vanished."""
        conn = self._get_connection()
        try:
            _yt = "(COALESCE(source,'') = 'youtube' OR kind = 'youtube')"
            row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN " + _yt + " THEN 1 ELSE 0 END) AS yt, "
                "SUM(CASE WHEN kind = 'movie' AND NOT " + _yt + " THEN 1 ELSE 0 END) AS mv, "
                "SUM(CASE WHEN kind <> 'movie' AND NOT " + _yt + " THEN 1 ELSE 0 END) AS tv, "
                "COUNT(*) AS total FROM video_download_history "
                "WHERE outcome='completed' AND cleared_at IS NULL").fetchone()
            return {"movie": row["mv"] or 0, "show": row["tv"] or 0,
                    "youtube": row["yt"] or 0, "total": row["total"] or 0}
        finally:
            conn.close()

    # ── release blocklist (never re-grab a proven-bad release) ───────────────
    def add_video_blocklist(self, row: dict) -> int:
        """Block one exact release file. Idempotent on (username, filename).
        Returns the row id (existing or new), 0 on bad input."""
        username, filename = (row or {}).get("username"), (row or {}).get("filename")
        if not username or not filename:
            return 0
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_blocklist
                       (kind, title, media_id, media_source, season_number, episode_number,
                        username, filename, release_title, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(username, filename) DO UPDATE SET
                       reason=COALESCE(excluded.reason, video_blocklist.reason)""",
                (row.get("kind"), row.get("title"), row.get("media_id"),
                 row.get("media_source"), row.get("season_number"), row.get("episode_number"),
                 username, filename, row.get("release_title"), row.get("reason")))
            conn.commit()
            r = conn.execute("SELECT id FROM video_blocklist WHERE username=? AND filename=?",
                             (username, filename)).fetchone()
            return r["id"] if r else 0
        except sqlite3.Error:
            logger.exception("add_video_blocklist failed")
            return 0
        finally:
            conn.close()

    def video_blocklist_pairs(self) -> set:
        """{(username, filename)} for per-release candidate filtering — cheap enough to
        read per search/retry pass (the table stays small). Excludes the source-wide
        ('' filename) sentinels — those are matched by ``blocked_usernames`` instead."""
        conn = self._get_connection()
        try:
            return {(r["username"], r["filename"]) for r in conn.execute(
                "SELECT username, filename FROM video_blocklist WHERE COALESCE(filename,'') <> ''")}
        finally:
            conn.close()

    def block_video_source(self, username, reason=None) -> int:
        """Block a whole uploader/peer: every release from this username is skipped by
        future searches (a SOURCE-wide block, stored with the '' filename sentinel so it
        dedupes on (username,'')). Returns the row id, 0 on an empty username."""
        username = (username or "").strip()
        if not username:
            return 0
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO video_blocklist (username, filename, reason) VALUES (?, '', ?) "
                "ON CONFLICT(username, filename) DO UPDATE SET "
                "reason=COALESCE(excluded.reason, video_blocklist.reason)",
                (username, reason or "Uploader blocked"))
            conn.commit()
            r = conn.execute("SELECT id FROM video_blocklist WHERE username=? AND filename=''",
                             (username,)).fetchone()
            return r["id"] if r else 0
        except sqlite3.Error:
            logger.exception("block_video_source failed")
            return 0
        finally:
            conn.close()

    def blocked_usernames(self) -> set:
        """Uploaders blocked source-wide (the '' filename sentinel) — every release from
        them is filtered out of search, on top of the per-release (username,filename)
        blocks. Read per search pass alongside ``video_blocklist_pairs``."""
        conn = self._get_connection()
        try:
            return {r["username"] for r in conn.execute(
                "SELECT username FROM video_blocklist WHERE COALESCE(filename,'') = '' AND username IS NOT NULL")}
        finally:
            conn.close()

    def list_video_blocklist(self) -> list:
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM video_blocklist ORDER BY created_at DESC, id DESC")]
        finally:
            conn.close()

    def remove_video_blocklist(self, row_id) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_blocklist WHERE id=?", (int(row_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            conn.close()

    def clear_video_blocklist(self) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_blocklist")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def delete_download_history(self, history_id) -> bool:
        """Forget one history grab — so the scans stop deduping against it and re-grab it
        (the 'Re-download' action after you've deleted the file). Returns True if removed."""
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_download_history WHERE id=?", (int(history_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            conn.close()

    # The YouTube OWNERSHIP LEDGER: completed grabs (incl. retention-pruned ones).
    # Scan dedup, retention, the Channels tab, the downloaded badges and the
    # ghost-cleanup repair job all depend on these rows never disappearing.
    # Dedup reads the whole ledger; UI truth (badges/counts) excludes pruned rows.
    _YT_LEDGER = "(source='youtube' AND outcome='completed')"

    def clear_download_history(self, kind=None) -> int:
        """The user-facing 'clear': the LOG deletes, the LEDGER only hides.
        YouTube completed rows are stamped ``cleared_at`` — they leave the
        History modal but keep every ownership fact (the user can clear all
        they want; the real history survives). Everything else — failures,
        cancellations, movie/TV rows (re-derivable from the server scan) —
        truly deletes. The per-row delete_download_history stays a REAL forget:
        that's the deliberate 'Re-download' action. Returns rows affected."""
        conn = self._get_connection()
        try:
            kf, args = ("", []) if kind not in ("movie", "show", "youtube") else \
                (" AND kind=?", [kind])
            hid = conn.execute(
                f"UPDATE video_download_history SET cleared_at=datetime('now') "
                f"WHERE {self._YT_LEDGER} AND cleared_at IS NULL{kf}", args).rowcount
            gone = conn.execute(
                f"DELETE FROM video_download_history WHERE NOT {self._YT_LEDGER}{kf}",
                args).rowcount
            conn.commit()
            return hid + gone
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
    def enrichment_coverage(self) -> dict:
        """How much of the library is matched + detail-enriched against TMDB / TVDB — powers
        the dashboard Studio cards' coverage bars (Overlay + Collection Studio need this
        metadata: posters, ratings, logos, studios/networks). ``tmdb_enriched`` = full detail
        pulled (``details_synced``); ``*_matched`` = an external id resolved. Purely read-only."""
        conn = self._get_connection()
        try:
            def n(sql):
                r = conn.execute(sql).fetchone()
                return int(r[0]) if r and r[0] is not None else 0
            return {
                "movies": {
                    "total": n("SELECT COUNT(*) FROM movies"),
                    "tmdb_matched": n("SELECT COUNT(*) FROM movies WHERE tmdb_id IS NOT NULL"),
                    "tmdb_enriched": n("SELECT COUNT(*) FROM movies WHERE details_synced=1"),
                },
                "shows": {
                    "total": n("SELECT COUNT(*) FROM shows"),
                    "tmdb_matched": n("SELECT COUNT(*) FROM shows WHERE tmdb_id IS NOT NULL"),
                    "tmdb_enriched": n("SELECT COUNT(*) FROM shows WHERE details_synced=1"),
                    "tvdb_matched": n("SELECT COUNT(*) FROM shows WHERE tvdb_id IS NOT NULL"),
                },
            }
        except sqlite3.Error:
            logger.exception("enrichment_coverage failed")
            return {"movies": {}, "shows": {}}
        finally:
            conn.close()

    def recently_added(self, server_source=None, limit=12) -> list:
        """Newest movies + shows for the dashboard's Recently Added row. A SHOW ranks by its
        NEWEST EPISODE's add-date (so a show with a freshly-added episode surfaces — matching
        Plex's own Recently Added), falling back to the show's own add-date. Movies rank by
        their add-date. [{kind, id, title, year, added_at}], newest first."""
        limit = max(1, min(50, int(limit)))
        m_where = "server_id IS NOT NULL AND server_id <> '' AND added_at IS NOT NULL"
        s_where = "s.server_id IS NOT NULL AND s.server_id <> ''"
        if server_source:
            m_where += " AND server_source = ?"
            s_where += " AND s.server_source = ?"
        p = ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, title, year, added_at FROM ("
                f"  SELECT 'movie' AS kind, id, title, year, added_at FROM movies WHERE {m_where}"
                "   UNION ALL "
                "  SELECT 'show' AS kind, s.id AS id, s.title AS title, s.year AS year, "
                "         COALESCE(MAX(e.added_at), s.added_at) AS added_at "
                "  FROM shows s LEFT JOIN episodes e ON e.show_id = s.id "
                f"  WHERE {s_where} GROUP BY s.id "
                "  HAVING COALESCE(MAX(e.added_at), s.added_at) IS NOT NULL"
                ") ORDER BY added_at DESC, id DESC LIMIT ?",
                p + p + [limit]).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("recently_added query failed")
            return []
        finally:
            conn.close()

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
    def _set_media_file(conn, owner_col: str, owner_id: int, files) -> None:
        """Replace the media_files row(s) for one owner — EVERY version the
        server reported (multiple copies/editions each get a row). Accepts a
        single file dict (legacy callers) or a list. owner_col is internal
        ('movie_id'|'episode_id'|'video_id'), never user input."""
        conn.execute(f"DELETE FROM media_files WHERE {owner_col} = ?", (owner_id,))
        if not files:
            return
        if isinstance(files, dict):
            files = [files]
        from core.video.mediainfo import canonical_aspect
        for file in files:
            if not file:
                continue
            conn.execute(
                f"INSERT INTO media_files ({owner_col}, relative_path, size_bytes, resolution, "
                "video_codec, audio_codec, release_source, quality, runtime_seconds, aspect) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_id,
                 file.get("relative_path") or file.get("path") or "",
                 file.get("size_bytes"), file.get("resolution"), file.get("video_codec"),
                 file.get("audio_codec"), file.get("release_source"), file.get("quality"),
                 file.get("runtime_seconds"), canonical_aspect(file.get("aspect"))),
            )

    @staticmethod
    def _parse_locked(raw) -> set:
        """Parse a stored ``locked_fields`` JSON list (None/garbage → empty)."""
        if not raw:
            return set()
        try:
            v = json.loads(raw)
            return {str(f) for f in v} if isinstance(v, list) else set()
        except (ValueError, TypeError):
            return set()

    @classmethod
    def _locked_fields_set(cls, conn, table: str, item_id: int) -> set:
        """The user-locked field names for one movies/shows row (empty when none)."""
        row = conn.execute(f"SELECT locked_fields FROM {table} WHERE id=?", (item_id,)).fetchone()
        return cls._parse_locked(row["locked_fields"]) if row else set()

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
    def _set_named_links(conn, link_table: str, owner_col: str, ref_table: str, ref_fk: str,
                         owner_id: int, names) -> None:
        """Replace the studios/networks links for one owner (normalised; dedup names in
        the shared ref table). Same shape as _set_genres, generalised for the multi-valued
        studio/network facets. Table/column names are internal, never user input."""
        conn.execute(f"DELETE FROM {link_table} WHERE {owner_col}=?", (owner_id,))
        seen = set()
        for raw in (names or []):
            name = (raw or "").strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            conn.execute(f"INSERT OR IGNORE INTO {ref_table} (name) VALUES (?)", (name,))
            rid = conn.execute(f"SELECT id FROM {ref_table} WHERE name=? COLLATE NOCASE", (name,)).fetchone()["id"]
            conn.execute(f"INSERT OR IGNORE INTO {link_table} ({owner_col}, {ref_fk}) VALUES (?, ?)",
                         (owner_id, rid))

    @staticmethod
    def _seed_named_links_from_scalar(conn) -> None:
        """One-time backfill: seed the studios/networks link tables from the LEGACY single
        studio/network columns, so existing collections match IDENTICALLY the moment the
        smart filters switch to the link tables — no regression. Full multi-company data
        then fills in as titles re-enrich (the setter replaces the seeded single value with
        the whole list). Guarded by a marker so it runs exactly once."""
        try:
            row = conn.execute(
                "SELECT value FROM video_settings WHERE key='studio_network_links_seeded'").fetchone()
            if row and str(row[0]) == '1':
                return
            conn.execute("INSERT OR IGNORE INTO studios(name) SELECT DISTINCT studio FROM movies "
                         "WHERE studio IS NOT NULL AND TRIM(studio) != ''")
            conn.execute("INSERT OR IGNORE INTO movie_studios(movie_id, studio_id) "
                         "SELECT m.id, s.id FROM movies m JOIN studios s ON s.name = m.studio COLLATE NOCASE "
                         "WHERE m.studio IS NOT NULL AND TRIM(m.studio) != ''")
            conn.execute("INSERT OR IGNORE INTO networks(name) SELECT DISTINCT network FROM shows "
                         "WHERE network IS NOT NULL AND TRIM(network) != ''")
            conn.execute("INSERT OR IGNORE INTO show_networks(show_id, network_id) "
                         "SELECT sh.id, n.id FROM shows sh JOIN networks n ON n.name = sh.network COLLATE NOCASE "
                         "WHERE sh.network IS NOT NULL AND TRIM(sh.network) != ''")
            conn.execute("INSERT INTO video_settings(key, value, updated_at) "
                         "VALUES('studio_network_links_seeded', '1', CURRENT_TIMESTAMP) "
                         "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP")
        except sqlite3.Error:
            logger.exception("studio/network link seed failed (non-fatal)")

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
        watchlist depends on). A FULL scan passes False = a clean overwrite / reset.

        User-locked fields (``locked_fields`` JSON on the row) are kept in EVERY
        mode — a full scan resets enrichment, never a user edit."""
        protect = (_ENRICH_META_COLS.get(table, set()) if preserve_enrichment else set())

        def _set(c):
            # On a conflict UPDATE, an enrichment-owned column only takes the server
            # value when it's non-blank; otherwise it keeps what's already stored.
            take = f"excluded.{c}"
            if c in protect:
                take = f"COALESCE(NULLIF(excluded.{c}, ''), {table}.{c})"
            # instr on the quoted name — '"title"' never matches '"sort_title"'.
            return (f"{c}=CASE WHEN instr(COALESCE({table}.locked_fields, ''), '\"{c}\"') > 0 "
                    f"THEN {table}.{c} ELSE {take} END")

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
                "play_count": item.get("play_count"),
                "last_viewed_at": item.get("last_viewed_at"),
                "poster_url": item.get("poster_url"),
                "has_file": 1 if (item.get("files") or item.get("file")) else 0,
            }, {"tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
            movie_id = conn.execute(
                "SELECT id FROM movies WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            self._set_media_file(conn, "movie_id", movie_id,
                                 item.get("files") or item.get("file"))
            if "genres" not in self._locked_fields_set(conn, "movies", movie_id):
                self._set_genres(conn, "movie_genres", "movie_id", movie_id, item.get("genres"))
            if "studio" not in self._locked_fields_set(conn, "movies", movie_id):
                # ALL production companies (falls back to the single scalar so a scan/enrichment
                # that only has one still links it — keeps parity with the old behaviour).
                studios = item.get("studios") or ([item["studio"]] if item.get("studio") else [])
                self._set_named_links(conn, "movie_studios", "movie_id", "studios", "studio_id",
                                      movie_id, studios)
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
                "watched_episodes": item.get("watched_episodes"),
                "poster_url": item.get("poster_url"),
            }, {"tvdb_id": item.get("tvdb_id"), "tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
            show_id = conn.execute(
                "SELECT id FROM shows WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            if "genres" not in self._locked_fields_set(conn, "shows", show_id):
                self._set_genres(conn, "show_genres", "show_id", show_id, item.get("genres"))
            if "network" not in self._locked_fields_set(conn, "shows", show_id):
                networks = item.get("networks") or ([item["network"]] if item.get("network") else [])
                self._set_named_links(conn, "show_networks", "show_id", "networks", "network_id",
                                      show_id, networks)

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
                        "runtime_minutes, still_url, rating, tvdb_id, has_file, added_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(show_id, season_number, episode_number) DO UPDATE SET "
                        "season_id=excluded.season_id, server_source=excluded.server_source, "
                        "server_id=excluded.server_id, title=excluded.title, "
                        "overview=excluded.overview, air_date=excluded.air_date, "
                        "runtime_minutes=excluded.runtime_minutes, still_url=excluded.still_url, "
                        "rating=excluded.rating, tvdb_id=excluded.tvdb_id, has_file=excluded.has_file, "
                        # keep the earliest known add-date: don't clobber it with a NULL from a
                        # source that didn't report one.
                        "added_at=COALESCE(episodes.added_at, excluded.added_at)",
                        (show_id, season_id, server_source, ep.get("server_id"), snum, enum,
                         ep.get("title"), ep.get("overview"), ep.get("air_date"),
                         ep.get("runtime_minutes"), ep.get("still_url"), ep.get("rating"),
                         ep.get("tvdb_id"), 1 if (ep.get("files") or ep.get("file")) else 0,
                         ep.get("added_at")),
                    )
                    ep_id = conn.execute(
                        "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, snum, enum),
                    ).fetchone()["id"]
                    self._set_media_file(conn, "episode_id", ep_id,
                                         ep.get("files") or ep.get("file"))

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
        """All server_ids already stored for a server (for incremental early-stop).
        'episodes' lets an incremental scan tell a fully-present show from one that
        gained episodes (a show's add-date doesn't move when episodes arrive)."""
        if table not in ("movies", "shows", "episodes"):
            return set()
        conn = self._get_connection()
        try:
            return {str(r[0]) for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=? AND server_id IS NOT NULL",
                (server_source,)).fetchall()}
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
            # A show with an episode in the calendar window is airing BY DEFINITION,
            # so for the calendar we only exclude shows we KNOW are terminal — a
            # NULL/blank status (enrichment gap) must NOT hide an airing show. (The
            # global _ACTIVE_SHOW_SQL requires a non-null status, which drops ~29%
            # of shows whose status backfill never landed — Cops, Dutton Ranch, …)
            active = ("(s.status IS NULL OR TRIM(s.status)='' OR LOWER(s.status) "
                      "NOT IN ('ended','canceled','cancelled','completed'))")
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

    def random_overlay_preview_item(self, kind="poster", server_source=None) -> dict | None:
        """A random owned item with art, to preview a template on real data. kind
        'poster' = movie/show (2:3), 'season' = a season poster, 'episode' = an
        episode still (16:9) — matching the template's type."""
        kind = str(kind).lower()
        if kind in ("season", "episode"):
            conn = self._get_connection()
            try:
                if kind == "season":
                    r = conn.execute(
                        "SELECT 'season' AS kind, se.id AS id, NULL AS tmdb_id, "
                        "COALESCE(sh.title,'') || ' — ' || COALESCE(NULLIF(se.title,''), 'Season ' || se.season_number) AS title "
                        "FROM seasons se JOIN shows sh ON sh.id = se.show_id "
                        "WHERE se.poster_url IS NOT NULL AND se.poster_url <> '' ORDER BY RANDOM() LIMIT 1").fetchone()
                else:
                    r = conn.execute(
                        "SELECT 'episode' AS kind, e.id AS id, NULL AS tmdb_id, "
                        "COALESCE(sh.title,'') || ' — S' || e.season_number || 'E' || e.episode_number AS title "
                        "FROM episodes e JOIN shows sh ON sh.id = e.show_id "
                        "WHERE e.still_url IS NOT NULL AND e.still_url <> '' ORDER BY RANDOM() LIMIT 1").fetchone()
                return dict(r) if r else None
            except sqlite3.Error:
                return None
            finally:
                conn.close()
        srcm = " AND server_source = ?" if server_source else ""
        srcs = " AND server_source = ?" if server_source else ""
        params = ([server_source] if server_source else []) + ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT kind, id, tmdb_id, title FROM ("
                f"  SELECT 'movie' AS kind, id, tmdb_id, title FROM movies "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcm}"
                "   UNION ALL "
                f"  SELECT 'show' AS kind, id, tmdb_id, title FROM shows "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcs}"
                ") ORDER BY RANDOM() LIMIT 1", params).fetchone()
            return dict(r) if r else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def search_overlay_preview(self, kind, q, limit=8) -> list:
        """[{id, title, has_poster}] seasons/episodes matching a query, to pick a
        specific one to preview a Season/Episode template on. (Poster templates use
        the general /library search.)"""
        kind = str(kind).lower()
        like = "%" + str(q or "").strip() + "%"
        limit = max(1, min(20, int(limit)))
        conn = self._get_connection()
        try:
            if kind == "season":
                rows = conn.execute(
                    "SELECT se.id AS id, "
                    "COALESCE(sh.title,'') || ' — ' || COALESCE(NULLIF(se.title,''), 'Season ' || se.season_number) AS title, "
                    "(se.poster_url IS NOT NULL AND se.poster_url <> '') AS has_poster "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id "
                    "WHERE sh.title LIKE ? AND se.poster_url IS NOT NULL AND se.poster_url <> '' "
                    "ORDER BY sh.title, se.season_number LIMIT ?", (like, limit)).fetchall()
                return [dict(r) for r in rows]
            if kind == "episode":
                rows = conn.execute(
                    "SELECT e.id AS id, "
                    "COALESCE(sh.title,'') || ' — S' || e.season_number || 'E' || e.episode_number || "
                    "CASE WHEN e.title IS NOT NULL AND e.title <> '' THEN ' ' || e.title ELSE '' END AS title, "
                    "(e.still_url IS NOT NULL AND e.still_url <> '') AS has_poster "
                    "FROM episodes e JOIN shows sh ON sh.id = e.show_id "
                    "WHERE (sh.title LIKE ? OR e.title LIKE ?) AND e.still_url IS NOT NULL AND e.still_url <> '' "
                    "ORDER BY sh.title, e.season_number, e.episode_number LIMIT ?", (like, like, limit)).fetchall()
                return [dict(r) for r in rows]
            return []
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def random_overlay_preview_items(self, n: int = 4, server_source=None) -> list:
        """Up to N distinct random owned titles (tmdb_id + poster) — powers the
        multi-poster filmstrip that checks a template across varying real data."""
        n = max(1, min(12, int(n)))
        srcm = " AND server_source = ?" if server_source else ""
        srcs = " AND server_source = ?" if server_source else ""
        params = ([server_source] if server_source else []) + ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, tmdb_id, title FROM ("
                f"  SELECT 'movie' AS kind, id, tmdb_id, title FROM movies "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcm}"
                "   UNION ALL "
                f"  SELECT 'show' AS kind, id, tmdb_id, title FROM shows "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcs}"
                ") ORDER BY RANDOM() LIMIT ?", params + [n]).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def item_tmdb_id(self, kind: str, item_id: int):
        """TMDB id for a movie/show — the key to re-fetch its clean original poster."""
        table = {"movie": "movies", "show": "shows"}.get(str(kind).lower())
        if not table:
            return None
        conn = self._get_connection()
        try:
            r = conn.execute(f"SELECT tmdb_id FROM {table} WHERE id=?", (int(item_id),)).fetchone()
            return r["tmdb_id"] if r else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def poster_set_target(self, kind: str, item_id: int) -> dict | None:
        """Server id + on-disk folder for a movie/show, so a new poster can be pushed
        to the media server and (best-effort) written into the item's folder."""
        kind = str(kind).lower()
        conn = self._get_connection()
        try:
            if kind == "season":
                row = conn.execute(
                    "SELECT sh.server_source, se.server_id, NULL AS path "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (int(item_id),)).fetchone()
                return dict(row) if row else None
            if kind == "episode":
                row = conn.execute(
                    "SELECT server_source, server_id, NULL AS path FROM episodes WHERE id=?",
                    (int(item_id),)).fetchone()
                return dict(row) if row else None
            table = {"movie": "movies", "show": "shows"}.get(kind)
            if not table:
                return None
            row = conn.execute(
                f"SELECT server_source, server_id, path FROM {table} WHERE id=?",
                (int(item_id),)).fetchone()
            return dict(row) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def set_item_poster_url(self, kind: str, item_id: int, poster_url: str) -> bool:
        """Best-effort: point a movie/show at a new poster path/URL so SoulSync shows it
        immediately (the next scan reconciles it with the server's own copy)."""
        table = {"movie": "movies", "show": "shows"}.get(str(kind).lower())
        if not table:
            return False
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET poster_url=? WHERE id=?",
                         (poster_url, int(item_id)))
            conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    # ── overlay templates (Artwork Studio) ────────────────────────────────────
    # CRUD for saved overlay designs. `definition` is a JSON scene (canvas meta +
    # ordered layer list); we store it as text and parse on read so callers get a
    # dict. The gallery list omits the (potentially large) definition + thumbnail.
    @staticmethod
    def _parse_definition(raw) -> dict:
        try:
            d = json.loads(raw) if raw else {}
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def list_overlay_templates(self) -> list:
        """Every saved template, newest-edited first — light rows for the gallery
        (id, name, timestamps, layer count, thumbnail). No full definition."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, thumbnail, definition, created_at, updated_at "
                "FROM overlay_templates ORDER BY updated_at DESC, id DESC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                defn = self._parse_definition(d.pop("definition", None))
                d["layer_count"] = len(defn.get("layers") or [])
                d["kind"] = defn.get("kind") or "poster"   # Poster / Season / Episode
                out.append(d)
            return out
        finally:
            conn.close()

    def get_overlay_template(self, template_id: int) -> dict | None:
        """One template with its parsed `definition` dict, or None."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, name, thumbnail, definition, created_at, updated_at "
                "FROM overlay_templates WHERE id=?", (int(template_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["definition"] = self._parse_definition(d.get("definition"))
            return d
        except (ValueError, TypeError):
            return None
        finally:
            conn.close()

    def create_overlay_template(self, name: str, definition=None, thumbnail=None) -> int | None:
        """Insert a new template; returns its id. `definition` may be a dict or a
        JSON string (dicts are serialized)."""
        name = (name or "").strip() or "Untitled template"
        raw = json.dumps(definition) if isinstance(definition, (dict, list)) else (definition or "{}")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO overlay_templates (name, definition, thumbnail) VALUES (?,?,?)",
                (name, raw, thumbnail))
            conn.commit()
            return cur.lastrowid
        except sqlite3.Error:
            logger.exception("create_overlay_template failed")
            return None
        finally:
            conn.close()

    def update_overlay_template(self, template_id: int, *, name=None,
                                definition=None, thumbnail=None) -> bool:
        """Patch a template's name / definition / thumbnail (only the provided
        fields) and bump updated_at. Returns True if a row was changed."""
        sets, params = [], []
        if name is not None:
            sets.append("name=?"); params.append((name or "").strip() or "Untitled template")
        if definition is not None:
            raw = json.dumps(definition) if isinstance(definition, (dict, list)) else definition
            sets.append("definition=?"); params.append(raw)
        if thumbnail is not None:
            sets.append("thumbnail=?"); params.append(thumbnail)
        if not sets:
            return False
        sets.append("updated_at=datetime('now')")
        params.append(int(template_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE overlay_templates SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("update_overlay_template failed for %s", template_id)
            return False
        finally:
            conn.close()

    def delete_overlay_template(self, template_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM overlay_templates WHERE id=?", (int(template_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def duplicate_overlay_template(self, template_id: int) -> int | None:
        """Copy a template into a new "… (copy)" row; returns the new id or None."""
        src = self.get_overlay_template(template_id)
        if not src:
            return None
        return self.create_overlay_template(
            (src.get("name") or "Untitled") + " (copy)", src.get("definition") or {})

    # ── Collections (Kometa parity) ───────────────────────────────────────────
    # SoulSync-managed movie/show collections. A definition (`definition` JSON =
    # smart rules OR a list source) resolves to a set of owned items and is synced
    # to the active video server. Same CRUD shape as overlay templates.
    _COLLECTION_BOOL_COLS = ("pinned", "wishlist_missing", "enabled")

    def list_collection_definitions(self) -> list:
        """Every collection definition, newest-edited first (light rows for the
        gallery) with the last-synced member count/time joined in."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, kind, media_type, poster_url, summary, sort_order, "
                "sync_mode, pinned, wishlist_missing, enabled, window_start, window_end, "
                "collection_mode, created_at, updated_at "
                "FROM collection_definitions ORDER BY updated_at DESC, id DESC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                s = conn.execute(
                    "SELECT member_count, synced_at FROM collection_sync WHERE definition_id=?",
                    (d["id"],)).fetchone()
                d["member_count"] = s["member_count"] if s else None
                d["synced_at"] = s["synced_at"] if s else None
                out.append(d)
            return out
        finally:
            conn.close()

    def get_collection_definition(self, definition_id: int) -> dict | None:
        """One definition with its parsed `definition` dict, or None."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM collection_definitions WHERE id=?",
                (int(definition_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["definition"] = self._parse_definition(d.get("definition"))
            return d
        except (ValueError, TypeError):
            return None
        finally:
            conn.close()

    def create_collection_definition(self, name: str, *, kind="smart", media_type="movie",
                                     definition=None, poster_url=None, summary=None,
                                     sort_order="release", sync_mode="sync", pinned=False,
                                     wishlist_missing=False, enabled=True,
                                     window_start=None, window_end=None,
                                     collection_mode=None) -> int | None:
        """Insert a collection definition; returns its id. `definition` may be a
        dict or a JSON string (dicts are serialized)."""
        name = (name or "").strip() or "Untitled collection"
        raw = json.dumps(definition) if isinstance(definition, (dict, list)) else (definition or "{}")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO collection_definitions (name, kind, media_type, definition, "
                "poster_url, summary, sort_order, sync_mode, pinned, wishlist_missing, enabled, "
                "window_start, window_end, collection_mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, kind, media_type, raw, poster_url, summary, sort_order, sync_mode,
                 1 if pinned else 0, 1 if wishlist_missing else 0, 1 if enabled else 0,
                 (window_start or "").strip() or None, (window_end or "").strip() or None,
                 (collection_mode or "").strip() or None))
            conn.commit()
            return cur.lastrowid
        except sqlite3.Error:
            logger.exception("create_collection_definition failed")
            return None
        finally:
            conn.close()

    def update_collection_definition(self, definition_id: int, **fields) -> bool:
        """Patch only the provided fields (None values ignored) and bump
        updated_at. Booleans coerced to 0/1; `definition` dicts serialized."""
        allowed = {"name", "kind", "media_type", "definition", "poster_url", "summary",
                   "sort_order", "sync_mode", "pinned", "wishlist_missing", "enabled",
                   "window_start", "window_end", "collection_mode"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "definition":
                v = json.dumps(v) if isinstance(v, (dict, list)) else v
            elif k in self._COLLECTION_BOOL_COLS:
                v = 1 if v else 0
            elif k == "name":
                v = (v or "").strip() or "Untitled collection"
            elif k in ("window_start", "window_end", "collection_mode"):
                v = (v or "").strip() or None   # "" clears (mode: back to leave-alone)
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return False
        sets.append("updated_at=datetime('now')")
        params.append(int(definition_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE collection_definitions SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("update_collection_definition failed for %s", definition_id)
            return False
        finally:
            conn.close()

    def delete_collection_definition(self, definition_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM collection_definitions WHERE id=?", (int(definition_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def duplicate_collection_definition(self, definition_id: int) -> int | None:
        src = self.get_collection_definition(definition_id)
        if not src:
            return None
        return self.create_collection_definition(
            (src.get("name") or "Untitled") + " (copy)",
            kind=src.get("kind", "smart"), media_type=src.get("media_type", "movie"),
            definition=src.get("definition") or {}, poster_url=src.get("poster_url"),
            summary=src.get("summary"), sort_order=src.get("sort_order", "release"),
            sync_mode=src.get("sync_mode", "sync"), pinned=bool(src.get("pinned")),
            wishlist_missing=bool(src.get("wishlist_missing")), enabled=bool(src.get("enabled")))

    def resolve_smart_members(self, media_type: str, definition: dict) -> list:
        """Owned library items matching a smart definition's rules. Raises
        SmartFilterError (from compile_rules) on an empty/invalid rule set — the
        caller decides how to surface it. Only items that are actually on a
        server (have a server_id) are returned, since a collection is a server
        grouping."""
        from core.video.collections.smart_filter import compile_rules
        where, params = compile_rules(definition, media_type)
        table = "movies" if media_type == "movie" else "shows"
        date_col = "release_date" if media_type == "movie" else "first_air_date"
        sql = (
            f"SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
            f"rating, added_at, {date_col} AS release_date "
            f"FROM {table} "
            f"WHERE server_id IS NOT NULL AND TRIM(server_id) != '' AND {where}"
        )
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def owned_by_tmdb_ids(self, media_type: str, tmdb_ids) -> list:
        """Owned (on-server) items whose tmdb_id is in the given set — the
        intersection step for list/franchise collections."""
        ids = [int(t) for t in (tmdb_ids or []) if t is not None]
        if not ids:
            return []
        table = "movies" if media_type == "movie" else "shows"
        date_col = "release_date" if media_type == "movie" else "first_air_date"
        placeholders = ", ".join("?" for _ in ids)
        sql = (
            f"SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
            f"rating, added_at, {date_col} AS release_date "
            f"FROM {table} "
            f"WHERE server_id IS NOT NULL AND TRIM(server_id) != '' "
            f"AND tmdb_id IN ({placeholders})"
        )
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, ids).fetchall()]
        finally:
            conn.close()

    def franchise_owned_members(self, tmdb_collection_id: int) -> list:
        """Owned movies belonging to a TMDB franchise (movies only)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
                "rating, added_at, release_date FROM movies "
                "WHERE server_id IS NOT NULL AND TRIM(server_id) != '' "
                "AND tmdb_collection_id = ?", (int(tmdb_collection_id),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Preset-pack aggregates (Collection Studio "easy setup") ──────────────
    # Facet counts over the OWNED, ON-SERVER library — the same base condition
    # resolve_smart_members uses, so a preset entry's count equals what the
    # collection will actually contain after apply + sync.
    _ON_SERVER = "server_id IS NOT NULL AND TRIM(server_id) != ''"

    def owned_genre_counts(self, media_type: str, limit: int = 60) -> list:
        """Genres across owned items with counts, busiest first —
        [{value, count}] for the Genres preset pack."""
        if media_type == "movie":
            link, owner, tbl = "movie_genres", "movie_id", "movies"
        elif media_type == "show":
            link, owner, tbl = "show_genres", "show_id", "shows"
        else:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT g.name AS v, COUNT(*) AS c FROM {link} lt "
                f"JOIN genres g ON g.id = lt.genre_id "
                f"JOIN {tbl} t ON t.id = lt.{owner} "
                f"WHERE t.server_id IS NOT NULL AND TRIM(t.server_id) != '' "
                f"GROUP BY g.name ORDER BY c DESC, g.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_decade_counts(self, media_type: str) -> list:
        """Owned items bucketed by decade — [{value: 1980, count}] newest first."""
        if media_type not in ("movie", "show"):
            return []
        tbl = "movies" if media_type == "movie" else "shows"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT (year / 10) * 10 AS d, COUNT(*) AS c FROM {tbl} "
                f"WHERE {self._ON_SERVER} AND year IS NOT NULL AND year >= 1900 "
                f"GROUP BY d ORDER BY d DESC").fetchall()
            return [{"value": r["d"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_studio_counts(self, limit: int = 40) -> list:
        """Movie studios with owned counts, busiest first — [{value, count}]. Counts over
        the studios link table (ALL production companies per movie), so a movie counts for
        every company it was made by, not just its primary one."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.name AS v, COUNT(*) AS c FROM movie_studios ms "
                "JOIN studios s ON s.id = ms.studio_id "
                "JOIN movies m ON m.id = ms.movie_id "
                "WHERE m.server_id IS NOT NULL AND TRIM(m.server_id) != '' "
                "GROUP BY s.name ORDER BY c DESC, s.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_network_counts(self, limit: int = 40) -> list:
        """Show networks with owned counts, busiest first — [{value, count}]. Counts over the
        networks link table (ALL networks per show)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT n.name AS v, COUNT(*) AS c FROM show_networks sn "
                "JOIN networks n ON n.id = sn.network_id "
                "JOIN shows sh ON sh.id = sn.show_id "
                "WHERE sh.server_id IS NOT NULL AND TRIM(sh.server_id) != '' "
                "GROUP BY n.name ORDER BY c DESC, n.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def count_smart_members(self, media_type: str, definition: dict) -> int:
        """COUNT of resolve_smart_members without materializing the rows — sizes
        the fixed preset entries (4K, Recently Added, …). Raises SmartFilterError
        for a bad definition, same as resolve."""
        from core.video.collections.smart_filter import compile_rules
        where, params = compile_rules(definition, media_type)
        table = "movies" if media_type == "movie" else "shows"
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} "
                f"WHERE {self._ON_SERVER} AND {where}", params).fetchone()
            return int(row["c"] if row else 0)
        finally:
            conn.close()

    def search_owned_titles(self, media_type: str, query: str, limit: int = 10) -> list:
        """Owned, TMDB-matched items whose title matches — feeds the editor's
        include-override picker. Returns [{tmdb_id, title, year}]."""
        if media_type not in ("movie", "show") or not (query or "").strip():
            return []
        table = "movies" if media_type == "movie" else "shows"
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT tmdb_id, title, year FROM {table} "
                f"WHERE {self._ON_SERVER} AND tmdb_id IS NOT NULL "
                f"AND title LIKE ? ESCAPE '\\' ORDER BY title LIMIT ?",
                (like, int(limit))).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_titles_by_tmdb_ids(self, media_type: str, tmdb_ids) -> list:
        """Titles for override chips — [{tmdb_id, title, year}] for owned items."""
        rows = self.owned_by_tmdb_ids(media_type, tmdb_ids) if tmdb_ids else []
        return [{"tmdb_id": r.get("tmdb_id"), "title": r.get("title"), "year": r.get("year")}
                for r in rows]

    def find_library_ref_by_title(self, kind, title, year=None):
        """A library movie/show id matched by title (+ year for movies) — the
        fallback the live-activity click-through uses when the native server id
        doesn't line up. None when not owned."""
        if not title:
            return None
        table = "movies" if str(kind) == "movie" else "shows"
        conn = self._get_connection()
        try:
            if year:
                r = conn.execute(f"SELECT id FROM {table} WHERE title=? COLLATE NOCASE "
                                 "AND year=? LIMIT 1", (str(title), year)).fetchone()
                if r:
                    return r["id"]
            r = conn.execute(f"SELECT id FROM {table} WHERE title=? COLLATE NOCASE LIMIT 1",
                             (str(title),)).fetchone()
            return r["id"] if r else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def items_by_server_ids(self, server_ids, server_source=None) -> list:
        """Library items (movies + shows) matching the given native server ids —
        maps a server collection's membership back to our rows for adoption.
        Returns [{kind, id, tmdb_id, server_id, title}]."""
        ids = [str(s) for s in (server_ids or []) if str(s).strip()]
        if not ids:
            return []
        out = []
        conn = self._get_connection()
        try:
            for kind, table in (("movie", "movies"), ("show", "shows")):
                for i in range(0, len(ids), 400):        # chunk under SQLite's var cap
                    chunk = ids[i:i + 400]
                    ph = ", ".join("?" for _ in chunk)
                    sql = (f"SELECT id, tmdb_id, server_id, title FROM {table} "
                           f"WHERE server_id IN ({ph})")
                    args: list = list(chunk)
                    if server_source:
                        sql += " AND server_source=?"
                        args.append(server_source)
                    for r in conn.execute(sql, args):
                        out.append({"kind": kind, "id": r["id"], "tmdb_id": r["tmdb_id"],
                                    "server_id": str(r["server_id"]), "title": r["title"]})
            return out
        except sqlite3.Error:
            logger.exception("items_by_server_ids failed")
            return []
        finally:
            conn.close()

    def get_imdb_tmdb(self, imdb_id) -> dict | None:
        """Persisted tt→TMDB mapping (+ poster art), or None when never resolved."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT movie_tmdb, show_tmdb, movie_poster, show_poster "
                             "FROM imdb_tmdb_map WHERE imdb_id=?", (imdb_id,)).fetchone()
            return ({"movie": r["movie_tmdb"], "show": r["show_tmdb"],
                     "movie_poster": r["movie_poster"], "show_poster": r["show_poster"]}
                    if r else None)
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def put_imdb_tmdb(self, imdb_id, movie_tmdb, show_tmdb,
                      movie_poster=None, show_poster=None) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO imdb_tmdb_map (imdb_id, movie_tmdb, show_tmdb, movie_poster, show_poster) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(imdb_id) DO UPDATE SET movie_tmdb=excluded.movie_tmdb, "
                "show_tmdb=excluded.show_tmdb, "
                "movie_poster=COALESCE(excluded.movie_poster, imdb_tmdb_map.movie_poster), "
                "show_poster=COALESCE(excluded.show_poster, imdb_tmdb_map.show_poster), "
                "updated_at=datetime('now')",
                (imdb_id, movie_tmdb, show_tmdb, movie_poster, show_poster))
            conn.commit()
        except sqlite3.Error:
            logger.exception("put_imdb_tmdb failed")
        finally:
            conn.close()

    def tmdb_by_library_imdb(self, imdb_id) -> dict:
        """{'movie': tmdb|None, 'show': tmdb|None} from the LIBRARY's own rows —
        a chart title you already own needs no network to map at all."""
        out = {"movie": None, "show": None}
        conn = self._get_connection()
        try:
            for kind, table in (("movie", "movies"), ("show", "shows")):
                r = conn.execute(f"SELECT tmdb_id FROM {table} "
                                 f"WHERE imdb_id=? AND tmdb_id IS NOT NULL LIMIT 1",
                                 (imdb_id,)).fetchone()
                if r:
                    out[kind] = r["tmdb_id"]
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    # ── Collection sync ledger (managed-collection map + skip signature) ──────
    def get_collection_sync(self, definition_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM collection_sync WHERE definition_id=?",
                (int(definition_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def record_collection_sync(self, definition_id: int, *, server_source, server_id,
                               members_sig, member_count) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO collection_sync (definition_id, server_source, server_id, "
                "members_sig, member_count, synced_at) VALUES (?,?,?,?,?, datetime('now')) "
                "ON CONFLICT(definition_id) DO UPDATE SET server_source=excluded.server_source, "
                "server_id=excluded.server_id, members_sig=excluded.members_sig, "
                "member_count=excluded.member_count, synced_at=excluded.synced_at",
                (int(definition_id), server_source, server_id, members_sig, int(member_count)))
            conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("record_collection_sync failed for %s", definition_id)
            return False
        finally:
            conn.close()

    def delete_collection_sync(self, definition_id: int) -> None:
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM collection_sync WHERE definition_id=?", (int(definition_id),))
            conn.commit()
        except sqlite3.Error:
            logger.exception("delete_collection_sync failed for %s", definition_id)
        finally:
            conn.close()

    def list_collection_syncs(self) -> list:
        """Every ledger row (+ its definition's name) — maps server collections
        back to the SoulSync definition that manages them, so the server-cleanup
        view can tell ours from foreign (e.g. old Kometa) collections."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.definition_id, s.server_source, s.server_id, s.member_count, "
                "s.synced_at, d.name AS definition_name "
                "FROM collection_sync s "
                "LEFT JOIN collection_definitions d ON d.id = s.definition_id").fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def overlay_sample_data(self, kind: str, item_id: int) -> dict | None:
        """Real badge values for one library item — the "load from a real title"
        source for the overlay editor's sample data. Raw values (e.g. resolution
        '2160p', ratings as numbers); the editor formats them for display."""
        kind = str(kind).lower()
        if kind in ("season", "episode"):
            return self._overlay_sample_sub(kind, item_id)
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return None
        conn = self._get_connection()
        try:
            _show_ratings = ", tvmaze_rating, anilist_score" if kind == "show" else ""
            _movie_only = ", tmdb_collection_name, tagline" if kind == "movie" else ""
            row = conn.execute(
                f"SELECT title, year, runtime_minutes, status, content_rating, "
                f"rating, imdb_rating, rt_rating, metacritic, trakt_rating{_show_ratings}{_movie_only}, subtitle_langs, streaming, mediastinger, awards, logo_url, "
                f"{'studio' if kind == 'movie' else 'network'} AS org, "
                f"(poster_url IS NOT NULL AND poster_url <> '') AS has_poster, "
                f"(backdrop_url IS NOT NULL AND backdrop_url <> '') AS has_backdrop "
                f"FROM {table} WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            out = {
                "title": d.get("title"), "year": d.get("year"),
                "runtime": d.get("runtime_minutes"), "status": d.get("status"),
                "content_rating": d.get("content_rating"),
                "tmdb": d.get("rating"), "imdb": d.get("imdb_rating"),
                "rt": d.get("rt_rating"), "metacritic": d.get("metacritic"),
                "trakt": d.get("trakt_rating"),
                "tvmaze": d.get("tvmaze_rating"), "anilist": d.get("anilist_score"),
                "streaming": d.get("streaming"),
                "collection": d.get("tmdb_collection_name"),
                "tagline": d.get("tagline"),
                "mediastinger": d.get("mediastinger"), "awards": d.get("awards"),
                "studio": d.get("org") if kind == "movie" else None,
                "network": d.get("org") if kind == "show" else None,
                "logo_url": d.get("logo_url"),
                "subtitles": len(_subtitle_langs_list(d.get("subtitle_langs"))) or None,
                "resolution": None, "video_codec": None, "audio_codec": None, "source": None,
                "aspect": None,
                "season_count": None, "episode_count": None, "versions": None,
                "season_number": None, "episode_number": None, "episode_code": None,
            }
            # ALL of the item's genres, comma-joined — a Genre badge shows the
            # primary (first) one, while a "genre includes X" condition can match
            # any of them. Isolated so a genre hiccup can't null the whole payload.
            out["genre"] = None
            try:
                g_link, g_owner = ("movie_genres", "movie_id") if kind == "movie" else ("show_genres", "show_id")
                genres = self._genres_for(conn, g_link, g_owner, int(item_id))
                out["genre"] = ", ".join(genres) if genres else None
            except sqlite3.Error:
                logger.debug("overlay genre lookup failed for %s %s", kind, item_id, exc_info=True)
            # Best owned file drives the quality badges.
            _res_rank = ("CASE mf.resolution WHEN '2160p' THEN 4 WHEN '1080p' THEN 3 "
                         "WHEN '720p' THEN 2 ELSE 1 END DESC")
            if kind == "movie":
                mf = conn.execute(
                    "SELECT resolution, video_codec, audio_codec, release_source, aspect FROM media_files mf "
                    f"WHERE mf.movie_id=? ORDER BY {_res_rank}, mf.size_bytes DESC LIMIT 1",
                    (int(item_id),)).fetchone()
                vc = conn.execute("SELECT COUNT(*) AS n FROM media_files WHERE movie_id=?",
                                  (int(item_id),)).fetchone()
                out["versions"] = (vc["n"] if vc else 0) or None
            else:
                mf = conn.execute(
                    "SELECT resolution, video_codec, audio_codec, release_source, aspect FROM media_files mf "
                    "JOIN episodes e ON e.id=mf.episode_id WHERE e.show_id=? "
                    f"ORDER BY {_res_rank} LIMIT 1", (int(item_id),)).fetchone()
                counts = conn.execute(
                    "SELECT COUNT(DISTINCT season_number) AS seasons, COUNT(*) AS eps "
                    "FROM episodes WHERE show_id=?", (int(item_id),)).fetchone()
                if counts:
                    out["season_count"] = counts["seasons"]
                    out["episode_count"] = counts["eps"]
            if mf:
                m = dict(mf)
                out["resolution"] = m.get("resolution")
                out["video_codec"] = m.get("video_codec")
                out["audio_codec"] = m.get("audio_codec")
                out["source"] = m.get("release_source")
                out["aspect"] = m.get("aspect")
            out["has_poster"] = bool(d.get("has_poster"))
            out["has_backdrop"] = bool(d.get("has_backdrop"))
            return out
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("overlay_sample_data failed for %s %s", kind, item_id)
            return None
        finally:
            conn.close()

    @staticmethod
    def _empty_overlay_values() -> dict:
        """Full badge-value key set (all None) so a season/episode payload has the
        same shape as movie/show — the editor + compositor use .get(), but keeping
        parity avoids surprises."""
        return {k: None for k in (
            "title", "year", "runtime", "status", "content_rating",
            "tmdb", "imdb", "rt", "metacritic", "trakt", "tvmaze", "anilist",
            "streaming", "collection", "tagline", "mediastinger", "awards",
            "studio", "network", "logo_url", "subtitles",
            "resolution", "video_codec", "audio_codec", "source", "aspect",
            "season_count", "episode_count", "versions", "genre",
            "season_number", "episode_number", "episode_code",
            "has_poster", "has_backdrop")}

    def _overlay_sample_sub(self, kind: str, item_id: int) -> dict | None:
        """Badge values for a season or episode (the sub-item overlay scopes). A
        season inherits the show's content_rating/network; an episode adds its own
        number/code/air-year/rating and quality badges from its owned file."""
        conn = self._get_connection()
        try:
            if kind == "season":
                row = conn.execute(
                    "SELECT se.title, se.season_number, sh.title AS show_title, "
                    "sh.content_rating, sh.network, sh.year, "
                    "(SELECT COUNT(*) FROM episodes e WHERE e.season_id = se.id) AS episode_count, "
                    "(se.poster_url IS NOT NULL AND se.poster_url <> '') AS has_poster "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (int(item_id),)).fetchone()
                if not row:
                    return None
                d = dict(row)
                out = self._empty_overlay_values()
                out.update({
                    "title": d.get("show_title"),
                    "season_number": d.get("season_number"),
                    "episode_count": d.get("episode_count") or None,
                    "content_rating": d.get("content_rating"),
                    "network": d.get("network"), "year": d.get("year"),
                    "has_poster": bool(d.get("has_poster")),
                })
                return out
            # episode
            row = conn.execute(
                "SELECT e.title, e.season_number, e.episode_number, e.air_date, "
                "e.runtime_minutes, e.rating, sh.title AS show_title, sh.content_rating, sh.network, "
                "(e.still_url IS NOT NULL AND e.still_url <> '') AS has_poster "
                "FROM episodes e JOIN shows sh ON sh.id = e.show_id WHERE e.id=?",
                (int(item_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            sn, en = d.get("season_number"), d.get("episode_number")
            code = ("S%dE%d" % (int(sn), int(en))) if sn is not None and en is not None else None
            ad = str(d.get("air_date") or "")
            year = int(ad[:4]) if len(ad) >= 4 and ad[:4].isdigit() else None
            mf = conn.execute(
                "SELECT resolution, video_codec, audio_codec, release_source, aspect "
                "FROM media_files WHERE episode_id=? ORDER BY size_bytes DESC LIMIT 1",
                (int(item_id),)).fetchone()
            out = self._empty_overlay_values()
            out.update({
                "title": d.get("title"),
                "season_number": sn, "episode_number": en, "episode_code": code,
                "runtime": d.get("runtime_minutes"), "tmdb": d.get("rating"),
                "content_rating": d.get("content_rating"), "network": d.get("network"),
                "year": year, "has_poster": bool(d.get("has_poster")),
            })
            if mf:
                m = dict(mf)
                out["resolution"] = m.get("resolution")
                out["video_codec"] = m.get("video_codec")
                out["audio_codec"] = m.get("audio_codec")
                out["source"] = m.get("release_source")
                out["aspect"] = m.get("aspect")
            return out
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("overlay_sample_data (sub) failed for %s %s", kind, item_id)
            return None
        finally:
            conn.close()

    # ── overlay apply: assignment (template → scope) + ledger ─────────────────
    def get_overlay_assignments(self) -> dict:
        """{'movie': {template_id, enabled, template_name}, 'show': {...}} — which
        template the apply pipeline burns onto each library scope."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT a.scope, a.template_id, a.enabled, a.filter, t.name AS template_name "
                "FROM overlay_assignment a LEFT JOIN overlay_templates t ON t.id = a.template_id").fetchall()
            out = {}
            for r in rows:
                d = dict(r)
                out[d["scope"]] = {"template_id": d["template_id"], "enabled": bool(d["enabled"]),
                                   "template_name": d["template_name"],
                                   "filter": self._parse_definition(d.get("filter")) or None}
            return out
        finally:
            conn.close()

    def set_overlay_assignment(self, scope: str, template_id, enabled: bool,
                               filter_definition=None) -> bool:
        if scope not in ("movie", "show", "season", "episode"):
            return False
        raw = (json.dumps(filter_definition)
               if isinstance(filter_definition, dict) and filter_definition.get("rules")
               else None)
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO overlay_assignment (scope, template_id, enabled, filter, updated_at) "
                "VALUES (?,?,?,?,datetime('now')) ON CONFLICT(scope) DO UPDATE SET "
                "template_id=excluded.template_id, enabled=excluded.enabled, "
                "filter=excluded.filter, updated_at=datetime('now')",
                (scope, template_id, 1 if enabled else 0, raw))
            conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("set_overlay_assignment failed for %s", scope)
            return False
        finally:
            conn.close()

    def record_overlay_apply(self, kind: str, item_id: int, template_id,
                             base_sha=None, values_sig=None, plex_poster_key=None) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO overlay_apply (kind, item_id, template_id, base_sha, values_sig, "
                "plex_poster_key, applied_at) "
                "VALUES (?,?,?,?,?,?,datetime('now')) ON CONFLICT(kind, item_id) DO UPDATE SET "
                "template_id=excluded.template_id, base_sha=excluded.base_sha, "
                "values_sig=excluded.values_sig, plex_poster_key=excluded.plex_poster_key, "
                "applied_at=datetime('now')",
                (kind, int(item_id), template_id, base_sha, values_sig, plex_poster_key))
            conn.commit()
        except sqlite3.Error:
            logger.exception("record_overlay_apply failed for %s %s", kind, item_id)
        finally:
            conn.close()

    def get_overlay_apply(self, kind: str, item_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT kind, item_id, template_id, base_sha, values_sig, plex_poster_key, applied_at "
                "FROM overlay_apply WHERE kind=? AND item_id=?", (kind, int(item_id))).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def delete_overlay_apply(self, kind: str, item_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM overlay_apply WHERE kind=? AND item_id=?", (kind, int(item_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def overlay_scope_items(self, scope: str, server_source=None,
                            filter_definition=None) -> list:
        """[{id, title}] for every on-server item in a scope — the apply targets
        (only items with a server_id can receive a pushed poster). Seasons/episodes
        inherit their show's server_source (seasons don't store one).

        ``filter_definition`` (smart-rule JSON, same language as collections)
        narrows the targets; for seasons/episodes it filters the PARENT SHOW
        ("only anime shows get episode overlays"). Raises SmartFilterError on a
        bad filter — the caller skips the scope rather than mis-applying."""
        scope = str(scope).lower()
        fwhere, fparams = "", []
        if filter_definition and (filter_definition.get("rules") or []):
            from core.video.collections.smart_filter import compile_rules
            kind = "movie" if scope == "movie" else "show"
            fwhere, fparams = compile_rules(filter_definition, kind)
        conn = self._get_connection()
        try:
            if scope == "season":
                where = ["seasons.server_id IS NOT NULL", "seasons.server_id <> ''"]
                params = []
                if server_source:
                    where.append("shows.server_source = ?")
                    params.append(server_source)
                if fwhere:
                    where.append(fwhere)
                    params.extend(fparams)
                rows = conn.execute(
                    "SELECT seasons.id AS id, "
                    "COALESCE(shows.title,'') || ' — ' "
                    "|| COALESCE(NULLIF(seasons.title,''), 'Season ' || seasons.season_number) AS title "
                    "FROM seasons JOIN shows ON shows.id = seasons.show_id "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY COALESCE(shows.sort_title, shows.title) COLLATE NOCASE, seasons.season_number",
                    params).fetchall()
                return [dict(r) for r in rows]
            if scope == "episode":
                where = ["episodes.server_id IS NOT NULL", "episodes.server_id <> ''"]
                params = []
                if server_source:
                    where.append("episodes.server_source = ?")
                    params.append(server_source)
                if fwhere:
                    where.append(fwhere)
                    params.extend(fparams)
                rows = conn.execute(
                    "SELECT episodes.id AS id, "
                    "COALESCE(shows.title,'') || ' — S' || episodes.season_number || 'E' || episodes.episode_number "
                    "|| CASE WHEN episodes.title IS NOT NULL AND episodes.title <> '' THEN ' ' || episodes.title ELSE '' END AS title "
                    "FROM episodes JOIN shows ON shows.id = episodes.show_id "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY COALESCE(shows.sort_title, shows.title) COLLATE NOCASE, episodes.season_number, episodes.episode_number",
                    params).fetchall()
                return [dict(r) for r in rows]
            table = {"movie": "movies", "show": "shows"}.get(scope)
            if not table:
                return []
            where = [f"{table}.server_id IS NOT NULL", f"{table}.server_id <> ''"]
            params = []
            if server_source:
                where.append(f"{table}.server_source = ?")
                params.append(server_source)
            if fwhere:
                where.append(fwhere)
                params.extend(fparams)
            rows = conn.execute(
                f"SELECT id, title FROM {table} WHERE {' AND '.join(where)} "
                f"ORDER BY COALESCE(sort_title, title) COLLATE NOCASE", params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def overlay_applied_ids(self, kind: str) -> set:
        """Item ids with a live overlay ledger row for a kind — the restore pass
        diffs these against the current (filtered) targets."""
        conn = self._get_connection()
        try:
            return {r["item_id"] for r in conn.execute(
                "SELECT item_id FROM overlay_apply WHERE kind=?", (kind,))}
        except sqlite3.Error:
            return set()
        finally:
            conn.close()

    def overlay_applied_count(self, template_id=None) -> int:
        conn = self._get_connection()
        try:
            if template_id is None:
                r = conn.execute("SELECT COUNT(*) FROM overlay_apply").fetchone()
            else:
                r = conn.execute("SELECT COUNT(*) FROM overlay_apply WHERE template_id=?", (template_id,)).fetchone()
            return r[0] if r else 0
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
                "(still_url IS NOT NULL AND still_url<>'') AS has_still, "
                # A server may hold several COPIES of one episode — surface them.
                "(SELECT COUNT(*) FROM media_files f WHERE f.episode_id=episodes.id) AS versions, "
                "(SELECT f.resolution FROM media_files f WHERE f.episode_id=episodes.id "
                "  ORDER BY f.size_bytes DESC LIMIT 1) AS resolution "
                "FROM episodes WHERE show_id=? "
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
                "versions": e["versions"], "resolution": e["resolution"],
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
            "sort_title": show["sort_title"],
            "locked_fields": sorted(self._parse_locked(show["locked_fields"])),
            "watched": (show["watched_episodes"] or 0) >= total > 0,
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

    def set_watch_state(self, kind: str, item_id: int, watched: bool) -> bool:
        """Reflect a played/unplayed toggle locally (the server push happens in
        core.video.metadata) so 'watched' smart rules react without waiting for
        the next scan. Movies flip play_count; shows set watched_episodes to the
        full episode count (matching what the server does on markPlayed)."""
        conn = self._get_connection()
        try:
            if kind == "movie":
                cur = conn.execute(
                    "UPDATE movies SET play_count=CASE WHEN ? THEN MAX(COALESCE(play_count,0),1) "
                    "ELSE 0 END WHERE id=?", (1 if watched else 0, item_id))
            elif kind == "show":
                cur = conn.execute(
                    "UPDATE shows SET watched_episodes=CASE WHEN ? THEN "
                    "(SELECT COUNT(*) FROM episodes WHERE show_id=shows.id) ELSE 0 END "
                    "WHERE id=?", (1 if watched else 0, item_id))
            else:
                return False
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── User metadata edits + field locks (Manage sidebar) ────────────────────
    # An edit writes the row AND records the field in ``locked_fields`` — from
    # then on scans (every mode) and enrichment leave it alone. Releasing the
    # lock hands the field back: the next scan re-adopts the server's value.

    def get_locked_fields(self, kind: str, item_id: int) -> list:
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return []
        conn = self._get_connection()
        try:
            return sorted(self._locked_fields_set(conn, table, item_id))
        finally:
            conn.close()

    def set_field_lock(self, kind: str, item_id: int, field: str, locked: bool):
        """Lock/release one field. Returns the new lock list, or None for an
        unknown kind/field/item."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or field not in _USER_EDITABLE[table]:
            return None
        conn = self._get_connection()
        try:
            if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone():
                return None
            locks = self._locked_fields_set(conn, table, item_id)
            locks = (locks | {field}) if locked else (locks - {field})
            conn.execute(f"UPDATE {table} SET locked_fields=? WHERE id=?",
                         (json.dumps(sorted(locks)) if locks else None, item_id))
            conn.commit()
            return sorted(locks)
        finally:
            conn.close()

    def update_item_fields(self, kind: str, item_id: int, changes: dict):
        """Apply user edits to a movie/show and auto-lock the edited fields.

        ``changes`` maps field name -> new value; fields outside _USER_EDITABLE
        raise ValueError (nothing partially applied). Editing ``title`` also
        derives ``sort_title`` unless it was provided or is already locked.
        Returns {"applied": [...], "locked": [...]} or None when the row is gone."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return None
        editable = _USER_EDITABLE[table]
        bad = [f for f in changes if f not in editable]
        if bad:
            raise ValueError(f"not editable: {', '.join(sorted(bad))}")
        clean: dict = {}
        for field, value in changes.items():
            if field == "genres":
                if not isinstance(value, (list, tuple)):
                    raise ValueError("genres must be a list")
                clean[field] = [str(g).strip() for g in value if str(g or "").strip()]
            elif field == "year":
                try:
                    clean[field] = int(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    raise ValueError("year must be a number") from None
            else:
                v = "" if value is None else str(value).strip()
                if field == "title" and not v:
                    raise ValueError("title cannot be empty")
                clean[field] = v
        conn = self._get_connection()
        try:
            row = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            locks = self._locked_fields_set(conn, table, item_id)
            if "title" in clean and "sort_title" not in clean and "sort_title" not in locks:
                clean["sort_title"] = _sort_title(clean["title"])
            genres = clean.pop("genres", None)
            if genres is not None:
                lt, oc = (("movie_genres", "movie_id") if table == "movies"
                          else ("show_genres", "show_id"))
                self._set_genres(conn, lt, oc, item_id, genres)
                locks.add("genres")
            if clean:
                sets = ", ".join(f"{c}=?" for c in clean)
                conn.execute(f"UPDATE {table} SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (*clean.values(), item_id))
                locks.update(clean)
            conn.execute(f"UPDATE {table} SET locked_fields=? WHERE id=?",
                         (json.dumps(sorted(locks)) if locks else None, item_id))
            conn.commit()
            applied = sorted(set(clean) | ({"genres"} if genres is not None else set()))
            return {"applied": applied, "locked": sorted(locks)}
        finally:
            conn.close()

    def item_genres(self, kind: str, item_id: int) -> list:
        """Current genre names for one movie/show (bulk genre add/remove reads
        these to build the new list per item)."""
        spec = {"movie": ("movie_genres", "movie_id"), "show": ("show_genres", "show_id")}.get(kind)
        if not spec:
            return []
        conn = self._get_connection()
        try:
            return self._genres_for(conn, spec[0], spec[1], item_id)
        finally:
            conn.close()

    def item_tmdb_ids(self, kind: str, ids) -> list:
        """tmdb ids for a set of library rows (order-free, Nones dropped) —
        bulk add-to-collection maps its selection through this."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        clean = [int(i) for i in (ids or []) if str(i).lstrip("-").isdigit()]
        if not table or not clean:
            return []
        conn = self._get_connection()
        try:
            marks = ",".join("?" * len(clean))
            rows = conn.execute(
                f"SELECT DISTINCT tmdb_id FROM {table} WHERE id IN ({marks}) "
                "AND tmdb_id IS NOT NULL", clean).fetchall()
            return [r["tmdb_id"] for r in rows]
        finally:
            conn.close()

    # ── Library Maintenance: jobs & findings (mirrors music's repair standard) ─
    # Findings: pending → resolved (approve == fix) | dismissed. Dedup treats
    # EVERY status as already-seen, so a re-scan never resurrects a dismissed or
    # fixed finding. Runs record per-job tallies for the History tab.

    def repair_create_finding(self, job_id: str, finding_type: str, *, title: str,
                              severity: str = "info", entity_type=None, entity_id=None,
                              file_path=None, description=None, details=None) -> bool:
        """Insert a finding unless an equivalent one exists in ANY status —
        same job+type and (same entity OR same non-null file_path). Returns
        True only when a genuinely new row landed (music dedup semantics)."""
        conn = self._get_connection()
        try:
            # Dedup basis: entity match (when the finding names one) OR same
            # non-null file_path. NULL entities never match each other — two
            # entity-less findings are only dupes via an identical path.
            conds, dparams = [], [job_id, finding_type]
            if entity_id is not None:
                conds.append("(entity_type IS ? AND entity_id=?)")
                dparams += [entity_type, str(entity_id)]
            if file_path:
                conds.append("(file_path IS NOT NULL AND file_path=?)")
                dparams.append(file_path)
            if conds:
                dup_sql = ("SELECT 1 FROM video_repair_findings WHERE job_id=? AND finding_type=? "
                           "AND (" + " OR ".join(conds) + ") LIMIT 1")
                if conn.execute(dup_sql, dparams).fetchone():
                    return False
            conn.execute(
                "INSERT INTO video_repair_findings (job_id, finding_type, severity, entity_type, "
                "entity_id, file_path, title, description, details_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, finding_type, severity, entity_type,
                 None if entity_id is None else str(entity_id), file_path, title, description,
                 json.dumps(details or {})))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def _finding_row(r) -> dict:
        d = dict(r)
        try:
            d["details"] = json.loads(d.pop("details_json") or "{}")
        except (ValueError, TypeError):
            d["details"] = {}
        return d

    def repair_get_findings(self, job_id=None, status=None, severity=None,
                            page: int = 1, limit: int = 50) -> dict:
        where, params = [], []
        for col, val in (("job_id", job_id), ("status", status), ("severity", severity)):
            if val:
                where.append(f"{col}=?")
                params.append(val)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        page, limit = max(1, int(page or 1)), max(1, min(200, int(limit or 50)))
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM video_repair_findings{w}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM video_repair_findings{w} ORDER BY created_at DESC, id DESC "
                "LIMIT ? OFFSET ?", (*params, limit, (page - 1) * limit)).fetchall()
            return {"items": [self._finding_row(r) for r in rows],
                    "total": total, "page": page, "limit": limit}
        finally:
            conn.close()

    def repair_get_finding(self, finding_id: int):
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM video_repair_findings WHERE id=?",
                             (int(finding_id),)).fetchone()
            return self._finding_row(r) if r else None
        finally:
            conn.close()

    def repair_set_finding_details(self, finding_id: int, details: dict) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_repair_findings SET details_json=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (json.dumps(details or {}), int(finding_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def repair_set_finding_status(self, finding_id: int, status: str, action=None) -> bool:
        """resolved/dismissed stamp resolved_at (+user_action on resolve)."""
        if status not in ("pending", "resolved", "dismissed"):
            return False
        conn = self._get_connection()
        try:
            stamp = ", resolved_at=CURRENT_TIMESTAMP" if status != "pending" else ", resolved_at=NULL"
            cur = conn.execute(
                f"UPDATE video_repair_findings SET status=?, user_action=?{stamp}, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, action, int(finding_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def repair_bulk_update_findings(self, ids, status: str) -> int:
        if status not in ("resolved", "dismissed") or not ids:
            return 0
        clean = [int(i) for i in ids]
        conn = self._get_connection()
        try:
            marks = ",".join("?" * len(clean))
            cur = conn.execute(
                f"UPDATE video_repair_findings SET status=?, resolved_at=CURRENT_TIMESTAMP, "
                f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks}) AND status='pending'",
                (status, *clean))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def repair_clear_findings(self, job_id=None, status=None) -> int:
        where, params = [], []
        if job_id:
            where.append("job_id=?")
            params.append(job_id)
        if status:
            where.append("status=?")
            params.append(status)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_connection()
        try:
            cur = conn.execute(f"DELETE FROM video_repair_findings{w}", params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def repair_counts(self) -> dict:
        conn = self._get_connection()
        try:
            out = {"pending": 0, "resolved": 0, "dismissed": 0, "total": 0, "by_job": {}}
            for r in conn.execute("SELECT status, COUNT(*) n FROM video_repair_findings "
                                  "GROUP BY status").fetchall():
                out[r["status"]] = r["n"]
                out["total"] += r["n"]
            for r in conn.execute("SELECT job_id, COUNT(*) n FROM video_repair_findings "
                                  "WHERE status='pending' GROUP BY job_id").fetchall():
                out["by_job"][r["job_id"]] = r["n"]
            return out
        finally:
            conn.close()

    def repair_dismiss_absent(self, job_id: str, finding_type: str, keep_entity_ids) -> int:
        """After a COMPLETE scan: dismiss PENDING findings whose entity the scan
        no longer produced — the situation changed (set shrank, file replaced,
        row removed by hand) or resolved itself entirely. Handled findings are
        never touched, and callers must skip this on a cancelled/errored scan
        (a partial enumeration would wrongly retire live findings)."""
        keep = {str(e) for e in (keep_entity_ids or [])}
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, entity_id FROM video_repair_findings "
                "WHERE job_id=? AND finding_type=? AND status='pending'",
                (job_id, finding_type)).fetchall()
            stale = [r["id"] for r in rows if (r["entity_id"] or "") not in keep]
            n = 0
            for i in range(0, len(stale), 500):
                chunk = stale[i:i + 500]
                marks = ",".join("?" * len(chunk))
                cur = conn.execute(
                    "UPDATE video_repair_findings SET status='dismissed', "
                    "user_action='superseded by a newer scan', resolved_at=CURRENT_TIMESTAMP, "
                    f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})", chunk)
                n += cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()

    def repair_sweep_stale_runs(self) -> int:
        """Close 'running' run rows left behind by a process death so the
        scheduler doesn't treat the job as forever-running."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_repair_job_runs SET status='completed', "
                "finished_at=COALESCE(finished_at, datetime('now')) WHERE status='running'")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def missing_episode_rows(self, include_specials: bool = False) -> list:
        """Every aired, monitored, un-owned episode of a LIBRARY show (the
        missing-episodes job's source). Terminal-status shows are included on
        purpose — 'ended' matters for watching-for-new, not for filling gaps."""
        w = "" if include_specials else "AND e.season_number > 0 "
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.show_id, e.season_number, e.episode_number, e.title, e.air_date, "
                "e.still_url, e.overview, s.title AS show_title, s.tmdb_id AS show_tmdb_id, "
                "s.server_source FROM episodes e JOIN shows s ON s.id = e.show_id "
                "WHERE e.has_file=0 AND e.monitored=1 AND s.server_source IS NOT NULL "
                "AND e.air_date IS NOT NULL AND e.air_date <= date('now') "
                + w +
                "ORDER BY s.title COLLATE NOCASE, e.season_number, e.episode_number").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def video_stored_file_path(self, kind: str, *, tmdb_id=None,
                               season=None, episode=None):
        """The server-reported path (+ size) of the library's existing file for
        this item (largest first): {"path", "size_bytes"} or None when nothing
        is owned. The path is the SERVER's filesystem view — feed both through
        core.video.path_resolver (the size is the identity proof; the resolver's
        callers REPLACE what they resolve to)."""
        if not tmdb_id:
            return None
        conn = self._get_connection()
        try:
            if kind == "movie":
                r = conn.execute(
                    "SELECT f.relative_path, f.size_bytes FROM media_files f "
                    "JOIN movies m ON m.id = f.movie_id "
                    "WHERE m.tmdb_id=? AND m.has_file=1 AND f.relative_path<>'' "
                    "ORDER BY f.size_bytes DESC LIMIT 1", (int(tmdb_id),)).fetchone()
            elif season is not None and episode is not None:
                r = conn.execute(
                    "SELECT f.relative_path, f.size_bytes FROM media_files f "
                    "JOIN episodes e ON e.id = f.episode_id "
                    "JOIN shows s ON s.id = e.show_id "
                    "WHERE s.tmdb_id=? AND e.season_number=? AND e.episode_number=? "
                    "AND e.has_file=1 AND f.relative_path<>'' "
                    "ORDER BY f.size_bytes DESC LIMIT 1",
                    (int(tmdb_id), int(season), int(episode))).fetchone()
            else:
                return None
            return ({"path": r["relative_path"], "size_bytes": r["size_bytes"]}
                    if r else None)
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    # ── repair-job source queries (movie-side jobs) ───────────────────────────
    def repair_movie_franchises(self) -> dict:
        """Owned movies grouped by TMDB collection:
        {collection_id: {"name", "movies": [{library_id, tmdb_id, title, year}]}}."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, tmdb_id, title, year, tmdb_collection_id, tmdb_collection_name "
                "FROM movies WHERE has_file=1 AND tmdb_collection_id IS NOT NULL "
                "AND tmdb_id IS NOT NULL ORDER BY year, title").fetchall()
        finally:
            conn.close()
        out: dict = {}
        for r in rows:
            g = out.setdefault(r["tmdb_collection_id"],
                               {"name": r["tmdb_collection_name"], "movies": []})
            if not g["name"] and r["tmdb_collection_name"]:
                g["name"] = r["tmdb_collection_name"]
            g["movies"].append({"library_id": r["id"], "tmdb_id": r["tmdb_id"],
                                "title": r["title"], "year": r["year"]})
        return out

    def repair_owned_movie_files(self) -> list:
        """Every owned movie with each of its files (quality/runtime checks)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, m.runtime_minutes, "
                "f.id AS file_id, f.relative_path, f.size_bytes, f.resolution, f.quality, "
                "f.video_codec, f.audio_codec, f.release_source, f.runtime_seconds "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                "WHERE m.has_file=1 ORDER BY m.title COLLATE NOCASE, f.size_bytes DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def repair_movie_metadata_gaps(self) -> list:
        """Owned movies with enrichment gaps: unmatched, or missing overview /
        genres / poster / backdrop. Returns the raw signals; the job decides
        (user-locked blank fields are deliberate and get filtered there)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, m.tmdb_match_status, "
                "m.locked_fields, "
                "(m.overview IS NULL OR m.overview='') AS no_overview, "
                "(m.poster_url IS NULL OR m.poster_url='') AS no_poster, "
                "(m.backdrop_url IS NULL OR m.backdrop_url='') AS no_backdrop, "
                "NOT EXISTS (SELECT 1 FROM movie_genres mg WHERE mg.movie_id=m.id) AS no_genres "
                "FROM movies m WHERE m.has_file=1 "
                "AND (m.tmdb_match_status IS NULL OR m.tmdb_match_status<>'matched' "
                "     OR m.overview IS NULL OR m.overview='' "
                "     OR m.poster_url IS NULL OR m.poster_url='' "
                "     OR m.backdrop_url IS NULL OR m.backdrop_url='' "
                "     OR NOT EXISTS (SELECT 1 FROM movie_genres mg WHERE mg.movie_id=m.id)) "
                "ORDER BY m.title COLLATE NOCASE").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def repair_duplicate_movies(self) -> dict:
        """Duplicate signals: {"rows": [[movie,…] same tmdb_id twice],
        "files": [{movie + its 2+ files}]} — both owned-only."""
        conn = self._get_connection()
        try:
            dup_rows = conn.execute(
                "SELECT m.id, m.tmdb_id, m.title, m.year, m.server_source, m.path "
                "FROM movies m WHERE m.has_file=1 AND m.tmdb_id IN ("
                "  SELECT tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL "
                "  GROUP BY tmdb_id HAVING COUNT(*)>1) ORDER BY m.tmdb_id, m.id").fetchall()
            multi = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, "
                "f.id AS file_id, f.relative_path, f.size_bytes, f.resolution, f.quality, "
                "f.video_codec FROM movies m JOIN media_files f ON f.movie_id=m.id "
                "WHERE m.has_file=1 AND m.id IN ("
                "  SELECT movie_id FROM media_files WHERE movie_id IS NOT NULL "
                "  GROUP BY movie_id HAVING COUNT(*)>1) "
                "ORDER BY m.id, f.size_bytes DESC").fetchall()
        finally:
            conn.close()
        by_tmdb: dict = {}
        for r in dup_rows:
            by_tmdb.setdefault(r["tmdb_id"], []).append(dict(r))
        by_movie: dict = {}
        for r in multi:
            by_movie.setdefault(r["movie_id"], []).append(dict(r))
        return {"rows": list(by_tmdb.values()), "files": list(by_movie.values())}

    def repair_watched_movies(self) -> list:
        """Every OWNED movie with its watch state + largest file — the
        watched-cleanup job's source (age filtering happens in the job)."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, "
                "m.play_count, m.last_viewed_at, m.added_at, "
                "f.relative_path, f.size_bytes "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                "WHERE m.has_file = 1 "
                "AND f.id = (SELECT f2.id FROM media_files f2 WHERE f2.movie_id = m.id "
                "            ORDER BY f2.size_bytes DESC LIMIT 1)")]
        finally:
            conn.close()

    def movie_file_paths(self, movie_id) -> list:
        """Every stored file for a movie ({relative_path, size_bytes}), largest
        first — so a cleanup recycles ALL versions of a multi-part movie, not
        just the biggest one (which would orphan the rest)."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT relative_path, size_bytes FROM media_files "
                "WHERE movie_id=? ORDER BY size_bytes DESC", (int(movie_id),))]
        finally:
            conn.close()

    def repair_mark_movie_fileless(self, movie_id) -> None:
        """After a cleanup fix recycled the movie's file: reflect reality now
        (has_file=0, file rows gone) instead of waiting for the weekly deep
        scan — exactly the state that scan would find."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM media_files WHERE movie_id=?", (int(movie_id),))
            conn.execute("UPDATE movies SET has_file=0, updated_at=datetime('now') WHERE id=?",
                         (int(movie_id),))
            conn.commit()
        finally:
            conn.close()

    def repair_library_files(self) -> list:
        """Every owned movie/episode file with the fields the naming templates
        need — the naming-conformance job's source."""
        conn = self._get_connection()
        try:
            movies = [dict(r) for r in conn.execute(
                "SELECT 'movie' AS scope, m.id AS item_id, f.id AS file_id, "
                "m.title, m.year, m.tmdb_id, NULL AS season, NULL AS episode, "
                "NULL AS episode_title, NULL AS series, "
                "f.relative_path, f.size_bytes, f.quality, f.resolution, f.video_codec "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                "WHERE m.has_file = 1")]
            eps = [dict(r) for r in conn.execute(
                "SELECT 'episode' AS scope, e.id AS item_id, f.id AS file_id, "
                "s.title AS title, NULL AS year, s.tmdb_id, "
                "e.season_number AS season, e.episode_number AS episode, "
                "e.title AS episode_title, s.title AS series, "
                "f.relative_path, f.size_bytes, f.quality, f.resolution, f.video_codec "
                "FROM episodes e JOIN shows s ON e.show_id = s.id "
                "JOIN media_files f ON f.episode_id = e.id "
                "WHERE e.has_file = 1")]
            return movies + eps
        finally:
            conn.close()

    def repair_stale_wishlist(self) -> list:
        """Wishlist rows whose target is ALREADY OWNED, annotated with the owned
        files' resolutions — the audit job judges them against the cutoff:
        owned-below-cutoff rows are LEGITIMATE (upgrade-until keeps them for a
        better copy); owned-at-cutoff and quality-unreadable rows are clutter."""
        conn = self._get_connection()
        try:
            movies = conn.execute(
                "SELECT w.id AS wishlist_id, 'movie' AS kind, w.tmdb_id, w.title, "
                "w.poster_url, NULL AS season_number, NULL AS episode_number, "
                "(SELECT m.id FROM movies m WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1 LIMIT 1) "
                "AS library_id, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM movies m "
                "  JOIN media_files f ON f.movie_id=m.id "
                "  WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) AS owned_resolutions "
                "FROM video_wishlist w WHERE w.kind='movie' "
                "AND w.tmdb_id IN (SELECT tmdb_id FROM movies WHERE has_file=1 "
                "AND tmdb_id IS NOT NULL)").fetchall()
            eps = conn.execute(
                "SELECT w.id AS wishlist_id, 'episode' AS kind, w.tmdb_id, w.title, "
                "w.poster_url, w.season_number, w.episode_number, w.library_id, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM episodes e "
                "  JOIN shows s ON e.show_id=s.id "
                "  JOIN media_files f ON f.episode_id=e.id "
                "  WHERE s.tmdb_id=w.tmdb_id AND e.season_number=w.season_number "
                "  AND e.episode_number=w.episode_number AND e.has_file=1) "
                "  AS owned_resolutions "
                "FROM video_wishlist w WHERE w.kind='episode' "
                "AND EXISTS (SELECT 1 FROM episodes e JOIN shows s ON e.show_id=s.id "
                "  WHERE s.tmdb_id=w.tmdb_id AND e.season_number=w.season_number "
                "  AND e.episode_number=w.episode_number AND e.has_file=1)").fetchall()
            return [dict(r) for r in movies] + [dict(r) for r in eps]
        finally:
            conn.close()

    def repair_record_job_start(self, job_id: str) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_repair_job_runs (job_id, started_at) "
                "VALUES (?, datetime('now'))", (job_id,))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def repair_record_job_finish(self, run_id: int, *, items_scanned=0, findings_created=0,
                                 auto_fixed=0, errors=0) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE video_repair_job_runs SET finished_at=datetime('now'), "
                "duration_seconds=(julianday(datetime('now'))-julianday(started_at))*86400, "
                "items_scanned=?, findings_created=?, auto_fixed=?, errors=?, "
                "status='completed' WHERE id=?",
                (int(items_scanned), int(findings_created), int(auto_fixed),
                 int(errors), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def repair_last_run(self, job_id: str):
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT * FROM video_repair_job_runs WHERE job_id=? "
                "ORDER BY started_at DESC, id DESC LIMIT 1", (job_id,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def repair_history(self, job_id=None, limit: int = 50) -> list:
        conn = self._get_connection()
        try:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM video_repair_job_runs WHERE job_id=? "
                    "ORDER BY started_at DESC, id DESC LIMIT ?", (job_id, int(limit))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM video_repair_job_runs ORDER BY started_at DESC, id DESC "
                    "LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Issues: user-reported problems on library items (music standard) ──────
    # Lifecycle open → in_progress → resolved | dismissed (reopenable). Resolved
    # issues are RETAINED. Non-admin reads are force-scoped to the caller.

    _ISSUE_UPDATABLE = {"status", "priority", "admin_response", "resolved_by",
                        "resolved_at", "title", "description", "category"}

    def create_issue(self, profile_id: int, entity_type: str, entity_id, category: str,
                     title: str, description: str = "", snapshot_data=None,
                     priority: str = "normal", reporter_name=None) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_issues (profile_id, reporter_name, entity_type, entity_id, "
                "category, title, description, snapshot_data, priority) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (int(profile_id), reporter_name, entity_type, str(entity_id), category,
                 title, description or "", json.dumps(snapshot_data or {}),
                 priority if priority in ("low", "normal", "high") else "normal"))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def _issue_row(r) -> dict:
        d = dict(r)
        try:
            d["snapshot_data"] = json.loads(d.get("snapshot_data") or "{}")
        except (ValueError, TypeError):
            d["snapshot_data"] = {}
        return d

    def get_issues(self, profile_id: int, status=None, category=None, entity_type=None,
                   limit: int = 100, offset: int = 0, is_admin: bool = False) -> list:
        """Open/urgent first (music ordering): open, in_progress, then the rest;
        high priority first inside each; newest first. Non-admin sees own only."""
        where, params = ["1=1"], []
        if not is_admin:
            where.append("profile_id=?")
            params.append(int(profile_id))
        for col, val in (("status", status), ("category", category),
                         ("entity_type", entity_type)):
            if val and val != "all":
                where.append(f"{col}=?")
                params.append(val)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_issues WHERE " + " AND ".join(where) +
                " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, "
                "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
                "created_at DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(200, int(limit or 100))), max(0, int(offset or 0)))
            ).fetchall()
            return [self._issue_row(r) for r in rows]
        finally:
            conn.close()

    def get_issue(self, issue_id: int):
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM video_issues WHERE id=?",
                             (int(issue_id),)).fetchone()
            return self._issue_row(r) if r else None
        finally:
            conn.close()

    def update_issue(self, issue_id: int, updates: dict) -> bool:
        fields = {k: v for k, v in (updates or {}).items() if k in self._ISSUE_UPDATABLE}
        if not fields:
            return False
        sets = ", ".join(f"{k}=?" for k in fields)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE video_issues SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), int(issue_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_issue(self, issue_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_issues WHERE id=?", (int(issue_id),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_issue_counts(self, is_admin: bool = False, profile_id: int = 1) -> dict:
        w, params = ("", []) if is_admin else (" WHERE profile_id=?", [int(profile_id)])
        conn = self._get_connection()
        try:
            out = {"open": 0, "in_progress": 0, "resolved": 0, "dismissed": 0, "total": 0}
            for r in conn.execute(
                    f"SELECT status, COUNT(*) n FROM video_issues{w} GROUP BY status", params):
                if r["status"] in out:
                    out[r["status"]] = r["n"]
                out["total"] += r["n"]
            return out
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
        """Explicitly follow a show/person/studio (state='follow'). Idempotent upsert on
        (kind, tmdb_id) — re-adding refreshes title/poster/library_id and clears
        any 'mute' tombstone. Returns True on success."""
        if kind not in ("show", "person", "studio") or not tmdb_id or not title:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute("SELECT state FROM video_watchlist WHERE kind=? AND tmdb_id=?",
                               (kind, int(tmdb_id))).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, library_id, state)
                   VALUES (?, ?, ?, ?, ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       library_id=COALESCE(excluded.library_id, video_watchlist.library_id)""",
                (kind, int(tmdb_id), title, poster_url, library_id))
            conn.commit()
            # A refresh-upsert of an existing follow is not a new follow.
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": kind, "title": title})
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

    def watchlist_continuing_shows(self, server_source=None) -> list[dict]:
        """Effective-watchlist shows that are IN the library (so they have an episodes table
        to refresh) and still airing — the set whose TMDB episode schedules the 'Refresh
        Airing TV Schedules' automation re-pulls so the calendar stays current. Skips
        tmdb-only follows (no episodes to refresh) and ended/canceled shows (no new episodes
        coming); unknown status is kept (never skip on uncertainty)."""
        terminal = ("ended", "canceled", "cancelled", "completed")
        conn = self._get_connection()
        try:
            rows = self._effective_shows(conn, server_source)
        finally:
            conn.close()
        out, seen = [], set()
        for r in rows:
            lib = r.get("library_id")
            if lib is None or lib in seen:
                continue
            if str(r.get("status") or "").strip().lower() in terminal:
                continue
            seen.add(lib)
            out.append({"library_id": lib, "tmdb_id": r.get("tmdb_id"),
                        "title": r.get("title"), "status": r.get("status")})
        return out

    def remove_from_watchlist(self, kind: str, tmdb_id: int) -> bool:
        """Un-follow. Stored as a 'mute' tombstone (not a delete) so an
        actively-airing library show — watched by default — is not silently
        re-added. Returns True."""
        if kind not in ("show", "person", "studio") or not tmdb_id:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute("SELECT state, title FROM video_watchlist WHERE kind=? AND tmdb_id=?",
                               (kind, int(tmdb_id))).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                   VALUES (?, ?, '', 'mute')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET state='mute'""",
                (kind, int(tmdb_id)))
            conn.commit()
            # Only an actual follow → mute transition is an unfollow event
            # (muting something never followed is just a tombstone write).
            if was and was["state"] == "follow":
                _publish_video_event("video_watchlist_removed",
                                     {"kind": kind, "title": was["title"] or ""})
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
        """Effective watchlist. Shows include the airing-library default; people and
        studios are explicit follows only."""
        conn = self._get_connection()
        try:
            people = []
            if kind in (None, "person"):
                for r in conn.execute(
                        "SELECT tmdb_id, title, poster_url, library_id, date_added, lookback_years "
                        "FROM video_watchlist WHERE kind='person' AND state='follow' "
                        "ORDER BY date_added DESC, id DESC"):
                    d = dict(r); d["kind"] = "person"; people.append(d)
            studios = []
            if kind in (None, "studio"):
                for r in conn.execute(
                        "SELECT tmdb_id, title, poster_url, library_id, date_added, lookback_years "
                        "FROM video_watchlist WHERE kind='studio' AND state='follow' "
                        "ORDER BY date_added DESC, id DESC"):
                    d = dict(r); d["kind"] = "studio"; studios.append(d)
            shows = self._effective_shows(conn, server_source) if kind in (None, "show") else []
            if kind == "person":
                return people
            if kind == "studio":
                return studios
            if kind == "show":
                return shows
            return shows + people + studios
        finally:
            conn.close()

    def watchlist_state(self, kind: str, tmdb_ids, server_source=None) -> dict:
        """{tmdb_id: True} for ids that are watched — explicit follow OR (for
        shows) an actively-airing library show that isn't muted. Hydrates buttons."""
        out: dict = {}
        ids = [int(x) for x in (tmdb_ids or []) if x]
        if kind not in ("show", "person", "studio") or not ids:
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
        """{'show': n, 'person': n, 'studio': n, 'total': n} over the EFFECTIVE watchlist."""
        shows = self.list_watchlist("show", server_source=server_source)
        people = self.list_watchlist("person")
        studios = self.list_watchlist("studio")
        return {"show": len(shows), "person": len(people), "studio": len(studios),
                "total": len(shows) + len(people) + len(studios)}

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
        items = self.list_watchlist(kind, server_source=server_source) if kind in ("show", "person", "studio") else []
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
                              library_id=None, server_source=None, status='wanted',
                              detail_json=None) -> bool:
        """Wish for a movie. Idempotent upsert on its tmdb id.

        ``status`` lets the watchlist-people scan add an UPCOMING (unreleased) movie as
        'monitored' so the wishlist engine skips it until it's out; a later scan re-adds it
        with 'wanted' once released, which PROMOTES it. We never downgrade a status the engine
        has already advanced (searching/downloading/downloaded/failed) and never knock a
        'wanted' back to 'monitored'.

        ``detail_json`` is a rich TMDB-detail blob (backdrop/overview/genres/cast/etc.,
        captured at add time) so the wishlist renders a full card without re-fetching; it's
        only filled in, never wiped, on re-add.
        """
        if not tmdb_id or not title:
            return False
        # NOTE: release_date (the downloadable/home-availability date) is NOT set here — TMDB's
        # primary detail date is theatrical/premiere, which mis-gates cinema-only films. The
        # wishlist drain's availability backfill owns it (digital/physical, or wide-theatrical +
        # window), leaving it NULL here so the backfill picks the movie up.
        if isinstance(detail_json, (dict, list)):
            import json as _json
            detail_json = _json.dumps(detail_json)
        conn = self._get_connection()
        try:
            existed = conn.execute(
                "SELECT 1 FROM video_wishlist WHERE kind='movie' AND tmdb_id=?",
                (int(tmdb_id),)).fetchone()
            conn.execute(
                """INSERT INTO video_wishlist (kind, tmdb_id, title, poster_url, year, library_id, server_source, status, detail_json)
                   VALUES ('movie', ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tmdb_id) WHERE kind='movie' DO UPDATE SET
                       title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                       year=COALESCE(excluded.year, video_wishlist.year),
                       library_id=COALESCE(excluded.library_id, video_wishlist.library_id),
                       detail_json=COALESCE(excluded.detail_json, video_wishlist.detail_json),
                       status=CASE WHEN video_wishlist.status='monitored' AND excluded.status='wanted'
                                   THEN 'wanted' ELSE video_wishlist.status END""",
                (int(tmdb_id), title, poster_url, year, library_id, server_source, status, detail_json))
            conn.commit()
            if not existed:   # a refresh-upsert is not a new wish
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "movie", "title": title, "count": 1})
            return True
        except Exception:
            logger.exception("add_movie_to_wishlist failed (%s)", tmdb_id)
            return False
        finally:
            conn.close()

    def watchlist_states(self, kind: str) -> dict:
        """{tmdb_id: state} for every watchlist row of a kind — lets the
        collections tie-in skip shows already followed AND respect 'mute'
        tombstones (a muted show must never be re-followed by automation)."""
        conn = self._get_connection()
        try:
            return {int(r["tmdb_id"]): r["state"] for r in conn.execute(
                "SELECT tmdb_id, state FROM video_watchlist "
                "WHERE kind=? AND tmdb_id IS NOT NULL", (kind,))}
        except Exception:
            logger.exception("watchlist_states failed")
            return {}
        finally:
            conn.close()

    def wishlisted_movie_status(self) -> dict:
        """{tmdb_id: status} for every movie on the wishlist. Lets the watchlist-people
        scan skip movies it's already handled (fast re-runs) and spot 'monitored' rows
        that are now releasable so it can promote them."""
        conn = self._get_connection()
        try:
            return {int(r["tmdb_id"]): r["status"] for r in conn.execute(
                "SELECT tmdb_id, status FROM video_wishlist WHERE kind='movie' AND tmdb_id IS NOT NULL")}
        except Exception:
            logger.exception("wishlisted_movie_status failed")
            return {}
        finally:
            conn.close()

    def movie_wishlist_to_download(self) -> list:
        """Wished movies ready to grab: only ``status='wanted'`` (released) — skips
        'monitored' (unreleased). OWNED titles are included, annotated with
        ``owned`` + ``owned_resolutions`` (the library files' resolutions,
        comma-joined) — the 'upgrade until cutoff' semantics: the drain skips
        owned items that already meet the cutoff and only accepts strictly
        better releases for the rest, which is what keeps the old
        owned-re-download loop broken. Newest year first."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.year, w.poster_url, "
                "EXISTS (SELECT 1 FROM movies m WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) "
                "  AS owned, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM movies m "
                "  JOIN media_files f ON f.movie_id=m.id "
                "  WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) AS owned_resolutions "
                "FROM video_wishlist w "
                "WHERE w.kind='movie' AND w.status='wanted' AND w.tmdb_id IS NOT NULL "
                # release-window gate: only search once within a week of release (early scene
                # releases appear a few days out). A further-off movie stays wished but isn't
                # hunted — no risk of grabbing a wrong-titled or fake 'release' before it exists.
                # Unknown release date → allow (the year check on the release still guards).
                "AND (w.release_date IS NULL OR w.release_date <= date('now', '+7 days')) "
                "ORDER BY w.year DESC, w.id DESC")]
        finally:
            conn.close()

    def wishlist_movies_missing_release_date(self, limit=25) -> list:
        """Wished (wanted) movies whose downloadable date hasn't been resolved yet — the drain's
        availability backfill fills these (via TMDB) before it gates. Returns [tmdb_id, ...]."""
        conn = self._get_connection()
        try:
            return [int(r["tmdb_id"]) for r in conn.execute(
                "SELECT tmdb_id FROM video_wishlist WHERE kind='movie' AND status='wanted' "
                "AND tmdb_id IS NOT NULL AND release_date IS NULL ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),))]
        except sqlite3.Error:
            logger.exception("wishlist_movies_missing_release_date failed")
            return []
        finally:
            conn.close()

    def set_wishlist_release_date(self, tmdb_id, release_date) -> None:
        """Record a wished movie's downloadable ('available') date — drives the release-window
        gate. Pass the sentinel '1970-01-01' for 'checked, but TMDB has no date' so it isn't
        re-queried forever yet still searches (the year check guards)."""
        if not tmdb_id or not release_date:
            return
        conn = self._get_connection()
        try:
            conn.execute("UPDATE video_wishlist SET release_date=? WHERE kind='movie' AND tmdb_id=?",
                         (str(release_date), int(tmdb_id)))
            conn.commit()
        except sqlite3.Error:
            logger.exception("set_wishlist_release_date failed for %s", tmdb_id)
        finally:
            conn.close()

    def clear_wishlist_movie_release_dates(self) -> int:
        """Wipe every wished movie's derived availability date so the backfill re-derives them
        with the current logic (used once when that logic changes). Returns rows cleared."""
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_wishlist SET release_date=NULL "
                               "WHERE kind='movie' AND release_date IS NOT NULL")
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            logger.exception("clear_wishlist_movie_release_dates failed")
            return 0
        finally:
            conn.close()

    def episode_wishlist_to_download(self) -> list:
        """Wished episodes READY to grab: aired (or air-date-unknown), never episodes still in
        the future. Upcoming episodes CAN sit on the wishlist now (e.g. pre-ordered from the
        calendar), but the drain must not hunt for a release that can't exist yet — so this
        skips ``air_date`` in the future. OWNED episodes are included with the same
        ``owned``/``owned_resolutions`` annotation as movies (upgrade-until semantics; the
        drain does the cutoff/strictly-better judging). Newest air date first."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id AS show_tmdb_id, w.title AS show_title, w.season_number, "
                "w.episode_number, w.episode_title, w.air_date, w.poster_url, w.library_id, "
                "EXISTS (SELECT 1 FROM episodes e JOIN shows s ON e.show_id = s.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) AS owned, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM episodes e "
                "  JOIN shows s ON e.show_id = s.id "
                "  JOIN media_files f ON f.episode_id = e.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) "
                "  AS owned_resolutions "
                "FROM video_wishlist w WHERE w.kind='episode' AND w.tmdb_id IS NOT NULL "
                # release-window gate: search an episode only once it's within a week of air
                # (early scene releases show up a few days out) — a further-off episode stays on
                # the wishlist but isn't searched yet, so the drain never hunts a release that
                # can't exist. Unknown air date → allow (can't prove it's future).
                "AND (w.air_date IS NULL OR w.air_date <= date('now', '+7 days')) "
                "ORDER BY w.air_date DESC, w.id DESC")]
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
            before_count = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='episode' AND tmdb_id=?",
                (int(show_tmdb_id),)).fetchone()[0]
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
            new_rows = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='episode' AND tmdb_id=?",
                (int(show_tmdb_id),)).fetchone()[0] - before_count
            conn.commit()
            if new_rows > 0:   # refresh-upserts of already-wished episodes don't fire
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "episode", "title": show_title, "count": new_rows})
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

    def wishlist_movies_missing_art(self, limit: int = 40) -> list:
        """Movie wishlist rows with no poster yet — added while upcoming, before TMDB had art.
        Bounded; the movie art-backfill re-fetches these so they stop rendering art-less."""
        conn = self._get_connection()
        try:
            return [{"tmdb_id": r["tmdb_id"], "title": r["title"]} for r in conn.execute(
                "SELECT tmdb_id, title FROM video_wishlist "
                "WHERE kind='movie' AND tmdb_id IS NOT NULL "
                "AND (poster_url IS NULL OR TRIM(poster_url)='') "
                "ORDER BY id DESC LIMIT ?", (int(limit),))]
        except sqlite3.Error:
            logger.exception("wishlist_movies_missing_art failed")
            return []
        finally:
            conn.close()

    def set_wishlist_movie_art(self, tmdb_id, poster_url=None, year=None) -> bool:
        """Fill a wishlist movie's poster / year once TMDB has them. Only fills blanks
        (COALESCE keeps any art already there), so it never clobbers. Returns True if updated."""
        if not poster_url and not year:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET poster_url=COALESCE(poster_url, ?), year=COALESCE(year, ?) "
                "WHERE kind='movie' AND tmdb_id=?",
                (poster_url or None, year or None, int(tmdb_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_wishlist_movie_art failed (%s)", tmdb_id)
            return False
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
            was = conn.execute(
                "SELECT state FROM video_watchlist WHERE kind='channel' AND source_id=?",
                (cid,)).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('channel', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(cid), title, channel.get("avatar_url"), cid))
            conn.commit()
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": "channel", "title": title})
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
            was = conn.execute(
                "SELECT title FROM video_watchlist WHERE kind='channel' AND source_id=? AND state='follow'",
                (youtube_id,)).fetchone()
            cur = conn.execute("DELETE FROM video_watchlist WHERE kind='channel' AND source_id=?", (youtube_id,))
            conn.commit()
            if was and cur.rowcount:
                _publish_video_event("video_watchlist_removed",
                                     {"kind": "channel", "title": was["title"] or ""})
            return True
        finally:
            conn.close()

    def get_watchlist_lookback(self, kind: str, tmdb_id) -> dict | None:
        """A followed person/studio's back-catalog window: {tmdb_id, title, date_added,
        lookback_years} (0/NULL = forward-only, N = years, -1 = everything). None if not
        followed. Shared by the person + studio settings modals."""
        if kind not in ("person", "studio"):
            return None
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id, title, date_added, lookback_years FROM video_watchlist "
                             "WHERE kind=? AND tmdb_id=? AND state='follow'",
                             (kind, int(tmdb_id))).fetchone()
            if not r:
                return None
            d = dict(r)
            d["lookback_years"] = int(d["lookback_years"]) if d["lookback_years"] is not None else 0
            return d
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("get_watchlist_lookback failed (%s %s)", kind, tmdb_id)
            return None
        finally:
            conn.close()

    def set_watchlist_lookback(self, kind: str, tmdb_id, lookback_years) -> bool:
        """Set a followed person/studio's back-catalog window (0=forward-only, N=years,
        -1=everything). Returns True if a followed row was updated."""
        if kind not in ("person", "studio"):
            return False
        try:
            lb = int(lookback_years)
        except (TypeError, ValueError):
            return False
        lb = max(-1, min(100, lb))
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_watchlist SET lookback_years=? "
                               "WHERE kind=? AND tmdb_id=? AND state='follow'",
                               (lb, kind, int(tmdb_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_watchlist_lookback failed (%s %s)", kind, tmdb_id)
            return False
        finally:
            conn.close()

    # kind-specific delegates (keep the existing person call sites + a studio pair).
    def get_person_lookback(self, tmdb_id) -> dict | None:
        return self.get_watchlist_lookback("person", tmdb_id)

    def set_person_lookback(self, tmdb_id, lookback_years) -> bool:
        return self.set_watchlist_lookback("person", tmdb_id, lookback_years)

    def get_studio_lookback(self, tmdb_id) -> dict | None:
        return self.get_watchlist_lookback("studio", tmdb_id)

    def set_studio_lookback(self, tmdb_id, lookback_years) -> bool:
        return self.set_watchlist_lookback("studio", tmdb_id, lookback_years)

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

    def query_channel_library(self, *, search=None, letter=None, sort="title",
                              page=1, limit=75) -> dict:
        """One page of the Library's Channels tab: FOLLOWED channels ∪ channels
        you HAVE DOWNLOADS FROM (a library shows what you own — a one-off grab
        from an unfollowed channel still belongs here). Same paged shape as
        query_library. ``owned_count`` = completed downloads in the permanent
        history; ``video_count`` = remembered catalog size; unfollowed channels
        fill title/avatar from the channel-meta cache."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(500, int(limit or 75)))
        except (TypeError, ValueError):
            page, limit = 1, 75
        base = (
            "WITH chans AS ("
            "  SELECT w.source_id AS cid, w.title AS wl_title, w.poster_url AS wl_poster, "
            "         1 AS followed, w.date_added AS added "
            "  FROM video_watchlist w WHERE w.kind='channel' AND w.state='follow' "
            "  UNION ALL "
            # ownership = ON DISK (pruned excluded): an unfollowed channel whose
            # every download was deleted/ghost-cleaned leaves the library tab.
            "  SELECT h.channel_id, NULL, NULL, 0, MAX(h.completed_at) "
            "  FROM video_download_history h "
            "  WHERE h.source='youtube' AND h.outcome='completed' AND h.channel_id IS NOT NULL "
            "    AND h.pruned_at IS NULL "
            "    AND h.channel_id NOT IN (SELECT source_id FROM video_watchlist "
            "                             WHERE kind='channel' AND state='follow') "
            "  GROUP BY h.channel_id"
            "), named AS ("
            "  SELECT c.cid, COALESCE(c.wl_title, m.title, c.cid) AS title, "
            "         COALESCE(c.wl_poster, m.avatar_url) AS poster_url, "
            "         c.followed, c.added "
            "  FROM chans c LEFT JOIN youtube_channel_meta m ON m.channel_id = c.cid"
            ") ")
        where, params = [], []
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + str(search) + "%")
        if letter and letter != "all":
            if letter == "#":
                where.append("substr(UPPER(title), 1, 1) NOT BETWEEN 'A' AND 'Z'")
            else:
                where.append("title LIKE ? COLLATE NOCASE")
                params.append(str(letter) + "%")
        w = (" WHERE " + " AND ".join(where)) if where else ""
        order = "added DESC" if sort == "added" else "title COLLATE NOCASE"
        conn = self._get_connection()
        try:
            total = conn.execute(base + f"SELECT COUNT(*) FROM named{w}", params).fetchone()[0]
            rows = conn.execute(
                base + "SELECT cid, title, poster_url, followed, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv "
                "  WHERE cv.channel_id = named.cid) AS video_count, "
                "(SELECT COUNT(DISTINCT h.media_id) FROM video_download_history h "
                "  WHERE h.source='youtube' AND h.outcome='completed' "
                "  AND h.pruned_at IS NULL "
                "  AND h.channel_id = named.cid) AS owned_count "
                f"FROM named{w} ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, limit, (page - 1) * limit)).fetchall()
            pages = max(1, (total + limit - 1) // limit)
            return {"items": [{"kind": "channel", "id": r["cid"],
                               "title": r["title"], "poster_url": r["poster_url"],
                               "followed": bool(r["followed"]),
                               "video_count": r["video_count"],
                               "owned_count": r["owned_count"]} for r in rows],
                    "pagination": {"page": page, "total_pages": pages, "total_count": total,
                                   "has_prev": page > 1, "has_next": page < pages}}
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
            was = conn.execute(
                "SELECT state FROM video_watchlist WHERE kind='playlist' AND source_id=?",
                (pid,)).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('playlist', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(pid), title, (playlist or {}).get("thumbnail_url"), pid))
            conn.commit()
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": "playlist", "title": title})
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
            was = conn.execute(
                "SELECT title FROM video_watchlist WHERE kind='playlist' AND source_id=? AND state='follow'",
                (playlist_id,)).fetchone()
            cur = conn.execute("DELETE FROM video_watchlist WHERE kind='playlist' AND source_id=?", (playlist_id,))
            conn.commit()
            if was and cur.rowcount:
                _publish_video_event("video_watchlist_removed",
                                     {"kind": "playlist", "title": was["title"] or ""})
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

    def add_videos_to_wishlist(self, channel: dict, videos: list, *, server_source=None,
                               allow_downloaded: bool = False) -> int:
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
            # Don't re-wish videos already downloaded — re-following a channel must
            # never re-queue everything you already have. ``allow_downloaded`` is
            # the DELIBERATE-single-click exception: with the ✓ downloaded marker
            # visible, a manual wish on an owned video means "get it again"
            # (silently no-oping that click was a dead button).
            downloaded = set() if allow_downloaded else {r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL")}
            before_count = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (cid,)).fetchone()[0]
            for v in videos:
                vid = v.get("youtube_id")
                if not vid or vid in downloaded:
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
            new_rows = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (cid,)).fetchone()[0] - before_count
            conn.commit()
            if new_rows > 0:   # refresh-upserts of already-wished videos don't fire
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "youtube", "title": ctitle, "count": new_rows})
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

    def get_channel_settings(self, channel_id) -> dict:
        """Per-channel YouTube overrides — ``{custom_name?, quality?}`` — or {} if none.
        ``custom_name`` overrides the show-name (the ``$channel`` folder token); ``quality``
        is a youtube_quality profile that forces a different quality than the global default
        for this channel. Kept in the settings KV store, so no schema change."""
        if not channel_id:
            return {}
        raw = self.get_setting("youtube_channel_settings:" + str(channel_id))
        if not raw:
            return {}
        try:
            import json
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def set_channel_settings(self, channel_id, settings: dict) -> bool:
        """Persist per-channel overrides (or clear them with an empty/blank dict)."""
        if not channel_id:
            return False
        import json
        clean = settings if isinstance(settings, dict) else {}
        # Drop empty values so a blank form clears the override rather than storing noise.
        clean = {k: v for k, v in clean.items() if v not in (None, "", {})}
        self.set_setting("youtube_channel_settings:" + str(channel_id), json.dumps(clean))
        return True

    def get_playlist_seen(self, playlist_id) -> list:
        """The video ids already accounted for in a followed playlist — the membership
        baseline captured on the first scan (+ additions since). A current member NOT in
        this set is a genuine new addition. Empty until the first scan baselines it."""
        raw = self.get_setting("youtube_playlist_seen:" + str(playlist_id or ""))
        if not raw:
            return []
        try:
            import json
            d = json.loads(raw)
            return [str(x) for x in d] if isinstance(d, list) else []
        except (ValueError, TypeError):
            return []

    def add_playlist_seen(self, playlist_id, video_ids) -> None:
        """Fold ids into a playlist's seen-baseline (union; never shrinks) so later scans
        only surface members added after this point."""
        pid = str(playlist_id or "")
        add = [str(v) for v in (video_ids or []) if v]
        if not pid or not add:
            return
        import json
        merged = set(self.get_playlist_seen(pid))
        merged.update(add)
        self.set_setting("youtube_playlist_seen:" + pid, json.dumps(sorted(merged)))

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

    def youtube_wishlist_to_download(self, limit: int = 0) -> list:
        """Flat list of wished YouTube videos for the fulfillment worker to grab, newest
        upload first. Each carries what organising + the download row need: the video id,
        its channel, the video title, thumbnail, and upload date. (A completed download
        removes its wishlist row, so this list naturally shrinks as the queue drains.)"""
        conn = self._get_connection()
        try:
            sql = ("SELECT source_id AS video_id, parent_source_id AS channel_id, "
                   "title AS channel_title, episode_title AS video_title, "
                   "still_url AS thumbnail_url, air_date AS published_at "
                   "FROM video_wishlist WHERE kind='video' AND source='youtube' "
                   "AND source_id IS NOT NULL ORDER BY air_date DESC, id DESC")
            if limit and int(limit) > 0:
                sql += " LIMIT %d" % int(limit)
            return [dict(r) for r in conn.execute(sql)]
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
            "sort_title": m["sort_title"],
            "locked_fields": sorted(self._parse_locked(m["locked_fields"])),
            "watched": (m["play_count"] or 0) > 0,
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
                      status="all", genre=None, page=1, limit=75, server_source=None) -> dict:
        """One page of movies/shows with search + A–Z + sort + owned/wanted/
        watched + genre filtering done in SQL. Scoped to ``server_source`` (the
        active video server) so Plex and Jellyfin libraries never commingle —
        mirrors how the music side keeps servers separate.
        Returns {items, pagination:{...}}."""
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
        if genre:
            jt, fk = ("show_genres", "show_id") if is_shows else ("movie_genres", "movie_id")
            where.append(f"EXISTS (SELECT 1 FROM {jt} xg JOIN genres g ON g.id=xg.genre_id "
                         f"WHERE xg.{fk}={alias}.id AND g.name = ? COLLATE NOCASE)")
            params.append(genre)
        if not is_shows:
            if status == "owned":
                where.append("m.has_file = 1")
            elif status == "wanted":
                where.append("m.has_file = 0")
            elif status == "watched":
                where.append("COALESCE(m.play_count, 0) > 0")
            elif status == "unwatched":
                # owned-but-untouched — the "what should I put on tonight" filter
                where.append("m.has_file = 1 AND COALESCE(m.play_count, 0) = 0")
        else:
            if status == "owned":
                where.append("EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            elif status == "wanted":
                where.append("NOT EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            elif status == "watched":
                where.append("COALESCE(s.watched_episodes, 0) > 0")
            elif status == "unwatched":
                where.append("COALESCE(s.watched_episodes, 0) = 0 AND "
                             "EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        title_key = f"COALESCE({alias}.sort_title, {alias}.title) COLLATE NOCASE ASC"
        # display rating: IMDb once OMDb has synced it, TMDB audience score before (both 0-10)
        rating_key = f"COALESCE({alias}.imdb_rating, {alias}.rating)"
        order_sql = {
            "title": title_key,
            "year": f"{alias}.year DESC, " + title_key,
            "added": f"{alias}.added_at DESC",
            "rating": f"{rating_key} IS NULL, {rating_key} DESC, " + title_key,
        }.get(sort, title_key)

        if is_shows:
            select = ("SELECT s.id, s.title, s.year, s.tmdb_id, s.status, "
                      f"{rating_key} AS rating, s.watched_episodes, "
                      "(s.poster_url IS NOT NULL AND s.poster_url <> '') AS has_poster, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id) AS episode_count, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) AS owned_count "
                      "FROM shows s")
        else:
            select = ("SELECT m.id, m.title, m.year, m.has_file, m.tmdb_id, "
                      f"{rating_key} AS rating, m.play_count, "
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

    def library_genres(self, kind: str, server_source=None) -> list:
        """Distinct genre names in use for movies/shows — feeds the library page's
        genre filter dropdown (only genres that would actually match something)."""
        is_shows = kind == "shows"
        jt, fk = ("show_genres", "show_id") if is_shows else ("movie_genres", "movie_id")
        tbl, alias = ("shows", "s") if is_shows else ("movies", "m")
        sql = (f"SELECT DISTINCT g.name FROM genres g "
               f"JOIN {jt} xg ON xg.genre_id=g.id "
               f"JOIN {tbl} {alias} ON {alias}.id=xg.{fk}")
        params = []
        if server_source:
            sql += f" WHERE {alias}.server_source = ?"
            params.append(server_source)
        sql += " ORDER BY g.name COLLATE NOCASE"
        conn = self._get_connection()
        try:
            return [r[0] for r in conn.execute(sql, params)]
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
