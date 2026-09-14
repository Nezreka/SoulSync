"""Optional pre-download music size-density guard. MB means 1,000,000 bytes.

Unknown sizes/durations cannot be checked and remain eligible. This is a
candidate filter, not a disk quota or a promise about post-processed output.
"""
from math import isfinite

from utils.logging_config import get_logger

logger = get_logger("downloads.size_limit")


def positive_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def configured_limit():
    from core.settings import config_manager
    return positive_number(config_manager.get('download_source.max_mb_per_minute', 0)) or 0


def exceeds_size_limit(size_bytes, duration_ms, limit):
    size = positive_number(size_bytes)
    duration = positive_number(duration_ms)
    cap = positive_number(limit)
    if size is None or duration is None or cap is None:
        return False
    return size / 1_000_000 > cap * (duration / 60_000)


def filter_music_candidates(candidates, *, expected_duration_ms=None):
    """Preserve candidate order; never compare an album's size to one song."""
    cap = configured_limit()
    if not cap:
        return candidates
    kept = []
    for candidate in candidates:
        if getattr(candidate, 'username', '') in ('torrent', 'usenet', 'lidarr'):
            kept.append(candidate)  # Whole-release sizes require an album duration.
            continue
        duration = positive_number(getattr(candidate, 'duration', None)) or expected_duration_ms
        if exceeds_size_limit(getattr(candidate, 'size', None), duration, cap):
            logger.info("Skipping %s: advertised size exceeds %g MB/min",
                        getattr(candidate, 'filename', '?'), cap)
        else:
            kept.append(candidate)
    return kept
