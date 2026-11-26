"""one server library read on its own, the moment it is switched on.

turning on Scan for another movie or TV library (Settings, Connections:
``movies_more`` / ``tv_more``, Anime turns Scan on with it) used to do
nothing until the next whole video scan: no titles, no count, and its folder
never showed under your library's folders. like home videos' "Add it"
(core/video/home_videos.scan_async), the new library is read at once in the
background, just that one: every title upserted, enrichment kept, nothing
pruned, so the rest of the library is never touched. switching one off
never deletes anything; it is only dropped from the queue if not read yet.

one at a time, after any scan already running. the state per library is
kept for the settings rows: "Reading 4K Movies... 312 found", then "Added".
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video_library_scan")

KINDS = ("movie", "show")
# a scan already running is waited for, checked this often, at most this long
_WAIT_STEP = 2.0
_WAIT_MAX = 6 * 3600.0

_lock = threading.Lock()
_queue: List[Tuple[str, str]] = []
_state: Dict[str, Dict[str, Any]] = {}
_worker: Optional[threading.Thread] = None


def key(kind: str, title: str) -> str:
    return f"{kind}|{title}"


def status() -> Dict[str, Dict[str, Any]]:
    """{"movie|4K Movies": {kind, library, state, found, error, ...}}: queued,
    running (found climbs as it reads), done or error"""
    with _lock:
        return {k: dict(v) for k, v in _state.items()}


def picks(sel: Dict[str, Any]) -> List[Tuple[str, str]]:
    """every (kind, library) a selection scans: the main picks and the extra ones"""
    from core.video.sources import _movie_pick, _tv_pick
    sel = sel or {}
    return ([("movie", t) for t in (_movie_pick(sel) or [])]
            + [("show", t) for t in (_tv_pick(sel) or [])])


def added(before: Dict[str, Any], after: Dict[str, Any]) -> List[Tuple[str, str]]:
    """the libraries ``after`` scans that ``before`` didn't"""
    had = set(picks(before))
    return [p for p in picks(after) if p not in had]


READ_KEY = "video_libraries_read"


def _read_set(db) -> set:
    import json
    try:
        return set(json.loads(db.get_setting(READ_KEY) or "[]"))
    except (ValueError, TypeError):
        return set()


def mark_read(db, kind: str, title: str) -> None:
    """remember a library has been read once, so it isn't read again for it"""
    import json
    try:
        have = _read_set(db)
        have.add(key(kind, title))
        db.set_setting(READ_KEY, json.dumps(sorted(have)))
    except Exception:  # noqa: BLE001 - only a hint; a full scan reads it anyway
        logger.debug("library read mark failed", exc_info=True)


def unread_extras(db, sel: Dict[str, Any]) -> List[Tuple[str, str]]:
    """extra libraries switched on that were never read on their own: one
    switched on before reading on switch-on existed, waiting for a full scan"""
    sel = sel or {}
    main = {("movie", str(sel.get("movies") or "")), ("show", str(sel.get("tv") or ""))}
    done = _read_set(db)
    return [p for p in picks(sel) if p not in main and key(*p) not in done]


def _default_build(kind: str, title: str):
    from core.video.sources import _build_source
    return _build_source([title], None) if kind == "movie" else _build_source(None, [title])


def _default_scanner(db):
    from core.video.scanner import get_video_scanner
    return get_video_scanner(db)


def start(db, libraries: Iterable[Tuple[str, str]], *, keep: Optional[Iterable[Tuple[str, str]]] = None,
          build: Callable[[str, str], Any] = _default_build,
          scanner: Optional[Any] = None) -> bool:
    """queue each (kind, title) and read them on a thread of their own.
    ``keep``: every library still switched on; a queued one no longer in it
    is dropped (switched off before it was read). True when a thread started."""
    global _worker
    todo = [(k, str(t)) for k, t in libraries if k in KINDS and str(t or "").strip()]
    with _lock:
        if keep is not None:
            still = set(keep)
            for p in [p for p in _queue if p not in still]:
                _queue.remove(p)
                _state.pop(key(*p), None)
        for p in todo:
            if p not in _queue:
                _queue.append(p)
            _state[key(*p)] = {"kind": p[0], "library": p[1], "state": "queued", "found": 0,
                               "error": None, "queued_at": time.time()}
        if not _queue or _worker is not None:
            return False
        _worker = threading.Thread(target=_run, args=(db, build, scanner), daemon=True,
                                   name="video-library-scan")
        _worker.start()
        return True


def _set(p: Tuple[str, str], **kw) -> None:
    with _lock:
        if key(*p) in _state:
            _state[key(*p)].update(kw)


def _run(db, build, scanner) -> None:
    global _worker
    while True:
        with _lock:
            if not _queue:
                # cleared under the lock: a start() after this starts a new one
                _worker = None
                return
            p = _queue.pop(0)
        try:
            read_one(db, p[0], p[1], build=build, scanner=scanner)
        except Exception:   # noqa: BLE001 - one library's failure never stops the rest
            logger.exception("video library scan: %s failed", p[1])
            _set(p, state="error", error="The scan failed. See the log.", finished_at=time.time())


def read_one(db, kind: str, title: str, *, build: Callable[[str, str], Any] = _default_build,
             scanner: Optional[Any] = None, wait_step: float = _WAIT_STEP,
             wait_max: float = _WAIT_MAX) -> Dict[str, Any]:
    """read one library now, after any scan already running. the scanner's
    own status is read while it runs so the row can count up."""
    p = (kind, title)
    sc = scanner if scanner is not None else _default_scanner(db)
    waited = 0.0
    while True:
        if sc.get_status().get("state") != "scanning":
            _set(p, state="running", started_at=time.time(), found=0)
            done: Dict[str, Any] = {}
            watcher = threading.Thread(target=_watch, args=(sc, p, done), daemon=True,
                                       name="video-library-scan-watch")
            watcher.start()
            try:
                result = sc.scan_library_sync(lambda: build(kind, title), kind, title)
            finally:
                done["yes"] = True
            if result.get("state") != "in_progress":
                break
        if waited >= wait_max:
            _set(p, state="error", error="Another scan ran too long. Scan again later.",
                 finished_at=time.time())
            return {"state": "error"}
        _set(p, state="queued")
        time.sleep(wait_step)
        waited += wait_step
    found = int(result.get("movies" if kind == "movie" else "shows") or 0)
    if result.get("state") == "done":
        _set(p, state="done", found=found, finished_at=time.time())
        mark_read(db, kind, title)
        logger.info("video library scan: %s read, %d found", title, found)
    else:
        _set(p, state="error", found=found, finished_at=time.time(),
             error=result.get("error") or "The scan stopped early")
    return result


def _watch(sc, p: Tuple[str, str], done: Dict[str, Any]) -> None:
    """the scanner's count, copied onto the row while it reads"""
    field = "movies" if p[0] == "movie" else "shows"
    while not done.get("yes"):
        st = sc.get_status()
        if st.get("library") == p[1]:
            _set(p, found=int(st.get(field) or 0))
        time.sleep(0.5)


def reset() -> None:
    """for tests"""
    global _worker
    with _lock:
        _queue.clear()
        _state.clear()
        _worker = None
