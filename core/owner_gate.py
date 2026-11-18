"""what a background job may add for the profile it works for (#2166, piece C).

watchlist scans, the airing auto-wishlist, people and studio scans,
collections, import lists and the music watchlist all add wants for a
profile that isn't sitting at the screen. each of them asks one OwnerGate
before every add, so they follow the same rules as that person's own clicks:

- no access to that kind of media, "Can't request", or a map that can't be
  built: nothing is added (fail closed, one log line).
- "Ask first": a request for an admin instead of a download, where a request
  path exists (video requests; a music wishlist row of an ask-first profile
  already is a request, see core/requests/music.py).
- the owner's rating cap (movies, tv) and hide-explicit (music) hold.
- the quality goes through resolve_request_quality. a pick the owner isn't
  allowed falls back to their default, logged.

the owner's rights are read again at most every TTL seconds, so a long scan
notices a change made while it runs. profile 1 (admin) never touches the
database here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Set, Tuple

from core import permissions as P
from utils.logging_config import get_logger

logger = get_logger("permissions.owner_gate")

ADD, REQUEST, SKIP = "add", "request", "skip"

# media -> (access row, get row, rating row, resolver media type)
_MEDIA = {
    "movie": ("movies.access", "movies.get", "movies.max_rating", "movie"),
    "tv": ("tv.access", "tv.get", "tv.max_rating", "tv"),
    "music": ("music.access", "music.get", None, "music"),
}
_VIDEO_KIND = {"movie": "movie", "tv": "show"}


@dataclass(frozen=True)
class Want:
    action: str                          # add | request | skip
    quality_profile_id: Optional[int] = None
    reason: str = ""

    @property
    def add(self) -> bool:
        return self.action == ADD

    @property
    def request(self) -> bool:
        return self.action == REQUEST

    @property
    def skip(self) -> bool:
        return self.action == SKIP


def _default_context(pid: int) -> Dict[str, Any]:
    from core.permission_store import context_for
    from database.music_database import get_database
    return context_for(get_database(), pid)


def _default_profile(pid: int) -> Optional[Dict[str, Any]]:
    try:
        from database.music_database import get_database
        return get_database().get_profile(pid)
    except Exception:  # noqa: BLE001 - the resolver gets the bare id
        return None


def _default_rating(kind: str, tmdb_id) -> Optional[str]:
    from api.video import get_video_db
    from api.video.kids import tmdb_title_rating
    return tmdb_title_rating(get_video_db(), kind, tmdb_id)


def current_owner() -> int:
    """the profile the running background work acts for (the automation
    owner, or the pass's profile in a per-profile fan-out)."""
    try:
        from core.profile_context import get_current_profile_id
        return int(get_current_profile_id() or 1)
    except Exception:  # noqa: BLE001 - no context is the admin-owned system run
        return 1


class OwnerGate:
    """one owner's rights for one background pass."""

    TTL = 60.0

    def __init__(self, owner_id, *, context: Optional[Mapping[str, Any]] = None,
                 load_context: Optional[Callable[[int], Mapping[str, Any]]] = None,
                 load_profile: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
                 rating_of: Optional[Callable[[str, Any], Optional[str]]] = None,
                 resolver: Optional[Callable[..., Any]] = None,
                 clock: Callable[[], float] = time.monotonic, label: str = "background"):
        try:
            self.owner_id: Optional[int] = int(owner_id)
        except (TypeError, ValueError):
            self.owner_id = None
        self.label = label
        self._fixed = context
        self._load_context = load_context or _default_context
        self._load_profile = load_profile or _default_profile
        self._rating_of = rating_of or _default_rating
        self._resolver = resolver
        self._clock = clock
        self._ctx: Optional[Mapping[str, Any]] = None
        self._at = 0.0
        self._profile: Optional[Dict[str, Any]] = None
        self._said: Set[Tuple[str, str]] = set()
        self._asked: Optional[Set[tuple]] = None

    @classmethod
    def for_current(cls, **kw) -> "OwnerGate":
        return cls(current_owner(), **kw)

    # ---- the owner's rights ---------------------------------------------------

    def ctx(self) -> Mapping[str, Any]:
        if self._fixed is not None:
            return self._fixed
        if self.owner_id is None:
            return P.make_context(None, perms=None, source="none")
        if self.owner_id == 1:
            # admin: never touches the database (system runs, tests)
            return P.make_context(1, is_admin=True, perms=P.admin_map(), source="admin")
        now = self._clock()
        if self._ctx is None or now - self._at >= self.TTL:
            try:
                self._ctx = self._load_context(self.owner_id)
            except Exception:  # noqa: BLE001 - can't read it is no rights
                logger.warning("%s: couldn't read profile %s's permissions, adding nothing",
                               self.label, self.owner_id, exc_info=True)
                self._ctx = P.make_context(self.owner_id, perms=None, source="none")
            self._at = now
        return self._ctx

    @property
    def is_admin(self) -> bool:
        return bool(self.ctx().get("is_admin"))

    def can(self, permission: str, value: Any = None) -> bool:
        return P.can(self.ctx(), permission, value)

    def profile(self) -> Dict[str, Any]:
        if self._profile is None:
            row = None
            if self.owner_id not in (None, 1):
                try:
                    row = self._load_profile(self.owner_id)
                except Exception:  # noqa: BLE001
                    row = None
            self._profile = dict(row) if row else {"id": self.owner_id,
                                                   "is_admin": self.owner_id == 1}
            self._profile["is_admin"] = bool(self._profile.get("is_admin")) or self.is_admin
        return self._profile

    def _once(self, key: str, reason: str) -> None:
        if (key, reason) in self._said:
            return
        self._said.add((key, reason))
        logger.warning("%s: profile %s gets no %s added: %s", self.label, self.owner_id, key, reason)

    def _skip(self, media: str, reason: str, title: str = "", blanket: bool = False) -> Want:
        if blanket:
            self._once(media, reason)
        else:
            logger.info("%s: skipped %s %r for profile %s: %s", self.label, media, title or "",
                        self.owner_id, reason)
        return Want(SKIP, None, reason)

    # ---- the check ------------------------------------------------------------

    def want(self, media: str, *, tmdb_id=None, explicit=None, quality_id=None,
             part: Optional[str] = None, title: str = "") -> Want:
        """may a ``media`` want ("movie", "tv", "music") be added for the owner,
        and how. ``part`` is the feature row the want comes through (a page
        part like music.watchlist), checked when given."""
        if media not in _MEDIA:
            raise ValueError("media is movie, tv or music")
        access, get_row, rating_row, qmedia = _MEDIA[media]
        ctx = self.ctx()
        admin = bool(ctx.get("is_admin"))
        if not admin and not isinstance(ctx.get("perms"), Mapping):
            return self._skip(media, "permissions couldn't be read", blanket=True)
        if not admin:
            if not P.can(ctx, access):
                return self._skip(media, "%s is off" % access, blanket=True)
            if not P.can(ctx, get_row, "ask"):
                return self._skip(media, "%s is Can't request" % get_row, blanket=True)
            if part and not P.can(ctx, part):
                return self._skip(media, "%s is off" % part, blanket=True)
            if media == "music" and explicit is not None and P.can(ctx, "music.hide_explicit"):
                from core.content_filter import is_explicit
                if is_explicit(explicit):
                    return self._skip(media, "explicit, and this profile hides explicit", title)
            if rating_row and tmdb_id is not None and self._capped(rating_row):
                try:
                    rating = self._rating_of(_VIDEO_KIND[media], tmdb_id)
                except Exception:  # noqa: BLE001 - unrated under a cap stays out
                    rating = None
                if not rating:
                    return self._skip(media, "no rating under a %s cap" % rating_row, title)
                if not P.can(ctx, rating_row, rating):
                    return self._skip(media, "rated %s, over the cap" % rating, title)
        decision = self._resolve(qmedia, quality_id, title)
        if decision is None or decision.refused:
            return self._skip(media, "no quality profile this profile may use", title)
        mode_download = admin or P.can(ctx, get_row, "download")
        action = ADD if mode_download and not decision.needs_approval else REQUEST
        return Want(action, decision.profile_id, "" if action == ADD else "asks first")

    def may_youtube(self) -> bool:
        """may followed channels and playlists wish new uploads for the
        owner? YouTube channels on, movies or tv on, and one of them
        Download right away. a follow wishes every upload, so Ask first gets
        none (asking for each upload would flood the Requests page, and the
        follow itself needs downloads)."""
        ctx = self.ctx()
        if ctx.get("is_admin"):
            return True
        if not isinstance(ctx.get("perms"), Mapping):
            self._once("youtube", "permissions couldn't be read")
            return False
        ok = P.can(ctx, "video.youtube") and any(
            P.can(ctx, cat + ".access") and P.can(ctx, cat + ".get", "download") for cat in ("movies", "tv"))
        if not ok:
            self._once("youtube", "YouTube channels or downloads are off")
        return ok

    def _capped(self, rating_row: str) -> bool:
        perms = self.ctx().get("perms") or {}
        v = perms.get(rating_row)
        if v == P.FOLLOW:
            v = perms.get("movies.max_rating")
        return v != "any"          # unreadable reads as capped

    def _resolve(self, qmedia: str, wanted, title: str):
        resolver = self._resolver or P.resolve_request_quality
        perms = self.ctx().get("perms")
        try:
            d = resolver(self.profile(), qmedia, wanted, perms=perms)
        except Exception:  # noqa: BLE001 - no decision is no add
            logger.warning("%s: quality for profile %s failed", self.label, self.owner_id, exc_info=True)
            return None
        if d.refused and wanted is not None:
            # the source's own pick (an import list's profile, the artist's
            # quality) isn't one this owner may use: their default instead
            logger.info("%s: quality %s isn't allowed for profile %s (%s), using their default",
                        self.label, wanted, self.owner_id, title or qmedia)
            try:
                d = resolver(self.profile(), qmedia, None, perms=perms)
            except Exception:  # noqa: BLE001
                return None
        return d

    # ---- requests -------------------------------------------------------------

    def asked_before(self, video_db, kind: str, tmdb_id, season=None, episode=None) -> bool:
        """has the owner asked for this already, in any state? a declined ask
        is never filed again by a scan, a pending or approved one is there."""
        if self._asked is None:
            try:
                rows = video_db.list_video_requests(profile_id=self.owner_id, limit=100000) or []
            except Exception:  # noqa: BLE001 - unreadable reads as asked: never a flood
                logger.warning("%s: couldn't read profile %s's requests", self.label, self.owner_id)
                return True
            self._asked = {(r.get("kind"), int(r.get("tmdb_id") or 0), r.get("season_number"),
                            r.get("episode_number")) for r in rows if r.get("tmdb_id") is not None}
        try:
            key = (kind, int(tmdb_id), season, episode)
        except (TypeError, ValueError):
            return True
        return key in self._asked

    def approved_show(self, video_db, tmdb_id) -> bool:
        """an approved show request covers its later episodes (approve follows
        the show for the asker)."""
        try:
            rows = video_db.list_video_requests(profile_id=self.owner_id, status="approved",
                                                limit=100000) or []
        except Exception:  # noqa: BLE001
            return False
        return any(r.get("kind") == "show" and str(r.get("tmdb_id")) == str(tmdb_id) for r in rows)

    def under_limit(self, video_db, kind: str = "movie") -> bool:
        """may the owner file one more ``kind`` request (that kind's request
        limit: movies, or tv for a show, season or episode)? a count that
        can't be read is a full limit."""
        ctx = self.ctx()
        if ctx.get("is_admin"):
            return True
        perms = ctx.get("perms")
        if not isinstance(perms, dict):
            return False
        limit_kind = P.ask_limit_kind(kind, perms) or "movies"
        lim = P.limit_of(perms, limit_kind)
        if lim["n"] <= 0:
            return True
        from core.requests.asks import asks_used
        used = asks_used(self.owner_id, lim["days"], limit_kind, perms=perms, video_db=video_db)
        if used is None:
            return False
        return P.can(ctx, P.LIMIT_ROWS[limit_kind], used)

    def file_video_request(self, video_db, *, kind: str, tmdb_id, title: str, year=None,
                           poster_url=None, quality_profile_id=None, monitor: str = "future",
                           note: Optional[str] = None, season_number=None, episode_number=None,
                           publish: Optional[Callable[[str, dict], Any]] = None) -> str:
        """file a request for the owner. returns filed | asked | over_limit | failed."""
        if self.asked_before(video_db, kind, tmdb_id, season_number, episode_number):
            return "asked"
        if not self.under_limit(video_db, kind):
            self._once("requests", "over the request limit")
            return "over_limit"
        try:
            rid, created = video_db.add_video_request(
                profile_id=self.owner_id, requester_name=self.profile().get("name") or "",
                kind=kind, tmdb_id=int(tmdb_id), title=title, year=year, poster_url=poster_url,
                note=note, monitor=monitor, quality_profile_id=quality_profile_id,
                season_number=season_number, episode_number=episode_number)
        except Exception:  # noqa: BLE001
            logger.warning("%s: request for %r failed", self.label, title, exc_info=True)
            return "failed"
        if rid is None:
            return "failed"
        if self._asked is not None:
            self._asked.add((kind, int(tmdb_id), season_number, episode_number))
        if not created:
            return "asked"
        logger.info("%s: filed a %s request for profile %s: %r", self.label, kind, self.owner_id, title)
        try:
            (publish or _publish)("video_request_created", {"kind": kind, "title": title,
                                                            "requester": self.profile().get("name") or ""})
        except Exception:  # noqa: BLE001 - events never disturb the request
            logger.debug("request-created event failed", exc_info=True)
        return "filed"


def quality_kw(want: Want) -> Dict[str, Any]:
    """``quality_profile_id=`` for an add, only when the gate picked one (so
    adders that predate it keep their call shape)."""
    return {"quality_profile_id": want.quality_profile_id} if want.quality_profile_id is not None else {}


def _publish(event: str, payload: dict) -> None:
    from core.video.download_events import publish
    publish(event, payload)


__all__ = ["OwnerGate", "Want", "ADD", "REQUEST", "SKIP", "current_owner", "quality_kw"]
