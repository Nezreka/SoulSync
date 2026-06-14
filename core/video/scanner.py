"""SoulSync — video library scanner.

The media SERVER (Plex/Jellyfin) is the source of truth, exactly like the music
side: we ask the server what it has and mirror it into video.db. This module is
server-agnostic — it consumes a "video media source" (duck-typed) that yields
normalized dicts, so it never touches a media-server SDK directly. The Plex /
Jellyfin adapters live in core/video/sources.py.

A source must provide:
    source.server_name -> 'plex' | 'jellyfin'
    source.iter_movies() -> iterable of normalized movie dicts
    source.iter_shows()  -> iterable of normalized show dicts (with seasons/episodes)

ISOLATION: imports only video.db + shared infra; music never imports this.
"""

from __future__ import annotations

import threading
import time

from utils.logging_config import get_logger

logger = get_logger("video_scanner")


class VideoLibraryScanner:
    """Reads the active media server and upserts movies/shows into video.db."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self._status = {"state": "idle"}
        self._thread = None

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **kw) -> None:
        with self._lock:
            self._status.update(kw)

    def request_scan(self, source_factory) -> dict:
        """Kick off a background scan. ``source_factory()`` returns a media
        source (or None if no video-capable server is connected)."""
        with self._lock:
            if self._status.get("state") == "scanning":
                return {"status": "in_progress"}
            self._status = {"state": "scanning", "phase": "starting",
                            "started_at": time.time(),
                            "movies": 0, "shows": 0, "episodes": 0}
        self._thread = threading.Thread(
            target=self._run, args=(source_factory,), daemon=True)
        self._thread.start()
        return {"status": "started"}

    def scan_sync(self, source_factory) -> dict:
        """Run a scan inline (used by tests / callers that want to block)."""
        with self._lock:
            self._status = {"state": "scanning", "phase": "starting",
                            "started_at": time.time(),
                            "movies": 0, "shows": 0, "episodes": 0}
        self._run(source_factory)
        return self.get_status()

    def _run(self, source_factory) -> None:
        try:
            source = source_factory()
            if source is None:
                self._set(state="error", phase="no video server",
                          error="No connected Plex/Jellyfin video server")
                return
            server = source.server_name

            # ── Movies ──
            self._set(phase="scanning movies")
            seen_movies: set[str] = set()
            movies = 0
            for item in source.iter_movies():
                self.db.upsert_movie(server, item)
                seen_movies.add(str(item["server_id"]))
                movies += 1
                if movies % 25 == 0:
                    self._set(movies=movies)
            self._set(movies=movies)
            # Prune only when we actually saw items — avoids wiping the library
            # if the server returned nothing due to a transient failure.
            removed_m = self.db.prune_missing("movies", server, seen_movies) if seen_movies else 0

            # ── Shows ──
            self._set(phase="scanning shows")
            seen_shows: set[str] = set()
            shows = 0
            episodes = 0
            for show in source.iter_shows():
                self.db.upsert_show_tree(server, show)
                seen_shows.add(str(show["server_id"]))
                shows += 1
                episodes += sum(len(s.get("episodes", [])) for s in show.get("seasons", []))
                self._set(shows=shows, episodes=episodes)
            removed_s = self.db.prune_missing("shows", server, seen_shows) if seen_shows else 0

            self._set(state="done", phase="complete", finished_at=time.time(),
                      movies=movies, shows=shows, episodes=episodes,
                      removed=removed_m + removed_s)
            logger.info("Video scan complete: %d movies, %d shows, %d episodes (%d pruned)",
                        movies, shows, episodes, removed_m + removed_s)
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Video library scan failed")
            self._set(state="error", phase="failed", error=str(e))


# Module-level singleton, bound to the (single) video DB.
_scanner = None
_scanner_lock = threading.Lock()


def get_video_scanner(db) -> VideoLibraryScanner:
    global _scanner
    if _scanner is None:
        with _scanner_lock:
            if _scanner is None:
                _scanner = VideoLibraryScanner(db)
    return _scanner
