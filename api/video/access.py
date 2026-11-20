"""who may do what on the video side (#2166, #2167, #2168).

the video blueprint reads the same effective permissions as everything else
(``g.perms``, put there once per request by web_server), through ``can()``:

  movies.access / tv.access   two switches: a tv-only profile never reaches
                              a movie, anywhere (lists drop them through the
                              kids layer, a direct hit gets the calm refusal)
  movies.get / tv.get         download right away / ask first / can't request
  movies.max_rating, tv.max_rating (tv follows the movie one by default)
  movies.request_limit, tv.request_limit (youtube asks count toward tv),
  requests.see_all, requests.moderate
  movies.quality*, tv.quality* through resolve_request_quality

a request with no ``g.perms`` at all (a caller that skipped web_server's
before_request: tests, edge callers) is read from the old g stamps
(allowed_sides, can_download, request_limit) the way the gate always did, so
nothing reads as more than it was.

``route_access`` is the one table of what each video path needs. the gate
enforces it and the route inventory reads it, so the two can't drift.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from core import permissions as P
from utils.logging_config import get_logger

logger = get_logger("video_api.access")

# ── kinds ────────────────────────────────────────────────────────────────

_ROW = {"movie": "movies", "movies": "movies", "m": "movies",
        "show": "tv", "shows": "tv", "tv": "tv", "episode": "tv", "season": "tv", "series": "tv",
        "t": "tv"}
VIDEO_SIDE = ("movies.access", "tv.access")


def row_for(kind) -> Optional[str]:
    """'movies' | 'tv' for anything naming a kind, else None."""
    return _ROW.get(str(kind or "").strip().lower())


def title_kind(kind) -> Optional[str]:
    """'movie' | 'show' (the video side's own words), else None."""
    r = row_for(kind)
    return "movie" if r == "movies" else "show" if r == "tv" else None


# ── the context ──────────────────────────────────────────────────────────

def _g():
    from flask import g, has_request_context
    return g if has_request_context() else None


def is_admin() -> bool:
    """the gate's admin test: the profile's real is_admin flag, else profile 1."""
    g = _g()
    if g is None:
        return False
    return bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))


def _legacy_perms(g) -> Dict[str, Any]:
    """a map from the old g stamps, for requests web_server didn't stamp."""
    row = {"allowed_sides": getattr(g, "allowed_sides", "both") or "both",
           "can_download": bool(getattr(g, "can_download", True)),
           "max_rating": None, "allowed_pages": None,
           "request_limit": getattr(g, "request_limit", 0) or 0,
           "request_limit_days": getattr(g, "request_limit_days", 7) or 7}
    perms, _reasons = P.perms_from_legacy(row)
    perms["movies.max_rating"] = "any"     # ratings: content_filter reads the row (kids.video_cap)
    return perms


def ctx() -> Dict[str, Any]:
    """this request's can() context, built once."""
    g = _g()
    if g is None:
        return P.make_context(None, perms=None, source="none")
    cached = getattr(g, "_video_perm_ctx", None)
    if cached is not None:
        return cached
    pid = getattr(g, "profile_id", None)
    if is_admin():
        out = P.make_context(pid, is_admin=True, perms=P.admin_map(), source="admin")
    elif hasattr(g, "perms"):
        out = P.make_context(pid, perms=getattr(g, "perms", None),
                             source=getattr(g, "perms_source", "template"))
    else:
        out = P.make_context(pid, perms=_legacy_perms(g), source="legacy-g")
    g._video_perm_ctx = out
    return out


def stamped() -> bool:
    """did web_server put a real map on this request (vs the legacy g read)?"""
    g = _g()
    return g is not None and (is_admin() or hasattr(g, "perms"))


def can(perm: str, value: Any = None) -> bool:
    return P.can(ctx(), perm, value)


def any_side() -> bool:
    c = ctx()
    return any(P.can(c, p) for p in VIDEO_SIDE)


def has_kind(kind) -> bool:
    """may this profile see this kind of title at all? unknown kinds (people,
    studios, youtube) ride on the side itself."""
    r = row_for(kind)
    if r is None:
        return any_side()
    return P.can(ctx(), "%s.access" % r)


def allowed_kinds() -> Tuple[str, ...]:
    return tuple(k for k in ("movie", "show") if has_kind(k))


def mode(kind) -> str:
    """'download' | 'ask' | 'none' for a kind. youtube and kind-less actions
    take the best mode of the kinds this profile can see."""
    c = ctx()
    if c.get("is_admin"):
        return "download"
    perms = c.get("perms")
    if not isinstance(perms, dict):
        return "none"
    r = row_for(kind)
    rows = (r,) if r else ("movies", "tv")
    best = "none"
    for x in rows:
        if not perms.get("%s.access" % x):
            continue
        m = perms.get("%s.get" % x, "none")
        if m in P.MODES and P.MODES.index(m) > P.MODES.index(best):
            best = m
    return best


def may_get(kind, want: str = "download") -> bool:
    m = mode(kind)
    return m != "none" and P.MODES.index(m) >= P.MODES.index(want)


def may_see_all_requests() -> bool:
    c = ctx()
    return bool(c.get("is_admin")) or P.can(c, "requests.see_all") or P.can(c, "requests.moderate")


def is_moderator() -> bool:
    c = ctx()
    return bool(c.get("is_admin")) or P.can(c, "requests.moderate")


def may_moderate(owner_profile_id) -> bool:
    return P.may_moderate(ctx(), owner_profile_id)


def limit_kind(kind) -> str:
    """the request limit an ask of ``kind`` counts toward: 'movies' or 'tv'
    (a youtube video rides with TV, or movies for a profile with no TV)."""
    perms = ctx().get("perms")
    return P.ask_limit_kind(kind, perms if isinstance(perms, dict) else None) or "movies"


def quota_row(pid, kind="movie") -> Optional[Dict[str, int]]:
    """{limit, days} for this profile's limit on ``kind`` (movies, or tv for
    a show, episode or youtube), None when that kind isn't limited (or for
    admins). a member with no map raises: an ask is never let through
    uncounted."""
    c = ctx()
    if c.get("is_admin"):
        return None
    g = _g()
    if not stamped() and (g is None or not hasattr(g, "request_limit")):
        # a member's request with no stamp came through a path that skipped
        # web_server: that is not "no limit"
        raise LookupError("no request limit on record for profile %s" % pid)
    perms = c.get("perms")
    row = P.LIMIT_ROWS[limit_kind(kind)]
    if not isinstance(perms, dict) or not isinstance(perms.get(row, perms.get(P.OLD_LIMIT)), dict):
        raise LookupError("no request limit on record for profile %s" % pid)
    from core.requests.asks import quota_for
    return quota_for(perms, limit_kind(kind))


# ── refusals ─────────────────────────────────────────────────────────────

def refusal(perm: str):
    from flask import jsonify
    return jsonify(P.refusal_body(perm)), P.REFUSAL_STATUS


def kind_refusal(kind):
    """the calm refusal for a kind this profile can't see or can't get."""
    r = row_for(kind) or "movies"
    return refusal("%s.access" % r)


def get_refusal(kind):
    r = row_for(kind)
    return refusal("%s.get" % r if r else "movies.get")


QUALITY_MESSAGE = "That quality isn't one you can pick for this. The usual one is still yours."


def quality_refusal(decision):
    from flask import jsonify
    if decision.error == P.QUALITY_NOT_ALLOWED:
        return jsonify({"success": False, "error": P.QUALITY_NOT_ALLOWED,
                        "message": QUALITY_MESSAGE}), 403
    return refusal("movies.get")


# ── quality ──────────────────────────────────────────────────────────────

def quality_options(db) -> list:
    """every profile a request can name (no blobs), once per request."""
    g = _g()
    cached = getattr(g, "_video_quality_options", None) if g is not None else None
    if cached is not None:
        return cached
    from core.video.quality_profile import quality_options as _opts
    try:
        out = _opts(db)
    except Exception:  # noqa: BLE001 - only Default is known for sure
        logger.warning("quality options unreadable", exc_info=True)
        out = [{"id": 0, "name": "Default", "media_type": None, "tags": []}]
    if g is not None:
        g._video_quality_options = out
    return out


def _kind_default(db, kind) -> Optional[int]:
    from core.video.quality_profile import kind_default_id
    try:
        return kind_default_id(db, title_kind(kind)) or 0
    except Exception:  # noqa: BLE001
        return 0


def resolve(db, kind, wanted, *, context: Optional[Dict[str, Any]] = None):
    """resolve_request_quality for this request (or ``context``, another
    profile's), with the profiles that exist and the kind's default."""
    c = context if context is not None else ctx()
    return P.resolve_request_quality(c, "movie" if title_kind(kind) == "movie" else "tv", wanted,
                                     options=quality_options(db), kind_default=_kind_default(db, kind))


def allowed_qualities(db, kind) -> list:
    """what this profile may pick for a kind: [{id, name, tags, default,
    asks}]. empty when it can't request this kind at all. admins get every
    profile for the kind."""
    from core.video.quality_profile import options_for_kind
    k = "movie" if title_kind(kind) == "movie" else "show"
    opts = options_for_kind(quality_options(db), k)
    c = ctx()
    base = resolve(db, k, None, context=c)
    if base.refused and not c.get("is_admin"):
        return []
    perms = c.get("perms") if isinstance(c.get("perms"), dict) else {}
    r = row_for(k)
    raw = perms.get("%s.quality" % r, P.ALL) if not c.get("is_admin") else P.ALL
    default = base.profile_id
    if default is None:
        default = _kind_default(db, k)
    out = []
    for o in opts:
        if raw != P.ALL and not P._same_quality(o["id"], raw):   # noqa: SLF001 - the one id compare
            continue
        d = resolve(db, k, o["id"], context=c)
        if d.refused:
            continue
        out.append({"id": o["id"], "name": o["name"], "tags": list(o.get("tags") or []),
                    "default": o["id"] == default, "asks": bool(d.needs_approval)})
    if out and not any(x["default"] for x in out):
        out[0]["default"] = True
    return out


# ── the route table ──────────────────────────────────────────────────────
# one answer per (path, method): ("public"|"admin"|"perm", detail). the gate
# enforces it; the route inventory reads it off every video view.

ADMIN_ANY = ("/api/video/overlays", "/api/video/import", "/api/video/collections",
             "/api/video/repair",
             "/api/video/server-config", "/api/video/jellyfin", "/api/video/libraries",
             "/api/video/organization", "/api/video/downloads/slskd",
             # the enrichment workers, reads and writes: their status, what
             # they matched, the controls and the keys are server
             # maintenance (#2196)
             "/api/video/enrichment",
             "/api/video/notifications",   # P11: GETs return webhook URLs/bot tokens
             "/api/video/backups",         # P10: restore/download the whole database
             # delete a whole title; its preview GET lists file paths
             "/api/video/title-delete",
             # the library manager: its rows carry file paths
             "/api/video/manager",
             # server operations the notification panel shows an admin
             # (#2170): disk, roots and monitor health, and the library
             # manager's bulk job. members never see either
             "/api/video/health", "/api/video/bulk")

# config the Settings page writes but content views read: the writes are admin's
ADMIN_WRITES = ("/api/video/server", "/api/video/downloads/config",
                "/api/video/downloads/quality",
                "/api/video/downloads/youtube-quality",
                # the household's recycle bin, download history and queue:
                # restoring or purging files, forgetting grabs and clearing rows
                # is the admin's, like the music downloads page. the GETs stay
                # open (a member's list is scoped to their own grabs). cancel is
                # per row: the route lets a member stop a grab they started
                "/api/video/downloads/recycle",
                "/api/video/downloads/history",
                "/api/video/downloads/remove",
                "/api/video/downloads/clear",
                # start/stop a library scan or a server rescan
                "/api/video/scan",
                # the discover page's languages and streaming services are one
                # setting for the household
                "/api/video/discover/languages",
                "/api/video/discover/providers-pref",
                # library / metadata management, parity with music's
                # @admin_only library edits + the download blocklist config
                "/api/video/bulk", "/api/video/monitor",
                "/api/video/episode/monitor",
                "/api/video/poster/set", "/api/video/downloads/blocklist",
                "/api/video/manual-match",
                "/api/video/files",
                "/api/video/subtitles/overrides",
                "/api/video/subtitles/whisper/download")
ADMIN_WRITE_SUFFIXES = ("/metadata", "/lock", "/refresh-art", "/monitor",
                        "/quality-profile", "/series-type", "/overrides", "/sync",
                        "/identify", "/identify/release", "/padlock")

# actions that fetch something: "download right away" for the kind they name
GET_PREFIXES = ("/api/video/downloads/grab", "/api/video/downloads/retry",
                # clients-tab match & import: following a download is a grab
                "/api/video/downloads/adopt",
                "/api/video/youtube/download",
                # manual subtitle download: an explicit download, same as a grab
                "/api/video/subtitles/manual-download",
                # the tab's bulk action spends the same disk and bandwidth
                "/api/video/wishlist/youtube/download-all",
                "/api/video/wishlist/add",
                # the calendar's "wishlist the missing ones" is a bulk add
                "/api/video/calendar/wishlist-missing",
                "/api/video/watchlist/add", "/api/video/youtube/wishlist/add",
                "/api/video/watch/grab",
                # the wishlist and watchlist are shared by the household: a
                # profile that can't add to them can't empty them either, and
                # searching/retrying spends the same indexer budget as a grab
                "/api/video/wishlist/remove", "/api/video/wishlist/clear",
                "/api/video/wishlist/search", "/api/video/wishlist/retry",
                "/api/video/watchlist/remove", "/api/video/watchlist/person",
                "/api/video/watchlist/studio", "/api/video/youtube/wishlist/remove",
                # following a channel or a playlist wishes its uploads
                "/api/video/youtube/follow", "/api/video/youtube/subscriptions/import",
                "/api/video/youtube/playlist/follow")

# youtube follows are their own row (S9)
YOUTUBE_PREFIXES = ("/api/video/youtube/follow", "/api/video/youtube/subscriptions/import",
                    "/api/video/youtube/playlist/follow", "/api/video/youtube/channel/",
                    "/api/video/youtube/download", "/api/video/youtube/wishlist",
                    "/api/video/wishlist/youtube")

WRITES = ("POST", "PUT", "PATCH", "DELETE")


def _p(path, prefixes) -> bool:
    return any(path.startswith(x) for x in prefixes)


def is_get_action(path: str, method: str) -> bool:
    if method not in WRITES:
        return False
    if _p(path, GET_PREFIXES):
        return True
    return path.startswith("/api/video/youtube/channel/") and path.endswith("/settings")


def route_access(path: str, method: str) -> Tuple[str, Any]:
    """what a video path needs: ("public", None) | ("admin", None) |
    ("perm", (ids, any one of them)). works on a live path or a rule string."""
    from core.video.calendar_feed import is_feed_path
    method = (method or "GET").upper()
    if is_feed_path(path, method) or (method == "GET" and path == "/api/video/calendar/feed/<token>.ics"):
        return ("public", None)        # the feed's token is the credential
    writing = method in WRITES
    if _p(path, ADMIN_ANY):
        return ("admin", None)
    if writing and (_p(path, ADMIN_WRITES) or path.endswith(ADMIN_WRITE_SUFFIXES)):
        return ("admin", None)
    if is_get_action(path, method):
        if _p(path, YOUTUBE_PREFIXES):
            return ("perm", ("video.youtube",))
        return ("perm", ("movies.get", "tv.get"))
    if path.startswith("/api/video/requests") and writing and (
            "/approve" in path or path.endswith("/deny") or path.endswith("/auto")):
        return ("perm", ("requests.moderate",))
    return ("perm", VIDEO_SIDE)


# a path that names its kind: /detail/movie/12, /detail/tmdb/show/9, ...
_KIND_IN_PATH = re.compile(r"/(movie|show|tv|episode|season)(?:/|$)")


def kind_in_path(path: str) -> Optional[str]:
    m = _KIND_IN_PATH.search(path or "")
    return title_kind(m.group(1)) if m else None


def body_kind(body, default: Optional[str] = None) -> Optional[str]:
    """the kind a request body names (kind, scope, media_type; a {"movie": ..}
    or {"show": ..} wishlist body), else ``default``."""
    if not isinstance(body, dict):
        return default
    for key in ("kind", "scope", "media_type", "type"):
        k = title_kind(body.get(key))
        if k:
            return k
        if str(body.get(key) or "").lower() == "youtube":
            return None
    if isinstance(body.get("movie"), dict):
        return "movie"
    if isinstance(body.get("show"), dict):
        return "show"
    kd = str(body.get("kd") or "").lower()          # movie night's bus shape
    if kd in ("m", "t"):
        return "movie" if kd == "m" else "show"
    return default


# actions that only ever touch one kind, whatever the body says
PATH_KINDS = {"/api/video/calendar/wishlist-missing": "show",
              "/api/video/downloads/grab-pack": "show",
              "/api/video/watchlist/add": None}


def path_kind(path: str, body) -> Optional[str]:
    """the kind a get action is about: its path's own, else its body's."""
    fixed = PATH_KINDS.get(path)
    if fixed:
        return fixed
    return body_kind(body)


def member_quality(db, kind):
    """(quality id or None, refusal response or None) for an add with no
    pick of its own by this profile: its default for the kind. a quality
    that needs an OK refuses (a bulk add can't be one request). admins:
    (None, None), the normal default chain."""
    from flask import jsonify
    if is_admin():
        return None, None
    d = resolve(db, kind, None)
    if d.refused:
        return None, quality_refusal(d)
    if d.needs_approval:
        return None, (jsonify({"success": False, "error": "needs_approval", "needs_approval": True,
                               "message": "Your usual quality needs an OK first. Ask for these "
                                          "from their Request button."}), 403)
    return d.profile_id, None


__all__ = ["row_for", "title_kind", "ctx", "can", "is_admin", "any_side", "has_kind", "allowed_kinds",
           "mode", "may_get", "may_see_all_requests", "is_moderator", "may_moderate", "quota_row", "limit_kind",
           "refusal", "kind_refusal", "get_refusal", "quality_refusal", "quality_options", "resolve",
           "allowed_qualities", "route_access", "kind_in_path", "body_kind", "is_get_action",
           "stamped", "VIDEO_SIDE", "path_kind", "member_quality"]
