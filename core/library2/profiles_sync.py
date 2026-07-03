"""Sync user-defined quality presets (Settings → Quality) into Library v2 profiles.

SoulSync already lets users craft quality profiles in Settings → Quality: an active
``quality_profile`` plus named ``quality_profile_presets`` (each a profile dict with
``ranked_targets`` etc.). Rather than make users re-define profiles inside the
library, we mirror those presets into ``lib2_quality_profiles`` so anything they
build in Settings becomes assignable per artist/album here.

One-way + idempotent: presets are upserted by name; the two built-in lib2 defaults
(Balanced / Upgrade until top quality) are left untouched. Never raises.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.profiles_sync")

# Don't clobber the built-in lib2 profiles (ids 1/2) by name.
_RESERVED_NAMES = {"balanced", "upgrade until top quality"}


def _profile_row_fields(name: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    ranked = profile.get("ranked_targets") or []
    search_mode = profile.get("search_mode", "priority")
    return {
        "name": name,
        "description": "Defined in Settings → Quality",
        "ranked_targets": json.dumps(ranked),
        "fallback_enabled": 1 if profile.get("fallback_enabled", True) else 0,
        "search_mode": search_mode if search_mode in ("priority", "best_quality") else "priority",
        "rank_candidates_by_quality": 1 if profile.get("rank_candidates_by_quality") else 0,
        # best_quality presets map to "keep upgrading"; others to "acceptable".
        "upgrade_policy": "until_top" if search_mode == "best_quality" else "acceptable",
    }


def sync_settings_presets(database) -> int:
    """Upsert the user's Settings → Quality presets into lib2_quality_profiles.

    Returns the number of presets synced. Safe no-op on any error.
    """
    synced = 0
    try:
        presets: Dict[str, Any] = {}
        try:
            presets = dict(database._load_preset_store() or {})
        except Exception:  # noqa: BLE001
            presets = {}
        # Also surface the active profile if it carries a usable preset name.
        try:
            active = database.get_quality_profile() or {}
            pname = active.get("preset")
            if pname and pname not in presets and active.get("ranked_targets"):
                presets[pname] = active
        except Exception as e:  # noqa: BLE001
            logger.debug("active-profile preset lookup skipped: %s", e)
        if not presets:
            return 0

        conn = database._get_connection()
        try:
            cur = conn.cursor()
            for raw_name, profile in presets.items():
                name = str(raw_name).strip()
                if not name or name.lower() in _RESERVED_NAMES or not isinstance(profile, dict):
                    continue
                f = _profile_row_fields(name, profile)
                # Insert if new (by unique name), then refresh the editable fields so
                # later edits in Settings propagate.
                cur.execute(
                    "INSERT OR IGNORE INTO lib2_quality_profiles "
                    "(name, description, ranked_targets, fallback_enabled, search_mode, "
                    " rank_candidates_by_quality, upgrade_policy, is_default) "
                    "VALUES (:name,:description,:ranked_targets,:fallback_enabled,:search_mode,"
                    " :rank_candidates_by_quality,:upgrade_policy, 0)",
                    f,
                )
                cur.execute(
                    "UPDATE lib2_quality_profiles SET ranked_targets=:ranked_targets, "
                    "fallback_enabled=:fallback_enabled, search_mode=:search_mode, "
                    "rank_candidates_by_quality=:rank_candidates_by_quality, "
                    "description=:description, updated_at=CURRENT_TIMESTAMP WHERE name=:name",
                    f,
                )
                synced += 1
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("settings-preset sync failed: %s", e)
    logger.info("Library v2 quality-profile sync: %d preset(s)", synced)
    return synced


__all__ = ["sync_settings_presets"]
