"""Pure helpers for the video download pipeline: locate a finished file in the
download dir and work out where it should move to.

slskd writes a completed download somewhere under the shared download folder (it
mirrors the remote folder structure), so we locate the file by basename. Kept pure
(filesystem access is injected) so it's unit-tested without touching disk.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import os
from typing import Any, Callable


def basename_of(path: Any) -> str:
    """Final path segment, handling both / and \\ separators (slskd uses Windows-style)."""
    return str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def find_completed_file(download_dir: str, filename: str, lister: Callable) -> str | None:
    """Find the downloaded file on disk by basename. ``lister(dir)`` yields candidate
    full paths (injected: os.walk-based in the monitor, a list in tests). Returns the
    largest match (the real video, not a stray same-named bit), or None."""
    base = basename_of(filename)
    if not base or not download_dir:
        return None
    matches = [p for p in lister(download_dir) if basename_of(p) == base]
    if not matches:
        return None
    return matches[0] if len(matches) == 1 else max(matches, key=len)


def dest_path_for(target_dir: str, src_path: str) -> str:
    """Where a finished file moves to inside its library folder (flat, by basename)."""
    return os.path.join(str(target_dir or ""), basename_of(src_path))


def target_dir_for(kind: str, paths: dict) -> str:
    """Pick the library folder for a download's kind. ``paths`` = the config dict
    ({movies_path, tv_path, youtube_path})."""
    paths = paths or {}
    k = str(kind or "").lower()
    if k == "movie":
        return paths.get("movies_path") or ""
    if k in ("show", "tv", "episode", "season", "series"):
        return paths.get("tv_path") or ""
    if k == "youtube":
        return paths.get("youtube_path") or ""
    return ""


__all__ = ["basename_of", "find_completed_file", "dest_path_for", "target_dir_for"]
