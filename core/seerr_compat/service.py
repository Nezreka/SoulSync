"""the seerr compatible api's logic (#2151). the blueprint parses requests and
hands everything here; every outside piece (tmdb engine, video db, profiles,
jellyfin users, the kids check, permissions) comes in through the constructor
so tests can stand them in.

who a call acts for: the profile ``X-Api-User`` names. a missing or unknown
user acts as the fallback profile from settings, or is refused when settings
say so (new installs refuse, #2166 decision 12). every route then asks the
acting profile's permissions (core.permissions.can), the same as soulsync's
own video pages:
- movies and tv are separate: a tv-only profile never sees or asks for movies.
- "Download right away" gets it straight away: a movie goes on its video
  wishlist, a show's aired episodes of the asked seasons go on it, and asking
  for the newest season also follows the show so new episodes come.
- "Ask first" (or a quality that always needs an OK) files a video request,
  which waits on the Requests page like any other, inside the request limit.
- "Can't request" is seerr's own 403.
- the rating cap per type holds on every list, detail and request.
- the quality comes from resolve_request_quality: overseerr's profileId is
  mapped, is4k picks the person's allowed 4K profile.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from core import permissions as P
from core.seerr_compat import shapes
from utils.logging_config import get_logger

logger = get_logger("seerr_compat.service")

_SORT = re.compile(r"^[a-z_]+\.(asc|desc)$")
# seerr's request list filters -> which soulsync requests they mean
_FILTERS = {
    "pending": ("pending", None),
    "approved": ("approved", False), "processing": ("approved", False),
    "unavailable": ("approved", False),
    "available": ("approved", True), "completed": ("approved", True),
    "declined": ("denied", None), "failed": ("denied", None),
}

# soulsync kind -> permission rows
_ROWS = {"movie": ("movies", "movie"), "show": ("tv", "tv")}

REFUSE_UNKNOWN = "refuse"
FALLBACK_UNKNOWN = "profile"
NOT_LINKED = ("This Jellyfin account isn't linked to a SoulSync profile. "
              "Ask an admin to link it.")
TURNED_OFF = "This SoulSync profile is turned off."


class SeerrError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def norm_jellyfin_id(value) -> str:
    return str(value or "").replace("-", "").strip().lower()


def monitor_for_seasons(real: List[int], wanted: Optional[List[int]]):
    """(monitor policy, note) for a show request that waits for approval.
    soulsync requests carry a policy, not a season list, so the nearest
    policy is picked and seasons it can't say exactly go in the note."""
    if not real or wanted is None or set(real) <= set(wanted):
        return "all", None
    if wanted == [real[0]]:
        return "first_season", None
    if wanted == [real[-1]]:
        return "latest_season", None
    seasons = ", ".join(str(s) for s in wanted)
    return "all", "asked for season%s %s" % ("s" if len(wanted) > 1 else "", seasons)


def parse_seasons(raw) -> Optional[List[int]]:
    """the request body's seasons: a list of numbers, or "all" (None)."""
    if raw is None or (isinstance(raw, str) and raw.lower() == "all"):
        return None
    if not isinstance(raw, list):
        raise SeerrError(400, "seasons must be a list of season numbers")
    out = sorted({n for n in (_int(x) for x in raw) if n and n > 0})
    if not out:
        raise SeerrError(400, "At least one season is required for a television request.")
    return out


def legacy_context(p: Optional[dict]) -> Dict[str, Any]:
    """a can() context read from a profile row's old columns. what the service
    uses when no permission reader is wired in (tests, tools); the app wires
    core.permission_store.context_for, which adds templates and the policy."""
    if not p:
        return P.make_context(None, perms=None, source="none")
    if P.is_admin_row(p):
        return P.make_context(p.get("id"), is_admin=True, perms=P.admin_map(), source="admin")
    return P.make_context(p.get("id"), perms=P.template_from_legacy(p), source="legacy")


def is_4k(q: Mapping[str, Any]) -> bool:
    return "4k" in (q.get("tags") or [])


class SeerrCompat:
    CTX_TTL = 5.0

    def __init__(self, *, engine: Callable[[], Any], video_db: Callable[[], Any], store,
                 get_profile: Callable[[int], Optional[dict]],
                 list_profiles: Callable[[], List[dict]],
                 jellyfin_users: Callable[[], List[dict]],
                 fallback_profile_id: Callable[[], int],
                 server: Callable[[], Optional[str]],
                 title_allowed: Callable[[str, int, Optional[int]], bool],
                 filter_titles: Callable[[List[dict], Dict[str, Optional[int]]], List[dict]] = lambda items, caps: items,
                 publish: Callable[[str, dict], Any] = lambda *a: None,
                 show_wanted: Callable[[int], Any] = lambda *a: None,
                 today: Callable[[], str] = lambda: date.today().isoformat(),
                 perms_for: Callable[[dict], Mapping[str, Any]] = legacy_context,
                 unknown_users: Callable[[], str] = lambda: FALLBACK_UNKNOWN,
                 quality_profiles: Callable[[], List[dict]] = lambda: [],
                 resolver: Optional[Callable[..., Any]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._engine = engine
        self._db = video_db
        self.store = store
        self._get_profile = get_profile
        self._list_profiles = list_profiles
        self._jellyfin_users = jellyfin_users
        self._fallback = fallback_profile_id
        self._server = server
        self._title_allowed = title_allowed
        self._filter_titles = filter_titles
        self._publish = publish
        self._show_wanted = show_wanted
        self._today = today
        self._perms_for = perms_for
        self._unknown_users = unknown_users
        self._quality_profiles = quality_profiles
        self._resolver = resolver
        self._clock = clock
        self._ctx_cache: Dict[int, tuple] = {}

    # ---- who a call acts for -------------------------------------------------

    def refuses_unknown(self) -> bool:
        try:
            return str(self._unknown_users() or "").lower() != FALLBACK_UNKNOWN
        except Exception:  # noqa: BLE001 - a setting that can't be read refuses
            return True

    def fallback_profile(self) -> Optional[dict]:
        """who a jellyfin user with no profile acts as: None when settings
        refuse them. the profile setting, else admin (today's rule, kept for
        installs that had it)."""
        if self.refuses_unknown():
            return None
        for candidate in (_int(self._fallback()), 1):
            if candidate is None:
                continue
            p = self._get_profile(candidate)
            if p:
                return p
        return None

    def profile(self, pid) -> dict:
        """the profile X-Api-User names, else the fallback profile. refused
        (SeerrError 403) when nobody may act: unknown users while settings
        refuse them, or a turned-off profile."""
        p = None
        uid = _int(pid)
        if uid is not None and uid > 0:
            p = self._get_profile(uid)
        if not p:
            p = self.fallback_profile()
            if not p:
                logger.info("seerr compat: refused X-Api-User %r, no profile acts for it", pid)
                raise SeerrError(403, NOT_LINKED)
        if p.get("disabled"):
            raise SeerrError(403, TURNED_OFF)
        return p

    def me(self, pid) -> dict:
        """/auth/me. the bare api key (no X-Api-User) while unknown users are
        refused answers as a user with no rights, so a client's connection
        test still passes but nothing can be asked for as nobody."""
        if (_int(pid) or 0) <= 0 and self.refuses_unknown():
            return shapes.user(0, "SoulSync API key", permissions=0)
        return self.user(self.profile(pid))

    def ctx(self, p: dict) -> Mapping[str, Any]:
        """the acting profile's can() context, kept a few seconds so a list
        of requests doesn't read every requester's rights again per row."""
        key = _int(p.get("id"), 0)
        now = self._clock()
        hit = self._ctx_cache.get(key)
        if hit and now - hit[0] < self.CTX_TTL:
            return hit[1]
        try:
            c = self._perms_for(p)
        except Exception:  # noqa: BLE001 - can't read it is no rights
            logger.warning("seerr compat: permissions for profile %s unreadable", key, exc_info=True)
            c = P.make_context(key, perms=None, source="none")
        self._ctx_cache[key] = (now, c)
        return c

    def may(self, p: dict, kind: str, mode: str = "ask") -> bool:
        """may this profile see (mode ask) or get right away (download) this kind."""
        if not p or p.get("disabled"):
            return False
        cat = _ROWS[kind][0]
        c = self.ctx(p)
        return P.can(c, cat + ".access") and P.can(c, cat + ".get", mode)

    def sees(self, p: dict, kind: str) -> bool:
        if not p or p.get("disabled"):
            return False
        return P.can(self.ctx(p), _ROWS[kind][0] + ".access")

    def video_allowed(self, p: dict) -> bool:
        return self.sees(p, "movie") or self.sees(p, "show")

    def caps(self, p: dict) -> Dict[str, Optional[int]]:
        """{kind: rating cap age or None} for the acting profile."""
        from core.content_filter import cap_age

        c = self.ctx(p)
        if c.get("is_admin"):
            return {"movie": None, "show": None}
        perms = c.get("perms")
        if not isinstance(perms, Mapping):
            return {"movie": 0, "show": 0}

        def cap(v):
            if v == "any":
                return None
            return cap_age(v) if v else 0
        movie = perms.get("movies.max_rating")
        tv = perms.get("tv.max_rating")
        return {"movie": cap(movie), "show": cap(movie if tv == P.FOLLOW else tv)}

    # ---- quality ---------------------------------------------------------------

    def _all_qualities(self) -> List[dict]:
        try:
            return [q for q in self._quality_profiles() or [] if q.get("id") is not None]
        except Exception:  # noqa: BLE001 - no list, nothing to offer
            logger.debug("seerr compat: quality profiles unreadable", exc_info=True)
            return []

    def qualities(self, p: dict, kind: str) -> List[dict]:
        """the quality profiles this profile may ask for, for this kind."""
        cat, mt = _ROWS[kind]
        c = self.ctx(p)
        return [q for q in self._all_qualities()
                if q.get("media_type") in (None, mt) and P.can(c, cat + ".quality", q["id"])]

    def default_quality(self, p: dict, kind: str, allowed: List[dict]) -> Optional[int]:
        pick = (self.ctx(p).get("perms") or {}).get(_ROWS[kind][0] + ".quality_default")
        ids = [q["id"] for q in allowed]
        if pick is not None and pick in ids:
            return pick
        return ids[0] if ids else None

    def _kind_default(self, kind: str) -> Optional[int]:
        """the household's default quality for this kind (Settings > Quality)."""
        try:
            from core.video.quality_profile import kind_default_id
            return kind_default_id(self._db(), kind) or 0
        except Exception:  # noqa: BLE001 - the resolver falls back to the first allowed
            return None

    def resolve_quality(self, p: dict, kind: str, body: Mapping[str, Any]):
        """(quality profile id or None, needs approval) for a request body.
        overseerr's profileId goes through resolve_request_quality, the same
        resolver every other request path uses (#2168), with this install's
        quality profiles (so an id overseerr only knows from radarr gets the
        person's default, and "4K always asks" means what it means
        everywhere); is4k picks the person's allowed 4K profile (403 when
        there is none). needs approval is the resolver's answer alone."""
        _cat, mt = _ROWS[kind]
        wanted = _int(body.get("profileId"))
        chosen = None
        if body.get("is4k"):
            fours = [q for q in self.qualities(p, kind) if is_4k(q)]
            if not fours:
                raise SeerrError(403, "4K isn't part of your profile for %s."
                                 % ("movies" if kind == "movie" else "shows"))
            by_id = {q["id"]: q for q in fours}
            if wanted not in by_id:
                default = self.default_quality(p, kind, fours)
                wanted = default if default in by_id else fours[0]["id"]
            chosen = by_id[wanted]
        perms = self.ctx(p).get("perms")
        if self._resolver is not None:
            d = self._resolver(p, mt, wanted, perms=perms)
        else:
            # an empty list is one that couldn't be read (Default is always
            # there): then every id in the allowed set counts, as before
            options = self._all_qualities() or None
            d = P.resolve_request_quality(p, mt, wanted, perms=perms, options=options,
                                          kind_default=self._kind_default(kind) if options else None)
        if d.refused:
            raise SeerrError(403, "That quality profile isn't part of your profile.")
        qid = d.profile_id
        if qid is None and chosen is not None:
            # the 4K pick was checked against the allowed set above
            qid = chosen["id"]
        return qid, bool(d.needs_approval)

    def services(self, p: dict, kind: str, server_id: Optional[int] = None):
        """/service/radarr|sonarr: soulsync's quality profiles this person may
        ask for, as overseerr servers (one for 4K when they may ask for 4K)."""
        if not self.may(p, kind):
            return [] if server_id is None else None
        allowed = self.qualities(p, kind)
        groups = [(0, "SoulSync", False, [q for q in allowed if not is_4k(q)]),
                  (1, "SoulSync 4K", True, [q for q in allowed if is_4k(q)])]
        servers = []
        for sid, name, four, qs in groups:
            if not qs:
                continue
            srv = {"id": sid, "name": name, "is4k": four, "isDefault": True,
                   "activeProfileId": self.default_quality(p, kind, qs), "activeDirectory": "",
                   "activeTags": []}
            if kind == "show":
                srv.update(activeAnimeProfileId=None, activeAnimeDirectory=None,
                           activeLanguageProfileId=None, activeAnimeLanguageProfileId=None,
                           activeAnimeTags=[], enableSeasonFolders=True)
            if server_id is None:
                servers.append(srv)
            elif sid == server_id:
                out = {"server": srv, "profiles": [{"id": q["id"], "name": q.get("name") or ""} for q in qs],
                       "rootFolders": [], "tags": []}
                if kind == "show":
                    out["languageProfiles"] = []
                return out
        return servers if server_id is None else None

    # ---- users -------------------------------------------------------------------

    def permissions(self, p: dict) -> int:
        """seerr's permission bits for what this profile may do here."""
        if not self.video_allowed(p):
            return 0
        c = self.ctx(p)
        req = {k: self.may(p, k) for k in ("movie", "show")}
        auto = {k: req[k] and self.may(p, k, "download") for k in ("movie", "show")}
        fours = {k: req[k] and any(is_4k(q) for q in self.qualities(p, k)) for k in ("movie", "show")}
        bits = 0

        def pair(flags, both, movie, tv):
            if flags["movie"] and flags["show"]:
                return both
            return (movie if flags["movie"] else 0) | (tv if flags["show"] else 0)
        bits |= pair(req, shapes.PERM_REQUEST, shapes.PERM_REQUEST_MOVIE, shapes.PERM_REQUEST_TV)
        bits |= pair(auto, shapes.PERM_AUTO_APPROVE, shapes.PERM_AUTO_APPROVE_MOVIE,
                     shapes.PERM_AUTO_APPROVE_TV)
        bits |= pair(fours, shapes.PERM_REQUEST_4K, shapes.PERM_REQUEST_4K_MOVIE,
                     shapes.PERM_REQUEST_4K_TV)
        if any(req[k] and len(self.qualities(p, k)) > 1 for k in req):
            bits |= shapes.PERM_REQUEST_ADVANCED
        if c.get("is_admin") or P.can(c, "requests.moderate"):
            bits |= shapes.PERM_MANAGE_REQUESTS
        if c.get("is_admin") or P.can(c, "requests.see_all") or P.can(c, "requests.moderate"):
            bits |= shapes.PERM_REQUEST_VIEW
        return bits

    def user(self, p: dict, **kw) -> dict:
        return shapes.user(p.get("id") or 1, p.get("name") or "", permissions=self.permissions(p), **kw)

    def users(self) -> List[dict]:
        """one seerr user per jellyfin user, carrying the soulsync profile it
        maps to as its id: the profile linked to that jellyfin account, else
        the profile with the same name, else the fallback profile (none while
        settings refuse unknown users). profiles no jellyfin user maps to are
        listed under their own name. only profiles that may use movies or tv
        are listed: a seerr client can't act as anyone else."""
        profiles = [p for p in self._list_profiles() or []
                    if p.get("id") is not None and self.video_allowed(p)]
        by_jf = {norm_jellyfin_id(p.get("jellyfin_user_id")): p for p in profiles
                 if norm_jellyfin_id(p.get("jellyfin_user_id"))}
        by_name = {str(p.get("name") or "").strip().casefold(): p for p in profiles}
        fallback = self.fallback_profile()
        if fallback is not None and not self.video_allowed(fallback):
            fallback = None
        out, covered = [], set()
        try:
            jf_users = self._jellyfin_users() or []
        except Exception:  # noqa: BLE001 - profiles alone still answer
            logger.debug("seerr compat: jellyfin user list failed", exc_info=True)
            jf_users = []
        for u in jf_users:
            jid, jname = str(u.get("id") or ""), str(u.get("name") or "")
            if not jid:
                continue
            p = by_jf.get(norm_jellyfin_id(jid)) or by_name.get(jname.strip().casefold()) or fallback
            if p is None:
                continue
            covered.add(int(p["id"]))
            out.append(self.user(p, jellyfin_id=jid, jellyfin_name=jname))
        for p in profiles:
            if int(p["id"]) not in covered:
                out.append(self.user(p, jellyfin_id=p.get("jellyfin_user_id") or None))
        return out

    def user_one(self, uid) -> dict:
        p = self._get_profile(_int(uid, 0)) if _int(uid, 0) > 0 else None
        if not p or p.get("disabled") or not self.video_allowed(p):
            raise SeerrError(404, "User not found.")
        return self.user(p)

    # ---- media status --------------------------------------------------------

    def _infos(self, items: Iterable[dict]) -> Dict[tuple, Optional[dict]]:
        """{(kind, tmdb_id): mediaInfo} for a batch of engine items. the status
        comes from the same batch the video pages read (#2160)."""
        from core.video import title_status

        db = self._db()
        srv = self._server()
        by_kind: Dict[str, List[dict]] = {"movie": [], "show": []}
        for it in items or []:
            if it.get("kind") in by_kind and it.get("tmdb_id"):
                by_kind[it["kind"]].append(it)
        out: Dict[tuple, Optional[dict]] = {}
        for kind, rows in by_kind.items():
            if not rows:
                continue
            ids = [int(r["tmdb_id"]) for r in rows]
            # the engine stamps library_id on what it knows; the rest is looked up
            hint = {int(r["tmdb_id"]): r.get("library_id") is not None
                    for r in rows if "library_id" in r}
            got = title_status.batch(db, self.store, kind, ids, server=srv, today=self._today(),
                                     owned_hint=hint)
            for tid in ids:
                st = (got.get(tid) or {}).get("status")
                out[(kind, tid)] = shapes.media_info(kind, tid, title_status.seerr_code(st))
        return out

    def _visible(self, items: Iterable[dict], p: dict) -> List[dict]:
        """the titles this profile may see: kinds it has access to, under its
        rating cap per kind (a kids profile's lists keep only what is rated
        under it). people and other cards pass."""
        items = [i for i in items or []
                 if i.get("kind") not in ("movie", "show") or self.sees(p, i["kind"])]
        caps = self.caps(p)
        if caps["movie"] is None and caps["show"] is None:
            return items
        return self._filter_titles(items, caps)

    def _results(self, items: Iterable[dict], *, people: bool = False, pid=None) -> List[dict]:
        items = [i for i in items or [] if people or i.get("kind") in ("movie", "show")]
        items = self._visible(items, self.profile(pid))
        infos = self._infos(items)
        out = []
        for it in items:
            r = shapes.result(it, infos.get((it.get("kind"), _int(it.get("tmdb_id")))))
            if r:
                out.append(r)
        return out

    # ---- discover and search -------------------------------------------------

    def trending(self, page_no: int, pid=None) -> dict:
        # the engine keeps tmdb's first trending page, the chart a seerr home row shows
        items = self._engine().trending() if page_no <= 1 else []
        return shapes.page(self._results(items, pid=pid), page_no, total_pages=1)

    def discover(self, kind: str, page_no: int, args: Dict[str, Any], pid=None) -> dict:
        sort = str(args.get("sortBy") or "popularity.desc")
        if not _SORT.match(sort):
            sort = "popularity.desc"
        if kind == "show":
            sort = sort.replace("primary_release_date", "first_air_date")
        items = self._engine().discover_filter(
            kind, genre=args.get("genre") or None, language=args.get("language") or None,
            keywords=args.get("keywords") or None,
            companies=args.get("studio") or None if kind == "movie" else None,
            networks=args.get("network") or None if kind == "show" else None,
            certification=args.get("certification") or None if kind == "movie" else None,
            sort_by=sort, page=page_no)
        return shapes.page(self._results(items, pid=pid), page_no)

    def upcoming(self, kind: str, page_no: int, pid=None) -> dict:
        if kind == "movie":
            items = self._engine().discover_curated("upcoming_movies", page=page_no)
        else:
            # no votes yet on anything that hasn't aired, so no vote floor
            items = self._engine().discover_filter("show", release_window="next_90",
                                                   vote_count_min=0, page=page_no)
        return shapes.page(self._results(items, pid=pid), page_no)

    def genre_slider(self, kind: str) -> List[dict]:
        return [{"id": g["id"], "name": g.get("name") or "", "backdrops": []}
                for g in self._engine().genre_list(kind) or [] if g.get("id")]

    def search(self, query: str, page_no: int, pid=None) -> dict:
        query = (query or "").strip()
        if not query or page_no > 1:
            # the engine's multi search is one page of tmdb results
            return shapes.page([], page_no, total_pages=1 if not query else page_no)
        return shapes.page(self._results(self._engine().search(query), people=True, pid=pid),
                           1, total_pages=1)

    # ---- details -------------------------------------------------------------

    def _detail(self, kind: str, tmdb_id: int) -> Optional[dict]:
        """the engine's tmdb detail. an owned title answers with a redirect to
        the library page there, so its tmdb data comes from the full detail."""
        eng = self._engine()
        d = eng.tmdb_detail(kind, tmdb_id)
        if isinstance(d, dict) and d.get("redirect"):
            d = eng.tmdb_full_detail(kind, tmdb_id)
            if not d:
                return None
            d = dict(d)
            ex = d.pop("_extras", {}) or {}
            for key in ("trailer", "similar", "recommendations", "collection", "videos",
                        "keywords", "facts", "studios", "cast_full"):
                if ex.get(key) is not None:
                    d[key] = ex[key]
            if kind == "show":
                d["seasons"] = [dict(s, episode_total=s.get("episode_count") or 0)
                                for s in d.pop("_seasons", []) or []]
        return d if isinstance(d, dict) and d.get("title") else None

    def details(self, kind: str, tmdb_id: int, pid=None) -> dict:
        p = self.profile(pid)
        # a kind it can't use, or over the acting profile's rating cap, reads as not there
        if not self.sees(p, kind) or not self._title_allowed(kind, tmdb_id, self.caps(p)[kind]):
            raise SeerrError(404, "Unable to retrieve %s." % ("movie" if kind == "movie" else "series"))
        d = self._detail(kind, tmdb_id)
        if not d:
            raise SeerrError(404, "Unable to retrieve %s." % ("movie" if kind == "movie" else "series"))
        from core.video import title_status

        db = self._db()
        srv = self._server()
        owned = db.library_id_for_tmdb(kind, tmdb_id, srv) is not None
        got = title_status.batch(db, self.store, kind, [tmdb_id], server=srv, today=self._today(),
                                 owned_hint={int(tmdb_id): owned}).get(int(tmdb_id)) or {}
        coverage = got.get("coverage")
        status = title_status.seerr_code(got.get("status"))
        seasons = []
        if kind == "show":
            seasons = shapes.season_statuses(coverage or {}, self.store.wished_seasons(tmdb_id))
        requests = [shapes.request_row(r, self._requested_by(r))
                    for r in self._title_requests(kind, tmdb_id)
                    if self._sees_request(p, r)]
        info = shapes.media_info(kind, tmdb_id, status, seasons=seasons,
                                 requests=[r for r in requests if r])
        return shapes.detail(kind, d, info)

    def _title_requests(self, kind: str, tmdb_id: int) -> List[dict]:
        kinds = ("movie",) if kind == "movie" else ("show", "episode")
        rows = [r for r in self._db().list_video_requests(status="pending")
                if r.get("kind") in kinds and _int(r.get("tmdb_id")) == int(tmdb_id)]
        return rows

    def similar(self, kind: str, tmdb_id: int, page_no: int, pid=None) -> dict:
        d = self._detail(kind, tmdb_id) if page_no <= 1 else None
        return shapes.page(self._results((d or {}).get("similar") or [], pid=pid), page_no,
                           total_pages=1)

    def recommendations(self, kind: str, tmdb_id: int, page_no: int, pid=None) -> dict:
        return shapes.page(self._results(self._engine().recommendations(kind, tmdb_id, page=page_no),
                                         pid=pid), page_no)

    def person(self, person_id: int) -> dict:
        p = self._engine().person_detail(person_id)
        if not p:
            raise SeerrError(404, "Unable to retrieve person.")
        return shapes.person(p)

    def person_credits(self, person_id: int, pid=None) -> dict:
        p = self._engine().person_detail(person_id)
        if not p:
            raise SeerrError(404, "Unable to retrieve person.")
        credits = [c for c in p.get("credits") or [] if c.get("kind") in ("movie", "show")]
        credits = self._visible(credits, self.profile(pid))
        return shapes.combined_credits(dict(p, credits=credits), self._infos(credits))

    def collection(self, collection_id: int, pid=None) -> dict:
        parts = self._results(self._engine().collection(collection_id) or [], pid=pid)
        if not parts:
            raise SeerrError(404, "Unable to retrieve collection.")
        # the engine keeps a collection's films, not its name
        return {"id": int(collection_id), "name": "", "overview": "", "posterPath": None,
                "backdropPath": None, "parts": parts}

    # ---- library -------------------------------------------------------------

    def recently_added(self, take: int, skip: int, pid=None) -> dict:
        db = self._db()
        take, skip = max(1, min(50, take)), max(0, skip)
        rows = db.recently_added(server_source=self._server(), limit=min(50, take + skip))
        out = []
        p = self.profile(pid)
        for r in rows[skip:skip + take]:
            tid = db.item_tmdb_id(r.get("kind"), r.get("id"))
            if not tid:
                continue
            if not self._visible([{"kind": r.get("kind"), "tmdb_id": int(tid)}], p):
                continue
            out.append({"id": int(tid), "tmdbId": int(tid), "tvdbId": None,
                        "mediaType": shapes.seerr_type(r.get("kind")),
                        "status": shapes.STATUS_AVAILABLE, "status4k": shapes.STATUS_UNKNOWN,
                        "createdAt": r.get("added_at"), "updatedAt": r.get("added_at"),
                        "mediaAddedAt": r.get("added_at")})
        return shapes.page_info(out, skip // take + 1, take, len(rows))

    # ---- requests ------------------------------------------------------------

    def _requested_by(self, row: dict) -> dict:
        p = self._get_profile(_int(row.get("profile_id"), 1)) or {
            "id": row.get("profile_id") or 1, "name": row.get("requester_name") or ""}
        return self.user(p)

    def _sees_all(self, p: dict) -> bool:
        """admins, moderators and see-everyone's-requests see every request."""
        c = self.ctx(p)
        return bool(c.get("is_admin")) or P.can(c, "requests.see_all") or P.can(c, "requests.moderate")

    def _sees_request(self, p: dict, row: dict) -> bool:
        return P.may_see_request(self.ctx(p), _int(row.get("profile_id")))

    def requests(self, *, take: int, skip: int, filt: str, requested_by=None, pid=None) -> dict:
        db = self._db()
        status, in_library = _FILTERS.get(str(filt or "all").lower(), (None, None))
        p = self.profile(pid)
        # the acting user's own requests unless they may see everyone's,
        # whatever requestedBy asks for (those who may can still narrow to one user)
        scope = _int(requested_by) if self._sees_all(p) else int(p["id"])
        rows = db.list_video_requests(profile_id=scope, status=status, limit=500)
        rows = [r for r in rows if r.get("kind") in ("movie", "show", "episode")]
        db.annotate_requests_in_library(rows)
        if in_library is not None:
            rows = [r for r in rows if bool(r.get("in_library")) == in_library]
        take, skip = max(1, min(100, take)), max(0, skip)
        out = [shapes.request_row(r, self._requested_by(r)) for r in rows[skip:skip + take]]
        return shapes.page_info([r for r in out if r], skip // take + 1, take, len(rows))

    def cancel_request(self, pid, request_id: int) -> None:
        p = self.profile(pid)
        db = self._db()
        row = db.get_video_request(int(request_id)) if hasattr(db, "get_video_request") else None
        own = row is not None and _int(row.get("profile_id")) == _int(p.get("id"))
        # your own always; an admin or a moderator anyone else's; the rest
        # reads as not there
        if row is None or not (own or P.may_moderate(self.ctx(p), _int(row.get("profile_id")))):
            raise SeerrError(404, "Request not found.")
        if not db.delete_video_request(int(request_id), profile_id=int(p["id"]) if own else None):
            raise SeerrError(404, "Request not found.")

    def create_request(self, pid, body: Dict[str, Any]) -> dict:
        from core.requests.video import request_metadata

        p = self.profile(pid)
        kind = shapes.soulsync_kind(body.get("mediaType"))
        tmdb_id = _int(body.get("mediaId"))
        if not kind or not tmdb_id or tmdb_id <= 0:
            raise SeerrError(400, "mediaType (movie or tv) and mediaId are required.")
        if not self.may(p, kind):
            raise SeerrError(403, "You do not have permission to request %s."
                             % ("movies" if kind == "movie" else "series"))
        seasons = parse_seasons(body.get("seasons")) if kind == "show" else None
        if not self._title_allowed(kind, tmdb_id, self.caps(p)[kind]):
            raise SeerrError(403, "This title is restricted for your profile.")
        quality, needs_ok = self.resolve_quality(p, kind, body)
        meta = request_metadata(kind, tmdb_id, {}, lambda k, t: self._engine().tmdb_detail(k, t))
        if kind == "movie" and meta["owned"]:
            raise SeerrError(409, "This movie is already available.")
        if not meta["title"] and not meta["owned"]:
            raise SeerrError(404, "Unable to retrieve that title.")
        if kind == "show":
            detail = self._detail("show", tmdb_id) or {}
            meta["title"] = meta["title"] or detail.get("title") or ""
            meta["poster_url"] = meta["poster_url"] or detail.get("poster_url")
            real = sorted({int(s["season_number"]) for s in detail.get("seasons") or []
                           if _int(s.get("season_number"), 0) >= 1})
            if seasons is not None and real:
                seasons = [s for s in seasons if s in real]
                if not seasons:
                    raise SeerrError(400, "Those seasons aren't part of this series.")
        else:
            real = []
        if not meta["title"]:
            raise SeerrError(404, "Unable to retrieve that title.")
        if self.may(p, kind, "download") and not needs_ok:
            return self._add_now(p, kind, tmdb_id, meta, real, seasons, quality)
        return self._file_request(p, kind, tmdb_id, meta, real, seasons, quality)

    def _created(self, rid: int, status: int, media_status: int, p: dict, kind: str,
                 tmdb_id: int, seasons: Optional[List[int]], quality=None) -> dict:
        four = quality is not None and any(q["id"] == quality and is_4k(q) for q in self._all_qualities())
        return {"id": int(rid), "status": status, "type": shapes.seerr_type(kind), "is4k": four,
                "profileId": quality,
                "media": {"id": tmdb_id, "tmdbId": tmdb_id, "tvdbId": None,
                          "mediaType": shapes.seerr_type(kind), "status": media_status,
                          "status4k": shapes.STATUS_UNKNOWN},
                "requestedBy": self.user(p),
                "seasons": [{"id": s, "seasonNumber": s, "status": status} for s in seasons or []]}

    def _add_now(self, p, kind, tmdb_id, meta, real, seasons, quality=None) -> dict:
        """the profile can download: on its wishlist now, like the video pages do."""
        db = self._db()
        srv = self._server()
        pid = int(p["id"])
        if kind == "movie":
            if tmdb_id in (db.wishlist_state(movie_ids=[tmdb_id], profile_id=pid) or {}).get("movies", ()):
                raise SeerrError(409, "Request for this media already exists.")
            if not db.add_movie_to_wishlist(tmdb_id, meta["title"], year=meta.get("year"),
                                            poster_url=meta.get("poster_url"),
                                            server_source=srv, profile_id=pid,
                                            quality_profile_id=quality):
                raise SeerrError(500, "Could not add the movie to the wishlist.")
            # no soulsync request row, so id 0: a seerr client can't cancel it,
            # it's taken off the wishlist in soulsync
            return self._created(0, shapes.REQUEST_APPROVED, shapes.STATUS_PROCESSING, p, kind,
                                 tmdb_id, None, quality)

        from core.video.monitor_policy import episodes_for_seasons
        wanted = real if seasons is None else seasons
        lib_id = db.library_id_for_tmdb("show", tmdb_id, srv)
        have = db.owned_episode_keys(lib_id) if lib_id else set()
        eps = [e for e in episodes_for_seasons(self._engine(), tmdb_id, wanted, self._today(),
                                               label="seerr request")
               if (e["season_number"], e["episode_number"]) not in have]
        # asking for the newest season is asking for what comes next too
        follow = bool(real) and real[-1] in wanted
        added = 0
        if eps:
            added = db.add_episodes_to_wishlist(tmdb_id, meta["title"], eps,
                                                poster_url=meta.get("poster_url"), library_id=lib_id,
                                                server_source=srv, profile_id=pid)
        if follow:
            if not db.add_to_watchlist("show", tmdb_id, meta["title"],
                                       poster_url=meta.get("poster_url"), library_id=lib_id,
                                       server_source=srv, profile_id=pid):
                raise SeerrError(500, "Could not follow the series.")
        elif not eps:
            raise SeerrError(409, "Those seasons are already available.")
        elif not added:
            raise SeerrError(409, "Request for this media already exists.")
        if quality is not None and lib_id is None and hasattr(db, "set_title_profile_choice") \
                and db.title_profile_choice("show", tmdb_id) is None:
            # a show not here yet keeps the asked quality for every episode
            # that comes later; a pick someone made already stays
            db.set_title_profile_choice("show", tmdb_id, quality)
        self._show_wanted(tmdb_id)
        return self._created(0, shapes.REQUEST_APPROVED, shapes.STATUS_PROCESSING, p, kind,
                             tmdb_id, wanted, quality)

    def _quota(self, p) -> Optional[Dict[str, int]]:
        """{limit, days} from the acting profile's request limit, or None."""
        c = self.ctx(p)
        if c.get("is_admin"):
            return None
        lim = (c.get("perms") or {}).get("requests.limit") or {}
        n, days = _int(lim.get("n"), 0), _int(lim.get("days"), 7)
        return {"limit": n, "days": max(1, days)} if n and n > 0 else None

    def _file_request(self, p, kind, tmdb_id, meta, real, seasons, quality=None) -> dict:
        """ask first (or a quality that needs an OK): a video request an admin approves."""
        from core.requests.quota import over_quota, quota_message

        db = self._db()
        pid = int(p["id"])
        pending = any(_int(r.get("tmdb_id")) == tmdb_id and r.get("kind") == kind
                      for r in db.list_video_requests(profile_id=pid, status="pending"))
        if pending:
            raise SeerrError(409, "Request for this media already exists.")
        # already on its way (wishlisted, approved, downloading): asking again
        # would only put a duplicate in the admin's queue (#2160)
        from core.video import title_status
        got = title_status.batch(db, self.store, kind, [tmdb_id], server=self._server(),
                                 today=self._today()).get(int(tmdb_id)) or {}
        if title_status.blocks_request(kind, got.get("status"), got.get("state")):
            raise SeerrError(409, "Request for this media already exists.")
        quota = self._quota(p)
        # one count for every ask, music and video (core/requests/asks.py)
        from core.requests.asks import asks_used
        used = asks_used(pid, quota["days"], video_db=db) if quota else 0
        # a count that can't be read is a full quota, never a free ask
        if quota and over_quota(quota, quota["limit"] if used is None else used):
            raise SeerrError(403, quota_message(quota))
        monitor, note = ("future", None)
        if kind == "show":
            monitor, note = monitor_for_seasons(real, seasons)
        rid, created = db.add_video_request(
            profile_id=pid, requester_name=p.get("name"), kind=kind, tmdb_id=tmdb_id,
            title=meta["title"], year=meta.get("year"), poster_url=meta.get("poster_url"),
            note=note, monitor=monitor, quality_profile_id=quality)
        if rid is None:
            raise SeerrError(500, "Could not file the request.")
        if not created:
            raise SeerrError(409, "Request for this media already exists.")
        try:
            self._publish("video_request_created", {"kind": kind, "title": meta["title"],
                                                    "requester": p.get("name") or ""})
        except Exception:  # noqa: BLE001 - events never disturb the request
            logger.exception("seerr compat: request-created event failed")
        return self._created(rid, shapes.REQUEST_PENDING, shapes.STATUS_PENDING, p, kind,
                             tmdb_id, seasons if seasons is not None else real, quality)
