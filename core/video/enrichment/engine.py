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

_DISPLAY = {"tmdb": "TMDB", "tvdb": "TVDB"}


class VideoEnrichmentEngine:
    def __init__(self, db, clients: dict):
        self.db = db
        self.workers = {
            service: VideoEnrichmentWorker(db, service, client, display_name=_DISPLAY.get(service))
            for service, client in clients.items()
        }
        # Restore each worker's persisted pause state (survives restart).
        for w in self.workers.values():
            w.restore_paused()

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
                from .clients import build_clients
                db = VideoDatabase()
                eng = VideoEnrichmentEngine(db, build_clients(db))
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
