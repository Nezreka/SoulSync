"""Import/staging controller helpers for Flask-style endpoints."""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from concurrent.futures import as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.imports.album import build_album_import_context, build_album_import_match_payload, resolve_album_artist_context
from core.imports.context import get_import_context_artist, get_import_track_info, normalize_import_context
from core.imports.filename import parse_filename_metadata
from core.imports.pipeline import import_rejection_reason, kept_existing_library_file
from core.imports.staging import (
    AUDIO_EXTENSIONS,
    available_import_sources as _available_import_sources,
    get_import_suggestions_cache,
    get_primary_source as _get_primary_source,
    get_primary_source_label as _get_primary_source_label,
    get_staging_path as _get_staging_path,
    read_staging_file_metadata as _read_staging_file_metadata,
    refresh_import_suggestions_cache as _refresh_import_suggestions_cache,
    search_import_albums as _search_import_albums,
    search_import_tracks as _search_import_tracks,
)
from utils.logging_config import get_logger


module_logger = get_logger("imports.routes")


def _default_read_tags(file_path: str):
    from mutagen import File as MutagenFile

    return MutagenFile(file_path, easy=True)


def _get_single_track_import_context(*args, **kwargs):
    from core.imports.resolution import get_single_track_import_context

    return get_single_track_import_context(*args, **kwargs)


def _backfill_stale_history(worker, candidates, history):
    """Attach history rows older than the 200-row window to their candidates.

    #1289: `get_results(limit=200)` only returns the newest rows. A folder
    with a stale terminal row (e.g. 'partial') older than the window gets no
    history attached in `build_inbox`, so `derive_status` returns 'waiting'
    — invisible under "Needs attention" with no retry path — instead of the
    retryable 'failed' the row would produce. For each candidate the window
    missed, do a direct DB lookup by folder_hash (fallback folder_path),
    mirroring `_is_already_processed`'s lookups.
    """
    import os
    seen_hashes = {r.get('folder_hash') for r in history if r.get('folder_hash')}
    seen_paths = {os.path.normpath(r.get('folder_path')) for r in history if r.get('folder_path')}
    missing = [c for c in candidates
               if getattr(c, 'folder_hash', None) not in seen_hashes
               and os.path.normpath(getattr(c, 'path', None) or '') not in seen_paths]
    if not missing:
        return history
    try:
        conn = worker.database._get_connection()
        try:
            cursor = conn.cursor()
            extra = []
            for cand in missing:
                row = None
                h = getattr(cand, 'folder_hash', None)
                if h:
                    cursor.execute(
                        "SELECT * FROM auto_import_history WHERE folder_hash = ?"
                        " ORDER BY created_at DESC LIMIT 1",
                        (h,),
                    )
                    row = cursor.fetchone()
                if row is None:
                    p = getattr(cand, 'path', None) or ''
                    for q in (p, os.path.normpath(p)):
                        cursor.execute(
                            "SELECT * FROM auto_import_history WHERE folder_path = ?"
                            " ORDER BY created_at DESC LIMIT 1",
                            (q,),
                        )
                        row = cursor.fetchone()
                        if row is not None:
                            break
                if row is not None:
                    extra.append(dict(row))
        finally:
            conn.close()
    except Exception:
        return history
    return history + extra


def _is_active_media_server_ready() -> tuple[bool, str]:
    from core.imports.side_effects import is_active_media_server_ready

    return is_active_media_server_ready()


def _get_allowed_import_roots() -> list[str]:
    from core.settings import config_manager
    from core.imports.paths import docker_resolve_path

    return [
        _get_staging_path(),
        docker_resolve_path(config_manager.get("soulseek.download_path", "./downloads")),
    ]


@dataclass
class ImportRouteRuntime:
    """Dependencies needed to service import/staging HTTP endpoints."""

    get_staging_path: Callable[[], str] = _get_staging_path
    get_allowed_import_roots: Callable[[], list[str]] = _get_allowed_import_roots
    read_staging_file_metadata: Callable[[str, str], Dict[str, Any]] = _read_staging_file_metadata
    read_tags: Callable[[str], Any] = _default_read_tags
    get_primary_source: Callable[[], str] = _get_primary_source
    get_primary_source_label: Callable[[], str] = _get_primary_source_label
    search_import_albums: Callable[..., list] = _search_import_albums
    search_import_tracks: Callable[..., list] = _search_import_tracks
    build_album_import_match_payload: Callable[..., Dict[str, Any]] = build_album_import_match_payload
    resolve_album_artist_context: Callable[..., Any] = resolve_album_artist_context
    build_album_import_context: Callable[..., Dict[str, Any]] = build_album_import_context
    get_single_track_import_context: Callable[..., Dict[str, Any]] = _get_single_track_import_context
    parse_filename_metadata: Callable[[str], Dict[str, Any]] = parse_filename_metadata
    normalize_import_context: Callable[[Dict[str, Any]], Dict[str, Any]] = normalize_import_context
    get_import_context_artist: Callable[[Dict[str, Any]], Dict[str, Any]] = get_import_context_artist
    get_import_track_info: Callable[[Dict[str, Any]], Dict[str, Any]] = get_import_track_info
    process_single_import_file: Callable[["ImportRouteRuntime", Dict[str, Any]], tuple[str, str]] | None = None
    post_process_matched_download: Callable[[str, Dict[str, Any], str], Any] | None = None
    is_active_media_server_ready: Callable[[], tuple[bool, str]] = _is_active_media_server_ready
    add_activity_item: Callable[[Any, Any, Any, Any], Any] | None = None
    refresh_import_suggestions_cache: Callable[[], Any] = _refresh_import_suggestions_cache
    automation_engine: Any = None
    hydrabase_worker: Any = None
    dev_mode_enabled: bool = False
    import_singles_executor: Any = None
    logger: Any = module_logger
    # the profile importing: an own-library profile's files land in its
    # folder (#1199). None = the shared library, as always.
    profile_id: Optional[int] = None


def _validate_import_file(runtime: ImportRouteRuntime, raw_path: Any) -> tuple[Optional[str], str]:
    """Resolve a client path and require containment in a configured source root.

    The roots are the staging folder and the download folder — between them
    they cover everything ``staging_files`` can ever hand the client, and the
    client cannot name a path it wasn't given (there is no path-entry UI).
    Containment is checked AFTER resolution, so a symlink inside staging that
    points elsewhere is rejected too: this endpoint MOVES the file it is given.
    A rejection is logged with the roots, because the message alone can't tell
    a user with a symlinked staging tree what the server actually allows.
    """
    if not isinstance(raw_path, str) or not raw_path:
        return None, "File path is missing"
    try:
        candidate = Path(raw_path).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "File not found"
    if not candidate.is_file():
        return None, "File not found"
    roots = []
    try:
        for root in runtime.get_allowed_import_roots():
            try:
                roots.append(Path(root).resolve(strict=True))
            except (OSError, RuntimeError, TypeError):
                continue
    except (OSError, RuntimeError, TypeError):
        pass
    if not any(root.is_dir() and candidate.is_relative_to(root) for root in roots):
        try:
            runtime.logger.warning(
                "Rejected import of %s: outside the allowed roots %s",
                candidate, [str(r) for r in roots],
            )
        except Exception:  # noqa: BLE001, S110 - the LOGGER is what failed here,
            pass           # so there is nothing left to log it with.
        return None, "File is outside the allowed import folders"
    return str(candidate), ""


# ── Shared staging scan ──────────────────────────────────────────────────────
# Opening the Import page fires staging files/groups/hints together; each used to
# os.walk the whole staging folder AND mutagen-read every file independently — 3×
# the directory walk + 3× the tag I/O on every page open (the import-page scan
# storm + memory spike, issue #935). They all need the same per-file tag data, so
# scan ONCE and let all three derive their views in-memory. A short TTL + a lock
# means the three near-simultaneous page-open requests (and any concurrent caller)
# share a single scan instead of each kicking off a full re-read.
_STAGING_SCAN_LOCK = threading.Lock()
_STAGING_SCAN_TTL = 6.0  # seconds — covers the page-open burst; re-scans after
_staging_scan_cache: Dict[str, Any] = {"path": None, "ts": 0.0, "records": None}
# full_path -> ((rel_path, mtime_ns, size), meta), for the reader that read them.
# survives invalidation on purpose: a moved or edited file changes its stamp.
_staging_meta_cache: Dict[str, Any] = {"reader": None, "entries": {}}
# Directories the last scan could not list, {path, error}. os.walk swallows
# these by default, so an unreadable staging folder answered "0 files" with
# nothing to say why (truenas apps uid vs the container's PUID).
_staging_scan_problems: list = []
# Bumped by invalidate_staging_scan_cache() so a background scan that finishes after an
# import doesn't re-commit stale (pre-import) records (see the generation guard above).
_staging_scan_generation: Dict[str, int] = {"value": 0}

# The inbox-summary slot's lock and build counter live up here, next to
# the generation they guard: invalidate_staging_scan_cache() (below)
# bumps the generation under this lock, and a name resolved at call time
# from 100 lines down is a trap for the next refactor. The slot itself is
# defined with its rationale further down (see _last_inbox_summary).
_last_inbox_summary_lock = threading.RLock()


def _initial_build_seq() -> int:
    return int(time.time() * 1000)


_inbox_build_counter: Dict[str, int] = {"value": _initial_build_seq()}
# Complete builds whose counts never reached the slot (generation guard,
# remember failure): adopting a remembered seq is only sound while no
# such build intervenes (see _remember_inbox_summary).
_inbox_remember_skips: Dict[str, int] = {"value": 0}

# Background-scan plumbing: a large staging folder (whole-library migration, #947) makes
# the synchronous scan exceed gunicorn's 120s request timeout. The runner moves the SAME
# scan off the request thread; the endpoints report progress instead of blocking.
_staging_scan_status: Dict[str, Any] = {
    "status": "idle", "scanned": 0, "total": 0, "path": None, "error": None,
    "started_at": None,
}
_staging_scan_status_lock = threading.Lock()


def _staging_cache_hit(staging_path: str) -> Optional[list]:
    """The cached records for ``staging_path`` if still fresh, else None (no scan triggered)."""
    c = _staging_scan_cache
    if (c["records"] is not None and c["path"] == staging_path
            and (time.time() - c["ts"]) < _STAGING_SCAN_TTL):
        return c["records"]
    return None


def ensure_background_staging_scan(runtime: ImportRouteRuntime, staging_path: str) -> None:
    """Start a background scan for ``staging_path`` unless the cache is warm or a scan for
    this path is already running. Idempotent — safe to call on every request."""
    if _staging_cache_hit(staging_path) is not None:
        return
    with _staging_scan_status_lock:
        if (_staging_scan_status["status"] == "scanning"
                and _staging_scan_status["path"] == staging_path):
            return
        _staging_scan_status.update({"status": "scanning", "scanned": 0, "total": 0,
                                     "path": staging_path, "error": None,
                                     "started_at": time.time()})

    def _run() -> None:
        try:
            _scan_staging_records(runtime, staging_path, progress=_staging_scan_status)
            with _staging_scan_status_lock:
                if _staging_scan_status["path"] == staging_path:
                    _staging_scan_status["status"] = "done"
        except Exception as exc:  # noqa: BLE001 — surface any scan error to the poller
            with _staging_scan_status_lock:
                _staging_scan_status.update({"status": "error", "error": str(exc)})

    threading.Thread(target=_run, name="staging-scan", daemon=True).start()


def get_staging_records_or_status(runtime: ImportRouteRuntime, staging_path: str,
                                  *, grace_seconds: float = 3.0) -> tuple[str, Any]:
    """Non-blocking staging access for the page endpoints. Returns ``("ready", records)``
    when the cache is warm or the scan completes within ``grace_seconds`` (so small/normal
    folders still answer in a single request), otherwise ``("scanning", status_dict)`` after
    making sure a background scan is running."""
    records = _staging_cache_hit(staging_path)
    if records is not None:
        return ("ready", records)
    ensure_background_staging_scan(runtime, staging_path)
    # #1889: past the ttl but never invalidated, the last scan is still the
    # folder as far as anyone moved it. serve it while the rescan runs instead
    # of making every poll wait out the grace and then drop the page to the
    # scanning stub. an import invalidates (records None), so this never
    # serves files an import just moved.
    c = _staging_scan_cache
    if c["records"] is not None and c["path"] == staging_path:
        return ("ready", c["records"])
    deadline = time.time() + max(0.0, grace_seconds)
    while True:
        records = _staging_cache_hit(staging_path)
        if records is not None:
            return ("ready", records)
        with _staging_scan_status_lock:
            status = dict(_staging_scan_status)
        if status.get("status") == "error":
            return ("error", status)
        if time.time() >= deadline:
            return ("scanning", status)
        time.sleep(0.05)


def _records_or_scanning_payload(runtime: ImportRouteRuntime, staging_path: str):
    """Shared helper for the page endpoints: returns ``(records, None)`` when the scan is
    ready, or ``(None, payload)`` when a background scan is still running — the caller
    returns that payload so the page polls + shows progress instead of blocking/timing out.

    A scan error is re-raised so the endpoint's own try/except logs + returns it exactly as
    when the scan ran inline (preserves the existing error contract)."""
    state, val = get_staging_records_or_status(runtime, staging_path)
    if state == "error":
        raise RuntimeError(val.get("error") or "staging scan failed")
    if state == "scanning":
        # #1671: the stub must still say WHICH folder is being read. Without
        # staging_path the page rendered "Import folder not configured" for a
        # folder that is configured — it was only mid-scan.
        return None, {"success": True, "scanning": True,
                      "staging_path": staging_path,
                      "progress": {"scanned": val.get("scanned", 0),
                                   "total": val.get("total", 0)}}
    return val, None


def staging_scan_status(runtime: ImportRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Lightweight, instant scan-progress poll for the page (no grace-wait, no file I/O) —
    ``ready`` true once the cache is warm and the files/groups/hints calls will answer fast."""
    try:
        staging_path = runtime.get_staging_path()
    except Exception as exc:
        return {"success": False, "error": str(exc)}, 500
    with _staging_scan_status_lock:
        st = dict(_staging_scan_status)
    return {
        "success": True,
        "ready": _staging_cache_hit(staging_path) is not None,
        "status": st.get("status", "idle"),
        "scanned": st.get("scanned", 0),
        "total": st.get("total", 0),
        "error": st.get("error"),
    }, 200


def _scan_staging_records(runtime: ImportRouteRuntime, staging_path: str,
                          *, progress: Optional[Dict[str, Any]] = None) -> list[Dict[str, Any]]:
    """Walk staging + read each audio file's tags ONCE, returning per-file records
    that staging files/groups/hints all derive from. Briefly cached + locked so the
    page-open trio shares a single scan rather than each re-walking and re-reading.

    ``progress`` (optional, default None = unchanged behaviour) is a dict the scan
    updates live with ``total`` (audio-file count, from a fast first pass) and ``scanned``
    (tag-reads done so far) so a background runner can report progress. A generation guard
    keeps a scan that finishes AFTER an import (which bumped ``_staging_scan_generation``)
    from committing stale records to the cache."""
    now = time.time()
    cached = _staging_scan_cache
    if (cached["records"] is not None and cached["path"] == staging_path
            and (now - cached["ts"]) < _STAGING_SCAN_TTL):
        return cached["records"]

    with _STAGING_SCAN_LOCK:
        # Double-check: another request may have filled the cache while we waited.
        now = time.time()
        if (cached["records"] is not None and cached["path"] == staging_path
                and (now - cached["ts"]) < _STAGING_SCAN_TTL):
            return cached["records"]

        start_generation = _staging_scan_generation["value"]

        # #1889: tags are kept per file between scans and only re-read when the
        # file changed. a full-library staging folder took minutes to re-read,
        # so the 6s ttl meant the page kept the folder in a scan forever.
        reader = runtime.read_staging_file_metadata
        if _staging_meta_cache["reader"] is not reader:
            _staging_meta_cache.update({"reader": reader, "entries": {}})
        old_entries = _staging_meta_cache["entries"]
        new_entries: Dict[str, tuple] = {}

        # Pass 1 (fast): collect the audio-file list — no tag I/O — so we know the total.
        audio_files: list[tuple[str, str, Optional[str]]] = []
        problems: list[Dict[str, str]] = []

        def _unreadable(err: OSError) -> None:
            problems.append({"path": err.filename or staging_path,
                             "error": err.strerror or str(err)})

        if os.path.isdir(staging_path):
            for root, _dirs, filenames in os.walk(staging_path, onerror=_unreadable):
                rel_dir = os.path.relpath(root, staging_path)
                top_folder = rel_dir.split(os.sep)[0] if rel_dir != "." else None
                for fname in filenames:
                    if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                        audio_files.append((root, fname, top_folder))
        # The root itself unreadable is not "no files", it is an error the
        # page has to show: nothing under it can ever be imported.
        if problems and not audio_files and os.path.normpath(problems[0]["path"]) == os.path.normpath(staging_path):
            raise PermissionError(
                f"Import folder is not readable: {problems[0]['error']} ({staging_path}). "
                f"If it is a bind mount, the folder's owner must match the container's PUID/PGID."
            )
        if progress is not None:
            progress["total"] = len(audio_files)
            progress["scanned"] = 0

        # Pass 2 (slow): read each file's tags, updating progress as we go.
        records: list[Dict[str, Any]] = []
        for root, fname, top_folder in audio_files:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, staging_path)
            try:
                st = os.stat(full_path)
                stamp = (rel_path, st.st_mtime_ns, st.st_size)
            except OSError:
                stamp = None
            hit = old_entries.get(full_path)
            if stamp is not None and hit is not None and hit[0] == stamp:
                meta = hit[1]
            else:
                meta = reader(full_path, rel_path)
            if stamp is not None:
                new_entries[full_path] = (stamp, meta)
            records.append({
                "filename": fname, "rel_path": rel_path, "full_path": full_path,
                "extension": os.path.splitext(fname)[1].lower(),
                "title": meta["title"], "album": meta["album"],
                "artist": meta["artist"], "albumartist": meta["albumartist"],
                "track_number": meta["track_number"], "disc_number": meta["disc_number"],
                "duration_ms": meta.get("duration_ms", 0), "bitrate": meta.get("bitrate", 0),
                "size": meta.get("size", 0),
                "top_folder": top_folder,
            })
            if progress is not None:
                progress["scanned"] += 1

        # files that left staging drop out here, so the cache never outgrows the folder
        _staging_meta_cache["entries"] = new_entries

        # Generation guard: if an import invalidated the cache mid-scan, these records are
        # stale — return them to this caller but do NOT commit them as the shared cache.
        if _staging_scan_generation["value"] == start_generation:
            _staging_scan_cache.update({"path": staging_path, "ts": time.time(), "records": records})
            _staging_scan_problems[:] = problems
        return records


def invalidate_staging_scan_cache() -> None:
    """Drop the cached staging scan (call after an import moves/removes files so the
    next files/groups/hints request reflects the new state immediately). Also bumps the
    scan generation so an in-flight background scan won't re-commit pre-import records."""
    # The bump takes the inbox-summary lock (defined further down; resolved
    # at call time) so it is atomic against _remember_inbox_summary's
    # check-then-write: a remember either sees the new generation and
    # skips, or completes before the bump — never slips stale counts in
    # between the check and the write.
    with _last_inbox_summary_lock:
        _staging_scan_generation["value"] += 1
    _staging_scan_cache.update({"path": None, "ts": 0.0, "records": None})
    _staging_scan_problems.clear()
    # the scanning payload's history cache is an invalidation target too:
    # an action that moves files must not leave stale history rows behind
    # for the rest of its TTL.
    _scanning_history_cache.update({"path": None, "ts": 0.0, "rows": None, "problems": None})


def staging_files(runtime: ImportRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Scan the staging folder and return audio files with tag metadata."""
    try:
        staging_path = runtime.get_staging_path()
        os.makedirs(staging_path, exist_ok=True)

        records, scanning = _records_or_scanning_payload(runtime, staging_path)
        if scanning is not None:
            return scanning, 200

        files = [
            {
                "filename": r["filename"],
                "rel_path": r["rel_path"],
                "full_path": r["full_path"],
                "title": r["title"],
                "artist": r["albumartist"] or r["artist"] or "Unknown Artist",
                "album": r["album"],
                "track_number": r["track_number"],
                "disc_number": r["disc_number"],
                "extension": r["extension"],
            }
            for r in records
        ]

        files.sort(key=lambda f: f["filename"].lower())
        return {"success": True, "files": files, "staging_path": staging_path,
                "problems": list(_staging_scan_problems)}, 200
    except Exception as exc:
        runtime.logger.error("Error scanning staging files: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def _inbox_worker_payload(worker: Any, status: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(status, dict):
        status = {}
    return {
        "available": worker is not None,
        "running": bool(status.get("running")),
        "paused": bool(status.get("paused")),
        "current_status": status.get("current_status", "idle"),
        "last_scan_time": status.get("last_scan_time"),
        "stats": status.get("stats") or {},
    }


# While a scan runs the page polls every ~1.5s; re-reading 200 history rows
# and re-parsing their match JSON on every poll is waste on exactly the slow
# installs #1671 is about. The history view may lag a few seconds during a
# scan — the scan's own completion brings the exact rows.
_SCANNING_HISTORY_TTL = 5.0
_scanning_history_cache: Dict[str, Any] = {"path": None, "ts": 0.0, "rows": None, "problems": None}

# The header strip's numbers from the last COMPLETE inbox build (#1874).
# A scanning reply cannot recount the folder yet, but dropping the strip's
# stats for the whole scan is the reported bug — a cold page open mid-scan
# (or a return from the matcher) has no client-side snapshot to fall back
# on, so the server carries the last counts it actually counted. Never
# fabricated: with no complete build for the folder on record the scanning
# reply still carries no summary at all, exactly as #1671 requires.
# Bounds, so last-known never becomes long-stale: a SINGLE slot (only the
# most recently built folder's summary is kept — the path check means a
# different folder never inherits it, it just gets none), a one-hour TTL,
# and an empty-folder build CLEARS the slot instead of remembering zeroes —
# serving a remembered "0 items" for a batch nobody has counted is the
# fabrication #1671 forbids, in older clothes. A complete build is
# authoritative for the folder's readable staged content even when it also
# reports problems (unreadable subdirectories are surfaced separately in
# the reply's problems list): an empty build with problems still clears.
# The TTL bounds cross-session staleness, not intra-batch staleness: while
# a client polls, every complete build between scans refreshes the entry,
# but the auto-import worker moves files without ever building an inbox,
# so a cold open late in a worker-only batch can show counts up to the TTL
# old while its first scan runs. That is the same last-known tradeoff the
# page's own snapshot makes; the scan banner's live progress total is the
# current file count in that window, and the scan landing replaces the
# numbers. Age is judged at the scan's start, so an entry that was fresh
# enough when a scan began is served for that scan's whole duration, even
# past the TTL — expiring mid-scan would be the original bug returning.
# Deliberately NOT cleared by invalidate_staging_scan_cache(): that is
# called after imports and uploads move files, and the re-scan that
# follows is precisely when the strip must keep showing the batch's
# numbers until the scan lands and a fresh complete build replaces them
# (the same lifetime the page's own in-memory snapshot has). One known
# corollary: after an upload, the post-upload scan can show pre-upload
# counts (without the just-uploaded files) until that scan lands.
# The entry is one immutable tuple swapped under a lock in a single
# assignment — (path, summary|None, ts, wire_seq, start_seq, cleared) —
# so a reader can never tear a path from another folder's summary across
# a concurrent complete build, and writers are serialised. Ordering is by
# START sequence: every complete build takes the next sequence number
# when its counting begins, and a build whose start is older than the
# remembered build's start never overwrites it, however late it finishes.
# The generation guard is the second check (a build whose records
# predate an invalidation never remembers — the same discipline
# _scan_staging_records follows). The WIRE sequence a summary carries
# ("seq") is the start sequence of the build that first counted those
# numbers: a build whose numbers are unchanged from the remembered ones
# ADOPTS the remembered wire sequence instead of minting a new one, so
# identical polls stay deeply equal (the page's structural sharing
# survives) and sequence comparisons are only ever asked to order
# summaries whose numbers differ. Clearing writes a tombstone that keeps
# the folder path, both sequences, and the cleared build's counted
# numbers, so a late older build cannot resurrect cleared counts and an
# identical later build can adopt the tombstone's wire sequence instead
# of churning a new one per poll. Like the neighbouring caches, the slot
# and its sequences are per-process state: coherent with the shipped
# single-worker gunicorn config; a multi-worker deployment would need
# both in shared storage before seq comparisons meant anything across
# processes.
_LAST_INBOX_SUMMARY_TTL = 3600.0
_LAST_INBOX_SUMMARY_HARD_TTL = 4 * _LAST_INBOX_SUMMARY_TTL
_RECENT_SCAN_DONE_SECONDS = 120.0
# The slot, and below it the "skips at write" marker adoption checks.
# Entry: (path, summary|None, ts, wire_seq, start_seq, cleared|None).
_last_inbox_summary: Dict[str, Any] = {"entry": None, "skips": 0}
# The build counter is seeded from the wall clock rather than 0: a tab
# that stays open across a server restart keeps its snapshot's seq, and
# a restarted counter starting near 0 would lose every comparison
# against it until the scan lands. Clock-seeded, a later process always
# starts ahead of any earlier process's increments (a process would need
# millions of builds in its lifetime to catch up with the clock).
# MILLISECONDS, not nanoseconds: the seq is compared in the browser, and
# a ns value (~1.8e18) exceeds JavaScript's safe-integer range (~9e15) —
# JSON parsing would round adjacent seqs to equal and silently break the
# ordering. A ms seed (~1.7e12) stays exact, with headroom for ~9e12
# builds. (A wall clock stepping BACKWARDS across a restart could still
# seed below an open tab's snapshot seq; that tab then keeps its
# snapshot until the scan lands and replaces both — one scan, accepted.)
# (The counter and the lock are defined by _staging_scan_generation:
# invalidate_staging_scan_cache() bumps the generation under the lock.)


def _copy_inbox_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(summary)
    if isinstance(copied.get("by_status"), dict):
        copied["by_status"] = dict(copied["by_status"])
    return copied


def _next_build_seq() -> int:
    with _last_inbox_summary_lock:
        _inbox_build_counter["value"] += 1
        return _inbox_build_counter["value"]


def _inbox_summary_cleared_seq(staging_path: str) -> Optional[int]:
    """The wire sequence of the slot's tombstone, if the slot is cleared
    FOR THIS FOLDER: a scanning reply carries it so a tab whose snapshot
    predates the clear (an empty complete build) drops that snapshot
    instead of showing counts a later build already superseded with
    zero. Folder-scoped like the summary itself — the single slot may
    hold another folder's tombstone after a path switch."""
    with _last_inbox_summary_lock:
        entry = _last_inbox_summary["entry"]
        if entry is not None and entry[1] is None and entry[0] == staging_path:
            return entry[3]
        return None


def _remember_inbox_summary(staging_path: str, summary: Dict[str, Any], *,
                            generation: Optional[int] = None,
                            build_seq: Optional[int] = None) -> Optional[int]:
    """Remember a complete build's counted summary (without its "seq").

    ``build_seq`` is the build's START sequence. Returns the wire sequence
    the build's reply should carry, or None when the build was skipped by
    an ordering guard (its reply then carries its own start sequence,
    which still orders it correctly against the remembered entry for any
    tab that received it)."""
    with _last_inbox_summary_lock:
        if generation is not None and generation != _staging_scan_generation["value"]:
            # Its counts reached a tab but never the slot: adoption must
            # not reuse an older sequence past it (see the skips check).
            _inbox_remember_skips["value"] += 1
            return None
        entry = _last_inbox_summary["entry"]
        start = build_seq if build_seq is not None else _next_build_seq()
        if entry is not None and entry[4] > start:
            return None
        counted_zero = not summary.get("items") and not summary.get("files")

        def _store(new_entry) -> None:
            _last_inbox_summary["entry"] = new_entry
            _last_inbox_summary["skips"] = _inbox_remember_skips["value"]

        if entry is not None and entry[0] == staging_path:
            no_skips_since = (
                _inbox_remember_skips["value"] == _last_inbox_summary.get("skips", 0))
            if entry[1] is not None:
                previous = {k: v for k, v in entry[1].items() if k != "seq"}
                if previous == summary and no_skips_since:
                    # Unchanged numbers, and no unremembered build in
                    # between: adopt the wire sequence they were first
                    # counted under, refresh the timestamp. (With a skip
                    # in between, a tab may hold the skipped build's
                    # different counts under a higher sequence — reusing
                    # the old one would order this fresher count behind
                    # it, so the build falls through and mints its own.)
                    _store((staging_path, entry[1], time.time(), entry[3], start, None))
                    return entry[3]
            elif entry[5] is not None and entry[5] == summary and no_skips_since:
                # A tombstone holding exactly these (zero) numbers:
                # adopt its wire sequence instead of churning a new one.
                wire = entry[3]
                _store((staging_path, None, time.time(), wire, start,
                        _copy_inbox_summary(summary)))
                return wire
        if counted_zero:
            _store((staging_path, None, time.time(), start, start,
                    _copy_inbox_summary(summary)))
            return start
        _store((staging_path, {**_copy_inbox_summary(summary), "seq": start},
                time.time(), start, start, None))
        return start


def _last_inbox_summary_for(staging_path: str, *,
                            scan_started_at: Optional[float] = None) -> Optional[Dict[str, Any]]:
    with _last_inbox_summary_lock:
        entry = _last_inbox_summary["entry"]
        if entry is None:
            return None
        path, summary, remembered_at, _wire_seq, _start_seq, _cleared = entry
        if path != staging_path or summary is None:
            return None
        # Age is judged at the scan's start when the caller is a scanning
        # reply: an entry fresh enough when the scan began stays servable
        # for that scan's whole duration (a >1h scan must not lose the
        # strip mid-scan — the original bug), while an entry already past
        # the TTL when the scan began is never served at all. A hard cap
        # still applies either way: a scan whose thread hangs (a stalled
        # network walk) must not make last-known immortal.
        now = time.time()
        if (now - remembered_at) >= _LAST_INBOX_SUMMARY_HARD_TTL:
            return None
        judged_at = now
        if scan_started_at is not None and scan_started_at < judged_at:
            judged_at = scan_started_at
        if (judged_at - remembered_at) >= _LAST_INBOX_SUMMARY_TTL:
            return None
        return _copy_inbox_summary(summary)


def _scanning_history_rows(worker: Any, staging_path: str,
                           status: Dict[str, Any]) -> tuple[list, list]:
    """(rows, problems) the inbox can show while the staging scan runs.

    Terminal history (imported / dismissed) stands as-is. Everything the
    worker has not finished with — needs review, needs identifying, queued,
    importing, failed, partial — still has its files in the folder as far as
    anyone knows mid-scan, so those rows are built through pseudo-candidates
    (in_staging, carrying the history row's status) instead of being dropped
    the way ``build_inbox`` drops file-less unfinished rows. A failed row
    built this way keeps its Retry action during the scan. Briefly cached:
    the page polls faster than history changes during a scan."""
    now = time.time()
    cache = _scanning_history_cache
    if (cache["rows"] is not None and cache["path"] == staging_path
            and (now - cache["ts"]) < _SCANNING_HISTORY_TTL):
        return cache["rows"], cache["problems"]

    from core.imports.inbox import build_inbox

    history = worker.get_results(limit=200)
    # History is INSERT-per-attempt, so one folder can carry several rows —
    # and its hash is content-derived, so fixing a file in the folder gives
    # the next attempt a NEW hash for the same path. Identity here is hash
    # OR path (the same rule build_inbox claims by); keep only the newest
    # row per folder, or a folder renders twice during scans — once per
    # attempt — and a superseded row can swallow the live one.
    seen_identities = set()
    deduped = []
    for hist in history:
        # normpath only a real path: normpath("") is ".", which would give
        # every pathless row the same identity and drop all but the newest.
        keys = {k for k in (hist.get("folder_hash"),
                            os.path.normpath(hist["folder_path"])
                            if hist.get("folder_path") else None) if k}
        if keys & seen_identities:
            continue
        seen_identities |= keys
        deduped.append(hist)
    history = deduped
    rows = build_inbox([], [], history, [], staging_root=staging_path)

    staged_statuses = {"pending_review", "needs_identification", "approved",
                       "processing", "failed", "partial"}
    by_id = {h.get("id"): h for h in history}
    kept: list = []
    staged_rows: list = []
    for row in rows:
        hist = by_id.get(row.get("history_id"))
        if (hist is not None and hist.get("status") in staged_statuses
                and hist.get("folder_path")):
            staged_rows.append(hist)
        else:
            kept.append(row)
    seen_ids = {r.get("history_id") for r in rows}
    staged_rows.extend(
        h for h in history
        if h.get("id") not in seen_ids and h.get("status") in staged_statuses
        and h.get("folder_path"))

    if staged_rows:
        from types import SimpleNamespace

        candidates = []
        seen_keys = set()
        for hist in staged_rows:
            path = hist.get("folder_path") or ""
            key = hist.get("folder_hash") or path
            if not path or key in seen_keys:
                continue
            seen_keys.add(key)
            candidates.append(SimpleNamespace(
                path=path,
                name=hist.get("folder_name") or hist.get("album_name") or "",
                audio_files=[],
                # legacy rows can carry an empty hash; a synthetic key keeps
                # two such rows from sharing one identity in the page's list
                # (the history claim itself falls back to the folder path).
                folder_hash=hist.get("folder_hash") or f"scan-{hist.get('id')}",
                is_single=(hist.get("total_files") or 0) <= 1,
            ))
        kept.extend(
            r for r in build_inbox(candidates, [], staged_rows,
                                   status.get("active_imports") or [],
                                   staging_root=staging_path)
            if r["in_staging"])

    problems = list(_staging_scan_problems)
    seen_problems = {(p["path"], p["error"]) for p in problems}
    for problem in status.get("scan_problems") or []:
        entry = {"path": problem.get("path", ""), "error": problem.get("error", "")}
        if entry["path"] and (entry["path"], entry["error"]) not in seen_problems:
            problems.append(entry)

    _scanning_history_cache.update(
        {"path": staging_path, "ts": time.time(), "rows": kept, "problems": problems})
    return kept, problems


def _scanning_inbox_payload(scanning: Dict[str, Any], staging_path: str,
                            worker: Any) -> Dict[str, Any]:
    """What the inbox can honestly say while the staging scan is still running (#1671).

    The scan only gates the *staging* rows: history lives in the database and
    the worker's state is in memory, so both can be served immediately. The
    page keeps showing them (plus the folder path and the auto-import switch)
    under a scan-progress banner instead of blanking to zeroes and claiming
    the folder is not configured. No fabricated ``summary``: the staged
    counts are exactly what is not known yet, and a fabricated "0 items" is
    the bug #1671 fixed. The one summary a scanning reply may carry is the
    last COMPLETE build's, for this same folder (#1874) — real counted
    numbers the page's header strip keeps showing until the scan lands,
    instead of vanishing whenever the page was not already open with a
    client-side snapshot when processing began. (On a cold open the header
    can therefore disagree with the filter-pill counts for the scan's
    duration: the pills count the history rows in ``items``, the header
    shows the last build's staged rows. The scan landing reconciles them.)
    Enrichment must never break the poll — any failure falls back to the
    bare scanning payload."""
    payload = dict(scanning)
    try:
        with _staging_scan_status_lock:
            scan_status = dict(_staging_scan_status)
        # A just-finished scan counts too: this reply was already
        # determined to be a scanning reply, and the scan may have
        # finished in the gap since — its start is still the right moment
        # to judge the entry's age. started_at is left in place after a
        # scan for exactly this read, but only a RECENT done is read as
        # this reply's scan; a stale done belongs to an earlier scan
        # whose polls have long ended (a new scan re-stamps it anyway).
        scan_started = scan_status.get("started_at")
        scan_started_at = (
            scan_started
            if scan_status.get("path") == staging_path
            and scan_started is not None
            and (
                scan_status.get("status") == "scanning"
                or (
                    scan_status.get("status") == "done"
                    and (time.time() - scan_started) < _RECENT_SCAN_DONE_SECONDS
                )
            )
            else None
        )
        summary = _last_inbox_summary_for(staging_path, scan_started_at=scan_started_at)
    except Exception:  # noqa: BLE001 — the summary is a nicety during a scan
        summary = None
    if summary is not None:
        payload["summary"] = summary
    else:
        try:
            cleared_seq = _inbox_summary_cleared_seq(staging_path)
        except Exception:  # noqa: BLE001 — same nicety rule as the summary
            cleared_seq = None
        if cleared_seq is not None:
            payload["summary_cleared_seq"] = cleared_seq
    status: Dict[str, Any] = {}
    rows: list = []
    problems: list = []
    if worker is not None:
        try:
            status = worker.get_status() or {}
        except Exception:  # noqa: BLE001 — worker state is a nicety during a scan
            status = {}
        if not isinstance(status, dict):
            status = {}
        try:
            rows, problems = _scanning_history_rows(worker, staging_path, status)
        except Exception:  # noqa: BLE001 — history is a nicety during a scan
            rows, problems = [], []
    payload["items"] = rows
    payload["problems"] = problems
    payload["worker"] = _inbox_worker_payload(worker, status)
    return payload


def inbox(runtime: ImportRouteRuntime, worker: Any) -> tuple[Dict[str, Any], int]:
    """Every staging item with its state: the page's one list.

    ``worker`` is the auto-import worker (its enumeration is the unit of
    work, its history and live state are the status). None when the worker
    failed to boot; the inbox then lists staging with nothing joined."""
    try:
        staging_path = runtime.get_staging_path()
        # Captured BEFORE the records are fetched, and re-checked at
        # remember time: a build that spans an invalidation never
        # remembers, even in the rare case its records turned out fresh
        # (the next poll's build, seconds later, remembers instead).
        # Skipping is the safe direction — the dangerous one is storing
        # pre-invalidation counts as fresh. One accepted consequence: a
        # skipped build leaves the PREVIOUS entry in place, so a cold
        # open during the following scan shows that older entry. A tab
        # that received the skipped build keeps its own numbers — they
        # were its complete reply, and its snapshot's sequence (the
        # skipped build's start) outranks the entry's — which is ordinary
        # snapshot semantics; the guard's distrust is about what the
        # SERVER serves to everyone else.
        build_generation = _staging_scan_generation["value"]
        records, scanning = _records_or_scanning_payload(runtime, staging_path)
        if scanning is not None:
            return _scanning_inbox_payload(scanning, staging_path, worker), 200
        # The build's start sequence: taken now, before the counting work
        # (enumeration, history, build), so ordering never follows finish
        # order (see the slot comment above _last_inbox_summary).
        build_seq = _next_build_seq()

        from core.imports.inbox import build_inbox, summarize

        problems = list(_staging_scan_problems)
        candidates: list = []
        history: list = []
        status: Dict[str, Any] = {}
        if worker is not None:
            candidates, walk_problems = worker.enumerate_candidates(staging_path)
            seen = {(p["path"], p["error"]) for p in problems}
            problems.extend(p for p in walk_problems if (p["path"], p["error"]) not in seen)
            history = worker.get_results(limit=200)
            status = worker.get_status()
            # #1289: a stale terminal row (e.g. 'partial') older than the
            # 200-row window never attaches to its candidate, so the folder
            # shows as 'waiting' (invisible under "Needs attention") instead
            # of retryable 'failed'. Backfill per-candidate lookups for any
            # candidate the window missed.
            history = _backfill_stale_history(worker, candidates, history)
        else:
            from core.auto_import_worker import AutoImportWorker
            bare = AutoImportWorker.__new__(AutoImportWorker)
            candidates, walk_problems = bare.enumerate_candidates(staging_path)
            problems.extend(walk_problems)

        rows = build_inbox(candidates, records, history, status.get("active_imports") or [],
                           staging_root=staging_path)
        # "seq" rides in the served summary so the page can order a
        # scanning reply's summary against its own snapshot by build. The
        # remember call returns the wire sequence (the start sequence, or
        # the adopted one when the numbers are unchanged); when an
        # ordering guard skipped the build, the reply still carries the
        # build's own start sequence.
        counted = summarize(rows)
        try:
            wire_seq = _remember_inbox_summary(
                staging_path, counted, generation=build_generation,
                build_seq=build_seq)
        except Exception as exc:  # noqa: BLE001 — remembering must never fail a built inbox
            runtime.logger.error("Error remembering import inbox summary: %s", exc)
            wire_seq = None
            # No clear needed on failure: the slot still holds an OLDER
            # entry whose wire sequence is below this build's, so every
            # tab orders this build's snapshot ahead of it by sequence —
            # and clearing would only strip stats from OTHER tabs whose
            # snapshots are older still. What failure must not do is
            # wedge the slot (a malformed entry would fail every later
            # remember the same way): reset it to empty so the next
            # build remembers cleanly. The reset is an RLock take plus a
            # plain assignment — neither can realistically raise.
            with _last_inbox_summary_lock:
                _inbox_remember_skips["value"] += 1
                _last_inbox_summary["entry"] = None
        summary = {**counted, "seq": wire_seq if wire_seq is not None else build_seq}
        return {
            "success": True,
            "staging_path": staging_path,
            "items": rows,
            "summary": summary,
            "problems": problems,
            "worker": _inbox_worker_payload(worker, status),
        }, 200
    except Exception as exc:
        runtime.logger.error("Error building import inbox: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def staging_groups(runtime: ImportRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Auto-detect album groups from staging files based on their tags."""
    try:
        staging_path = runtime.get_staging_path()
        if not os.path.isdir(staging_path):
            return {"success": True, "groups": []}, 200

        records, scanning = _records_or_scanning_payload(runtime, staging_path)
        if scanning is not None:
            return scanning, 200

        album_groups = {}
        for r in records:
            album = r["album"]
            artist = r["albumartist"] or r["artist"]
            if not album or not artist:
                continue

            key = (album.lower().strip(), artist.lower().strip())
            if key not in album_groups:
                album_groups[key] = {"album": album.strip(), "artist": artist.strip(), "files": []}
            album_groups[key]["files"].append(
                {
                    "filename": r["filename"],
                    "full_path": r["full_path"],
                    "title": r["title"],
                    "track_number": r["track_number"],
                }
            )

        groups = []
        for group in album_groups.values():
            if len(group["files"]) >= 2:
                group["files"].sort(key=lambda f: f.get("track_number") or 999)
                groups.append(
                    {
                        "album": group["album"],
                        "artist": group["artist"],
                        "file_count": len(group["files"]),
                        "files": group["files"],
                        "file_paths": [f["full_path"] for f in group["files"]],
                    }
                )

        groups.sort(key=lambda g: g["file_count"], reverse=True)
        return {"success": True, "groups": groups}, 200
    except Exception as exc:
        runtime.logger.error("Error building staging groups: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def staging_hints(runtime: ImportRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Extract album search hints from staging folder tags and folder names."""
    try:
        staging_path = runtime.get_staging_path()
        if not os.path.isdir(staging_path):
            return {"success": True, "hints": []}, 200

        records, scanning = _records_or_scanning_payload(runtime, staging_path)
        if scanning is not None:
            return scanning, 200

        tag_albums = {}
        folder_hints = {}
        for r in records:
            if r["top_folder"]:
                folder_hints[r["top_folder"]] = folder_hints.get(r["top_folder"], 0) + 1

            album = r["album"]
            artist = r["artist"] or r["albumartist"]
            if album:
                key = (album.strip(), (artist or "").strip())
                tag_albums[key] = tag_albums.get(key, 0) + 1

        queries = []
        seen_queries_lower = set()

        for (album, artist), _count in sorted(tag_albums.items(), key=lambda x: -x[1]):
            query = f"{album} {artist}".strip() if artist else album
            if query.lower() not in seen_queries_lower:
                seen_queries_lower.add(query.lower())
                queries.append(query)

        for folder, _count in sorted(folder_hints.items(), key=lambda x: -x[1]):
            query = folder.replace("_", " ")
            if query.lower() not in seen_queries_lower:
                seen_queries_lower.add(query.lower())
                queries.append(query)

        return {"success": True, "hints": queries[:5]}, 200
    except Exception as exc:
        runtime.logger.error("Error getting staging hints: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def staging_suggestions() -> tuple[Dict[str, Any], int]:
    """Return cached import suggestions and readiness state."""
    cache = get_import_suggestions_cache()
    return {
        "success": True,
        "suggestions": cache["suggestions"],
        "ready": cache["built"],
        "primary_source": _get_primary_source_label(),
    }, 200


def search_albums(runtime: ImportRouteRuntime, query: str, limit: int = 12,
                   source: str = "") -> tuple[Dict[str, Any], int]:
    """Search albums for manual import using the active metadata provider,
    or an explicitly-chosen source when ``source`` is given (the Import
    Search source picker) — see search_import_albums()'s docstring for why
    that bypass exists."""
    try:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Missing query parameter"}, 400

        limit = min(int(limit), 50)
        source_override = (source or "").strip().lower() or None
        primary_source = runtime.get_primary_source()
        if not source_override and primary_source == "hydrabase" and runtime.hydrabase_worker and runtime.dev_mode_enabled:
            runtime.hydrabase_worker.enqueue(query, "albums")

        albums = runtime.search_import_albums(query, limit=limit, source_override=source_override)
        # The label names the user's CONFIGURED source (Spotify Free reads as
        # 'spotify', not the deezer fallback the functional source downgrades to).
        return {"success": True, "albums": albums,
                "primary_source": runtime.get_primary_source_label(),
                "source_override": source_override}, 200
    except Exception as exc:
        runtime.logger.error("Error searching albums for import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def search_sources() -> tuple[Dict[str, Any], int]:
    """Source picker options for Import Search — every metadata source with
    a live client, the primary one flagged 'active'. Same shape as
    /api/reidentify/sources (core.imports.rematch_search.available_sources)."""
    try:
        return {"success": True, "sources": _available_import_sources()}, 200
    except Exception as exc:
        module_logger.error("Error listing import search sources: %s", exc)
        return {"success": False, "error": str(exc), "sources": []}, 500


def _get_import_db(runtime) -> Any:
    """The music database for library lookups, or None when unavailable.

    Every library pre-check fails open without it (proceed, never skip).
    """
    try:
        from database.music_database import get_database
        return get_database()
    except Exception as exc:                                # noqa: BLE001
        runtime.logger.debug("[Library Check] database unavailable: %s", exc)
        return None


def _annotate_library_status(runtime: ImportRouteRuntime,
                             payload: Dict[str, Any], source: str,
                             album_name: str = "") -> Dict[str, Any]:
    """Attach per-track existing-library decisions to an album-match payload.

    Runs at the matching stage, so the review UI can show what's already
    owned BEFORE the user imports. The process endpoint re-checks (never
    trusts the client's copy) — this is presentation, not enforcement.
    """
    from core.imports.library_check import check_track_library_status

    matches = payload.get("matches") or []
    if not payload.get("success") or not matches:
        return payload
    db = _get_import_db(runtime)
    # album_name is optional on the way in (callers may pass only album_id);
    # fall back to the metadata source's album title so release scoping
    # still works and the review UI shows skip badges.
    resolved_album_name = (
        album_name
        or str((payload.get("album") or {}).get("name") or "")
        or str((payload.get("album") or {}).get("title") or "")
    )
    quality_profile = _resolve_gate_profile(runtime)
    for match in matches:
        staging_file = match.get("staging_file") or {}
        track = match.get("track") or {}
        if not staging_file or not track:
            match["library_status"] = None
            continue
        match["library_status"] = check_track_library_status(
            db,
            track=track,
            staging_file_path=staging_file.get("full_path", ""),
            match_type=match.get("match_type"),
            confidence=match.get("confidence", 0) or 0,
            source_hint=source,
            album_name=resolved_album_name,
            quality_profile=quality_profile,
            quiet=True,  # presentation only; enforcement re-checks and logs
        )
    return payload


def _resolve_gate_profile(runtime: ImportRouteRuntime) -> dict:
    """Best-effort quality profile for the library-check gate.

    Mirrors what the pipeline will resolve for the replacement arbiter
    (``load_profile_by_id(runtime.profile_id)``); {} on any failure, in
    which case the gate falls back to the global ``import.replace_lower_quality``
    setting — exactly like the pipeline does.
    """
    try:
        from core.quality.selection import load_profile_by_id
        profile_id = getattr(runtime, "profile_id", None)
        profile = load_profile_by_id(profile_id)
        return profile if isinstance(profile, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def album_match(runtime: ImportRouteRuntime, data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
    """Match staging files to an album's tracklist."""
    try:
        data = data or {}
        album_id = data.get("album_id")
        album_name = data.get("album_name", "")
        album_artist = data.get("album_artist", "")
        source = str(data.get("source") or "").strip().lower()
        filter_file_paths = set(data.get("file_paths", []))
        if not album_id:
            return {"success": False, "error": "Missing album_id"}, 400

        if not source:
            runtime.logger.warning(
                "[Import Match] Missing 'source' on album_id=%s - lookup will "
                "guess via primary-source priority chain. If this fires "
                "consistently, a frontend caller is dropping source from "
                "the match POST body.",
                album_id,
            )

        payload = runtime.build_album_import_match_payload(
            album_id,
            album_name=album_name,
            album_artist=album_artist,
            file_paths=filter_file_paths,
            source=source or None,
        )
        # Existing-library pre-check: annotate each pairing so the review UI
        # shows what's already owned before anything is imported.
        payload = _annotate_library_status(runtime, payload, source, album_name)
        return payload, 200
    except Exception as exc:
        runtime.logger.error("Error matching album for import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# an upload is capped per file, not per request: the browser sends one
# request per file so a whole album does not have to fit one body.
UPLOAD_MAX_BYTES = 1_024 * 1_024 * 1_024  # 1 GB, a DSD or a long WAV fits


def _safe_relative_path(raw: str) -> Optional[str]:
    """A relative path a browser sent, cleaned: forward or back slashes,
    no absolute, no dot-dot, no empty segment. None when it is not one."""
    text = str(raw or "").replace("\\", "/").strip().strip("/")
    if not text:
        return None
    parts = []
    for part in text.split("/"):
        part = part.strip()
        if part in ("", ".", ".."):
            return None
        if ":" in part:
            return None
        parts.append(part)
    return os.path.join(*parts)


def upload_to_staging(runtime: ImportRouteRuntime, files: list, relative_paths: list) -> tuple[Dict[str, Any], int]:
    """Write browser-uploaded audio into the import folder, keeping the
    folder structure the browser sent (a dropped folder keeps its name, so
    it lands as one album). Audio extensions only; the staging cache is
    dropped so the inbox sees the files on its next read.

    ``files`` are werkzeug FileStorage objects; ``relative_paths`` the
    matching ``webkitRelativePath`` (or filename) for each."""
    try:
        staging_path = runtime.get_staging_path()
        os.makedirs(staging_path, exist_ok=True)
        staging_root = os.path.realpath(staging_path)

        saved, skipped = [], []
        for index, storage in enumerate(files):
            name = storage.filename or ""
            rel = _safe_relative_path(relative_paths[index] if index < len(relative_paths) and relative_paths[index] else name)
            if rel is None:
                skipped.append({"file": name, "reason": "bad path"})
                continue
            if os.path.splitext(rel)[1].lower() not in AUDIO_EXTENSIONS:
                skipped.append({"file": rel, "reason": "not an audio file"})
                continue
            target = os.path.realpath(os.path.join(staging_root, rel))
            if os.path.commonpath([staging_root, target]) != staging_root:
                skipped.append({"file": rel, "reason": "outside the import folder"})
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            # never clobber: a second upload of the same name gets a suffix
            final = target
            stem, ext = os.path.splitext(target)
            n = 1
            while os.path.exists(final):
                n += 1
                final = f"{stem} ({n}){ext}"
            storage.save(final)
            size = os.path.getsize(final)
            if size > UPLOAD_MAX_BYTES:
                os.remove(final)
                skipped.append({"file": rel, "reason": "over the 1 GB per-file limit"})
                continue
            saved.append({"file": os.path.relpath(final, staging_root), "size": size})

        if saved:
            invalidate_staging_scan_cache()
        return {"success": True, "saved": saved, "skipped": skipped, "staging_path": staging_path}, 200
    except Exception as exc:
        runtime.logger.error("Error uploading to staging: %s", exc)
        return {"success": False, "error": str(exc)}, 500


UPLOAD_PART_DIR = ".uploads"
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _upload_target(staging_root: str, rel: str) -> Optional[str]:
    target = os.path.realpath(os.path.join(staging_root, rel))
    if os.path.commonpath([staging_root, target]) != staging_root:
        return None
    return target


def _unique_path(target: str) -> str:
    final = target
    stem, ext = os.path.splitext(target)
    n = 1
    while os.path.exists(final):
        n += 1
        final = f"{stem} ({n}){ext}"
    return final


def upload_chunk_to_staging(runtime: ImportRouteRuntime, *, upload_id: str, index: int, total: int,
                            relative_path: str, chunk) -> tuple[Dict[str, Any], int]:
    """One piece of a browser upload. A reverse proxy in front of a docker
    install commonly caps a request body at a megabyte or so, which would
    refuse every whole-file upload; pieces of a few megabytes get through
    anything. Pieces append to a part file under staging/.uploads; the last
    one moves it into place. The part dir starts with a dot so the scanner
    never mistakes a half-uploaded file for an album."""
    try:
        if not _UPLOAD_ID_RE.match(str(upload_id or "")):
            return {"success": False, "error": "bad upload id"}, 400
        try:
            index = int(index)
            total = int(total)
        except (TypeError, ValueError):
            return {"success": False, "error": "bad chunk index"}, 400
        if total < 1 or index < 0 or index >= total:
            return {"success": False, "error": "bad chunk index"}, 400
        rel = _safe_relative_path(relative_path)
        if rel is None:
            return {"success": False, "error": "bad path"}, 400
        if os.path.splitext(rel)[1].lower() not in AUDIO_EXTENSIONS:
            return {"success": False, "error": "not an audio file"}, 400

        staging_path = runtime.get_staging_path()
        os.makedirs(staging_path, exist_ok=True)
        staging_root = os.path.realpath(staging_path)
        target = _upload_target(staging_root, rel)
        if target is None:
            return {"success": False, "error": "outside the import folder"}, 400

        part_dir = os.path.join(staging_root, UPLOAD_PART_DIR)
        os.makedirs(part_dir, exist_ok=True)
        part = os.path.join(part_dir, f"{upload_id}.part")
        # the first piece starts the file over: a retried upload must not
        # stack onto a stale part from the last try
        mode = "wb" if index == 0 else "ab"
        with open(part, mode) as handle:
            chunk.save(handle)
        size = os.path.getsize(part)
        if size > UPLOAD_MAX_BYTES:
            os.remove(part)
            return {"success": False, "error": "over the 1 GB per-file limit"}, 413

        if index < total - 1:
            return {"success": True, "received": index + 1, "total": total}, 200

        os.makedirs(os.path.dirname(target), exist_ok=True)
        final = _unique_path(target)
        os.replace(part, final)
        invalidate_staging_scan_cache()
        return {
            "success": True,
            "received": total,
            "total": total,
            "saved": {"file": os.path.relpath(final, staging_root), "size": os.path.getsize(final)},
        }, 200
    except Exception as exc:
        runtime.logger.error("Error receiving upload chunk: %s", exc)
        return {"success": False, "error": str(exc)}, 500


# fingerprinting is a second a file plus a network call; three files say
# who the artist is as well as thirty would
FINGERPRINT_MAX_FILES = 3


def fingerprint_files(runtime: ImportRouteRuntime, file_paths: list,
                      *, client_factory=None) -> tuple[Dict[str, Any], int]:
    """Identify staging files by AcoustID fingerprint, on demand. The worker
    already tries this last on its own; here it is a button for the matcher,
    for the folder whose tags and name say nothing. Returns what each file
    was recognised as and a search query the matcher can run from it."""
    try:
        paths = [p for p in (file_paths or []) if isinstance(p, str)][:FINGERPRINT_MAX_FILES]
        if not paths:
            return {"success": False, "error": "file_paths is required"}, 400

        if client_factory is None:
            from core.acoustid_client import AcoustIDClient
            client_factory = AcoustIDClient
        try:
            client = client_factory()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"AcoustID is not available: {exc}"}, 503
        available, reason = client.is_available()
        if not available:
            return {"success": False, "error": reason or "AcoustID is not set up", "code": "acoustid_unavailable"}, 503

        results = []
        for raw in paths:
            resolved, error = _validate_import_file(runtime, raw)
            if error:
                results.append({"file": os.path.basename(str(raw)), "status": "error", "error": error})
                continue
            res = client.lookup_with_status(resolved)
            best = (res.get("recordings") or [None])[0]
            results.append({
                "file": os.path.basename(resolved),
                "status": res.get("status"),
                "error": res.get("error"),
                "title": best.get("title") if best else None,
                "artist": best.get("artist") if best else None,
                "mbid": best.get("mbid") if best else None,
                "score": round(float(best.get("score") or 0), 3) if best else None,
            })

        artists = [r["artist"] for r in results if r.get("artist")]
        artist = max(set(artists), key=artists.count) if artists else None
        titles = [r["title"] for r in results if r.get("title")]
        recognised = sum(1 for r in results if r.get("status") == "ok")
        return {
            "success": True,
            "results": results,
            "recognised": recognised,
            "artist": artist,
            "title": titles[0] if len(paths) == 1 and titles else None,
        }, 200
    except Exception as exc:
        runtime.logger.error("Error fingerprinting import files: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def album_preview(runtime: ImportRouteRuntime, data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
    """What an album import WOULD do, per track: the destination path on the
    user's template, and the tags the release will write against the tags the
    file has now. Nothing is created (the path builder runs with
    create_dirs=False, the reorganize dry run's seam). The pipeline still owns
    the real answer; this is the same builders on the same context."""
    try:
        from core.imports.context import build_import_album_info, get_import_clean_title
        from core.imports.paths import _extract_year_from_release_date, build_final_path_for_track
        from core.imports.track_number import resolve_disc_for_track

        data = data or {}
        album = data.get("album") or {}
        matches = data.get("matches") or []
        source = str(album.get("source") or data.get("source") or "").strip().lower()
        if not album or not matches:
            return {"success": False, "error": "album and matches are required"}, 400

        total_discs = max(
            (int(m.get("track", {}).get("disc_number") or 1) for m in matches if m.get("track")),
            default=1,
        )
        artist_context = runtime.resolve_album_artist_context(album, source=source)
        rows = []
        for match in matches:
            staging_file = match.get("staging_file") or {}
            track = match.get("track") or {}
            if not staging_file or not track:
                continue
            full_path = staging_file.get("full_path", "")
            ext = os.path.splitext(full_path)[1] or ".flac"
            context = runtime.build_album_import_context(
                album, track, artist_context=artist_context, total_discs=total_discs, source=source,
            )
            context["is_local_import"] = True
            ctx_artist = context.get("artist") or artist_context or {}
            album_info = build_import_album_info(context, force_album=True)
            album_info["track_number"] = int(track.get("track_number") or 1)
            album_info["clean_track_name"] = get_import_clean_title(
                context, album_info=album_info, default=track.get("name") or "Unknown Track",
            )
            try:
                album_info["disc_number"] = resolve_disc_for_track(
                    context.get("original_search") or {}, album_info,
                )
            except Exception:  # noqa: BLE001 - disc is a nicety in a preview
                album_info["disc_number"] = int(track.get("disc_number") or 1)

            destination = None
            path_error = None
            try:
                destination, _ = build_final_path_for_track(
                    context, ctx_artist, album_info, ext, create_dirs=False,
                )
            except Exception as exc:  # noqa: BLE001 - say why, do not fail the preview
                path_error = str(exc)

            current = {}
            try:
                if full_path and os.path.isfile(full_path):
                    current = runtime.read_staging_file_metadata(full_path, os.path.basename(full_path))
            except Exception:  # noqa: BLE001
                current = {}

            artists = track.get("artists") or []
            artist_names = [a.get("name") if isinstance(a, dict) else str(a) for a in artists]
            artist_names = [a for a in artist_names if a]
            after = {
                "title": album_info.get("clean_track_name") or track.get("name") or "",
                "artist": ", ".join(artist_names) or ctx_artist.get("name") or album.get("artist") or "",
                "albumartist": ctx_artist.get("name") or album.get("artist") or "",
                "album": album_info.get("album_name") or album.get("name") or "",
                "track_number": album_info.get("track_number"),
                "disc_number": album_info.get("disc_number"),
                "year": _extract_year_from_release_date(album.get("release_date") or "") or "",
            }
            before = {
                "title": current.get("title") or "",
                "artist": current.get("artist") or "",
                "albumartist": current.get("albumartist") or "",
                "album": current.get("album") or "",
                "track_number": current.get("track_number") or None,
                "disc_number": current.get("disc_number") or None,
                "year": "",
            }
            changed = [k for k in after if str(after[k] or "") != str(before.get(k) or "") and after[k] not in (None, "")]
            rows.append({
                "file": os.path.basename(full_path),
                "full_path": full_path,
                "destination": destination,
                "path_error": path_error,
                "before": before,
                "after": after,
                "changed": changed,
            })

        return {"success": True, "tracks": rows}, 200
    except Exception as exc:
        runtime.logger.error("Error previewing album import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def album_process(runtime: ImportRouteRuntime, data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
    """Process matched album files through the post-processing pipeline."""
    try:
        data = data or {}
        album = data.get("album", {})
        matches = data.get("matches", [])

        if not album or not matches:
            return {"success": False, "error": "Missing album or matches data"}, 400
        if runtime.post_process_matched_download is None:
            return {"success": False, "error": "Import post-processing not available"}, 500

        ready, reason = runtime.is_active_media_server_ready()
        if not ready:
            return {"success": False, "error": reason, "error_code": "media_server_not_connected"}, 503

        processed = 0
        errors = []
        dispositions = []
        finished_paths = []   # staged files this import is done with
        summary = {"imported": 0, "skipped_owned": 0, "upgraded": 0}
        album_name = album.get("name", album.get("album_name", "Unknown Album"))
        artist_name = album.get("artist", album.get("artist_name", "Unknown Artist"))
        album_id = album.get("id", album.get("album_id", ""))
        source = str(album.get("source") or data.get("source") or "").strip().lower()

        from core.imports.library_check import (
            DECISION_SKIP_OWNED,
            DECISION_UPGRADE,
            check_track_library_status,
        )
        db = _get_import_db(runtime)
        gate_profile = _resolve_gate_profile(runtime)

        total_discs = max(
            (
                match.get("track", {}).get("disc_number", 1)
                for match in matches
                if match.get("track")
            ),
            default=1,
        )
        artist_context = runtime.resolve_album_artist_context(album, source=source)

        def _record_disposition(track_name, file_name, decision, reason):
            dispositions.append({
                "track_name": track_name,
                "file_name": file_name,
                "decision": decision,
                "reason": reason,
            })
            if decision in summary:
                summary[decision] += 1

        for match in matches:
            staging_file = match.get("staging_file")
            track = match.get("track") or {}
            if not staging_file or not track:
                continue

            file_path, path_error = _validate_import_file(
                runtime, staging_file.get("full_path", ""),
            )
            _err_track = (track or {}).get("name", "Unknown Track")
            _err_file = staging_file.get("filename") or "?"
            if path_error:
                errors.append(f"{path_error}: {_err_file}")
                _record_disposition(
                    _err_track, _err_file, "error", f"{path_error}: {_err_file}",
                )
                continue

            track_name = track.get("name", "Unknown Track")
            track_number = track.get("track_number", 1)
            file_name = staging_file.get("filename") or os.path.basename(file_path)

            # Existing-library pre-check (enforcement): re-checked here, never
            # trusting the client's annotated copy. Confident "already owned"
            # pairings skip the pipeline entirely — no transcoding, tagging or
            # file moves for tracks the library already has.
            status = check_track_library_status(
                db,
                track=track,
                staging_file_path=file_path,
                match_type=match.get("match_type"),
                confidence=match.get("confidence", 0) or 0,
                source_hint=source,
                album_name=album_name,
                quality_profile=gate_profile,
            )
            decision = status["decision"]
            if decision == DECISION_SKIP_OWNED:
                runtime.logger.info(
                    "[Library Check] Skipping %s — %s",
                    track_name, status["reason"],
                )
                _record_disposition(track_name, file_name, "skipped_owned", status["reason"])
                # NOT unlinking the staging file here: on the manual import
                # page these are the user's own files. they stay in staging
                # unless the user turned on "clear finished folders" (below).
                # (Automated client copies are working copies and ARE
                # discarded — see client_match.)
                finished_paths.append(file_path)
                continue
            # "upgrade" and everything else proceed; the pipeline's existing
            # upgrade/replacement logic decides what the better file replaces.
            gate_decision = decision

            context_key = f"import_album_{album_id}_{track_number}_{uuid.uuid4().hex[:8]}"
            context = runtime.build_album_import_context(
                album,
                track,
                artist_context=artist_context,
                total_discs=total_discs,
                source=source,
            )
            if isinstance(context, dict):
                context['is_local_import'] = True  # user's own file, not an slskd transfer (#804)
                # Manual import = the user explicitly matched this exact file to this
                # track, so the quality profile has no veto here (#1017). AcoustID,
                # integrity and silence guards still run.
                context['_skip_quarantine_check'] = ['quality', 'bit_depth']
                if runtime.profile_id:
                    context['profile_id'] = runtime.profile_id

            try:
                runtime.post_process_matched_download(context_key, context, file_path)
                # A quarantine/race-guard rejection returns normally (no
                # exception) and leaves the file in ss_quarantine, NOT the
                # library — so it must be reported as an error, not counted
                # as a successful import (#764).
                reject_reason = import_rejection_reason(context)
                if reject_reason:
                    errors.append(f"{track_name}: {reject_reason}")
                    runtime.logger.warning("Import rejected: %s — %s", track_name, reject_reason)
                    _record_disposition(track_name, file_name, "error", reject_reason)
                elif kept_existing_library_file(context):
                    # already at its library path and the pipeline kept that
                    # copy: nothing was imported, so don't call it imported.
                    _record_disposition(track_name, file_name, "skipped_owned",
                                        "already in library at the same spot, kept the library copy")
                    finished_paths.append(file_path)
                else:
                    processed += 1
                    # The gate's decision stands: an "upgrade" routing that
                    # processed cleanly counts as upgraded, the rest imported.
                    final_decision = "upgraded" if gate_decision == DECISION_UPGRADE else "imported"
                    _record_disposition(track_name, file_name, final_decision, status["reason"])
                    finished_paths.append(file_path)
                    runtime.logger.info("Import processed: %s. %s from %s", track_number, track_name, album_name)
            except Exception as proc_err:
                err_msg = f"{track_name}: {str(proc_err)}"
                errors.append(err_msg)
                runtime.logger.error("Import processing error: %s", err_msg)
                _record_disposition(track_name, file_name, "error", str(proc_err))

        if runtime.add_activity_item:
            runtime.add_activity_item("", "Album Imported", _import_summary_text(
                album_name, artist_name, summary, len(matches)), "Now")

        if processed > 0:
            _emit_import_completed(
                runtime,
                track_count=processed,
                album_name=album_name or "",
                artist=artist_name or "",
                playlist_name=f"Import: {album_name}" if album_name else "Import",
                total_tracks=len(matches),
                failed_tracks=len(errors),
                log_label="album",
            )
            runtime.refresh_import_suggestions_cache()

        # opt-in: every track went in or was already owned, so the folder it
        # came in is done with. a run with errors keeps everything.
        cleared = False
        if finished_paths and not errors and _clear_finished_enabled():
            from core.imports.file_ops import clear_finished_staging
            try:
                cleared = bool(clear_finished_staging(runtime.get_staging_path(), finished_paths))
            except Exception as exc:                        # noqa: BLE001
                runtime.logger.warning("[Import] could not clear finished staging files: %s", exc)

        # Files just left staging — drop the shared scan so the next files/groups/hints
        # reflects reality immediately instead of waiting out the cache TTL.
        if processed > 0 or cleared:
            invalidate_staging_scan_cache()

        return {"success": True, "processed": processed, "total": len(matches),
                "errors": errors, "dispositions": dispositions,
                "summary": summary}, 200
    except Exception as exc:
        runtime.logger.error("Error processing album import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def _clear_finished_enabled() -> bool:
    try:
        from core.settings import config_manager
        return bool(config_manager.get('import.clear_finished_from_staging', False))
    except Exception:                                       # noqa: BLE001
        return False


def _import_summary_text(album_name: str, artist_name: str,
                         summary: Dict[str, int], total: int) -> str:
    """One-line import result, e.g. "3 imported · 9 already owned · 1 upgraded"."""
    parts = []
    if summary.get("imported"):
        parts.append(f"{summary['imported']} imported")
    if summary.get("upgraded"):
        parts.append(f"{summary['upgraded']} upgraded")
    if summary.get("skipped_owned"):
        parts.append(f"{summary['skipped_owned']} already owned")
    detail = " · ".join(parts) if parts else "nothing imported"
    return f"{album_name} by {artist_name} ({detail})"


def search_tracks(runtime: ImportRouteRuntime, query: str, limit: int = 10) -> tuple[Dict[str, Any], int]:
    """Search tracks for manual single import using metadata source priority."""
    try:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Missing query parameter"}, 400

        limit = min(int(limit), 30)
        primary_source = runtime.get_primary_source()
        if primary_source == "hydrabase" and runtime.hydrabase_worker and runtime.dev_mode_enabled:
            runtime.hydrabase_worker.enqueue(query, "tracks")

        tracks = runtime.search_import_tracks(query, limit=limit)
        return {"success": True, "tracks": tracks,
                "primary_source": runtime.get_primary_source_label()}, 200
    except Exception as exc:
        runtime.logger.error("Error searching tracks for import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def process_single_import_file(runtime: ImportRouteRuntime, file_info: Dict[str, Any]) -> tuple[str, str]:
    """Validate, resolve metadata, and post-process one single import file."""
    file_path, path_error = _validate_import_file(runtime, file_info.get("full_path", ""))
    if path_error:
        return ("error", f"{path_error}: {file_info.get('filename', '?')}")
    if runtime.post_process_matched_download is None:
        return ("error", "Import post-processing not available")

    title = file_info.get("title") if isinstance(file_info.get("title"), str) else ""
    artist = file_info.get("artist") if isinstance(file_info.get("artist"), str) else ""
    manual_match = file_info.get("manual_match")
    if manual_match is not None and not isinstance(manual_match, dict):
        manual_match = None

    manual_match_source = ""
    manual_match_id = None
    if manual_match:
        manual_match_source = str(manual_match.get("source") or "").strip().lower()
        manual_match_id = str(manual_match.get("id") or "").strip()
        if not manual_match_id or not manual_match_source:
            return ("error", f"Malformed manual match for file: {file_info.get('filename', '?')}")

    # "No track picked: imported from its own tags" (and the inbox's bulk
    # "from tags" import): the user declined a catalogue match, so the file
    # must NOT be run through another automatic lookup — that lookup could
    # rematch it to an unrelated track (#1772). An explicit manual match is
    # more specific and wins over the flag if both somehow arrive. Only a
    # real JSON true sets the flag — the string "false" must not turn it on
    # for raw API callers. (An empty manual_match dict counts as no match
    # picked, same as it did before this flag existed.)
    from_own_tags = file_info.get("from_own_tags") is True and not manual_match

    if not title and not manual_match:
        parsed = runtime.parse_filename_metadata(file_info.get("filename", ""))
        title = parsed.get("title") or os.path.splitext(file_info.get("filename", "Unknown"))[0]
        if not artist:
            artist = parsed.get("artist", "")

    try:
        resolved = runtime.get_single_track_import_context(
            title,
            artist,
            override_id=manual_match_id,
            override_source=manual_match_source,
            from_own_tags=from_own_tags,
            album=file_info.get("album") or "",
            # raw on purpose: the resolver's tag coercion is the one place
            # that decides what an unusable number becomes
            track_number=file_info.get("track_number"),
            disc_number=file_info.get("disc_number"),
            duration_ms=file_info.get("duration_ms"),
        )
        context = runtime.normalize_import_context(resolved["context"])
        context['is_local_import'] = True  # user's own file, not an slskd transfer (#804)
        # Manual import = the user explicitly matched this exact file to this
        # track, so the quality profile has no veto here (#1017). AcoustID,
        # integrity and silence guards still run.
        context['_skip_quarantine_check'] = ['quality', 'bit_depth']
        if runtime.profile_id:
            context['profile_id'] = runtime.profile_id
        artist_data = runtime.get_import_context_artist(context)
        track_data = runtime.get_import_track_info(context)
        final_title = track_data.get("name", title)
        final_artist = artist_data.get("name", artist)

        context_key = f"import_single_{uuid.uuid4().hex[:8]}"
        runtime.post_process_matched_download(context_key, context, file_path)
        # Quarantine/race-guard returns normally but the file is in
        # ss_quarantine, not the library — report it as an error rather than
        # "ok", else the UI shows a green "Done" for a file that vanished (#764).
        reject_reason = import_rejection_reason(context)
        if reject_reason:
            runtime.logger.warning("Import single rejected: %s — %s", final_title, reject_reason)
            return ("error", f"{final_title}: {reject_reason}")
        runtime.logger.info(
            "Import single processed: %s by %s (source=%s)",
            final_title,
            final_artist,
            resolved.get("source") or "local",
        )
        # A re-identified file staged with "replace the original" ticked can be
        # imported EITHER by the auto-import worker or by hand from this page.
        # Only the worker honoured the hint, so importing manually left the old
        # file and its library row in place — the checkbox silently did nothing
        # (reported by Urethra Franklin). The identification half of the hint is
        # redundant here (the user picked the release themselves in this UI);
        # the replace half is the promise that was being broken.
        finalize_manual_rematch_replace(runtime, file_path, context)
        return ("ok", final_title)
    except Exception as proc_err:
        err_msg = f"{title}: {str(proc_err)}"
        runtime.logger.error("Import single processing error: %s", err_msg)
        return ("error", err_msg)


def finalize_manual_rematch_replace(runtime, staged_path: str, context: Dict[str, Any]) -> None:
    """Honour a re-identify hint's "replace the original" after a MANUAL import.

    Best-effort in every direction: the import has already succeeded, so a
    cleanup problem must be logged and swallowed rather than turned into a
    failed import. No hint (the overwhelmingly common case: an ordinary
    staging file) is a silent no-op.
    """
    try:
        from core.imports.rematch_hints import (
            consume_hint,
            delete_replaced_track,
            find_hint_for_file,
            quick_file_signature,
        )
    except Exception:                                   # pragma: no cover - defensive
        return

    try:
        signature = quick_file_signature(staged_path)
    except Exception:
        signature = None

    try:
        from database.music_database import get_database
        conn = get_database()._get_connection()
    except Exception as exc:                            # pragma: no cover - defensive
        runtime.logger.debug("manual re-identify cleanup skipped (no db): %s", exc)
        return

    try:
        cursor = conn.cursor()
        hint = find_hint_for_file(cursor, staged_path, signature)
        if hint is None:
            return
        if hint.replace_track_id:
            # Where the re-import actually landed. `_final_processed_path` is
            # the canonical key and takes precedence — side_effects.py and
            # auto_import_worker.py both read it first, and post-processing can
            # move a file after `_final_path` was recorded.
            new_paths = (context.get('_final_processed_path')
                         or context.get('_final_path')
                         or context.get('_reid_final_paths'))
            if isinstance(new_paths, str):
                new_paths = [new_paths]

            if not new_paths:
                # The same-home guard needs to know where the import landed. A
                # re-identify onto a release that resolves to the SAME path
                # would otherwise delete the file that IS the re-imported
                # track. Without that knowledge, refuse: leaving a duplicate is
                # recoverable, deleting the only copy is not. The hint is still
                # consumed so it cannot fire again later against a stale path.
                runtime.logger.warning(
                    "Manual import could not determine where track %s landed — "
                    "keeping the original rather than risking the only copy",
                    hint.replace_track_id)
            else:
                # resolve_fn maps the STORED path (a Docker/media-server view
                # this process may not be able to open literally) to the real
                # on-disk file. The auto-import worker passes the same thing;
                # without it the row is deleted and the FILE is orphaned.
                def _resolve_old(stored):
                    try:
                        from core.library.path_resolver import resolve_library_file_path
                        return resolve_library_file_path(stored)
                    except Exception:
                        return None

                delete_replaced_track(cursor, hint.replace_track_id,
                                      resolve_fn=_resolve_old, new_paths=new_paths)
                runtime.logger.info(
                    "Manual import honoured a re-identify replace for library track %s",
                    hint.replace_track_id)
        consume_hint(cursor, hint.id)
        conn.commit()
    except Exception as exc:
        runtime.logger.warning("Manual re-identify cleanup failed (import still succeeded): %s", exc)
    finally:
        try:
            conn.close()
        except Exception as exc:                        # noqa: BLE001 - close is best-effort
            runtime.logger.debug("re-identify cleanup: connection close failed: %s", exc)


def singles_process(runtime: ImportRouteRuntime, files: list[Dict[str, Any]]) -> tuple[Dict[str, Any], int]:
    """Process individual staging files as singles through the import pipeline."""
    try:
        files = files or []
        if not files:
            return {"success": False, "error": "No files provided"}, 400
        if runtime.import_singles_executor is None:
            return {"success": False, "error": "Import executor not available"}, 500

        ready, reason = runtime.is_active_media_server_ready()
        if not ready:
            return {"success": False, "error": reason, "error_code": "media_server_not_connected"}, 503

        processed = 0
        errors = []
        process_file = runtime.process_single_import_file or process_single_import_file
        future_to_filename = {
            runtime.import_singles_executor.submit(process_file, runtime, file_info):
                file_info.get("filename", "?")
            for file_info in files
        }

        for future in as_completed(future_to_filename):
            try:
                outcome, payload = future.result()
            except Exception as worker_err:
                errors.append(f"{future_to_filename[future]}: worker crashed: {worker_err}")
                continue
            if outcome == "ok":
                processed += 1
            else:
                errors.append(payload)

        if runtime.add_activity_item:
            runtime.add_activity_item("", "Singles Imported", f"{processed}/{len(files)} tracks processed", "Now")

        if processed > 0:
            _emit_import_completed(
                runtime,
                track_count=processed,
                album_name="",
                artist="Various",
                playlist_name="Import: Singles",
                total_tracks=len(files),
                failed_tracks=len(errors),
                log_label="singles",
            )
            runtime.refresh_import_suggestions_cache()

        # Files just left staging — drop the shared scan so the list updates immediately.
        if processed > 0:
            invalidate_staging_scan_cache()

        return {"success": True, "processed": processed, "total": len(files), "errors": errors}, 200
    except Exception as exc:
        runtime.logger.error("Error processing singles import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def _emit_import_completed(
    runtime: ImportRouteRuntime,
    *,
    track_count: int,
    album_name: str,
    artist: str,
    playlist_name: str,
    total_tracks: int,
    failed_tracks: int,
    log_label: str,
) -> None:
    # Keep import automation on the same chain as download batches:
    # batch_complete -> auto-scan -> library_scan_completed -> auto-update DB.
    try:
        if runtime.automation_engine:
            runtime.automation_engine.emit(
                "import_completed",
                {
                    "track_count": str(track_count),
                    "album_name": album_name,
                    "artist": artist,
                },
            )
            runtime.automation_engine.emit(
                "batch_complete",
                {
                    "playlist_name": playlist_name,
                    "artist": artist,
                    "total_tracks": str(total_tracks),
                    "completed_tracks": str(track_count),
                    "failed_tracks": str(failed_tracks),
                },
            )
    except Exception as exc:
        runtime.logger.debug("%s import automation emit failed: %s", log_label, exc)
