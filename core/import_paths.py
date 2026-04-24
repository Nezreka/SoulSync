"""Shared path and naming helpers for import processing."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.import_context import (
    get_import_clean_title,
    get_import_context_album,
    get_import_original_search,
    get_import_source,
    get_import_track_info,
    normalize_import_context,
)

logger = logging.getLogger("import_paths")

_album_cache_lock = threading.Lock()
_album_editions: dict[str, str] = {}
_album_name_cache: dict[str, str] = {}


def _get_config_manager():
    try:
        from config.settings import config_manager
        return config_manager
    except Exception:
        class _FallbackConfig:
            @staticmethod
            def get(key, default=None):
                return default

        return _FallbackConfig()


def _get_itunes_client():
    try:
        from core.metadata_service import get_itunes_client
        return get_itunes_client()
    except Exception:
        return None


def _get_album_tracks_for_source(source: str, album_id: str):
    try:
        from core.metadata_service import get_album_tracks_for_source
        return get_album_tracks_for_source(source, album_id)
    except Exception:
        return None


def _extract_artist_name(artist_context: Any) -> str:
    if not artist_context:
        return ""
    if isinstance(artist_context, dict):
        return str(artist_context.get("name", "") or "").strip()
    return str(artist_context).strip()


def docker_resolve_path(path_str: str) -> str:
    """Resolve Docker-hosted Windows paths into container paths."""
    if os.path.exists("/.dockerenv") and len(path_str) >= 3 and path_str[1] == ":" and path_str[0].isalpha():
        drive_letter = path_str[0].lower()
        rest_of_path = path_str[2:].replace("\\", "/")
        return f"/host/mnt/{drive_letter}{rest_of_path}"
    return path_str


def build_simple_download_destination(context, file_path: str):
    """Build the destination path for a simple download into Transfer."""
    context = normalize_import_context(context)
    search_result = context.get("search_result", {}) or {}
    if not isinstance(search_result, dict):
        search_result = {}

    transfer_dir = Path(docker_resolve_path(_get_config_manager().get("soulseek.transfer_path", "./Transfer")))
    album_name = None
    original_filename = search_result.get("filename", "")
    if "/" in original_filename or "\\" in original_filename:
        path_parts = original_filename.replace("\\", "/").split("/")
        if len(path_parts) >= 2:
            album_name = path_parts[-2]
    if not album_name:
        album_value = search_result.get("album")
        if isinstance(album_value, dict):
            album_name = album_value.get("name", "")
        else:
            album_name = album_value

    filename = Path(file_path).name
    if album_name and str(album_name).lower() not in {"unknown", "unknown album", ""}:
        album_name = sanitize_filename(str(album_name))
        destination_dir = transfer_dir / album_name
    else:
        album_name = ""
        destination_dir = transfer_dir

    destination_dir.mkdir(parents=True, exist_ok=True)
    return destination_dir / filename, album_name, filename


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for file system compatibility."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized.rstrip(". ") or "_"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", sanitized, re.IGNORECASE):
        sanitized = "_" + sanitized
    return sanitized[:200]


def sanitize_context_values(context: dict) -> dict:
    """Sanitize all string values in a template context for path safety."""
    sanitized = {}
    for key, value in context.items():
        if isinstance(value, str) and value:
            sanitized[key] = sanitize_filename(value)
        else:
            sanitized[key] = value
    return sanitized


def clean_track_title(track_title: str, artist_name: str) -> str:
    """Clean up track title by removing artist prefix and other noise."""
    original = (track_title or "").strip()
    cleaned = original
    cleaned = re.sub(r"^\d{1,2}[\.\s\-]+", "", cleaned)
    artist_pattern = re.escape(artist_name or "") + r"\s*-\s*"
    cleaned = re.sub(f"^{artist_pattern}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[A-Za-z0-9\.]+\s*-\s*\d{1,2}\s*-\s*", "", cleaned)
    quality_patterns = [
        r"\s*[\[\(][0-9]+\s*kbps[\]\)]\s*",
        r"\s*[\[\(]flac[\]\)]\s*",
        r"\s*[\[\(]mp3[\]\)]\s*",
    ]
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[-\s\.]+", "", cleaned)
    cleaned = re.sub(r"[-\s\.]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else original


def get_base_album_name(album_name: str) -> str:
    """Extract the base album name without edition indicators."""
    base_name = album_name or ""
    base_name = re.sub(
        r"\s*[\[\(][^)\]]*\b(deluxe|special|expanded|extended|bonus|remaster(?:ed)?|anniversary|collectors?|limited|silver|gold|platinum)\b[^)\]]*[\]\)]\s*$",
        "",
        base_name,
        flags=re.IGNORECASE,
    )
    base_name = re.sub(r"\s*[\[\(][^)\]]*\bedition\b[^)\]]*[\]\)]\s*$", "", base_name, flags=re.IGNORECASE)
    base_name = re.sub(
        r"\s+(deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited|silver|gold|platinum)\s*(edition)?\s*$",
        "",
        base_name,
        flags=re.IGNORECASE,
    )
    return base_name.strip()


def detect_deluxe_edition(album_name: str) -> bool:
    """Detect if an album name indicates a deluxe/special edition."""
    if not album_name:
        return False

    album_lower = album_name.lower()
    deluxe_indicators = [
        "deluxe",
        "deluxe edition",
        "special edition",
        "expanded edition",
        "extended edition",
        "bonus",
        "remastered",
        "anniversary",
        "collectors edition",
        "limited edition",
        "silver edition",
        "gold edition",
        "platinum edition",
    ]
    for indicator in deluxe_indicators:
        if indicator in album_lower:
            logger.info("Detected deluxe edition: %r contains %r", album_name, indicator)
            return True
    return False


def normalize_base_album_name(base_album: str, artist_name: str) -> str:
    """Normalize the base album name to handle case variations and known corrections."""
    normalized_lower = (base_album or "").lower().strip()
    known_corrections = {
        # Add specific album name corrections here as needed.
    }

    for variant, correction in known_corrections.items():
        if normalized_lower == variant.lower():
            logger.info("Album correction applied: %r -> %r", base_album, correction)
            return correction

    normalized = base_album or ""
    normalized = re.sub(r"\s*&\s*", " & ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.strip()
    logger.info("Album variant normalization: %r -> %r", base_album, normalized)
    return normalized


def clean_album_title(album_title: str, artist_name: str) -> str:
    """Clean up album title by removing common prefixes, suffixes, and artist redundancy."""
    original = (album_title or "").strip()
    cleaned = original
    logger.info("Album Title Cleaning: %r (artist: %r)", original, artist_name)

    cleaned = re.sub(r"^Album\s*-\s*", "", cleaned, flags=re.IGNORECASE)
    artist_pattern = re.escape(artist_name or "") + r"\s*-\s*"
    cleaned = re.sub(f"^{artist_pattern}", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[\[\(]\d{4}[\]\)]\s*", " ", cleaned)

    quality_patterns = [
        r"\s*[\[\(].*?320.*?kbps.*?[\]\)]\s*",
        r"\s*[\[\(].*?256.*?kbps.*?[\]\)]\s*",
        r"\s*[\[\(].*?flac.*?[\]\)]\s*",
        r"\s*[\[\(].*?mp3.*?[\]\)]\s*",
        r"\s*[\[\(].*?itunes.*?[\]\)]\s*",
        r"\s*[\[\(].*?web.*?[\]\)]\s*",
        r"\s*[\[\(].*?cd.*?[\]\)]\s*",
    ]
    for pattern in quality_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s*[\[\(][^\]\)]*\b(deluxe|special|expanded|extended|bonus|remaster(?:ed)?|anniversary|collectors?|limited|silver|gold|platinum)\b[^\]\)]*[\]\)]\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[\[\(][^\]\)]*\bedition\b[^\]\)]*[\]\)]\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*(deluxe|special|expanded|extended|bonus|remastered|anniversary|collectors?|limited|silver|gold|platinum)\s*(edition)?\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[-\s\.]+", "", cleaned)
    cleaned = re.sub(r"[-\s\.]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else original


def resolve_album_group(artist_context: dict, album_info: dict, original_album: str = None) -> str:
    """Smart album grouping: upgrade to deluxe if any track is deluxe."""
    try:
        with _album_cache_lock:
            artist_name = _extract_artist_name(artist_context)
            detected_album = (album_info or {}).get("album_name", "")

            if detected_album:
                base_album = get_base_album_name(detected_album)
            elif original_album:
                cleaned_original = clean_album_title(original_album, artist_name)
                base_album = get_base_album_name(cleaned_original)
            else:
                base_album = get_base_album_name(detected_album)

            base_album = normalize_base_album_name(base_album, artist_name)
            album_key = f"{artist_name}::{base_album}"
            is_deluxe_track = False
            if detected_album:
                is_deluxe_track = detect_deluxe_edition(detected_album)
            elif original_album:
                is_deluxe_track = detect_deluxe_edition(original_album)

            if album_key in _album_name_cache:
                cached_name = _album_name_cache[album_key]
                current_edition = _album_editions.get(album_key, "standard")
                if is_deluxe_track and current_edition == "standard":
                    final_album_name = f"{base_album} (Deluxe Edition)"
                    _album_editions[album_key] = "deluxe"
                    _album_name_cache[album_key] = final_album_name
                    logger.info("Album cache upgrade: %r -> %r", album_key, final_album_name)
                    return final_album_name
                logger.info("Using cached album name for %r: %r", album_key, cached_name)
                return cached_name

            logger.info("Album grouping - Key: %r, Detected: %r", album_key, detected_album)

            current_edition = _album_editions.get(album_key, "standard")
            if is_deluxe_track and current_edition == "standard":
                logger.info("UPGRADE: Album %r upgraded from standard to deluxe!", base_album)
                _album_editions[album_key] = "deluxe"
                current_edition = "deluxe"

            if current_edition == "deluxe":
                final_album_name = f"{base_album} (Deluxe Edition)"
            else:
                final_album_name = base_album

            _album_name_cache[album_key] = final_album_name

            logger.info("Album resolution: %r -> %r (edition: %s)", detected_album, final_album_name, current_edition)
            return final_album_name
    except Exception as e:
        logger.error("Error resolving album group: %s", e)
        album_name = (album_info or {}).get("album_name", "Unknown Album")
        return album_name


def get_album_type_display(raw_type, track_count) -> str:
    """Return the display form of an album's type for the $albumtype template variable."""
    raw = (raw_type or "").strip().lower()
    try:
        tc = int(track_count or 0)
    except (TypeError, ValueError):
        tc = 0

    if raw in ("compilation", "compile"):
        return "Compilation"
    if raw == "album":
        return "Album"
    if raw in ("single", "ep"):
        if tc <= 3:
            return "Single"
        if tc <= 6:
            return "EP"
        return "Album"

    if tc <= 0:
        return "Album"
    if tc <= 3:
        return "Single"
    if tc <= 6:
        return "EP"
    return "Album"


def _replace_template_variables(template: str, context: dict) -> str:
    clean_context = sanitize_context_values(context)
    result = template

    album_artist_value = clean_context.get("albumartist", clean_context.get("artist", "Unknown Artist"))
    collab_mode = _get_config_manager().get("file_organization.collab_artist_mode", "first")
    if collab_mode == "first" and album_artist_value:
        artists_list = context.get("_artists_list")
        if artists_list and len(artists_list) > 1:
            first = artists_list[0]
            album_artist_value = first.get("name", first) if isinstance(first, dict) else str(first)
        elif artists_list and len(artists_list) == 1:
            itunes_artist_id = context.get("_itunes_artist_id")
            if itunes_artist_id and ("," in album_artist_value or " & " in album_artist_value):
                try:
                    resolved_client = _get_itunes_client()
                    if resolved_client and hasattr(resolved_client, "resolve_primary_artist"):
                        resolved = resolved_client.resolve_primary_artist(itunes_artist_id)
                        if resolved and resolved != album_artist_value:
                            album_artist_value = resolved
                except Exception:
                    pass

    bracket_map = {
        "albumartist": album_artist_value,
        "albumtype": clean_context.get("albumtype", "Album"),
        "playlist": clean_context.get("playlist_name", ""),
        "artistletter": (clean_context.get("artist", "U") or "U")[0].upper(),
        "artist": clean_context.get("artist", "Unknown Artist"),
        "album": clean_context.get("album", "Unknown Album"),
        "title": clean_context.get("title", "Unknown Track"),
        "track": f"{_coerce_int(clean_context.get('track_number', 1), 1):02d}",
        "disc": str(_coerce_int(clean_context.get("disc_number", 1), 1)),
        "discnum": str(_coerce_int(clean_context.get("disc_number", 1), 1)),
        "year": str(clean_context.get("year", "")),
        "quality": clean_context.get("quality", ""),
    }
    for var_name, val in bracket_map.items():
        result = result.replace("${" + var_name + "}", val)

    result = result.replace("$albumartist", album_artist_value)
    result = result.replace("$albumtype", clean_context.get("albumtype", "Album"))
    result = result.replace("$playlist", clean_context.get("playlist_name", ""))
    result = result.replace("$artistletter", (clean_context.get("artist", "U") or "U")[0].upper())
    result = result.replace("$artist", clean_context.get("artist", "Unknown Artist"))
    result = result.replace("$album", clean_context.get("album", "Unknown Album"))
    result = result.replace("$title", clean_context.get("title", "Unknown Track"))
    result = result.replace("$track", f"{clean_context.get('track_number', 1):02d}")
    result = result.replace("$year", str(clean_context.get("year", "")))

    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s*-\s*-\s*", " - ", result)
    result = result.strip()
    return result


def apply_path_template(template: str, context: dict) -> str:
    """Apply a template to build a path string."""
    return _replace_template_variables(template, context)


def get_file_path_from_template_raw(template: str, context: dict) -> tuple[str, str]:
    """Build file path using a user-provided template string directly."""
    full_path = apply_path_template(template, context)

    quality_value = context.get("quality", "")
    disc_number = _coerce_int(context.get("disc_number", 1), 1)
    disc_value = f"{disc_number:02d}"
    disc_value_raw = str(disc_number)

    path_parts = full_path.split("/")
    if len(path_parts) > 1:
        folder_parts = path_parts[:-1]
        filename_base = path_parts[-1]

        cleaned_folders = []
        for part in folder_parts:
            part = part.replace("$quality", "")
            part = part.replace("$discnum", "")
            part = part.replace("$disc", "")
            part = re.sub(r"\s*\[\s*\]", "", part)
            part = re.sub(r"\s*\(\s*\)", "", part)
            part = re.sub(r"\s*\{\s*\}", "", part)
            part = re.sub(r"\s*-\s*$", "", part)
            part = re.sub(r"^\s*-\s*", "", part)
            part = re.sub(r"\s+", " ", part).strip()
            if part:
                cleaned_folders.append(part)

        filename_base = filename_base.replace("$quality", quality_value)
        filename_base = filename_base.replace("$discnum", disc_value_raw)
        filename_base = filename_base.replace("$disc", disc_value)
        filename_base = re.sub(r"\s*\[\s*\]", "", filename_base)
        filename_base = re.sub(r"\s*\(\s*\)", "", filename_base)
        filename_base = re.sub(r"\s*\{\s*\}", "", filename_base)
        filename_base = re.sub(r"\s*-\s*$", "", filename_base)
        filename_base = re.sub(r"\s+", " ", filename_base).strip()

        sanitized_folders = [sanitize_filename(part) for part in cleaned_folders]
        folder_path = os.path.join(*sanitized_folders) if sanitized_folders else ""
        return folder_path, sanitize_filename(filename_base)

    full_path = full_path.replace("$quality", quality_value)
    full_path = full_path.replace("$discnum", disc_value_raw)
    full_path = full_path.replace("$disc", disc_value)
    full_path = re.sub(r"\s*\[\s*\]", "", full_path)
    full_path = re.sub(r"\s*\(\s*\)", "", full_path)
    full_path = re.sub(r"\s*\{\s*\}", "", full_path)
    full_path = re.sub(r"\s*-\s*$", "", full_path)
    full_path = re.sub(r"\s+", " ", full_path).strip()
    return "", sanitize_filename(full_path)


def get_file_path_from_template(context: dict, template_type: str = "album_path") -> tuple[str, str]:
    """Build complete file path using configured templates."""
    if not _get_config_manager().get("file_organization.enabled", True):
        return None, None

    templates = _get_config_manager().get("file_organization.templates", {})
    template = templates.get(template_type)
    if not template:
        default_templates = {
            "album_path": "$albumartist/$albumartist - $album/$track - $title",
            "single_path": "$artist/$artist - $title/$title",
            "compilation_path": "Compilations/$album/$track - $artist - $title",
            "playlist_path": "$playlist/$artist - $title",
        }
        template = default_templates.get(template_type, "$artist/$album/$track - $title")

    full_path = apply_path_template(template, context)

    path_parts = full_path.split("/")
    quality_value = context.get("quality", "")
    disc_number = _coerce_int(context.get("disc_number", 1), 1)
    disc_value = f"{disc_number:02d}"
    disc_value_raw = str(disc_number)

    if len(path_parts) > 1:
        folder_parts = path_parts[:-1]
        filename_base = path_parts[-1]

        cleaned_folders = []
        for part in folder_parts:
            part = part.replace("$quality", "")
            part = part.replace("$discnum", "")
            part = part.replace("$disc", "")
            part = re.sub(r"\s*\[\s*\]", "", part)
            part = re.sub(r"\s*\(\s*\)", "", part)
            part = re.sub(r"\s*\{\s*\}", "", part)
            part = re.sub(r"\s*-\s*$", "", part)
            part = re.sub(r"^\s*-\s*", "", part)
            part = re.sub(r"\s+", " ", part).strip()
            if part:
                cleaned_folders.append(part)

        filename_base = filename_base.replace("$quality", quality_value)
        filename_base = filename_base.replace("$discnum", disc_value_raw)
        filename_base = filename_base.replace("$disc", disc_value)
        filename_base = re.sub(r"\s*\[\s*\]", "", filename_base)
        filename_base = re.sub(r"\s*\(\s*\)", "", filename_base)
        filename_base = re.sub(r"\s*\{\s*\}", "", filename_base)
        filename_base = re.sub(r"\s*-\s*$", "", filename_base)
        filename_base = re.sub(r"\s+", " ", filename_base).strip()

        sanitized_folders = [sanitize_filename(part) for part in cleaned_folders]
        folder_path = os.path.join(*sanitized_folders) if sanitized_folders else ""
        filename = sanitize_filename(filename_base)
        return folder_path, filename

    full_path = full_path.replace("$quality", quality_value)
    full_path = full_path.replace("$discnum", disc_value_raw)
    full_path = full_path.replace("$disc", disc_value)
    full_path = re.sub(r"\s*\[\s*\]", "", full_path)
    full_path = re.sub(r"\s*\(\s*\)", "", full_path)
    full_path = re.sub(r"\s*\{\s*\}", "", full_path)
    full_path = re.sub(r"\s*-\s*$", "", full_path)
    full_path = re.sub(r"\s+", " ", full_path).strip()
    return "", sanitize_filename(full_path)


def _max_disc_number(album_tracks: Any) -> int:
    items = []
    if isinstance(album_tracks, dict):
        items = album_tracks.get("items") or album_tracks.get("tracks") or []
    elif isinstance(album_tracks, list):
        items = album_tracks

    max_disc = 1
    for track in items:
        if not isinstance(track, dict):
            continue
        try:
            disc_number = int(track.get("disc_number", 1) or 1)
        except (TypeError, ValueError):
            disc_number = 1
        if disc_number > max_disc:
            max_disc = disc_number
    return max_disc


def _coerce_int(value: Any, default: int = 1) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default


def build_final_path_for_track(context, artist_context, album_info, file_ext):
    """Shared path builder used by both post-processing and verification."""
    transfer_dir = docker_resolve_path(_get_config_manager().get("soulseek.transfer_path", "./Transfer"))
    context = normalize_import_context(context)
    track_info = get_import_track_info(context)
    original_search = get_import_original_search(context)
    album_context = get_import_context_album(context)
    source = get_import_source(context)
    playlist_folder_mode = track_info.get("_playlist_folder_mode", False)
    artist_name = _extract_artist_name(artist_context)

    source_info = track_info.get("source_info") or {}
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (json.JSONDecodeError, TypeError):
            source_info = {}
    if source_info.get("enhance") and source_info.get("original_file_path"):
        original_path = source_info["original_file_path"]
        original_dir = os.path.dirname(original_path)
        original_stem = os.path.splitext(os.path.basename(original_path))[0]
        final_path = os.path.join(original_dir, original_stem + file_ext)
        os.makedirs(original_dir, exist_ok=True)
        logger.info("[Enhance] Using original file location: %s", final_path)
        return final_path, True

    year = ""
    if album_context and album_context.get("release_date"):
        release_date = album_context["release_date"]
        if release_date and len(release_date) >= 4:
            year = release_date[:4]

    raw_album_type = ""
    if album_context:
        raw_album_type = album_context.get("album_type", "") or ""
    total_tracks = (album_context.get("total_tracks", 0) or 0) if album_context else 0
    album_type_display = get_album_type_display(raw_album_type, total_tracks)

    if playlist_folder_mode:
        playlist_name = track_info.get("_playlist_name", "Unknown Playlist")
        track_name = get_import_clean_title(context, default=original_search.get("title", "Unknown Track"))
        _artists = original_search.get("artists") or track_info.get("artists") or []

        template_context = {
            "artist": artist_name,
            "albumartist": artist_name,
            "album": track_name,
            "title": track_name,
            "playlist_name": playlist_name,
            "track_number": 1,
            "disc_number": 1,
            "year": year,
            "quality": context.get("_audio_quality", ""),
            "albumtype": album_type_display,
            "_artists_list": _artists,
            "_itunes_artist_id": str(artist_context.get("id", "")) if isinstance(artist_context, dict) and str(artist_context.get("id", "")).isdigit() and source == "itunes" else None,
        }

        folder_path, filename_base = get_file_path_from_template(template_context, "playlist_path")
        if folder_path and filename_base:
            final_path = os.path.join(transfer_dir, folder_path, filename_base + file_ext)
            os.makedirs(os.path.join(transfer_dir, folder_path), exist_ok=True)
            return final_path, True

        playlist_name_sanitized = sanitize_filename(playlist_name)
        playlist_dir = os.path.join(transfer_dir, playlist_name_sanitized)
        os.makedirs(playlist_dir, exist_ok=True)
        artist_name_sanitized = sanitize_filename(template_context["artist"])
        track_name_sanitized = sanitize_filename(track_name)
        new_filename = f"{artist_name_sanitized} - {track_name_sanitized}{file_ext}"
        return os.path.join(playlist_dir, new_filename), True

    if album_info and album_info.get("is_album"):
        clean_track_name = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", "Unknown Track"))
        track_number = _coerce_int(album_info.get("track_number", 1), 1)
        disc_number = _coerce_int(album_info.get("disc_number", 1), 1)
        _artists = original_search.get("artists") or track_info.get("artists") or []
        _album_ctx = album_context
        _itunes_aid = None
        _is_itunes = source == "itunes" or (isinstance(artist_context, dict) and str(artist_context.get("id", "")).isdigit() and source != "deezer")
        if _is_itunes and isinstance(artist_context, dict):
            _aid = artist_context.get("id", "")
            if str(_aid).isdigit():
                _itunes_aid = str(_aid)
        if not _itunes_aid and _album_ctx:
            _ext = _album_ctx.get("external_urls", {})
            if isinstance(_ext, dict) and _ext.get("itunes_artist_id"):
                _itunes_aid = _ext["itunes_artist_id"]

        _artist_name = artist_name
        _album_artist_name = _artist_name
        _album_artists_for_collab = None
        _explicit_artist_ctx = track_info.get("_explicit_artist_context") if isinstance(track_info, dict) else None
        if isinstance(_explicit_artist_ctx, dict) and _explicit_artist_ctx.get("name"):
            _album_artist_name = _explicit_artist_ctx["name"]
            _album_artists_for_collab = [_explicit_artist_ctx]
        elif isinstance(_explicit_artist_ctx, str) and _explicit_artist_ctx:
            _album_artist_name = _explicit_artist_ctx
            _album_artists_for_collab = [{"name": _explicit_artist_ctx}]
        else:
            _sa_artists = _album_ctx.get("artists", []) if _album_ctx else []
            if _sa_artists:
                _first_sa = _sa_artists[0]
                if isinstance(_first_sa, dict) and _first_sa.get("name"):
                    _album_artist_name = _first_sa["name"]
                elif isinstance(_first_sa, str) and _first_sa:
                    _album_artist_name = _first_sa
                _album_artists_for_collab = _sa_artists

        template_context = {
            "artist": _artist_name,
            "albumartist": _album_artist_name,
            "album": album_info["album_name"],
            "title": clean_track_name,
            "track_number": track_number,
            "disc_number": disc_number,
            "year": year,
            "quality": context.get("_audio_quality", ""),
            "albumtype": album_type_display,
            "_artists_list": _album_artists_for_collab if _album_artists_for_collab else _artists,
            "_itunes_artist_id": _itunes_aid,
        }
        total_discs = _coerce_int(album_context.get("total_discs", 1) if album_context else 1, 1)

        if total_discs <= 1 and album_context and album_context.get("id"):
            if disc_number > 1:
                total_discs = disc_number
            else:
                try:
                    _album_tracks = _get_album_tracks_for_source(source, str(album_context["id"]))
                    if _album_tracks:
                        total_discs = _max_disc_number(_album_tracks)
                        if total_discs > 1:
                            album_context["total_discs"] = total_discs
                            logger.info(
                                "[Multi-Disc] Resolved %s discs for single-track download of %r",
                                total_discs,
                                album_context.get("name"),
                            )
                except Exception as _disc_err:
                    logger.warning("[Multi-Disc] Could not resolve total_discs: %s", _disc_err)

        album_template = _get_config_manager().get("file_organization.templates.album_path", "")
        user_controls_disc = "$disc" in album_template
        disc_label = _get_config_manager().get("file_organization.disc_label", "Disc")

        folder_path, filename_base = get_file_path_from_template(template_context, "album_path")
        if folder_path and filename_base:
            if total_discs > 1 and not user_controls_disc:
                disc_folder = f"{disc_label} {disc_number}"
                final_path = os.path.join(transfer_dir, folder_path, disc_folder, filename_base + file_ext)
                os.makedirs(os.path.join(transfer_dir, folder_path, disc_folder), exist_ok=True)
            else:
                final_path = os.path.join(transfer_dir, folder_path, filename_base + file_ext)
                os.makedirs(os.path.join(transfer_dir, folder_path), exist_ok=True)
            return final_path, True

        artist_name_sanitized = sanitize_filename(template_context["albumartist"])
        album_name_sanitized = sanitize_filename(album_info["album_name"])
        artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
        album_folder_name = f"{artist_name_sanitized} - {album_name_sanitized}"
        album_dir = os.path.join(artist_dir, album_folder_name)
        if total_discs > 1:
            album_dir = os.path.join(album_dir, f"{disc_label} {disc_number}")
        os.makedirs(album_dir, exist_ok=True)
        final_track_name_sanitized = sanitize_filename(clean_track_name)
        new_filename = f"{track_number:02d} - {final_track_name_sanitized}{file_ext}"
        return os.path.join(album_dir, new_filename), True

    clean_track_name = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", "Unknown Track"))
    _artists = original_search.get("artists") or track_info.get("artists") or []
    _album_ctx = album_context
    _itunes_aid = None
    _is_itunes = source == "itunes" or (isinstance(artist_context, dict) and str(artist_context.get("id", "")).isdigit() and source != "deezer")
    if _is_itunes and isinstance(artist_context, dict):
        _aid = artist_context.get("id", "")
        if str(_aid).isdigit():
            _itunes_aid = str(_aid)
    if not _itunes_aid and _album_ctx:
        _ext = _album_ctx.get("external_urls", {})
        if isinstance(_ext, dict) and _ext.get("itunes_artist_id"):
            _itunes_aid = _ext["itunes_artist_id"]

    template_context = {
        "artist": artist_name,
        "albumartist": artist_name,
        "album": album_info.get("album_name", clean_track_name) if album_info else clean_track_name,
        "title": clean_track_name,
        "track_number": 1,
        "disc_number": 1,
        "year": year,
        "quality": context.get("_audio_quality", ""),
        "albumtype": album_type_display,
        "_artists_list": _artists,
        "_itunes_artist_id": _itunes_aid,
    }

    folder_path, filename_base = get_file_path_from_template(template_context, "single_path")
    if filename_base:
        if folder_path:
            final_path = os.path.join(transfer_dir, folder_path, filename_base + file_ext)
            os.makedirs(os.path.join(transfer_dir, folder_path), exist_ok=True)
        else:
            final_path = os.path.join(transfer_dir, filename_base + file_ext)
            os.makedirs(transfer_dir, exist_ok=True)
        return final_path, True

    artist_name_sanitized = sanitize_filename(template_context["artist"])
    final_track_name_sanitized = sanitize_filename(clean_track_name)
    artist_dir = os.path.join(transfer_dir, artist_name_sanitized)
    single_folder_name = f"{artist_name_sanitized} - {final_track_name_sanitized}"
    single_dir = os.path.join(artist_dir, single_folder_name)
    os.makedirs(single_dir, exist_ok=True)
    new_filename = f"{final_track_name_sanitized}{file_ext}"
    return os.path.join(single_dir, new_filename), True
