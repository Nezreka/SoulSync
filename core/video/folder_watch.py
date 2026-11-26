"""soulsync's video folders, watched.

with SoulSync Standalone (core/video/disk_source.py) a file a person drops
into a Movies or TV folder shows in the library within a minute, the way a
media server notices it, instead of at the next scheduled scan.

two ways, the first that works:
* the filesystem tells us (watchdog): a change under a library folder, once
  things have been quiet for a moment (a copy in progress keeps changing),
  asks the scanner for an incremental scan;
* a folder that can't be watched (many network shares) is checked on a
  timer instead.

only while soulsync's own scan is the library (the video source is
'soulsync'); with plex or jellyfin the server does the noticing. downloads
never need it: they're written into the library as they land.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video_folder_watch")

QUIET_SEC = 20          # no change for this long = the copy is done
POLL_SEC = 15 * 60      # folders that can't be watched
CHECK_SEC = 60          # how often the watch list follows the settings

_started = False
_start_lock = threading.Lock()


class Debounce:
    """collects changes and fires once they've gone quiet. the clock is
    passed in so the rule can be tested without waiting."""

    def __init__(self, quiet: float = QUIET_SEC):
        self.quiet = quiet
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def touch(self, now: float) -> None:
        with self._lock:
            self._last = now

    def due(self, now: float) -> bool:
        """True once, when something changed and it has been quiet since"""
        with self._lock:
            if self._last is None or now - self._last < self.quiet:
                return False
            self._last = None
            return True


def is_library_change(path: str) -> bool:
    """a change worth a scan: a video, a .nfo or a folder. a partial download
    ('x.mkv.part') and sidecar writes (posters, subtitles) are not: only a
    finished video's own extension counts."""
    from core.video.importer import is_video
    p = str(path or "")
    name = p.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not name or name.startswith("."):
        return False
    if "." not in name:
        return True                      # a folder made, moved or removed
    return is_video(name) or name.endswith(".nfo")


class VideoFolderWatch:
    def __init__(self, db_provider: Callable, *, request_scan: Optional[Callable] = None,
                 observer_factory: Optional[Callable] = None, clock: Callable = time.time):
        self._db = db_provider
        self._request_scan = request_scan or self._scan
        self._observer_factory = observer_factory
        self._clock = clock
        self.debounce = Debounce()
        self._observer = None
        self._watched: List[str] = []
        self._unwatched: List[str] = []
        self._last_poll = clock()

    # ── what to watch ────────────────────────────────────────────────────────
    def roots(self) -> List[str]:
        """the folders to watch now: soulsync's own, and only while its scan
        is the library"""
        db = self._db()
        if db is None:
            return []
        try:
            from core.video.sources import resolve_video_server
            if resolve_video_server(db) != "soulsync":
                return []
            from core.video.library_roots import library_roots
            return list(dict.fromkeys(library_roots(db, "movie") + library_roots(db, "episode")))
        except Exception:   # noqa: BLE001
            logger.debug("folder watch: couldn't read the folders", exc_info=True)
            return []

    def _make_observer(self):
        if self._observer_factory is not None:
            return self._observer_factory()
        from watchdog.observers import Observer
        return Observer()

    def sync(self) -> None:
        """follow the settings: watch what should be watched, drop the rest"""
        want = self.roots()
        if sorted(want) == sorted(self._watched + self._unwatched):
            return
        self.stop()
        watched, unwatched = [], []
        if want:
            try:
                observer = self._make_observer()
            except Exception:   # noqa: BLE001 - no watchdog: everything is polled
                logger.info("video folder watch: no filesystem watcher here, checking on a timer")
                observer = None
            for root in want:
                ok = False
                if observer is not None:
                    try:
                        observer.schedule(_Handler(self), root, recursive=True)
                        ok = True
                    except Exception:   # noqa: BLE001 - a share that can't be watched
                        logger.info("video folder watch: %s can't be watched, checking it on a timer", root)
                (watched if ok else unwatched).append(root)
            if observer is not None and watched:
                try:
                    observer.start()
                    self._observer = observer
                except Exception:   # noqa: BLE001
                    logger.info("video folder watch: the watcher didn't start, checking on a timer")
                    unwatched, watched = watched + unwatched, []
        self._watched, self._unwatched = watched, unwatched
        if want:
            logger.info("video folder watch: watching %d folder(s), %d on a timer",
                        len(watched), len(unwatched))

    def stop(self) -> None:
        obs, self._observer = self._observer, None
        if obs is not None:
            try:
                obs.stop()
            except Exception:   # noqa: BLE001, S110 - already gone
                pass
        self._watched, self._unwatched = [], []

    # ── changes ──────────────────────────────────────────────────────────────
    def changed(self, path: str) -> None:
        if is_library_change(path):
            self.debounce.touch(self._clock())

    def tick(self) -> bool:
        """one pass of the loop. True when it asked for a scan."""
        now = self._clock()
        fire = self.debounce.due(now)
        if self._unwatched and now - self._last_poll >= POLL_SEC:
            self._last_poll = now
            fire = True
        if fire:
            try:
                self._request_scan()
            except Exception:   # noqa: BLE001
                logger.exception("video folder watch: couldn't start a scan")
            return True
        return False

    def _scan(self) -> None:
        db = self._db()
        if db is None:
            return
        from core.video.scanner import get_video_scanner
        from core.video.sources import scan_video_source
        get_video_scanner(db).request_scan(scan_video_source, mode="incremental")


class _Handler:
    """watchdog's handler shape, without importing watchdog at module load"""

    def __init__(self, watch: VideoFolderWatch):
        self._watch = watch

    def dispatch(self, event) -> None:
        self._watch.changed(getattr(event, "src_path", "") or "")
        dest = getattr(event, "dest_path", "")
        if dest:
            self._watch.changed(dest)


def ensure_started(db_provider: Callable) -> None:
    """start the watch once. it follows the settings itself, so switching the
    video source or the folders needs no restart."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    watch = VideoFolderWatch(db_provider)

    def loop():
        last_sync = 0.0
        while True:
            try:
                if time.time() - last_sync >= CHECK_SEC:
                    last_sync = time.time()
                    watch.sync()
                watch.tick()
            except Exception:   # noqa: BLE001
                logger.exception("video folder watch: pass failed")
            time.sleep(5)

    threading.Thread(target=loop, name="video-folder-watch", daemon=True).start()
