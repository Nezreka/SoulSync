"""Decision logic for the SoulSync standalone Deep Scan's untracked → Staging move.

The standalone deep scan (``_run_soulsync_deep_scan`` in web_server) walks the
Transfer folder, diffs it against the ``soulsync`` rows in the DB, and relocates
every file it can't find a DB record for into Staging for auto-import. That's fine
when Transfer is a scratch/landing area — files arrive, get moved, imported, and
recorded, so a later scan only ever sees a few genuinely-new arrivals.

It is a DATA-LOSS trap when the DB is empty or out of sync with disk (a volume
swap, a DB reset, external tag edits) while Transfer holds the user's real library:
a path-only diff then flags the *entire* library as "untracked" and the scan
relocates all of it (issue #904). The same failure mode the orphan detector and the
media-server deep scan already guard against (``core.library.stale_guard``) — this
path just never used the guard.

This module is the pure, testable decision: given the Transfer file set, the DB's
known paths, and the user's "Transfer is my permanent library" preference, decide
WHICH files are untracked and WHETHER it's safe to relocate them. The web layer does
only the I/O (walk/move/delete) based on the returned plan.
"""

from __future__ import annotations

from typing import Iterable, Set

from core.library.stale_guard import (
    DEFAULT_MAX_ORPHAN_FRACTION,
    DEFAULT_MIN_ORPHANS,
    is_implausible_orphan_flood,
)

# Block reason codes (web layer turns these into a user-facing warning).
BLOCK_NONE = ""
BLOCK_TRANSFER_PERMANENT = "transfer_permanent"
BLOCK_DESYNC = "desync"


def _norm(path: str) -> str:
    """Normalize a path for cross-platform comparison (Windows vs Unix separators)."""
    return str(path).replace("\\", "/")


def diff_untracked(transfer_files: Iterable[str], db_paths: Iterable[str]) -> Set[str]:
    """Files present in ``transfer_files`` but with no matching ``db_paths`` record.

    Comparison is separator-normalized, so a DB path stored with one separator style
    still matches the on-disk path. Pure — no I/O. Returns the original (un-normalized)
    transfer paths so the caller can act on the real filesystem entries.
    """
    db_norm = {_norm(p) for p in db_paths if p}
    return {f for f in transfer_files if _norm(f) not in db_norm}


def plan_standalone_deep_scan(
    transfer_files: Iterable[str],
    db_paths: Iterable[str],
    *,
    never_move: bool = False,
    min_untracked: int = DEFAULT_MIN_ORPHANS,
    max_fraction: float = DEFAULT_MAX_ORPHAN_FRACTION,
) -> dict:
    """Plan the untracked → Staging move for a standalone deep scan. Pure — no I/O.

    Returns a dict:
      * ``untracked`` (set[str]) — Transfer files with no DB record.
      * ``move_blocked`` (bool) — True when the untracked files must NOT be relocated.
      * ``block_reason`` (str) — ``BLOCK_TRANSFER_PERMANENT`` / ``BLOCK_DESYNC`` / "".

    The move is blocked when either:
      * ``never_move`` is set (the user marked Transfer as their permanent library), or
      * the untracked share is implausibly large (> ``min_untracked`` files AND
        > ``max_fraction`` of the folder) — the empty/desynced-DB signature, where a
        path-only diff would relocate the whole library. Below that floor a normal
        batch of new arrivals still moves as before.

    ``move_blocked`` is only ever True when there ARE untracked files; an empty scan
    or a clean library returns ``move_blocked=False`` with no reason.
    """
    transfer_set = set(transfer_files)  # concrete (handles generators) + dedups
    untracked = diff_untracked(transfer_set, db_paths)
    total = len(transfer_set)
    n_untracked = len(untracked)

    if n_untracked == 0:
        return {"untracked": untracked, "move_blocked": False, "block_reason": BLOCK_NONE}

    if never_move:
        return {"untracked": untracked, "move_blocked": True, "block_reason": BLOCK_TRANSFER_PERMANENT}

    if is_implausible_orphan_flood(
        n_untracked, total, min_orphans=min_untracked, max_fraction=max_fraction
    ):
        return {"untracked": untracked, "move_blocked": True, "block_reason": BLOCK_DESYNC}

    return {"untracked": untracked, "move_blocked": False, "block_reason": BLOCK_NONE}


__all__ = [
    "diff_untracked",
    "plan_standalone_deep_scan",
    "BLOCK_NONE",
    "BLOCK_TRANSFER_PERMANENT",
    "BLOCK_DESYNC",
]
