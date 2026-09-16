"""Where a Because You Listen To generation lives.

the old layout was three ordinal rows plus three GLOBAL metadata labels:
``because_you_listen_to_0..2`` held the tracks (profile scoped) and
``bylt_artist_0..2`` held the headings (not scoped at all). a run that filled
two slots left the third one standing, which is how a september generation
ended up sitting next to an august shelf with one track in it, under a heading
that had already been used above it.

one generation is now one value under one key, per profile. it is written in a
single store call, so a reader sees either the whole old generation or the
whole new one. failure is recorded separately and never overwrites a good
generation with a convincing-looking empty one.

the legacy rows are still READ, once, when a profile has no generation yet, and
they are marked as legacy when they are. retiring them only ever touches the
profile's own rows - the global heading keys are left alone, because deleting
them would take another profile's headings with them.
"""

import json
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.bylt_store")

GENERATION_KEY = "because_you_listen_to_generation"
ERROR_KEY = "because_you_listen_to_error"
LEGACY_SLOTS = 3
LEGACY_TRACK_KEY = "because_you_listen_to_{}"
LEGACY_LABEL_KEY = "bylt_artist_{}"


def _read_record(database, key: str, profile_id: int) -> Optional[dict]:
    try:
        rows = database.get_curated_playlist(key, profile_id=profile_id)
    except Exception as e:  # noqa: BLE001 - a read failure is not a generation
        logger.debug("bylt store read failed for %s: %s", key, e)
        return None
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except ValueError:
            return None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def save_generation(database, generation: Dict[str, Any], profile_id: int = 1) -> bool:
    """replace the visible generation. one call, one row, all or nothing."""
    try:
        return bool(database.save_curated_playlist(
            GENERATION_KEY, [generation], profile_id=profile_id))
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to store BYLT generation: %s", e)
        return False


def read_generation(database, profile_id: int = 1) -> Optional[dict]:
    return _read_record(database, GENERATION_KEY, profile_id)


def save_failure(database, profile_id: int, message: str,
                 attempted_at: str, generation_id: Optional[str] = None) -> bool:
    """record that a run failed WITHOUT touching the last good generation.

    an empty success and a failed run look identical to a reader that only
    counts sections, so the difference is written down.
    """
    try:
        return bool(database.save_curated_playlist(ERROR_KEY, [{
            "message": str(message)[:500],
            "attempted_at": attempted_at,
            "generation_id": generation_id,
        }], profile_id=profile_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to store BYLT failure marker: %s", e)
        return False


def read_failure(database, profile_id: int = 1) -> Optional[dict]:
    return _read_record(database, ERROR_KEY, profile_id)


def clear_failure(database, profile_id: int = 1) -> bool:
    try:
        return bool(database.save_curated_playlist(ERROR_KEY, [], profile_id=profile_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to clear BYLT failure marker: %s", e)
        return False


def read_legacy_slots(database, profile_id: int = 1) -> List[dict]:
    """the pre-generation ordinal rows, with their provenance attached.

    read only when a profile has no generation at all. the heading comes from
    a global metadata key, so it is reported as such: it is not proof that the
    heading belonged to THIS profile's tracks.
    """
    out: List[dict] = []
    for i in range(LEGACY_SLOTS):
        try:
            name = database.get_metadata(LEGACY_LABEL_KEY.format(i))
            ids = database.get_curated_playlist(
                LEGACY_TRACK_KEY.format(i), profile_id=profile_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("legacy BYLT slot %s unreadable: %s", i, e)
            continue
        if not name or not ids:
            continue
        out.append({
            "slot": i,
            "seed_key": f"legacy:{i}",
            "seed_name": name,
            "track_ids": [str(t) for t in ids if t],
            "legacy": True,
            "heading_scope": "global",
        })
    return out


def retire_legacy_slots(database, profile_id: int = 1) -> int:
    """empty this profile's ordinal rows once a real generation exists.

    profile-scoped writes only. the global ``bylt_artist_*`` keys stay where
    they are: they are shared, and clearing them would blank another profile's
    headings. the read path stops consulting them the moment a generation
    exists, so leaving them costs nothing.
    """
    cleared = 0
    for i in range(LEGACY_SLOTS):
        try:
            if database.get_curated_playlist(LEGACY_TRACK_KEY.format(i),
                                             profile_id=profile_id):
                database.save_curated_playlist(
                    LEGACY_TRACK_KEY.format(i), [], profile_id=profile_id)
                cleared += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("could not retire legacy BYLT slot %s: %s", i, e)
    return cleared


__all__ = [
    "ERROR_KEY",
    "GENERATION_KEY",
    "LEGACY_SLOTS",
    "clear_failure",
    "read_failure",
    "read_generation",
    "read_legacy_slots",
    "retire_legacy_slots",
    "save_failure",
    "save_generation",
]
