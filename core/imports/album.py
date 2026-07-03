"""Album import helpers for staging matching and post-processing context."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from core.imports.context import normalize_import_context
from core.imports.staging import collect_staging_files
from utils.logging_config import get_logger


logger = get_logger("imports.album")


def get_client_for_source(source: str):
    from core.metadata_service import get_client_for_source as _get_client_for_source

    return _get_client_for_source(source)


def get_artist_album_tracks(
    album_id: str,
    artist_name: str = "",
    album_name: str = "",
    source: Optional[str] = None,
):
    from core.metadata_service import get_artist_album_tracks as _get_artist_album_tracks

    return _get_artist_album_tracks(
        album_id,
        artist_name=artist_name,
        album_name=album_name,
        source_override=source,
    )


def _normalize_artist_entries(artists: Any) -> List[Dict[str, Any]]:
    if not artists:
        return []

    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized: List[Dict[str, Any]] = []
    for artist in artists:
        if isinstance(artist, dict):
            entry: Dict[str, Any] = {}
            name = artist.get("name") or artist.get("artist_name") or artist.get("title") or ""
            artist_id = artist.get("id") or artist.get("artist_id") or ""
            if name:
                entry["name"] = str(name)
            if artist_id:
                entry["id"] = str(artist_id)
            genres = artist.get("genres")
            if genres is not None:
                entry["genres"] = genres
            if entry:
                normalized.append(entry)
            continue

        name = str(artist).strip()
        if name:
            normalized.append({"name": name})

    return normalized


def _normalize_album_source(album: Dict[str, Any], source: str = "") -> str:
    album_source = source or album.get("source") or ""
    return str(album_source).strip().lower()


def _strip_legacy_source_fields(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    cleaned = dict(payload)
    cleaned.pop("_source", None)
    cleaned.pop("provider", None)
    return cleaned


def _coerce_track_int(value: Any, default: int = 1) -> int:
    if value in (None, ""):
        return default
    try:
        return int(str(value).split("/")[0].strip() or default)
    except (TypeError, ValueError):
        return default


def _normalize_match_track(track: Dict[str, Any], source: str, album: Dict[str, Any]) -> Dict[str, Any]:
    track_album = track.get("album") if isinstance(track.get("album"), dict) else album
    if isinstance(track_album, dict):
        track_album = _strip_legacy_source_fields(track_album)
    track_source = _normalize_album_source(track, source)
    track_artists = _normalize_artist_entries(track.get("artists") or [])

    if not track_artists and album.get("artists"):
        track_artists = _normalize_artist_entries(album.get("artists"))

    return {
        "id": track.get("id", ""),
        "name": track.get("name", "Unknown Track"),
        "track_number": _coerce_track_int(track.get("track_number", 1), default=1),
        "disc_number": _coerce_track_int(track.get("disc_number", 1), default=1),
        "duration_ms": _coerce_track_int(track.get("duration_ms", 0), default=0),
        "artists": track_artists,
        "uri": track.get("uri", ""),
        "album": track_album,
        "source": track_source,
    }


def _fetch_artist_data_for_source(client: Any, artist_id: str, source: str) -> Any:
    if source == "spotify":
        try:
            return client.get_artist(artist_id, allow_fallback=False)
        except TypeError:
            return client.get_artist(artist_id)
    return client.get_artist(artist_id)


def resolve_album_artist_context(album: Dict[str, Any], source: str = "") -> Dict[str, Any]:
    """Build a neutral artist context for album import processing."""
    album = dict(album or {})
    source = _normalize_album_source(album, source)

    artists = _normalize_artist_entries(album.get("artists") or [])
    if not artists:
        artist_name = album.get("artist") or album.get("artist_name") or ""
        artist_id = album.get("artist_id") or ""
        if artist_name or artist_id:
            artist_entry: Dict[str, Any] = {}
            if artist_name:
                artist_entry["name"] = str(artist_name)
            if artist_id:
                artist_entry["id"] = str(artist_id)
            artists = [artist_entry]

    primary_artist = artists[0] if artists else {}
    artist_name = str(
        primary_artist.get("name")
        or album.get("artist")
        or album.get("artist_name")
        or "Unknown Artist"
    ).strip()
    artist_id = str(primary_artist.get("id") or album.get("artist_id") or "").strip()

    genres: List[Any] = []
    if artist_id and source:
        client = get_client_for_source(source)
        if client and hasattr(client, "get_artist"):
            try:
                artist_data = _fetch_artist_data_for_source(client, artist_id, source)
                raw_genres = artist_data.get("genres") if isinstance(artist_data, dict) else getattr(artist_data, "genres", [])
                if isinstance(raw_genres, str):
                    genres = [raw_genres]
                elif raw_genres:
                    try:
                        genres = list(raw_genres)
                    except TypeError:
                        genres = [raw_genres]
            except Exception as exc:
                logger.debug("Could not resolve artist genres for %s on %s: %s", artist_id, source, exc)

    return {
        "id": artist_id,
        "name": artist_name,
        "genres": genres,
        "source": source,
    }


def build_album_import_context(
    album: Dict[str, Any],
    track: Dict[str, Any],
    *,
    artist_context: Optional[Dict[str, Any]] = None,
    total_discs: int = 1,
    source: str = "",
) -> Dict[str, Any]:
    """Build a neutral post-processing context for one album track."""
    album = dict(album or {})
    track = dict(track or {})
    source = _normalize_album_source(album, source)

    album_artists = _normalize_artist_entries(album.get("artists") or [])
    if not album_artists and artist_context:
        album_artists = _normalize_artist_entries([artist_context])

    if artist_context:
        artist_ctx = dict(artist_context)
    else:
        artist_ctx = resolve_album_artist_context(album, source)

    artist_ctx = _strip_legacy_source_fields(artist_ctx)
    artist_ctx.setdefault("genres", [])
    artist_ctx.setdefault("source", source)
    artist_ctx["genres"] = artist_ctx.get("genres") or []

    track_artists = _normalize_artist_entries(track.get("artists") or [])
    if not track_artists:
        track_artists = album_artists or [artist_ctx]

    track_album_value = track.get("album")
    if isinstance(track_album_value, dict):
        track_album_name = (
            track_album_value.get("name")
            or track_album_value.get("title")
            or album.get("name")
            or album.get("album_name")
            or ""
        )
        track_album_id = str(track_album_value.get("id") or track_album_value.get("album_id") or "").strip()
        track_album_type = track_album_value.get("album_type") or album.get("album_type") or "album"
        track_album_release = track_album_value.get("release_date") or album.get("release_date") or ""
        track_album_image = track_album_value.get("image_url") or album.get("image_url") or ""
    else:
        track_album_name = str(track_album_value or album.get("name") or album.get("album_name") or "").strip()
        track_album_id = str(album.get("id") or album.get("album_id") or "").strip()
        track_album_type = album.get("album_type") or "album"
        track_album_release = album.get("release_date") or ""
        track_album_image = album.get("image_url") or ""

    album_name = str(album.get("name") or album.get("album_name") or track_album_name or "Unknown Album").strip()
    artist_name = str(
        artist_ctx.get("name")
        or album.get("artist")
        or album.get("artist_name")
        or "Unknown Artist"
    ).strip()

    track_number = _coerce_track_int(track.get("track_number", 1), default=1)
    disc_number = _coerce_track_int(track.get("disc_number", 1), default=1)

    normalized_track = {
        "id": str(track.get("id") or track.get("track_id") or "").strip(),
        "name": str(track.get("name") or "Unknown Track").strip(),
        "track_number": track_number,
        "disc_number": disc_number,
        "duration_ms": _coerce_track_int(track.get("duration_ms", 0), default=0),
        "artists": track_artists,
        "uri": str(track.get("uri") or "").strip(),
        "album": track_album_name,
        "album_id": track_album_id,
        "album_type": track_album_type,
        "release_date": track_album_release,
        "source": source,
    }

    normalized_album = {
        "id": str(album.get("id") or album.get("album_id") or track_album_id or "").strip(),
        "name": album_name,
        "artist": artist_name,
        "artist_name": artist_name,
        "artist_id": str(artist_ctx.get("id") or album.get("artist_id") or "").strip(),
        "artists": album_artists,
        "release_date": str(album.get("release_date") or track_album_release or "").strip(),
        "total_tracks": int(album.get("total_tracks") or track.get("total_tracks") or 0) or 1,
        "total_discs": int(total_discs or 1) if str(total_discs or 1).isdigit() else total_discs or 1,
        "album_type": str(album.get("album_type") or track_album_type or "album").strip() or "album",
        "image_url": str(album.get("image_url") or track_album_image or "").strip(),
        "images": album.get("images") or ([] if not track_album_image else [{"url": track_album_image}]),
        "source": source,
    }
    for key in ("format", "country", "status", "label", "disambiguation", "release_group_id"):
        value = str(album.get(key) or "").strip()
        if value:
            normalized_album[key] = value

    original_search = {
        "title": normalized_track["name"],
        "artist": artist_name,
        "album": album_name,
        "track_number": track_number,
        "disc_number": disc_number,
        "clean_title": normalized_track["name"],
        "clean_album": album_name,
        "clean_artist": artist_name,
        "artists": track_artists,
        "duration_ms": normalized_track["duration_ms"],
        "id": normalized_track["id"],
        "source": source,
    }

    context = {
        "artist": artist_ctx,
        "album": normalized_album,
        "track_info": normalized_track,
        "original_search_result": original_search,
        "is_album_download": True,
        "has_clean_metadata": bool(normalized_track["id"]),
        "has_full_metadata": bool(normalized_track["id"]),
        "source": source,
    }

    normalized_context = normalize_import_context(context)
    normalized_context["artist"] = _strip_legacy_source_fields(normalized_context.get("artist"))
    normalized_context["album"] = _strip_legacy_source_fields(normalized_context.get("album"))
    normalized_context["track_info"] = _strip_legacy_source_fields(normalized_context.get("track_info"))
    normalized_context["original_search_result"] = _strip_legacy_source_fields(normalized_context.get("original_search_result"))
    return normalized_context


def build_album_import_match_payload(
    album_id: str,
    *,
    album_name: str = "",
    album_artist: str = "",
    file_paths: Optional[Iterable[str]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the album import match payload using provider-priority metadata lookup."""
    album_response = get_artist_album_tracks(
        album_id,
        artist_name=album_artist,
        album_name=album_name,
        source=source,
    )

    album = _strip_legacy_source_fields(dict(album_response.get("album") or {}))
    source = _normalize_album_source(album, album_response.get("source") or source or "")
    tracks = list(album_response.get("tracks") or [])
    if not album_response.get("success") or not tracks:
        return {
            "success": False,
            "error": album_response.get("error", "Album not found"),
            "status_code": album_response.get("status_code", 404),
            "album": {
                "id": album_id,
                "name": album_name or album_id,
                "artist": album_artist or "Unknown Artist",
                "artist_name": album_artist or "Unknown Artist",
                "artist_id": "",
                "artists": [],
                "release_date": "",
                "total_tracks": 0,
                "total_discs": 1,
                "album_type": "album",
                "image_url": "",
                "images": [],
                "source": source,
            },
            "matches": [],
            "unmatched_files": [],
            "source": source,
            "source_priority": album_response.get("source_priority", []),
            "resolved_album_id": album_response.get("resolved_album_id") or album_id,
        }

    staging_files = collect_staging_files(file_paths)
    album_name_for_match = album.get("name") or album_name or ""
    normalized_tracks = [
        _normalize_match_track(track, source, album) for track in tracks
    ]

    from core.imports.album_matching import default_quality_rank, match_files_to_tracks

    audio_files = [sf["full_path"] for sf in staging_files]
    staging_by_path = {sf["full_path"]: sf for sf in staging_files}
    file_tags = {
        sf["full_path"]: {
            "title": sf.get("title") or "",
            "artist": sf.get("artist") or "",
            "album": sf.get("album") or "",
            "track_number": sf.get("track_number") or 0,
            "disc_number": sf.get("disc_number") or 1,
        }
        for sf in staging_files
    }

    match_result = match_files_to_tracks(
        audio_files,
        file_tags,
        normalized_tracks,
        target_album=album_name_for_match,
        quality_rank=default_quality_rank,
    )
    # Re-map matches back to tracks by object identity, NOT track["id"]. match_files_to_tracks stores
    # the same track object on each match (both the exact-id and fuzzy phases), so identity is exact +
    # unique. Keying on track["id"] collided when a source omitted track ids — every id-less track
    # ("") mapped to one match, pairing several tracks to the same file.
    match_by_track = {id(m["track"]): m for m in match_result["matches"]}

    matches: List[Dict[str, Any]] = []
    for track in normalized_tracks:
        hit = match_by_track.get(id(track))
        matches.append(
            {
                "track": track,
                "staging_file": staging_by_path.get(hit["file"]) if hit else None,
                "confidence": round(hit["confidence"], 2) if hit else 0,
            }
        )

    unmatched_paths = set(match_result["unmatched_files"])
    unmatched_files = [
        sf for sf in staging_files if sf["full_path"] in unmatched_paths
    ]

    return {
        "success": True,
        "album": album,
        "matches": matches,
        "unmatched_files": unmatched_files,
        "source": source,
        "source_priority": album_response.get("source_priority", []),
        "resolved_album_id": album_response.get("resolved_album_id") or album_id,
    }
