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


# The orphan detector walks the transfer folder and flags any audio file whose
# path/title doesn't resolve to a DB track. If the DB's stored paths share a base
# prefix the local filesystem no longer has (remount, Docker volume change, WSL
# hiccup), EVERY file misses and the whole library looks "orphaned" — and a user
# batch-applying "move to staging" on those findings would relocate their entire
# library. Same failure mode as stale-removal, so we skip the whole result when
# the orphan share is implausibly large. Needs an absolute floor too: 3/4 orphans
# in a tiny folder is normal, 4000/5000 is a path mismatch.
DEFAULT_MIN_ORPHANS = 20
DEFAULT_MAX_ORPHAN_FRACTION = 0.5


def is_implausible_orphan_flood(
    orphan_count: int,
    total_count: int,
    *,
    min_orphans: int = DEFAULT_MIN_ORPHANS,
    max_fraction: float = DEFAULT_MAX_ORPHAN_FRACTION,
) -> bool:
    """True when so many files look orphaned that the DB↔filesystem path mapping is
    almost certainly broken (not real orphans) and the scan should create NO
    findings — otherwise a batch "move to staging" / "delete" could wipe the
    library. Below ``min_orphans`` (absolute) it always returns False so small,
    genuine orphan sets still surface.
    """
    if total_count <= 0 or orphan_count <= 0:
        return False
    if orphan_count <= min_orphans:
        return False
    return orphan_count > total_count * max_fraction


__all__ = [
    "is_implausible_stale_removal",
    "is_implausible_orphan_flood",
    "DEFAULT_MIN_TOTAL",
    "DEFAULT_MAX_MISSING_FRACTION",
    "DEFAULT_MIN_ORPHANS",
    "DEFAULT_MAX_ORPHAN_FRACTION",
]
