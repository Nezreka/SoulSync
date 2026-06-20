"""SoulSync — video library scanner.

The media SERVER (Plex/Jellyfin) is the source of truth, exactly like the music
side: we ask the server what it has and mirror it into video.db. This module is
server-agnostic — it consumes a "video media source" (duck-typed) that yields
normalized dicts, so it never touches a media-server SDK directly. The Plex /
Jellyfin adapters live in core/video/sources.py.

A source must provide:
    source.server_name -> 'plex' | 'jellyfin'
    source.iter_movies(incremental=False) -> iterable of normalized movie dicts
    source.iter_shows(incremental=False)  -> iterable of normalized show dicts

Scan MODES (mirroring the music side's full_refresh / incremental / deep_scan):
    'incremental' - only recently-added items from the server; upsert; no prune.
    'full'        - every item; upsert all (refresh metadata + add new); no prune.
    'deep'        - every item; upsert; PRUNE what the server no longer has.

ISOLATION: imports only video.db + shared infra; music never imports this.
"""

from __future__ import annotations

import threading
import time

from utils.logging_config import get_logger

logger = get_logger("video_scanner")

VALID_MODES = ("incremental", "full", "deep")

# Incremental stops after this many consecutive already-known items (recent
# first), mirroring music's "25 consecutive complete albums" early-stop.
INCREMENTAL_STOP_AFTER = 25
# Below this library size, an incremental scan falls back to a full pass (music
# does the same when the DB is too small to be worth an incremental).
INCREMENTAL_MIN_LIBRARY = 50


class VideoLibraryScanner:
    """Reads the active media server and upserts movies/shows into video.db."""

    def __init__(self, db, pause_workers=None, resume_workers=None):
        self.db = db
        self._lock = threading.Lock()
        self._status = {"state": "idle"}
        self._thread = None
        self._cancel = False
        # Optional hooks: pause enrichment workers while a scan runs, resume after
        # (injected by get_video_scanner; left None in tests so no engine spins up).
        self._pause_workers = pause_workers
        self._resume_workers = resume_workers

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **kw) -> None:
        with self._lock:
            self._status.update(kw)

    def cancel(self) -> dict:
        """Request the running scan to stop after the current item."""
        with self._lock:
            if self._status.get("state") == "scanning":
                self._cancel = True
                self._status["phase"] = "cancelling"
                return {"status": "cancelling"}
        return {"status": "idle"}

    @staticmethod
    def _norm_mode(mode) -> str:
        return mode if mode in VALID_MODES else "full"

    def request_scan(self, source_factory, mode: str = "full") -> dict:
        """Kick off a background scan. ``source_factory()`` returns a media
        source (or None if no video-capable server is connected)."""
        mode = self._norm_mode(mode)
        with self._lock:
            if self._status.get("state") == "scanning":
                return {"status": "in_progress"}
            self._cancel = False
            self._status = {"state": "scanning", "phase": "starting", "mode": mode,
                            "started_at": time.time(), "percent": None,
                            "movies": 0, "shows": 0, "episodes": 0}
        self._thread = threading.Thread(
            target=self._run, args=(source_factory, mode), daemon=True)
        self._thread.start()
        return {"status": "started", "mode": mode}

    def scan_sync(self, source_factory, mode: str = "full") -> dict:
        """Run a scan inline (used by tests / callers that want to block)."""
        mode = self._norm_mode(mode)
        with self._lock:
            self._cancel = False
            self._status = {"state": "scanning", "phase": "starting", "mode": mode,
                            "started_at": time.time(), "percent": None,
                            "movies": 0, "shows": 0, "episodes": 0}
        self._run(source_factory, mode)
        return self.get_status()

    def _finish_cancelled(self, movies, shows, episodes) -> None:
        self._set(state="cancelled", phase="cancelled", finished_at=time.time(),
                  movies=movies, shows=shows, episodes=episodes)
        logger.info("Video scan cancelled at %d movies, %d shows", movies, shows)

    def _pause_for_scan(self) -> bool:
        """Pause enrichment workers for the duration of the scan. Best-effort —
        a failure here must never abort the scan."""
        if not self._pause_workers:
            return False
        try:
            self._pause_workers()
            return True
        except Exception:
            logger.debug("video scan: pausing enrichment workers failed", exc_info=True)
            return False

    def _resume_after_scan(self) -> None:
        if not self._resume_workers:
            return
        try:
            self._resume_workers()
        except Exception:
            logger.debug("video scan: resuming enrichment workers failed", exc_info=True)

    def _run(self, source_factory, mode: str = "full") -> None:
        # Enrichment steps aside for the scan (all modes, both entry points), and
        # the finally guarantees it resumes on success, cancel, or error.
        paused = self._pause_for_scan()
        try:
            source = source_factory()
            if source is None:
                self._set(state="error", phase="no video server",
                          error="No connected Plex/Jellyfin video server")
                return
            server = source.server_name
            incremental = mode == "incremental"
            do_prune = mode == "deep"

            # Incremental on a near-empty library is pointless — fall back to a
            # full pass so the first scan actually populates (music does this).
            if incremental and (self.db.table_count("movies") + self.db.table_count("shows")) < INCREMENTAL_MIN_LIBRARY:
                incremental = False

            # Totals up front so the progress bar shows a REAL percentage
            # (movies + shows are the unit; episodes ride along under each show).
            total = 0
            try:
                c = source.counts(incremental=incremental) or {}
                total = int(c.get("movies", 0) or 0) + int(c.get("shows", 0) or 0)
            except Exception:
                logger.debug("video scan: counts() unavailable; progress will be indeterminate")
            processed = 0

            def pct():
                return round(processed / total * 100) if total else None

            known_movies = self.db.server_ids("movies", server) if incremental else set()
            known_shows = self.db.server_ids("shows", server) if incremental else set()

            # ── Movies ──
            self._set(phase="scanning movies", total=total, percent=pct())
            seen_movies: set[str] = set()
            movies = 0
            consec = 0
            for item in source.iter_movies(incremental=incremental):
                if self._cancel:
                    return self._finish_cancelled(movies, 0, 0)
                sid = str(item["server_id"])
                # Incremental early-stop: skip already-known items and bail after
                # a run of consecutive known ones (server lists recent first).
                if incremental and sid in known_movies:
                    consec += 1
                    if consec >= INCREMENTAL_STOP_AFTER:
                        break
                    continue
                consec = 0
                try:
                    self.db.upsert_movie(server, item)
                except Exception:
                    logger.exception("video scan: skipping movie %s", sid)
                    continue
                seen_movies.add(sid)
                movies += 1
                processed += 1
                if movies % 10 == 0:
                    self._set(movies=movies, percent=pct())
            self._set(movies=movies, percent=pct())
            # Prune ONLY on a deep scan, and only when we actually saw items —
            # so a transient empty response can never wipe the library. The prune
            # runs AFTER the bar fills, and a big cleanup (many orphaned rows +
            # cascades) takes a few seconds — surface a phase so the UI shows
            # "cleaning up", not a stuck 100%.
            if do_prune and seen_movies:
                self._set(phase="cleaning up removed movies", percent=pct())
            removed_m = (self.db.prune_missing("movies", server, seen_movies)
                         if do_prune and seen_movies else 0)

            # ── Shows ──
            self._set(phase="scanning shows")
            seen_shows: set[str] = set()
            shows = 0
            episodes = 0
            consec = 0
            for show in source.iter_shows(incremental=incremental):
                if self._cancel:
                    return self._finish_cancelled(movies, shows, episodes)
                sid = str(show["server_id"])
                if incremental and sid in known_shows:
                    consec += 1
                    if consec >= INCREMENTAL_STOP_AFTER:
                        break
                    continue
                consec = 0
                try:
                    self.db.upsert_show_tree(server, show)
                except Exception:
                    logger.exception("video scan: skipping show %s", sid)
                    continue
                seen_shows.add(sid)
                shows += 1
                episodes += sum(len(s.get("episodes", [])) for s in show.get("seasons", []))
                processed += 1
                self._set(shows=shows, episodes=episodes, percent=pct())
            # Final prune (the one that delays "done" on a deep scan) — show it.
            if do_prune and seen_shows:
                self._set(phase="cleaning up removed shows", percent=100)
            removed_s = (self.db.prune_missing("shows", server, seen_shows)
                         if do_prune and seen_shows else 0)

            self._set(state="done", phase="complete", finished_at=time.time(),
                      movies=movies, shows=shows, episodes=episodes, percent=100,
                      removed=removed_m + removed_s)
            logger.info("Video scan (%s) complete: %d movies, %d shows, %d episodes (%d pruned)",
                        mode, movies, shows, episodes, removed_m + removed_s)
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Video library scan failed")
            self._set(state="error", phase="failed", error=str(e))
        finally:
            if paused:
                self._resume_after_scan()


# Module-level singleton, bound to the (single) video DB.
_scanner = None
_scanner_lock = threading.Lock()


def _engine_pause_for_scan() -> None:
    from core.video.enrichment.engine import get_video_enrichment_engine
    get_video_enrichment_engine().pause_for_scan()


def _engine_resume_after_scan() -> None:
    from core.video.enrichment.engine import get_video_enrichment_engine
    get_video_enrichment_engine().resume_after_scan()


def get_video_scanner(db) -> VideoLibraryScanner:
    global _scanner
    if _scanner is None:
        with _scanner_lock:
            if _scanner is None:
                _scanner = VideoLibraryScanner(
                    db, pause_workers=_engine_pause_for_scan,
                    resume_workers=_engine_resume_after_scan)
    return _scanner
