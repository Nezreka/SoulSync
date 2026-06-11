"""Guard against mass-deleting library rows when storage is unreachable.

The library "sync" / cleanup paths mark a track stale when its file isn't on
disk and then delete the row. But ``os.path.exists`` returns False for EVERY
file when the music storage is momentarily unavailable — a sleeping NAS, a
dropped network mount, an unmounted Docker volume, a WSL mount hiccup. Without a
guard, one click then wipes the whole artist/library from the DB even though the
files are fine.

This mirrors the safety the deep-scan path already had (``database_update_worker``
skips removal when stale > 50% of a >100-track library — issue #828). Centralised
here so every stale-removal site can share one tested rule.
"""

from __future__ import annotations

# Don't second-guess tiny sets — a 2-track artist legitimately losing both files
# shouldn't be blocked. Above this, an implausibly large missing fraction almost
# always means "storage down", not "files actually deleted".
DEFAULT_MIN_TOTAL = 5
DEFAULT_MAX_MISSING_FRACTION = 0.5


def is_implausible_stale_removal(
    missing_count: int,
    total_count: int,
    *,
    min_total: int = DEFAULT_MIN_TOTAL,
    max_fraction: float = DEFAULT_MAX_MISSING_FRACTION,
) -> bool:
    """True when ``missing_count`` is too large a share of ``total_count`` to be a
    real deletion — i.e. the storage is probably unreachable and the caller should
    SKIP removal (and warn) rather than delete.

    Returns False for small sets (< ``min_total``) so normal cleanup of a few
    genuinely-gone files still works.
    """
    if total_count <= 0 or missing_count <= 0:
        return False
    if total_count < min_total:
        return False
    return missing_count > total_count * max_fraction


__all__ = ["is_implausible_stale_removal", "DEFAULT_MIN_TOTAL", "DEFAULT_MAX_MISSING_FRACTION"]
