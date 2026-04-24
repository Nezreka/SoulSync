"""Shared staging folder and import suggestion helpers."""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.import_paths import docker_resolve_path
from utils.logging_config import get_logger

logger = get_logger("import_staging")

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".aiff", ".aif", ".ape"}

_import_suggestions_cache_lock = threading.Lock()
_import_suggestions_cache: Dict[str, Any] = {
    "suggestions": [],
    "building": False,
    "built": False,
}


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


def get_staging_path() -> str:
    """Resolve the configured staging folder path."""
    raw = _get_config_manager().get("import.staging_path", "./Staging")
    return docker_resolve_path(raw)


def get_import_suggestions_cache() -> Dict[str, Any]:
    """Expose the shared import suggestions cache."""
    return _import_suggestions_cache


def get_primary_source() -> str:
    from core.metadata_service import get_primary_source as _get_primary_source

    return _get_primary_source()


def get_source_priority(preferred_source: str):
    from core.metadata_service import get_source_priority as _get_source_priority

    return _get_source_priority(preferred_source)


def get_client_for_source(source: str):
    from core.metadata_service import get_client_for_source as _get_client_for_source

    return _get_client_for_source(source)


def _search_albums_for_source(source: str, client: Any, query: str, limit: int = 5):
    from core.metadata_service import _search_albums_for_source as _metadata_search_albums_for_source

    return _metadata_search_albums_for_source(source, client, query, limit=limit)


def _search_tracks_for_source(source: str, client: Any, query: str, limit: int = 5):
    from core.metadata_service import _search_tracks_for_source as _metadata_search_tracks_for_source

    return _metadata_search_tracks_for_source(source, client, query, limit=limit)


def _extract_value(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default

    if isinstance(value, (str, bytes)):
        return default

    for name in names:
        if isinstance(value, dict):
            if name in value and value[name] is not None:
                return value[name]
        else:
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate

    return default


def _extract_artist_names(artists: Any) -> List[str]:
    if not artists:
        return []

    if isinstance(artists, (str, bytes)):
        artist = str(artists).strip()
        return [artist] if artist else []

    try:
        items = list(artists)
    except TypeError:
        items = [artists]

    names: List[str] = []
    for artist in items:
        if isinstance(artist, dict):
            name = str(_extract_value(artist, "name", "artist_name", "title", default="") or "").strip()
        else:
            candidate = getattr(artist, "name", None)
            if candidate is None:
                candidate = artist
            name = str(candidate or "").strip()
        if name:
            names.append(name)

    return names


def _normalize_album_result(album: Any, source: str) -> Dict[str, Any]:
    album_id = str(_extract_value(album, "id", "album_id", "release_id", default="") or "").strip()
    album_name = str(_extract_value(album, "name", "title", default="") or "").strip()
    artists = _extract_artist_names(_extract_value(album, "artists", default=[]))
    artist_name = ", ".join(artists) if artists else str(
        _extract_value(album, "artist_name", "artist", default="Unknown Artist") or "Unknown Artist"
    ).strip()
    release_date = str(_extract_value(album, "release_date", "releaseDate", default="") or "").strip()
    album_type = str(_extract_value(album, "album_type", "type", default="album") or "album").strip() or "album"

    total_tracks = _extract_value(album, "total_tracks", "track_count", default=0)
    if isinstance(total_tracks, (list, tuple, set)):
        total_tracks = len(total_tracks)
    try:
        total_tracks = int(total_tracks or 0)
    except (TypeError, ValueError):
        total_tracks = 0

    image_url = _extract_value(album, "image_url", "thumb_url", "cover_image", "cover_url", default="")
    if not image_url:
        images = _extract_value(album, "images", default=[]) or []
        if isinstance(images, dict):
            images = [images]
        elif isinstance(images, (str, bytes)):
            images = [images]
        try:
            images = list(images)
        except TypeError:
            images = [images]
        if images:
            first_image = images[0]
            if isinstance(first_image, (str, bytes)):
                image_url = str(first_image).strip()
            else:
                image_url = _extract_value(first_image, "url", "image_url", "src", default="")

    return {
        "id": album_id or album_name or "unknown-album",
        "name": album_name or album_id or "Unknown Album",
        "artist": artist_name or "Unknown Artist",
        "release_date": release_date,
        "total_tracks": total_tracks,
        "image_url": str(image_url or ""),
        "album_type": album_type,
        "source": source,
    }


def _album_fingerprint(album: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(album.get("name", "") or "").strip().casefold(),
        str(album.get("artist", "") or "").strip().casefold(),
        str(album.get("release_date", "") or "").strip()[:10].casefold(),
        str(album.get("album_type", "") or "").strip().casefold(),
    )


def _normalize_track_result(track: Any, source: str) -> Dict[str, Any]:
    track_id = str(_extract_value(track, "id", "track_id", "trackId", default="") or "").strip()
    track_name = str(_extract_value(track, "name", "title", "track_name", default="") or "").strip()
    artists = _extract_artist_names(_extract_value(track, "artists", default=[]))
    artist_name = ", ".join(artists) if artists else str(
        _extract_value(track, "artist", "artist_name", default="Unknown Artist") or "Unknown Artist"
    ).strip()

    album_value = _extract_value(track, "album", default=None)
    album_name = ""
    album_id = str(_extract_value(track, "album_id", "collectionId", "albumId", default="") or "").strip()
    if isinstance(album_value, dict):
        album_name = str(_extract_value(album_value, "name", "title", default="") or "").strip()
        album_id = album_id or str(_extract_value(album_value, "id", "album_id", "collectionId", default="") or "").strip()
        if not album_name:
            album_name = album_id
    elif isinstance(album_value, (str, bytes)):
        album_name = str(album_value).strip()
    elif album_value is not None:
        album_name = str(_extract_value(album_value, "name", "title", default=album_value) or "").strip()
        if not album_id:
            album_id = str(_extract_value(album_value, "id", "album_id", "collectionId", default="") or "").strip()

    image_url = _extract_value(track, "image_url", "thumb_url", "cover_image", default="")
    if not image_url:
        images = _extract_value(track, "images", default=[]) or []
        if isinstance(images, dict):
            images = [images]
        elif isinstance(images, (str, bytes)):
            images = [images]
        try:
            images = list(images)
        except TypeError:
            images = [images]
        if images:
            first_image = images[0]
            if isinstance(first_image, (str, bytes)):
                image_url = str(first_image).strip()
            else:
                image_url = _extract_value(first_image, "url", "image_url", "src", default="")
    if not image_url and album_value is not None:
        album_images = _extract_value(album_value, "images", default=[]) or []
        if isinstance(album_images, dict):
            album_images = [album_images]
        elif isinstance(album_images, (str, bytes)):
            album_images = [album_images]
        try:
            album_images = list(album_images)
        except TypeError:
            album_images = [album_images]
        if album_images:
            first_album_image = album_images[0]
            if isinstance(first_album_image, (str, bytes)):
                image_url = str(first_album_image).strip()
            else:
                image_url = _extract_value(first_album_image, "url", "image_url", "src", default="")

    duration_ms = _extract_value(track, "duration_ms", "duration", "trackTimeMillis", default=0)
    try:
        duration_ms = int(duration_ms or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    track_number = _extract_value(track, "track_number", "trackNumber", default=1)
    try:
        track_number = int(track_number or 1)
    except (TypeError, ValueError):
        track_number = 1

    return {
        "id": track_id or track_name or "unknown-track",
        "name": track_name or track_id or "Unknown Track",
        "artist": artist_name or "Unknown Artist",
        "album": album_name or "",
        "album_id": album_id or "",
        "duration_ms": duration_ms,
        "image_url": str(image_url or ""),
        "track_number": track_number,
        "source": source,
    }


def _read_staging_audio_tags(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        from mutagen import File as MutagenFile

        tags = MutagenFile(file_path, easy=True)
        if not tags:
            return None, None

        album = (tags.get("album") or [None])[0]
        artist = (tags.get("artist") or (tags.get("albumartist") or [None]))[0]
        album_text = str(album).strip() if album else ""
        artist_text = str(artist).strip() if artist else ""
        return (album_text or None, artist_text or None)
    except Exception:
        return None, None


def _collect_import_suggestion_queries(staging_path: str) -> List[str]:
    tag_albums: Dict[Tuple[str, str], int] = {}
    folder_hints: Dict[str, int] = {}

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
            album, artist = _read_staging_audio_tags(full_path)
            if album:
                key = (album.strip(), (artist or "").strip())
                tag_albums[key] = tag_albums.get(key, 0) + 1

    queries: List[str] = []
    seen_lower = set()

    for (album, artist), _count in sorted(tag_albums.items(), key=lambda item: -item[1]):
        q = f"{album} {artist}".strip() if artist else album
        if q and q.lower() not in seen_lower:
            seen_lower.add(q.lower())
            queries.append(q)

    for folder, _count in sorted(folder_hints.items(), key=lambda item: -item[1]):
        q = folder.replace("_", " ")
        if q and q.lower() not in seen_lower:
            seen_lower.add(q.lower())
            queries.append(q)

    return queries[:5]


def search_import_albums(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Search albums using the configured metadata provider first."""
    query = (query or "").strip()
    if not query:
        return []

    results: List[Dict[str, Any]] = []
    seen = set()
    source_chain = get_source_priority(get_primary_source())

    for source in source_chain:
        client = get_client_for_source(source)
        if not client:
            continue

        source_results = _search_albums_for_source(source, client, query, limit=limit)
        if not source_results:
            continue

        added_for_source = False
        for album in source_results:
            suggestion = _normalize_album_result(album, source)
            fingerprint = _album_fingerprint(suggestion)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            results.append(suggestion)
            added_for_source = True
            if len(results) >= limit:
                return results[:limit]

        if added_for_source:
            break

    return results[:limit]


def search_import_tracks(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Search tracks using the configured metadata provider priority order."""
    query = (query or "").strip()
    if not query:
        return []

    results: List[Dict[str, Any]] = []
    source_chain = get_source_priority(get_primary_source())

    for source in source_chain:
        client = get_client_for_source(source)
        if not client:
            continue

        source_results = _search_tracks_for_source(source, client, query, limit=limit)
        if not source_results:
            continue

        for track in source_results:
            results.append(_normalize_track_result(track, source))
            if len(results) >= limit:
                return results[:limit]
        break

    return results[:limit]


def _build_import_suggestions_background():
    cache = _import_suggestions_cache

    with _import_suggestions_cache_lock:
        if cache["building"]:
            return
        cache["building"] = True

    try:
        staging_path = get_staging_path()
        if not os.path.isdir(staging_path):
            with _import_suggestions_cache_lock:
                cache["suggestions"] = []
                cache["built"] = True
            return

        queries = _collect_import_suggestion_queries(staging_path)
        if not queries:
            with _import_suggestions_cache_lock:
                cache["suggestions"] = []
                cache["built"] = True
            return

        suggestions: List[Dict[str, Any]] = []
        seen = set()
        for query in queries:
            try:
                albums = search_import_albums(query, limit=2)
                for album in albums:
                    fingerprint = _album_fingerprint(album)
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                    suggestions.append(album)
            except Exception as exc:
                logger.warning("Import suggestion search failed for %r: %s", query, exc)

        with _import_suggestions_cache_lock:
            cache["suggestions"] = suggestions[:8]
            cache["built"] = True

        logger.info(
            "Import suggestions cache built: %s suggestions from %s hints",
            len(cache["suggestions"]),
            len(queries),
        )
    except Exception as exc:
        logger.error("Error building import suggestions cache: %s", exc)
        with _import_suggestions_cache_lock:
            cache["suggestions"] = []
            cache["built"] = True
    finally:
        with _import_suggestions_cache_lock:
            cache["building"] = False


def start_import_suggestions_cache():
    """Start building the import suggestions cache in a background thread."""
    threading.Thread(
        target=_build_import_suggestions_background,
        daemon=True,
        name="import-suggestions-cache",
    ).start()


def refresh_import_suggestions_cache():
    """Invalidate and rebuild the suggestions cache."""
    with _import_suggestions_cache_lock:
        _import_suggestions_cache["built"] = False
    start_import_suggestions_cache()
