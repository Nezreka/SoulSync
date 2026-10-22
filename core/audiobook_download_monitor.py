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
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_download_monitor")

_FAILED_STATES = {"error", "failed"}
_COMPLETE_STATES = {"seeding", "completed", "complete", "succeeded", "finished"}

DEFAULT_POLL_SECONDS = 20.0

# consecutive ticks a job may be unknown to a REACHABLE client before the
# book is failed and handed back to the wishlist. the video monitor's rule
# (_GIVE_UP_AFTER). without it a torrent deleted from the client sat on
# "waiting for client" forever, and the wishlist row behind it stayed
# "grabbed" forever because a live-looking download blocked the reset.
GIVE_UP_AFTER_MISSES = 8
_misses: Dict[str, int] = {}

# a failed download goes straight on to the next-best release (the failed one
# is already blocked, so the search cannot hand it back) instead of waiting out
# the wishlist's retry backoff. a Soulseek peer that never answers is common,
# and six hours per dead peer meant a book with dozens of sources sat idle.
# capped per book so a run of bad sources falls back to the normal backoff
# rather than searching in a tight loop.
DEFAULT_IMMEDIATE_FALLBACKS = 5
_fallbacks: Dict[str, int] = {}
_fallbacks_lock = threading.Lock()


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
        return {"status": "unavailable", "error": "Waiting for the download client to report this job"}

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

    speed = getattr(status, "download_speed", None)
    if speed is not None:
        patch["speed"] = max(0, float(speed or 0))

    state = normalize_state(status)
    if state == "failed":
        patch["status"] = "failed"
        patch["error"] = str(getattr(status, "error", "") or "The download client reported a failure")
        return patch

    if state != "completed":
        raw_state = str(getattr(status, "state", "")).lower()
        if raw_state in ("queued", "waiting", "stalled", "missing"):
            patch["status"] = "queued"
        elif raw_state in ("paused", "unavailable"):
            patch["status"] = raw_state
        else:
            patch["status"] = "downloading"
        patch["error"] = "Waiting for the download client to report this job" if raw_state == "unavailable" else ""
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
            if verdict.get("no_audio"):
                # files but no audio, an ebook claimed as an audiobook. staging
                # held these as "importing" for the whole window, and no amount
                # of waiting turns an epub into a book you can listen to
                patch["status"] = "failed"
                patch["error"] = "No audio in this download, only other files (an ebook?)"
            elif verdict.get("overshoot"):
                # far longer than the book: a different title that shares its
                # words, or a whole series. it never gets shorter, so staging it
                # held the wishlist row for staging_days with nothing to wait
                # for. failing blocks this release and goes on to the next (#1833)
                patch["status"] = "failed"
                patch["error"] = f"Wrong release: {verdict.get('reason') or 'far longer than the book'}"
            elif verdict.get("expired"):
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
        self.state = "completed" if rolled["state"] == "done" else rolled["state"]
        self.progress = rolled["progress"] / 100.0
        self.size = rolled["size"]
        self.downloaded = rolled["transferred"]
        self.download_speed = rolled.get("speed", 0)
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


def _client_reachable(source: str) -> bool:
    """whether the client carrying `source` jobs can be asked at all.

    a job the client does not know is a different thing from a client that
    is down: the first will never come back, the second will. only the
    first should count towards giving up."""
    try:
        if source == "soulseek":
            from core.audiobook_soulseek import _shared_client
            return _shared_client() is not None
        if source == "torrent":
            from core.torrent_clients import get_active_adapter
        else:
            from core.usenet_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return False
        return bool(_run(adapter.check_connection()))
    except Exception:                                       # noqa: BLE001
        return False


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
        UNKNOWN_NARRATION_TOLERANCE,
        assess,
        narration_confidence,
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

    # The edition's runtime is the runtime of ONE reading. When the edition
    # names its narrator and the download is (or may be) a different reading,
    # that number is not the book's runtime and must not gate the import: a
    # complete alternate narration would be held as incomplete, then failed
    # back to the wishlist after staging expires, which would grab the same
    # release again.
    #
    # A download confidently identified as the edition's own narration gets
    # the hard runtime gate. Any other outcome — confidently different, or
    # unidentified — gets a loose plausibility floor instead of the exacting
    # gate (UNKNOWN_NARRATION_TOLERANCE): speaking rate is the only thing that
    # moves a complete unabridged reading, so anything far below is a sample
    # pack or a grossly truncated download, not an alternate narration. A
    # partial download naming a different narrator must not sail through
    # unchecked.
    #
    # When the edition names no narrator there is nothing to be aware of, and
    # the runtime stays the book's only completeness signal — the gate keeps
    # working exactly as before. Same when there is no expected runtime or
    # the release is abridged/dramatized: the narration question is moot, so
    # it is not even asked.
    gate_tolerance = tolerance_from_settings()
    narration: Optional[Dict[str, Any]] = None
    edition_narrators = [n for n in (book.get("narrator_names") or [])
                         if str(n).strip()]
    if expected > 0 and not abridged and edition_narrators:
        narration = narration_confidence(
            source_path, row.get("release_title"), book)
        if narration["confidence"] in ("different", "unknown"):
            # A floor, never a ceiling: it may only loosen the user's own
            # tolerance, never tighten it.
            gate_tolerance = min(gate_tolerance, UNKNOWN_NARRATION_TOLERANCE)

    verdict = assess(source_path, expected, gate_tolerance,
                     abridged=abridged)
    if narration is not None and verdict.get("complete"):
        measured = int(verdict.get("measured_minutes") or 0)
        if narration["confidence"] == "different":
            verdict["measured_by"] = "alternate-narration"
            verdict["reason"] = (
                f"{measured} minutes present; narrated by "
                f"{narration['detected_narrator']} rather than the edition's "
                f"narrator, so only a plausibility floor was checked"
            )
        elif narration["confidence"] == "unknown":
            verdict["measured_by"] = "narration-unverified"
            verdict["reason"] = (
                f"{measured} minutes present; the narration could not be "
                f"confirmed as the edition's, so a plausibility floor was "
                f"used instead of the strict gate"
            )
    elif (narration is not None and narration["confidence"] in ("different", "unknown")
            and not verdict.get("complete") and verdict.get("files")
            and verdict.get("reason")):
        # Held below the plausibility floor: the shortfall is still framed
        # against the edition's runtime, so annotate what was actually
        # checked rather than implying a definitive measurement.
        if narration["confidence"] == "different":
            verdict["reason"] = (
                f"{verdict['reason']} (alternate narration: "
                f"{narration['detected_narrator']}, below plausibility floor)"
            )
        else:
            verdict["reason"] = f"{verdict['reason']} (narration unconfirmed)"
    if not verdict.get("complete") and not verdict.get("files"):
        from core.audiobook_completeness import has_files_but_no_audio
        verdict["no_audio"] = has_files_but_no_audio(source_path)
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


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` lives under ``root`` (symlinks resolved)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _nudge_media_server_scan() -> None:
    """Best-effort: tell the active media server a book just landed.

    Plex's filesystem watcher usually notices the reveal on its own; this
    covers scheduled-scan setups so nobody has to rescan by hand. Uses the
    default section, same as the web UI's Scan Library button. Never raises.
    """
    try:
        from core.media_server.engine import get_media_server_engine
        client = get_media_server_engine().active_client()
        if client is None or not client.is_connected():
            return
        client.trigger_library_scan()
        logger.info("Requested a media-server library scan for the new audiobook")
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Media-server scan nudge failed: %s", exc)


def _request_scan_nudge() -> None:
    """Fire-and-forget wrapper: a slow or unreachable media server must never
    stall the import tick (is_connected can block retrying a dead server)."""
    threading.Thread(target=_nudge_media_server_scan, daemon=True).start()


def _organize(source_path: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """File a finished download into the audiobook library.

    The book is built in a staging folder outside the media-server-watched
    library and revealed when it is complete — the folder-level version of
    the music side's atomic_copy_to_staging. The reveal is one atomic folder
    rename (per-file, each appearing atomically, when the destination
    already exists or the rename crosses filesystems). Without
    this, the M4B merge's file-then-delete sequence lets Plex ingest chapter
    files that vanish moments later, leaving ghost entries until a manual
    rescan.
    """
    from core.audiobook_organizer import (
        configured_template,
        folder_accepts_release,
        library_root,
        new_staging_dir,
        organize_download,
        read_release_marker,
        render_audiobook_path,
        reveal_staged_book,
        staging_base,
    )

    book = _book_for(row)

    try:
        from core.settings import config_manager
        renumber = bool(config_manager.get("audiobooks.renumber_chapters", True))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the renumber setting, defaulting on: %s", exc)
        renumber = True

    lib_root = library_root()
    # Claims the folder for this release so a retry or an "any narrator"
    # fallback can never interleave two readings into one book.
    release_id = str(row.get("release_title") or row.get("download_id") or "")

    template = configured_template()
    if release_id:
        # Fail fast: the one-book-one-release rule is checked against the
        # REAL destination before anything is copied or merged — the reveal
        # re-checks, but a refused reveal after a 20-minute merge is a
        # wasted evening.
        real_dest = Path(lib_root).expanduser().joinpath(
            *render_audiobook_path(template, book))
        if not folder_accepts_release(real_dest, release_id):
            claimed = read_release_marker(real_dest)
            return {"ok": False, "path": "", "files": [], "skipped": [],
                    "error": (f"{real_dest.name} was already filled from a different release "
                              f"({claimed}). Mixing two editions would interleave narrators.")}

    # Staging lives next to the library, invisible to media servers. If it
    # cannot be set up, file directly into the library — today's behavior,
    # never worse.
    stage_root = None
    file_root = lib_root
    base = staging_base(lib_root)
    if base is not None:
        stage_root = new_staging_dir(base)
        if stage_root is not None:
            file_root = str(stage_root)

    try:
        result = organize_download(
            source_path, book, file_root,
            template=template, renumber=renumber,
            release_id=release_id,
        )

        # Tags, cover and sidecars, after the files are safely in place.
        # Guarded separately and never allowed to fail the import: a badly
        # labelled book beats a lost one.
        if result.get("ok") and result.get("path"):
            # Optional: fold a multi-file book into one chaptered .m4b before
            # tagging. merge_book_files never raises and returns the original
            # list on any skip or failure, so the book is never worse off for the
            # attempt — the guard below is belt and suspenders.
            files = result.get("files") or []
            if result.get("skipped"):
                # A retry that found some chapters already filed: `files` holds
                # only the remainder, and merging that subset would fold a
                # partial book and strand the rest as loose files.
                logger.info("Skipping the M4B merge for %s: %d file(s) already filed",
                            result.get("path"), len(result["skipped"]))
                # A crash mid-encode can still have stranded a partial .m4b here.
                # The merge itself is skipped, but move the unplayable strand
                # aside (never delete) so it is neither ingested nor squatting
                # the name a future merge wants.
                try:
                    from core.audiobook_m4b_merge import merge_enabled, sweep_unverifiable_m4b
                    if merge_enabled():
                        sweep_unverifiable_m4b(
                            result["path"], files + list(result.get("skipped") or []))
                except Exception as exc:                        # noqa: BLE001
                    logger.warning("M4B sweep failed for %s: %s", result.get("path"), exc)
            else:
                try:
                    from core.audiobook_m4b_merge import merge_book_files
                    files = merge_book_files(result["path"], files, book)
                except Exception as exc:                        # noqa: BLE001
                    logger.warning("M4B merge step failed for %s: %s", result.get("path"), exc)
                    files = result.get("files") or []
            try:
                from core.audiobook_post_processor import post_process_book
                post_process_book(result["path"], book, files or None)
            except Exception as exc:                            # noqa: BLE001
                logger.warning("Post-processing %s failed: %s", result.get("path"), exc)

        if stage_root is not None and result.get("ok") and result.get("path"):
            staged_path = Path(result["path"])
            if _is_within(staged_path, stage_root):
                revealed = reveal_staged_book(
                    staged_path, stage_root, lib_root, release_id=release_id,
                )
                if not revealed.get("ok"):
                    logger.warning("Revealing %s into the library failed: %s",
                                   result.get("path"), revealed.get("error"))
                    return {"ok": False, "path": "", "files": [],
                            "skipped": result.get("skipped") or [],
                            "error": revealed.get("error") or "Could not move the book into the library"}
                result = {"ok": True, "path": revealed["path"], "files": revealed["files"],
                          "skipped": result.get("skipped") or [], "error": ""}
            else:
                # organize_download filed outside the staging root (a test
                # double, or a future code path that bypasses staging): there
                # is nothing to reveal, return the result untouched.
                logger.debug("Organized path %s is not under staging; skipping reveal",
                             result["path"])
        if result.get("ok") and result.get("path"):
            # The library changed — book revealed or filed directly. Nudge
            # the media server so it shows up without a manual rescan.
            _request_scan_nudge()
        return result
    finally:
        if stage_root is not None:
            shutil.rmtree(stage_root, ignore_errors=True)


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
        register_download,
        is_cancelled,
        mark_status,
        set_task_metadata,
        update_progress,
    )

    for row in active:
        summary["checked"] += 1
        source = str(row.get("source") or "").lower()
        username = ""
        release_title = str(row.get("release_title") or "")
        if source == "soulseek":
            from core.audiobook_soulseek import decode_refs
            unpacked = decode_refs(row.get("client_id"))
            username = unpacked.get("username") or ""
            if not release_title:
                release_title = unpacked.get("folder") or ""
        # Runtime cards disappear on restart; the durable job and client refs
        # remain. Reattach without replacing existing progress/cancellation.
        register_download(row["download_id"], row.get("title") or "Audiobook",
                          author=row.get("author") or "", protocol=row.get("source") or "",
                          size_bytes=row.get("bytes_total") or 0, only_if_missing=True,
                          username=username, release_title=release_title)
        set_task_metadata(row["download_id"], username=username, release_title=release_title)

        # Cancelling a card used to remove it from the page while the torrent
        # carried on downloading. Persist cancellation before runtime cleanup.
        if is_cancelled(row["download_id"]):
            cancel_downloads([row["download_id"]], db=database)
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
        speed = patch.pop("speed", None)
        if not database.update_download(row["download_id"], imported_path=imported_path or None, **patch):
            forget(row["download_id"])
            continue

        # Same numbers onto the Downloads page card.
        update_progress(
            row["download_id"],
            percent=patch.get("progress"),
            bytes_done=patch.get("bytes_done"),
            bytes_total=patch.get("bytes_total"),
            speed=speed,
        )

        if patch.get("status") == "unavailable":
            # unknown to the client. counted only when the client is there to
            # ask, so a client that is down keeps every book waiting instead
            # of failing them all.
            if _client_reachable(str(row.get("source") or "")):
                misses = _misses.get(row["download_id"], 0) + 1
                _misses[row["download_id"]] = misses
                if misses >= GIVE_UP_AFTER_MISSES:
                    _misses.pop(row["download_id"], None)
                    patch = {
                        "status": "failed",
                        "error": "The download client no longer has this job; the book goes back to the wishlist",
                    }
                    database.update_download(row["download_id"], **patch)
        else:
            _misses.pop(row["download_id"], None)
        if patch.get("status") in ("downloading", "queued", "paused", "unavailable"):
            mark_status(row["download_id"], "downloading" if patch["status"] == "downloading" else "queued",
                        error=patch.get("error") or ("Paused in download client" if patch["status"] == "paused" else ""),
                        release_title=release_title)

        asin = str(row.get("asin") or "")
        if patch.get("status") == "completed":
            mark_status(row["download_id"], "completed", file_path=imported_path, release_title=release_title)
            # The card has served its purpose; the history lives in the
            # audiobook database, not in runtime state.
            forget(row["download_id"])
            summary["completed"] += 1
            if asin:
                # every profile's row: the library is shared, so the book is
                # done for whoever wanted it, not only profile 1
                database.mark_wishlist_status(asin, STATUS_DONE, profile_id=None)
                database.add_to_library(
                    _book_for(row), imported_path or patch.get("save_path", ""),
                    download_id=row["download_id"], origin="soulsync",
                )
            _forget_fallbacks(asin)
            logger.info("Audiobook imported: %s -> %s", row.get("title"), imported_path)
            _announce_import(row, imported_path)
        elif patch.get("status") == "staged":
            # "importing" on the card, and deliberately NOT an error: the book is
            # waiting for the rest of itself, which is a normal state a torrent
            # passes through.
            summary["staged"] += 1
            held_msg = str(patch.get("completeness") or "Completeness check held import")
            # told once, when the hold starts: the row stays staged for every
            # later tick and a message per poll would be spam
            if row.get("status") != "staged":
                _announce_row("audiobook_import_needs_attention", row, reason=held_msg)
            mark_status(row["download_id"], "importing", held_reason=held_msg, release_title=release_title)
        elif patch.get("status") == "failed":
            mark_status(row["download_id"], "failed", error=str(patch.get("error") or ""), release_title=release_title)
            summary["failed"] += 1
            _announce_row("audiobook_download_failed", row, error=str(patch.get("error") or ""))
            _return_to_wishlist(database, row, str(patch.get("error") or ""))
            if asin:
                database.mark_wishlist_status(
                    asin, STATUS_FAILED, profile_id=None,
                    error=str(patch.get("error") or ""),
                )
                _try_next_release(database, asin)
    return summary


def _announce_import(row: Dict[str, Any], imported_path: str) -> None:
    """Tell the automation engine a book landed, as the music importers do.

    ``import_completed`` is the trigger Settings offers for "an import
    finished"; without this a finished audiobook never fired it.
    """
    from core.audiobook_events import announce, first_author

    try:
        book = _book_for(row)
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not describe the imported book: %s", exc)
        book = {}
    announce("import_completed",
             track_count=1,
             album_name=book.get("title") or row.get("title") or "",
             artist=first_author(book, row.get("author") or ""))


def _announce_row(event_type: str, row: Dict[str, Any], **extra: Any) -> None:
    """A download event for ``row``: its title and author plus ``extra``."""
    from core.audiobook_events import announce

    announce(event_type, title=row.get("title") or "", author=row.get("author") or "", **extra)


def _fallback_limit() -> int:
    try:
        from core.settings import config_manager
        value = config_manager.get("audiobooks.immediate_fallback_attempts",
                                   DEFAULT_IMMEDIATE_FALLBACKS)
        return max(0, int(DEFAULT_IMMEDIATE_FALLBACKS if value is None else value))
    except Exception:                                       # noqa: BLE001
        return DEFAULT_IMMEDIATE_FALLBACKS


def _forget_fallbacks(asin: str) -> None:
    with _fallbacks_lock:
        _fallbacks.pop(asin, None)


def _spawn(target: Callable[..., Any], *args: Any) -> None:
    threading.Thread(target=target, args=args,
                     name="audiobook-fallback", daemon=True).start()


def _try_next_release(database: Any, asin: str) -> bool:
    """Search again for a book whose download just failed, without the backoff.

    Off the monitor thread: a search waits on every source, and the tick must
    keep polling the downloads that are still moving.
    """
    limit = _fallback_limit()
    with _fallbacks_lock:
        used = _fallbacks.get(asin, 0)
        if used >= limit:
            _fallbacks.pop(asin, None)
            if limit:
                logger.info("Audiobook %s failed %d releases in a row; leaving it to the wishlist backoff",
                            asin, used)
            return False
        _fallbacks[asin] = used + 1
    _spawn(_run_fallback, database, asin, used + 1, limit)
    return True


def _run_fallback(database: Any, asin: str, attempt: int, limit: int) -> None:
    from core.audiobook_database import STATUS_FAILED
    from core.audiobook_wishlist_worker import process_one

    try:
        for profile_id in database.profiles_with_rows():
            row = database.get_wishlist_entry(asin, profile_id=profile_id)
            # only a row still sitting on "failed": a wishlist pass or a manual
            # search may already have picked it up, and the library may have it
            if not row or row.get("status") != STATUS_FAILED:
                continue
            logger.info("Audiobook download for %s failed; trying the next release (%d/%d)",
                        asin, attempt, limit)
            if not process_one(row, db=database).get("grabbed"):
                _forget_fallbacks(asin)
            return
        _forget_fallbacks(asin)
    except Exception as exc:                                # noqa: BLE001
        _forget_fallbacks(asin)
        logger.warning("Could not try the next release for %s: %s", asin, exc, exc_info=True)


def cancel_downloads(task_ids=None, db=None):
    from core.audiobook_database import get_audiobook_db
    from core.audiobook_download_state import forget
    database = db if db is not None else get_audiobook_db()
    rows = database.cancel_active_downloads(task_ids)
    for row in rows:
        _cancel_at_client(row)
        forget(row['download_id'])
    return len(rows)


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
        # whoever wanted it. checking profile 1 alone meant another profile's
        # failed grab re-added the book to profile 1's list instead
        if database.mark_wishlist_status(asin, STATUS_FAILED, profile_id=None, error=error):
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
