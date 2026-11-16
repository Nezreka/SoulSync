"""the seerr compatible api's logic (#2151). the blueprint parses requests and
hands everything here; every outside piece (tmdb engine, video db, profiles,
jellyfin users, the kids check) comes in through the constructor so tests can
stand them in.

what a request does depends on the asking profile, the same as in soulsync's
own video pages:
- a profile that can download gets it straight away: a movie goes on its
  video wishlist, a show's aired episodes of the asked seasons go on it, and
  asking for the newest season also follows the show so new episodes come.
- a profile that can't download files a video request, which waits on the
  Requests page for an admin like any other.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

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


class SeerrCompat:
    def __init__(self, *, engine: Callable[[], Any], video_db: Callable[[], Any], store,
                 get_profile: Callable[[int], Optional[dict]],
                 list_profiles: Callable[[], List[dict]],
                 jellyfin_users: Callable[[], List[dict]],
                 fallback_profile_id: Callable[[], int],
                 server: Callable[[], Optional[str]],
                 title_allowed: Callable[[str, int, dict], bool],
                 publish: Callable[[str, dict], Any] = lambda *a: None,
                 show_wanted: Callable[[int], Any] = lambda *a: None,
                 today: Callable[[], str] = lambda: date.today().isoformat()):
        self._engine = engine
        self._db = video_db
        self.store = store
        self._get_profile = get_profile
        self._list_profiles = list_profiles
        self._jellyfin_users = jellyfin_users
        self._fallback = fallback_profile_id
        self._server = server
        self._title_allowed = title_allowed
        self._publish = publish
        self._show_wanted = show_wanted
        self._today = today

    # ---- profiles and users ----------------------------------------------------

    def profile(self, pid) -> dict:
        """the profile X-Api-User names, else the fallback profile, else admin."""
        for candidate in (_int(pid), _int(self._fallback()), 1):
            if candidate is None:
                continue
            p = self._get_profile(candidate)
            if p:
                return p
        return {"id": 1, "name": "Admin", "is_admin": True, "can_download": True}

    @staticmethod
    def video_allowed(p: dict) -> bool:
        if not p or p.get("disabled"):
            return False
        if p.get("is_admin") or int(p.get("id") or 0) == 1:
            return True
        return (p.get("allowed_sides") or "music") in ("video", "both")

    @staticmethod
    def may_download(p: dict) -> bool:
        return bool(p.get("is_admin") or int(p.get("id") or 0) == 1 or p.get("can_download"))

    def permissions(self, p: dict) -> int:
        if not self.video_allowed(p):
            return 0
        return shapes.PERM_REQUEST | (shapes.PERM_AUTO_APPROVE if self.may_download(p) else 0)

    def user(self, p: dict, **kw) -> dict:
        return shapes.user(p.get("id") or 1, p.get("name") or "", permissions=self.permissions(p), **kw)

    def users(self) -> List[dict]:
        """one seerr user per jellyfin user, carrying the soulsync profile it
        maps to as its id: the profile linked to that jellyfin account, else
        the profile with the same name, else the fallback profile. profiles
        no jellyfin user maps to are listed under their own name."""
        profiles = [p for p in self._list_profiles() or [] if p.get("id") is not None]
        by_jf = {norm_jellyfin_id(p.get("jellyfin_user_id")): p for p in profiles
                 if norm_jellyfin_id(p.get("jellyfin_user_id"))}
        by_name = {str(p.get("name") or "").strip().casefold(): p for p in profiles}
        fallback = self.profile(None)
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
            covered.add(int(p["id"]))
            out.append(self.user(p, jellyfin_id=jid, jellyfin_name=jname))
        for p in profiles:
            if int(p["id"]) not in covered:
                out.append(self.user(p, jellyfin_id=p.get("jellyfin_user_id") or None))
        return out

    # ---- media status --------------------------------------------------------

    def _infos(self, items: Iterable[dict]) -> Dict[tuple, Optional[dict]]:
        """{(kind, tmdb_id): mediaInfo} for a batch of engine items."""
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
            owned = {int(r["tmdb_id"]) for r in rows if r.get("library_id") is not None}
            unknown = [int(r["tmdb_id"]) for r in rows if "library_id" not in r]
            if unknown:
                owned |= set(db.library_ids_for_tmdb(kind, unknown, srv) or {})
            states = self.store.title_states(kind, ids)
            coverage = (self.store.show_coverage(owned, self._today(), srv)
                        if kind == "show" and owned else {})
            for tid in ids:
                status = shapes.media_status(kind, owned=tid in owned, coverage=coverage.get(tid),
                                             state=states.get(tid))
                out[(kind, tid)] = shapes.media_info(kind, tid, status)
        return out

    def _results(self, items: Iterable[dict], *, people: bool = False) -> List[dict]:
        items = [i for i in items or [] if people or i.get("kind") in ("movie", "show")]
        infos = self._infos(items)
        out = []
        for it in items:
            r = shapes.result(it, infos.get((it.get("kind"), _int(it.get("tmdb_id")))))
            if r:
                out.append(r)
        return out

    # ---- discover and search -------------------------------------------------

    def trending(self, page_no: int) -> dict:
        # the engine keeps tmdb's first trending page, the chart a seerr home row shows
        items = self._engine().trending() if page_no <= 1 else []
        return shapes.page(self._results(items), page_no, total_pages=1)

    def discover(self, kind: str, page_no: int, args: Dict[str, Any]) -> dict:
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
        return shapes.page(self._results(items), page_no)

    def upcoming(self, kind: str, page_no: int) -> dict:
        if kind == "movie":
            items = self._engine().discover_curated("upcoming_movies", page=page_no)
        else:
            # no votes yet on anything that hasn't aired, so no vote floor
            items = self._engine().discover_filter("show", release_window="next_90",
                                                   vote_count_min=0, page=page_no)
        return shapes.page(self._results(items), page_no)

    def genre_slider(self, kind: str) -> List[dict]:
        return [{"id": g["id"], "name": g.get("name") or "", "backdrops": []}
                for g in self._engine().genre_list(kind) or [] if g.get("id")]

    def search(self, query: str, page_no: int) -> dict:
        query = (query or "").strip()
        if not query or page_no > 1:
            # the engine's multi search is one page of tmdb results
            return shapes.page([], page_no, total_pages=1 if not query else page_no)
        return shapes.page(self._results(self._engine().search(query), people=True), 1, total_pages=1)

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

    def details(self, kind: str, tmdb_id: int) -> dict:
        d = self._detail(kind, tmdb_id)
        if not d:
            raise SeerrError(404, "Unable to retrieve %s." % ("movie" if kind == "movie" else "series"))
        db = self._db()
        srv = self._server()
        owned = db.library_id_for_tmdb(kind, tmdb_id, srv) is not None
        state = self.store.title_states(kind, [tmdb_id]).get(int(tmdb_id))
        coverage = (self.store.show_coverage([tmdb_id], self._today(), srv).get(int(tmdb_id))
                    if kind == "show" and owned else None)
        status = shapes.media_status(kind, owned=owned, coverage=coverage, state=state)
        seasons = []
        if kind == "show":
            seasons = shapes.season_statuses(coverage or {}, self.store.wished_seasons(tmdb_id))
        requests = [shapes.request_row(r, self._requested_by(r))
                    for r in self._title_requests(kind, tmdb_id)]
        info = shapes.media_info(kind, tmdb_id, status, seasons=seasons,
                                 requests=[r for r in requests if r])
        return shapes.detail(kind, d, info)

    def _title_requests(self, kind: str, tmdb_id: int) -> List[dict]:
        kinds = ("movie",) if kind == "movie" else ("show", "episode")
        rows = [r for r in self._db().list_video_requests(status="pending")
                if r.get("kind") in kinds and _int(r.get("tmdb_id")) == int(tmdb_id)]
        return rows

    def similar(self, kind: str, tmdb_id: int, page_no: int) -> dict:
        d = self._detail(kind, tmdb_id) if page_no <= 1 else None
        return shapes.page(self._results((d or {}).get("similar") or []), page_no, total_pages=1)

    def recommendations(self, kind: str, tmdb_id: int, page_no: int) -> dict:
        return shapes.page(self._results(self._engine().recommendations(kind, tmdb_id, page=page_no)),
                           page_no)

    def person(self, person_id: int) -> dict:
        p = self._engine().person_detail(person_id)
        if not p:
            raise SeerrError(404, "Unable to retrieve person.")
        return shapes.person(p)

    def person_credits(self, person_id: int) -> dict:
        p = self._engine().person_detail(person_id)
        if not p:
            raise SeerrError(404, "Unable to retrieve person.")
        credits = [c for c in p.get("credits") or [] if c.get("kind") in ("movie", "show")]
        return shapes.combined_credits(p, self._infos(credits))

    def collection(self, collection_id: int) -> dict:
        parts = self._results(self._engine().collection(collection_id) or [])
        if not parts:
            raise SeerrError(404, "Unable to retrieve collection.")
        # the engine keeps a collection's films, not its name
        return {"id": int(collection_id), "name": "", "overview": "", "posterPath": None,
                "backdropPath": None, "parts": parts}

    # ---- library -------------------------------------------------------------

    def recently_added(self, take: int, skip: int) -> dict:
        db = self._db()
        take, skip = max(1, min(50, take)), max(0, skip)
        rows = db.recently_added(server_source=self._server(), limit=min(50, take + skip))
        out = []
        for r in rows[skip:skip + take]:
            tid = db.item_tmdb_id(r.get("kind"), r.get("id"))
            if not tid:
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

    def requests(self, *, take: int, skip: int, filt: str, requested_by=None) -> dict:
        db = self._db()
        status, in_library = _FILTERS.get(str(filt or "all").lower(), (None, None))
        scope = _int(requested_by)
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
        # an admin may cancel anyone's, everyone else only their own
        scope = None if p.get("is_admin") or int(p["id"]) == 1 else int(p["id"])
        if not self._db().delete_video_request(int(request_id), profile_id=scope):
            raise SeerrError(404, "Request not found.")

    def create_request(self, pid, body: Dict[str, Any]) -> dict:
        from core.requests.video import request_metadata

        p = self.profile(pid)
        if not self.video_allowed(p):
            raise SeerrError(403, "You do not have permission to make video requests.")
        kind = shapes.soulsync_kind(body.get("mediaType"))
        tmdb_id = _int(body.get("mediaId"))
        if not kind or not tmdb_id or tmdb_id <= 0:
            raise SeerrError(400, "mediaType (movie or tv) and mediaId are required.")
        seasons = parse_seasons(body.get("seasons")) if kind == "show" else None
        if not self._title_allowed(kind, tmdb_id, p):
            raise SeerrError(403, "This title is restricted for your profile.")
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
        if self.may_download(p):
            return self._add_now(p, kind, tmdb_id, meta, real, seasons)
        return self._file_request(p, kind, tmdb_id, meta, real, seasons)

    def _created(self, rid: int, status: int, media_status: int, p: dict, kind: str,
                 tmdb_id: int, seasons: Optional[List[int]]) -> dict:
        return {"id": int(rid), "status": status, "type": shapes.seerr_type(kind), "is4k": False,
                "media": {"id": tmdb_id, "tmdbId": tmdb_id, "tvdbId": None,
                          "mediaType": shapes.seerr_type(kind), "status": media_status,
                          "status4k": shapes.STATUS_UNKNOWN},
                "requestedBy": self.user(p),
                "seasons": [{"id": s, "seasonNumber": s, "status": status} for s in seasons or []]}

    def _add_now(self, p, kind, tmdb_id, meta, real, seasons) -> dict:
        """the profile can download: on its wishlist now, like the video pages do."""
        db = self._db()
        srv = self._server()
        pid = int(p["id"])
        if kind == "movie":
            if tmdb_id in (db.wishlist_state(movie_ids=[tmdb_id], profile_id=pid) or {}).get("movies", ()):
                raise SeerrError(409, "Request for this media already exists.")
            if not db.add_movie_to_wishlist(tmdb_id, meta["title"], year=meta.get("year"),
                                            poster_url=meta.get("poster_url"),
                                            server_source=srv, profile_id=pid):
                raise SeerrError(500, "Could not add the movie to the wishlist.")
            # no soulsync request row, so id 0: a seerr client can't cancel it,
            # it's taken off the wishlist in soulsync
            return self._created(0, shapes.REQUEST_APPROVED, shapes.STATUS_PROCESSING, p, kind,
                                 tmdb_id, None)

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
        self._show_wanted(tmdb_id)
        return self._created(0, shapes.REQUEST_APPROVED, shapes.STATUS_PROCESSING, p, kind,
                             tmdb_id, wanted)

    def _file_request(self, p, kind, tmdb_id, meta, real, seasons) -> dict:
        """the profile can't download: a video request an admin approves."""
        from core.requests.quota import over_quota, quota_for, quota_message

        db = self._db()
        pid = int(p["id"])
        pending = any(_int(r.get("tmdb_id")) == tmdb_id and r.get("kind") == kind
                      for r in db.list_video_requests(profile_id=pid, status="pending"))
        if pending:
            raise SeerrError(409, "Request for this media already exists.")
        quota = quota_for(p)
        if quota and over_quota(quota, db.count_video_requests_since(pid, quota["days"])):
            raise SeerrError(403, quota_message(quota))
        monitor, note = ("future", None)
        if kind == "show":
            monitor, note = monitor_for_seasons(real, seasons)
        rid, created = db.add_video_request(
            profile_id=pid, requester_name=p.get("name"), kind=kind, tmdb_id=tmdb_id,
            title=meta["title"], year=meta.get("year"), poster_url=meta.get("poster_url"),
            note=note, monitor=monitor)
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
                             tmdb_id, seasons if seasons is not None else real)
