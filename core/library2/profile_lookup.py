"""Resolve a Library-v2 artist's assigned quality profile by artist name.

Used by acquisition paths that predate Library v2 (the watchlist scanner's
new-release queueing) so a per-artist profile assignment still reaches the
wishlist row — and therefore the download/import pipeline — for releases lib2
itself didn't queue.

Fail-open: returns ``None`` (→ app-wide default profile) when the feature is
off, the artist isn't in lib2, or anything errors. Never raises.
"""

from __future__ import annotations

from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("library2.profile_lookup")


def default_quality_profile_id(conn) -> int:
    """The app-wide default profile's id, for fallbacks.

    Profile ids must never be hardcoded to 1 — the starter profiles are fully
    user-manageable (incl. deleting id 1), so a literal 1 can dangle. Falls
    back to the lowest existing profile id, then 1 (empty table = seed order).
    """
    try:
        row = conn.execute(
            "SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0])
        row = conn.execute("SELECT id FROM quality_profiles ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row[0])
    except Exception as e:  # noqa: BLE001
        logger.debug("default profile lookup failed: %s", e)
    return 1


def lib2_quality_profile_for_artist(database, artist_name: str) -> Optional[int]:
    """The app-wide ``quality_profiles`` id assigned to this artist in
    Library v2, or ``None`` when unavailable."""
    if not artist_name:
        return None
    try:
        from config.settings import config_manager
        if config_manager.get("features.library_v2", False) is not True:
            return None
        from .importer import normalize_name
        key = normalize_name(artist_name)
        conn = database._get_connection()
        try:
            # Fast path: SQL case-insensitive match (avoids a full-table
            # python scan on every watchlist queue decision).
            row = conn.execute(
                "SELECT quality_profile_id FROM lib2_artists "
                "WHERE lower(name) = ? AND quality_profile_id IS NOT NULL LIMIT 1",
                (key,),
            ).fetchone()
            if row:
                return int(row["quality_profile_id"])
            for row in conn.execute(
                "SELECT name, quality_profile_id FROM lib2_artists "
                "WHERE quality_profile_id IS NOT NULL"
            ):
                if normalize_name(row["name"]) == key:
                    return int(row["quality_profile_id"])
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("lib2 profile lookup failed (%s): %s", artist_name, e)
    return None


__all__ = ["lib2_quality_profile_for_artist"]
