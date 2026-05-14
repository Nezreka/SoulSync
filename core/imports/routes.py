"""Import/staging controller helpers for Flask-style endpoints."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.imports.album import build_album_import_context, build_album_import_match_payload, resolve_album_artist_context
from core.imports.context import get_import_context_artist, get_import_track_info, normalize_import_context
from core.imports.filename import parse_filename_metadata
from core.imports.staging import (
    AUDIO_EXTENSIONS,
    get_import_suggestions_cache,
    get_primary_source as _get_primary_source,
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


def staging_files(runtime: ImportRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Scan the staging folder and return audio files with tag metadata."""
    try:
        staging_path = runtime.get_staging_path()
        os.makedirs(staging_path, exist_ok=True)

        files = []
        for root, _dirs, filenames in os.walk(staging_path):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, staging_path)

                meta = runtime.read_staging_file_metadata(full_path, rel_path)

                files.append(
                    {
                        "filename": fname,
                        "rel_path": rel_path,
                        "full_path": full_path,
                        "title": meta["title"],
                        "artist": meta["albumartist"] or meta["artist"] or "Unknown Artist",
                        "album": meta["album"],
                        "track_number": meta["track_number"],
                        "disc_number": meta["disc_number"],
                        "extension": ext,
                    }
                )

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

        album_groups = {}
        for root, _dirs, filenames in os.walk(staging_path):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, staging_path)

                meta = runtime.read_staging_file_metadata(full_path, rel_path)
                album = meta["album"]
                artist = meta["albumartist"] or meta["artist"]
                if not album or not artist:
                    continue

                key = (album.lower().strip(), artist.lower().strip())
                if key not in album_groups:
                    album_groups[key] = {"album": album.strip(), "artist": artist.strip(), "files": []}
                album_groups[key]["files"].append(
                    {
                        "filename": fname,
                        "full_path": full_path,
                        "title": meta["title"],
                        "track_number": meta["track_number"],
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

        tag_albums = {}
        folder_hints = {}
        for root, _dirs, filenames in os.walk(staging_path):
            audio_files = [f for f in filenames if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
            if not audio_files:
                continue

            rel_dir = os.path.relpath(root, staging_path)
            if rel_dir != ".":
                top_folder = rel_dir.split(os.sep)[0]
                folder_hints[top_folder] = folder_hints.get(top_folder, 0) + len(audio_files)

            for fname in audio_files:
                full_path = os.path.join(root, fname)
                try:
                    tags = runtime.read_tags(full_path)
                    if tags:
                        album = (tags.get("album") or [None])[0]
                        artist = (tags.get("artist") or (tags.get("albumartist") or [None]))[0]
                        if album:
                            key = (album.strip(), (artist or "").strip())
                            tag_albums[key] = tag_albums.get(key, 0) + 1
                except Exception as exc:
                    runtime.logger.debug("tag read failed: %s", exc)

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
    return {"success": True, "suggestions": cache["suggestions"], "ready": cache["built"]}, 200


def search_albums(runtime: ImportRouteRuntime, query: str, limit: int = 12) -> tuple[Dict[str, Any], int]:
    """Search albums for manual import using the active metadata provider."""
    try:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Missing query parameter"}, 400

        limit = min(int(limit), 50)
        if runtime.get_primary_source() == "hydrabase" and runtime.hydrabase_worker and runtime.dev_mode_enabled:
            runtime.hydrabase_worker.enqueue(query, "albums")

        albums = runtime.search_import_albums(query, limit=limit)
        return {"success": True, "albums": albums}, 200
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

            try:
                runtime.post_process_matched_download(context_key, context, file_path)
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
        if runtime.get_primary_source() == "hydrabase" and runtime.hydrabase_worker and runtime.dev_mode_enabled:
            runtime.hydrabase_worker.enqueue(query, "tracks")

        tracks = runtime.search_import_tracks(query, limit=limit)
        return {"success": True, "tracks": tracks}, 200
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
        artist_data = runtime.get_import_context_artist(context)
        track_data = runtime.get_import_track_info(context)
        final_title = track_data.get("name", title)
        final_artist = artist_data.get("name", artist)

        context_key = f"import_single_{uuid.uuid4().hex[:8]}"
        runtime.post_process_matched_download(context_key, context, file_path)
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
