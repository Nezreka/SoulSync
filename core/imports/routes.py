"""Import/staging controller helpers for Flask-style endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.imports.staging import (
    AUDIO_EXTENSIONS,
    get_import_suggestions_cache,
    get_staging_path as _get_staging_path,
    read_staging_file_metadata as _read_staging_file_metadata,
)
from utils.logging_config import get_logger


module_logger = get_logger("imports.routes")


def _default_read_tags(file_path: str):
    from mutagen import File as MutagenFile

    return MutagenFile(file_path, easy=True)


@dataclass
class ImportRouteRuntime:
    """Dependencies needed to service import/staging HTTP endpoints."""

    get_staging_path: Callable[[], str] = _get_staging_path
    read_staging_file_metadata: Callable[[str, str], Dict[str, Any]] = _read_staging_file_metadata
    read_tags: Callable[[str], Any] = _default_read_tags
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
