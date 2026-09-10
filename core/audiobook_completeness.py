"""Decide whether a downloaded audiobook is actually the whole book.

Music can trust a download client's "complete": an album's completeness is a
track count you can check in a second, and a wrong answer costs one track.
An audiobook cannot. A torrent finishes when the files it was ASKED for finish,
which is not the same as the book being whole — a release can be missing its
last three chapters, or be a sample pack, and every file present will play
perfectly. Import it and the failure surfaces hours later, at the point the
listener runs out of book.

The gift that makes this checkable: Audible publishes ``runtime_length_min`` for
every title, so we know how long the book IS. Summing the audio on disk and
comparing is a real measurement, not a heuristic on filenames or file counts.

Books that come up short are STAGED, not failed. The usual reason is a torrent
still fetching, or a release that will be completed by its uploader; the right
answer is to keep the files and look again later, which is what the monitor does
until the staging deadline runs out.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("audiobook_completeness")

# How much of the expected runtime has to be on disk. Not 100%: Audible's
# runtime includes the publisher's own intro/outro and rounds to the minute, and
# a rip that drops a few seconds of silence at a chapter boundary is still the
# whole book. Below this and chapters are genuinely missing.
DEFAULT_TOLERANCE = 0.92

# A release that is LONGER than the book is not automatically wrong — box sets,
# duplicated intros and bonus interviews all overshoot — but far over means the
# folder holds something else entirely, like a whole series.
MAX_OVERSHOOT = 1.60

# How long a short book is kept staged before giving up. Torrents finish late
# and uploaders repair releases, so patience is correct; forever is not.
DEFAULT_STAGING_DAYS = 7

_AUDIO_EXTENSIONS = frozenset({
    ".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4",
})

# Bounds on bytes-per-minute for anything that could be a speech recording, used
# only when NOTHING in the folder can be read and there is no local rate to
# derive. Wide on purpose: this is a sanity check, not a measurement, and it is
# the difference between "cannot verify" and "obviously wrong".
_MIN_BYTES_PER_MINUTE = 60 * 1024
_MAX_BYTES_PER_MINUTE = 40 * 1024 * 1024


def measure_duration_seconds(path: Path) -> Optional[float]:
    """Playing time of one audio file, or None when it cannot be read.

    None is not zero. A file whose header mutagen cannot parse must not be
    counted as silence, or one unreadable chapter would make a complete book
    look short.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=False)
        length = getattr(getattr(audio, "info", None), "length", None)
        if length is None:
            return None
        length = float(length)
        return length if length > 0 else None
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the duration of %s: %s", path, exc)
        return None


def collect_audio(folder: Path) -> List[Path]:
    """Every audio file under a download, recursively."""
    root = Path(folder)
    if root.is_file():
        return [root] if root.suffix.lower() in _AUDIO_EXTENSIONS else []
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
    )


def assess(
    folder: str,
    expected_minutes: Optional[int],
    tolerance: float = DEFAULT_TOLERANCE,
    abridged: bool = False,
) -> Dict[str, Any]:
    """Is what is on disk the whole book?

    Returns ``{complete, measured_minutes, expected_minutes, ratio, files,
    unreadable, reason, measured_by}``.

    Deliberately measures PLAYING TIME, never file count. There is no standard
    for how an audiobook is cut up: the same title ships as one m4b, as 20
    chapters, or as 50, and every one of those is complete. Counting files
    could only ever compare a release against another release. Runtime is the
    one thing that describes the BOOK.

    With no expected runtime there is nothing to compare against, so the only
    check left is "is there any audio at all" — a book we know nothing about is
    let through rather than held forever on a comparison that cannot be made.

    ``abridged`` is that same case. Audible publishes the runtime of the
    edition being viewed, which is the unabridged one; an abridged release is
    routinely half of it and would look like a book missing half its chapters
    forever. We do not know how long the abridgement is meant to be, so we do
    not pretend to. A dramatised adaptation (GraphicAudio and the like) is the
    same problem again and rides the same flag: a full-cast re-recording with
    music and effects, sold in parts, whose length has no relation to the
    reading Audible sells.
    """
    files = collect_audio(Path(folder))
    result: Dict[str, Any] = {
        "complete": False,
        "measured_minutes": 0.0,
        "expected_minutes": int(expected_minutes or 0),
        "ratio": 0.0,
        "files": len(files),
        "unreadable": 0,
        "reason": "",
        "measured_by": "duration",
    }

    if not files:
        result["reason"] = "No audio files in the download"
        return result

    total_seconds = 0.0
    readable_bytes = 0
    unreadable: List[Path] = []
    for path in files:
        seconds = measure_duration_seconds(path)
        if seconds is None:
            unreadable.append(path)
        else:
            total_seconds += seconds
            readable_bytes += _safe_size(path)

    result["unreadable"] = len(unreadable)

    if unreadable and total_seconds and readable_bytes:
        # Some readable, some not. The unreadable ones are almost always the
        # same encode as their neighbours, so their length is estimated at the
        # rate measured FROM THIS FOLDER rather than from a constant guess — a
        # 64kbps chapter and a 128kbps one differ by a factor of two, and a
        # hardcoded rate would be wrong for one of them.
        bytes_per_second = readable_bytes / total_seconds
        missing_bytes = sum(_safe_size(p) for p in unreadable)
        if bytes_per_second > 0:
            total_seconds += missing_bytes / bytes_per_second
        result["measured_by"] = "duration+size"
    elif unreadable and not total_seconds:
        # Nothing could be read at all. There is no honest way to measure this,
        # so the only question left is whether the bytes on disk could plausibly
        # be a book of this length. Held only when they obviously could not.
        total_bytes = sum(_safe_size(p) for p in files)
        expected_for_check = int(expected_minutes or 0)
        result["measured_by"] = "size"
        if expected_for_check > 0:
            per_minute = total_bytes / expected_for_check
            if per_minute < _MIN_BYTES_PER_MINUTE:
                result["reason"] = (
                    f"Unreadable audio, and {total_bytes // (1024 * 1024)}MB is far too "
                    f"little for a {expected_for_check}-minute book"
                )
                return result
            if per_minute > _MAX_BYTES_PER_MINUTE:
                result["reason"] = (
                    f"Unreadable audio, and {total_bytes // (1024 * 1024)}MB is far too "
                    f"much for a {expected_for_check}-minute book"
                )
                return result
        result["complete"] = True
        result["reason"] = "Could not read playing time; accepted on file size"
        return result
    measured_minutes = total_seconds / 60.0
    result["measured_minutes"] = round(measured_minutes, 1)

    expected = int(expected_minutes or 0)
    if expected <= 0:
        result["complete"] = True
        result["reason"] = "No published runtime to compare against"
        return result

    if abridged:
        # An abridgement has its own runtime that nobody publishes. Comparing
        # it against the unabridged figure would stage a perfectly good
        # download for a week and then fail it to the wishlist, which would
        # re-grab the same release and do it again.
        result["complete"] = True
        result["measured_by"] = "abridged"
        result["reason"] = (
            f"{int(measured_minutes)} minutes present; abridged releases have no "
            f"published runtime to check against"
        )
        return result

    ratio = measured_minutes / expected
    result["ratio"] = round(ratio, 3)

    if ratio < max(0.0, float(tolerance)):
        short_by = max(0, int(expected - measured_minutes))
        result["reason"] = (
            f"Only {int(measured_minutes)} of {expected} minutes present "
            f"— about {short_by} minutes short"
        )
        return result

    if ratio > MAX_OVERSHOOT:
        result["reason"] = (
            f"{int(measured_minutes)} minutes for a {expected}-minute book "
            f"— this folder holds more than one title"
        )
        return result

    result["complete"] = True
    result["reason"] = f"{int(measured_minutes)} of {expected} minutes present"
    return result


def _safe_size(path: Path) -> int:
    try:
        return int(os.path.getsize(path))
    except OSError:
        return 0


def staging_expired(started_at: float, days: Optional[float] = None) -> bool:
    """Whether a short book has been waiting long enough to give up on.

    Torrents finish late and uploaders repair releases, so waiting is right;
    waiting forever means a broken release occupies a wishlist row for good.
    """
    limit = DEFAULT_STAGING_DAYS if days is None else float(days)
    if limit <= 0:
        return False
    return (time.time() - float(started_at or 0)) > (limit * 86400)


def tolerance_from_settings() -> float:
    """How complete is complete enough, from settings."""
    try:
        from core.settings import config_manager

        value = float(config_manager.get("audiobooks.completeness_tolerance",
                                         DEFAULT_TOLERANCE))
    except Exception:                                       # noqa: BLE001
        return DEFAULT_TOLERANCE
    # A tolerance outside this range is a typo, not an intention: 0 would import
    # anything and >1 could never be satisfied.
    return value if 0.1 <= value <= 1.0 else DEFAULT_TOLERANCE


def staging_days_from_settings() -> float:
    try:
        from core.settings import config_manager

        return max(0.0, float(config_manager.get("audiobooks.staging_days",
                                                 DEFAULT_STAGING_DAYS)))
    except Exception:                                       # noqa: BLE001
        return float(DEFAULT_STAGING_DAYS)
