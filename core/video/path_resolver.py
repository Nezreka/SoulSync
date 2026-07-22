"""Video path resolver — from the SERVER's view of a file path to a real one.

The scanner stores what the media server reports (Plex ``part.file`` /
Jellyfin ``Path``) — the path as seen from INSIDE the server's own container/
host. From SoulSync's filesystem view that path often doesn't exist (different
Docker mounts, drive letters, NAS exports). This is the video twin of the
music side's ``core.library.path_resolver`` (the issue-#476 class of bugs):
try the stored path as-is, then re-root its tail segments against the folders
SoulSync actually knows about until a file exists.

Upgrades depend on this: replacing an owned copy means finding the REAL file,
not the template location SoulSync would have chosen for a fresh import.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

# How many trailing path segments to re-root (file, its folder, a parent…).
_PROBE_DEPTH = 4


def video_base_dirs(db) -> list:
    """The local folders video files can live under, per the user's settings:
    the movie/TV library roots + the transfer folder. Missing/blank skipped."""
    dirs = []
    for key in ("movies_path", "tv_path", "transfer_path"):
        try:
            v = db.get_setting(key)
        except Exception:   # noqa: BLE001 - a settings hiccup just narrows the search
            v = None
        if v:
            dirs.append(str(v))
    return dirs


def resolve_video_file_path(stored_path, base_dirs, *, size_bytes=None,
                            exists: Callable[[str], bool] = os.path.exists,
                            getsize: Callable[[str], int] = os.path.getsize) -> Optional[str]:
    """Resolve a DB-stored (server-view) file path to a file that exists HERE.

    Tries the raw path first, then joins the path's last N..1 segments onto
    each base dir — DEEPEST FIRST, so the most specific match wins (a bare
    basename like 'movie.mkv' must never shadow the real
    'The Matrix (1999)/movie.mkv' when both exist).

    This resolver's callers REPLACE the file they resolve to (upgrades), so a
    re-rooted candidate must prove identity: when ``size_bytes`` is known, a
    candidate whose on-disk size differs is rejected. The raw stored path is
    exempt (it IS the recorded location). Returns the first hit or None —
    never raises."""
    if not isinstance(stored_path, str) or not stored_path:
        return None
    if exists(stored_path):
        return stored_path
    parts = [p for p in stored_path.replace("\\", "/").split("/") if p]
    if not parts:
        return None

    def _same_file(cand: str) -> bool:
        if not size_bytes:
            return True
        try:
            return int(getsize(cand)) == int(size_bytes)
        except Exception:   # noqa: BLE001 - unreadable size → can't prove identity
            return False

    for base in (base_dirs or []):
        base = str(base or "").rstrip("/\\")
        if not base:
            continue
        for k in range(min(_PROBE_DEPTH, len(parts)), 0, -1):
            cand = os.path.join(base, *parts[-k:])
            if exists(cand) and _same_file(cand):
                return cand
    return None
