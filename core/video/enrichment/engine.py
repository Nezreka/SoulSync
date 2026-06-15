"""Video enrichment engine — owns the per-source workers (registry).

Parallels music's enrichment registry but is isolated to the video side. Built
lazily as a process-wide singleton; starts the workers (each idles until its API
key is configured). Imports only video.db + this package.
"""

from __future__ import annotations

import threading

from utils.logging_config import get_logger

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
        # Restore each worker's persisted pause state (survives restart).
        for w in self.workers.values():
            w.restore_paused()

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
        page. Not cached — fetched per view so providers stay current."""
        w = self.workers.get("tmdb")
        if not w or not w.enabled:
            return {}
        info = (self.db.movie_match_info(item_id) if kind == "movie"
                else self.db.show_match_info(item_id))
        if not info or not info.get("tmdb_id"):
            return {}
        try:
            return w.client.extras(kind, info["tmdb_id"]) or {}
        except Exception:
            logger.exception("item_extras failed for %s %s", kind, item_id)
            return {}

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
