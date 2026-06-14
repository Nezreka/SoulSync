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

    def start_all(self):
        for w in self.workers.values():
            w.start()

    def stop_all(self):
        for w in self.workers.values():
            w.stop()

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
