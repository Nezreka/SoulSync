"""WAL checkpointing for the long-running Library-v2 migration paths.

iss32-M04: SQLite in WAL mode appends every write to ``<db>-wal`` and only
folds it back into the main database at a *checkpoint*. A checkpoint needs a
moment with no open transaction, so a migration that holds one write
transaction from start to finish can never checkpoint — the WAL grows without
bound and every lookup inside that transaction pays for the growing WAL index.
Nezreka's 9 GB library showed exactly that: WAL 85 → 135 MB, main database
frozen at 9,652 MB, throughput falling from 4 to 1.4 MB/min.

Checkpointing is therefore only half the fix and useless on its own: the
batched commits from iss32-M05 create the windows this module needs.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.wal")

# TRUNCATE resets the WAL file to zero bytes once it has been folded back in.
# PASSIVE only folds what it can without ever waiting for a reader, which is
# why it is the safe default *inside* a batch loop: it can never block the
# migration behind someone else's long read.
CHECKPOINT_PASSIVE = "PASSIVE"
CHECKPOINT_TRUNCATE = "TRUNCATE"


def checkpoint_wal(connection: Any, mode: str = CHECKPOINT_TRUNCATE) -> Optional[int]:
    """Fold the WAL back into the main database. Returns pages checkpointed.

    Best-effort by contract: a checkpoint that cannot run right now (a reader
    holds the WAL, the database is not in WAL mode, the connection is inside a
    transaction) is not an error — the next call gets it. Returning ``None``
    means "did not happen", never "failed".

    The caller must not be inside a transaction. ``PRAGMA wal_checkpoint``
    silently degrades to a no-op there, which would make the whole exercise
    look like it worked while the WAL keeps growing.
    """
    if getattr(connection, "in_transaction", False):
        logger.debug("wal checkpoint skipped: connection is inside a transaction")
        return None
    try:
        row = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    except Exception as exc:  # noqa: BLE001 - never fail the caller's work
        logger.debug("wal checkpoint (%s) skipped: %s", mode, exc)
        return None
    if not row:
        return None
    # (busy, log_pages, checkpointed_pages); busy=1 means a reader blocked it.
    busy, _log_pages, checkpointed = (int(row[0]), int(row[1]), int(row[2]))
    if busy:
        logger.debug("wal checkpoint (%s) was blocked by a reader", mode)
    return checkpointed


class PeriodicCheckpointer:
    """Checkpoint every ``every_n`` batches, and leave a window between them.

    Two jobs, both about the same scarce resource:

    **Checkpointing.** A checkpoint over a large WAL is real work; doing it
    after each 500-row batch would trade one stall for many. Counting batches
    keeps the WAL bounded without paying that cost on every commit.

    **Yielding.** Committing between batches is necessary but not sufficient.
    Measured at 8,000 tracks: with per-batch commits a competing writer still
    waited for the entire backfill, because the loop reacquires the write lock
    within microseconds of releasing it and SQLite's busy handler backs off to
    polling every 100 ms — so it keeps arriving while the lock is held again.
    A deliberate pause after each commit is what actually lets the config save
    and the media-server scan through.

    The pause is a fraction of the batch's own duration (capped), so it costs a
    bounded percentage of the run regardless of hardware, and widens exactly
    when batches are slow — which is when contention hurts most.
    """

    def __init__(self, connection: Any, *, every_n: int = 20,
                 mode: str = CHECKPOINT_TRUNCATE,
                 yield_fraction: float = 0.15,
                 max_yield_seconds: float = 0.05) -> None:
        self._connection = connection
        self._every_n = max(int(every_n), 1)
        self._mode = mode
        self._yield_fraction = max(float(yield_fraction), 0.0)
        self._max_yield = max(float(max_yield_seconds), 0.0)
        self._seen = 0
        self._last_at = time.monotonic()

    def batch_committed(self) -> None:
        now = time.monotonic()
        batch_seconds = now - self._last_at
        self._seen += 1
        if self._seen % self._every_n == 0:
            checkpoint_wal(self._connection, self._mode)
        if self._yield_fraction and self._max_yield:
            time.sleep(min(batch_seconds * self._yield_fraction, self._max_yield))
        self._last_at = time.monotonic()


__all__ = [
    "CHECKPOINT_PASSIVE",
    "CHECKPOINT_TRUNCATE",
    "PeriodicCheckpointer",
    "checkpoint_wal",
]
