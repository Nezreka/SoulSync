"""Schema + migration helpers for the app-wide ``quality_profiles`` table.

Quality profiles are the single, named, per-item-assignable unit of
configuration for "what does 'good enough' mean for this item, and what
should the pipeline do about it": the ranked-target ladder, whether to accept
a fallback file that matches none of them, AcoustID strictness, downsample
behaviour, real-audio verification, and lossy-copy settings. (Whether to
trust the staging folder name as the artist is a separate, Auto-Import-only
setting — ``import.folder_artist_override`` — and deliberately NOT part of a
quality profile.) Every ``wishlist_tracks`` row carries a
``quality_profile_id`` pointing at one of
these rows instead of the pipeline consulting a single global setting -- see
``core/quality/selection.py::load_profile_by_id`` for how each pipeline stage
resolves a profile's current settings live (not a frozen snapshot), and
``core/quality/migrate_to_profiles.py`` for the one-time upgrade migration
that materializes a user's pre-existing global settings into the default row.

The schema is created idempotently -- ``ensure_quality_profiles_schema`` runs
``CREATE TABLE IF NOT EXISTS`` at startup, so existing installs upgrade
silently. The caller owns the transaction (we don't commit), so this composes
with the other schema-init steps in ``MusicDatabase._initialize_database``.
"""

from __future__ import annotations

from typing import Any

from utils.logging_config import get_logger

logger = get_logger("database.quality_schema")


# --- Quality profiles --------------------------------------------------------
# ``upgrade_policy='until_cutoff'`` is the Lidarr-like "keep searching/
# upgrading until the selected ranked target is reached" mode. The selected
# target is stored as ``upgrade_cutoff_index``; index 0 means "top quality".
# ``until_top`` is kept as a compatibility alias for rows created by the first
# quality-profile branch.
# ``acoustid_required`` is the STRICTNESS dial (same meaning as the
# ``acoustid.require_verified`` setting it was migrated from): when on, a
# track AcoustID runs on but cannot confirm is quarantined instead of
# imported with the "unverified" badge. It does NOT mean "run AcoustID at
# all" — whether AcoustID is enabled/configured (API key, fpcalc) stays a
# true global capability, not a per-profile preference, and skipping the
# check entirely remains an explicit per-download user action. Default 0
# (lenient) to match ``acoustid.require_verified``'s default.
# There is deliberately no "run the quality check at all" master toggle: an
# empty ``ranked_targets`` list (or ``fallback_enabled=True``) already means
# "accept anything" (see `core/imports/guards.py::check_quality_target`) --
# a separate on/off switch would just be a second way to say the same thing.
# An earlier pass added exactly that toggle (``quality_filter_enabled``) before
# this was noticed; ``_DROPPED_COLUMNS`` below removes it for anyone who ran
# that intermediate version.
QUALITY_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS quality_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    ranked_targets TEXT NOT NULL DEFAULT '[]',
    fallback_enabled INTEGER NOT NULL DEFAULT 1,
    search_mode TEXT NOT NULL DEFAULT 'priority',
    rank_candidates_by_quality INTEGER NOT NULL DEFAULT 0,
    upgrade_policy TEXT NOT NULL DEFAULT 'acceptable', -- 'acceptable'|'until_cutoff'|'until_top'
    upgrade_cutoff_index INTEGER NOT NULL DEFAULT 0,
    acoustid_required INTEGER NOT NULL DEFAULT 0,
    downsample_enabled INTEGER NOT NULL DEFAULT 0,
    deep_audio_verify INTEGER NOT NULL DEFAULT 0,
    replace_lower_quality INTEGER NOT NULL DEFAULT 0,
    lossy_copy_enabled INTEGER NOT NULL DEFAULT 0,
    lossy_copy_codec TEXT NOT NULL DEFAULT 'mp3',
    lossy_copy_bitrate TEXT NOT NULL DEFAULT '320',
    lossy_copy_delete_original INTEGER NOT NULL DEFAULT 0,
    repair_job_id TEXT NOT NULL DEFAULT 'quality_upgrade',
    repair_settings TEXT NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_quality_profiles_default ON quality_profiles(is_default)",
)

# Columns added after the initial schema shipped -- applied to existing installs via
# a PRAGMA-probe ALTER (SQLite has no ADD COLUMN IF NOT EXISTS). (table, column, ddl).
_ADDED_COLUMNS = (
    ("quality_profiles", "upgrade_policy",
     "ALTER TABLE quality_profiles ADD COLUMN upgrade_policy TEXT NOT NULL DEFAULT 'acceptable'"),
    ("quality_profiles", "upgrade_cutoff_index",
     "ALTER TABLE quality_profiles ADD COLUMN upgrade_cutoff_index INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "acoustid_required",
     "ALTER TABLE quality_profiles ADD COLUMN acoustid_required INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "downsample_enabled",
     "ALTER TABLE quality_profiles ADD COLUMN downsample_enabled INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "deep_audio_verify",
     "ALTER TABLE quality_profiles ADD COLUMN deep_audio_verify INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "replace_lower_quality",
     "ALTER TABLE quality_profiles ADD COLUMN replace_lower_quality INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "lossy_copy_enabled",
     "ALTER TABLE quality_profiles ADD COLUMN lossy_copy_enabled INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "lossy_copy_codec",
     "ALTER TABLE quality_profiles ADD COLUMN lossy_copy_codec TEXT NOT NULL DEFAULT 'mp3'"),
    ("quality_profiles", "lossy_copy_bitrate",
     "ALTER TABLE quality_profiles ADD COLUMN lossy_copy_bitrate TEXT NOT NULL DEFAULT '320'"),
    ("quality_profiles", "lossy_copy_delete_original",
     "ALTER TABLE quality_profiles ADD COLUMN lossy_copy_delete_original INTEGER NOT NULL DEFAULT 0"),
    ("quality_profiles", "repair_job_id",
     "ALTER TABLE quality_profiles ADD COLUMN repair_job_id TEXT NOT NULL DEFAULT 'quality_upgrade'"),
    ("quality_profiles", "repair_settings",
     "ALTER TABLE quality_profiles ADD COLUMN repair_settings TEXT NOT NULL DEFAULT '{}'"),
)

# Columns removed after shipping -- dropped via a PRAGMA-probe ALTER, mirroring
# _ADDED_COLUMNS. Requires SQLite >= 3.35 (2021); the app's runtime image ships
# 3.46+. (table, column).
_DROPPED_COLUMNS = (
    ("quality_profiles", "quality_filter_enabled"),
    # folder_artist_override doesn't make sense per-profile (it's a Staging
    # folder-layout quirk Auto-Import deals with, not a quality preference) --
    # moved back to a plain import.folder_artist_override global setting.
    ("quality_profiles", "folder_artist_override"),
)


_DEFAULT_RANKED_TARGETS = """[
  {"label":"FLAC 24-bit/192kHz","format":"flac","bit_depth":24,"min_sample_rate":192000},
  {"label":"FLAC 24-bit/96kHz","format":"flac","bit_depth":24,"min_sample_rate":96000},
  {"label":"FLAC 24-bit/48kHz","format":"flac","bit_depth":24,"min_sample_rate":48000},
  {"label":"FLAC 24-bit/44.1kHz","format":"flac","bit_depth":24,"min_sample_rate":44100},
  {"label":"FLAC 16-bit","format":"flac","bit_depth":16},
  {"label":"MP3 320kbps","format":"mp3","min_bitrate":320}
]"""

_TOP_RANKED_TARGETS = """[
  {"label":"FLAC 24-bit/192kHz","format":"flac","bit_depth":24,"min_sample_rate":192000},
  {"label":"FLAC 24-bit/96kHz","format":"flac","bit_depth":24,"min_sample_rate":96000},
  {"label":"FLAC 24-bit/48kHz","format":"flac","bit_depth":24,"min_sample_rate":48000},
  {"label":"FLAC 24-bit/44.1kHz","format":"flac","bit_depth":24,"min_sample_rate":44100},
  {"label":"FLAC 16-bit","format":"flac","bit_depth":16}
]"""


def _seed_quality_profiles(cursor: Any) -> None:
    """Seed the two starter profiles ONLY on a truly empty table (fresh
    install). Profiles are fully user-manageable, including the two starter
    ones -- a user may rename or delete either. Once the table has ANY rows,
    this must never re-insert a deleted starter profile by its old hardcoded
    id, so the guard is "table is empty", not "these specific ids are
    missing"."""
    count = cursor.execute("SELECT COUNT(*) FROM quality_profiles").fetchone()[0]
    if count:
        return
    cursor.execute(
        """
        INSERT INTO quality_profiles
            (id, name, description, ranked_targets, fallback_enabled, search_mode,
             rank_candidates_by_quality, upgrade_policy, upgrade_cutoff_index,
             repair_job_id, repair_settings, is_default)
        VALUES
            (1, 'Balanced', 'Lossless preferred, high-quality lossy accepted.',
             ?, 1, 'priority', 0, 'acceptable', 0, 'quality_upgrade', '{}', 1),
            (2, 'Upgrade until top quality', 'Keep proposing upgrades until the first ranked target is reached.',
             ?, 1, 'best_quality', 1, 'until_cutoff', 0, 'quality_upgrade',
             '{"require_top_target": true}', 0)
        """,
        (_DEFAULT_RANKED_TARGETS, _TOP_RANKED_TARGETS),
    )


def ensure_quality_profiles_schema(connection: Any) -> None:
    """Create the ``quality_profiles`` table + index if missing, seed the two
    built-in profiles, and apply additive column migrations.

    Idempotent. Safe to call on every app startup. The caller is responsible
    for committing the connection (we leave that to the caller so this
    composes with the other schema-init steps in one transaction).
    """
    cursor = connection.cursor()
    cursor.execute(QUALITY_PROFILES_DDL)
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
    _seed_quality_profiles(cursor)
    # Removed-column cleanup for installs that ran an intermediate version.
    for table, column in _DROPPED_COLUMNS:
        cursor.execute(f"PRAGMA table_info({table})")
        if column in {r[1] for r in cursor.fetchall()}:
            try:
                cursor.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                logger.info("Dropped dormant column %s.%s", table, column)
            except Exception as e:  # noqa: BLE001
                logger.debug("column removal %s.%s: %s", table, column, e)
    logger.debug("Quality-profiles schema ensured")


__all__ = [
    "ensure_quality_profiles_schema",
    "QUALITY_PROFILES_DDL",
]
