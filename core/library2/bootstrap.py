"""Automatic, idempotent initial-import bootstrap for existing installations.

Problem (docs/library-v2.md §78, docs/library-v2-tool-integration-audit-2026-07-18.md
§7 item 7): on an existing installation, the legacy library only ever reaches
``lib2_*`` if someone manually opens the Library v2 UI and clicks Import. The
native repair/quality/wanted jobs (P3) already assume ``lib2_*`` is populated,
so an upgraded installation that restarts gets a schema with no rows and no
native job coverage for its existing library
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
import uuid
import json
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
    heartbeat_at TEXT,
    owner_token TEXT,
    source_watermark TEXT
)
"""


def ensure_bootstrap_schema(cursor) -> None:
    """Create the single-row bootstrap-status table. Idempotent."""
    cursor.execute(LIB2_BOOTSTRAP_STATE_DDL)
    columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(lib2_bootstrap_state)")
    }
    if "owner_token" not in columns:
        cursor.execute("ALTER TABLE lib2_bootstrap_state ADD COLUMN owner_token TEXT")
    if "source_watermark" not in columns:
        cursor.execute("ALTER TABLE lib2_bootstrap_state ADD COLUMN source_watermark TEXT")
    cursor.execute(
        "INSERT OR IGNORE INTO lib2_bootstrap_state (id, status) VALUES (1, 'pending')"
    )


def source_watermark(database: Any) -> str:
    """Return a stable snapshot of the legacy source population.

    The bootstrap must run again when a fresh install receives its first media
    server scan after startup. Counts plus max ids are intentionally cheap and
    sufficient to distinguish that empty -> populated transition.
    """
    conn = database._get_connection()
    try:
        snapshot = {}
        for table in ("artists", "albums", "tracks"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                snapshot[table] = [0, ""]
                continue
            row = conn.execute(
                f"SELECT COUNT(*) AS n, MAX(id) AS max_id FROM {table}"
            ).fetchone()
            snapshot[table] = [int(row["n"] or 0), str(row["max_id"] or "")]
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    finally:
        conn.close()


def source_row_count(watermark: str) -> int:
    try:
        data = json.loads(watermark or "{}")
        return sum(int((data.get(table) or [0])[0]) for table in ("artists", "albums", "tracks"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0


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
            "last_error, started_at, finished_at, heartbeat_at, source_watermark "
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
            "source_watermark": None,
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
        "source_watermark": row["source_watermark"],
    }


def try_claim(database: Any, *, stale_after_seconds: int = STALE_AFTER_SECONDS) -> Optional[str]:
    """Try to acquire the exclusive right to run ``import_legacy_library`` now.

    Returns an opaque owner token iff this call won the race, otherwise None.
    A currently-``running`` claim
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
            return None

        now = _now_iso()
        owner_token = uuid.uuid4().hex
        if old_heartbeat is None:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=?, "
                "owner_token=? "
                "WHERE id=1 AND status=? AND heartbeat_at IS NULL",
                (attempts + 1, now, now, owner_token, old_status),
            )
        else:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=?, "
                "owner_token=? "
                "WHERE id=1 AND status=? AND heartbeat_at=?",
                (attempts + 1, now, now, owner_token, old_status, old_heartbeat),
            )
        won = cursor.rowcount > 0
        conn.commit()
        return owner_token if won else None
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap claim contended: %s", exc)
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.debug("lib2 bootstrap claim rollback skipped: %s", rollback_exc)
        return None
    finally:
        conn.close()


def heartbeat(database: Any, owner_token: str, *, stage: Optional[str] = None,
              current: int = 0, total: int = 0, connection=None) -> bool:
    """Extend a held claim's lease and record progress. Fenced by owner."""
    conn = connection or database._get_connection()
    owns_connection = connection is None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at=?, stage=?, "
            "current_count=?, total_count=? WHERE id=1 AND status='running' "
            "AND owner_token=?",
            (_now_iso(), stage, current, total, owner_token),
        )
        updated = cursor.rowcount > 0
        if owns_connection:
            conn.commit()
        return updated
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap heartbeat skipped: %s", exc)
        return False
    finally:
        if owns_connection:
            conn.close()


def mark_done(database: Any, owner_token: str, *, watermark: Optional[str] = None) -> bool:
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='done', finished_at=?, "
            "heartbeat_at=?, last_error=NULL, source_watermark=?, owner_token=NULL "
            "WHERE id=1 AND status='running' AND owner_token=?",
            (now, now, watermark, owner_token),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def mark_failed(database: Any, owner_token: str, error: str) -> bool:
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='failed', finished_at=?, "
            "heartbeat_at=?, last_error=?, owner_token=NULL "
            "WHERE id=1 AND status='running' AND owner_token=?",
            (now, now, str(error)[:2000], owner_token),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def mark_waiting_for_source(database: Any, owner_token: str, *, watermark: str) -> bool:
    """Release an empty bootstrap without declaring it permanently complete."""
    conn = database._get_connection()
    try:
        now = _now_iso()
        cursor = conn.execute(
            "UPDATE lib2_bootstrap_state SET status='waiting_for_source', "
            "finished_at=?, heartbeat_at=?, source_watermark=?, owner_token=NULL, "
            "last_error=NULL WHERE id=1 AND status='running' AND owner_token=?",
            (now, now, watermark, owner_token),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def run_bootstrap_if_needed(database: Any, config_get, *,
                            profile_id: int = ADMIN_PROFILE_ID) -> Dict[str, Any]:
    """Run the legacy → v2 import exactly once, only if it's actually needed.

    Always returns a dict: either ``{"skipped": reason}`` (``"already_done"``,
    ``"already_running"``) or the outcome of an actual
    run (``{"success": True, "stats": {...}}`` / ``{"success": False,
    "error": str}``). Safe to call repeatedly from a periodic autostart
    loop — cheap no-ops once the import has completed.
    """
    from core.library2.feature import library_v2_enabled

    library_v2_enabled(config_get=config_get)

    current_watermark = source_watermark(database)
    state = get_state(database)
    if state.get("status") in {"done", "waiting_for_source"}:
        if state.get("source_watermark") == current_watermark:
            reason = "already_done" if state.get("status") == "done" else "empty_source"
            return {"skipped": reason}

    owner_token = try_claim(database)
    if not owner_token:
        return {"skipped": "already_running"}

    logger.info("Library v2 bootstrap import starting")
    last_beat = {"t": 0.0}

    def _progress(stage, current, total, *, connection=None):
        now = time.monotonic()
        if current != total and now - last_beat["t"] < _HEARTBEAT_THROTTLE_SECONDS:
            return
        last_beat["t"] = now
        heartbeat(
            database, owner_token, stage=stage, current=current, total=total,
            connection=connection,
        )

    _progress.lib2_connection_aware = True

    try:
        stats = _import_legacy_library(database, profile_id=profile_id, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        logger.error("Library v2 bootstrap import failed: %s", exc, exc_info=True)
        mark_failed(database, owner_token, str(exc))
        return {"success": False, "error": str(exc)}

    final_watermark = source_watermark(database)
    if source_row_count(final_watermark) == 0:
        mark_waiting_for_source(database, owner_token, watermark=final_watermark)
        logger.info("Library v2 bootstrap is waiting for the first legacy library scan")
        return {"success": True, "stats": stats, "waiting_for_source": True}
    if not mark_done(database, owner_token, watermark=final_watermark):
        return {"success": False, "error": "Bootstrap lease was lost before completion"}
    logger.info("Library v2 bootstrap import completed: %s", stats)
    return {"success": True, "stats": stats}


__all__ = [
    "ensure_bootstrap_schema",
    "get_state",
    "try_claim",
    "heartbeat",
    "mark_done",
    "mark_failed",
    "mark_waiting_for_source",
    "run_bootstrap_if_needed",
    "source_watermark",
    "source_row_count",
    "STALE_AFTER_SECONDS",
]
