"""Helpers for normalizing and reading import contexts.

These functions keep the single-import pipeline source-agnostic while still
accepting legacy `spotify_*` payloads from older callers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(mapping: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in mapping:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return default


def _first_id_value(*values: Any) -> str:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def extract_artist_name(artist: Any) -> str:
    if isinstance(artist, dict):
        return str(artist.get("name", "") or "")
    if hasattr(artist, "name"):
        return str(getattr(artist, "name") or "")
    return str(artist) if artist else ""


def normalize_import_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize an import context to neutral fields in place and drop legacy aliases."""
    if not isinstance(context, dict):
        return {}

    artist = _as_dict(context.get("artist") or context.get("spotify_artist"))
    album = _as_dict(context.get("album") or context.get("spotify_album"))
    track_info = _as_dict(context.get("track_info"))
    original_search = _as_dict(context.get("original_search_result"))

    context["artist"] = artist
    context["album"] = album
    context["track_info"] = track_info
    context["original_search_result"] = original_search
    context.pop("spotify_artist", None)
    context.pop("spotify_album", None)

    for clean_key, legacy_key in (
        ("clean_title", "spotify_clean_title"),
        ("clean_album", "spotify_clean_album"),
        ("clean_artist", "spotify_clean_artist"),
    ):
        if clean_key not in original_search or original_search.get(clean_key) in (None, ""):
            legacy_value = original_search.get(legacy_key)
            if legacy_value not in (None, ""):
                original_search[clean_key] = legacy_value
        original_search.pop(legacy_key, None)

    has_clean = bool(context.get("has_clean_metadata", context.get("has_clean_spotify_data", False)))
    has_full = bool(context.get("has_full_metadata", context.get("has_full_spotify_metadata", False)))
    context["has_clean_metadata"] = has_clean
    context["has_full_metadata"] = has_full
    context.pop("has_clean_spotify_data", None)
    context.pop("has_full_spotify_metadata", None)

    return context


def get_import_context_artist(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return _as_dict(context.get("artist") or context.get("spotify_artist"))


def get_import_context_album(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return _as_dict(context.get("album") or context.get("spotify_album"))


def get_import_track_info(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return _as_dict(context.get("track_info"))


def get_import_original_search(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return _as_dict(context.get("original_search_result"))


def get_import_source(context: Optional[Dict[str, Any]]) -> str:
    if not isinstance(context, dict):
        return ""

    source = context.get("source")
    if source:
        return str(source)

    track_info = get_import_track_info(context)
    source = _first_value(track_info, "source", default="")
    if source:
        return str(source)

    original_search = get_import_original_search(context)
    source = _first_value(original_search, "source", default="")
    if source:
        return str(source)

    album = get_import_context_album(context)
    source = _first_value(album, "source", default="")
    if source:
        return str(source)

    artist = get_import_context_artist(context)
    source = _first_value(artist, "source", default="")
    return str(source) if source else ""


def get_import_clean_title(
    context: Optional[Dict[str, Any]],
    album_info: Optional[Dict[str, Any]] = None,
    default: str = "Unknown Track",
) -> str:
    original_search = get_import_original_search(context)
    title = _first_value(
        original_search,
        "clean_title",
        "title",
        default="",
    )
    if not title and album_info:
        title = _first_value(album_info, "clean_track_name", "track_name", default="")
    if not title:
        track_info = get_import_track_info(context)
        title = _first_value(track_info, "name", "title", default="")
    return str(title or default)


def get_import_clean_album(
    context: Optional[Dict[str, Any]],
    album_info: Optional[Dict[str, Any]] = None,
    default: str = "Unknown Album",
) -> str:
    original_search = get_import_original_search(context)
    album = _first_value(
        original_search,
        "clean_album",
        "album",
        default="",
    )
    if not album and album_info:
        album = _first_value(album_info, "album_name", "clean_album_name", default="")
    if not album:
        album_ctx = get_import_context_album(context)
        album = _first_value(album_ctx, "name", default="")
    return str(album or default)


def get_import_clean_artist(context: Optional[Dict[str, Any]], default: str = "Unknown Artist") -> str:
    original_search = get_import_original_search(context)
    artist = _first_value(
        original_search,
        "clean_artist",
        "artist",
        default="",
    )
    if not artist:
        artist_ctx = get_import_context_artist(context)
        artist = _first_value(artist_ctx, "name", default="")
    return str(artist or default)


def get_import_has_clean_metadata(context: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(context, dict):
        return False
    return bool(context.get("has_clean_metadata", False))


def get_import_has_full_metadata(context: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(context, dict):
        return False
    return bool(context.get("has_full_metadata", False))


def get_import_source_ids(context: Optional[Dict[str, Any]]) -> Dict[str, str]:
    track_info = get_import_track_info(context)
    original_search = get_import_original_search(context)
    artist = get_import_context_artist(context)
    album = get_import_context_album(context)

    return {
        "track_id": _first_id_value(
            _first_value(track_info, "id", "track_id", "trackId", "source_track_id", default=""),
            _first_value(track_info, "spotify_track_id", "itunes_track_id", "deezer_id", "deezer_track_id", "discogs_id", "soul_id", default=""),
            _first_value(original_search, "id", "track_id", "source_track_id", default=""),
            _first_value(original_search, "spotify_track_id", "itunes_track_id", "deezer_id", "deezer_track_id", "discogs_id", "soul_id", default=""),
        ),
        "artist_id": _first_id_value(
            _first_value(artist, "id", "artist_id", "source_artist_id", default=""),
            _first_value(artist, "spotify_artist_id", "itunes_artist_id", "deezer_id", "deezer_artist_id", "discogs_id", "soul_id", default=""),
            _first_value(original_search, "artist_id", "source_artist_id", default=""),
            _first_value(original_search, "spotify_artist_id", "itunes_artist_id", "deezer_id", "deezer_artist_id", "discogs_id", "soul_id", default=""),
        ),
        "album_id": _first_id_value(
            _first_value(album, "id", "album_id", "collectionId", "source_album_id", default=""),
            _first_value(album, "spotify_album_id", "itunes_album_id", "deezer_id", "deezer_album_id", "discogs_id", "soul_id", "album_soul_id", "hydrabase_album_id", default=""),
            _first_value(original_search, "album_id", "source_album_id", default=""),
            _first_value(original_search, "spotify_album_id", "itunes_album_id", "deezer_id", "deezer_album_id", "discogs_id", "soul_id", "album_soul_id", "hydrabase_album_id", default=""),
            _first_value(track_info, "album_id", "source_album_id", default=""),
            _first_value(track_info, "spotify_album_id", "itunes_album_id", "deezer_id", "deezer_album_id", "discogs_id", "soul_id", "album_soul_id", "hydrabase_album_id", default=""),
        ),
    }


def get_source_tag_names(source: str) -> Dict[str, Optional[str]]:
    source_name = (source or "").strip().lower()
    if source_name == "spotify":
        return {"track": "SPOTIFY_TRACK_ID", "artist": "SPOTIFY_ARTIST_ID", "album": "SPOTIFY_ALBUM_ID"}
    if source_name == "itunes":
        return {"track": "ITUNES_TRACK_ID", "artist": "ITUNES_ARTIST_ID", "album": "ITUNES_ALBUM_ID"}
    if source_name == "deezer":
        return {"track": "DEEZER_TRACK_ID", "artist": "DEEZER_ARTIST_ID", "album": None}
    if source_name == "hydrabase":
        return {"track": None, "artist": None, "album": None}
    if source_name == "discogs":
        return {"track": None, "artist": None, "album": None}
    return {"track": None, "artist": None, "album": None}


def get_library_source_id_columns(source: str) -> Dict[str, Optional[str]]:
    source_name = (source or "").strip().lower()
    if source_name == "spotify":
        return {"artist": "spotify_artist_id", "album": "spotify_album_id", "track": "spotify_track_id"}
    if source_name == "itunes":
        return {"artist": "itunes_artist_id", "album": "itunes_album_id", "track": "itunes_track_id"}
    if source_name == "deezer":
        return {"artist": "deezer_id", "album": "deezer_id", "track": "deezer_id"}
    if source_name == "hydrabase":
        return {"artist": "soul_id", "album": "soul_id", "track": "soul_id", "track_album": "album_soul_id"}
    if source_name == "discogs":
        return {"artist": "discogs_id", "album": "discogs_id", "track": None}
    return {}


def build_import_album_info(
    context: Optional[Dict[str, Any]],
    *,
    album_info: Optional[Dict[str, Any]] = None,
    force_album: bool = False,
) -> Dict[str, Any]:
    """Build the album-info payload used by post-processing."""
    album_ctx = get_import_context_album(context)
    track_info = get_import_track_info(context)
    original_search = get_import_original_search(context)
    artist_ctx = get_import_context_artist(context)

    track_number = (
        (album_info or {}).get("track_number")
        or track_info.get("track_number")
        or original_search.get("track_number")
        or 1
    )
    disc_number = (
        (album_info or {}).get("disc_number")
        or track_info.get("disc_number")
        or original_search.get("disc_number")
        or 1
    )

    clean_track_name = get_import_clean_title(context, album_info=album_info, default=original_search.get("title", "Unknown Track"))
    album_name = get_import_clean_album(context, album_info=album_info, default=original_search.get("album", "Unknown Album"))
    album_image_url = (
        (album_info or {}).get("album_image_url")
        or album_ctx.get("image_url")
        or ""
    )
    total_tracks = (
        album_ctx.get("total_tracks")
        or track_info.get("total_tracks")
        or (album_info or {}).get("total_tracks")
        or 0
    )
    album_type = (album_ctx.get("album_type") or track_info.get("album_type") or "album")
    source = get_import_source(context)

    artist_name = artist_ctx.get("name") or original_search.get("artist") or get_import_clean_artist(context)
    normalized_album = str(album_name or "").strip().lower()
    normalized_title = str(clean_track_name or "").strip().lower()
    normalized_artist = str(artist_name or "").strip().lower()
    is_album = bool(
        force_album
        or (
            normalized_album
            and total_tracks
            and int(total_tracks) > 1
            and normalized_album != normalized_title
            and normalized_album != normalized_artist
        )
    )

    return {
        "is_album": is_album,
        "album_name": album_name,
        "track_number": int(track_number) if str(track_number).isdigit() else track_number,
        "disc_number": int(disc_number) if str(disc_number).isdigit() else disc_number,
        "clean_track_name": clean_track_name,
        "album_image_url": album_image_url,
        "confidence": (album_info or {}).get("confidence", 1.0 if is_album or force_album else 0.0),
        "source": source,
        "album_type": album_type,
        "total_tracks": int(total_tracks) if str(total_tracks).isdigit() else total_tracks,
    }
