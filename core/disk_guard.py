"""Minimum-free-disk guard for the MUSIC download pipeline.

The video side has had this since its downloads phase (core/video/disk_guard);
the music side had NOTHING — a fresh Proxmox-LXC install left on the default
./downloads / ./Transfer paths downloads onto the 8GB root disk until the
container hangs (reported live on Discord by Kazimir Iskander). Every music
download — Soulseek AND the streaming sources — funnels through
DownloadOrchestrator.download(), which asks :func:`music_has_room` before
dispatching.

Failure discipline matches the video guard: an unreadable filesystem answers
"has room" — a probe error must never wedge downloads. ``soulseek.
min_free_disk_gb`` (default 5.0; 0 disables) is the floor.
"""

from __future__ import annotations

import os
import shutil

from utils.logging_config import get_logger

logger = get_logger("disk_guard")

DEFAULT_FLOOR_GB = 5.0

# Test hook: the suite must never depend on the CI runner's real fill level
# (conftest pins this to 0.0; tests of the guard itself set it back to None).
_floor_override: float | None = None


def free_gb(path: str) -> float | None:
    """Free space (GB) on the drive holding ``path`` (nearest existing
    ancestor — the dir itself may not exist yet), or None when unprobeable."""
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


def floor_gb() -> float:
    """The configured minimum-free floor (GB). 0 = guard off."""
    if _floor_override is not None:
        return _floor_override
    try:
        from config.settings import config_manager
        return max(0.0, float(config_manager.get("soulseek.min_free_disk_gb", DEFAULT_FLOOR_GB)))
    except Exception:   # noqa: BLE001 - config hiccup must never wedge downloads
        return DEFAULT_FLOOR_GB


def music_has_room() -> tuple[bool, float | None, float]:
    """(ok, free_gb, floor_gb) for the music download folder.

    ok is False only when the floor is set AND the probe succeeded AND free
    space is below it — unknown always passes (never wedge on a probe error).
    """
    floor = floor_gb()
    if floor <= 0:
        return True, None, floor
    try:
        from config.settings import config_manager
        target = config_manager.get("soulseek.download_path", "./downloads")
    except Exception:   # noqa: BLE001
        return True, None, floor
    free = free_gb(target)
    if free is None:
        return True, None, floor
    if free < floor:
        logger.warning("disk guard: %.1f GB free on the download disk (floor %.0f GB) — refusing new downloads",
                       free, floor)
        return False, free, floor
    return True, free, floor
