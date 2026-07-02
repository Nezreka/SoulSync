"""One-time migration: materialize the user's pre-existing global quality
settings into the ``quality_profiles`` default row, and backfill existing
``wishlist_tracks`` rows with a pointer to that profile, so every wishlist
item is self-sufficient for the download/import pipeline instead of the
pipeline consulting a global setting.

Why this exists
----------------
Before this migration, ONE global singleton (``preferences.quality_profile``)
plus several separate global toggles (``acoustid.require_verified``,
``lossy_copy.downsample_hires``, ...) governed every download/import in the
app. Quality profiles are the single, app-wide, named, per-item-assignable
unit of configuration instead (see ``core/quality/schema.py``'s
``quality_profiles`` table). Users must not have to reconfigure anything on
upgrade: this migration reads whatever they already had configured and turns
it into the new ``is_default=1`` profile row, then stamps every existing
wishlist row with that profile's id so nothing changes behaviorally until the
user deliberately assigns a different profile.

Runs once, gated by a ``metadata`` table flag (the same gating pattern as
``MusicDatabase._normalize_genres_to_json``). Safe no-op on any error — never
blocks app startup.
"""

from __future__ import annotations

import json
from typing import Any

from utils.logging_config import get_logger

logger = get_logger("quality.migrate_to_profiles")

_MIGRATION_FLAG_KEY = "quality_profiles_migrated_v1"


def _profile_row_fields(profile: dict) -> dict:
    """Convert a legacy v3 quality-profile dict into `quality_profiles` row fields."""
    ranked = profile.get("ranked_targets") or []
    search_mode = profile.get("search_mode", "priority")
    upgrade_policy = profile.get("upgrade_policy", "acceptable")
    if upgrade_policy not in ("acceptable", "until_cutoff", "until_top"):
        upgrade_policy = "acceptable"
    try:
        upgrade_cutoff_index = max(0, int(profile.get("upgrade_cutoff_index") or 0))
    except (TypeError, ValueError):
        upgrade_cutoff_index = 0
    return {
        "ranked_targets": json.dumps(ranked),
        "fallback_enabled": 1 if profile.get("fallback_enabled", True) else 0,
        "search_mode": search_mode if search_mode in ("priority", "best_quality") else "priority",
        "rank_candidates_by_quality": 1 if profile.get("rank_candidates_by_quality") else 0,
        "upgrade_policy": upgrade_policy,
        "upgrade_cutoff_index": upgrade_cutoff_index,
    }


def _bool(config_manager, key: str, default: bool = False) -> int:
    try:
        return 1 if config_manager.get(key, default) else 0
    except Exception:  # noqa: BLE001
        return 1 if default else 0


def _str(config_manager, key: str, default: str) -> str:
    try:
        return str(config_manager.get(key, default) or default)
    except Exception:  # noqa: BLE001
        return default


def _legacy_import_quality_filter_disabled(config_manager) -> bool:
    """Whether the old global "quality filter on import" switch was
    explicitly disabled.

    That switch no longer exists as a standalone setting — "accept anything"
    is represented by a profile's fallback behaviour instead. Used by
    ``_materialize_relaxed_auto_import_profile`` to preserve upgrades from
    installs where the user had deliberately turned the old gate off (see
    that function for why this becomes an Auto-Import-only profile rather
    than loosening the default profile everything else also uses).
    """
    try:
        value = config_manager.get("import.quality_filter_enabled", True)
    except Exception:  # noqa: BLE001
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("0", "false", "no", "off")
    return value is False


def _resolve_settings_bundle(config_manager) -> dict:
    """Read every Settings -> Quality toggle the profile now captures.

    ``acoustid_required`` maps directly to ``acoustid.require_verified`` — a
    profile expresses "how strict should verification be", independent of
    whether AcoustID is enabled/configured at all (a true global capability,
    like a Connections credential, not a per-profile preference). If AcoustID
    isn't enabled, the pipeline's own availability check already skips
    verification regardless of this value.
    """
    return {
        "acoustid_required": _bool(config_manager, "acoustid.require_verified"),
        "downsample_enabled": _bool(config_manager, "lossy_copy.downsample_hires"),
        "deep_audio_verify": _bool(config_manager, "post_processing.audio_completeness_check"),
        "replace_lower_quality": _bool(config_manager, "import.replace_lower_quality"),
        "lossy_copy_enabled": _bool(config_manager, "lossy_copy.enabled"),
        "lossy_copy_codec": _str(config_manager, "lossy_copy.codec", "mp3"),
        "lossy_copy_bitrate": _str(config_manager, "lossy_copy.bitrate", "320"),
        "lossy_copy_delete_original": _bool(config_manager, "lossy_copy.delete_original"),
    }


def _default_profile_id(cursor) -> int:
    """Return the profile row the migration should materialize into.

    Fresh installs get the seeded row at id=1, but intermediate builds may have
    let users delete or rename that row before this migration flag existed. In
    that case the user's current default row must win, and if no default marker
    survived we promote the lowest remaining row instead of backfilling
    wishlist items to a dangling hard-coded id.
    """
    row = cursor.execute(
        "SELECT id FROM quality_profiles WHERE is_default = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        row = cursor.execute(
            "SELECT id FROM quality_profiles ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        cursor.execute(
            """
            INSERT INTO quality_profiles
                (name, description, ranked_targets, fallback_enabled, is_default)
            VALUES ('Balanced', 'Migrated from your previous global Quality settings',
                    '[]', 1, 1)
            """
        )
        return int(cursor.lastrowid)
    profile_id = int(row["id"] if hasattr(row, "keys") else row[0])
    cursor.execute("UPDATE quality_profiles SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (profile_id,))
    return profile_id


def _materialize_relaxed_auto_import_profile(cursor, config_manager, fields: dict, bundle: dict) -> None:
    """When the legacy install had the old global "quality filter on import"
    switch turned off entirely, that most plausibly reflects Auto-Import
    specifically: it scans an already-acquired Staging folder with no
    alternative version to search for, so rejecting/quarantining those files
    on import was rarely the intent — unlike a fresh Wishlist download, where
    there IS a better version to look for. Give Auto-Import its own lenient
    clone of the migrated settings (same ranked targets, fallback forced on)
    instead of loosening the one profile every normal download/Wishlist item
    also uses, which would silently start accepting low-quality files there
    too. Only assigns it when Auto-Import doesn't already have an explicit
    override (never clobber a real user choice).
    """
    clone_fields = dict(fields)
    clone_fields["fallback_enabled"] = 1
    cursor.execute(
        """
        INSERT INTO quality_profiles
            (name, description, ranked_targets, fallback_enabled, search_mode,
             rank_candidates_by_quality, upgrade_policy, upgrade_cutoff_index,
             acoustid_required, downsample_enabled, deep_audio_verify,
             replace_lower_quality, lossy_copy_enabled, lossy_copy_codec,
             lossy_copy_bitrate, lossy_copy_delete_original, is_default)
        VALUES (:name, :description, :ranked_targets, :fallback_enabled, :search_mode,
                :rank_candidates_by_quality, :upgrade_policy, :upgrade_cutoff_index,
                :acoustid_required, :downsample_enabled, :deep_audio_verify,
                :replace_lower_quality, :lossy_copy_enabled, :lossy_copy_codec,
                :lossy_copy_bitrate, :lossy_copy_delete_original, 0)
        """,
        {
            "name": "Auto-Import (accept anything)",
            "description": (
                "Migrated: your previous install had quality filtering on "
                "import disabled entirely. Assigned to Auto-Import so it "
                "keeps accepting files it always used to; normal downloads "
                "and Wishlist items stay on your real quality settings."
            ),
            **clone_fields, **bundle,
        },
    )
    new_profile_id = cursor.lastrowid
    try:
        if not config_manager.get("auto_import.quality_profile_id"):
            config_manager.set("auto_import.quality_profile_id", new_profile_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("could not assign migrated relaxed profile to Auto-Import: %s", e)


def materialize_default_profile_and_backfill(database, conn) -> bool:
    """Materialize the pre-migration global settings into the default
    ``quality_profiles`` row and backfill existing ``wishlist_tracks`` rows.

    ``database`` is the ``MusicDatabase`` instance (for
    ``_legacy_quality_profile_from_preferences`` + ``config_manager`` access);
    ``conn`` is the already-open connection from ``_initialize_database`` (the
    caller commits — this function does not commit or close it).

    Returns True if the migration ran, False if it was already applied or
    skipped due to an error (fail-open: never blocks startup).
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT value FROM metadata WHERE key = ? LIMIT 1", (_MIGRATION_FLAG_KEY,)
        )
        if cursor.fetchone():
            return False
    except Exception as e:  # noqa: BLE001
        logger.debug("quality-profile migration flag check failed: %s", e)
        return False

    try:
        from config.settings import config_manager

        legacy_profile = database._legacy_quality_profile_from_preferences()
        fields = _profile_row_fields(legacy_profile)
        bundle = _resolve_settings_bundle(config_manager)
        needs_relaxed_auto_import_profile = _legacy_import_quality_filter_disabled(config_manager)
        default_profile_id = _default_profile_id(cursor)

        # Overwrite (not INSERT OR IGNORE) — `_seed_quality_profiles` already
        # inserted factory content if the table was empty; the user's real
        # settings must win over that seed or any intermediate default row.
        # Rename to 'Default' too: the seeded name ("Balanced") describes a
        # factory preset the user never actually chose, which is misleading
        # once the row holds their real carried-over settings. Only rename
        # when the row still has ITS OWN seeded name — an intermediate build
        # may have let the user rename it already, and that choice must win.
        cursor.execute(
            """
            UPDATE quality_profiles
               SET name = CASE WHEN name IN ('Balanced', 'Upgrade until top quality')
                                THEN 'Default' ELSE name END,
                   description = 'Migrated from your previous global Quality settings',
                   ranked_targets = :ranked_targets,
                   fallback_enabled = :fallback_enabled,
                   search_mode = :search_mode,
                   rank_candidates_by_quality = :rank_candidates_by_quality,
                   upgrade_policy = :upgrade_policy,
                   upgrade_cutoff_index = :upgrade_cutoff_index,
                   acoustid_required = :acoustid_required,
                   downsample_enabled = :downsample_enabled,
                   deep_audio_verify = :deep_audio_verify,
                   replace_lower_quality = :replace_lower_quality,
                   lossy_copy_enabled = :lossy_copy_enabled,
                   lossy_copy_codec = :lossy_copy_codec,
                   lossy_copy_bitrate = :lossy_copy_bitrate,
                   lossy_copy_delete_original = :lossy_copy_delete_original,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = :default_profile_id
            """,
            {"default_profile_id": default_profile_id, **fields, **bundle},
        )
        if cursor.rowcount == 0:
            raise RuntimeError(f"default quality profile {default_profile_id} was not found")

        cursor.execute(
            "UPDATE wishlist_tracks SET quality_profile_id = ? WHERE quality_profile_id IS NULL",
            (default_profile_id,),
        )
        backfilled = cursor.rowcount

        # Existing library tracks predate per-item profile assignment entirely
        # (there was nothing to point at before this migration created a
        # profile row) — pin them to the same migrated profile, same as
        # wishlist rows above, so a Quality Check/Upgrade Finder run right
        # after upgrading judges them against the settings the user actually
        # had, not a silent reset to factory defaults. New tracks added after
        # this point are inserted with quality_profile_id=NULL and simply
        # follow whichever profile is default at read time.
        try:
            cursor.execute(
                "UPDATE tracks SET quality_profile_id = ? WHERE quality_profile_id IS NULL",
                (default_profile_id,),
            )
            library_backfilled = cursor.rowcount
        except Exception as e:  # noqa: BLE001 — column may not exist yet on a very old schema
            logger.debug("library track backfill skipped: %s", e)
            library_backfilled = 0

        if needs_relaxed_auto_import_profile:
            try:
                _materialize_relaxed_auto_import_profile(cursor, config_manager, fields, bundle)
            except Exception as e:  # noqa: BLE001
                logger.error("Could not create migrated Auto-Import profile: %s", e)

        cursor.execute(
            "INSERT OR IGNORE INTO metadata (key, value, updated_at) "
            "VALUES (?, 'true', CURRENT_TIMESTAMP)",
            (_MIGRATION_FLAG_KEY,),
        )
        logger.info(
            "Quality-profile migration: materialized default profile, backfilled "
            "%d wishlist row(s), %d library track(s)",
            backfilled, library_backfilled,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Quality-profile migration failed: %s", e)
        return False


__all__ = ["materialize_default_profile_and_backfill"]
