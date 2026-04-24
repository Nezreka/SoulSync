"""Album naming and grouping helpers used by import flows."""

from __future__ import annotations

import re
import threading
from typing import Any, Dict

from core.import_context import extract_artist_name
from utils.logging_config import get_logger


logger = get_logger("import_album_naming")

_album_cache_lock = threading.Lock()
_album_editions: dict[str, str] = {}
_album_name_cache: dict[str, str] = {}


def clear_album_grouping_cache() -> None:
    """Clear cached album grouping decisions.

    Useful for tests and for any future config reload flows.
    """
    with _album_cache_lock:
        _album_editions.clear()
        _album_name_cache.clear()


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
            artist_name = extract_artist_name(artist_context)
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
