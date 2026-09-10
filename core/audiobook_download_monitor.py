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
from pathlib import Path
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
    check_complete: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Advance one tracked download by a tick.

    Returns a patch of what changed — ``{status, progress, bytes_done,
    bytes_total, save_path, error, imported_path, completeness}`` — with only
    the keys that have a value. An empty patch means nothing to record this tick.

    ``check_complete`` is the gate between "the client finished" and "the book
    is whole". Without one the old behaviour holds and anything the client calls
    complete is imported.

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

    # content_path FIRST. It is the client's absolute path to THIS torrent's
    # own file or folder; save_path is the shared directory it saved into. The
    # completeness gate walks whatever it is handed recursively, so passing
    # save_path measured every other download in the folder against this book's
    # runtime and then staged it forever.
    #
    # This is #1139 again — the music album flow had the same bug and fixed it
    # the same way. Usenet has no content_path and does not need one: SAB's
    # save_path is already the job's own storage folder.
    reported = (
        getattr(status, "content_path", None)
        or getattr(status, "save_path", None)
        or getattr(status, "path", None)
    )
    resolved = resolve_path(reported)
    if not resolved:
        # Complete but the path is not visible from this container yet. Left as
        # downloading so the next tick tries again rather than failing a book
        # that is actually on disk somewhere.
        patch["status"] = "downloading"
        return patch

    patch["save_path"] = str(resolved)

    # The gate. A download client says "complete" when the files it was ASKED
    # for finished, which is not the same as the book being whole — and a book
    # missing its last chapters plays perfectly until the listener runs out of
    # it. Short books are STAGED, not failed: torrents finish late and uploaders
    # repair releases, so the right answer is to keep the files and look again.
    if check_complete is not None:
        verdict = check_complete(str(resolved), row)
        patch["completeness"] = verdict.get("reason", "")
        if not verdict.get("complete"):
            if verdict.get("expired"):
                patch["status"] = "failed"
                patch["error"] = (
                    f"Never completed: {verdict.get('reason') or 'still incomplete'}"
                )
            else:
                # Held, with the reason visible, so "waiting" never looks like
                # "stuck".
                patch["status"] = "staged"
            return patch

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


class _SoulseekStatus:
    """The aggregated folder, wearing the shape a client adapter returns.

    Soulseek has no single job to ask about, so the transfers are added up and
    presented as one. Doing the shaping here keeps the branch to this function
    instead of spreading a second status shape through the whole tick.
    """

    def __init__(self, rolled: Dict[str, Any]) -> None:
        self.state = rolled["state"]
        self.progress = rolled["progress"]
        self.size = rolled["size"]
        self.transferred = rolled["transferred"]
        self.speed = 0
        self.save_path = rolled.get("save_path", "")
        self.files = rolled["total"]
        self.files_done = rolled["finished"]


def _get_status(source: str, ref: str) -> Any:
    """Poll whichever client is carrying this job."""
    try:
        if source == "soulseek":
            from core.audiobook_soulseek import status_for
            rolled = status_for(ref)
            return _SoulseekStatus(rolled) if rolled else None

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


def _check_complete(source_path: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Whether the download on disk is the whole book.

    Compared against the runtime Audible publishes for the title, so this is a
    measurement rather than a guess at filenames. A short book reports
    ``expired`` once it has been staged past the deadline — waiting is right,
    waiting forever means one broken release holds a row for good.
    """
    from core.audiobook_completeness import (
        assess,
        staging_days_from_settings,
        staging_expired,
        tolerance_from_settings,
    )

    book = _book_for(row)
    expected = int(book.get("runtime_minutes") or 0)

    # Audible publishes the UNABRIDGED runtime of the reading it sells. Two
    # kinds of release can never match it and must not be measured against it:
    # an abridgement (roughly half), and a dramatised adaptation like
    # GraphicAudio (a full-cast re-recording sold in parts, of unrelated
    # length). Both would otherwise be held for the whole staging window and
    # then failed to the wishlist, which would grab the same release again.
    #
    # The folder name is checked too: the release title does not always say
    # what the download turns out to be.
    from core.audiobook_release_search import is_abridged, is_dramatized

    labels = f"{row.get('release_title') or ''} {Path(source_path).name}"
    abridged = is_abridged(labels) or is_dramatized(labels)

    verdict = assess(source_path, expected, tolerance_from_settings(),
                     abridged=abridged)
    if not verdict.get("complete"):
        verdict["expired"] = staging_expired(
            row.get("created_at") or 0, staging_days_from_settings(),
        )
    return verdict


def _book_for(row: Dict[str, Any]) -> Dict[str, Any]:
    """The catalogue's description of this download's book.

    Prefers the payload captured when the release was grabbed. Importing needs
    the series and narrator to shelve the book and the runtime to measure it,
    and asking Audible again hours later makes the import depend on a storefront
    that sheds load and occasionally pulls titles outright.

    Falls back to a live lookup only for rows recorded before the payload was
    captured, and to the thin row itself if even that fails — a book still files
    under its author rather than failing to import over missing cover art.
    """
    from core.audiobook_database import AudiobookDatabase

    stored = AudiobookDatabase.stored_book(row)
    if stored.get("title"):
        return stored

    try:
        from core.audiobook_client import get_audiobook_client

        full = get_audiobook_client().get_book(str(row.get("asin") or ""))
        if full is not None:
            return full.to_dict()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not look up %s at import time: %s", row.get("asin"), exc)

    return {
        "asin": row.get("asin"),
        "title": row.get("title"),
        "author_names": [row["author"]] if row.get("author") else [],
        "narrator_names": [],
        "series": [],
        "release_date": "",
    }


def _organize(source_path: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """File a finished download into the audiobook library."""
    from core.audiobook_organizer import configured_template, library_root, organize_download

    book = _book_for(row)

    try:
        from core.settings import config_manager
        renumber = bool(config_manager.get("audiobooks.renumber_chapters", True))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the renumber setting, defaulting on: %s", exc)
        renumber = True

    result = organize_download(
        source_path, book, library_root(),
        template=configured_template(), renumber=renumber,
        # Claims the folder for this release so a retry or an "any narrator"
        # fallback can never interleave two readings into one book.
        release_id=str(row.get("release_title") or row.get("download_id") or ""),
    )

    # Tags, cover and sidecars, after the files are safely in place. Guarded
    # separately and never allowed to fail the import: the book is already in
    # the library by now, and a badly labelled book beats a lost one.
    if result.get("ok") and result.get("path"):
        try:
            from core.audiobook_post_processor import post_process_book
            post_process_book(result["path"], book, result.get("files") or None)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Post-processing %s failed: %s", result.get("path"), exc)

    return result


def tick(db: Any = None) -> Dict[str, int]:
    """One pass over everything still in flight."""
    from core.audiobook_database import STATUS_DONE, STATUS_FAILED, get_audiobook_db

    database = db if db is not None else get_audiobook_db()
    summary = {"checked": 0, "completed": 0, "failed": 0, "staged": 0, "cancelled": 0}

    try:
        active = database.get_downloads(active_only=True)
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not read active audiobook downloads: %s", exc)
        return summary

    from core.audiobook_download_state import (
        forget,
        is_cancelled,
        mark_status,
        update_progress,
    )

    for row in active:
        summary["checked"] += 1

        # Cancelling a card used to remove it from the page while the torrent
        # carried on downloading. The client is told, then the row is closed.
        if is_cancelled(row["download_id"]):
            _cancel_at_client(row)
            database.update_download(row["download_id"], status="cancelled",
                                     error="Cancelled")
            forget(row["download_id"])
            summary["cancelled"] += 1
            continue
        patch = process_download(
            row, get_status=_get_status, resolve_path=_resolve_path,
            organize=_organize, check_complete=_check_complete,
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
                    _book_for(row), imported_path or patch.get("save_path", ""),
                )
            logger.info("Audiobook imported: %s -> %s", row.get("title"), imported_path)
        elif patch.get("status") == "staged":
            # "importing" on the card, and deliberately NOT an error: the book is
            # waiting for the rest of itself, which is a normal state a torrent
            # passes through. Putting the reason in error_message would paint it
            # red on the shared Downloads page as though something had gone
            # wrong. The reason lives in the audiobook database, where the
            # audiobook UI can show it as what it is.
            summary["staged"] += 1
            mark_status(row["download_id"], "importing")
        elif patch.get("status") == "failed":
            mark_status(row["download_id"], "failed", error=str(patch.get("error") or ""))
            summary["failed"] += 1
            _return_to_wishlist(database, row, str(patch.get("error") or ""))
            if asin:
                database.mark_wishlist_status(
                    asin, STATUS_FAILED, error=str(patch.get("error") or ""),
                )
    return summary


def _cancel_at_client(row: Dict[str, Any]) -> None:
    """Tell the download client to stop, and take its partial data with it.

    Best effort: a client that cannot be reached must not leave the card stuck
    on the page forever, so the row is closed either way. The worst case is an
    orphaned torrent the user removes by hand, which is what happened on EVERY
    cancel before this existed.
    """
    source = str(row.get("source") or "").lower()
    ref = str(row.get("client_id") or "")
    if not ref:
        return
    try:
        if source == "soulseek":
            from core.audiobook_soulseek import cancel
            cancel(ref)
            return

        if source == "torrent":
            from core.torrent_clients import get_active_adapter
        else:
            from core.usenet_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return
        _run(adapter.remove(ref, delete_files=True))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not cancel %s at the download client: %s", ref, exc)


def _return_to_wishlist(database: Any, row: Dict[str, Any], error: str) -> None:
    """A download that failed becomes a book we are still looking for.

    Matches the music side, where a failed track returns to the wishlist rather
    than evaporating. A book already on the wishlist simply goes back to
    "failed", which the retry backoff picks up on a later pass; one that was
    grabbed manually and never wishlisted is ADDED, because otherwise a failed
    manual grab is the one path where a book someone asked for is silently
    forgotten.
    """
    from core.audiobook_database import STATUS_FAILED

    asin = str(row.get("asin") or "")
    if not asin:
        return

    # Block the release that just failed BEFORE putting the book back. The
    # wishlist searches again on its next pass and would otherwise find the
    # same broken posting, grab it, fail, and go round again forever — which is
    # exactly what a release that is really part 1 of 5 would do.
    #
    # The RELEASE is blocked, never the book. The book is still wanted; this
    # says only that one posting of it is no good.
    try:
        database.block_release(
            {"guid": row.get("release_guid") or "",
             "title": row.get("release_title") or "",
             "indexer": row.get("indexer") or "",
             "protocol": row.get("source") or ""},
            asin=asin,
            book_title=str(row.get("title") or ""),
            reason=error or "The download failed",
        )
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not block the failed release: %s", exc)

    try:
        if database.is_wishlisted(asin):
            database.mark_wishlist_status(asin, STATUS_FAILED, error=error)
            return
        book = _book_for(row)
        if book.get("title") and database.add_to_wishlist(book):
            database.mark_wishlist_status(asin, STATUS_FAILED, error=error)
            logger.info("Failed download %s went back on the wishlist", row.get("title"))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not return %s to the wishlist: %s", asin, exc)


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
