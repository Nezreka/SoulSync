"""Resolve database-stored file paths to actual files on disk.

Database track rows store file paths as the media server reported them
(`/music/Artist/Album/track.flac`, `H:\\Music\\Artist\\...`, etc). When
SoulSync runs in Docker, those paths don't exist as-is inside the
container — the user's library is bind-mounted at a container path
(commonly `/music`) that has nothing to do with what Plex/Jellyfin
recorded. Same problem for native installs that point at a NAS via SMB:
the path the media server scanned isn't the path SoulSync reads.

The resolver tries the raw path first (cheap happy-path), then walks
progressively shorter suffixes against every configured base directory:
the transfer folder, the slskd download folder, every configured Plex
library location, and every entry in the user's `library.music_paths`
config. The first existing match wins.

This module replaces four duplicated copies of the same function (each
with the same incomplete logic) that lived in
`core/repair_worker.py` and three modules under `core/repair_jobs/`.
The duplicates only checked the transfer + download folders and
silently returned None for files in the actual media-server library —
which is why, for example, the Album Completeness "Auto-Fill" button
returned ``Could not determine album folder from existing tracks`` for
every Docker user (issue #476).

The web server has its own near-duplicate at
``web_server.py:_resolve_library_file_path`` which already covers the
full search space; this module is the lifted, shared version usable
from any background worker.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from utils.logging_config import get_logger


logger = get_logger("library.path_resolver")


def _docker_resolve_path(path_str: Any) -> Optional[str]:
    """Translate Windows-style paths to the Docker container layout.

    Mirrors ``core/imports/paths.docker_resolve_path`` but kept local to
    avoid a cross-package import in case this module is consumed early
    in a job startup. Returns the input unchanged outside Docker.
    """
    if not isinstance(path_str, str):
        return None
    if (
        os.path.exists("/.dockerenv")
        and len(path_str) >= 3
        and path_str[1] == ":"
        and path_str[0].isalpha()
    ):
        drive_letter = path_str[0].lower()
        rest = path_str[2:].replace("\\", "/")
        return f"/host/mnt/{drive_letter}{rest}"
    return path_str


def _collect_base_dirs(
    transfer_folder: Optional[str],
    download_folder: Optional[str],
    config_manager: Any,
    plex_client: Any,
) -> list[str]:
    """Build the ordered list of base directories to probe."""
    candidates: list[Optional[str]] = []

    if transfer_folder:
        candidates.append(_docker_resolve_path(transfer_folder))
    if download_folder:
        candidates.append(_docker_resolve_path(download_folder))

    if config_manager is not None:
        try:
            transfer_cfg = config_manager.get("soulseek.transfer_path", "") or ""
            download_cfg = config_manager.get("soulseek.download_path", "") or ""
            if transfer_cfg:
                candidates.append(_docker_resolve_path(transfer_cfg))
            if download_cfg:
                candidates.append(_docker_resolve_path(download_cfg))
        except Exception:
            pass

    # Plex-reported library locations (handles "Plex scanned at /music but
    # SoulSync mounts at /library" cases).
    if plex_client is not None:
        try:
            server = getattr(plex_client, "server", None)
            music_library = getattr(plex_client, "music_library", None)
            if server is not None and music_library is not None:
                for loc in getattr(music_library, "locations", []) or []:
                    if loc:
                        candidates.append(loc)
        except Exception:
            pass

    # User-configured library music paths (Settings → Library → Music Paths).
    if config_manager is not None:
        try:
            music_paths = config_manager.get("library.music_paths", []) or []
            if isinstance(music_paths, Iterable):
                for p in music_paths:
                    if isinstance(p, str) and p.strip():
                        candidates.append(_docker_resolve_path(p.strip()))
        except Exception:
            pass

    # De-duplicate while preserving order, drop empties / non-existent dirs.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isdir(c):
            out.append(c)
    return out


def resolve_library_file_path(
    file_path: Any,
    *,
    transfer_folder: Optional[str] = None,
    download_folder: Optional[str] = None,
    config_manager: Any = None,
    plex_client: Any = None,
) -> Optional[str]:
    """Resolve a stored DB path to an actual file on disk.

    Args:
        file_path: The path as recorded in the database (may not exist
            as-is in the current process's filesystem view).
        transfer_folder: Optional explicit transfer-folder override
            (bypasses the config_manager lookup). Useful when the caller
            already cached one.
        download_folder: Optional explicit download-folder override.
        config_manager: When provided, the resolver also pulls
            ``soulseek.transfer_path``, ``soulseek.download_path``, and
            ``library.music_paths`` from config to expand the search.
        plex_client: When provided, every Plex-reported music-library
            location is added to the search.

    Returns:
        The first existing path on disk, or None when no match is found.
        Never raises — failure is the None return.
    """
    if not isinstance(file_path, str) or not file_path:
        return None

    if os.path.exists(file_path):
        return file_path

    path_parts = file_path.replace("\\", "/").split("/")
    base_dirs = _collect_base_dirs(transfer_folder, download_folder, config_manager, plex_client)
    if not base_dirs:
        return None

    # Skip index 0 to avoid drive-letter / leading-slash artifacts
    # (e.g. "E:" or "" from a leading "/").
    for base in base_dirs:
        for i in range(1, len(path_parts)):
            candidate = os.path.join(base, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate
    return None


__all__ = ["resolve_library_file_path"]
