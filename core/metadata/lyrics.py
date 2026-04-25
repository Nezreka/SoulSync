"""Lyrics export helpers for metadata enrichment."""

from __future__ import annotations

from core.imports.context import (
    get_import_clean_album,
    get_import_clean_title,
    get_import_context_album,
    get_import_original_search,
    normalize_import_context,
)
from core.metadata.common import get_config_manager
from utils.logging_config import get_logger as _create_logger

__all__ = [
    "generate_lrc_file",
]


logger = _create_logger("metadata.lyrics")


def generate_lrc_file(file_path: str, context: dict, artist: dict, album_info: dict) -> bool:
    cfg = get_config_manager()
    if cfg.get("metadata_enhancement.lrclib_enabled", True) is False:
        return False

    try:
        from core.lyrics_client import lyrics_client

        context = normalize_import_context(context)
        original_search = get_import_original_search(context)
        album_context = get_import_context_album(context)
        track_name = get_import_clean_title(context, default=original_search.get("title", "Unknown Track"))

        if isinstance(artist, dict):
            artist_name = artist.get("name", "Unknown Artist")
        elif hasattr(artist, "name"):
            artist_name = artist.name
        else:
            artist_name = str(artist) if artist else "Unknown Artist"

        album_name = None
        duration_seconds = None
        if album_info and album_info.get("is_album"):
            album_name = (
                get_import_clean_album(context, album_info=album_info, default="")
                or album_info.get("album_name")
                or album_context.get("name")
            )

        if original_search.get("duration_ms"):
            duration_seconds = int(original_search["duration_ms"] / 1000)

        success = lyrics_client.create_lrc_file(
            audio_file_path=file_path,
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            duration_seconds=duration_seconds,
        )

        if success:
            logger.info("LRC file generated for: %s", track_name)
        else:
            logger.warning("No lyrics found for: %s", track_name)
        return success
    except Exception as exc:
        logger.error("Error generating LRC file for %s: %s", file_path, exc)
        return False
