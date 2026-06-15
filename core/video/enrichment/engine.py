"""Video enrichment engine — owns the per-source workers (registry).

Parallels music's enrichment registry but is isolated to the video side. Built
lazily as a process-wide singleton; starts the workers (each idles until its API
key is configured). Imports only video.db + this package.
"""

from __future__ import annotations

import threading

from utils.logging_config import get_logger

from .cache import TTLCache
from .worker import VideoEnrichmentWorker

logger = get_logger("video_enrichment.engine")

_DISPLAY = {"tmdb": "TMDB", "tvdb": "TVDB", "omdb": "OMDb"}


class VideoEnrichmentEngine:
    def __init__(self, db, clients: dict, ratings_client=None):
        self.db = db
        self.workers = {
            service: VideoEnrichmentWorker(db, service, client, display_name=_DISPLAY.get(service))
            for service, client in clients.items()
        }
        # OMDb ratings (IMDb/RT/Metacritic) — not a matcher, so not a worker;
        # backfilled on the lazy detail refresh.
        self.ratings_client = ratings_client
        # Thread-safe TTL+LRU cache for live TMDB detail extras / preview payloads /
        # person pages / seasons / trending, so re-opening a title is instant
        # instead of re-hitting TMDB. (Volatile by design — durable art/episodes/
        # ratings live in video.db; we don't persist this tier.)
        self._cache = TTLCache(maxsize=256, ttl=1800)
        # Restore each worker's persisted pause state (survives restart).
        for w in self.workers.values():
            w.restore_paused()

    def _region(self):
        try:
            return (self.db.get_setting("watch_region") or "US").upper()
        except Exception:
            return "US"

    def _cache_get(self, key):
        return self._cache.get(key)

    def _cache_put(self, key, data, ttl=1800):
        self._cache.put(key, data, ttl=ttl)

    def _backfill_ratings(self, kind, item_id):
        # The OMDb worker owns the ratings client (fallback to an injected one
        # for tests that don't build a worker).
        w = self.workers.get("omdb")
        rc = w.client if w else self.ratings_client
        if not rc or not getattr(rc, "enabled", False):
            return
        info = (self.db.movie_match_info(item_id) if kind == "movie"
                else self.db.show_match_info(item_id))
        # IMDb id lives on the row — fetch it directly.
        row = None
        try:
            with self.db.connect() as c:
                tbl = "movies" if kind == "movie" else "shows"
                row = c.execute(f"SELECT imdb_id FROM {tbl} WHERE id=?", (item_id,)).fetchone()
        except Exception:
            return
        imdb_id = row["imdb_id"] if row else None
        if not imdb_id:
            return
        try:
            ratings = rc.ratings(imdb_id)
            if ratings:
                self.db.apply_ratings(kind, item_id, ratings)
        except Exception:
            logger.exception("ratings backfill failed for %s %s", kind, item_id)

    def start_all(self):
        for w in self.workers.values():
            w.start()

    def stop_all(self):
        for w in self.workers.values():
            w.stop()

    # ── scan coupling ─────────────────────────────────────────────────────────
    # While a library scan runs, the enrichment workers step aside to cut DB lock
    # contention — exactly like the music side. We pause ONLY workers that were
    # actually running (never a user's manual pause) and remember which, so the
    # post-scan resume can't un-pause something the user deliberately paused. The
    # auto-pause is transient (persist=False) so it never leaks into the saved
    # <service>_paused flag and survives a restart as a "real" pause.
    def pause_for_scan(self) -> set:
        self._scan_paused = set()
        for service, w in self.workers.items():
            if not w.paused:
                w.pause(persist=False)
                self._scan_paused.add(service)
        if self._scan_paused:
            logger.info("video enrichment: paused %s for library scan",
                        ", ".join(sorted(self._scan_paused)))
        return self._scan_paused

    def resume_after_scan(self) -> None:
        for service in getattr(self, "_scan_paused", set()):
            w = self.workers.get(service)
            if w:
                w.resume(persist=False)
        if getattr(self, "_scan_paused", None):
            logger.info("video enrichment: resumed %s after library scan",
                        ", ".join(sorted(self._scan_paused)))
        self._scan_paused = set()

    def refresh_show_art(self, show_id) -> dict:
        """On-demand (lazy) backfill of a show's season posters + episode art from
        TMDB, used when the detail page is opened and art is missing. Works
        regardless of the show's match status (sidesteps 'already matched, never
        re-runs'), and caches the result so it's a one-time cost per show."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return {"ok": False, "reason": "tmdb_not_configured"}
        info = self.db.show_match_info(show_id)
        if not info:
            return {"ok": False, "reason": "not_found"}
        try:
            result = w.client.match("show", info.get("title"), info.get("year"),
                                    known_id=info.get("tmdb_id"))
        except Exception:
            logger.exception("refresh_show_art: match failed for show %s", show_id)
            return {"ok": False, "reason": "match_error"}
        if not result or not result.get("id"):
            return {"ok": False, "reason": "no_match"}
        # Backfills season posters + show metadata gaps (never clobbers).
        self.db.enrichment_apply("tmdb", "show", show_id, matched=True,
                                 external_id=result["id"], metadata=result.get("metadata"))
        try:
            nums = [s["season_number"] for s in (result.get("metadata") or {}).get("seasons") or []]
            w._cascade_episodes(show_id, result["id"], nums)    # full list: owned + missing
        except Exception:
            logger.exception("refresh_show_art: episode cascade failed for show %s", show_id)
        self._backfill_ratings("show", show_id)
        return {"ok": True}

    def refresh_movie_art(self, movie_id) -> dict:
        """On-demand (lazy) backfill of a movie's cast / genres / backdrop / ratings
        from TMDB when the detail page is opened and they're missing. Works
        regardless of match status; caches the result."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return {"ok": False, "reason": "tmdb_not_configured"}
        info = self.db.movie_match_info(movie_id)
        if not info:
            return {"ok": False, "reason": "not_found"}
        try:
            result = w.client.match("movie", info.get("title"), info.get("year"),
                                    known_id=info.get("tmdb_id"))
        except Exception:
            logger.exception("refresh_movie_art: match failed for movie %s", movie_id)
            return {"ok": False, "reason": "match_error"}
        if not result or not result.get("id"):
            return {"ok": False, "reason": "no_match"}
        self.db.enrichment_apply("tmdb", "movie", movie_id, matched=True,
                                 external_id=result["id"], metadata=result.get("metadata"))
        self._backfill_ratings("movie", movie_id)
        return {"ok": True}

    def item_extras(self, kind, item_id) -> dict:
        """Live TMDB extras (trailer / where-to-watch / similar) for the detail
        page. Not cached — fetched per view so providers stay current. For an
        owned item we also surface a 'watch on your server' deep link as the first
        where-to-watch option."""
        out = {}
        w = self.workers.get("tmdb")
        if w and w.enabled:
            info = (self.db.movie_match_info(item_id) if kind == "movie"
                    else self.db.show_match_info(item_id))
            if info and info.get("tmdb_id"):
                region = self._region()
                key = ("extras", kind, info["tmdb_id"], region)
                cached = self._cache_get(key)
                if cached is None:
                    try:
                        cached = w.client.extras(kind, info["tmdb_id"], region=region) or {}
                        self._cache_put(key, cached)
                    except Exception:
                        logger.exception("item_extras failed for %s %s", kind, item_id)
                        cached = {}
                out = dict(cached)          # copy — the per-item server link isn't cached
        srv = self._server_watch_link(kind, item_id)
        if srv:
            out["server"] = srv
        return out

    def _server_watch_link(self, kind, item_id) -> dict | None:
        """A 'play on your media server' deep link for an owned item, or None.
        Plex → the Plex web app at the item; Jellyfin → its web detail page."""
        table = "movies" if kind == "movie" else "shows"
        try:
            with self.db.connect() as c:
                row = c.execute(
                    f"SELECT server_source, server_id FROM {table} WHERE id=?", (item_id,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        source, sid = row["server_source"], row["server_id"]
        if not source or not sid:
            return None                      # not on a server (e.g. a wishlist row)
        try:
            # Use the VIDEO side's effective connection (its own creds, or inherited
            # from music) — the item lives on the server the video side scanned.
            from core.video.sources import video_plex_config, video_jellyfin_config
            if source == "plex":
                cfg = video_plex_config(self.db)
                base, token = cfg.get("base_url"), cfg.get("token")
                if not base or not token:
                    return None
                mid = self._plex_machine_id(base, token)
                if not mid:
                    return None
                from urllib.parse import quote
                key = quote("/library/metadata/" + str(sid), safe="")
                return {"server": "Plex",
                        "url": "https://app.plex.tv/desktop/#!/server/%s/details?key=%s" % (mid, key)}
            if source == "jellyfin":
                cfg = video_jellyfin_config(self.db)
                base = cfg.get("base_url")
                if not base:
                    return None
                return {"server": "Jellyfin",
                        "url": base.rstrip("/") + "/web/index.html#!/details?id=" + str(sid)}
        except Exception:
            logger.exception("server watch link failed for %s %s", kind, item_id)
        return None

    def _plex_machine_id(self, base, token):
        """The Plex server's machineIdentifier (needed for app.plex.tv deep links),
        fetched once and cached per base URL."""
        cached = getattr(self, "_plex_mid", None)
        if cached and cached[0] == base:
            return cached[1]
        try:
            import requests
            r = requests.get(base.rstrip("/") + "/identity",
                             params={"X-Plex-Token": token},
                             headers={"Accept": "application/json"}, timeout=8)
            mid = None
            try:
                mid = ((r.json() or {}).get("MediaContainer") or {}).get("machineIdentifier")
            except Exception:
                import re
                m = re.search(r'machineIdentifier="([^"]+)"', r.text or "")
                mid = m.group(1) if m else None
            if mid:
                self._plex_mid = (base, mid)
            return mid
        except Exception:
            logger.exception("plex identity fetch failed")
            return None

    # ── in-app search + TMDB-backed (un-owned) detail ─────────────────────────
    def search(self, query) -> list:
        """Multi-search via TMDB, each movie/show annotated with the library row id
        if it's already owned (so the UI links to the owned detail, not the tmdb
        view)."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        # Short TTL — identical queries within ~a minute reuse the result; ownership
        # is re-stamped fresh below so 'In Library' badges stay current.
        key = ("search", (query or "").strip().lower())
        results = self._cache_get(key)
        if results is None:
            try:
                results = w.client.search(query) or []
                self._cache_put(key, results, ttl=60)
            except Exception:
                logger.exception("video search failed for %r", query)
                return []
        for r in results:
            if r.get("kind") in ("movie", "show") and r.get("tmdb_id"):
                r["library_id"] = self.db.library_id_for_tmdb(r["kind"], r["tmdb_id"])
        return results

    def trending(self) -> list:
        """Trending titles for the idle search page, annotated owned/not."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        cached = self._cache_get(("trending",))
        if cached is None:
            try:
                cached = w.client.trending() or []
                self._cache_put(("trending",), cached, ttl=3600)
            except Exception:
                logger.exception("video trending failed")
                return []
        # Re-annotate ownership fresh each call (cheap) so it tracks the library.
        for r in cached:
            if r.get("tmdb_id"):
                r["library_id"] = self.db.library_id_for_tmdb(r["kind"], r["tmdb_id"])
        return cached

    def tmdb_detail(self, kind, tmdb_id) -> dict | None:
        """Full detail for a TMDB title not in the library — same shape as the
        library detail (source='tmdb', direct image URLs, nothing owned). If it IS
        in the library, returns a redirect to the owned detail instead."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        lib_id = self.db.library_id_for_tmdb(kind, tmdb_id)
        if lib_id:
            return {"redirect": {"source": "library", "kind": kind, "id": lib_id}}
        region = self._region()
        cached = self._cache_get(("detail", kind, tmdb_id, region))
        if cached is not None:
            return dict(cached)
        try:
            d = w.client.full_detail(kind, tmdb_id, region=region)
        except Exception:
            logger.exception("tmdb_detail failed for %s %s", kind, tmdb_id)
            return None
        if not d:
            return None
        d.update({"source": "tmdb", "id": tmdb_id, "owned": False, "monitored": False,
                  "has_poster": bool(d.get("poster_url")), "has_backdrop": bool(d.get("backdrop_url"))})
        ex = d.pop("_extras", {}) or {}
        d.update({"trailer": ex.get("trailer"), "providers": ex.get("providers") or [],
                  "providers_link": ex.get("providers_link"), "similar": ex.get("similar") or [],
                  "recommendations": ex.get("recommendations") or [], "collection": ex.get("collection"),
                  "next_episode": ex.get("next_episode"), "last_episode": ex.get("last_episode"),
                  "gallery": ex.get("gallery"), "videos": ex.get("videos") or [],
                  "keywords": ex.get("keywords") or [], "facts": ex.get("facts"),
                  "cast_full": ex.get("cast_full") or [], "review": ex.get("review")})
        if kind == "show":
            seasons = d.pop("_seasons", []) or []
            for s in seasons:
                s["has_poster"] = bool(s.get("poster_url"))
                s["episode_total"] = s.pop("episode_count", 0) or 0
                s["episode_owned"] = 0
                s["episodes"] = []           # loaded lazily per season (tmdb_season)
            d["seasons"] = seasons
            d["season_count"] = len(seasons)
            d["episode_total"] = sum(s["episode_total"] for s in seasons)
            d["episode_owned"] = 0
        self._fill_tmdb_ratings(d)
        self._cache_put(("detail", kind, tmdb_id, region), d)
        return d

    def _fill_tmdb_ratings(self, d) -> None:
        imdb_id = d.get("imdb_id")
        ow = self.workers.get("omdb")
        if not imdb_id or not ow or not getattr(ow.client, "enabled", False):
            return
        try:
            r = ow.client.ratings(imdb_id) or {}
            for k in ("imdb_rating", "rt_rating", "metacritic"):
                if r.get(k) is not None:
                    d[k] = r[k]
        except Exception:
            logger.exception("tmdb_detail ratings failed for %s", imdb_id)

    def tmdb_season(self, tv_id, season_number) -> dict | None:
        """One season's episodes for a TMDB (un-owned) show — lazy-loaded when the
        season is selected on the search detail page. Nothing is owned."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        key = ("season", tv_id, season_number)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            se = w.client.season_episodes(tv_id, season_number)
        except Exception:
            logger.exception("tmdb_season failed for %s S%s", tv_id, season_number)
            return None
        if not se:
            return None
        eps = [{"episode_number": e.get("episode_number"), "title": e.get("title"),
                "overview": e.get("overview"), "air_date": e.get("air_date"),
                "runtime_minutes": e.get("runtime_minutes"), "rating": e.get("rating"),
                "still_url": e.get("still_url"), "has_still": bool(e.get("still_url")),
                "owned": False}
               for e in (se.get("episodes") or []) if e.get("episode_number") is not None]
        out = {"season_number": season_number, "overview": se.get("overview"),
               "poster_url": se.get("poster_url"), "episodes": eps}
        self._cache_put(key, out)
        return out

    def episode_extra(self, tmdb_id, season_number, episode_number) -> dict | None:
        """Deeper episode detail (guest stars + still) for the episode expand,
        annotated owned/not + cached."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not tmdb_id:
            return None
        key = ("episode", tmdb_id, season_number, episode_number)
        cached = self._cache_get(key)
        if cached is None:
            try:
                cached = w.client.episode_detail(tmdb_id, season_number, episode_number)
            except Exception:
                logger.exception("episode_extra failed for %s S%sE%s", tmdb_id, season_number, episode_number)
                return None
            if cached is None:
                return None
            self._cache_put(key, cached)
        return cached

    def person_detail(self, tmdb_id) -> dict | None:
        """A person (actor/director) page — bio + filmography, each credit
        annotated with the library id if owned. Keeps cast clicks in-app."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        p = self._cache_get(("person", tmdb_id))
        if p is None:
            try:
                p = w.client.person(tmdb_id)
            except Exception:
                logger.exception("person_detail failed for %s", tmdb_id)
                return None
            if not p:
                return None
            self._cache_put(("person", tmdb_id), p)
        # Re-annotate ownership fresh each call (cheap) so it tracks the library.
        for c in p.get("credits") or []:
            if c.get("tmdb_id"):
                c["library_id"] = self.db.library_id_for_tmdb(c["kind"], c["tmdb_id"])
        return p

    def worker(self, service):
        return self.workers.get(service)

    def services(self) -> list:
        return [{"id": s, "display_name": w.display_name} for s, w in self.workers.items()]


_engine = None
_lock = threading.Lock()


def get_video_enrichment_engine():
    """Process-wide engine, created (and started) on first use."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from database.video_database import VideoDatabase
                from .clients import build_clients, OMDBClient
                db = VideoDatabase()
                eng = VideoEnrichmentEngine(db, build_clients(db),
                                            ratings_client=OMDBClient(db.get_setting("omdb_api_key")))
                eng.start_all()
                _engine = eng
    return _engine


def peek_video_enrichment_engine():
    """The engine ONLY if it's already running — never creates or starts it. Lets
    the socket status emitter push updates without spinning up the video engine on
    the music side (it stays None until something actually uses video)."""
    return _engine


def rebuild_video_enrichment_engine():
    """Rebuild the engine so workers pick up changed API keys (stops the old
    workers first so threads don't leak)."""
    global _engine
    with _lock:
        if _engine is not None:
            try:
                _engine.stop_all()
            except Exception:
                logger.exception("video enrichment: stopping old engine failed")
            _engine = None
    return get_video_enrichment_engine()
