"""Import/staging controller helpers for Flask-style endpoints."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from core.imports.album import build_album_import_context, build_album_import_match_payload, resolve_album_artist_context
from core.imports.context import get_import_context_artist, get_import_track_info, normalize_import_context
from core.imports.filename import parse_filename_metadata
from core.imports.pipeline import import_rejection_reason
from core.imports.staging import (
    AUDIO_EXTENSIONS,
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


@dataclass
class ImportRouteRuntime:
    """Dependencies needed to service import/staging HTTP endpoints."""

    get_staging_path: Callable[[], str] = _get_staging_path
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
    add_activity_item: Callable[[Any, Any, Any, Any], Any] | None = None
    refresh_import_suggestions_cache: Callable[[], Any] = _refresh_import_suggestions_cache
    automation_engine: Any = None
    hydrabase_worker: Any = None
    dev_mode_enabled: bool = False
    import_singles_executor: Any = None
    logger: Any = module_logger


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
# Bumped by invalidate_staging_scan_cache() so a background scan that finishes after an
# import doesn't re-commit stale (pre-import) records (see the generation guard above).
_staging_scan_generation: Dict[str, int] = {"value": 0}

# Background-scan plumbing: a large staging folder (whole-library migration, #947) makes
# the synchronous scan exceed gunicorn's 120s request timeout. The runner moves the SAME
# scan off the request thread; the endpoints report progress instead of blocking.
_staging_scan_status: Dict[str, Any] = {
    "status": "idle", "scanned": 0, "total": 0, "path": None, "error": None,
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
                                     "path": staging_path, "error": None})

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
        return None, {"success": True, "scanning": True,
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

        # Pass 1 (fast): collect the audio-file list — no tag I/O — so we know the total.
        audio_files: list[tuple[str, str, Optional[str]]] = []
        if os.path.isdir(staging_path):
            for root, _dirs, filenames in os.walk(staging_path):
                rel_dir = os.path.relpath(root, staging_path)
                top_folder = rel_dir.split(os.sep)[0] if rel_dir != "." else None
                for fname in filenames:
                    if os.path.splitext(fname)[1].lower() in AUDIO_EXTENSIONS:
                        audio_files.append((root, fname, top_folder))
        if progress is not None:
            progress["total"] = len(audio_files)
            progress["scanned"] = 0

        # Pass 2 (slow): read each file's tags, updating progress as we go.
        records: list[Dict[str, Any]] = []
        for root, fname, top_folder in audio_files:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, staging_path)
            meta = runtime.read_staging_file_metadata(full_path, rel_path)
            records.append({
                "filename": fname, "rel_path": rel_path, "full_path": full_path,
                "extension": os.path.splitext(fname)[1].lower(),
                "title": meta["title"], "album": meta["album"],
                "artist": meta["artist"], "albumartist": meta["albumartist"],
                "track_number": meta["track_number"], "disc_number": meta["disc_number"],
                "top_folder": top_folder,
            })
            if progress is not None:
                progress["scanned"] += 1

        # Generation guard: if an import invalidated the cache mid-scan, these records are
        # stale — return them to this caller but do NOT commit them as the shared cache.
        if _staging_scan_generation["value"] == start_generation:
            _staging_scan_cache.update({"path": staging_path, "ts": time.time(), "records": records})
        return records


def invalidate_staging_scan_cache() -> None:
    """Drop the cached staging scan (call after an import moves/removes files so the
    next files/groups/hints request reflects the new state immediately). Also bumps the
    scan generation so an in-flight background scan won't re-commit pre-import records."""
    _staging_scan_generation["value"] += 1
    _staging_scan_cache.update({"path": None, "ts": 0.0, "records": None})


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
        return {"success": True, "files": files, "staging_path": staging_path}, 200
    except Exception as exc:
        runtime.logger.error("Error scanning staging files: %s", exc)
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


def search_albums(runtime: ImportRouteRuntime, query: str, limit: int = 12) -> tuple[Dict[str, Any], int]:
    """Search albums for manual import using the active metadata provider."""
    try:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Missing query parameter"}, 400

        limit = min(int(limit), 50)
        primary_source = runtime.get_primary_source()
        if primary_source == "hydrabase" and runtime.hydrabase_worker and runtime.dev_mode_enabled:
            runtime.hydrabase_worker.enqueue(query, "albums")

        albums = runtime.search_import_albums(query, limit=limit)
        # The label names the user's CONFIGURED source (Spotify Free reads as
        # 'spotify', not the deezer fallback the functional source downgrades to).
        return {"success": True, "albums": albums,
                "primary_source": runtime.get_primary_source_label()}, 200
    except Exception as exc:
        runtime.logger.error("Error searching albums for import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


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
        return payload, 200
    except Exception as exc:
        runtime.logger.error("Error matching album for import: %s", exc)
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

        processed = 0
        errors = []
        album_name = album.get("name", album.get("album_name", "Unknown Album"))
        artist_name = album.get("artist", album.get("artist_name", "Unknown Artist"))
        album_id = album.get("id", album.get("album_id", ""))
        source = str(album.get("source") or data.get("source") or "").strip().lower()

        total_discs = max(
            (
                match.get("track", {}).get("disc_number", 1)
                for match in matches
                if match.get("track")
            ),
            default=1,
        )
        artist_context = runtime.resolve_album_artist_context(album, source=source)

        for match in matches:
            staging_file = match.get("staging_file")
            track = match.get("track") or {}
            if not staging_file or not track:
                continue

            file_path = staging_file.get("full_path", "")
            if not os.path.isfile(file_path):
                errors.append(f"File not found: {staging_file.get('filename', '?')}")
                continue

            track_name = track.get("name", "Unknown Track")
            track_number = track.get("track_number", 1)
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
                else:
                    processed += 1
                    runtime.logger.info("Import processed: %s. %s from %s", track_number, track_name, album_name)
            except Exception as proc_err:
                err_msg = f"{track_name}: {str(proc_err)}"
                errors.append(err_msg)
                runtime.logger.error("Import processing error: %s", err_msg)

        if runtime.add_activity_item:
            runtime.add_activity_item("", "Album Imported", f"{album_name} by {artist_name} ({processed}/{len(matches)} tracks)", "Now")

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

        # Files just left staging — drop the shared scan so the next files/groups/hints
        # reflects reality immediately instead of waiting out the cache TTL.
        if processed > 0:
            invalidate_staging_scan_cache()

        return {"success": True, "processed": processed, "total": len(matches), "errors": errors}, 200
    except Exception as exc:
        runtime.logger.error("Error processing album import: %s", exc)
        return {"success": False, "error": str(exc)}, 500


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
    file_path = file_info.get("full_path", "")
    if not os.path.isfile(file_path):
        return ("error", f"File not found: {file_info.get('filename', '?')}")
    if runtime.post_process_matched_download is None:
        return ("error", "Import post-processing not available")

    title = file_info.get("title", "")
    artist = file_info.get("artist", "")
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
        )
        context = runtime.normalize_import_context(resolved["context"])
        context['is_local_import'] = True  # user's own file, not an slskd transfer (#804)
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
        return ("ok", final_title)
    except Exception as proc_err:
        err_msg = f"{title}: {str(proc_err)}"
        runtime.logger.error("Import single processing error: %s", err_msg)
        return ("error", err_msg)


def singles_process(runtime: ImportRouteRuntime, files: list[Dict[str, Any]]) -> tuple[Dict[str, Any], int]:
    """Process individual staging files as singles through the import pipeline."""
    try:
        files = files or []
        if not files:
            return {"success": False, "error": "No files provided"}, 400
        if runtime.import_singles_executor is None:
            return {"success": False, "error": "Import executor not available"}, 500

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
                    "total_tracks": str(total_tracks),
                    "completed_tracks": str(track_count),
                    "failed_tracks": str(failed_tracks),
                },
            )
    except Exception as exc:
        runtime.logger.debug("%s import automation emit failed: %s", log_label, exc)
