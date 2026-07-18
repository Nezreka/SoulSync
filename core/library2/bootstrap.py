"""Automatic, idempotent initial-import bootstrap for existing installations.

Problem (docs/library-v2.md §78, docs/library-v2-tool-integration-audit-2026-07-18.md
§7 item 7): on an existing installation, the legacy library only ever reaches
``lib2_*`` if someone manually opens the Library v2 UI and clicks Import. The
native repair/quality/wanted jobs (P3) already assume ``lib2_*`` is populated,
so an admin who merely flips ``features.library_v2`` on and restarts gets a
schema with no rows and no native job coverage for their existing library
until they discover the manual button.

This module makes the first import happen on its own, on server start, without
depending on the UI. Three properties the docs call for:

- **Persisted status** — survives a restart (unlike the in-memory
  ``_import_state`` the manual ``/api/library/v2/import`` endpoint already
  uses), stored in a dedicated single-row ``lib2_bootstrap_state`` table.
- **Lock against double-starts** — ``try_claim()`` is an optimistic
  compare-and-swap on ``(status, heartbeat_at)`` so two processes/threads
  racing at boot (or a dev-reload overlap) can't both run
  ``import_legacy_library()`` at once against the same DB. A ``running``
  claim with a stale heartbeat (no process actually alive, e.g. after a
  crash) is reclaimable, which also gives us crash "resumability" — cheap
  here because the importer is upsert-by-``legacy_*_id`` and re-runnable
  (see its docstring), so retrying from scratch is safe, just not free.
- **Error status + retry** — a failed run is recorded with its error and
  stays claimable, so the next server start (or an explicit retry) tries
  again instead of getting stuck.

``try_claim`` is intentionally reusable by *any* caller of
``import_legacy_library`` (not just this module's own ``run_bootstrap_if_needed``
loop) — the manual "Reimport"/"Reset & Reimport" admin action wraps its own
call with the same claim/mark_done/mark_failed primitives, so a manual run and
the automatic bootstrap can never race each other's writes. A completed
("done") state is claimable again for that reason: "done" only means the
automatic bootstrap has nothing left to do, never "permanently locked".
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.library2 import ADMIN_PROFILE_ID
from core.library2.importer import import_legacy_library as _import_legacy_library
from utils.logging_config import get_logger

logger = get_logger("library2.bootstrap")

STALE_AFTER_SECONDS = 600  # a "running" claim with no heartbeat in 10min is dead
_HEARTBEAT_THROTTLE_SECONDS = 5

LIB2_BOOTSTRAP_STATE_DDL = """
CREATE TABLE IF NOT EXISTS lib2_bootstrap_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    stage TEXT,
    current_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT
)
"""


def ensure_bootstrap_schema(cursor) -> None:
    """Create the single-row bootstrap-status table. Idempotent."""
    cursor.execute(LIB2_BOOTSTRAP_STATE_DDL)
    cursor.execute(
        "INSERT OR IGNORE INTO lib2_bootstrap_state (id, status) VALUES (1, 'pending')"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(iso_value: Optional[str], stale_after_seconds: int) -> bool:
    if not iso_value:
        return True
    try:
        ts = datetime.fromisoformat(iso_value)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age >= stale_after_seconds


def get_state(database: Any) -> Dict[str, Any]:
    """Read the persisted bootstrap status. Safe to call before any claim."""
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        ensure_bootstrap_schema(cursor)
        conn.commit()
        cursor.execute(
            "SELECT status, attempts, stage, current_count, total_count, "
            "last_error, started_at, finished_at, heartbeat_at "
            "FROM lib2_bootstrap_state WHERE id = 1"
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if row is None:
        return {
            "status": "pending", "attempts": 0, "stage": None,
            "current": 0, "total": 0, "last_error": None,
            "started_at": None, "finished_at": None, "heartbeat_at": None,
        }
    return {
        "status": row["status"],
        "attempts": row["attempts"],
        "stage": row["stage"],
        "current": row["current_count"],
        "total": row["total_count"],
        "last_error": row["last_error"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "heartbeat_at": row["heartbeat_at"],
    }


def try_claim(database: Any, *, stale_after_seconds: int = STALE_AFTER_SECONDS) -> bool:
    """Try to acquire the exclusive right to run ``import_legacy_library`` now.

    Returns True iff this call won the race. A currently-``running`` claim
    with a fresh heartbeat refuses every other claimant; everything else
    (``pending``, ``failed``, ``done``, or a stale ``running``) is claimable.
    The swap is a compare-and-swap on the exact ``(status, heartbeat_at)``
    pair just read, so two concurrent claimants can't both "win" a stale
    lock — only whichever commits first actually changes those columns.
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        ensure_bootstrap_schema(cursor)
        cursor.execute(
            "SELECT status, heartbeat_at, attempts FROM lib2_bootstrap_state WHERE id = 1"
        )
        row = cursor.fetchone()
        old_status = row["status"]
        old_heartbeat = row["heartbeat_at"]
        attempts = int(row["attempts"] or 0)

        if old_status == "running" and not _is_stale(old_heartbeat, stale_after_seconds):
            conn.commit()
            return False

        now = _now_iso()
        if old_heartbeat is None:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=? "
                "WHERE id=1 AND status=? AND heartbeat_at IS NULL",
                (attempts + 1, now, now, old_status),
            )
        else:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=? "
                "WHERE id=1 AND status=? AND heartbeat_at=?",
                (attempts + 1, now, now, old_status, old_heartbeat),
            )
        won = cursor.rowcount > 0
        conn.commit()
        return won
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap claim contended: %s", exc)
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.debug("lib2 bootstrap claim rollback skipped: %s", rollback_exc)
        return False
    finally:
        conn.close()


def heartbeat(database: Any, *, stage: Optional[str] = None, current: int = 0,
              total: int = 0) -> None:
    """Extend a held claim's lease and record progress. Best-effort."""
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at=?, stage=?, "
            "current_count=?, total_count=? WHERE id=1",
            (_now_iso(), stage, current, total),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap heartbeat skipped: %s", exc)
    finally:
        conn.close()


def mark_done(database: Any) -> None:
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='done', finished_at=?, "
            "heartbeat_at=?, last_error=NULL WHERE id=1",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()


def mark_failed(database: Any, error: str) -> None:
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='failed', finished_at=?, "
            "heartbeat_at=?, last_error=? WHERE id=1",
            (now, now, str(error)[:2000]),
        )
        conn.commit()
    finally:
        conn.close()


def run_bootstrap_if_needed(database: Any, config_get, *,
                            profile_id: int = ADMIN_PROFILE_ID) -> Dict[str, Any]:
    """Run the legacy → v2 import exactly once, only if it's actually needed.

    Always returns a dict: either ``{"skipped": reason}`` (``"disabled"``,
    ``"already_done"``, ``"already_running"``) or the outcome of an actual
    run (``{"success": True, "stats": {...}}`` / ``{"success": False,
    "error": str}``). Safe to call repeatedly from a periodic autostart
    loop — cheap no-ops once the feature is off or the import has completed.
    """
    try:
        enabled = config_get("features.library_v2", False) is True
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return {"skipped": "disabled"}

    if get_state(database).get("status") == "done":
        return {"skipped": "already_done"}

    if not try_claim(database):
        return {"skipped": "already_running"}

    logger.info("Library v2 bootstrap import starting")
    last_beat = {"t": 0.0}

    def _progress(stage, current, total):
        now = time.monotonic()
        if current != total and now - last_beat["t"] < _HEARTBEAT_THROTTLE_SECONDS:
            return
        last_beat["t"] = now
        heartbeat(database, stage=stage, current=current, total=total)

    try:
        stats = _import_legacy_library(database, profile_id=profile_id, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        logger.error("Library v2 bootstrap import failed: %s", exc, exc_info=True)
        mark_failed(database, str(exc))
        return {"success": False, "error": str(exc)}

    mark_done(database)
    logger.info("Library v2 bootstrap import completed: %s", stats)
    return {"success": True, "stats": stats}


__all__ = [
    "ensure_bootstrap_schema",
    "get_state",
    "try_claim",
    "heartbeat",
    "mark_done",
    "mark_failed",
    "run_bootstrap_if_needed",
    "STALE_AFTER_SECONDS",
]
