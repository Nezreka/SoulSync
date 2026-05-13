"""Write `artist.jpg` to the artist's folder on disk.

Navidrome has no API for setting an artist image — it reads
`artist.jpg` (or `artist.png` / `folder.jpg`) directly from the
artist's folder during library scans. Plex and Jellyfin have API
uploads (already implemented elsewhere), but their `read_from_disk`
behavior also picks up `artist.jpg` as a fallback, so writing the
file to disk is a portable mechanism that works for every server.

Pre-existing reference: issue #572 (rhwc) — Navidrome users only
saw album-art-derived artist thumbnails. SoulSync's
`update_artist_poster()` for Navidrome at `core/navidrome_client.py`
was a NO-OP (returned True without doing anything).

This module is the pure helpers backing the new endpoint. No
network, no DB, no Flask. Each function is testable in isolation
with `tmp_path` fixtures.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import requests

from utils.logging_config import get_logger


logger = get_logger("library.artist_image")


_ARTIST_IMAGE_FILENAME = "artist.jpg"

# Reasonable timeout for the image download. Artist images from
# Spotify/Deezer are typically 100-500KB so a generous timeout still
# completes in a few seconds on a slow connection.
_DEFAULT_IMAGE_DOWNLOAD_TIMEOUT = 30


def derive_artist_folder(album_folder: str) -> str:
    """Derive the artist's folder from an album's folder.

    Standard SoulSync path templates produce
    ``<library_root>/<artist>/<album>/...`` — so the artist folder is
    one level up from the album folder. Returns empty string for
    empty input; preserves the platform's path separator.

    Doesn't validate that the result exists on disk. Caller checks.
    """
    if not album_folder or not isinstance(album_folder, str):
        return ""
    # Trim trailing separator so dirname doesn't return the album
    # folder unchanged on inputs like "Music/Drake/Views/".
    trimmed = album_folder.rstrip("/").rstrip("\\")
    parent = os.path.dirname(trimmed)
    return parent or ""


def pick_artist_image_url(artist_obj) -> Optional[str]:
    """Return the URL to use for the artist image, if any.

    Reads the `image_url` attribute from a typed Artist dataclass
    (Spotify / Deezer / Discogs / etc — every typed Artist exposes
    this). Source converters already pick the largest variant the
    provider returns (Spotify upgrades to 640+, Deezer uses
    `picture_xl` at ~1000px) so we don't need to re-rank here.

    Returns None when the attribute is missing or empty.
    """
    if artist_obj is None:
        return None
    image_url = getattr(artist_obj, "image_url", "")
    if not image_url or not isinstance(image_url, str):
        return None
    image_url = image_url.strip()
    return image_url or None


def download_image_bytes(url: str, timeout: int = _DEFAULT_IMAGE_DOWNLOAD_TIMEOUT) -> Optional[bytes]:
    """Fetch image bytes from a URL.

    Returns None on any failure (HTTP error, timeout, non-image
    content-type, empty body). Caller surfaces a user-facing error.
    Doesn't raise.
    """
    if not url or not isinstance(url, str):
        return None
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
    except Exception as exc:
        logger.debug("artist image fetch failed for %s: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.debug("artist image fetch %s returned status %s", url, resp.status_code)
        return None
    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "image" not in content_type:
        logger.debug("artist image URL %s returned non-image content-type %s", url, content_type)
        return None
    try:
        body = resp.content
    except Exception as exc:
        logger.debug("artist image read failed for %s: %s", url, exc)
        return None
    if not body:
        return None
    return body


def write_artist_jpg(
    folder: str,
    image_bytes: bytes,
    *,
    overwrite: bool = False,
) -> Tuple[bool, str]:
    """Write `artist.jpg` to the given folder.

    Returns ``(True, written_path)`` on success or ``(False, reason)``
    on failure. Atomic write via `<filename>.tmp` + os.replace so a
    partial write never leaves a corrupt file on disk.

    When `overwrite=False` and the target file already exists,
    returns ``(False, 'file exists')`` without touching anything —
    respects user-supplied artist images.
    """
    if not folder or not isinstance(folder, str):
        return False, "no folder provided"
    if not image_bytes:
        return False, "no image bytes"
    if not os.path.isdir(folder):
        return False, f"folder does not exist: {folder}"

    target = os.path.join(folder, _ARTIST_IMAGE_FILENAME)
    if os.path.exists(target) and not overwrite:
        return False, "artist.jpg already exists; pass overwrite=True to replace"

    tmp = target + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(image_bytes)
        os.replace(tmp, target)
    except Exception as exc:
        # Best-effort cleanup of the partial temp file. Not worth
        # propagating any error here — primary write already failed.
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:  # noqa: S110 — cleanup, not critical
            pass
        return False, f"write failed: {exc}"

    return True, target


__all__ = [
    "derive_artist_folder",
    "pick_artist_image_url",
    "download_image_bytes",
    "write_artist_jpg",
]
