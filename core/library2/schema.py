"""Schema + migration helpers for the Library Manager v2 subsystem.

Why a parallel schema?
----------------------
The legacy library (``artists`` / ``albums`` / ``tracks`` in
``database/music_database.py``) is a read-only mirror of the media server: artist
is a single string FK, every track has exactly one ``artist_id`` / ``album_id`` and
one nullable ``file_path``. It cannot express multi-artist tracks, monitoring,
import/processing status, the single-vs-album relationship, or the separation
between "track metadata" and "the physical file".

Library v2 models these explicitly, Lidarr-style, by separating three concerns:

- **Metadata**  — what the recording *is* (``lib2_artists`` / ``lib2_albums`` /
  ``lib2_tracks``), independent of any file.
- **Configuration** — what the *user* wants (``monitored`` / ``monitor_new_items``
  flags live on the metadata rows).
- **Physical file** — what is actually on disk (``lib2_track_files``), linked to a
  track but able to exist before a link is made (manual-import staging) and able to
  represent the same recording appearing as both a single and an album track.

Multi-artist is modelled with junction tables (``lib2_album_artists`` /
``lib2_track_artists``) so a song by two artists is stored once but shows under
both.

The schema is created idempotently — ``ensure_library_v2_schema`` runs
``CREATE TABLE IF NOT EXISTS`` at startup, so existing installs upgrade silently.
The caller owns the transaction (we don't commit), so this composes with the other
schema-init steps in ``MusicDatabase._initialize_database``.
"""

from __future__ import annotations

from typing import Any

from utils.logging_config import get_logger

logger = get_logger("database.library2_schema")


# --- Artists -----------------------------------------------------------------
# One row per artist. ``monitored`` / ``monitor_new_items`` are user config;
# everything else is metadata. ``external_ids`` is a JSON object keyed by source
# ('spotify'|'musicbrainz'|'deezer'|...) for the long tail; the two IDs the
# importer dedupes on get their own indexed columns.
LIB2_ARTISTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT,
    spotify_id TEXT,
    musicbrainz_id TEXT,
    external_ids TEXT NOT NULL DEFAULT '{}',
    image_url TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    monitored INTEGER NOT NULL DEFAULT 1,
    monitor_new_items TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'none' | 'new'
    quality_profile_id INTEGER NOT NULL DEFAULT 1,
    legacy_artist_id INTEGER,                         -- source row in legacy `artists`
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# --- Quality profiles --------------------------------------------------------
# Library v2 uses the APP-WIDE ``quality_profiles`` table (core/quality/schema.py)
# — the same rows the wishlist/download/import pipeline resolves live via
# ``core/quality/selection.load_profile_by_id``. The ``quality_profile_id``
# columns on lib2 rows are plain pointers into that table. (An earlier draft
# kept a parallel ``lib2_quality_profiles`` table; it predated the app-wide
# table's extraction and meant lib2 assignments never reached the pipeline —
# ``_migrate_lib2_profiles_to_app_wide`` below converges old installs.)

# --- Albums (releases; a "single" is an album row with type='single') --------
LIB2_ALBUMS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_artist_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    album_type TEXT NOT NULL DEFAULT 'album',         -- 'album'|'single'|'ep'|'compilation'|'live'|...
    secondary_types TEXT NOT NULL DEFAULT '[]',       -- JSON array of extra tags
    release_date TEXT,
    year INTEGER,
    spotify_id TEXT,
    musicbrainz_id TEXT,
    external_ids TEXT NOT NULL DEFAULT '{}',
    image_url TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    track_count INTEGER,
    expected_track_count INTEGER,                      -- true total from metadata (for have/missing)
    tracklist_json TEXT,                               -- cached canonical tracklist (missing-track titles)
    origin TEXT NOT NULL DEFAULT 'library',            -- 'library' (has/had files) | 'discography' (provider-only)
    monitored INTEGER NOT NULL DEFAULT 1,
    quality_profile_id INTEGER NOT NULL DEFAULT 1,
    legacy_album_id INTEGER,                           -- source row in legacy `albums`
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (primary_artist_id) REFERENCES lib2_artists(id) ON DELETE CASCADE
)
"""

# --- Album <-> Artist junction (multi-artist albums) -------------------------
LIB2_ALBUM_ARTISTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_album_artists (
    album_id INTEGER NOT NULL,
    artist_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'primary',             -- 'primary'|'featured'|'various'
    PRIMARY KEY (album_id, artist_id),
    FOREIGN KEY (album_id) REFERENCES lib2_albums(id) ON DELETE CASCADE,
    FOREIGN KEY (artist_id) REFERENCES lib2_artists(id) ON DELETE CASCADE
)
"""

# --- Tracks (metadata, file-independent) -------------------------------------
# ``canonical_track_id`` links the same recording across releases (e.g. a single
# that also appears on an album). NULL = this row is its own canonical. The dedup
# UI uses this to offer keep-single / keep-album / move / remove without losing
# the relationship.
LIB2_TRACKS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    track_number INTEGER,
    disc_number INTEGER DEFAULT 1,
    duration INTEGER,                                 -- milliseconds
    isrc TEXT,
    musicbrainz_id TEXT,
    spotify_id TEXT,                                  -- for wishlist mirroring
    monitored INTEGER NOT NULL DEFAULT 1,
    quality_profile_id INTEGER NOT NULL DEFAULT 1,
    canonical_track_id INTEGER,                       -- self-ref; NULL = canonical
    legacy_track_id INTEGER,                          -- source row in legacy `tracks`
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (album_id) REFERENCES lib2_albums(id) ON DELETE CASCADE,
    FOREIGN KEY (canonical_track_id) REFERENCES lib2_tracks(id) ON DELETE SET NULL
)
"""

# --- Track <-> Artist junction (multi-artist tracks) -------------------------
LIB2_TRACK_ARTISTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_track_artists (
    track_id INTEGER NOT NULL,
    artist_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'primary',             -- 'primary'|'featured'
    position INTEGER NOT NULL DEFAULT 0,              -- credited order
    PRIMARY KEY (track_id, artist_id),
    FOREIGN KEY (track_id) REFERENCES lib2_tracks(id) ON DELETE CASCADE,
    FOREIGN KEY (artist_id) REFERENCES lib2_artists(id) ON DELETE CASCADE
)
"""

# --- Track files (physical files; the DB-row <-> file link) ------------------
# A file row can exist before it is linked to a track (``track_id`` NULL) for
# manual-import staging. ``import_status`` / ``processing_status`` /
# ``verification_status`` capture pipeline state; ``tags_json`` /
# ``missing_tags_json`` / ``metadata_gaps_json`` cache the computed tag picture so
# the UI doesn't re-read every file on each request. ``content_hash`` powers
# duplicate / single-also-on-album detection.
LIB2_TRACK_FILES_DDL = """
CREATE TABLE IF NOT EXISTS lib2_track_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER,
    path TEXT NOT NULL,
    original_path TEXT,                               -- staging / download origin
    size INTEGER,
    bitrate INTEGER,
    sample_rate INTEGER,
    bit_depth INTEGER,
    format TEXT,                                      -- 'flac'|'mp3'|'m4a'|...
    quality_tier TEXT,                                -- computed: 'lossless'|'lossy_high'|...
    source TEXT,                                      -- where it came from (soulseek|tidal|...)
    import_status TEXT NOT NULL DEFAULT 'imported',   -- 'imported'|'staged'|'pending'|'failed'
    processing_status TEXT,                           -- mirrors download pipeline state
    verification_status TEXT,                         -- 'verified'|'unverified'|'force_imported'
    acoustid_status TEXT,                             -- 'pass'|'skip'|'fail'|NULL
    tags_json TEXT NOT NULL DEFAULT '{}',             -- present tags snapshot
    missing_tags_json TEXT NOT NULL DEFAULT '[]',     -- list of missing tag keys
    metadata_gaps_json TEXT NOT NULL DEFAULT '[]',    -- list of gap descriptors
    content_hash TEXT,                                -- for dedup / single-vs-album
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (track_id) REFERENCES lib2_tracks(id) ON DELETE SET NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_lib2_artists_name ON lib2_artists(name)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_artists_spotify ON lib2_artists(spotify_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_artists_mbid ON lib2_artists(musicbrainz_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_artists_legacy ON lib2_artists(legacy_artist_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_albums_artist ON lib2_albums(primary_artist_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_albums_type ON lib2_albums(album_type)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_albums_legacy ON lib2_albums(legacy_album_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_albums_origin ON lib2_albums(origin)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_album_artists_artist ON lib2_album_artists(artist_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_tracks_album ON lib2_tracks(album_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_tracks_isrc ON lib2_tracks(isrc)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_tracks_canonical ON lib2_tracks(canonical_track_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_tracks_legacy ON lib2_tracks(legacy_track_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_track_artists_artist ON lib2_track_artists(artist_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_track_files_track ON lib2_track_files(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_track_files_hash ON lib2_track_files(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_track_files_path ON lib2_track_files(path)",
    "CREATE INDEX IF NOT EXISTS idx_lib2_mirror_outbox_status ON lib2_mirror_outbox(status)",
)

# Audit log: when a user manually downloads while skipping checks (AcoustID /
# quality) that the quality profile would otherwise enforce, we record it so the
# user is on record as responsible and later cleanup/repair jobs can respect (or
# re-offer) the override instead of silently re-flagging the file.
LIB2_MANUAL_SKIPS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_manual_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_key TEXT,                                 -- 'username::filename'
    file_path TEXT,
    title TEXT,
    artist TEXT,
    skipped_checks TEXT NOT NULL DEFAULT '[]',        -- JSON: ['acoustid','quality',...]
    profile_id INTEGER,                               -- profile in effect, if any
    reason TEXT NOT NULL DEFAULT 'manual_download',
    acknowledged INTEGER NOT NULL DEFAULT 0,          -- cleanup jobs flip this
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Transactional outbox for lib2 → legacy watchlist/wishlist mirroring (audit
# P0-04 / ADR-02). The outbox row is written in the SAME transaction as the
# lib2 monitor-flag change; a worker replays it against the legacy tables and
# records the outcome. A mirror failure is therefore visible and retryable
# instead of silently leaving lib2 and the wishlist in split-brain.
LIB2_MIRROR_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS lib2_mirror_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL,                     -- 'wishlist_add'|'wishlist_remove'|'watchlist_add'|'watchlist_remove'
    payload TEXT NOT NULL DEFAULT '{}',   -- JSON: everything the op needs (resolved at enqueue time)
    profile_id INTEGER NOT NULL DEFAULT 1,
    user_initiated INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'done'|'failed'
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
)
"""

_ALL_DDL = (
    LIB2_ARTISTS_DDL,
    LIB2_ALBUMS_DDL,
    LIB2_ALBUM_ARTISTS_DDL,
    LIB2_TRACKS_DDL,
    LIB2_TRACK_ARTISTS_DDL,
    LIB2_TRACK_FILES_DDL,
    LIB2_MANUAL_SKIPS_DDL,
    LIB2_MIRROR_OUTBOX_DDL,
)


# Columns added after the initial schema shipped — applied to existing installs via
# a PRAGMA-probe ALTER (SQLite has no ADD COLUMN IF NOT EXISTS). (table, column, ddl).
_ADDED_COLUMNS = (
    ("lib2_tracks", "spotify_id", "ALTER TABLE lib2_tracks ADD COLUMN spotify_id TEXT"),
    ("lib2_albums", "expected_track_count",
     "ALTER TABLE lib2_albums ADD COLUMN expected_track_count INTEGER"),
    ("lib2_albums", "tracklist_json",
     "ALTER TABLE lib2_albums ADD COLUMN tracklist_json TEXT"),
    ("lib2_artists", "quality_profile_id",
     "ALTER TABLE lib2_artists ADD COLUMN quality_profile_id INTEGER NOT NULL DEFAULT 1"),
    ("lib2_albums", "quality_profile_id",
     "ALTER TABLE lib2_albums ADD COLUMN quality_profile_id INTEGER NOT NULL DEFAULT 1"),
    ("lib2_tracks", "quality_profile_id",
     "ALTER TABLE lib2_tracks ADD COLUMN quality_profile_id INTEGER NOT NULL DEFAULT 1"),
    ("lib2_albums", "origin",
     "ALTER TABLE lib2_albums ADD COLUMN origin TEXT NOT NULL DEFAULT 'library'"),
    # NULL = the artist's provider catalog was never expanded; used by the
    # monitor_new_items enforcement to tell first expansion from re-expansion.
    ("lib2_artists", "discography_synced_at",
     "ALTER TABLE lib2_artists ADD COLUMN discography_synced_at TIMESTAMP"),
)


def _migrate_lib2_profiles_to_app_wide(cursor: Any) -> None:
    """One-time converge: retire the parallel ``lib2_quality_profiles`` table.

    Early lib2 builds stored profile assignments against their own table, so
    the ids on lib2 rows never matched the app-wide ``quality_profiles`` rows
    the pipeline resolves. Remap by profile NAME (the seeds were identical:
    1=Balanced, 2=Upgrade until top quality), point unmatched assignments at
    the app-wide default, then drop the old table. Dropping it is what makes
    this idempotent — once gone, this is a no-op.
    """
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='lib2_quality_profiles'")
    if not cursor.fetchone():
        return
    try:
        cursor.execute("SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1")
        row = cursor.fetchone()
        default_id = row[0] if row else 1

        remap = {}
        cursor.execute("SELECT id, name FROM lib2_quality_profiles")
        old_rows = cursor.fetchall()
        for old in old_rows:
            cursor.execute("SELECT id FROM quality_profiles WHERE name=?", (old[1],))
            match = cursor.fetchone()
            remap[old[0]] = match[0] if match else default_id

        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            cursor.execute(f"PRAGMA table_info({table})")
            if "quality_profile_id" not in {r[1] for r in cursor.fetchall()}:
                continue
            for old_id, new_id in remap.items():
                if old_id != new_id:
                    cursor.execute(
                        f"UPDATE {table} SET quality_profile_id=? WHERE quality_profile_id=?",
                        (new_id, old_id))
            # Anything left pointing at a nonexistent profile → default.
            cursor.execute(
                f"UPDATE {table} SET quality_profile_id=? WHERE quality_profile_id NOT IN "
                f"(SELECT id FROM quality_profiles)", (default_id,))

        cursor.execute("DROP TABLE lib2_quality_profiles")
        logger.info("Migrated %d lib2 quality profiles onto the app-wide table", len(old_rows))
    except Exception as e:  # noqa: BLE001
        logger.error("lib2 quality-profile migration failed (will retry next start): %s", e)


def ensure_library_v2_schema(connection: Any) -> None:
    """Create the Library v2 tables + indexes if missing.

    Idempotent. Safe to call on every app startup. The caller is responsible for
    committing the connection (we leave that to the caller so this composes with
    the other schema-init steps in one transaction).

    Library v2 reads/writes the app-wide ``quality_profiles`` table, so its
    schema is ensured here too (idempotent; normally already done by
    ``MusicDatabase._initialize_database`` — this covers standalone use such
    as the sqlite-only test harness).
    """
    cursor = connection.cursor()
    try:
        from core.quality.schema import ensure_quality_profiles_schema
        ensure_quality_profiles_schema(connection)
    except Exception as e:  # noqa: BLE001
        logger.debug("quality_profiles ensure skipped: %s", e)
    for ddl in _ALL_DDL:
        cursor.execute(ddl)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)
    # Additive column migrations for installs created before a column existed.
    for table, column, alter_sql in _ADDED_COLUMNS:
        cursor.execute(f"PRAGMA table_info({table})")
        if column not in {r[1] for r in cursor.fetchall()}:
            try:
                cursor.execute(alter_sql)
            except Exception as e:  # noqa: BLE001
                logger.debug("column migration %s.%s: %s", table, column, e)
    _migrate_lib2_profiles_to_app_wide(cursor)
    # The read API falls back to download provenance (track_downloads) for
    # files the importer knew no quality data for — index the lookup column so
    # album views don't table-scan a large history per track. Guarded: the
    # table belongs to the legacy schema and may not exist in test harnesses.
    try:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_downloads'")
        if cursor.fetchone():
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_track_downloads_file_path "
                "ON track_downloads(file_path)")
    except Exception as e:  # noqa: BLE001
        logger.debug("track_downloads index skipped: %s", e)
    logger.debug("Library v2 schema ensured")


__all__ = [
    "ensure_library_v2_schema",
    "LIB2_ARTISTS_DDL",
    "LIB2_ALBUMS_DDL",
    "LIB2_ALBUM_ARTISTS_DDL",
    "LIB2_TRACKS_DDL",
    "LIB2_TRACK_ARTISTS_DDL",
    "LIB2_TRACK_FILES_DDL",
]
