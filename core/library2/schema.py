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

from typing import Any, Dict

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
    style TEXT,
    mood TEXT,
    label TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    banner_url TEXT,
    enrichment TEXT NOT NULL DEFAULT '{}',            -- provider-keyed extra bio/stats (lastfm/genius/discogs; see importer._artist_enrichment_payload)
    monitored INTEGER NOT NULL DEFAULT 0,
    monitor_new_items TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'none' | 'new'
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE RESTRICT,
    quality_profile_explicit INTEGER NOT NULL DEFAULT 0,
    canonical_artist_id INTEGER REFERENCES lib2_artists(id) ON DELETE SET NULL, -- self-ref; NULL = canonical/standalone. Set = alias of that row (§40 registry: same real artist under a different, unlinked provider identity — see core/library2/artist_aliases.py)
    legacy_artist_id INTEGER,                         -- source row in legacy `artists`
    legacy_import_run_id TEXT,                        -- last complete legacy snapshot that saw it
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
    explicit INTEGER,
    label TEXT,
    upc TEXT,                                          -- barcode; provider-neutral identifier like isrc on tracks
    track_count INTEGER,
    expected_track_count INTEGER,                      -- true total from metadata (for have/missing)
    tracklist_json TEXT,                               -- cached canonical tracklist (missing-track titles)
    tracklist_status TEXT NOT NULL DEFAULT 'idle',     -- idle | pending | failed | ready
    tracklist_attempts INTEGER NOT NULL DEFAULT 0,
    tracklist_error TEXT,
    tracklist_retry_at TIMESTAMP,
    origin TEXT NOT NULL DEFAULT 'library',            -- 'library' (has/had files) | 'discography' (provider-only)
    stable_id TEXT,                                    -- provider-less identity (audit P1-12); minted once, survives reset+reimport
    monitored INTEGER NOT NULL DEFAULT 1,
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE RESTRICT,
    quality_profile_explicit INTEGER NOT NULL DEFAULT 0,
    legacy_album_id INTEGER,                           -- source row in legacy `albums`
    legacy_import_run_id TEXT,                         -- last complete legacy snapshot that saw it
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
    external_ids TEXT NOT NULL DEFAULT '{}',           -- long-tail provider ids (deezer/tidal/qobuz/itunes/...); isrc/mbid/spotify keep their own columns above
    bpm REAL,
    explicit INTEGER,
    genius_lyrics TEXT,
    copyright TEXT,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played TIMESTAMP,
    stable_id TEXT,                                   -- provider-less identity (audit P1-12); minted once, survives reset+reimport
    monitored INTEGER NOT NULL DEFAULT 1,
    quality_profile_id INTEGER REFERENCES quality_profiles(id) ON DELETE RESTRICT,
    quality_profile_explicit INTEGER NOT NULL DEFAULT 0,
    canonical_track_id INTEGER,                       -- self-ref; NULL = canonical
    legacy_track_id INTEGER,                          -- source row in legacy `tracks`
    legacy_import_run_id TEXT,                        -- last complete legacy snapshot that saw it
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
    pipeline_result_json TEXT NOT NULL DEFAULT '{}',  -- deep-dive A7/C4: compact import-pipeline
                                                       -- detail (acoustid_message, quality_fallback)
                                                       -- not covered by a dedicated column
    tags_json TEXT NOT NULL DEFAULT '{}',             -- present tags snapshot
    missing_tags_json TEXT NOT NULL DEFAULT '[]',     -- list of missing tag keys
    metadata_gaps_json TEXT NOT NULL DEFAULT '[]',    -- list of gap descriptors
    content_hash TEXT,                                -- for dedup / single-vs-album
    is_primary INTEGER NOT NULL DEFAULT 0,            -- exactly one per track (ADR-03)
    file_state TEXT NOT NULL DEFAULT 'active',        -- 'active'|'missing_suspected'|'missing_confirmed'|'quarantined'|'deleted'
    missing_since TIMESTAMP,
    missing_scan_count INTEGER NOT NULL DEFAULT 0,
    legacy_track_id INTEGER,                          -- non-NULL only for legacy-import-owned files
    legacy_import_run_id TEXT,                        -- last complete legacy snapshot that saw it
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

# --- Monitor rules (audit P1-13/P1-14) ---------------------------------------
# WHY an entity is (un)monitored, per user profile. The ``monitored`` columns
# on lib2 rows stay the effective projection; this table records the intent so
# cascades can preserve explicit per-track choices and imports are
# distinguishable from user decisions. See core/library2/monitor_rules.py.
LIB2_MONITOR_RULES_DDL = """
CREATE TABLE IF NOT EXISTS lib2_monitor_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,            -- 'artist'|'album'|'track'
    entity_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL DEFAULT 1,
    monitored INTEGER NOT NULL,
    provenance TEXT NOT NULL,             -- user_explicit|wishlist_import|cascade|new_release|legacy_import
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, profile_id)
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
    ("lib2_albums", "tracklist_status",
     "ALTER TABLE lib2_albums ADD COLUMN tracklist_status TEXT NOT NULL DEFAULT 'idle'"),
    ("lib2_albums", "tracklist_attempts",
     "ALTER TABLE lib2_albums ADD COLUMN tracklist_attempts INTEGER NOT NULL DEFAULT 0"),
    ("lib2_albums", "tracklist_error",
     "ALTER TABLE lib2_albums ADD COLUMN tracklist_error TEXT"),
    ("lib2_albums", "tracklist_retry_at",
     "ALTER TABLE lib2_albums ADD COLUMN tracklist_retry_at TIMESTAMP"),
    ("lib2_artists", "quality_profile_id",
     "ALTER TABLE lib2_artists ADD COLUMN quality_profile_id INTEGER"),
    ("lib2_albums", "quality_profile_id",
     "ALTER TABLE lib2_albums ADD COLUMN quality_profile_id INTEGER"),
    ("lib2_tracks", "quality_profile_id",
     "ALTER TABLE lib2_tracks ADD COLUMN quality_profile_id INTEGER"),
    # §52.2: distinguish a direct user assignment from a persisted inherited
    # value.  Existing installs get NULL first so the one-time inference below
    # can recover old cascade intent; fresh rows use the DDL's DEFAULT 0.
    ("lib2_artists", "quality_profile_explicit",
     "ALTER TABLE lib2_artists ADD COLUMN quality_profile_explicit INTEGER"),
    ("lib2_albums", "quality_profile_explicit",
     "ALTER TABLE lib2_albums ADD COLUMN quality_profile_explicit INTEGER"),
    ("lib2_tracks", "quality_profile_explicit",
     "ALTER TABLE lib2_tracks ADD COLUMN quality_profile_explicit INTEGER"),
    ("lib2_albums", "origin",
     "ALTER TABLE lib2_albums ADD COLUMN origin TEXT NOT NULL DEFAULT 'library'"),
    # NULL = the artist's provider catalog was never expanded; used by the
    # monitor_new_items enforcement to tell first expansion from re-expansion.
    ("lib2_artists", "discography_synced_at",
     "ALTER TABLE lib2_artists ADD COLUMN discography_synced_at TIMESTAMP"),
    # Provider-less stable identity (audit P1-12): deterministic hash of the
    # natural identity, minted once and persisted; replaces rowid-based
    # wishlist surrogate ids that broke across reset/reimport.
    ("lib2_albums", "stable_id",
     "ALTER TABLE lib2_albums ADD COLUMN stable_id TEXT"),
    ("lib2_tracks", "stable_id",
     "ALTER TABLE lib2_tracks ADD COLUMN stable_id TEXT"),
    # Multi-file model (audit P1-07 / ADR-03): exactly one primary file per
    # track plus a lifecycle state per file. Backfill + triggers live in
    # core/library2/track_files.py.
    ("lib2_track_files", "is_primary",
     "ALTER TABLE lib2_track_files ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0"),
    ("lib2_track_files", "file_state",
     "ALTER TABLE lib2_track_files ADD COLUMN file_state TEXT NOT NULL DEFAULT 'active'"),
    # Snapshot ownership (audit P1-02): a complete import marks every row it
    # observed. Only rows with explicit legacy ownership are reconciled away;
    # provider/manual rows and secondary files remain outside that boundary.
    ("lib2_artists", "legacy_import_run_id",
     "ALTER TABLE lib2_artists ADD COLUMN legacy_import_run_id TEXT"),
    ("lib2_albums", "legacy_import_run_id",
     "ALTER TABLE lib2_albums ADD COLUMN legacy_import_run_id TEXT"),
    ("lib2_tracks", "legacy_import_run_id",
     "ALTER TABLE lib2_tracks ADD COLUMN legacy_import_run_id TEXT"),
    ("lib2_track_files", "legacy_track_id",
     "ALTER TABLE lib2_track_files ADD COLUMN legacy_track_id INTEGER"),
    ("lib2_track_files", "legacy_import_run_id",
     "ALTER TABLE lib2_track_files ADD COLUMN legacy_import_run_id TEXT"),
    # Missing lifecycle (audit P2-02): consecutive misses only count while
    # the relevant library root is known healthy.
    ("lib2_track_files", "missing_since",
     "ALTER TABLE lib2_track_files ADD COLUMN missing_since TIMESTAMP"),
    ("lib2_track_files", "missing_scan_count",
     "ALTER TABLE lib2_track_files ADD COLUMN missing_scan_count INTEGER NOT NULL DEFAULT 0"),
    # §17.7: importer metadata parity — long-tail provider ids + fields that
    # exist on the legacy row but previously had no lib2 destination column.
    ("lib2_tracks", "external_ids",
     "ALTER TABLE lib2_tracks ADD COLUMN external_ids TEXT NOT NULL DEFAULT '{}'"),
    ("lib2_tracks", "bpm", "ALTER TABLE lib2_tracks ADD COLUMN bpm REAL"),
    ("lib2_tracks", "explicit", "ALTER TABLE lib2_tracks ADD COLUMN explicit INTEGER"),
    ("lib2_albums", "explicit", "ALTER TABLE lib2_albums ADD COLUMN explicit INTEGER"),
    ("lib2_albums", "label", "ALTER TABLE lib2_albums ADD COLUMN label TEXT"),
    ("lib2_albums", "upc", "ALTER TABLE lib2_albums ADD COLUMN upc TEXT"),
    # §17.7 remainder: artist enrichment + track listening/lyrics fields, and
    # per-track quality_profile_id sourced from the legacy row (previously
    # only the run-wide default was ever written).
    ("lib2_artists", "style", "ALTER TABLE lib2_artists ADD COLUMN style TEXT"),
    ("lib2_artists", "mood", "ALTER TABLE lib2_artists ADD COLUMN mood TEXT"),
    ("lib2_artists", "label", "ALTER TABLE lib2_artists ADD COLUMN label TEXT"),
    ("lib2_artists", "aliases",
     "ALTER TABLE lib2_artists ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'"),
    ("lib2_artists", "banner_url", "ALTER TABLE lib2_artists ADD COLUMN banner_url TEXT"),
    ("lib2_artists", "enrichment",
     "ALTER TABLE lib2_artists ADD COLUMN enrichment TEXT NOT NULL DEFAULT '{}'"),
    ("lib2_tracks", "genius_lyrics", "ALTER TABLE lib2_tracks ADD COLUMN genius_lyrics TEXT"),
    ("lib2_tracks", "copyright", "ALTER TABLE lib2_tracks ADD COLUMN copyright TEXT"),
    ("lib2_tracks", "play_count",
     "ALTER TABLE lib2_tracks ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0"),
    ("lib2_tracks", "last_played", "ALTER TABLE lib2_tracks ADD COLUMN last_played TIMESTAMP"),
    # §40: alias registry — soft-link two artist rows that are the same real
    # artist under a different, unlinked provider identity (e.g. a kanji vs.
    # romaji name with distinct Deezer/Spotify catalog entries).
    ("lib2_artists", "canonical_artist_id",
     "ALTER TABLE lib2_artists ADD COLUMN canonical_artist_id INTEGER"),
    # §48: rich-metadata-edit parity — style/mood exist on the legacy albums/
    # tracks tables (and already on lib2_artists) but had no lib2 destination
    # column, so neither the importer nor the metadata-override store could
    # carry or correct them for albums/tracks.
    ("lib2_albums", "style", "ALTER TABLE lib2_albums ADD COLUMN style TEXT"),
    ("lib2_albums", "mood", "ALTER TABLE lib2_albums ADD COLUMN mood TEXT"),
    ("lib2_tracks", "style", "ALTER TABLE lib2_tracks ADD COLUMN style TEXT"),
    ("lib2_tracks", "mood", "ALTER TABLE lib2_tracks ADD COLUMN mood TEXT"),
    # Deep-dive A7/C4: pipeline-result detail (AcoustID message, quality-gate
    # fallback) that the autolink import-callback now persists per file.
    ("lib2_track_files", "pipeline_result_json",
     "ALTER TABLE lib2_track_files ADD COLUMN pipeline_result_json TEXT NOT NULL DEFAULT '{}'"),
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


_QUALITY_PROFILE_TABLES = ("lib2_artists", "lib2_albums", "lib2_tracks")


def _has_quality_profile_fk(cursor: Any, table: str) -> bool:
    return any(
        row[2] == "quality_profiles" and row[3] == "quality_profile_id"
        and row[4] == "id"
        for row in cursor.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    )


def _install_quality_profile_triggers(cursor: Any, table: str) -> None:
    """Enforce a live default and referential integrity on every SQLite build.

    Existing tables are migrated through an additive nullable column because
    SQLite cannot add ``NOT NULL REFERENCES`` to a populated table. These
    triggers make the persisted invariant equivalent: NULL inserts resolve to
    the current default, while invalid explicit values and NULL updates fail.
    """
    trigger_names = {
        "default": f"trg_{table}_quality_profile_default",
        "insert": f"trg_{table}_quality_profile_insert",
        "update": f"trg_{table}_quality_profile_update",
    }
    for name in trigger_names.values():
        cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
    cursor.execute(f"""
        CREATE TRIGGER {trigger_names['insert']}
        BEFORE INSERT ON {table}
        FOR EACH ROW
        WHEN NEW.quality_profile_id IS NOT NULL
         AND NOT EXISTS (
             SELECT 1 FROM quality_profiles WHERE id=NEW.quality_profile_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'invalid Library v2 quality_profile_id');
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER {trigger_names['default']}
        AFTER INSERT ON {table}
        FOR EACH ROW
        WHEN NEW.quality_profile_id IS NULL
        BEGIN
            UPDATE {table}
               SET quality_profile_id=(
                   SELECT id FROM quality_profiles
                    ORDER BY is_default DESC, id LIMIT 1
               )
             WHERE id=NEW.id;
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER {trigger_names['update']}
        BEFORE UPDATE OF quality_profile_id ON {table}
        FOR EACH ROW
        WHEN NEW.quality_profile_id IS NULL
          OR NOT EXISTS (
              SELECT 1 FROM quality_profiles WHERE id=NEW.quality_profile_id
          )
        BEGIN
            SELECT RAISE(ABORT, 'invalid Library v2 quality_profile_id');
        END
    """)


def _migrate_quality_profile_constraints(cursor: Any) -> None:
    """Remove numeric defaults and attach Lib2 rows to app-wide profiles.

    ``ALTER TABLE ... ADD COLUMN`` can add a nullable FK without rebuilding
    the heavily connected Library-v2 graph. Populate that column with either
    the valid old assignment or the live default, drop the old DEFAULT-1
    column, then rename the FK column into place. Each table uses a savepoint
    so a failed SQLite capability check cannot leave a half-migrated schema.
    """
    # Projection triggers reference all three profile columns. Drop them
    # before a possible column rebuild and recreate them below.
    for trigger_name in (
        "trg_quality_profiles_lib2_default_insert",
        "trg_quality_profiles_lib2_default_update",
    ):
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    default_row = cursor.execute(
        "SELECT id FROM quality_profiles ORDER BY is_default DESC, id LIMIT 1"
    ).fetchone()
    if default_row is None:
        raise RuntimeError("Library v2 requires at least one quality profile")
    default_id = int(default_row[0])

    for table in _QUALITY_PROFILE_TABLES:
        info = {
            row[1]: row for row in cursor.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        if "quality_profile_id" not in info:
            continue
        needs_fk_migration = (
            info["quality_profile_id"][4] is not None
            or not _has_quality_profile_fk(cursor, table)
        )
        savepoint = f"migrate_{table}_quality_profile"
        cursor.execute(f"SAVEPOINT {savepoint}")
        try:
            for suffix in ("default", "insert", "update"):
                cursor.execute(
                    f"DROP TRIGGER IF EXISTS trg_{table}_quality_profile_{suffix}")
            if needs_fk_migration:
                if "quality_profile_id_v2" in info:
                    cursor.execute(
                        f"ALTER TABLE {table} DROP COLUMN quality_profile_id_v2")
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN quality_profile_id_v2 "
                    "INTEGER REFERENCES quality_profiles(id) ON DELETE RESTRICT")
                cursor.execute(f"""
                    UPDATE {table}
                       SET quality_profile_id_v2=CASE
                           WHEN quality_profile_id IN (SELECT id FROM quality_profiles)
                           THEN quality_profile_id ELSE ? END
                """, (default_id,))
                cursor.execute(
                    f"ALTER TABLE {table} DROP COLUMN quality_profile_id")
                cursor.execute(
                    f"ALTER TABLE {table} RENAME COLUMN quality_profile_id_v2 "
                    "TO quality_profile_id")
            else:
                cursor.execute(f"""
                    UPDATE {table}
                       SET quality_profile_id=?
                     WHERE quality_profile_id IS NULL
                        OR quality_profile_id NOT IN (
                            SELECT id FROM quality_profiles
                        )
                """, (default_id,))
            _install_quality_profile_triggers(cursor, table)
            cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

    cursor.execute("DROP TRIGGER IF EXISTS trg_quality_profiles_lib2_restrict")
    cursor.execute("""
        CREATE TRIGGER trg_quality_profiles_lib2_restrict
        BEFORE DELETE ON quality_profiles
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM lib2_artists WHERE quality_profile_id=OLD.id)
          OR EXISTS (SELECT 1 FROM lib2_albums WHERE quality_profile_id=OLD.id)
          OR EXISTS (SELECT 1 FROM lib2_tracks WHERE quality_profile_id=OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'quality profile is referenced by Library v2');
        END
    """)
    # Keep the persisted effective compatibility projection aligned when the
    # app-wide default changes.  Explicit Library-v2 choices remain pinned;
    # inherited albums/tracks follow their (possibly explicit) parent.
    for trigger_name in (
        "trg_quality_profiles_lib2_default_insert",
        "trg_quality_profiles_lib2_default_update",
    ):
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    propagation_sql = """
        UPDATE lib2_artists
           SET quality_profile_id=NEW.id
         WHERE COALESCE(quality_profile_explicit, 0)=0;
        UPDATE lib2_albums
           SET quality_profile_id=(
               SELECT a.quality_profile_id FROM lib2_artists a
                WHERE a.id=lib2_albums.primary_artist_id)
         WHERE COALESCE(quality_profile_explicit, 0)=0;
        UPDATE lib2_tracks
           SET quality_profile_id=(
               SELECT al.quality_profile_id FROM lib2_albums al
                WHERE al.id=lib2_tracks.album_id)
         WHERE COALESCE(quality_profile_explicit, 0)=0;
    """
    cursor.execute(f"""
        CREATE TRIGGER trg_quality_profiles_lib2_default_insert
        AFTER INSERT ON quality_profiles
        FOR EACH ROW WHEN NEW.is_default=1
        BEGIN
            {propagation_sql}
        END
    """)
    cursor.execute(f"""
        CREATE TRIGGER trg_quality_profiles_lib2_default_update
        AFTER UPDATE OF is_default ON quality_profiles
        FOR EACH ROW WHEN NEW.is_default=1
        BEGIN
            {propagation_sql}
        END
    """)


def _backfill_quality_profile_provenance(cursor: Any) -> None:
    """Infer explicit-vs-inherited provenance for pre-§52.2 rows once.

    The additive migration deliberately creates nullable columns on old
    installs.  NULL therefore means "not classified yet" while fresh rows
    are born with ``DEFAULT 0``.  A child whose value equals its parent's
    persisted effective value is the old cascade shape; a differing value is
    the strongest evidence of an explicit override.  At the artist root a
    non-default value is treated as explicit.  Same-value explicit overrides
    cannot be reconstructed from the old schema, but retaining the cascade is
    the least surprising and least destructive migration.
    """
    default_row = cursor.execute(
        "SELECT id FROM quality_profiles ORDER BY is_default DESC, id LIMIT 1"
    ).fetchone()
    default_id = int(default_row[0]) if default_row else 1
    cursor.execute(
        """UPDATE lib2_artists
              SET quality_profile_explicit=CASE
                    WHEN quality_profile_id<>? THEN 1 ELSE 0 END
            WHERE quality_profile_explicit IS NULL""",
        (default_id,),
    )
    cursor.execute(
        """UPDATE lib2_albums
              SET quality_profile_explicit=CASE
                    WHEN quality_profile_id<>(
                        SELECT a.quality_profile_id FROM lib2_artists a
                         WHERE a.id=lib2_albums.primary_artist_id
                    ) THEN 1 ELSE 0 END
            WHERE quality_profile_explicit IS NULL"""
    )
    cursor.execute(
        """UPDATE lib2_tracks
              SET quality_profile_explicit=CASE
                    WHEN quality_profile_id<>(
                        SELECT al.quality_profile_id FROM lib2_albums al
                         WHERE al.id=lib2_tracks.album_id
                    ) THEN 1 ELSE 0 END
            WHERE quality_profile_explicit IS NULL"""
    )


def _migrate_artist_monitored_default(cursor: Any) -> None:
    """Change old installs' artist default from monitored to unmonitored.

    SQLite cannot alter a column default in place. A temporary replacement
    column preserves every existing effective value while making omitted future
    inserts fail safe. Application writers also pass ``monitored`` explicitly;
    this migration closes the remaining raw-SQL/default path.
    """
    info = {
        row[1]: row for row in cursor.execute(
            "PRAGMA table_info(lib2_artists)"
        ).fetchall()
    }
    monitored = info.get("monitored")
    if monitored is None:
        return
    normalized_default = str(monitored[4] or "").strip("()'\" ")
    if normalized_default == "0":
        return

    savepoint = "migrate_lib2_artist_monitored_default"
    cursor.execute(f"SAVEPOINT {savepoint}")
    try:
        if "monitored_v2" in info:
            cursor.execute("ALTER TABLE lib2_artists DROP COLUMN monitored_v2")
        cursor.execute(
            "ALTER TABLE lib2_artists ADD COLUMN monitored_v2 "
            "INTEGER NOT NULL DEFAULT 0"
        )
        cursor.execute(
            "UPDATE lib2_artists SET monitored_v2=CASE "
            "WHEN monitored<>0 THEN 1 ELSE 0 END"
        )
        cursor.execute("ALTER TABLE lib2_artists DROP COLUMN monitored")
        cursor.execute(
            "ALTER TABLE lib2_artists RENAME COLUMN monitored_v2 TO monitored"
        )
        cursor.execute(f"RELEASE {savepoint}")
        logger.info("Migrated Library-v2 artist monitored default from 1 to 0")
    except Exception:
        cursor.execute(f"ROLLBACK TO {savepoint}")
        cursor.execute(f"RELEASE {savepoint}")
        raise


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
    # PRAGMA table_info is read ONCE per distinct table and cached — the
    # previous per-column query re-read the same table's schema ~30 times a
    # startup (many columns per table in _ADDED_COLUMNS) for ~4 distinct
    # tables' worth of information.
    _existing_columns: Dict[str, set] = {}
    for table, column, alter_sql in _ADDED_COLUMNS:
        columns = _existing_columns.get(table)
        if columns is None:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = _existing_columns[table] = {r[1] for r in cursor.fetchall()}
        if column not in columns:
            try:
                cursor.execute(alter_sql)
                # Keep the cache truthful so a later column on the same table
                # sees this one as present (matches the old per-column re-read).
                columns.add(column)
            except Exception as e:  # noqa: BLE001
                logger.debug("column migration %s.%s: %s", table, column, e)
    _migrate_lib2_profiles_to_app_wide(cursor)
    _migrate_quality_profile_constraints(cursor)
    _backfill_quality_profile_provenance(cursor)
    _migrate_artist_monitored_default(cursor)
    # §40 alias registry index — runs AFTER the additive column migration
    # above so it also works on installs that predate canonical_artist_id.
    try:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_lib2_artists_canonical "
            "ON lib2_artists(canonical_artist_id)"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("idx_lib2_artists_canonical create skipped: %s", e)
    # Provider-less stable ids (audit P1-12). Index + backfill run AFTER the
    # additive column migration above so they also work on installs that
    # predate the stable_id columns.
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lib2_albums_stable "
                       "ON lib2_albums(stable_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lib2_tracks_stable "
                       "ON lib2_tracks(stable_id)")
        from core.library2.stable_ids import backfill_stable_ids
        backfill_stable_ids(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("stable_id backfill failed (will retry next start): %s", e)
    # Multi-file primary model (audit P1-07 / ADR-03): elect a primary where
    # missing, repair accidental extras, and keep the invariant via triggers
    # so every write path participates without changes.
    try:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_lib2_track_files_primary "
            "ON lib2_track_files(track_id, is_primary)")
        from core.library2.track_files import backfill_primary_flags, install_primary_triggers
        changed = backfill_primary_flags(cursor)
        if changed:
            logger.info("Primary-file backfill adjusted %d file rows", changed)
        install_primary_triggers(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("primary-file migration failed (will retry next start): %s", e)
    # Monitor rules with provenance (audit P1-13/P1-14). Seeding runs only on
    # the migration that CREATES the table: pre-existing flags get a truthful
    # 'legacy_import' provenance exactly once; afterwards rules exist only
    # where an action recorded intent.
    try:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_monitor_rules'")
        rules_table_is_fresh = cursor.fetchone() is None
        cursor.execute(LIB2_MONITOR_RULES_DDL)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_lib2_monitor_rules_entity "
            "ON lib2_monitor_rules(entity_type, entity_id)")
        from core.library2.monitor_rules import prune_orphaned_rules, seed_legacy_rules
        if rules_table_is_fresh:
            seeded = seed_legacy_rules(cursor)
            if seeded:
                logger.info("Seeded %d legacy_import monitor rules", seeded)
        prune_orphaned_rules(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("monitor-rules migration failed (will retry next start): %s", e)
    # Materialized wanted projection (audit §11.2 / ADR-02 Stufe 2): the
    # effective per-track wanted state computed from the monitor rules, with
    # the deciding rule level recorded. Rebuilds itself when fresh or when
    # the priority version changed.
    try:
        from core.library2.wanted import ensure_wanted_projection
        ensure_wanted_projection(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("wanted-projection migration failed (will retry next start): %s", e)
    # Release editions + recordings (audit P1-04 / ADR-04, §14.2 Schritt 3):
    # additive shadow model — one default edition per album, one recording +
    # release track per track; recordings merge on hard IDs only.
    try:
        from core.library2.editions import backfill_editions, ensure_editions_schema
        ensure_editions_schema(cursor)
        backfill_editions(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("edition/recording migration failed (will retry next start): %s", e)
    # Typed provider provenance (audit ADR-06): normalized payload snapshots
    # carry completeness, parser version and a stable hash. Refresh paths use
    # this contract to distinguish a complete catalog from partial pagination.
    try:
        from core.library2.provider_snapshots import ensure_provider_snapshot_schema
        ensure_provider_snapshot_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("provider-snapshot migration failed (will retry next start): %s", e)
    # External/old-ID history (Roadmap 4): DB triggers cover every current
    # import/provider/edition writer and preserve identifier changes after an
    # entity is deleted. This is an audit/read source, never a second resolver.
    try:
        from core.library2.identity_history import ensure_external_id_history_schema
        backfilled = ensure_external_id_history_schema(cursor)
        if backfilled:
            logger.info("Backfilled %d Library-v2 external identifier events", backfilled)
    except Exception as e:  # noqa: BLE001
        logger.error("external-id-history migration failed (will retry next start): %s", e)
    # Merge/move/link history (Roadmap 4): relationship triggers capture the
    # existing Manage-Tracks, file move and ADR-04 shadow mutation paths.
    try:
        from core.library2.entity_history import ensure_entity_history_schema
        backfilled = ensure_entity_history_schema(cursor)
        if backfilled:
            logger.info("Backfilled %d Library-v2 entity relationship events", backfilled)
    except Exception as e:  # noqa: BLE001
        logger.error("entity-history migration failed (will retry next start): %s", e)
    # ADR-06 field-level user overrides stay separate from provider/import
    # columns; central read projections overlay them without blocking refresh.
    try:
        from core.library2.metadata_overrides import ensure_metadata_overrides_schema
        ensure_metadata_overrides_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("metadata-overrides migration failed (will retry next start): %s", e)
    # ADR-05 physical deletion is a separate, journaled command. The journal
    # is durable before any filesystem mutation and retains crash evidence.
    try:
        from core.library2.file_delete import ensure_file_delete_schema
        ensure_file_delete_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("file-delete journal migration failed (will retry next start): %s", e)
    # Transitional repair-worker bridge: append-only, entity-addressable
    # events for tag/path/artwork/verification mutations performed by the
    # shared maintenance tools outside Library-v2-native endpoints.
    try:
        from core.library2.maintenance_sync import ensure_maintenance_event_schema
        ensure_maintenance_event_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("maintenance-event schema failed (will retry next start): %s", e)
    # B5: persisted table/column/match-provider display preferences.
    try:
        from core.library2.ui_preferences import ensure_ui_preferences_schema
        ensure_ui_preferences_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("ui-preferences migration failed (will retry next start): %s", e)
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
    # Automatic initial-import bootstrap (§78 / tool-integration-audit §7.7):
    # persisted status so an existing installation's first import can run on
    # its own at server start, survive a restart, and be retried on failure.
    try:
        from core.library2.bootstrap import ensure_bootstrap_schema
        ensure_bootstrap_schema(cursor)
    except Exception as e:  # noqa: BLE001
        logger.error("bootstrap-state migration failed (will retry next start): %s", e)
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
