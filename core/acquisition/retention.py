"""How long the acquisition journal keeps what is over and done.

Third #1062 review G04-4. Every admin download writes a request, its
candidates, a decision run with reasons, a grab and several history rows,
and nothing ever deleted them; 3.6.1 kept only the download history. On a
large wishlist that is tens of thousands of rows a month in a database that
is already big, read by the reconciler, the history feed and every backup.

Kept, always: anything still open (a request not settled, a grab still
downloading, an import still running), the blocklist (its own table), and the
``recovered_to_staging`` events quarantine recovery checks before it moves a
file again. Pruned, after ``acquisition.history_retention_days`` (90 by
default, 0 turns it off): expired candidates, settled requests with their
decisions and imports, finished grabs, and history rows that are not part of
anything kept.

``acquisition_history`` is append-only by trigger. The trigger now lets one
writer through while ``acquisition_history_prune_gate`` holds a row; the
gate is filled and emptied inside the same transaction as the delete, under
sqlite's write lock, so no other writer ever sees it open.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("acquisition.retention")

DEFAULT_RETENTION_DAYS = 90
SETTLED_REQUESTS = ("completed", "failed", "cancelled", "no_candidate")
SETTLED_GRABS = ("completed", "failed", "cancelled")
SETTLED_IMPORTS = ("completed", "failed")
# what something else reads back later, whatever its age
_KEEP_EVENTS = ("recovered_to_staging",)

_GATED_TRIGGER = """CREATE TRIGGER trg_acquisition_history_no_delete
       BEFORE DELETE ON acquisition_history
       WHEN NOT EXISTS (SELECT 1 FROM acquisition_history_prune_gate)
       BEGIN SELECT RAISE(ABORT, 'acquisition_history is append-only'); END"""


def retention_days(config_manager: Any = None) -> int:
    try:
        if config_manager is None:
            from core.settings import config_manager as configured
            config_manager = configured
        value = int(config_manager.get("acquisition.history_retention_days",
                                       DEFAULT_RETENTION_DAYS))
    except Exception:  # noqa: BLE001
        return DEFAULT_RETENTION_DAYS
    return max(value, 0)


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _gate_history_trigger(conn) -> None:
    """the append-only trigger, with a gate the prune opens (once per db)"""
    conn.execute("CREATE TABLE IF NOT EXISTS acquisition_history_prune_gate (open INTEGER)")
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                       "AND name='trg_acquisition_history_no_delete'").fetchone()
    if row is not None and "acquisition_history_prune_gate" in str(row[0] or ""):
        return
    conn.execute("DROP TRIGGER IF EXISTS trg_acquisition_history_no_delete")
    conn.execute(_GATED_TRIGGER)


def _marks(values) -> str:
    return ",".join("?" for _ in values)


HISTORY_BATCH = 5000


def _prune_history(conn, cutoff: str, has_grabs: bool) -> int:
    """old history rows of nothing kept, a batch per transaction. review 4
    R4-C-7: the first sweep after years of journal deleted it all in one
    transaction, holding the write lock while other writers waited"""
    kept_request = "request_id IN (SELECT id FROM acquisition_requests)"
    kept_grab = "download_id IN (SELECT download_id FROM acquisition_grabs)" if has_grabs else "0"
    removed = 0
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO acquisition_history_prune_gate(open) VALUES (1)")
            cur = conn.execute(
                f"""DELETE FROM acquisition_history WHERE rowid IN (
                        SELECT rowid FROM acquisition_history
                         WHERE created_at < ?
                           AND event_type NOT IN ({_marks(_KEEP_EVENTS)})
                           AND NOT (request_id IS NOT NULL AND {kept_request})
                           AND NOT (download_id IS NOT NULL AND {kept_grab})
                         LIMIT ?)""",
                (cutoff, *_KEEP_EVENTS, HISTORY_BATCH))
            batch = max(cur.rowcount, 0)
            conn.execute("DELETE FROM acquisition_history_prune_gate")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        removed += batch
        if batch < HISTORY_BATCH:
            return removed


def prune_acquisition_journal(conn, *, days: int = DEFAULT_RETENTION_DAYS,
                              now: Optional[float] = None) -> Dict[str, int]:
    """Delete what has been settled for longer than ``days``. Requests, grabs
    and decisions in one transaction, then old history in short batches; a
    failure rolls back the open one and the next run tries again."""
    stats = {"candidates": 0, "requests": 0, "grabs": 0, "imports": 0,
             "decision_runs": 0, "history": 0}
    if days <= 0:
        return stats
    tables = _tables(conn)
    if "acquisition_requests" not in tables:
        return stats
    clock = time.time() if now is None else float(now)
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(clock - days * 86400))
    has = tables.__contains__
    try:
        conn.execute("BEGIN IMMEDIATE")
        # requests settled before the cutoff, with nothing of theirs still open
        open_import = (f"EXISTS (SELECT 1 FROM acquisition_imports i WHERE i.request_id=r.id "
                       f"AND i.status NOT IN ({_marks(SETTLED_IMPORTS)}))"
                       if has("acquisition_imports") else "0")
        # review 4 R4-C-3: a grab settled after the cutoff is kept below,
        # so its request stays too. deleting it cascaded away the kept
        # grab's decision run, candidate and import
        open_grab = (f"EXISTS (SELECT 1 FROM acquisition_grabs g WHERE g.acquisition_request_id=r.id "
                     f"AND (g.status NOT IN ({_marks(SETTLED_GRABS)}) "
                     f"OR COALESCE(g.updated_at, g.created_at) >= ?))"
                     if has("acquisition_grabs") else "0")
        params = [*SETTLED_REQUESTS, cutoff]
        if has("acquisition_imports"):
            params += SETTLED_IMPORTS
        if has("acquisition_grabs"):
            params += [*SETTLED_GRABS, cutoff]
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _acq_prune_requests (id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _acq_prune_requests")
        conn.execute(
            f"""INSERT INTO _acq_prune_requests(id)
                SELECT r.id FROM acquisition_requests r
                 WHERE r.status IN ({_marks(SETTLED_REQUESTS)})
                   AND COALESCE(r.completed_at, r.updated_at, r.created_at) < ?
                   AND NOT {open_import} AND NOT {open_grab}""", params)

        # finished grabs before the cutoff, whose request (if any) goes too or is gone
        if has("acquisition_grabs"):
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _acq_prune_grabs (download_id TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _acq_prune_grabs")
            open_grab_import = (f"EXISTS (SELECT 1 FROM acquisition_imports i WHERE i.download_id=g.download_id "
                                f"AND i.status NOT IN ({_marks(SETTLED_IMPORTS)}))"
                                if has("acquisition_imports") else "0")
            gparams = [*SETTLED_GRABS, cutoff]
            if has("acquisition_imports"):
                gparams += SETTLED_IMPORTS
            conn.execute(
                f"""INSERT INTO _acq_prune_grabs(download_id)
                    SELECT g.download_id FROM acquisition_grabs g
                     WHERE g.status IN ({_marks(SETTLED_GRABS)})
                       AND COALESCE(g.updated_at, g.created_at) < ?
                       AND NOT {open_grab_import}
                       AND (g.acquisition_request_id IS NULL
                            OR g.acquisition_request_id IN (SELECT id FROM _acq_prune_requests)
                            OR g.acquisition_request_id NOT IN (SELECT id FROM acquisition_requests))""",
                gparams)

        if has("acquisition_imports"):
            cur = conn.execute(
                "DELETE FROM acquisition_imports WHERE request_id IN (SELECT id FROM _acq_prune_requests)"
                + (" OR download_id IN (SELECT download_id FROM _acq_prune_grabs)"
                   if has("acquisition_grabs") else ""))
            stats["imports"] = max(cur.rowcount, 0)
        if has("acquisition_grabs"):
            cur = conn.execute("DELETE FROM acquisition_grabs WHERE download_id IN "
                               "(SELECT download_id FROM _acq_prune_grabs)")
            stats["grabs"] = max(cur.rowcount, 0)

        # decisions go with their request, or with a candidate that is going
        if has("release_candidates"):
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _acq_prune_candidates (id TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _acq_prune_candidates")
            conn.execute(
                """INSERT OR IGNORE INTO _acq_prune_candidates(id)
                   SELECT id FROM release_candidates
                    WHERE request_id IN (SELECT id FROM _acq_prune_requests) OR expires_at <= ?""",
                (clock,))
        if has("candidate_decision_runs"):
            run_where = "request_id IN (SELECT id FROM _acq_prune_requests)"
            if has("release_candidates"):
                run_where += " OR candidate_id IN (SELECT id FROM _acq_prune_candidates)"
            if has("acquisition_grabs"):
                # a kept grab still names its run
                run_where = (f"({run_where}) AND id NOT IN (SELECT decision_run_id FROM acquisition_grabs "
                             f"WHERE decision_run_id IS NOT NULL)")
            if has("candidate_decisions"):
                conn.execute(f"DELETE FROM candidate_decisions WHERE run_id IN "
                             f"(SELECT id FROM candidate_decision_runs WHERE {run_where})")
            cur = conn.execute(f"DELETE FROM candidate_decision_runs WHERE {run_where}")
            stats["decision_runs"] = max(cur.rowcount, 0)
        if has("release_candidates"):
            keep = ""
            if has("acquisition_grabs"):
                keep += (" AND id NOT IN (SELECT release_candidate_id FROM acquisition_grabs "
                         "WHERE release_candidate_id IS NOT NULL)")
            if has("candidate_decision_runs"):
                keep += " AND id NOT IN (SELECT candidate_id FROM candidate_decision_runs)"
            cur = conn.execute(f"DELETE FROM release_candidates WHERE id IN "
                               f"(SELECT id FROM _acq_prune_candidates){keep}")
            stats["candidates"] = max(cur.rowcount, 0)

        cur = conn.execute("DELETE FROM acquisition_requests WHERE id IN (SELECT id FROM _acq_prune_requests)")
        stats["requests"] = max(cur.rowcount, 0)

        if has("acquisition_history"):
            _gate_history_trigger(conn)
        conn.commit()
        if has("acquisition_history"):
            stats["history"] = _prune_history(conn, cutoff, has("acquisition_grabs"))
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001 - the error above is the one that matters
            logger.debug("rollback after a failed carry: %s", rollback_exc)
        raise
    if any(stats.values()):
        logger.info("Acquisition journal pruned (older than %s days): %s", days, stats)
    return stats


__all__ = ["DEFAULT_RETENTION_DAYS", "prune_acquisition_journal", "retention_days"]
