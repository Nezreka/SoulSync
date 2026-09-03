"""Minimum-free-disk guard — refuse new grabs when a drive involved is nearly full.

Radarr's min-free-space check: with ``min_free_disk_gb`` set (organization
settings; 0 = off), every enqueue path asks :func:`has_room` before starting a
download. The guard walks up from the target dir to the nearest EXISTING
ancestor (the dir itself may not exist yet) and compares the drive's free
space. Failure discipline: an unreadable filesystem answers "has room" — a
probe error must never wedge downloads.

TWO drives matter, not one. The guard originally checked only where the file
would END UP, and Boulder lost 18 YouTube downloads to ``[Errno 28] No space
left on device`` while that destination had thirteen terabytes free: the volume
the file was being BUILT on — subtitle writes, the ffmpeg remux, ordinary Python
temp — had 500MB. A guard that reads the wrong drive is worse than no guard,
because it answers the question confidently.
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


def scratch_dir() -> str:
    """Where downloads are assembled before they land: yt-dlp's subtitle and
    remux temporaries, ffmpeg's working files, ordinary Python tempfiles. Often
    a different drive from the library, which is the whole point."""
    import tempfile
    return tempfile.gettempdir()


def check_room(target_dir: str, settings: dict | None) -> dict:
    """``{ok, free, floor, where}`` across BOTH drives a download touches.

    ``where`` names the one that is short ('library' or 'scratch') so the caller
    can say which drive to clear — being told to free space on a drive with
    terabytes spare is how a real disk problem gets dismissed as a bug.

    Same on/off switch as before: a floor of 0 disables the guard entirely, and
    an unreadable filesystem answers "has room". A probe error must never wedge
    downloads.
    """
    out = {"ok": True, "free": None, "floor": 0.0, "where": None}
    try:
        floor = float((settings or {}).get("min_free_disk_gb") or 0)
    except (TypeError, ValueError):
        floor = 0
    out["floor"] = floor
    if floor <= 0:
        return out

    lib = free_gb(target_dir)
    if lib is not None:
        out["free"], out["where"] = lib, "library"
        if lib < floor:
            out["ok"] = False
            return out

    scratch = free_gb(scratch_dir())
    if scratch is None:
        return out
    # Same volume as the library? Then it is one number, already judged, and
    # reporting it twice would just be confusing.
    if lib is not None and _same_volume(target_dir, scratch_dir()):
        return out
    if scratch < floor:
        return {"ok": False, "free": scratch, "floor": floor, "where": "scratch"}
    return out


def _same_volume(a: str, b: str) -> bool:
    """Whether two paths live on the same device. False when it cannot be told —
    an extra check on one drive is cheap; a skipped check on two is the bug."""
    try:
        return os.stat(_existing(a)).st_dev == os.stat(_existing(b)).st_dev
    except (OSError, TypeError, ValueError):
        return False


def _existing(path: str) -> str:
    probe = os.path.abspath(str(path or "").strip())
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return probe


def shortfall_message(res: dict, target_dir: str) -> str:
    """Why the grab was refused, naming the drive that is actually short.

    'Only 0.5 GB free on H:\\Media' when H: has terabytes spare reads as a bug in
    SoulSync and gets dismissed. Saying it was the scratch drive sends the user
    to the disk that is really full.
    """
    free = res.get("free")
    floor = res.get("floor") or 0
    where = "the temporary/working drive (%s)" % scratch_dir() \
        if res.get("where") == "scratch" else target_dir
    return "Only %.1f GB free on %s — under your %.0f GB minimum" % (free or 0, where, floor)


def has_room(target_dir: str, settings: dict | None) -> tuple[bool, float | None]:
    """(ok, free_gb) — the original two-value form, kept for existing callers.
    Prefer :func:`check_room`, which also says WHICH drive is short."""
    res = check_room(target_dir, settings)
    return res["ok"], res["free"]
