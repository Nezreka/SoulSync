"""Reuse an album's existing on-disk folder for new downloads (#829).

When tracks are added to an album across multiple batches (a wishlist run, the
Album Completeness job, a missed track re-downloaded later), the destination
folder is normally rebuilt from API metadata each time. If ``$albumtype`` or
``$year`` come back blank/different on a later batch, the folder *name* changes
and the album splits across folders — forcing a Reorganize afterwards.

This resolves the folder the album *already* lives in so the new track joins its
existing files instead. Matching is deliberately conservative: the exact stored
Spotify album id first (definitive), then a STRICT (>= 0.85) name+artist match —
higher than the 0.7 used elsewhere, because a wrong match here misplaces a file.

Safety rails:
  * Only ever returns a folder UNDER the transfer dir (the managed download
    tree) — never a read-only library/NAS mount the resolver happens to find.
  * Only reuses when the album lives in EXACTLY ONE folder on disk. Multiple
    folders means disc subfolders (DatabaseTrack carries no disc number, so we
    can't safely pick the right one) — those defer to the template path.
  * Any failure returns None — the caller falls back to the normal template.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from core.library.path_resolver import resolve_library_file_path
from utils.logging_config import get_logger

logger = get_logger("library.existing_album_folder")

# Strict — a wrong album match drops the file in the wrong folder.
_STRICT_ALBUM_CONFIDENCE = 0.85


def _is_under(child: str, parent: str) -> bool:
    """True if ``child`` is the same as or inside ``parent`` (normalized)."""
    try:
        child_n = os.path.normcase(os.path.normpath(os.path.abspath(child)))
        parent_n = os.path.normcase(os.path.normpath(os.path.abspath(parent)))
        return child_n == parent_n or child_n.startswith(parent_n + os.sep)
    except Exception:
        return False


def _find_album(db: Any, spotify_album_id: Optional[str], album_name: Optional[str],
                album_artist: Optional[str], active_server: Optional[str],
                expected_track_count: Optional[int]):
    """Stored Spotify id first, then a strict name+artist match. None on no match."""
    if spotify_album_id:
        try:
            album = db.get_album_by_spotify_album_id(spotify_album_id)
            if album:
                return album
        except Exception as e:
            logger.debug("album-by-spotify-id lookup failed: %s", e)
    if album_name and album_artist:
        try:
            match, confidence = db.check_album_exists_with_editions(
                title=album_name, artist=album_artist,
                confidence_threshold=_STRICT_ALBUM_CONFIDENCE,
                expected_track_count=expected_track_count,
                server_source=active_server,
            )
            if match and confidence >= _STRICT_ALBUM_CONFIDENCE:
                return match
        except Exception as e:
            logger.debug("strict album name+artist match failed: %s", e)
    return None


def resolve_existing_album_folder(
    *,
    db: Any,
    transfer_dir: Optional[str],
    album_name: Optional[str] = None,
    album_artist: Optional[str] = None,
    spotify_album_id: Optional[str] = None,
    active_server: Optional[str] = None,
    expected_track_count: Optional[int] = None,
    config_manager: Any = None,
    resolver=resolve_library_file_path,
) -> Optional[str]:
    """Return the on-disk folder an existing album lives in (so a new track joins
    it) or None to fall back to the templated path. See module docstring."""
    if not transfer_dir or not os.path.isdir(transfer_dir):
        return None
    if not db:
        return None

    album = _find_album(db, spotify_album_id, album_name, album_artist,
                        active_server, expected_track_count)
    if not album:
        return None

    try:
        tracks = db.get_tracks_by_album(album.id)
    except Exception as e:
        logger.debug("get_tracks_by_album(%s) failed: %s", getattr(album, 'id', '?'), e)
        return None

    folders = set()
    for t in tracks:
        file_path = getattr(t, 'file_path', None)
        if not file_path:
            continue
        try:
            resolved = resolver(file_path, transfer_folder=transfer_dir,
                                config_manager=config_manager)
        except Exception:
            resolved = None
        if not resolved:
            continue
        folder = os.path.dirname(resolved)
        if _is_under(folder, transfer_dir):
            folders.add(os.path.normpath(folder))

    # Single folder under the transfer dir → reuse it. Zero (nothing on disk yet)
    # or many (disc subfolders) → let the template decide.
    if len(folders) == 1:
        reuse = next(iter(folders))
        logger.info("[Existing Album Folder] Reusing '%s' for album '%s'",
                    reuse, getattr(album, 'title', album_name))
        return reuse
    return None


__all__ = ["resolve_existing_album_folder"]
