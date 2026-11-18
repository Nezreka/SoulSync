"""a member's asks, counted per kind (music, movies, TV).

each kind has its own request limit (music.request_limit,
movies.request_limit, tv.request_limit): "5 movie asks a week" is five
movies, and a show or an album never spends it. an ask counts toward:

  music    albums and tracks (grouped the way core.requests.music groups
           them) and music videos, from the music db's ask log
  movies   movie requests
  tv       show, season and episode requests, and YouTube videos (a
           channel's episode). a profile with no TV at all counts YouTube
           toward movies, so it never lands on a limit they can't see

every place that checks or shows a limit asks here, so "3 of 5 movie asks
left" is the same number on the Requests page, the ask sheet, Seerr, the
background scans and the People list.

a count that can't be read is None, and every caller treats None as a full
limit: an unreadable count never hands out a free ask.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from core import permissions as P
from utils.logging_config import get_logger

logger = get_logger("requests.asks")

KINDS = P.LIMIT_KINDS

# the video request kinds behind each video limit (youtube placed per profile)
_VIDEO_KINDS = {"movies": ("movie",), "tv": ("show", "season", "episode")}


def _music_db(db=None):
    if db is not None:
        return db
    from core.permission_store import profile_db
    return profile_db()


def _video_db(db=None):
    if db is not None:
        return db
    from api.video import get_video_db
    return get_video_db()


def video_kinds(kind: str, perms: Optional[Mapping[str, Any]] = None) -> Tuple[str, ...]:
    """the video request kinds that count toward the movies or tv limit."""
    out = _VIDEO_KINDS[kind]
    if P.ask_limit_kind("youtube", perms) == kind:
        out = out + ("youtube",)
    return out


def music_asks_used(profile_id, days, music_db=None) -> Optional[int]:
    """album and track asks plus music video requests, in the last ``days`` days."""
    try:
        db = _music_db(music_db)
        conn = db._get_connection()
        try:
            used = len(db.music_request_asks_since(conn.cursor(), int(profile_id), int(days)))
        finally:
            conn.close()
        return used + int(db.count_music_video_requests_since(int(profile_id), int(days)) or 0)
    except Exception:  # noqa: BLE001 - see the module doc: None is a full limit
        logger.warning("music ask count failed for profile %s", profile_id, exc_info=True)
        return None


def video_asks_used(profile_id, days, kind: str, video_db=None,
                    perms: Optional[Mapping[str, Any]] = None) -> Optional[int]:
    """movie asks (kind 'movies') or show, episode and YouTube asks ('tv')."""
    try:
        used = _video_db(video_db).count_video_requests_since(
            int(profile_id), int(days), kinds=video_kinds(kind, perms))
        return None if used is None else int(used)
    except Exception:  # noqa: BLE001
        logger.warning("video ask count failed for profile %s", profile_id, exc_info=True)
        return None


def asks_used(profile_id, days, kind: str, *, perms: Optional[Mapping[str, Any]] = None,
              music_db: Any = None, video_db: Any = None) -> Optional[int]:
    """the asks this profile made in the window toward one kind's limit."""
    if kind not in KINDS:
        raise ValueError("asks_used(): kind is one of %s" % ", ".join(KINDS))
    if kind == "music":
        return music_asks_used(profile_id, days, music_db)
    return video_asks_used(profile_id, days, kind, video_db, perms)


def quota_for(perms: Optional[Mapping[str, Any]], kind: str, *,
              is_admin: bool = False) -> Optional[Dict[str, int]]:
    """{limit, days} when this kind is limited for the map, else None
    (admins and n 0 are never counted)."""
    if is_admin:
        return None
    lim = P.limit_of(perms, kind)
    if lim["n"] <= 0:
        return None
    return {"limit": lim["n"], "days": lim["days"]}


def status(profile_id, perms: Optional[Mapping[str, Any]], kind: str, *, is_admin: bool = False,
           music_db: Any = None, video_db: Any = None) -> Optional[Dict[str, Any]]:
    """{kind, limit, days, used, remaining} for one kind, None when it isn't
    limited. an unreadable count reads as the whole limit spent."""
    quota = quota_for(perms, kind, is_admin=is_admin)
    if quota is None:
        return None
    used = asks_used(profile_id, quota["days"], kind, perms=perms, music_db=music_db, video_db=video_db)
    used = quota["limit"] if used is None else int(used)
    return {"kind": kind, "limit": quota["limit"], "days": quota["days"], "used": used,
            "remaining": max(0, quota["limit"] - used)}


__all__ = ["KINDS", "video_kinds", "music_asks_used", "video_asks_used", "asks_used", "quota_for", "status"]
