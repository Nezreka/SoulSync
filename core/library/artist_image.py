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
import re
from typing import Iterable, Optional, Tuple

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


def _folder_key(name: str) -> str:
    """an artist or folder name reduced to letters and digits, accents folded.

    path templates sanitize names (``AC/DC`` lands as ``AC_DC``), so only
    the letters and digits are compared.
    """
    from core.text.normalize import normalize_for_comparison

    return "".join(ch for ch in normalize_for_comparison(name or "") if ch.isalnum())


def find_artist_folder(track_paths: Iterable[str], artist_name: str) -> str:
    """the artist's own folder, found from their track files, or "" when there isn't one.

    "two folders up from a track" is only the artist folder for
    Artist/Album/track. a track loose in the library root put it one level
    above the root, an Artist/track one at the library root, and artist.jpg
    landed there for every artist in the library to pick up. so a folder
    only counts when it is named after the artist. the parent is checked
    before the track's own folder, so a self-titled album folder isn't
    mistaken for the artist's.
    """
    want = _folder_key(artist_name)
    if not want:
        return ""
    for path in track_paths:
        if not path or not isinstance(path, str):
            continue
        here = os.path.dirname(path)
        for folder in (derive_artist_folder(here), here):
            if folder and _folder_key(os.path.basename(folder.rstrip("/\\"))) == want:
                return folder
    return ""


_ARTIST_FOLDER_TEMPLATES = (
    ("album_path", "$albumartist/$albumartist - $album/$track - $title"),
    ("single_path", "$albumartist/$albumartist - $title/$title"),
)


def template_artist_levels(templates) -> list:
    """the folder level (0 = right under the library root) the album and
    single templates name after the artist.

    ``$albumartist/Album/$albumartist - $album/...`` is level 0 even though the
    album folder sits two below the artist (#2138). a template with no folder
    of the artist's own (compilations, "$albumartist - $album/...") has no
    level."""
    from core.imports.paths import _ARTIST_TOKEN_RE

    templates = templates if isinstance(templates, dict) else {}
    levels = []
    for key, default in _ARTIST_FOLDER_TEMPLATES:
        template = templates.get(key)
        if not isinstance(template, str) or not template.strip():
            template = default
        folders = template.replace("\\", "/").split("/")[:-1]
        for level, segment in enumerate(folders):
            if _ARTIST_TOKEN_RE.search(segment):
                # "$albumartist - $album" is an album folder named after the
                # artist, not the artist's folder
                if "$" not in _ARTIST_TOKEN_RE.sub("", segment) and level not in levels:
                    levels.append(level)
                break
    return levels


def _words(name: str) -> list:
    from core.text.normalize import normalize_for_comparison

    return re.findall(r"[^\W_]+", normalize_for_comparison(name or ""))


def folder_is_named_for(folder_name: str, artist_name: str) -> bool:
    """the folder name starts with the artist's name, word for word.

    "3JS (Nederpop trio)" is 3JS's folder when the template adds a
    disambiguation (#2138). only trusted at the level the template puts the
    artist, where an album folder ("3JS - Nu") can't be mistaken for it."""
    want = _words(artist_name)
    have = _words(folder_name)
    return bool(want) and have[:len(want)] == want


def _containing_root(path: str, roots) -> str:
    best = ""
    for root in roots or ():
        if not root:
            continue
        try:
            root = os.path.realpath(root)
            if os.path.commonpath((root, path)) == root and len(root) > len(best):
                best = root
        except (OSError, ValueError):
            continue
    return best


def locate_artist_folder(track_path: str, artist_name: str, roots, templates) -> str:
    """the artist's folder for a track in the library, or "" when it can't be told.

    "one up from the album folder" (derive_artist_folder) is only right for
    Artist/Album/track. a template like Artist/Album/Artist - Album/track put
    artist.nfo in the "Album" folder (#2138). so the folder comes from the
    library root down, at the level the naming template puts the artist, when
    it carries the artist's name. failing that, the topmost folder below the
    root named exactly after the artist. never a library root.
    """
    if not track_path or not isinstance(track_path, str):
        return ""
    track_dir = os.path.dirname(os.path.realpath(track_path))
    root = _containing_root(track_dir, roots)
    if root:
        rel = os.path.relpath(track_dir, root)
        parts = [] if rel in (".", "") else rel.split(os.sep)
        for level in template_artist_levels(templates):
            if level < len(parts) and folder_is_named_for(parts[level], artist_name):
                return os.path.join(root, *parts[:level + 1])
        ancestors = [os.path.join(root, *parts[:i + 1]) for i in range(len(parts))]
        want = _folder_key(artist_name)
        for folder in ancestors:
            if want and _folder_key(os.path.basename(folder)) == want:
                return folder
        # no folder carries the name: a compilation ("Compilations/Album")
        # or a layout we can't read. no guess, a wrong artist.nfo is worse
        # than none.
        return ""
    # outside every known root: only the nearest folders, and only by name
    return find_artist_folder([track_path], artist_name)


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


POSTER_FILENAME = "poster.jpg"
BACKGROUND_FILENAME = "background.jpg"
# what plex / jellyfin / kodi already read as the artist's photo or
# background. any of them in the folder means the user (or an earlier run)
# picked one, so nothing new is written beside it.
_PHOTO_STEMS = ("poster", "artist", "folder", "thumb")
_BACKGROUND_STEMS = ("background", "backdrop", "fanart", "art")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".tbn")


def _has_image(names, stems) -> bool:
    for name in names:
        stem, ext = os.path.splitext(name.casefold())
        if stem in stems and ext in _IMAGE_EXTS:
            return True
    return False


def _write_new_file(folder: str, filename: str, data: bytes) -> Tuple[bool, str]:
    """write ``filename`` only when nothing by that name is there."""
    target = os.path.join(folder, filename)
    if os.path.lexists(target):
        return False, f"{filename} already exists"
    tmp = target + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        if os.path.lexists(target):  # appeared while we wrote: theirs wins
            os.remove(tmp)
            return False, f"{filename} already exists"
        os.replace(tmp, target)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError as cleanup:
            logger.debug("artist artwork temp cleanup failed for %s: %s", tmp, cleanup)
        return False, f"write failed: {exc}"
    return True, target


def write_artist_artwork(folder: str, artist_name: str, mbid: str = "", *,
                         artwork=None) -> dict:
    """poster.jpg (the artist's photo) and background.jpg (the wide art) in
    the artist folder, for plex and anything else that reads local artwork
    (#2138).

    pictures come from the same HD sources the metadata updater pushes to
    plex/jellyfin (fanart.tv, TheAudioDB, Deezer), tied to the artist by
    MusicBrainz id. a folder that already has a photo or background (poster,
    artist, folder, fanart, backdrop...) keeps it: nothing is overwritten.
    returns {"poster": (ok, detail), "background": (ok, detail)} for what
    was tried. ``artwork`` swaps in the artist_artwork module for tests.
    """
    if not folder or not os.path.isdir(folder) or not str(artist_name or "").strip():
        return {}
    try:
        names = os.listdir(folder)
    except OSError:
        return {}
    need_photo = not _has_image(names, _PHOTO_STEMS)
    need_background = not _has_image(names, _BACKGROUND_STEMS)
    if not need_photo and not need_background:
        return {}
    if artwork is None:
        from core.metadata import artist_artwork as artwork
    found = artwork.candidates(str(artist_name).strip(), mbid=str(mbid or "").strip())
    results = {}
    if need_photo:
        picked = artwork.first_that_fits(found.get("photos") or [], artwork.fits_photo)
        results["poster"] = (_write_new_file(folder, POSTER_FILENAME, picked[0])
                             if picked else (False, "no photo found"))
    if need_background:
        picked = artwork.first_that_fits(found.get("backgrounds") or [], artwork.fits_background)
        results["background"] = (_write_new_file(folder, BACKGROUND_FILENAME, picked[0])
                                 if picked else (False, "no background found"))
    return results


__all__ = [
    "derive_artist_folder",
    "find_artist_folder",
    "folder_is_named_for",
    "locate_artist_folder",
    "template_artist_levels",
    "pick_artist_image_url",
    "download_image_bytes",
    "write_artist_artwork",
    "write_artist_jpg",
]
