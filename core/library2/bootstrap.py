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
  crash) is reclaimable.
- **Resume instead of restart** — every batch heartbeat also records the
  walk position the importer committed (stage, source rowid, run id). The
  next attempt continues from there rather than re-walking the whole legacy
  library, which on a large collection is the difference between seconds and
  another full migration. The checkpoint is only honoured while the legacy
  source still looks the same (``resume_point_for``); after a scan changed it
  the run starts clean, because the recorded row offsets no longer describe
  the same rows.
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
import threading
import time
import uuid
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from core.library2 import ADMIN_PROFILE_ID
from core.library2.importer import ResumePoint
from core.library2.importer import import_legacy_library as _import_legacy_library
from utils.logging_config import get_logger

logger = get_logger("library2.bootstrap")

STALE_AFTER_SECONDS = 600  # a "running" claim with no heartbeat in 10min is dead
_HEARTBEAT_THROTTLE_SECONDS = 5
# iss29-A08: comfortably inside STALE_AFTER_SECONDS, so several beats have to be
# missed in a row before anything considers the run dead.
KEEPALIVE_INTERVAL_SECONDS = 60

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
    source_watermark TEXT,
    resume_stage TEXT,
    resume_rowid INTEGER,
    resume_run_id TEXT,
    resume_watermark TEXT
)
"""

_ADDED_COLUMNS = (
    ("owner_token", "TEXT"),
    ("source_watermark", "TEXT"),
    ("resume_stage", "TEXT"),
    ("resume_rowid", "INTEGER"),
    ("resume_run_id", "TEXT"),
    ("resume_watermark", "TEXT"),
)


def ensure_bootstrap_schema(cursor) -> None:
    """Create the single-row bootstrap-status table. Idempotent."""
    cursor.execute(LIB2_BOOTSTRAP_STATE_DDL)
    columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(lib2_bootstrap_state)")
    }
    for name, column_type in _ADDED_COLUMNS:
        if name not in columns:
            cursor.execute(
                f"ALTER TABLE lib2_bootstrap_state ADD COLUMN {name} {column_type}"
            )
    # iss29-A05: only INSERT when the row is genuinely absent. `ensure_bootstrap_schema`
    # runs on every `get_state`, i.e. on every `/import/status` poll — once a
    # second per open tab — and an unconditional `INSERT OR IGNORE` takes
    # SQLite's write lock even when it changes nothing, competing with the
    # artwork workers for the one writer this database has. The read is served
    # from the page cache; the write was pure contention.
    if cursor.execute(
        "SELECT 1 FROM lib2_bootstrap_state WHERE id = 1"
    ).fetchone() is None:
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


def _epoch_of(iso_value: Optional[str]) -> float:
    """Unix timestamp of a stored heartbeat; 0.0 when absent or unparsable
    (both mean "older than anything we could be comparing against")."""
    if not iso_value:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso_value)
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


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
            "last_error, started_at, finished_at, heartbeat_at, source_watermark, "
            "resume_stage, resume_rowid, resume_run_id, resume_watermark "
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
            "source_watermark": None, "resume_stage": None, "resume_rowid": 0,
            "resume_run_id": None, "resume_watermark": None,
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
        "resume_stage": row["resume_stage"],
        "resume_rowid": int(row["resume_rowid"] or 0),
        "resume_run_id": row["resume_run_id"],
        "resume_watermark": row["resume_watermark"],
    }


def resume_point_for(state: Dict[str, Any],
                     current_watermark: str) -> Optional[ResumePoint]:
    """Turn a persisted checkpoint into a usable resume point, or ``None``.

    The checkpoint is row offsets into the legacy tables, so it only means
    anything while those tables still hold the same rows. A media-server scan
    that ran while we were down invalidates it and the next attempt starts
    from the beginning — correctness over speed.
    """
    stage = (state.get("resume_stage") or "").strip()
    run_id = (state.get("resume_run_id") or "").strip()
    if not stage or not run_id:
        return None
    if state.get("resume_watermark") != current_watermark:
        return None
    return ResumePoint(stage=stage, rowid=int(state.get("resume_rowid") or 0),
                       run_id=run_id)


def try_claim(database: Any, *, stale_after_seconds: int = STALE_AFTER_SECONDS,
              watermark: Optional[str] = None) -> Optional[str]:
    """Try to acquire the exclusive right to run ``import_legacy_library`` now.

    Returns an opaque owner token iff this call won the race, otherwise None.
    A currently-``running`` claim
    with a fresh heartbeat refuses every other claimant; everything else
    (``pending``, ``failed``, ``done``, or a stale ``running``) is claimable.
    The swap is a compare-and-swap on the exact ``(status, heartbeat_at)``
    pair just read, so two concurrent claimants can't both "win" a stale
    lock — only whichever commits first actually changes those columns.

    ``watermark`` stamps the legacy-source snapshot this run walks, so the
    checkpoints it writes can later be checked against the source they were
    taken from. It is computed here when the caller has not already done so.
    """
    conn = database._get_connection()
    if watermark is None:
        watermark = source_watermark(database)
    try:
        cursor = conn.cursor()
        ensure_bootstrap_schema(cursor)
        cursor.execute(
            "SELECT status, heartbeat_at, attempts, resume_watermark "
            "FROM lib2_bootstrap_state WHERE id = 1"
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

        # iss29-A01: stamping the new watermark WITHOUT clearing the checkpoint
        # re-validates it. `resume_watermark` is the only invalidation signal a
        # checkpoint has (`resume_point_for`), so leaving row offsets taken
        # against a different source snapshot next to a freshly stamped
        # watermark declares them current again — and the caller already read
        # its resume point before this claim, so it never learns of the revival.
        # A later attempt then resumes at `stage='tracks'`, skips the artist and
        # album walks entirely, finds an empty album map, imports nothing, and
        # marks the migration done over an empty library.
        #
        # The invariant is local and holds for every caller: a checkpoint
        # survives a claim only if it was taken against the snapshot this claim
        # is stamping.
        keep_checkpoint = row["resume_watermark"] == watermark
        checkpoint_sql = "" if keep_checkpoint else (
            ", resume_stage=NULL, resume_rowid=NULL, resume_run_id=NULL"
        )
        if old_heartbeat is None:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=?, "
                "owner_token=?, resume_watermark=?" + checkpoint_sql + " "
                "WHERE id=1 AND status=? AND heartbeat_at IS NULL",
                (attempts + 1, now, now, owner_token, watermark, old_status),
            )
        else:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET status='running', attempts=?, "
                "last_error=NULL, started_at=?, finished_at=NULL, heartbeat_at=?, "
                "owner_token=?, resume_watermark=?" + checkpoint_sql + " "
                "WHERE id=1 AND status=? AND heartbeat_at=?",
                (attempts + 1, now, now, owner_token, watermark, old_status,
                 old_heartbeat),
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


def reclaim_abandoned_claim(database: Any, *, process_started_at: float) -> bool:
    """Release a ``running`` claim that a previous process died holding.

    SoulSync serves from a single application process (``workers = 1``), so the
    only writer of this row is us. A claim whose heartbeat has not moved since
    before this process existed therefore cannot belong to a live importer —
    its owner went away with the old process. Marking it failed here (the
    resume checkpoint is kept) lets the migration continue within seconds of a
    restart instead of waiting out the full ``STALE_AFTER_SECONDS`` window.

    Returns True when a claim was actually released.
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        ensure_bootstrap_schema(cursor)
        row = cursor.execute(
            "SELECT status, heartbeat_at FROM lib2_bootstrap_state WHERE id=1"
        ).fetchone()
        if row is None or row["status"] != "running":
            conn.commit()
            return False
        heartbeat_at = row["heartbeat_at"]
        if _epoch_of(heartbeat_at) >= process_started_at:
            # Beaten since we booted: a live import in this process owns it.
            conn.commit()
            return False
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='failed', owner_token=NULL, "
            "last_error=?, finished_at=? "
            "WHERE id=1 AND status='running' AND heartbeat_at IS ?",
            ("Interrupted by a restart", _now_iso(), heartbeat_at),
        )
        released = cursor.rowcount > 0
        conn.commit()
        if released:
            logger.info("Released a Library v2 migration claim left by a previous process")
        return released
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap reclaim skipped: %s", exc)
        return False
    finally:
        conn.close()


def heartbeat(database: Any, owner_token: str, *, stage: Optional[str] = None,
              current: int = 0, total: int = 0, connection=None,
              rowid: Optional[int] = None, run_id: Optional[str] = None) -> bool:
    """Extend a held claim's lease and record progress. Fenced by owner.

    ``rowid``/``run_id`` additionally move the resume checkpoint. They are only
    written together: a beat without a rowid is a stage that has no position in
    a source walk (the post-import precache work), and must leave the import
    checkpoint of the walk that preceded it intact.
    """
    conn = connection or database._get_connection()
    owns_connection = connection is None
    writes_checkpoint = rowid is not None and bool(run_id)
    try:
        cursor = conn.cursor()
        if writes_checkpoint:
            cursor.execute(
                "UPDATE lib2_bootstrap_state SET heartbeat_at=?, stage=?, "
                "current_count=?, total_count=?, resume_stage=?, resume_rowid=?, "
                "resume_run_id=? WHERE id=1 AND status='running' AND owner_token=?",
                (_now_iso(), stage, current, total, stage, int(rowid), run_id,
                 owner_token),
            )
        else:
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


def touch_claim(database: Any, owner_token: str) -> bool:
    """Extend a held claim's lease and nothing else.

    Deliberately narrower than ``heartbeat``: it writes only ``heartbeat_at``,
    leaving stage/progress *and* the resume checkpoint exactly as the last real
    progress report left them. A liveness beat is not progress, and must not be
    able to blank the stage the UI is showing or move a walk position no walk
    reached.
    """
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at=? "
            "WHERE id=1 AND status='running' AND owner_token=?",
            (_now_iso(), owner_token),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    except sqlite3.OperationalError as exc:
        logger.debug("lib2 bootstrap keepalive skipped: %s", exc)
        return False
    finally:
        conn.close()


class _ClaimKeepalive:
    """Keep a held bootstrap claim fresh for as long as the run is alive.

    iss29-A08: the lease used to be extended only by progress callbacks, so
    liveness depended on how chatty the current stage happens to be. Post-import
    precache is the sparse one — ``_resolve_stage`` beats every 20 albums and
    ``precache_tag_cache`` every 50 files — and fifty tag reads across a slow or
    wedged network mount outlast ``STALE_AFTER_SECONDS`` without difficulty. The
    claim then reads as abandoned while the work is still running: another
    process takes it, ``mark_done`` fails with "lease was lost before
    completion", and the whole migration runs again.

    Liveness therefore follows the run itself. The thread is a daemon so it can
    never hold up shutdown, and it stops beating the moment the claim stops
    being ours — a stolen claim must not be silently re-extended underneath its
    new owner.
    """

    def __init__(self, database: Any, owner_token: str, interval: float):
        self._database = database
        self._owner_token = owner_token
        self._interval = max(float(interval), 0.001)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="Lib2BootstrapKeepalive", daemon=True)

    def start(self) -> "_ClaimKeepalive":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not touch_claim(self._database, self._owner_token):
                    return  # no longer running, or no longer ours
            except Exception as exc:  # noqa: BLE001 - liveness must never crash the run
                logger.debug("lib2 bootstrap keepalive beat failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def mark_done(database: Any, owner_token: str, *, watermark: Optional[str] = None) -> bool:
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        now = _now_iso()
        # A completed run has nothing left to resume; clearing the checkpoint
        # keeps a later unrelated failure from continuing this run's walk.
        cursor.execute(
            "UPDATE lib2_bootstrap_state SET status='done', finished_at=?, "
            "heartbeat_at=?, last_error=NULL, source_watermark=?, owner_token=NULL, "
            "resume_stage=NULL, resume_rowid=NULL, resume_run_id=NULL, "
            "resume_watermark=NULL "
            "WHERE id=1 AND status='running' AND owner_token=?",
            (now, now, watermark, owner_token),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def mark_failed(database: Any, owner_token: str, error: str) -> bool:
    """Record a failed run. The resume checkpoint is deliberately kept: the
    retry should continue where this attempt stopped, not start over."""
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


def should_stop_autostart(result: Dict[str, Any]) -> bool:
    """Whether the periodic autostart caller may retire for good.

    Only a genuinely migrated library ends the loop. ``waiting_for_source``
    also reports success — a fresh installation has nothing to import yet —
    and treating that as finished used to retire the thread before the first
    media-server scan ever produced rows, so nothing reached ``lib2_*`` until
    the next restart.
    """
    if result.get("waiting_for_source"):
        return False
    if result.get("skipped") == "already_done":
        return True
    return result.get("success") is True


def run_bootstrap_if_needed(database: Any, config_get, *,
                            profile_id: int = ADMIN_PROFILE_ID,
                            post_import: Optional[Callable[[Any], Any]] = None,
                            stale_after_seconds: int = STALE_AFTER_SECONDS,
                            keepalive_interval_seconds: float = KEEPALIVE_INTERVAL_SECONDS,
                            ) -> Dict[str, Any]:
    """Run the legacy → v2 import exactly once, only if it's actually needed.

    Always returns a dict: either ``{"skipped": reason}`` (``"already_done"``,
    ``"already_running"``) or the outcome of an actual
    run (``{"success": True, "stats": {...}}`` / ``{"success": False,
    "error": str}``). Safe to call repeatedly from a periodic autostart
    loop — cheap no-ops once the import has completed.

    ``post_import`` receives the same progress callback and runs the work that
    finishes a migration but is not catalogue rows (tracklist/tag/artwork
    precache). It runs while the claim is still held, so a restart cannot
    interleave a second migration with it, and a failure inside it never fails
    the migration: the rows are committed and the caches fill in later anyway.
    """
    from core.library2.feature import library_v2_enabled

    # iss29-A07 assumed this was a dead call because the return value is
    # discarded. It is not: `library_v2_enabled` emits the one-time
    # "features.library_v2=false is deprecated and ignored" warning, and this is
    # the only place on the automatic path that reaches it — the flag cannot
    # disable the migration any more, so the RESULT is genuinely irrelevant
    # while the warning is not. Called for the side effect, deliberately.
    library_v2_enabled(config_get=config_get)

    current_watermark = source_watermark(database)
    state = get_state(database)
    if state.get("status") in {"done", "waiting_for_source"}:
        if state.get("source_watermark") == current_watermark:
            reason = "already_done" if state.get("status") == "done" else "empty_source"
            return {"skipped": reason}

    resume = resume_point_for(state, current_watermark)
    owner_token = try_claim(database, stale_after_seconds=stale_after_seconds,
                            watermark=current_watermark)
    if not owner_token:
        return {"skipped": "already_running"}

    if resume:
        logger.info("Library v2 bootstrap import resuming at %s row %s",
                    resume.stage, resume.rowid)
    else:
        logger.info("Library v2 bootstrap import starting")
    last_beat = {"t": 0.0}

    def _progress(stage, current, total, *, connection=None, rowid=None, run_id=None):
        now = time.monotonic()
        # iss29-A02: a beat carrying a rowid is the ONLY kind `heartbeat`
        # persists as a resume checkpoint, and those are exactly the stage
        # openings and the finalize transition — a handful per run, not a
        # stream. Throttling them meant that on any library whose walks finish
        # inside the 5 s window only the first checkpoint was ever written, so a
        # fully successful run ended with `resume_stage='tracks', rowid=0`
        # instead of the finalize marker. That is the value that turns
        # iss29-A01 from wasted work into an empty migrated library.
        writes_checkpoint = rowid is not None and bool(run_id)
        if (not writes_checkpoint and current != total
                and now - last_beat["t"] < _HEARTBEAT_THROTTLE_SECONDS):
            return
        last_beat["t"] = now
        heartbeat(
            database, owner_token, stage=stage, current=current, total=total,
            connection=connection, rowid=rowid, run_id=run_id,
        )

    _progress.lib2_connection_aware = True
    _progress.lib2_resume_aware = True

    # iss29-A08: hold the lease for as long as this run is alive, independent of
    # how often the stage it is in reports progress. Stopped before `mark_done`
    # so no beat can land after the run has settled.
    keepalive = _ClaimKeepalive(database, owner_token,
                                keepalive_interval_seconds).start()
    try:
        try:
            stats = _import_legacy_library(database, profile_id=profile_id,
                                           progress=_progress, resume=resume)
        except Exception as exc:  # noqa: BLE001
            logger.error("Library v2 bootstrap import failed: %s", exc, exc_info=True)
            mark_failed(database, owner_token, str(exc))
            return {"success": False, "error": str(exc)}

        if post_import is not None:
            try:
                post_import(_progress)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Library v2 post-import precache failed: %s", exc)
    finally:
        keepalive.stop()

    # iss29-A04: stamp the watermark the walks actually saw, not the one the
    # source has now. The three walks are keyset scans taken at three different
    # moments and the whole post-import precache runs after them, while
    # auto-import, wishlist downloads and the media-server sync keep writing to
    # the legacy tables. An artist created after the artists walk is never
    # walked — but a watermark taken here would count it, so the next tick
    # answers `already_done`, `should_stop_autostart` returns True, and that
    # artist stays invisible in V2 for the rest of the process lifetime.
    # Stamping the pre-run snapshot leaves the difference visible, so the next
    # tick runs again and picks the row up.
    if source_row_count(current_watermark) == 0:
        mark_waiting_for_source(database, owner_token, watermark=current_watermark)
        logger.info("Library v2 bootstrap is waiting for the first legacy library scan")
        return {"success": True, "stats": stats, "waiting_for_source": True}
    if not mark_done(database, owner_token, watermark=current_watermark):
        return {"success": False, "error": "Bootstrap lease was lost before completion"}
    logger.info("Library v2 bootstrap import completed: %s", stats)
    return {"success": True, "stats": stats}


__all__ = [
    "ensure_bootstrap_schema",
    "get_state",
    "reclaim_abandoned_claim",
    "resume_point_for",
    "should_stop_autostart",
    "try_claim",
    "heartbeat",
    "touch_claim",
    "mark_done",
    "mark_failed",
    "mark_waiting_for_source",
    "run_bootstrap_if_needed",
    "source_watermark",
    "source_row_count",
    "STALE_AFTER_SECONDS",
]
