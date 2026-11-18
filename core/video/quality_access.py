"""request quality for a profile off-request (#2168): background work
(import lists, the watchlist scanner, collections, automations, seerr) asks
the same resolver the dialog does, with the acting profile's own
permissions.

    from core.video.quality_access import resolve_for_profile
    d = resolve_for_profile(video_db, owner_id, "movie", wanted_id)
    if d.refused: ...            # d.error: "not_allowed" | "quality_not_allowed"
    d.profile_id                 # None = the normal default chain
    d.needs_approval             # file a request instead of a wish

a profile whose permissions can't be read is refused (fails closed).
"""

from __future__ import annotations

from typing import Any, Optional

from core import permissions as P
from utils.logging_config import get_logger

logger = get_logger("video.quality_access")


def _ctx(profile_id, music_db=None):
    from core.permission_store import context_for
    if music_db is None:
        from database.music_database import get_database
        music_db = get_database()
    return context_for(music_db, profile_id)


def context_for_profile(profile_id, music_db=None) -> dict:
    """a can() context for any profile (fails closed), for callers that
    can't import the music side themselves (the video api)."""
    return _ctx(profile_id, music_db)


def effective_for_row(profile) -> dict:
    """the effective permissions for a profile row (raises when they can't
    be built). the tokened calendar feed reads its profile through this."""
    from core.permission_store import effective_for
    from database.music_database import get_database
    perms, _source = effective_for(get_database(), profile)
    return perms


def first_admin_name():
    """the name a member's ask goes to ("Sam gets asked first"), or None."""
    try:
        from database.music_database import get_database
        return (get_database().get_profile(1) or {}).get("name") or None
    except Exception:  # noqa: BLE001 - only words
        return None


def resolve_for_profile(video_db, profile_id, kind: str, wanted: Any = None, *, music_db=None,
                        context: Optional[dict] = None) -> P.QualityDecision:
    """resolve_request_quality for ``profile_id`` (or a ready ``context``)
    with the quality profiles this install has and the kind's default."""
    from core.video.quality_profile import kind_default_id, quality_options
    k = "movie" if str(kind or "").lower() == "movie" else "tv"
    try:
        ctx = context if context is not None else _ctx(profile_id, music_db)
    except Exception:  # noqa: BLE001 - can't read the profile: no rights
        logger.warning("quality: no permissions for profile %s, refusing", profile_id, exc_info=True)
        return P.QualityDecision(None, False, refused=True, error=P.NOT_ALLOWED,
                                 note="permissions unavailable")
    try:
        options = quality_options(video_db)
    except Exception:  # noqa: BLE001 - only Default is known for sure
        options = [{"id": 0, "name": "Default", "media_type": None, "tags": []}]
    try:
        kd = kind_default_id(video_db, "movie" if k == "movie" else "show") or 0
    except Exception:  # noqa: BLE001
        kd = 0
    return P.resolve_request_quality(ctx, k, wanted, options=options, kind_default=kd)


def held_quality(video_db, profile_id, kind: str, wanted: Any = None, *, music_db=None) -> Optional[int]:
    """the quality an automatic add for ``profile_id`` gets: ``wanted`` when
    they may have it, else their default (logged). None = the normal default
    chain (also when they can't request the kind at all: whether the add
    happens at all is the caller's call)."""
    d = resolve_for_profile(video_db, profile_id, kind, wanted, music_db=music_db)
    if not d.refused:
        return d.profile_id
    if d.error == P.QUALITY_NOT_ALLOWED and wanted not in (None, ""):
        logger.info("quality %s isn't one profile %s may have for %s, using their default",
                    wanted, profile_id, kind)
        fallback = resolve_for_profile(video_db, profile_id, kind, None, music_db=music_db)
        return None if fallback.refused else fallback.profile_id
    return None


__all__ = ["resolve_for_profile", "held_quality", "context_for_profile", "effective_for_row",
           "first_admin_name"]
