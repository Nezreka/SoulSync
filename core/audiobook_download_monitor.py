"""Watch grabbed audiobooks to completion and file them into the library.

Without this a grab is fire-and-forget: the download client is fetching
something the app has no idea about, so nothing ever notices it finished and the
book never reaches the library.

``process_download`` is PURE — every piece of I/O is injected — so the state
machine can be tested without a download client, a filesystem or a network. The
thread around it is a thin polling loop.

MUSIC-SAFE, in the same sense core/video/client_download.py is: it polls the
SHARED torrent/usenet adapters and reuses ``resolve_reported_save_path`` from
the music download plugins, importing and calling them, never modifying them.
Nothing here touches the music batches, worker pool, wishlist or database.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_download_monitor")

_FAILED_STATES = {"error", "failed"}
_COMPLETE_STATES = {"seeding", "completed", "complete", "succeeded", "finished"}

DEFAULT_POLL_SECONDS = 20.0


def normalize_state(status: Any) -> str:
    """Collapse a client's own vocabulary into downloading / completed / failed.

    "seeding" counts as complete: a torrent that has finished downloading and is
    now uploading has the files on disk, and waiting for it to stop seeding
    would hold the book hostage to a ratio.
    """
    state = str(getattr(status, "state", "") or "").lower()
    if state in _FAILED_STATES:
        return "failed"
    if state in _COMPLETE_STATES:
        return "completed"
    return "downloading"


def process_download(
    row: Dict[str, Any],
    *,
    get_status: Callable[[str, str], Any],
    resolve_path: Callable[[Any], Any],
    organize: Callable[[str, Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Advance one tracked download by a tick.

    Returns a patch of what changed — ``{status, progress, bytes_done,
    bytes_total, save_path, error, imported_path}`` — with only the keys that
    have a value. An empty patch means nothing to record this tick.

    A poll that fails is treated as "unknown right now", not as a failure. A
    download client restarting, or a momentary timeout, must not mark a
    perfectly healthy download as broken.
    """
    ref = str(row.get("client_id") or "")
    source = str(row.get("source") or "")
    if not ref or not source:
        return {"status": "failed", "error": "No download client reference"}

    status = get_status(source, ref)
    if status is None:
        return {}

    patch: Dict[str, Any] = {}
    for key, attr in (("bytes_done", "downloaded"), ("bytes_total", "size")):
        value = getattr(status, attr, None)
        if value is not None:
            try:
                patch[key] = int(value)
            except (TypeError, ValueError):
                pass

    progress = getattr(status, "progress", None)
    if progress is not None:
        try:
            # Adapters disagree: some report 0-1, some 0-100.
            value = float(progress)
            patch["progress"] = round(value * 100 if value <= 1.0 else value, 2)
        except (TypeError, ValueError):
            pass

    state = normalize_state(status)
    if state == "failed":
        patch["status"] = "failed"
        patch["error"] = str(getattr(status, "error", "") or "The download client reported a failure")
        return patch

    if state != "completed":
        patch["status"] = "downloading"
        return patch

    reported = getattr(status, "save_path", None) or getattr(status, "path", None)
    resolved = resolve_path(reported)
    if not resolved:
        # Complete but the path is not visible from this container yet. Left as
        # downloading so the next tick tries again rather than failing a book
        # that is actually on disk somewhere.
        patch["status"] = "downloading"
        return patch

    patch["save_path"] = str(resolved)
    result = organize(str(resolved), row)
    if not result.get("ok"):
        patch["status"] = "failed"
        patch["error"] = str(result.get("error") or "Could not organize the download")
        return patch

    patch["status"] = "completed"
    patch["progress"] = 100.0
    patch["imported_path"] = result.get("path", "")
    return patch


# ---------------------------------------------------------------------------
# Production wiring
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _get_status(source: str, ref: str) -> Any:
    """Poll the shared torrent/usenet client for one job."""
    try:
        if source == "torrent":
            from core.torrent_clients import get_active_adapter
        else:
            from core.usenet_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return None
        return _run(adapter.get_status(ref))
    except Exception:                                       # noqa: BLE001
        logger.debug("Audiobook status poll failed for %s %s", source, ref, exc_info=True)
        return None


def _resolve_path(reported: Any) -> Any:
    """Map the client's reported save path onto a path this process can read.

    The downloader reports from inside its OWN container, which may mount the
    same directory somewhere else. The music side already solved this, so its
    resolver is reused rather than re-derived.
    """
    try:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        return resolve_reported_save_path(reported)
    except Exception:                                       # noqa: BLE001
        return reported


def _organize(source_path: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """File a finished download into the audiobook library."""
    from core.audiobook_organizer import configured_template, library_root, organize_download

    book = {
        "asin": row.get("asin"),
        "title": row.get("title"),
        "author_names": [row["author"]] if row.get("author") else [],
        "narrator_names": [],
        "series": [],
        "release_date": "",
    }
    # The stored row is thin; the catalogue knows the series and narrator, which
    # the path template needs to shelve the book correctly.
    try:
        from core.audiobook_client import get_audiobook_client
        full = get_audiobook_client().get_book(str(row.get("asin") or ""))
        if full is not None:
            book = full.to_dict()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not enrich %s before organizing: %s", row.get("asin"), exc)

    try:
        from core.settings import config_manager
        renumber = bool(config_manager.get("audiobooks.renumber_chapters", True))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the renumber setting, defaulting on: %s", exc)
        renumber = True

    return organize_download(
        source_path, book, library_root(),
        template=configured_template(), renumber=renumber,
    )


def tick(db: Any = None) -> Dict[str, int]:
    """One pass over everything still in flight."""
    from core.audiobook_database import STATUS_DONE, STATUS_FAILED, get_audiobook_db

    database = db if db is not None else get_audiobook_db()
    summary = {"checked": 0, "completed": 0, "failed": 0}

    try:
        active = database.get_downloads(active_only=True)
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not read active audiobook downloads: %s", exc)
        return summary

    from core.audiobook_download_state import forget, mark_status, update_progress

    for row in active:
        summary["checked"] += 1
        patch = process_download(
            row, get_status=_get_status, resolve_path=_resolve_path, organize=_organize,
        )
        if not patch:
            continue

        imported_path = patch.pop("imported_path", "")
        database.update_download(row["download_id"], **patch)

        # Same numbers onto the Downloads page card.
        update_progress(
            row["download_id"],
            percent=patch.get("progress"),
            bytes_done=patch.get("bytes_done"),
            bytes_total=patch.get("bytes_total"),
        )

        asin = str(row.get("asin") or "")
        if patch.get("status") == "completed":
            mark_status(row["download_id"], "completed", file_path=imported_path)
            # The card has served its purpose; the history lives in the
            # audiobook database, not in runtime state.
            forget(row["download_id"])
            summary["completed"] += 1
            if asin:
                database.mark_wishlist_status(asin, STATUS_DONE)
                database.add_to_library(
                    {"asin": asin, "title": row.get("title"),
                     "author_names": [row["author"]] if row.get("author") else []},
                    imported_path or patch.get("save_path", ""),
                )
            logger.info("Audiobook imported: %s -> %s", row.get("title"), imported_path)
        elif patch.get("status") == "failed":
            mark_status(row["download_id"], "failed", error=str(patch.get("error") or ""))
            summary["failed"] += 1
            if asin:
                database.mark_wishlist_status(
                    asin, STATUS_FAILED, error=str(patch.get("error") or ""),
                )
    return summary


class AudiobookDownloadMonitor:
    """The timer around tick(). Same shape as the wishlist worker, same reasons."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_run_at: float = 0.0
        self.last_summary: Dict[str, int] = {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poll_seconds(self) -> float:
        try:
            from core.settings import config_manager
            return max(5.0, float(
                config_manager.get("audiobooks.download_poll_seconds", DEFAULT_POLL_SECONDS)
            ))
        except Exception:                                   # noqa: BLE001
            return DEFAULT_POLL_SECONDS

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="audiobook-downloads", daemon=True,
            )
            self._thread.start()
            logger.info("Audiobook download monitor started")
            return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.last_summary = tick()
                self.last_run_at = time.time()
            except Exception as exc:                        # noqa: BLE001
                # A monitor that dies on one bad row stops watching everything.
                logger.warning("Audiobook download tick failed: %s", exc, exc_info=True)
            if self._stop.wait(self.poll_seconds()):
                return

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "poll_seconds": self.poll_seconds(),
            "last_run_at": self.last_run_at,
            "last_summary": self.last_summary,
        }


_monitor: Optional[AudiobookDownloadMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> AudiobookDownloadMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = AudiobookDownloadMonitor()
    return _monitor


def ensure_started(force: bool = False) -> bool:
    """Start watching, if this install uses audiobooks at all.

    At boot the monitor stays asleep until the subsystem has been used once —
    otherwise every SoulSync install would grow an audiobook database and poll a
    download client forever for a feature its owner never opened.

    ``force`` skips that check and is what a grab passes: the download that just
    started is the thing to watch, and it exists before the next boot.
    """
    if not force:
        from core.audiobook_database import subsystem_in_use
        if not subsystem_in_use():
            logger.debug("No audiobook database yet; the download monitor stays asleep")
            return False
    return get_monitor().start()
