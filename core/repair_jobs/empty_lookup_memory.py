"""Remember that a lookup came back empty, so a job does not ask the same
server the same question on every run.

Jobs that search an outside service for something a library item is missing
(cover art, a BPM, a release year) repeat the search for every item the service
had no answer for, each run, forever. This is a small per-job memory of those
empty answers: ``(job, key, signature, checked_at)``. An answer is trusted for
``max_age_days`` and only while the ``signature`` (what was asked: names, ids,
source order) is unchanged, so editing the album or the source settings asks
again straight away.

Only EMPTY answers are remembered. A hit becomes a finding, and findings already
have their own recurrence rules. A lookup that failed (a timeout, a rate limit, a
source that is not configured) is not an empty answer and must not be passed to
``remember``. The metadata clients mostly turn a failure into ``None`` or ``[]``
instead of raising, so ``core.lookup_failures`` counts them where they happen
and the job asks it whether a lookup could be answered.

Its own small table, created on first use, so it needs nothing from the music
database's migrations. Any database problem disables the memory for the run
instead of failing the scan, the same shape as the Corrupt File Detector's
result memory.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("repair_job.empty_lookup_memory")

_DAY = 86400.0


class EmptyLookupMemory:
    def __init__(self, conn, job_id: str, max_age_days: float):
        self._conn = conn
        self._job_id = job_id
        self._max_age = max(0.0, float(max_age_days)) * _DAY
        self._rows: Dict[str, Tuple[str, float]] = {}
        # Written in one short transaction per commit(), so a write transaction
        # is never held open across slow network calls.
        self._remembered = []
        self._forgotten = []

    @classmethod
    def open(cls, db, job_id: str, max_age_days: float) -> Optional['EmptyLookupMemory']:
        try:
            conn = db._get_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repair_empty_lookups (
                    job_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    checked_at REAL NOT NULL,
                    PRIMARY KEY (job_id, key)
                )
            """)
            conn.commit()
            memory = cls(conn, job_id, max_age_days)
            memory._rows = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT key, signature, checked_at FROM repair_empty_lookups "
                "WHERE job_id = ?", (job_id,))}
            return memory
        except Exception as e:
            logger.debug("[%s] no empty-lookup memory this run: %s", job_id, e)
            return None

    def recently_empty(self, key: str, signature: str) -> bool:
        """True when this exact question already came back empty within the
        window, so asking again would only repeat the answer."""
        row = self._rows.get(key)
        if not row or row[0] != signature:
            return False
        return (time.time() - row[1]) < self._max_age

    def remember(self, key: str, signature: str) -> None:
        now = time.time()
        self._rows[key] = (signature, now)
        self._remembered.append((self._job_id, key, signature, now))

    def forget(self, key: str) -> None:
        if self._rows.pop(key, None) is not None:
            self._forgotten.append((self._job_id, key))

    def prune(self, keep: Iterable[str]) -> None:
        """Drop memory of items that no longer exist. Call only after a scan
        that reached the end: a stopped scan has not seen every item."""
        keep = set(keep)
        for key in [k for k in self._rows if k not in keep]:
            self.forget(key)

    def commit(self) -> None:
        if not (self._remembered or self._forgotten):
            return
        try:
            with self._conn:
                if self._remembered:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO repair_empty_lookups "
                        "(job_id, key, signature, checked_at) VALUES (?, ?, ?, ?)",
                        self._remembered)
                if self._forgotten:
                    self._conn.executemany(
                        "DELETE FROM repair_empty_lookups WHERE job_id = ? AND key = ?",
                        self._forgotten)
        except Exception as e:
            logger.debug("[%s] could not save empty-lookup memory: %s", self._job_id, e)
        self._remembered, self._forgotten = [], []

    def close(self) -> None:
        self.commit()
        try:
            self._conn.close()
        except Exception as e:
            logger.debug("[%s] error closing empty-lookup memory: %s", self._job_id, e)
