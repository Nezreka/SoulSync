"""Video enrichment engine — owns the per-source workers (registry).

Parallels music's enrichment registry but is isolated to the video side. Built
lazily as a process-wide singleton; starts the workers (each idles until its API
key is configured). Imports only video.db + this package.
"""

from __future__ import annotations

import threading
import time

from utils.logging_config import get_logger

from .cache import TTLCache
from .clients import OMDbAuthError
from .worker import VideoEnrichmentWorker

logger = get_logger("video_enrichment.engine")

_DISPLAY = {"tmdb": "TMDB", "tvdb": "TVDB", "omdb": "OMDb"}

# Once the OMDb daily-limit / bad-key latch trips it pauses ratings for the rest of the
# run — but it self-heals after this window so a long-running server re-probes OMDb (whose
# quota resets daily) on its own, without needing a restart. Matches the re-enrich cadence.
_OMDB_RETRY_SECONDS = 6 * 3600


def _latest_seasons(season_nums, keep: int = 2):
    """The most recent ``keep`` regular seasons (highest season numbers, specials /
    season 0 excluded) — the only seasons where a still-airing show gains new episodes.
    The nightly airing refresh scopes to these so it stops re-pulling long-finished
    seasons every night. Falls back to the full list when there are no regular seasons
    (a specials-only show)."""
    regular = sorted({n for n in (season_nums or []) if isinstance(n, int) and n > 0}, reverse=True)
    return regular[:keep] if regular else list(season_nums or [])


class VideoEnrichmentEngine:
    def __init__(self, db, clients: dict, ratings_client=None):
        self.db = db
        self.workers = {
            service: VideoEnrichmentWorker(db, service, client, display_name=_DISPLAY.get(service))
            for service, client in clients.items()
        }
        # Backfill workers (artwork / subtitles / no-key YouTube extras). Same
        # lifecycle + get_stats() shape, so the registry/API/UI drive them too.
        try:
            from .backfill import build_backfill_workers
            self.workers.update(build_backfill_workers(db))
        except Exception:
            logger.exception("video enrichment: backfill workers unavailable")
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

    def alt_titles_for(self, kind, tmdb_id) -> list:
        """Cached AKA titles for a movie/show — the alias set the downloader matches
        releases against. Cached hard (aliases barely change) so the search path adds
        no per-grab TMDB latency. Best-effort: [] when TMDB isn't configured."""
        if not tmdb_id:
            return []
        key = ("alt_titles", kind, str(tmdb_id))
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        out = []
        w = self.workers.get("tmdb")
        if w and getattr(w, "enabled", False) and hasattr(w.client, "alternative_titles"):
            try:
                out = w.client.alternative_titles(kind, tmdb_id) or []
            except Exception:   # noqa: BLE001 - a matching assist must never break a grab
                logger.debug("alt_titles_for failed for %s %s", kind, tmdb_id, exc_info=True)
        self._cache_put(key, out, ttl=86400)
        return out

    def _omdb_blocked_now(self) -> bool:
        """The OMDb over-quota / bad-key latch — True while ratings should be skipped.
        Self-healing: once tripped it stays set (no per-item re-probe within a run), but
        auto-clears after ``_OMDB_RETRY_SECONDS`` so the next run re-tries OMDb once its
        daily quota has rolled over, instead of staying stuck until the app restarts."""
        if not getattr(self, "_omdb_blocked", False):
            return False
        at = getattr(self, "_omdb_blocked_at", None)
        if at is not None and (time.time() - at) >= _OMDB_RETRY_SECONDS:
            self._omdb_blocked = False
            self._omdb_blocked_at = None
            logger.info("OMDb ratings latch cleared — re-probing (quota window elapsed)")
            return False
        return True

    def _trip_omdb_latch(self, exc) -> None:
        """Latch OMDb off (daily limit / bad key) with a timestamp so it can self-heal."""
        self._omdb_blocked = True
        self._omdb_blocked_at = time.time()
        logger.warning("OMDb ratings paused (auto-retries in ~%dh): %s",
                       _OMDB_RETRY_SECONDS // 3600, exc)

    def _backfill_ratings(self, kind, item_id):
        # The OMDb worker owns the ratings client (fallback to an injected one
        # for tests that don't build a worker).
        w = self.workers.get("omdb")
        rc = w.client if w else self.ratings_client
        # _omdb_blocked latches once the daily request limit / a bad key is hit — it affects
        # EVERY item, so we stop calling OMDb for the rest of the run instead of failing
        # (and logging a traceback) once per show. The latch self-heals after a window
        # (_omdb_blocked_now) so a long-running server recovers ratings without a restart.
        if not rc or not getattr(rc, "enabled", False) or self._omdb_blocked_now():
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
        except OMDbAuthError as e:
            # daily limit / bad key — hits every item; latch off + one quiet warning, no spam.
            self._trip_omdb_latch(e)
        except Exception:
            logger.exception("ratings backfill failed for %s %s", kind, item_id)

    def start_all(self):
        for w in self.workers.values():
            w.start()
        self._kick_franchise_backfill_soon()

    def _kick_franchise_backfill_soon(self, delay: float = 45.0):
        """Self-heal the franchise collection-id backlog on ANY first video activity (this runs
        when the engine boots — dashboard, search, sync, whatever), not only on a Collection
        Studio visit. Delayed off the boot path; single-flight + no-op-when-empty live inside
        kick_franchise_backfill, so repeat engine starts are cheap. Lazy import avoids the
        collections→engine circular; stays lazy overall (music-only sessions never start the
        engine, so this never fires)."""
        def _go():
            try:
                from core.video.collections.presets import kick_franchise_backfill
                kick_franchise_backfill(self.db)
            except Exception:   # noqa: BLE001 - a heal-nicety must never disturb the engine
                logger.debug("delayed franchise backfill kick failed", exc_info=True)
        try:
            t = threading.Timer(delay, _go)
            t.daemon = True
            t.start()
        except Exception:   # noqa: BLE001
            logger.debug("could not schedule franchise backfill", exc_info=True)

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
        # The YouTube date enricher is a separate singleton (not in self.workers) —
        # pause it too so a scan stops EVERY enricher, not just the matcher/backfill
        # workers. Only if it wasn't already paused (never override a manual pause).
        self._scan_paused_yt = False
        try:
            from core.video.youtube_enrichment import get_youtube_date_enricher
            yt = get_youtube_date_enricher()
            if yt and not getattr(yt, "_paused", False):
                yt.pause()
                self._scan_paused_yt = True
                self._scan_paused.add("youtube")
        except Exception:
            logger.debug("video enrichment: could not pause YouTube date enricher for scan", exc_info=True)
        if self._scan_paused:
            logger.info("video enrichment: paused %s for library scan",
                        ", ".join(sorted(self._scan_paused)))
        return self._scan_paused

    def resume_after_scan(self) -> None:
        for service in getattr(self, "_scan_paused", set()):
            w = self.workers.get(service)
            if w:
                w.resume(persist=False)
        if getattr(self, "_scan_paused_yt", False):
            try:
                from core.video.youtube_enrichment import get_youtube_date_enricher
                yt = get_youtube_date_enricher()
                if yt:
                    yt.resume()
            except Exception:
                logger.debug("video enrichment: could not resume YouTube date enricher", exc_info=True)
            self._scan_paused_yt = False
        if getattr(self, "_scan_paused", None):
            logger.info("video enrichment: resumed %s after library scan",
                        ", ".join(sorted(self._scan_paused)))
        self._scan_paused = set()

    def refresh_show_art(self, show_id, *, with_ratings: bool = True,
                         recent_seasons_only: bool = False) -> dict:
        """On-demand (lazy) backfill of a show's season posters + episode art from
        TMDB, used when the detail page is opened and art is missing. Works
        regardless of the show's match status (sidesteps 'already matched, never
        re-runs'), and caches the result so it's a one-time cost per show.

        ``with_ratings=False`` skips the OMDb ratings backfill — used by the bulk
        'Refresh Airing TV Schedules' automation, which only needs episode schedules and
        would otherwise burn the daily OMDb quota one call per show.

        ``recent_seasons_only=True`` cascades ONLY the latest 1–2 regular seasons instead
        of every season — also for the nightly airing refresh, where new episodes only ever
        land in the current season and re-pulling long-finished seasons every night (one API
        call per season, per show) is pure waste since backfill is gap-fill and settled
        seasons write nothing. It also leaves the episodes-synced flag untouched, so a show
        that hasn't had its FULL history pulled yet still gets its older seasons filled by
        the background episode-sync pass rather than being falsely marked complete."""
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
        nums = []
        try:
            nums = [s["season_number"] for s in (result.get("metadata") or {}).get("seasons") or []]
            if recent_seasons_only:
                nums = _latest_seasons(nums)    # only the current season(s) gain new episodes
            # mark_synced only when we pulled the FULL season list — a scoped refresh must
            # not claim the show is fully synced (the background pass finishes the rest).
            w._cascade_episodes(show_id, result["id"], nums,
                                mark_synced=not recent_seasons_only)
        except Exception:
            logger.exception("refresh_show_art: episode cascade failed for show %s", show_id)
        # TVDB episode GAP-FILL — TMDB is often slow on just-aired / reality-TV episode
        # overviews + titles; TVDB frequently has them first. backfill_episodes is COALESCE
        # gap-fill, so this only fills what TMDB left blank and never clobbers.
        self._cascade_tvdb_episodes(show_id, info.get("tvdb_id"), nums)
        if with_ratings:
            self._backfill_ratings("show", show_id)
        return {"ok": True}

    def _cascade_tvdb_episodes(self, show_id, tvdb_id, season_nums) -> None:
        """Fill episode overviews/titles/stills TMDB lacked from TVDB (best-effort, gap-only)."""
        tw = self.workers.get("tvdb")
        if not tvdb_id or not tw or not tw.enabled:
            return
        for sn in season_nums or []:
            try:
                eps = tw.client.season_episodes(tvdb_id, sn) or []
            except Exception:   # noqa: BLE001 - one bad season shouldn't abort the refresh
                logger.debug("tvdb season fetch failed (%s S%s)", tvdb_id, sn, exc_info=True)
                continue
            if eps:
                try:
                    self.db.backfill_episodes(show_id, sn, eps)
                except Exception:   # noqa: BLE001
                    logger.debug("tvdb episode backfill failed (show %s S%s)", show_id, sn, exc_info=True)

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
        return self._stamp_owned(results)

    def trending(self, window="week", kind=None) -> list:
        """Trending titles, annotated owned/not. ``window='day'`` is the real-time
        daily chart (the iconic 'Top 10 today' rows); 'week' is the steadier hero/idle
        feed. ``kind`` None = mixed; 'movie'/'show' = the dedicated single-type charts
        (split 'Top 10 Movies / TV Shows Today'). Order is preserved for rank numbers."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        window = "day" if window == "day" else "week"
        kind = kind if kind in ("movie", "show") else None
        ck = ("trending", window, kind)
        cached = self._cache_get(ck)
        if cached is None:
            try:
                cached = w.client.trending(window=window, kind=kind) or []
                self._cache_put(ck, cached, ttl=3600)
            except Exception:
                logger.exception("video trending failed")
                return []
        # Re-annotate ownership fresh each call (batched) so it tracks the library.
        return self._stamp_owned(cached)

    # ── discover (browse TMDB lists; owned titles annotated) ──────────────────
    def _stamp_owned(self, items):
        """Annotate each movie/show with its library_id for the active server —
        stamped fresh (not cached with ownership) so 'In Library' tracks scans.
        Batched: one query per kind for the whole list, not one per item."""
        srv = self._server()
        by_kind: dict = {}
        for r in items or []:
            if r.get("kind") in ("movie", "show") and r.get("tmdb_id"):
                by_kind.setdefault(r["kind"], []).append(r["tmdb_id"])
        maps = {k: self.db.library_ids_for_tmdb(k, ids, srv) for k, ids in by_kind.items()}
        for r in items or []:
            if r.get("kind") in ("movie", "show") and r.get("tmdb_id"):
                try:
                    r["library_id"] = maps.get(r["kind"], {}).get(int(r["tmdb_id"]))
                except (TypeError, ValueError):
                    r["library_id"] = None
        # Drop titles the user marked 'Not interested' — one tiny indexed query, always
        # fresh, so every discover surface (rails, recs, collection gaps) hides them uniformly.
        try:
            ignored = self.db.ignored_keys()
        except Exception:
            ignored = None
        if ignored:
            items = [r for r in (items or [])
                     if f"{r.get('kind')}:{r.get('tmdb_id')}" not in ignored]
        return items

    def discover_curated(self, key, page=1) -> list:
        """A canned TMDB list (popular / top-rated / now-playing / upcoming /
        on-the-air / airing-today), cached then owned-annotated."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        ck = ("disc-cur", key, page)
        items = self._cache_get(ck)
        if items is None:
            try:
                items = w.client.curated(key, page=page) or []
                self._cache_put(ck, items, ttl=3600)
            except Exception:
                logger.exception("discover curated failed (%s p%s)", key, page)
                return []
        return self._stamp_owned(items)

    def discover_filter(self, kind, *, genre=None, year=None, decade=None, providers=None,
                        sort_by="popularity.desc", page=1, language=None,
                        keywords=None, companies=None, networks=None, cast=None, crew=None,
                        min_runtime=None, max_runtime=None, certification=None,
                        vote_count_min=None, release_window=None) -> list:
        """Browse /discover filtered by genre / year / decade / streaming provider /
        original language — plus the Netflix-class extensions (keywords/mood,
        companies/studio, networks, cast/crew, runtime, certification, release window).
        Cached + owned-annotated; all extension params are optional/additive."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        if kind not in ("movie", "show"):
            kind = "movie"
        ck = ("disc-flt", kind, genre, year, decade, providers, sort_by, page, language,
              keywords, companies, networks, cast, crew, min_runtime, max_runtime,
              certification, vote_count_min, release_window)
        items = self._cache_get(ck)
        if items is None:
            try:
                items = w.client.discover(
                    kind, genre=genre, year=year, decade=decade, providers=providers,
                    sort_by=sort_by, page=page, region=self._region(), language=language,
                    keywords=keywords, companies=companies, networks=networks, cast=cast,
                    crew=crew, min_runtime=min_runtime, max_runtime=max_runtime,
                    certification=certification, vote_count_min=vote_count_min,
                    release_window=release_window) or []
                self._cache_put(ck, items, ttl=3600)
            except Exception:
                logger.exception("discover filter failed (%s g=%s y=%s d=%s p=%s)",
                                 kind, genre, year, decade, providers)
                return []
        return self._stamp_owned(items)

    def genre_list(self, kind) -> list:
        """TMDB genre id→name list (long-cached — these barely change)."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        if kind not in ("movie", "show"):
            kind = "movie"
        ck = ("genres", kind)
        items = self._cache_get(ck)
        if items is None:
            try:
                items = w.client.genres(kind) or []
                self._cache_put(ck, items, ttl=86400)
            except Exception:
                logger.exception("genre list failed (%s)", kind)
                return []
        return items

    def collection_poster(self, collection_id) -> str | None:
        """A TMDB collection's own poster URL (real title art), day-cached."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not collection_id:
            return None
        ck = ("colposter", int(collection_id))
        hit = self._cache_get(ck)
        if hit is None:
            try:
                info = w.client.collection_info(collection_id) or {}
                hit = info.get("poster_url") or ""   # "" = cached miss
                self._cache_put(ck, hit, ttl=86400)
            except Exception:
                logger.exception("collection poster lookup failed (%s)", collection_id)
                return None
        return hit or None

    def person_photo(self, name) -> str | None:
        """A person's TMDB profile photo URL by name, day-cached."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not (name or "").strip():
            return None
        ck = ("personphoto", name.strip().lower())
        hit = self._cache_get(ck)
        if hit is None:
            try:
                hit = w.client.person_photo(name) or ""   # "" = cached miss
                self._cache_put(ck, hit, ttl=86400)
            except Exception:
                logger.exception("person photo lookup failed (%s)", name)
                return None
        return hit or None

    def title_logo(self, kind, tmdb_id) -> str | None:
        """A title's logo/wordmark art for the Discover hero — day-cached
        ('' = cached miss so a logo-less title doesn't re-fetch every load)."""
        if not tmdb_id:
            return None
        try:
            ck = ("titlelogo", kind, int(tmdb_id))
        except (TypeError, ValueError):
            return None
        hit = self._cache_get(ck)
        if hit is None:
            w = self.workers.get("tmdb")
            hit = ""
            if w and getattr(w, "enabled", False) and hasattr(w.client, "title_logo"):
                try:
                    hit = w.client.title_logo(kind, tmdb_id) or ""
                except Exception:   # noqa: BLE001 - hero chrome must never break the page
                    logger.debug("title logo lookup failed (%s %s)", kind, tmdb_id, exc_info=True)
            self._cache_put(ck, hit, ttl=86400)
        return hit or None

    def company_logo(self, name) -> str | None:
        """A studio's TMDB logo URL by name, day-cached."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not (name or "").strip():
            return None
        ck = ("companylogo", name.strip().lower())
        hit = self._cache_get(ck)
        if hit is None:
            try:
                hit = w.client.company_logo(name) or ""   # "" = cached miss
                self._cache_put(ck, hit, ttl=86400)
            except Exception:
                logger.exception("company logo lookup failed (%s)", name)
                return None
        return hit or None

    def _imdb_map(self, imdb_id) -> dict | None:
        """The full tt→TMDB record. The mapping never changes, so it cascades
        proc cache → PERSISTED map (imdb_tmdb_map) → the library's own rows
        (an owned chart title maps with zero network) → one /find call, which
        is then persisted — an IMDb chart costs its lookups exactly once ever."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not imdb_id:
            return None
        ck = ("imdbmap", imdb_id)
        hit = self._cache_get(ck)
        if hit is not None:
            return hit
        try:
            hit = self.db.get_imdb_tmdb(imdb_id)
        except Exception:   # noqa: BLE001 - persistence is an optimization
            hit = None
        if hit is None:
            try:
                lib = self.db.tmdb_by_library_imdb(imdb_id)
            except Exception:   # noqa: BLE001
                lib = {"movie": None, "show": None}
            if lib.get("movie") or lib.get("show"):
                hit = lib
            else:
                try:
                    hit = w.client.find_by_imdb(imdb_id) or {}
                except Exception:
                    logger.exception("imdb find failed (%s)", imdb_id)
                    return None
            try:
                self.db.put_imdb_tmdb(imdb_id, hit.get("movie"), hit.get("show"),
                                      movie_poster=hit.get("movie_poster"),
                                      show_poster=hit.get("show_poster"))
            except Exception:   # noqa: BLE001
                pass
        self._cache_put(ck, hit, ttl=86400)
        return hit

    def tmdb_from_imdb(self, imdb_id, kind) -> int | None:
        """TMDB id for an IMDb tt-id (see _imdb_map for the lookup cascade)."""
        hit = self._imdb_map(imdb_id)
        return (hit or {}).get("show" if kind == "show" else "movie")

    def imdb_poster(self, imdb_id, kind) -> str | None:
        """TMDB poster URL for an IMDb tt-id — gives IMDb chart/list entries
        real art in the missing browser (IMDb's GraphQL carries none)."""
        hit = self._imdb_map(imdb_id)
        return (hit or {}).get("show_poster" if kind == "show" else "movie_poster")

    def keyword_id(self, query) -> int | None:
        """TMDB keyword id for a name (day-cached — keyword ids never move).
        Powers the seasonal/themed collection sources."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not (query or "").strip():
            return None
        ck = ("kwid", query.strip().lower())
        hit = self._cache_get(ck)
        if hit is None:
            try:
                hit = w.client.keyword_search(query) or 0   # 0 = cached miss
                self._cache_put(ck, hit, ttl=86400)
            except Exception:
                logger.exception("keyword search failed (%s)", query)
                return None
        return hit or None

    def list_page(self, list_id, page=1) -> tuple:
        """One page of a public TMDB list — (items, total_pages), cached."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not list_id:
            return [], 0
        ck = ("list", str(list_id), page)
        hit = self._cache_get(ck)
        if hit is None:
            try:
                hit = w.client.list_items(list_id, page=page)
                self._cache_put(ck, hit, ttl=3600)
            except Exception:
                logger.exception("tmdb list fetch failed (%s p%s)", list_id, page)
                return [], 0
        return hit

    def recommendations(self, kind, tmdb_id, page=1) -> list:
        """'More like this' titles for a tmdb id — cached + owned-annotated."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not tmdb_id:
            return []
        ck = ("recs", kind, tmdb_id, page)
        items = self._cache_get(ck)
        if items is None:
            try:
                items = w.client.recommendations(kind, tmdb_id, page=page) or []
                self._cache_put(ck, items, ttl=3600)
            except Exception:
                logger.exception("recommendations failed (%s %s)", kind, tmdb_id)
                return []
        return self._stamp_owned(items)

    def collection(self, collection_id) -> list:
        """The films of a TMDB collection (franchise) — cached + owned-annotated.
        Drives the 'complete your collections' gap rails."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not collection_id:
            return []
        ck = ("collection", collection_id)
        items = self._cache_get(ck)
        if items is None:
            try:
                items = w.client.collection(collection_id) or []
                self._cache_put(ck, items, ttl=86400)   # franchises rarely change
            except Exception:
                logger.exception("collection failed (%s)", collection_id)
                return []
        return self._stamp_owned(items)

    def movie_collection(self, tmdb_id) -> dict | None:
        """A movie's TMDB franchise membership {id, name} (belongs_to_collection), or
        {id: None} when it belongs to no collection. Used to backfill already-matched
        movies that predate the collection column."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not tmdb_id:
            return None
        try:
            meta = w.client.match("movie", None, None, known_id=tmdb_id) or {}
            # match() nests everything under 'metadata' — read the collection fields THERE,
            # not at the top level (reading the top level returned None, so every franchise
            # backfilled as id 0 → whole franchises like Jurassic Park never surfaced).
            md = meta.get("metadata") or {}
            return {"id": md.get("tmdb_collection_id"), "name": md.get("tmdb_collection_name")}
        except Exception:
            logger.exception("movie_collection backfill failed for %s", tmdb_id)
            return None

    def trailer(self, kind, tmdb_id) -> dict | None:
        """Best YouTube trailer for a title (cached a day — trailers don't move)."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not tmdb_id:
            return None
        ck = ("trailer", kind, tmdb_id)
        cached = self._cache_get(ck)
        if cached is None:
            try:
                cached = w.client.video_trailer(kind, tmdb_id) or {}
                self._cache_put(ck, cached, ttl=86400)
            except Exception:
                logger.exception("trailer failed (%s %s)", kind, tmdb_id)
                return None
        return cached or None

    def movie_available_date(self, tmdb_id) -> str | None:
        """The date a downloadable (home/digital) copy of a movie is expected — Radarr-style
        'released' availability, so the wishlist drain skips films still only in cinemas.
        Cached a day; None when TMDB has no release-date data at all."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled or not tmdb_id:
            return None
        ck = ("avail_date", tmdb_id)
        cached = self._cache_get(ck)
        if cached is None:
            try:
                from core.video.release_availability import available_date
                cached = available_date(w.client.movie_release_dates(tmdb_id)) or ""
                self._cache_put(ck, cached, ttl=86400)
            except Exception:
                logger.exception("movie_available_date failed for %s", tmdb_id)
                return None
        return cached or None

    def tmdb_full_detail(self, kind, tmdb_id) -> dict | None:
        """Raw TMDB full detail (absolute image URLs + metadata) WITHOUT the
        owned→library redirect — for sidecar / NFO writing, which needs the data even
        for titles already in the library."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        try:
            return w.client.full_detail(kind, tmdb_id, region=self._region())
        except Exception:
            logger.exception("tmdb_full_detail failed for %s %s", kind, tmdb_id)
            return None

    def tmdb_detail(self, kind, tmdb_id) -> dict | None:
        """Full detail for a TMDB title not in the library — same shape as the
        library detail (source='tmdb', direct image URLs, nothing owned). If it IS
        in the library, returns a redirect to the owned detail instead."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        lib_id = self.db.library_id_for_tmdb(kind, tmdb_id, self._server())
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
                  "studios": ex.get("studios") or [],
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

    def poster_options(self, kind, tmdb_id) -> list:
        """Candidate posters for the poster manager — TMDB's poster art for a title,
        cached (it doesn't change often). Empty list if TMDB isn't configured."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        cached = self._cache_get(("posters", kind, tmdb_id))
        if cached is not None:
            return list(cached)
        try:
            out = w.client.poster_options(kind, tmdb_id) or []
        except Exception:
            logger.exception("poster_options failed for %s %s", kind, tmdb_id)
            return []
        self._cache_put(("posters", kind, tmdb_id), out)
        return out

    def _fill_tmdb_ratings(self, d) -> None:
        imdb_id = d.get("imdb_id")
        ow = self.workers.get("omdb")
        # share the same daily-limit latch as _backfill_ratings — once OMDb is over quota,
        # every detail fetch would otherwise re-hit it and dump a traceback per title.
        if (not imdb_id or not ow or not getattr(ow.client, "enabled", False)
                or self._omdb_blocked_now()):
            return
        try:
            r = ow.client.ratings(imdb_id) or {}
            for k in ("imdb_rating", "rt_rating", "metacritic"):
                if r.get(k) is not None:
                    d[k] = r[k]
        except OMDbAuthError as e:
            self._trip_omdb_latch(e)
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
        srv = self._server()
        for c in p.get("credits") or []:
            if c.get("tmdb_id"):
                c["library_id"] = self.db.library_id_for_tmdb(c["kind"], c["tmdb_id"], srv)
        return p

    @staticmethod
    def _norm_company(s) -> str:
        """Lowercased alphanumerics only — so 'A 24 Studios' → 'a24studios' matches 'a24'."""
        return "".join(ch for ch in str(s or "").lower() if ch.isalnum())

    def company_search(self, query, *, limit=10) -> list:
        """TMDB studio search for the in-app Studios search. TMDB's /search/company is fuzzy
        (a query of 'a24' also returns N24 / A2O / B24 / A26 …), so we (1) keep only results
        whose normalized name actually contains the query (or vice-versa) to drop the junk,
        (2) attach each survivor's movie count in parallel, (3) drop empty (0-film) shells you
        couldn't follow anyway, and (4) sort by count so the real studio (A24, 177 films) leads
        and the UI can show '177 films' to disambiguate genuine namesakes."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        try:
            raw = w.client.search_companies(query) or []
        except Exception:
            logger.exception("company_search failed")
            return []
        # Relevance filter first (before the expensive count fetch), so 'a24' stops matching
        # 'N24'/'A2O'/'B24'. A genuinely-named namesake ('A24' with 1 film) still matches and
        # is kept — disambiguation is by film count, not by hiding real namesakes.
        qn = self._norm_company(query)
        if qn:
            raw = [c for c in raw
                   if (lambda n: n and (qn in n or n in qn))(self._norm_company(c.get("title")))]
        raw = raw[:limit]
        if not raw:
            return []
        from concurrent.futures import ThreadPoolExecutor

        def _count(c):
            try:
                return w.client.company_movies(c["tmdb_id"], page=1).get("total_results") or 0
            except Exception:   # noqa: BLE001 - a count hiccup just means 'unknown', not fatal
                return 0
        try:
            with ThreadPoolExecutor(max_workers=min(8, len(raw))) as ex:
                for c, n in zip(raw, ex.map(_count, raw), strict=True):   # map() is 1:1 with raw
                    c["movie_count"] = n
            raw = [c for c in raw if (c.get("movie_count") or 0) > 0]   # no films → nothing to follow
            raw.sort(key=lambda c: c.get("movie_count") or 0, reverse=True)
        except Exception:   # noqa: BLE001 - unranked results are still usable
            logger.debug("company count enrichment failed", exc_info=True)
        return raw

    def company_detail(self, company_id) -> dict | None:
        """A studio's TMDB detail (name/logo/description/HQ). Cached a day."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return None
        ck = ("company", company_id)
        cached = self._cache_get(ck)
        if cached is None:
            try:
                cached = w.client.company(company_id) or {}
                self._cache_put(ck, cached, ttl=86400)
            except Exception:
                logger.exception("company_detail failed for %s", company_id)
                return None
        return cached or None

    def company_movies(self, company_id, *, page=1, sort="primary_release_date.desc") -> dict:
        """A studio's movies (paged), each annotated with the library id if owned so the grid
        marks owned copies + opens them in-app."""
        empty = {"results": [], "page": 1, "total_pages": 0, "total_results": 0}
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return empty
        try:
            out = w.client.company_movies(company_id, page=page, sort=sort)
        except Exception:
            logger.exception("company_movies failed for %s", company_id)
            return empty
        rows = out.get("results") or []
        owned = self.db.library_ids_for_tmdb(   # one batched query for the whole page, not N
            "movie", [m["tmdb_id"] for m in rows if m.get("tmdb_id")], self._server())
        for m in rows:
            m["library_id"] = owned.get(m.get("tmdb_id"))
        return out

    def company_films(self, company_id, *, max_pages=10,
                      sort="primary_release_date.desc") -> list:
        """A studio's catalog as a flat film list (newest first), for the studio-watchlist
        scan. Pages through /discover up to ``max_pages`` (TMDB caps discover at 500 pages /
        20 per page); logs when a big catalog is truncated so a silent cap can't read as
        'scanned everything'. No ownership annotation — the scan diffs against owned itself."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return []
        films: list = []
        total_pages = 1
        page = 1
        while page <= max_pages:
            try:
                out = w.client.company_movies(company_id, page=page, sort=sort)
            except Exception:
                logger.exception("company_films page %s failed for %s", page, company_id)
                break
            films.extend(out.get("results") or [])
            total_pages = out.get("total_pages") or 1
            if page >= total_pages:
                break
            page += 1
        if total_pages > max_pages:
            logger.info("company_films: studio %s has %s pages; scanned the newest %s (%s films)",
                        company_id, total_pages, max_pages, len(films))
        return films

    def _server(self):
        """Active video server — scopes ownership lookups so an item owned only on
        the inactive server doesn't read as owned (Plex/Jellyfin stay separate)."""
        try:
            from core.video.sources import resolve_video_server
            return resolve_video_server(self.db)
        except Exception:
            return None

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
