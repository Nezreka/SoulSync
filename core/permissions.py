"""profile permission decisions, one copy.

the download check lived three times (web_server, api/helpers, and not at all
in the background wishlist run) and every copy read a missing profile as the
admin: ``int(pid or 1)``. with no profile picked that waved the download
through. these take the lookups as arguments so they test without the app.

people and permissions (#2166, #2167, #2168) live here too, further down:
the catalogue of every permission, the layers that make what a profile may
do (global policy ceiling > template > personal changes = effective), the
check (``can``), the route decorator (``requires``), the standard refusal,
the request quality resolver, and the mapping to and from the old profile
columns, which stay as a written-through cache. all pure: storage is
core.permission_store, the routes are api/permissions_routes.py.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

DOWNLOADS_OFF = "Downloads are disabled for this profile."
NO_PROFILE = "Pick a profile first."
PROFILE_UNREADABLE = "Couldn't check this profile's download rights. Try again in a moment."


def profile_can_download(profile: Optional[Dict[str, Any]]) -> bool:
    """a profile row's own answer. the admin profile always can."""
    if not profile:
        return False
    try:
        if int(profile.get("id") or 0) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(profile.get("can_download", True))


def owner_may_download(pid, get_profile: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None) -> bool:
    """may a background job download for the profile that owns this row?

    the wishlist and watchlist workers run as the system, not as the person
    whose row it is, so they ask about the owner, at grab time (a switch
    turned off after the add counts). fails closed: an owner that can't be
    read is skipped this pass and looked at again on the next."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == 1:
        return True
    try:
        if get_profile is None:
            from database.music_database import get_database
            get_profile = get_database().get_profile
        profile = get_profile(pid)
    except Exception:  # noqa: BLE001 - see docstring, skip until it can be read
        return False
    if profile and profile.get("is_admin"):
        return True
    return profile_can_download(profile)


def owner_may_get(pid, permission: str, db=None) -> bool:
    """owner_may_download for one kind of media (#2166): may a background
    job get ``permission`` (books.get, podcasts.get) right away for the
    profile that owns the row? read from that owner's effective permissions
    under the global policy (core.permission_store.context_for), at grab
    time, so turning it off after the add counts. ``db`` is the music
    database (the default one when None). fails closed: an owner whose
    rights can't be read is skipped this pass."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid == 1:
        return True
    try:
        from core.permission_store import context_for
        if db is None:
            from database.music_database import get_database
            db = get_database()
        if not hasattr(db, "get_permission_inputs"):
            # a database without the permission tables (an old test double)
            # answers from the profile's one download switch, as before
            return owner_may_download(pid, db.get_profile)
        ctx = context_for(db, pid)
    except Exception:  # noqa: BLE001 - see docstring
        return False
    if not isinstance(ctx, Mapping):
        return False
    if ctx.get("is_admin"):
        return True
    return can(ctx, permission, "download")


def owner_mode(pid, permission: str, db=None) -> str:
    """download | ask | none for ``permission`` (books.get, podcasts.get) of
    the profile that owns a row, read the way owner_may_get reads it. an
    owner whose rights can't be read is none (fails closed)."""
    if owner_may_get(pid, permission, db=db):
        return "download"
    try:
        pid = int(pid)
        from core.permission_store import context_for
        if db is None:
            from database.music_database import get_database
            db = get_database()
        if not hasattr(db, "get_permission_inputs"):
            return "none"
        ctx = context_for(db, pid)
    except Exception:  # noqa: BLE001 - see docstring
        return "none"
    if not isinstance(ctx, Mapping):
        return "none"
    return "ask" if can(ctx, permission, "ask") else "none"


def download_denied_reason(pid, get_profile: Callable[[int], Optional[Dict[str, Any]]]) -> Optional[str]:
    """why this profile may not start a download, or None when it may."""
    if pid is None:
        return NO_PROFILE
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return NO_PROFILE
    if pid == 1:
        return None
    try:
        profile = get_profile(pid)
    except Exception:
        # a check that can't read the profile row can't say yes: a profile
        # with downloads off would download through every db hiccup. the
        # reason tells the caller it's passing, not a switch that's off
        return PROFILE_UNREADABLE
    # no row is not a row that says no (a deleted profile never gets here, the
    # session gate drops it). callers pass a getter that raises on a read
    # error, so an unreadable row lands above instead of here.
    if profile and not profile.get("can_download", True):
        return DOWNLOADS_OFF
    return None


def strict_profile_getter(get_database: Callable[[], Any]) -> Callable[[int], Optional[Dict[str, Any]]]:
    """a get_profile for download_denied_reason that raises on a read error
    instead of answering None (which reads as "no row, allowed"). a stand-in
    database without the flag (tests, the lidarr shim) is asked plainly."""
    def _get(pid):
        db = get_database()
        try:
            return db.get_profile(pid, raise_errors=True)
        except TypeError as exc:
            if "raise_errors" not in str(exc):
                raise
            return db.get_profile(pid)
    return _get


def may_manage_profile(actor_id, actor_is_admin: bool, target_id) -> bool:
    """may this caller change the target profile's name, pin, password or
    access? yourself, always. anyone else, only as an admin. profile 1 is the
    owner: a second admin can't reset its pin or password or demote it,
    because that is a takeover of the install."""
    try:
        actor_id = int(actor_id) if actor_id is not None else None
        target_id = int(target_id)
    except (TypeError, ValueError):
        return False
    if actor_id is None:
        return False
    if actor_id == target_id:
        return True
    if target_id == 1:
        return actor_id == 1
    return bool(actor_is_admin)


def may_cancel_download(owner_profile_id, actor_id, actor_is_admin: bool) -> bool:
    """may this caller cancel a download? an admin, any of them. anyone else,
    only one their own profile started: managing the downloads page is the
    admin's, stopping something you asked for is yours. a download with no
    recorded owner is the admin's."""
    if actor_is_admin:
        return True
    try:
        return (owner_profile_id is not None and actor_id is not None
                and int(owner_profile_id) == int(actor_id))
    except (TypeError, ValueError):
        return False


# what anyone may see about another profile: enough to draw the picker card
PUBLIC_PROFILE_FIELDS = ("id", "name", "avatar_color", "avatar_url", "is_admin",
                         "has_pin", "has_password", "disabled")


def profile_view_for(profile: Dict[str, Any], *, viewer_id, viewer_is_admin: bool) -> Dict[str, Any]:
    """a profile row as this viewer may see it. admins and the profile itself
    get everything; everyone else (including a locked lock screen) just the
    card, not recovery questions, library folders or linked usernames."""
    if viewer_is_admin:
        return profile
    try:
        if viewer_id is not None and int(viewer_id) == int(profile.get("id")):
            return profile
    except (TypeError, ValueError):
        pass
    return {k: profile.get(k) for k in PUBLIC_PROFILE_FIELDS if k in profile}


def profiles_listed_for(profiles, *, viewer_id, viewer_is_admin: bool, login_mode: bool):
    """the profiles this viewer may list, each as profile_view_for shows it.

    login mode (#2162): everyone signs in with their own password, so a
    member has no one to switch to and no business knowing who else is on
    the server. they get their own profile and nothing else. the household
    picker is different: it lists every card before anyone opens a profile,
    so the cards stay listed there (hiding them from a signed-in member
    would hide nothing). admins see everyone either way."""
    if login_mode and not viewer_is_admin:
        try:
            me = int(viewer_id) if viewer_id is not None else None
        except (TypeError, ValueError):
            me = None
        if me is None:
            return []
        profiles = [p for p in profiles if _same_id(p.get("id"), me)]
    return [profile_view_for(p, viewer_id=viewer_id, viewer_is_admin=viewer_is_admin)
            for p in profiles]


def _same_id(value, me: int) -> bool:
    try:
        return int(value) == me
    except (TypeError, ValueError):
        return False


# PAGE_EXCLUSIVE_APIS (api prefixes that belong to exactly one page) is built
# from the catalogue below: each page permission carries the prefixes only its
# page calls. a profile whose page list leaves the page out gets a 403 from
# these too, not just a hidden button. shared apis (stats feed the dashboard,
# automations feed sync, podcasts feed the watchlist) stay out: gating them
# would break the pages that are allowed.


def page_for_path(path: str):
    path = path or ""
    for prefix, page in PAGE_EXCLUSIVE_APIS:
        if path == prefix or path.startswith(prefix + "/"):
            return page
    return None


def page_denied(path: str, allowed_pages, is_admin: bool) -> bool:
    """True when this api belongs to a page the profile isn't given.
    None = every page (the default), admins always pass."""
    if is_admin or allowed_pages is None:
        return False
    page = page_for_path(path)
    return page is not None and page not in set(allowed_pages)


# ══════════════════════════════════════════════════════════════════════════
# people and permissions (#2166, #2167, #2168)
#
#   global policy (ceiling) > template > personal changes = effective
#
# - the policy is the most any member may have. admins are never limited.
# - a template (adult, teen, kids, guest, your own) is a full map; a profile
#   follows one, so changing the template reaches everyone on it.
# - personal changes store only the differences. a change above the ceiling
#   is kept but does nothing until the ceiling is lifted (reversible).
# - effective = clamp(policy, overrides.get(p, template[p])) per kind.
# - sidebar & look is presentation: it is in the catalogue so the editor can
#   draw it, but it is never in an effective map and can() refuses to answer
#   for it. presentation never grants.
# - the old profile columns (allowed_sides, allowed_pages, can_download,
#   hide_explicit, max_rating, request_limit*) are a written-through cache of
#   the effective map, so every check written before this keeps working.
# ══════════════════════════════════════════════════════════════════════════

# kinds
BOOL = "bool"            # allowed / admins only.            clamp: value and ceiling
RESTRICT = "restrict"    # a restriction switch (hide explicit). clamp: value or ceiling
MODE = "mode"            # ordered: none < ask < download.    clamp: min
RATING = "rating"        # ordered: G < PG < PG-13 < R < any. clamp: min
COUNT = "count"          # {"n", "days"}, n 0 = no limit.     clamp: the stricter rate
SET = "set"              # "*" or [ids].                       clamp: intersection
CHOICE = "choice"        # one id or None, inside a SET row.   never above the set
SET_FLAG = "set_flag"    # [ids or tokens] that always ask.    clamp: union
FIXED = "fixed"          # always on for everyone, not stored (cancel your own, report problems)
ADMIN = "admin"          # the is_admin column itself, not stored here
PRESENTATION = "presentation"  # sidebar & look, never grants

KINDS = (BOOL, RESTRICT, MODE, RATING, COUNT, SET, CHOICE, SET_FLAG, FIXED, ADMIN, PRESENTATION)

MODES = ("none", "ask", "download")                 # low to high
MODE_LABELS = {"download": "Auto-approve", "ask": "Require approval", "none": "Requests off"}
# a mode row whose values read differently (#2166 follow-up 6): a podcast
# can always be followed, only the downloading is off
ROW_VALUE_LABELS = {"podcasts.get": {"download": "Auto-approve", "ask": "Require approval",
                                     "none": "Follow only"}}
RATINGS = ("G", "PG", "PG-13", "R", "any")          # low to high
FOLLOW = "follow"                                   # tv.max_rating: same as movies
TV_RATING_LABELS = {"G": "TV-Y7", "PG": "TV-PG", "PG-13": "TV-14", "R": "TV-MA", "any": "Any"}
ALL = "*"                                           # a SET that allows every id, later ones too
QUALITY_TOKENS = ("4k",)                            # SET_FLAG tokens: "every 4K profile"
COUNT_MAX = 1000
DAYS_MAX = 365

CATEGORIES: List[Dict[str, str]] = [
    {"id": "general", "label": "General & account",
     "help": "Their own account, chat and their own automations."},
    {"id": "movies", "label": "Movies",
     "help": "Movies, plus the Movies & TV pages they share with TV shows."},
    {"id": "tv", "label": "TV shows", "help": "Shows and seasons."},
    {"id": "music", "label": "Music", "help": "Library, discover, playlists and downloads."},
    {"id": "books", "label": "Audiobooks & podcasts", "help": "Books and shows to listen to."},
    {"id": "requests", "label": "Requests & moderation",
     "help": "Whose requests they see, and who may answer them. Each kind's request limit sits with it."},
    {"id": "admin", "label": "Administration",
     "help": "These belong to admins. Members never get them."},
    {"id": "look", "label": "Sidebar & look",
     "help": "What shows in their sidebar. It only changes what shows, never what they may do."},
]
CATEGORY_IDS = tuple(c["id"] for c in CATEGORIES)


@dataclass(frozen=True)
class Perm:
    id: str
    kind: str
    category: str
    label: str
    help: str
    default: Any = None                 # adult template, and what a template gets for a row added later
    values: Tuple[str, ...] = ()        # MODE: the values this row offers (low to high)
    pages: Tuple[str, ...] = ()         # shell page ids it owns (allowed_pages is derived from these)
    api_prefixes: Tuple[str, ...] = ()  # prefixes the page gate enforces today (page_denied)
    planned_prefixes: Tuple[str, ...] = ()  # prefixes piece A should gate once checked for sharing
    scope: str = "PTO"                  # P policy can cap it, T template, O personal change
    group: str = ""                     # a sub-heading inside the category
    also_in: Tuple[str, ...] = ()       # other categories that show the same row (shared video pages)
    needs: Tuple[str, ...] = ()         # any of these must be on for the row to matter ("turn on X first")
    set_row: str = ""                   # CHOICE: the SET row it must sit inside
    media: str = ""                     # quality rows: music | movie | tv
    legacy: str = ""                    # the old column that caches it
    enforced_at: Tuple[str, ...] = ()   # where the server checks it (and the findings it closes)

    @property
    def presentation(self) -> bool:
        return self.kind == PRESENTATION

    @property
    def stored(self) -> bool:
        """in a policy / template / override map (and the effective map)."""
        return self.kind not in (FIXED, ADMIN, PRESENTATION)


def _p(*args, **kw) -> Perm:
    return Perm(*args, **kw)


_VIDEO_NEEDS = ("movies.access", "tv.access")

CATALOGUE_ROWS: Tuple[Perm, ...] = (
    # ── general & account ────────────────────────────────────────────────
    _p("account.admin", ADMIN, "general", "Admin",
       "Everything, including settings and people.", default=False, scope="O",
       legacy="is_admin", enforced_at=("core/profile_context.py admin_only",)),
    _p("account.connect", BOOL, "general", "Connect their own accounts",
       "Spotify, Tidal, ListenBrainz, Last.fm, their Plex user or Navidrome login.", default=True,
       enforced_at=("NEW: /api/profiles/me/{spotify,lastfm,listenbrainz,connections,plex-*,navidrome-*,oidc*}",)),
    _p("account.own_library", BOOL, "general", "Own library",
       "Their downloads go to their own folder and server library.", default=True, scope="PO",
       enforced_at=("api/user_profiles.py library mode (admin sets it; the policy only hides the switch)",)),
    _p("chat.use", BOOL, "general", "Chat & Arcade",
       "Talk with other people here and play games.", default=True, pages=("chat", "video-chat"),
       legacy="allowed_pages", planned_prefixes=("/api/chat",),
       enforced_at=("NEW: /api/chat prefix + chat socket events",)),
    _p("automations.personal", BOOL, "general", "Their own automations",
       "Playlist pulls, their own scans and notes to themselves. System jobs stay with admins.",
       default=True, pages=("automations",), legacy="allowed_pages",
       planned_prefixes=("/api/automations",),
       enforced_at=("NEW (S4, decision 4): api/automations.py actions limited to PERSONAL_AUTOMATION_ACTIONS",)),

    # ── movies ───────────────────────────────────────────────────────────
    _p("movies.access", BOOL, "movies", "Movies",
       "Browse, search, watch and ask for movies.", default=True, legacy="allowed_sides",
       enforced_at=("api/video/__init__.py side gate (today one switch for movies and tv)",
                    "core/seerr_compat/service.py video_allowed", "NEW: per-type split (decision 8)")),
    _p("movies.get", MODE, "movies", "Movie requests",
       "Auto-approve, Require approval or Requests off.", default="download", values=MODES,
       needs=("movies.access",), legacy="can_download",
       enforced_at=("api/video/requests.py", "api/video/__init__.py download prefixes",
                    "api/video/wishlist.py", "api/video/watchlist.py", "seerr create", "video automations")),
    _p("movies.max_rating", RATING, "movies", "Maximum content rating",
       "Titles above it, or with no rating, stay out of sight.", default="any",
       needs=("movies.access",), legacy="max_rating",
       enforced_at=("core/content_filter.py via api/video/kids.py", "NEW write paths (S16)")),
    _p("movies.request_limit", COUNT, "movies", "Request limit",
       "Movie asks. Movies they get right away are never capped.",
       default={"n": 0, "days": 7}, needs=("movies.access",), legacy="request_limit",
       enforced_at=("api/video/requests.py", "seerr _file_request", "core/owner_gate.py")),
    _p("movies.quality", SET, "movies", "Allowed quality profiles",
       "Only profiles for movies.", default=ALL, needs=("movies.access",), media="movie",
       enforced_at=("resolve_request_quality on every request path (S23, #2168)",)),
    _p("movies.quality_default", CHOICE, "movies", "Default quality profile",
       "What a request gets when they don't pick.", default=None, set_row="movies.quality",
       needs=("movies.access",), media="movie"),
    _p("movies.quality_ask", SET_FLAG, "movies", "Needs my OK",
       "These always come to you first.", default=[], needs=("movies.access",), media="movie"),
    # the movies & tv pages, shared by both kinds
    _p("video.dashboard", BOOL, "movies", "Dashboard", "The movies & TV home page.",
       default=True, pages=("video-dashboard",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages"),
    _p("video.search", BOOL, "movies", "Search", "Find movies and shows.",
       default=True, pages=("video-search",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages"),
    _p("video.library", BOOL, "movies", "Library", "What's already on the server.",
       default=True, pages=("video-library",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages"),
    _p("video.discover", BOOL, "movies", "Discover", "Trending, upcoming and recommended.",
       default=True, pages=("video-discover",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages", planned_prefixes=("/api/video/discover",)),
    _p("video.calendar", BOOL, "movies", "Calendar", "Air dates and releases, plus the iCal feed.",
       default=True, pages=("video-calendar",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages", planned_prefixes=("/api/video/calendar",)),
    _p("video.watchlist", BOOL, "movies", "Watchlist", "Follow people, studios and channels.",
       default=True, pages=("video-watchlist",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages", planned_prefixes=("/api/video/watchlist",)),
    _p("video.wishlist", BOOL, "movies", "Wishlist", "What they're waiting for.",
       default=True, pages=("video-wishlist",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages", planned_prefixes=("/api/video/wishlist",)),
    _p("video.downloads", BOOL, "movies", "Downloads", "The movies & tv download queue.",
       default=True, pages=("video-downloads",), group="Movies & TV pages", also_in=("tv",),
       needs=_VIDEO_NEEDS, legacy="allowed_pages"),
    _p("video.youtube", BOOL, "movies", "YouTube channels", "Follow channels and get new uploads.",
       default=True, group="Movies & TV pages", also_in=("tv",), needs=_VIDEO_NEEDS,
       planned_prefixes=("/api/video/youtube",),
       enforced_at=("NEW: api/video/youtube.py follows (S9)",)),

    # ── tv shows ─────────────────────────────────────────────────────────
    _p("tv.access", BOOL, "tv", "TV shows",
       "Browse, search, watch and ask for shows.", default=True, legacy="allowed_sides",
       enforced_at=("api/video/__init__.py side gate (today one switch for movies and tv)",
                    "NEW: per-type split (decision 8)")),
    _p("tv.get", MODE, "tv", "TV show requests",
       "Auto-approve, Require approval or Requests off. A season or a whole series is one request.", default="download", values=MODES,
       needs=("tv.access",), legacy="can_download",
       enforced_at=("api/video/requests.py", "api/video/__init__.py download prefixes", "seerr create")),
    _p("tv.max_rating", RATING, "tv", "Maximum content rating",
       "TV ratings follow the movie one unless you set their own: PG-13 allows up to TV-14.",
       default=FOLLOW, needs=("tv.access",), legacy="max_rating",
       enforced_at=("core/content_filter.py (today one cap for both)",)),
    _p("tv.request_limit", COUNT, "tv", "Request limit",
       "Show, season, episode and YouTube asks. A season or a whole series is one ask.",
       default={"n": 0, "days": 7}, needs=("tv.access",), legacy="request_limit",
       enforced_at=("api/video/requests.py", "seerr _file_request", "core/owner_gate.py")),
    _p("tv.quality", SET, "tv", "Allowed quality profiles", "Only profiles for shows.",
       default=ALL, needs=("tv.access",), media="tv"),
    _p("tv.quality_default", CHOICE, "tv", "Default quality profile",
       "What a request gets when they don't pick.",
       default=None, set_row="tv.quality", needs=("tv.access",), media="tv"),
    _p("tv.quality_ask", SET_FLAG, "tv", "Needs my OK", "These always come to you first.",
       default=[], needs=("tv.access",), media="tv"),

    # ── music ────────────────────────────────────────────────────────────
    _p("music.access", BOOL, "music", "Music side", "Library, search and discover.",
       default=True, legacy="allowed_sides",
       enforced_at=("NEW music-side gate (S15)",)),
    _p("music.apps", BOOL, "music", "Use music apps",
       "Listen from music apps paired in Settings > Devices.", default=True,
       needs=("music.access",),
       enforced_at=("core/connect/pick.py: the pick list and every request a music app makes",)),
    _p("music.get", MODE, "music", "Music requests",
       "Auto-approve, Require approval or Requests off.", default="download", values=MODES,
       needs=("music.access",), legacy="can_download",
       enforced_at=("web_server.check_download_permission", "wishlist routes",
                    "core/wishlist/processing.py approved_only")),
    _p("music.hide_explicit", RESTRICT, "music", "Hide explicit music",
       "Albums and tracks marked explicit are left out.", default=False,
       needs=("music.access",), legacy="hide_explicit",
       enforced_at=("api/content_guard.py", "NEW paths (S17)")),
    _p("music.request_limit", COUNT, "music", "Request limit",
       "Album, track and music video asks. Music they get right away is never capped.",
       default={"n": 0, "days": 7}, needs=("music.access",), legacy="request_limit",
       enforced_at=("api/music_requests.py quota", "music_database music quota",
                    "core/owner_gate.py")),
    _p("music.quality", SET, "music", "Allowed quality profiles", "Music quality profiles.",
       default=ALL, needs=("music.access",), media="music",
       enforced_at=("resolver on web_server wishlist add, api/wishlist_routes.py, "
                    "api/artist_watchlist.py, api/mirrored_playlists.py",)),
    _p("music.quality_default", CHOICE, "music", "Default quality profile",
       "What an add gets when they don't pick.",
       default=None, set_row="music.quality", needs=("music.access",), media="music"),
    _p("music.dashboard", BOOL, "music", "Dashboard", "The music home page.",
       default=True, pages=("dashboard",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages"),
    _p("music.search", BOOL, "music", "Search", "Find artists, albums and tracks.",
       default=True, pages=("search",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages"),
    _p("music.library", BOOL, "music", "Library", "What's already on the server.",
       default=True, pages=("library",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages"),
    _p("music.discover", BOOL, "music", "Discover",
       "Picks, mixes and new music made from what they play.",
       default=True, pages=("discover",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages",
       planned_prefixes=("/api/discover", "/api/discovery", "/api/discovery-pool")),
    _p("music.playlist_sync", BOOL, "music", "Playlist sync",
       "Mirror playlists from Spotify, Tidal, YouTube and more.",
       default=True, pages=("sync",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", planned_prefixes=("/api/sync", "/api/mirrored-playlists")),
    _p("music.explorer", BOOL, "music", "Playlist Explorer",
       "Every playlist from every source in one tree.",
       default=True, pages=("playlist-explorer",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", api_prefixes=("/api/playlist-explorer",)),
    _p("music.import", BOOL, "music", "Import", "Bring in music from disk.",
       default=True, pages=("import",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", api_prefixes=("/api/import", "/api/auto-import")),
    _p("music.stats", BOOL, "music", "Listening stats", "Charts of what they play.",
       default=True, pages=("stats",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", api_prefixes=("/api/listening-stats",)),
    _p("music.watchlist", BOOL, "music", "Watchlist", "Follow artists for new releases.",
       default=True, pages=("watchlist",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", planned_prefixes=("/api/watchlist",)),
    _p("music.wishlist", BOOL, "music", "Wishlist", "What they're waiting for.",
       default=True, pages=("wishlist",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages"),
    _p("music.downloads", BOOL, "music", "Downloads", "The music download queue page.",
       default=True, pages=("active-downloads",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages"),
    _p("music.videos", BOOL, "music", "Music videos",
       "The Videos tab in search and on artist pages.", default=True,
       group="Parts of music", needs=("music.access",),
       enforced_at=("NEW: api/music_requests.py music video requests",)),
    _p("music.sample_studio", BOOL, "music", "Sample Studio",
       "Split songs into stems and chop samples.",
       default=True, pages=("sample-studio",), group="Parts of music", needs=("music.access",),
       legacy="allowed_pages", api_prefixes=("/api/sample",)),

    # ── audiobooks & podcasts ────────────────────────────────────────────
    _p("books.access", BOOL, "books", "Audiobooks",
       "Browse Audible, follow authors and get books.",
       default=True, pages=("audiobooks",), legacy="allowed_pages", api_prefixes=("/api/audiobooks",)),
    # ask first files a book request (core/requests/media.py, #2188): the
    # book reaches their wishlist when an admin approves it
    _p("books.get", MODE, "books", "Audiobook requests",
       "Auto-approve, Require approval or Requests off.",
       default="download", values=MODES, needs=("books.access",), legacy="can_download",
       enforced_at=("api/helpers.py download gate", "api/audiobooks.py wishlist add (asks file a request)",
                    "core/audiobook_wishlist_worker.py owner check + approved requests")),
    _p("podcasts.access", BOOL, "books", "Podcasts",
       "Subscribe to shows; new episodes arrive on their own.",
       default=True, pages=("podcasts",), legacy="allowed_pages", planned_prefixes=("/api/podcasts",)),
    _p("podcasts.get", MODE, "books", "Podcast episode requests",
       "Auto-approve, Require approval or Follow only. Subscribing to shows is always allowed.",
       default="download", values=MODES, needs=("podcasts.access",), legacy="can_download",
       enforced_at=("api/podcasts.py (asks file an episode request)", "core/podcast_automation.py")),

    # ── requests & moderation ────────────────────────────────────────────
    _p("requests.see_all", BOOL, "requests", "See everyone's requests",
       "Members see only their own unless this is on.", default=False,
       enforced_at=("NEW: GET /api/video/requests, music requests list, seerr list (S7)",)),
    _p("requests.moderate", BOOL, "requests", "Moderator",
       "Approve, decline and manage other people's requests and issues. Never settings, people or tools.",
       default=False,
       enforced_at=("NEW: approve/deny routes (api/music_requests.py, api/video/requests.py), issues "
                    "management; see may_moderate()",)),
    _p("requests.cancel_own", FIXED, "requests", "Cancel their own downloads", "Always on.",
       default=True, scope="", enforced_at=("may_cancel_download",)),
    _p("issues.report", FIXED, "requests", "Report problems", "Issues is always there for everyone.",
       default=True, scope="", enforced_at=("api/issues.py", "api/video/issues.py")),

    # ── administration ───────────────────────────────────────────────────
    # the two tools pages are grantable today (the page list has them), so
    # they stay rows; every tools job behind them is admin_only
    _p("admin.tools", BOOL, "admin", "Tools page",
       "Shows the Tools page. Jobs that change the library still need an admin.",
       default=True, pages=("tools",), legacy="allowed_pages"),
    _p("admin.video_tools", BOOL, "admin", "Movies & TV tools page",
       "Shows the video Tools page. Jobs that change the library still need an admin.",
       default=True, pages=("video-tools",), needs=_VIDEO_NEEDS, legacy="allowed_pages"),
    _p("admin.everything", FIXED, "admin", "Settings, people, tools jobs, automations for everyone, "
       "server activity", "Admins only, always.", default=False, scope=""),

    # ── sidebar & look (presentation, never grants) ──────────────────────
    _p("ui.sidebar", PRESENTATION, "look", "Start from",
       "Everything or Simple, then one switch per part. Only parts they can use are offered.",
       scope="TO", legacy="ui_features"),
    _p("ui.locked", PRESENTATION, "look", "Let them change it",
       "Off: they see exactly this and Customize goes away for them.", scope="TO",
       legacy="ui_features_locked"),
    _p("ui.home_page", PRESENTATION, "look", "Opens on", "Only pages they can use.", scope="O",
       legacy="home_page"),
)

CATALOGUE: Dict[str, Perm] = {r.id: r for r in CATALOGUE_ROWS}
STORED_IDS: Tuple[str, ...] = tuple(r.id for r in CATALOGUE_ROWS if r.stored)
PRESENTATION_IDS: Tuple[str, ...] = tuple(r.id for r in CATALOGUE_ROWS if r.presentation)
PAGE_ROWS: Tuple[Perm, ...] = tuple(r for r in CATALOGUE_ROWS if r.pages)

# ── request limits, one per kind ─────────────────────────────────────────
# music, movies and TV each have their own limit. "requests.limit" was the
# one shared row before; it's still read as "every kind's" so an old caller
# (a body that sets it, a can() that asks it) keeps working: setting it sets
# all three, and asking it is held to the strictest.
LIMIT_KINDS: Tuple[str, ...] = ("music", "movies", "tv")
LIMIT_ROWS: Dict[str, str] = {k: "%s.request_limit" % k for k in LIMIT_KINDS}
OLD_LIMIT = "requests.limit"
LIMIT_WORDS = {"music": "music", "movies": "movie", "tv": "TV"}

# what an ask is, to the limit it counts toward. a season, a whole series
# and an episode are TV; a music video is music. youtube rides with TV, or
# with movies for a profile that has no TV (see ask_limit_kind).
_ASK_KIND = {"album": "music", "track": "music", "single": "music", "music": "music",
             "music_video": "music", "artist": "music",
             "movie": "movies", "movies": "movies",
             "show": "tv", "tv": "tv", "series": "tv", "season": "tv", "episode": "tv"}


def limit_words(lim: Mapping[str, int], kind: Optional[str] = None) -> str:
    """"5 movie asks a week", "1 ask a day", "3 TV asks every 10 days"."""
    n = int(lim.get("n") or 0)
    days = int(lim.get("days") or 7)
    noun = ("%s ask" % LIMIT_WORDS[kind]) if kind else "ask"
    noun += "" if n == 1 else "s"
    span = "a day" if days == 1 else "a week" if days == 7 else "a month" if days == 30 \
        else "every %d days" % days
    return "%d %s %s" % (n, noun, span)


def ask_limit_kind(kind, perms: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """'music' | 'movies' | 'tv' for an ask of this kind, else None. a youtube
    video counts toward TV (it's a channel's episode), or toward movies when
    the profile can't see TV at all, so it never lands on a limit they can't
    see."""
    k = str(kind or "").strip().lower()
    if k == "youtube":
        if perms is not None and not perms.get("tv.access") and perms.get("movies.access"):
            return "movies"
        return "tv"
    return _ASK_KIND.get(k)


def limit_of(perms: Optional[Mapping[str, Any]], kind: str) -> Dict[str, int]:
    """{n, days} for one kind from an effective map; n 0 = no limit. a map
    without the row (an old one) reads the shared row it came from."""
    perms = perms or {}
    lim = perms.get(LIMIT_ROWS[kind])
    if not isinstance(lim, Mapping):
        lim = perms.get(OLD_LIMIT)
    if not isinstance(lim, Mapping):
        return {"n": 0, "days": 7}
    try:
        return {"n": int(lim.get("n") or 0), "days": max(1, int(lim.get("days") or 7))}
    except (TypeError, ValueError):
        return {"n": 0, "days": 7}
# every page the catalogue knows, and its row
PAGE_OWNER: Dict[str, str] = {pg: r.id for r in PAGE_ROWS for pg in r.pages}
# pages a page list never decides: always there, admin only, or derived
UNGATED_PAGES = frozenset({"help", "issues", "settings", "artist-detail", "label-detail", "requests",
                           "video-help", "video-issues", "video-requests", "video-settings",
                           "video-import", "video-automations"})
# old rows still carry retired page ids (webui/static/profile-nav.js ALIASES)
PAGE_ALIASES = {"downloads": "search", "artists": "search", "library-v2": "library"}

PAGE_EXCLUSIVE_APIS = tuple((prefix, r.pages[0]) for r in PAGE_ROWS for prefix in r.api_prefixes)

# decision 4 (S4): what a member's own automation may do. everything else
# (library cleanup, recycle bins, repair, backups, duplicate cleaner, deep
# scans, search_and_download, every video_* job) is an admin's. piece A
# enforces this in api/automations.py; confirm scan_watchlist only scans the
# owner's own artists before relying on it.
PERSONAL_AUTOMATION_ACTIONS = frozenset({
    "refresh_mirrored", "sync_playlist", "discover_playlist", "playlist_pipeline",
    "personalized_pipeline", "import_lastfm_listening", "import_listenbrainz_listening",
    "scan_watchlist",
})


class PermissionDataError(ValueError):
    """a stored or posted permission value that isn't valid for its row."""


# ── values per kind ──────────────────────────────────────────────────────

def _open_value(row: Perm):
    if row.kind == BOOL:
        return True
    if row.kind == RESTRICT:
        return False
    if row.kind == MODE:
        return row.values[-1]
    if row.kind == RATING:
        return FOLLOW if row.id == "tv.max_rating" else "any"
    if row.kind == COUNT:
        return {"n": 0, "days": 7}
    if row.kind == SET:
        return ALL
    if row.kind == CHOICE:
        return None
    if row.kind == SET_FLAG:
        return []
    raise KeyError(row.id)


def _floor_value(row: Perm):
    if row.kind == BOOL:
        return False
    if row.kind == RESTRICT:
        return True
    if row.kind == MODE:
        return row.values[0]
    if row.kind == RATING:
        return "G"
    if row.kind == COUNT:
        return {"n": 1, "days": DAYS_MAX}
    if row.kind == SET:
        return []
    if row.kind == CHOICE:
        return None
    if row.kind == SET_FLAG:
        return []
    raise KeyError(row.id)


def _copy(v):
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, list):
        return list(v)
    return v


OPEN_POLICY: Dict[str, Any] = {pid: _open_value(CATALOGUE[pid]) for pid in STORED_IDS}
FLOOR: Dict[str, Any] = {pid: _floor_value(CATALOGUE[pid]) for pid in STORED_IDS}
TEMPLATE_DEFAULTS: Dict[str, Any] = {pid: CATALOGUE[pid].default for pid in STORED_IDS}


def admin_map() -> Dict[str, Any]:
    """what an admin has: every row at its most, no restriction."""
    out = {pid: _copy(_open_value(CATALOGUE[pid])) for pid in STORED_IDS}
    out["tv.max_rating"] = "any"
    out["requests.see_all"] = True
    out["requests.moderate"] = True
    return out


def floor_map() -> Dict[str, Any]:
    """the fail-closed map: nothing on, every restriction on."""
    return {pid: _copy(v) for pid, v in FLOOR.items()}


def _id_list(value, *, tokens: bool = False) -> List[Any]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise PermissionDataError("expected a list")
    out: List[Any] = []
    for v in value:
        if tokens and isinstance(v, str) and v in QUALITY_TOKENS:
            item: Any = v
        elif isinstance(v, bool):
            raise PermissionDataError("expected a profile id")
        else:
            try:
                item = int(v)
            except (TypeError, ValueError):
                raise PermissionDataError("expected a profile id") from None
            if item < 0:
                raise PermissionDataError("expected a profile id")
        if item not in out:
            out.append(item)
    return sorted(out, key=lambda x: (isinstance(x, str), x if not isinstance(x, str) else 0, str(x)))


def coerce(perm_id: str, value: Any) -> Any:
    """a clean value for this row, or PermissionDataError. what every layer
    is held to on the way in (api) and on the way out (storage)."""
    row = CATALOGUE.get(perm_id)
    if row is None:
        raise PermissionDataError("unknown permission %r" % perm_id)
    k = row.kind
    if not row.stored:
        raise PermissionDataError("%s isn't stored as a permission" % perm_id)
    if k in (BOOL, RESTRICT):
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        raise PermissionDataError("%s takes true or false" % perm_id)
    if k == MODE:
        if value in row.values:
            return value
        raise PermissionDataError("%s takes one of %s" % (perm_id, ", ".join(row.values)))
    if k == RATING:
        if value in RATINGS or (perm_id == "tv.max_rating" and value == FOLLOW):
            return value
        raise PermissionDataError("%s takes one of %s" % (perm_id, ", ".join(RATINGS)))
    if k == COUNT:
        if not isinstance(value, Mapping):
            raise PermissionDataError("%s takes {n, days}" % perm_id)
        try:
            n = int(value["n"]) if value.get("n") is not None else 0
            days = int(value["days"]) if value.get("days") is not None else 7
        except (TypeError, ValueError):
            raise PermissionDataError("%s takes whole numbers" % perm_id) from None
        if isinstance(value.get("n"), bool) or not (0 <= n <= COUNT_MAX) or not (1 <= days <= DAYS_MAX):
            raise PermissionDataError("%s: n 0..%d, days 1..%d" % (perm_id, COUNT_MAX, DAYS_MAX))
        return {"n": n, "days": days}
    if k == SET:
        if value == ALL:
            return ALL
        return _id_list(value)
    if k == CHOICE:
        if value is None:
            return None
        if isinstance(value, bool):
            raise PermissionDataError("%s takes a profile id or null" % perm_id)
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise PermissionDataError("%s takes a profile id or null" % perm_id) from None
        if v < 0:
            raise PermissionDataError("%s takes a profile id or null" % perm_id)
        return v
    if k == SET_FLAG:
        return _id_list(value, tokens=True)
    raise PermissionDataError(perm_id)  # pragma: no cover - every stored kind is above


def clean_layer(layer: Optional[Mapping[str, Any]], *, fill: Optional[Mapping[str, Any]] = None,
                keep_unknown: bool = False) -> Dict[str, Any]:
    """a layer with every value checked. unknown ids (a row retired later)
    are dropped unless keep_unknown; with ``fill`` every missing row is
    taken from it, so the result is a full map."""
    if layer is None:
        layer = {}
    if isinstance(layer, str):
        try:
            layer = json.loads(layer) if layer.strip() else {}
        except ValueError:
            raise PermissionDataError("unreadable permission map") from None
    if not isinstance(layer, Mapping):
        raise PermissionDataError("a permission map is an object")
    out: Dict[str, Any] = {}
    if OLD_LIMIT in layer:
        # the one shared limit sets each kind that doesn't name its own
        layer = dict(layer)
        old = layer.pop(OLD_LIMIT)
        for row in LIMIT_ROWS.values():
            layer.setdefault(row, old)
    for key, value in layer.items():
        if key not in CATALOGUE or not CATALOGUE[key].stored:
            if keep_unknown:
                out[key] = value
            continue
        out[key] = coerce(key, value)
    if fill is not None:
        for pid in STORED_IDS:
            if pid not in out:
                out[pid] = _copy(fill[pid])
    return out


# ── clamp ────────────────────────────────────────────────────────────────

def _rate(c: Mapping[str, int]) -> float:
    return float(c["n"]) / float(c["days"])


def clamp(perm_id: str, value: Any, ceiling: Any) -> Any:
    """one row's value held under the policy ceiling (see the kinds above)."""
    row = CATALOGUE[perm_id]
    k = row.kind
    if k == BOOL:
        return bool(value) and bool(ceiling)
    if k == RESTRICT:
        return bool(value) or bool(ceiling)
    if k == MODE:
        order = MODES
        return value if order.index(value) <= order.index(ceiling) else ceiling
    if k == RATING:
        return value if RATINGS.index(value) <= RATINGS.index(ceiling) else ceiling
    if k == COUNT:
        if not ceiling["n"]:
            return dict(value)
        if not value["n"] or _rate(value) > _rate(ceiling):
            return dict(ceiling)
        return dict(value)
    if k == SET:
        if ceiling == ALL:
            return _copy(value)
        if value == ALL:
            return list(ceiling)
        return [v for v in value if v in ceiling]
    if k == SET_FLAG:
        return _id_list(list(value) + list(ceiling), tokens=True)
    if k == CHOICE:
        return value
    raise KeyError(perm_id)  # pragma: no cover


def is_admin_row(profile: Optional[Mapping[str, Any]]) -> bool:
    if not profile:
        return False
    try:
        if int(profile.get("id") or 0) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return bool(profile.get("is_admin"))


def _resolve(policy: Dict[str, Any], base: Dict[str, Any], overrides: Dict[str, Any]):
    """(effective, raw) for full, clean maps."""
    raw = {pid: _copy(overrides[pid] if pid in overrides else base[pid]) for pid in STORED_IDS}
    eff: Dict[str, Any] = {}
    for pid in STORED_IDS:
        row = CATALOGUE[pid]
        if row.kind == CHOICE or pid == "tv.max_rating":
            continue
        eff[pid] = clamp(pid, raw[pid], policy[pid])
    # tv follows the movie rating (decision 5); the ceiling may follow too
    tv_raw = raw["tv.max_rating"]
    tv_ceiling = policy["tv.max_rating"]
    if tv_ceiling == FOLLOW:
        tv_ceiling = policy["movies.max_rating"]
    if tv_raw == FOLLOW:
        tv_raw = eff["movies.max_rating"]
    eff["tv.max_rating"] = clamp("tv.max_rating", tv_raw, tv_ceiling)
    # a default only counts inside what's allowed; else the policy's, else none
    for pid in STORED_IDS:
        row = CATALOGUE[pid]
        if row.kind != CHOICE:
            continue
        pick = raw[pid] if raw[pid] is not None else policy[pid]
        allowed = eff[row.set_row]
        eff[pid] = pick if pick is not None and (allowed == ALL or pick in allowed) else None
    return eff, raw


def effective(policy: Optional[Mapping[str, Any]], template: Optional[Mapping[str, Any]],
              overrides: Optional[Mapping[str, Any]] = None,
              profile: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """what a profile may do, every stored row. admins skip the layers.

    ``policy`` None is the open policy (nothing saved yet). ``template`` None
    is no template at all, which fails closed to the floor; a profile kept on
    its old columns passes ``template_from_legacy(profile)`` instead. a value
    that isn't valid for its row raises PermissionDataError: a caller that
    can't build the map must fail closed, never guess."""
    if is_admin_row(profile):
        return admin_map()
    pol = clean_layer(policy, fill=OPEN_POLICY)
    if template is None:
        return floor_map()
    base = clean_layer(template, fill=TEMPLATE_DEFAULTS)
    ov = clean_layer(overrides or {})
    eff, _raw = _resolve(pol, base, ov)
    return eff


def explain(policy: Optional[Mapping[str, Any]], template: Optional[Mapping[str, Any]],
            overrides: Optional[Mapping[str, Any]] = None,
            profile: Optional[Mapping[str, Any]] = None, *, base_source: str = "template") -> List[Dict[str, Any]]:
    """one row per stored permission with where its value comes from.

    source: admin | override (a personal change) | template | legacy (the
    profile still runs on its old columns) | floor (no template, closed).
    capped: the policy holds it below what the template or change asks for;
    an override that is capped is kept but does nothing (inert)."""
    pol = clean_layer(policy, fill=OPEN_POLICY)
    if is_admin_row(profile):
        eff = admin_map()
        return [{"id": pid, "value": eff[pid], "source": "admin", "template_value": None,
                 "override": False, "override_value": None, "ceiling": pol[pid], "capped": False,
                 "inert": False} for pid in STORED_IDS]
    ov = clean_layer(overrides or {})
    if template is None:
        eff = floor_map()
        return [{"id": pid, "value": eff[pid], "source": "floor", "template_value": None,
                 "override": pid in ov, "override_value": ov.get(pid), "ceiling": pol[pid],
                 "capped": False, "inert": pid in ov} for pid in STORED_IDS]
    base = clean_layer(template, fill=TEMPLATE_DEFAULTS)
    eff, raw = _resolve(pol, base, ov)
    out = []
    for pid in STORED_IDS:
        want = raw[pid]
        if pid == "tv.max_rating" and want == FOLLOW:
            want = eff["movies.max_rating"]
        capped = CATALOGUE[pid].kind != CHOICE and _canon(eff[pid]) != _canon(want)
        out.append({"id": pid, "value": eff[pid], "source": "override" if pid in ov else base_source,
                    "template_value": base[pid], "override": pid in ov,
                    "override_value": ov.get(pid), "ceiling": pol[pid], "capped": capped,
                    "inert": capped and pid in ov})
    return out


def _canon(v):
    return json.dumps(v, sort_keys=True)


def diff_overrides(template: Mapping[str, Any], wanted: Mapping[str, Any]) -> Dict[str, Any]:
    """the personal changes that turn a template into ``wanted``: only rows
    that differ are kept (decision 2: overrides only)."""
    base = clean_layer(template, fill=TEMPLATE_DEFAULTS)
    want = clean_layer(wanted)
    return {pid: want[pid] for pid in STORED_IDS if pid in want and _canon(want[pid]) != _canon(base[pid])}


def impact(before: Mapping[int, Mapping[str, Any]], after: Mapping[int, Mapping[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """per profile, the rows whose effective value moves: {pid: [{id, before, after}]}."""
    out: Dict[int, List[Dict[str, Any]]] = {}
    for pid, b in before.items():
        a = after.get(pid, {})
        moved = [{"id": k, "before": b.get(k), "after": a.get(k)} for k in STORED_IDS
                 if _canon(b.get(k)) != _canon(a.get(k))]
        if moved:
            out[pid] = moved
    return out


# ── the old columns: cache out, and the mapping in ───────────────────────

_PRESET_FIELDS = ("allowed_sides", "can_download", "hide_explicit", "max_rating")
LEGACY_GROUPS: Dict[str, Tuple[str, ...]] = {
    "allowed_sides": ("music.access", "movies.access", "tv.access"),
    "allowed_pages": tuple(r.id for r in PAGE_ROWS),
    "can_download": ("music.get", "movies.get", "tv.get", "books.get", "podcasts.get"),
    "hide_explicit": ("music.hide_explicit",),
    "max_rating": ("movies.max_rating", "tv.max_rating"),
    "request_limit": tuple(LIMIT_ROWS.values()),
}
# the request_limit group covers both columns
LEGACY_COLUMNS = ("allowed_sides", "allowed_pages", "can_download", "hide_explicit", "max_rating",
                  "request_limit", "request_limit_days")


def _norm_rating(raw) -> Optional[str]:
    """the old max_rating as a catalogue rating, or None when it has no
    equivalent (content_filter caps an unknown value at age 0)."""
    from core.content_filter import _CAPS, _norm
    if raw is None:
        return "any"
    s = _norm(raw)
    if not s:
        return "any"
    return s if s in _CAPS else None


def _sides(profile: Mapping[str, Any]) -> str:
    s = profile.get("allowed_sides")
    return s if s in ("music", "video", "both") else "music"


def template_from_legacy(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """the full map a profile's old columns mean. what a profile with no
    template (not mapped yet, or kept on its columns) runs on."""
    perms, _unmapped = perms_from_legacy(profile)
    return perms


def perms_from_legacy(profile: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """(full map, reasons) for a profile row as get_profile returns it.

    reasons lists what the old columns say that no catalogue value can (an
    unknown page id, a rating content_filter reads as 'nothing'). a profile
    with reasons is kept exactly as it is and flagged for a look."""
    reasons: List[str] = []
    perms = {pid: _copy(TEMPLATE_DEFAULTS[pid]) for pid in STORED_IDS}
    sides = _sides(profile)
    perms["music.access"] = sides in ("music", "both")
    perms["movies.access"] = perms["tv.access"] = sides in ("video", "both")
    dl = bool(profile.get("can_download", True))
    for pid in ("music.get", "movies.get", "tv.get"):
        perms[pid] = "download" if dl else "ask"
    perms["books.get"] = perms["podcasts.get"] = "download" if dl else "none"
    perms["music.hide_explicit"] = bool(profile.get("hide_explicit"))
    # no column says it. a profile that hides explicit music is a child's
    # (the Kids preset), and music apps start off for children
    perms["music.apps"] = not perms["music.hide_explicit"]
    rating = _norm_rating(profile.get("max_rating"))
    if rating is None:
        reasons.append("max_rating %r has no catalogue rating" % (profile.get("max_rating"),))
        rating = "G"
    perms["movies.max_rating"] = rating
    perms["tv.max_rating"] = FOLLOW
    try:
        n = int(profile.get("request_limit") or 0)
        days = int(profile.get("request_limit_days") or 7)
    except (TypeError, ValueError):
        reasons.append("request limit unreadable")
        n, days = 0, 7
    # music and video each counted against these columns on their own, so
    # every kind starts from them: nobody's music allowance shrinks, and
    # movies and TV each get what video had
    if n <= 0:
        lim = {"n": 0, "days": 7}
    else:
        if n > COUNT_MAX:
            reasons.append("request limit %d above %d" % (n, COUNT_MAX))
        lim = {"n": min(n, COUNT_MAX), "days": min(max(1, days), DAYS_MAX)}
        if days > DAYS_MAX:
            reasons.append("request window %d days above %d" % (days, DAYS_MAX))
    for row in LIMIT_ROWS.values():
        perms[row] = dict(lim)
    pages = profile.get("allowed_pages")
    if pages is None:
        for r in PAGE_ROWS:
            perms[r.id] = True
    elif isinstance(pages, list):
        have = {PAGE_ALIASES.get(str(p), str(p)) for p in pages}
        for extra in sorted(have - set(PAGE_OWNER) - UNGATED_PAGES):
            reasons.append("page %r isn't in the catalogue" % extra)
        for r in PAGE_ROWS:
            on = [pg in have for pg in r.pages]
            if any(on) and not all(on):
                reasons.append("only some pages of %s" % r.id)
            perms[r.id] = all(on)
    else:
        reasons.append("allowed_pages isn't a list")
        for r in PAGE_ROWS:
            perms[r.id] = False
    return perms, reasons


def legacy_from_perms(eff: Mapping[str, Any]) -> Dict[str, Any]:
    """the old columns for an effective map (the written-through cache).
    allowed_pages is None when every page row is on, as the editor writes it."""
    music = bool(eff.get("music.access"))
    video = bool(eff.get("movies.access")) or bool(eff.get("tv.access"))
    sides = "both" if music and video else "video" if video else "music"
    if all(eff.get(r.id) for r in PAGE_ROWS):
        pages = None
    else:
        pages = [pg for r in PAGE_ROWS if eff.get(r.id) for pg in r.pages]
    rating = eff.get("movies.max_rating") or "any"
    # the columns hold one limit: they cache the music one (the column was
    # music's before video had requests)
    limit = limit_of(eff, "music")
    return {
        "allowed_sides": sides,
        "allowed_pages": pages,
        "can_download": eff.get("music.get") == "download",
        "hide_explicit": bool(eff.get("music.hide_explicit")),
        "max_rating": None if rating == "any" else rating,
        "request_limit": int(limit["n"]),
        "request_limit_days": int(limit["days"]),
    }


def legacy_behaviour(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """what the old columns make the app do, normalised so two rows that
    behave the same compare equal (NULL pages = every page, aliases, an
    unlimited quota's window, ratings read the way content_filter reads them)."""
    from core.content_filter import cap_age
    pages = profile.get("allowed_pages")
    universe = frozenset(PAGE_OWNER)
    if pages is None:
        page_set = universe
    elif isinstance(pages, list):
        page_set = frozenset(PAGE_ALIASES.get(str(p), str(p)) for p in pages) & universe
    else:
        page_set = frozenset()
    try:
        n = int(profile.get("request_limit") or 0)
        days = max(1, int(profile.get("request_limit_days") or 7))
    except (TypeError, ValueError):
        n, days = 0, 7
    return {
        "allowed_sides": _sides(profile),
        "pages": page_set,
        "can_download": bool(profile.get("can_download", True)),
        "hide_explicit": bool(profile.get("hide_explicit")),
        "cap": cap_age(profile.get("max_rating")),
        "quota": (n, days) if n > 0 else None,
    }


def cache_matches(profile: Mapping[str, Any], eff: Mapping[str, Any], *, strict: bool = False) -> List[str]:
    """the old-column fields where ``eff`` would make the app behave
    differently from ``profile``'s columns today. empty = no change.

    strict (the migration's verify step) also holds the per-type rows the
    cache can't hold to the one switch they came from: every get mode to
    can_download, tv's rating to max_rating. after the migration a split
    (movies download, music ask) is real and only can() sees it."""
    old = legacy_behaviour(profile)
    new = legacy_behaviour(dict(profile, **legacy_from_perms(eff)))
    out = [k for k in old if old[k] != new[k]]
    if not strict:
        return out
    dl = old["can_download"]
    for pid in ("music.get", "movies.get", "tv.get"):
        if (eff.get(pid) == "download") != dl:
            out.append(pid)
    for pid in ("books.get", "podcasts.get"):
        if (eff.get(pid) == "download") != dl:
            out.append(pid)
    for kind in ("movies", "tv"):
        lim = limit_of(eff, kind)
        if ((lim["n"], lim["days"]) if lim["n"] > 0 else None) != old["quota"]:
            out.append(LIMIT_ROWS[kind])
    from core.content_filter import cap_age
    tv = eff.get("tv.max_rating")
    if cap_age(None if tv in (None, "any") else tv) != old["cap"]:
        out.append("tv.max_rating")
    return sorted(set(out))


def _preset_key(perms: Mapping[str, Any]) -> Tuple:
    return (perms["music.access"], perms["movies.access"], perms["tv.access"], perms["music.get"],
            perms["music.hide_explicit"], perms["movies.max_rating"])


def match_template(profile: Mapping[str, Any], templates: Iterable[Mapping[str, Any]],
                   policy: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """the migration's step 3 and 4 for one non-admin profile.

    templates: [{id, slug, perms}] in preference order (adult first). picks
    the template whose four preset fields match (sides, downloads, clean
    music, rating; the same test as init.js pfPresetOf), else the one with
    the fewest differences (ties to the first), stores every other
    difference as a personal change, then recomputes effective and compares
    it with the old columns. returns {template_id, overrides, ok, reasons}:
    ok False means keep the profile on its columns and flag it."""
    legacy, reasons = perms_from_legacy(profile)
    tpls = [t for t in templates if t.get("perms") is not None]
    if not tpls:
        return {"template_id": None, "overrides": {}, "ok": False, "reasons": reasons + ["no templates"]}
    want_key = _preset_key(legacy)
    best = None
    for order, t in enumerate(tpls):
        try:
            base = clean_layer(t["perms"], fill=TEMPLATE_DEFAULTS)
        except PermissionDataError:
            continue
        ov = diff_overrides(base, legacy)
        rank = (0 if _preset_key(base) == want_key else 1, len(ov), order)
        if best is None or rank < best[0]:
            best = (rank, t, base, ov)
    if best is None:
        return {"template_id": None, "overrides": {}, "ok": False, "reasons": reasons + ["no readable template"]}
    _rank, t, base, ov = best
    try:
        eff = effective(policy, base, ov, profile)
    except PermissionDataError as exc:
        return {"template_id": None, "overrides": {}, "ok": False, "reasons": reasons + [str(exc)]}
    mismatch = cache_matches(profile, eff, strict=True)
    if mismatch:
        reasons = reasons + ["effective differs on %s" % ", ".join(mismatch)]
    ok = not reasons
    return {"template_id": t.get("id") if ok else None, "overrides": ov if ok else {}, "ok": ok,
            "reasons": reasons, "matched": t.get("slug") or t.get("name")}


# the four built-ins, from webui/static/init.js PROFILE_PRESETS. every page
# and part on, no limit. ask-first templates can't ask for books or podcast
# downloads (no books request flow yet, decision 7), as today.
def _builtin(sides: str, dl: bool, clean: bool, rating: str) -> Dict[str, Any]:
    perms, _ = perms_from_legacy({"allowed_sides": sides, "can_download": dl, "hide_explicit": clean,
                                  "max_rating": None if rating == "any" else rating,
                                  "allowed_pages": None, "request_limit": 0})
    return perms


BUILTIN_TEMPLATES: Tuple[Dict[str, Any], ...] = (
    {"slug": "adult", "name": "Adult", "description": "Music, movies and shows. Gets things right away.",
     "perms": _builtin("both", True, False, "any")},
    {"slug": "teen", "name": "Teen", "description": "Movies up to PG-13, asks before downloading.",
     "perms": _builtin("both", False, False, "PG-13")},
    # music apps start off here (perms_from_legacy: clean music is a child's)
    {"slug": "kids", "name": "Kids", "description": "Clean music, movies up to PG, asks for everything.",
     "perms": _builtin("both", False, True, "PG")},
    {"slug": "guest", "name": "Guest", "description": "Listens to music, nothing else.",
     "perms": _builtin("music", False, False, "any")},
)


def signup_overrides(access, guest_perms: Mapping[str, Any]) -> Dict[str, Any]:
    """the personal changes that make a profile following ``guest_perms``
    match what sign-up gives today (core.security.signup_defaults
    SignupAccess). for piece A/D when sign-up moves to templates."""
    perms, _ = perms_from_legacy({"allowed_sides": access.allowed_sides, "can_download": False,
                                  "allowed_pages": list(access.allowed_pages),
                                  "request_limit": access.request_limit,
                                  "request_limit_days": access.request_limit_days})
    return diff_overrides(guest_perms, perms)


# ── the check ────────────────────────────────────────────────────────────

def make_context(profile_id=None, *, is_admin: bool = False, perms: Optional[Mapping[str, Any]] = None,
                 source: str = "template") -> Dict[str, Any]:
    """the profile_ctx can() reads. perms None (couldn't build) answers no."""
    return {"profile_id": profile_id, "is_admin": bool(is_admin),
            "perms": dict(perms) if perms is not None else None, "source": source}


def context_from_g() -> Dict[str, Any]:
    """the current request's context, from what _set_profile_context put on g."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return make_context(None, perms=None, source="none")
        return make_context(getattr(g, "profile_id", None), is_admin=bool(getattr(g, "is_admin", False)),
                            perms=getattr(g, "perms", None), source=getattr(g, "perms_source", "none"))
    except Exception:  # noqa: BLE001 - no context reads as no rights
        return make_context(None, perms=None, source="none")


def can(profile_ctx: Optional[Mapping[str, Any]], permission: str, context: Any = None) -> bool:
    """may this profile do it? admins always may.

    profile_ctx: make_context(...) (or None for the current request).
    context, per kind:
      bool       ignored
      restrict   ignored; True means the restriction is ON (hide explicit)
      mode       the least mode wanted ("ask" by default: may request at all;
                 "download" = may get it right away)
      rating     a title's rating; True when it may be shown (no rating given:
                 True, the row alone never refuses)
      count      requests used so far; True while under the limit
      set        a quality profile id; True when allowed (none given: any allowed)
      choice     ignored (read the map for the value)
      set_flag   an id; True when that id always needs approval
      fixed      always True
    presentation rows raise ValueError: they never grant.
    requests.limit (the old shared limit) is held to every kind's."""
    if permission == OLD_LIMIT:
        return all(can(profile_ctx, row, context) for row in LIMIT_ROWS.values())
    row = CATALOGUE.get(permission)
    if row is None:
        logger.error("can(): unknown permission %r, refusing", permission)
        return False
    if row.kind == PRESENTATION:
        raise ValueError("%s is presentation only and never grants" % permission)
    if row.kind == FIXED:
        return bool(row.default) or bool((profile_ctx or context_from_g()).get("is_admin"))
    ctx = profile_ctx if profile_ctx is not None else context_from_g()
    if row.kind == ADMIN:
        return bool(ctx.get("is_admin"))
    if ctx.get("is_admin"):
        return row.kind != RESTRICT and row.kind != SET_FLAG
    perms = ctx.get("perms")
    if not isinstance(perms, Mapping) or permission not in perms:
        return False if row.kind != RESTRICT else True
    value = perms[permission]
    k = row.kind
    # a row that needs a side ("turn on X first") does nothing while that
    # side is off: a video-only member with music.get "download" left over
    # from its old columns still can't get music. restrictions and ratings
    # keep their own answer (a restriction never loosens with its side).
    if k in (BOOL, MODE, SET, CHOICE) and row.needs and not any(perms.get(n) is True for n in row.needs):
        return False
    try:
        if k == BOOL:
            return bool(value)
        if k == RESTRICT:
            return bool(value)
        if k == MODE:
            want = context or "ask"
            return MODES.index(value) >= MODES.index(want) and value != "none"
        if k == RATING:
            if context is None:
                return True
            from core.content_filter import cap_age, title_allowed
            v = perms.get("movies.max_rating") if value == FOLLOW else value
            return title_allowed(context, cap_age(None if v == "any" else v))
        if k == COUNT:
            if context is None or not value.get("n"):
                return True
            return int(context) < int(value["n"])
        if k == SET:
            if context is None:
                return value == ALL or bool(value)
            return value == ALL or _same_quality(context, value)
        if k == CHOICE:
            return value is not None
        if k == SET_FLAG:
            return context is not None and _same_quality(context, value)
    except (TypeError, ValueError, KeyError):
        return False
    return False


def _same_quality(wanted, ids) -> bool:
    try:
        w = int(wanted)
    except (TypeError, ValueError):
        return wanted in ids
    return w in ids


def may_moderate(profile_ctx: Optional[Mapping[str, Any]], owner_profile_id) -> bool:
    """may this caller approve, decline or manage a request (or issue) that
    belongs to ``owner_profile_id``? admins: any. a moderator: anyone's but
    their own, since approving your own ask is downloading without the
    right. what an approval may then fetch stays inside the global policy
    (the approve route resolves quality for the requester, not the moderator)."""
    ctx = profile_ctx if profile_ctx is not None else context_from_g()
    if ctx.get("is_admin"):
        return True
    if not can(ctx, "requests.moderate"):
        return False
    try:
        return owner_profile_id is None or int(owner_profile_id) != int(ctx.get("profile_id"))
    except (TypeError, ValueError):
        return False


def may_see_request(profile_ctx: Optional[Mapping[str, Any]], owner_profile_id) -> bool:
    """own requests always; everyone's with requests.see_all, moderators, admins."""
    ctx = profile_ctx if profile_ctx is not None else context_from_g()
    if ctx.get("is_admin") or can(ctx, "requests.see_all") or can(ctx, "requests.moderate"):
        return True
    try:
        return owner_profile_id is not None and int(owner_profile_id) == int(ctx.get("profile_id"))
    except (TypeError, ValueError):
        return False


NOT_ALLOWED = "not_allowed"


# a get row's label names the setting ("Movie requests"); a refusal says
# what the member can't do (#2192)
REFUSAL_LABELS = {"music.get": "Getting music", "movies.get": "Getting movies", "tv.get": "Getting shows",
                  "books.get": "Getting books", "podcasts.get": "Getting episodes"}


def refusal_body(permission: str) -> Dict[str, Any]:
    """the standard calm refusal. the ui shows ``message`` as an empty
    state, never a 403 toast."""
    row = CATALOGUE.get(permission)
    label = REFUSAL_LABELS.get(permission) or (row.label if row else "That")
    return {"success": False, "error": NOT_ALLOWED, "permission": permission,
            "message": "%s isn't part of your profile. Ask an admin if you'd like it." % label}


REFUSAL_STATUS = 403


def refusal(permission: str):
    """(response, 403) for a refused permission."""
    from flask import jsonify
    return jsonify(refusal_body(permission)), REFUSAL_STATUS


# ── route declarations (the route inventory reads these) ─────────────────

ACCESS_ATTR = "_soulsync_access"


def _mark(fn, value):
    try:
        setattr(fn, ACCESS_ATTR, value)
    except (AttributeError, TypeError):  # pragma: no cover - builtins
        pass
    return fn


def requires(permission: str, value: Any = None):
    """route decorator: admins pass, anyone else needs ``can(perm, value)``,
    else the standard refusal. unknown or presentation permissions fail at
    import, not at request time."""
    row = CATALOGUE.get(permission)
    if row is None:
        raise ValueError("requires(): unknown permission %r" % permission)
    if row.kind == PRESENTATION:
        raise ValueError("requires(): %s is presentation only and never grants" % permission)

    def deco(view_fn):
        @functools.wraps(view_fn)
        def wrapper(*args, **kwargs):
            if enforcing() and not can(None, permission, value):
                return refusal(permission)
            return view_fn(*args, **kwargs)
        return _mark(wrapper, ("perm", permission))
    return deco


def requires_any(*permissions: str):
    """route decorator like requires(), but any one of these rows lets the
    caller in, and the route narrows what each one reaches. the automations
    routes serve both "Their own automations" and the sync page's auto-sync
    schedules, which follow Playlist sync. refuses with the first row."""
    if not permissions:
        raise ValueError("requires_any(): name at least one permission")
    for permission in permissions:
        row = CATALOGUE.get(permission)
        if row is None:
            raise ValueError("requires_any(): unknown permission %r" % permission)
        if row.kind == PRESENTATION:
            raise ValueError("requires_any(): %s is presentation only and never grants" % permission)

    def deco(view_fn):
        @functools.wraps(view_fn)
        def wrapper(*args, **kwargs):
            if enforcing() and not any(can(None, p) for p in permissions):
                return refusal(permissions[0])
            return view_fn(*args, **kwargs)
        return _mark(wrapper, ("perm_any", tuple(permissions)))
    return deco


# web_server sets this on its app next to the profile gate
# (_set_profile_context). a request in that app is always held to the
# declarations, and one with no profile context there is no one, so it is
# refused. a bare Flask app that mounts a blueprint (unit tests) has no
# profiles at all: there the declarations are only read by the inventory,
# unless the request carries a permission map (g.perms) of its own.
GATE_FLAG = "SOULSYNC_PROFILE_GATE"


def enforcing() -> bool:
    """are permission checks live for this request? always in the real app
    (it runs the profile gate); in an app without one, only when the request
    carries a permission map. outside a request: yes."""
    try:
        from flask import current_app, g, has_request_context
        if not has_request_context():
            return True
        if current_app.config.get(GATE_FLAG):
            return True
        return "perms" in g
    except Exception:  # noqa: BLE001 - can't tell, so check
        return True


def restricted(permission: str) -> bool:
    """a restriction row (music.hide_explicit) for the current request, with
    the same rule as requires(): an app without the profile gate and no map
    on the request has no profiles, so nothing is restricted there."""
    if not enforcing():
        return False
    return can(None, permission)


def map_refuses(permission: str, value: Any = None) -> bool:
    """True when the current request carries a permission map (g.perms, set
    by the profile gate for every signed-in profile) and that map says no.
    no map, no opinion: the caller's own checks answer (an api key acts as
    the admin; a helper unit-tested without the gate keeps its old rule). a
    map that couldn't be built (None) says no."""
    try:
        from flask import g, has_request_context
        if not has_request_context() or "perms" not in g:
            return False
    except Exception:  # noqa: BLE001
        return False
    return not can(None, permission, value)


def public(view_fn):
    """declares a route open to anyone (sign-in, the shell, health). marks
    only; it changes nothing about the route."""
    return _mark(view_fn, ("public", None))


def self_only(view_fn):
    """declares a route that only ever reads or writes the caller's own
    profile (/api/profiles/me/*). marks only; the route still checks."""
    return _mark(view_fn, ("self", None))


def own_or_admin(view_fn):
    """declares a route about one profile that the profile itself or an
    admin may call (pin, password, recovery, its own row). marks only; the
    route still checks (may_manage_profile)."""
    return _mark(view_fn, ("own_or_admin", None))


def signed_in(view_fn):
    """declares a route any signed-in profile may call whatever its
    permissions: the shell around every page (weather, toasts, the image
    cache). marks only; the session gates decide who is signed in, and the
    route keeps to the caller's own data."""
    return _mark(view_fn, ("signed_in", None))


def api_key_only(view_fn):
    """declares a route only an api key reaches (/api/v1, the lidarr shim,
    mcp). marks only; the key check is the route's (api.auth.require_api_key)."""
    return _mark(view_fn, ("api_key", None))


def device_only(view_fn):
    """declares a route only a paired device calls (its pick, who it is).
    marks only; the device gate (core.connect.device_access) and the route
    check the device token."""
    return _mark(view_fn, ("device", None))


def declared_access(view_fn) -> Optional[Tuple[str, Any]]:
    """what a view declares: ("perm", id) | ("perm_any", (ids)) | ("admin", None)
    | ("self", None) | ("public", None), looking through functools.wraps
    layers, else None."""
    seen = 0
    fn = view_fn
    while fn is not None and seen < 20:
        mark = getattr(fn, ACCESS_ATTR, None)
        if mark:
            return mark
        fn = getattr(fn, "__wrapped__", None)
        seen += 1
    return None


# ── request quality (#2168) ──────────────────────────────────────────────

@dataclass(frozen=True)
class QualityDecision:
    profile_id: Optional[int]      # the quality profile the request gets (None = the normal default)
    needs_approval: bool           # lands as a request for an admin, even for a downloader
    refused: bool = False
    error: Optional[str] = None    # "quality_not_allowed" when refused
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"profile_id": self.profile_id, "needs_approval": self.needs_approval,
                "refused": self.refused, "error": self.error, "note": self.note}


QUALITY_NOT_ALLOWED = "quality_not_allowed"
_MEDIA_ROWS = {"music": "music", "movie": "movies", "movies": "movies", "tv": "tv", "show": "tv",
               "episode": "tv", "season": "tv", "series": "tv"}
# what a quality profile's media_type must be (or None, both) for each kind
_MEDIA_TYPE_FOR = {"movies": "movie", "tv": "tv"}


def media_row(media_type) -> Optional[str]:
    """'music' | 'movies' | 'tv' for anything naming one (movie, show,
    episode, season...), else None."""
    return _MEDIA_ROWS.get(str(media_type or "").strip().lower())


def _qid(value) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _option_list(options, kind: str) -> Optional[List[Dict[str, Any]]]:
    """the quality profiles that exist for this kind: [{id, media_type, tags}],
    in the order given. None when the caller passed none (ids then can't be
    told apart from unknown ones, every id in the allowed set counts)."""
    if options is None:
        return None
    want = _MEDIA_TYPE_FOR.get(kind)
    out = []
    for o in options:
        if not isinstance(o, Mapping):
            continue
        oid = _qid(o.get("id"))
        if oid is None:
            continue
        out.append({"id": oid, "media_type": o.get("media_type") or None,
                    "tags": [str(t).lower() for t in (o.get("tags") or [])],
                    "fits": want is None or (o.get("media_type") or None) in (None, want)})
    return out


def _map_of(profile, perms) -> Optional[Mapping[str, Any]]:
    """the effective map to judge by: the one passed (a map, or a context
    with a "perms" key), else the profile row's own columns (a caller that
    passed nothing, for a legacy row). None = can't be built."""
    if perms is not None:
        if isinstance(perms, Mapping) and "perms" in perms and "profile_id" in perms:
            inner = perms.get("perms")
            return inner if isinstance(inner, Mapping) else None
        return perms if isinstance(perms, Mapping) else None
    if isinstance(profile, Mapping) and "perms" in profile and "profile_id" in profile:
        inner = profile.get("perms")
        return inner if isinstance(inner, Mapping) else None
    if isinstance(profile, Mapping) and profile:
        try:
            return perms_from_legacy(profile)[0]
        except Exception:  # noqa: BLE001 - an unreadable row decides nothing
            return None
    return None


def _is_admin_input(profile, perms) -> bool:
    for src in (profile, perms):
        if isinstance(src, Mapping) and "profile_id" in src and "perms" in src:
            if src.get("is_admin"):
                return True
    return is_admin_row(profile) if isinstance(profile, Mapping) and "profile_id" not in profile else False


def resolve_request_quality(profile: Optional[Mapping[str, Any]], media_type: str, requested_id,
                            *, perms: Optional[Mapping[str, Any]] = None, options=None,
                            kind_default: Optional[int] = None) -> QualityDecision:
    """the quality a request gets, for every request path (the dialog, the
    api, wishlist and watchlist adds, manual grabs, seerr, automations and
    import lists). one function, so every path holds the same line.

    profile: the profile row, or a context (make_context / context_for).
    perms: its effective map (or a context); leave it out only for a row on
      its own columns, which is read from them. a member whose map can't be
      built is refused.
    options: the quality profiles that exist, [{id, media_type, tags}]
      (core.video.quality_profile.quality_options for video). with them an
      id of the wrong media type is refused and an id nobody has heard of
      (a seerr client sending radarr ids) gets the default; without them
      every id in the allowed set counts.
    kind_default: the household's default for this kind, used when the
      person has no default of their own and it sits in their set.

    the rules, for movies and tv (music has no "needs my OK" row):
      admins: what they asked for (None = the normal default), never refused,
        never asked. an unknown id with options given is the normal default.
      no access to the kind, or "can't request": refused (not_allowed).
      the allowed set: {kind}.quality ("*" = every profile for this kind)
        intersected with the profiles whose media type fits (movie or tv, or
        both). nothing left: refused (quality_not_allowed).
      nothing asked: the person's default ({kind}.quality_default when it's in
        the set), else the household kind default when it's in the set, else
        the first allowed. an open set ("*") with no default of its own is
        None: the normal default (title, library, kind default), as today.
      asked for an allowed id: that id.
      asked for a known id outside the set, or of the other media type:
        refused (quality_not_allowed). the dialog never sends one, so only a
        tampered call or a stale page gets here.
      asked for an unknown id: the default, with a note (logged).
      needs_approval: the person's mode is "ask", or the id is in
        {kind}.quality_ask, or "4k" is there and the profile is tagged 4k.
        a "download right away" member with an ask-first pick is asked,
        which every path turns into a request."""
    kind = media_row(media_type)
    if kind is None:
        raise ValueError("media_type is music, movie or tv")
    wanted = _qid(requested_id)
    opts = _option_list(options, kind)
    known = {o["id"]: o for o in opts} if opts is not None else None

    if _is_admin_input(profile, perms):
        if wanted is not None and known is not None and wanted not in known:
            logger.info("quality %s isn't a %s profile here, using the default", wanted, kind)
            return QualityDecision(None, False, note="unknown quality, used the default")
        return QualityDecision(wanted, False)

    eff = _map_of(profile, perms)
    if eff is None:
        return QualityDecision(None, False, refused=True, error=NOT_ALLOWED,
                               note="permissions unavailable")
    if not eff.get("%s.access" % kind):
        return QualityDecision(None, False, refused=True, error=NOT_ALLOWED,
                               note="no %s access" % kind)
    mode = eff.get("%s.get" % kind, "none")
    if mode not in MODES or mode == "none":
        return QualityDecision(None, False, refused=True, error=NOT_ALLOWED,
                               note="%s can't be requested" % kind)

    raw_set = eff.get("%s.quality" % kind, ALL)
    open_set = raw_set == ALL
    listed = [] if open_set else [i for i in (_qid(v) for v in (raw_set or [])) if i is not None]
    if known is not None:
        fitting = [o["id"] for o in opts if o["fits"]]
        allowed = fitting if open_set else [i for i in fitting if i in listed]
    else:
        allowed = None if open_set else listed
    if allowed is not None and not allowed:
        return QualityDecision(None, False, refused=True, error=QUALITY_NOT_ALLOWED,
                               note="no %s quality is allowed" % kind)

    def _ok(i) -> bool:
        return allowed is None or i in allowed

    own_default = _qid(eff.get("%s.quality_default" % kind))
    if own_default is not None and _ok(own_default) and (known is None or own_default in known):
        default = own_default
    elif open_set:
        default = None
    elif kind_default is not None and _ok(_qid(kind_default)):
        default = _qid(kind_default)
    else:
        default = allowed[0] if allowed else None

    note = ""
    if wanted is None:
        chosen = default
    elif known is not None and wanted not in known:
        logger.info("quality %s isn't a %s profile here, using the default", wanted, kind)
        chosen, note = default, "unknown quality, used the default"
    elif known is not None and not known[wanted]["fits"]:
        return QualityDecision(None, False, refused=True, error=QUALITY_NOT_ALLOWED,
                               note="quality %s is for the other kind" % wanted)
    elif not _ok(wanted):
        return QualityDecision(None, False, refused=True, error=QUALITY_NOT_ALLOWED,
                               note="quality %s isn't in the allowed set" % wanted)
    else:
        chosen = wanted

    asks = mode != "download" or _needs_ok(eff, kind, chosen, opts, kind_default)
    return QualityDecision(chosen, asks, note=note)


def needs_ok(eff: Optional[Mapping[str, Any]], media_type: str, quality_id, *, options=None,
             kind_default: Optional[int] = None) -> bool:
    """does this quality always need an OK for this person ({kind}.quality_ask:
    the id itself, or "4k" and a profile tagged 4k)? quality None is the
    normal default, judged as the household kind default when one is given."""
    kind = media_row(media_type)
    if kind is None:
        return False
    return _needs_ok(eff, kind, quality_id, _option_list(options, kind), kind_default)


def _needs_ok(eff, kind: str, quality_id, opts, kind_default) -> bool:
    if not isinstance(eff, Mapping):
        return False
    flags = eff.get("%s.quality_ask" % kind) or []
    if not isinstance(flags, (list, tuple)) or not flags:
        return False
    qid = _qid(quality_id)
    if qid is None:
        qid = _qid(kind_default)
    if qid is None:
        return False
    if _same_quality(qid, [f for f in flags if not isinstance(f, str)]):
        return True
    return "4k" in flags and any(o["id"] == qid and "4k" in o["tags"] for o in (opts or []))


# ── "what they can do" ───────────────────────────────────────────────────

def summarize(eff: Mapping[str, Any], *, is_admin: bool = False, name: str = "They") -> Dict[str, Any]:
    """a short plain-words summary of an effective map, by category, built
    from the map itself so it is the truth, not a restatement of the form."""
    if is_admin:
        return {"sentence": "%s can use everything, including settings and people." % name,
                "lines": [], "never": []}
    lines: List[Dict[str, str]] = []
    you = name == "You"

    def mode_words(m):
        if you:
            return {"download": "you get them right away", "ask": "you ask first",
                    "none": "you can't request them"}[m]
        return {"download": "gets it right away", "ask": "asks first", "none": "can't request"}[m]

    def asks_bit(kind):
        # "5 asks a week" on the kind's own line, when that kind asks first
        lim = limit_of(eff, kind)
        if not lim["n"] or eff.get("%s.get" % kind) != "ask":
            return []
        return [limit_words(lim, kind)]

    def second_person(label):
        # "Their own automations" reads "Your own automations" to the member
        if not you:
            return label
        return label.replace("Their ", "Your ").replace(" their ", " your ")

    if eff.get("movies.access"):
        bits = ["Movies: %s" % mode_words(eff["movies.get"])]
        if eff.get("movies.max_rating") not in (None, "any"):
            bits.append("up to %s" % eff["movies.max_rating"])
        bits += asks_bit("movies")
        lines.append({"category": "movies", "text": ", ".join(bits) + "."})
    if eff.get("tv.access"):
        bits = ["TV shows: %s" % mode_words(eff["tv.get"])]
        tv = eff.get("tv.max_rating")
        if tv not in (None, "any"):
            bits.append("up to %s" % TV_RATING_LABELS.get(tv, tv))
        bits += asks_bit("tv")
        lines.append({"category": "tv", "text": ", ".join(bits) + "."})
    if eff.get("music.access"):
        bits = ["Music: %s" % mode_words(eff["music.get"])]
        if eff.get("music.hide_explicit"):
            bits.append("clean only")
        bits += asks_bit("music")
        lines.append({"category": "music", "text": ", ".join(bits) + "."})
    if eff.get("books.access"):
        lines.append({"category": "books", "text": "Audiobooks: %s." % mode_words(eff["books.get"])})
    if eff.get("podcasts.access"):
        pod = eff.get("podcasts.get")
        lines.append({"category": "books", "text": "Podcasts: %s." % (
            "new episodes download on their own" if pod == "download"
            else ("%s ask for episodes, new ones don't download on their own" % ("you" if you else "they"))
            if pod == "ask"
            else "%s follow shows, episodes don't download" % ("you" if you else "they"))})
    never = [second_person(CATALOGUE[pid].label) for pid in STORED_IDS
             if CATALOGUE[pid].kind == BOOL and not eff.get(pid) and not CATALOGUE[pid].group
             and pid not in ("requests.see_all", "requests.moderate")]
    sides = [c for c, on in (("music", eff.get("music.access")), ("movies", eff.get("movies.access")),
                             ("TV shows", eff.get("tv.access"))) if on]
    verb = "use" if name in ("You", "They") else "uses"
    if len(sides) > 2:
        what = ", ".join(sides[:-1]) + " and " + sides[-1]
    else:
        what = " and ".join(sides) if sides else "nothing yet"
    sentence = "%s %s %s." % (name, verb, what)
    return {"sentence": sentence, "lines": lines, "never": never}


def catalogue_payload() -> Dict[str, Any]:
    """GET /api/permissions/catalogue: categories in rail order, each with
    its rows. plain data, no lookups."""
    rows = []
    for r in CATALOGUE_ROWS:
        rows.append({
            "id": r.id, "kind": r.kind, "category": r.category, "label": r.label, "help": r.help,
            "default": r.default, "values": list(r.values), "pages": list(r.pages),
            "scope": r.scope, "group": r.group, "also_in": list(r.also_in), "needs": list(r.needs),
            "set_row": r.set_row or None, "media": r.media or None, "presentation": r.presentation,
            "stored": r.stored, "legacy": r.legacy or None,
            "value_labels": (ROW_VALUE_LABELS.get(r.id) or MODE_LABELS if r.kind == MODE else
                             (TV_RATING_LABELS if r.id == "tv.max_rating" else {})),
        })
    return {"categories": CATEGORIES, "rows": rows, "kinds": list(KINDS), "modes": list(MODES),
            "ratings": list(RATINGS), "follow": FOLLOW, "all": ALL, "quality_tokens": list(QUALITY_TOKENS),
            "open_policy": OPEN_POLICY}


__all__ = ["profile_can_download", "owner_may_get", "owner_mode", "download_denied_reason", "DOWNLOADS_OFF", "NO_PROFILE",
           "PROFILE_UNREADABLE", "strict_profile_getter",
           "may_manage_profile", "may_cancel_download", "profile_view_for", "profiles_listed_for",
           "PUBLIC_PROFILE_FIELDS",
           "PAGE_EXCLUSIVE_APIS", "page_for_path", "page_denied",
           # people and permissions
           "Perm", "CATALOGUE", "CATALOGUE_ROWS", "CATEGORIES", "CATEGORY_IDS", "STORED_IDS",
           "PRESENTATION_IDS", "PAGE_ROWS", "PAGE_OWNER", "KINDS", "MODES", "RATINGS", "FOLLOW", "ALL",
           "OPEN_POLICY", "FLOOR", "TEMPLATE_DEFAULTS", "BUILTIN_TEMPLATES", "LEGACY_GROUPS",
           "LEGACY_COLUMNS", "PERSONAL_AUTOMATION_ACTIONS", "PermissionDataError", "coerce",
           "clean_layer", "clamp", "effective", "explain", "diff_overrides", "impact", "admin_map",
           "floor_map", "perms_from_legacy", "template_from_legacy", "legacy_from_perms",
           "legacy_behaviour", "cache_matches", "match_template", "signup_overrides", "make_context",
           "context_from_g", "can", "may_moderate", "may_see_request", "refusal", "refusal_body",
           "REFUSAL_STATUS", "NOT_ALLOWED", "requires", "requires_any", "public", "self_only", "device_only",
           "declared_access",
           "ACCESS_ATTR", "QualityDecision", "QUALITY_NOT_ALLOWED", "resolve_request_quality",
           "summarize", "catalogue_payload", "is_admin_row"]
