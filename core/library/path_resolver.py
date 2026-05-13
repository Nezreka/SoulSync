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
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger


logger = get_logger("library.path_resolver")


@dataclass
class ResolveAttempt:
    """Diagnostic record for a single `resolve_library_file_path` call.

    Returned by `resolve_library_file_path_with_diagnostic` so callers
    that need to surface a useful error message (instead of just a
    silent None) can describe what was tried. Pure data — no side
    effects, no rendering opinions.

    Fields:
        raw_path_existed: True if `os.path.exists(file_path)` returned
            True at the start of the resolver. When this is True the
            resolver short-circuits and `base_dirs_tried` will be empty.
        base_dirs_tried: The ordered list of base directories the
            resolver suffix-walked against (already filtered by
            `os.path.isdir`).
        had_config_manager: Whether a config_manager was supplied. Useful
            for distinguishing "no candidates discovered" from "couldn't
            even read config to discover".
        had_plex_client: Same, for the Plex API probe.
    """
    raw_path_existed: bool = False
    base_dirs_tried: List[str] = field(default_factory=list)
    had_config_manager: bool = False
    had_plex_client: bool = False


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
        except Exception as e:
            logger.debug("soulseek paths read failed: %s", e)

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
        except Exception as e:
            logger.debug("plex locations read failed: %s", e)

    # User-configured library music paths (Settings → Library → Music Paths).
    if config_manager is not None:
        try:
            music_paths = config_manager.get("library.music_paths", []) or []
            if isinstance(music_paths, Iterable):
                for p in music_paths:
                    if isinstance(p, str) and p.strip():
                        candidates.append(_docker_resolve_path(p.strip()))
        except Exception as e:
            logger.debug("music paths read failed: %s", e)

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
    resolved, _ = resolve_library_file_path_with_diagnostic(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
        plex_client=plex_client,
    )
    return resolved


def resolve_library_file_path_with_diagnostic(
    file_path: Any,
    *,
    transfer_folder: Optional[str] = None,
    download_folder: Optional[str] = None,
    config_manager: Any = None,
    plex_client: Any = None,
) -> Tuple[Optional[str], ResolveAttempt]:
    """Same as ``resolve_library_file_path`` but also returns a
    ``ResolveAttempt`` describing what the resolver tried.

    Use this when you need to surface a useful "we tried X, Y, Z" error
    to the user instead of a silent None. Issue #558 (gabistek, Navidrome
    on Docker): the resolver was returning None because Navidrome doesn't
    expose library filesystem paths via API (unlike Plex), and the user
    hadn't configured ``library.music_paths``. The Album Completeness
    fix endpoint surfaced a generic "Could not determine album folder"
    error with no diagnostic — user had no way to know what to configure.
    """
    attempt = ResolveAttempt(
        had_config_manager=config_manager is not None,
        had_plex_client=plex_client is not None,
    )

    if not isinstance(file_path, str) or not file_path:
        return None, attempt

    if os.path.exists(file_path):
        attempt.raw_path_existed = True
        return file_path, attempt

    path_parts = file_path.replace("\\", "/").split("/")
    base_dirs = _collect_base_dirs(transfer_folder, download_folder, config_manager, plex_client)
    attempt.base_dirs_tried = list(base_dirs)
    if not base_dirs:
        return None, attempt

    # Skip index 0 to avoid drive-letter / leading-slash artifacts
    # (e.g. "E:" or "" from a leading "/").
    for base in base_dirs:
        for i in range(1, len(path_parts)):
            candidate = os.path.join(base, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate, attempt
    return None, attempt


__all__ = [
    "ResolveAttempt",
    "resolve_library_file_path",
    "resolve_library_file_path_with_diagnostic",
]
