"""Minimum-free-disk guard — refuse new grabs when the target drive is nearly full.

Radarr's min-free-space check: with ``min_free_disk_gb`` set (organization
settings; 0 = off), every enqueue path asks :func:`has_room` before starting a
download. The guard walks up from the target dir to the nearest EXISTING
ancestor (the dir itself may not exist yet) and compares the drive's free
space. Failure discipline: an unreadable filesystem answers "has room" — a
probe error must never wedge downloads.
"""

from __future__ import annotations

import os
import shutil

from utils.logging_config import get_logger

logger = get_logger("video.disk_guard")


def free_gb(path: str) -> float | None:
    """Free space (GB) on the drive holding ``path`` (nearest existing
    ancestor), or None when it can't be probed."""
    p = str(path or "").strip()
    if not p:
        return None
    probe = os.path.abspath(p)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free / 1024 ** 3
    except OSError:
        logger.debug("disk probe failed for %s", probe, exc_info=True)
        return None


def has_room(target_dir: str, settings: dict | None) -> tuple[bool, float | None]:
    """(ok, free_gb). ok False ONLY when the guard is on, the probe worked,
    and free space is under the floor."""
    try:
        floor = float((settings or {}).get("min_free_disk_gb") or 0)
    except (TypeError, ValueError):
        floor = 0
    if floor <= 0:
        return True, None
    free = free_gb(target_dir)
    if free is None:
        return True, None
    return free >= floor, free
