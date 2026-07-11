"""Persistent grab correlation with external download clients (ADR-07).

The audit's P1-20/P1-21: download state for client-backed sources (usenet
today, torrent in Phase 6) lived purely in process memory. After a SoulSync
restart the external client (SABnzbd/NZBGet) kept downloading, but the
``download_id``, job context and post-processing correlation were gone.

ADR-07's decision: the external client stays the live truth for
progress/percent — SoulSync persists only the BUSINESS correlation:

- which SoulSync download corresponds to which external job (``external_job_id``),
- the business status (``submitting → queued → downloading →
  completed | failed | cancel_pending → cancelled``),
- the last observed client state and the final output path,
- adoption metadata for jobs re-attached after a restart.

Deliberately NOT here: progress percent, speeds, ETA — polling writes a row
only when the business status changes, never per poll tick (that would be
"das aktuelle Problem, nur formalisiert statt gelöst").

Status semantics: ``completed``/``cancelled``/``failed`` are terminal. A
terminal status is never overwritten by a different status — a late poll
thread observing a removed job cannot flip a user's ``cancelled`` into
``failed`` (P1-21 race).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("acquisition.grabs")

STATUS_SUBMITTING = "submitting"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCEL_PENDING = "cancel_pending"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
# Grabs a restart must reconcile: work the client may still be doing.
OPEN_STATUSES = (STATUS_SUBMITTING, STATUS_QUEUED, STATUS_DOWNLOADING,
                 STATUS_CANCEL_PENDING)

ACQUISITION_GRABS_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_grabs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    download_id TEXT NOT NULL UNIQUE,     -- SoulSync-side correlation id
    acquisition_request_id TEXT,
    release_candidate_id TEXT,
    decision_run_id TEXT,
    source TEXT NOT NULL,                 -- 'usenet'|'torrent'|...
    client TEXT,                          -- adapter identity ('SABnzbdAdapter'|...)
    external_job_id TEXT,                 -- the client's job id (nzo_id, ...)
    category TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'submitting',
    last_client_state TEXT,               -- last observed adapter state
    output_path TEXT,                     -- resolved completed path
    error TEXT,
    context_json TEXT NOT NULL DEFAULT '{}',  -- flow / entity / profile context
    adopted INTEGER NOT NULL DEFAULT 0,   -- re-attached after a restart
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (acquisition_request_id) REFERENCES acquisition_requests(id) ON DELETE SET NULL,
    FOREIGN KEY (release_candidate_id) REFERENCES release_candidates(id) ON DELETE SET NULL,
    FOREIGN KEY (decision_run_id) REFERENCES candidate_decision_runs(id) ON DELETE SET NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_grabs_source_status "
    "ON acquisition_grabs(source, status)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_grabs_job "
    "ON acquisition_grabs(external_job_id)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_grabs_request "
    "ON acquisition_grabs(acquisition_request_id, status)",
)

_ADDED_COLUMNS = (
    ("acquisition_request_id",
     "ALTER TABLE acquisition_grabs ADD COLUMN acquisition_request_id TEXT "
     "REFERENCES acquisition_requests(id) ON DELETE SET NULL"),
    ("release_candidate_id",
     "ALTER TABLE acquisition_grabs ADD COLUMN release_candidate_id TEXT "
     "REFERENCES release_candidates(id) ON DELETE SET NULL"),
    ("decision_run_id",
     "ALTER TABLE acquisition_grabs ADD COLUMN decision_run_id TEXT "
     "REFERENCES candidate_decision_runs(id) ON DELETE SET NULL"),
)


def ensure_acquisition_grabs_schema(conn: Any) -> None:
    """Create the grabs table + indexes. Idempotent; caller commits."""
    cursor = conn.cursor()
    cursor.execute(ACQUISITION_GRABS_DDL)
    columns = {
        row[1] for row in cursor.execute(
            "PRAGMA table_info(acquisition_grabs)").fetchall()
    }
    for column, alter_sql in _ADDED_COLUMNS:
        if column not in columns:
            cursor.execute(alter_sql)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)


def record_grab(conn: Any, download_id: str, source: str, *,
                client: Optional[str] = None, title: Optional[str] = None,
                category: Optional[str] = None,
                context: Optional[Dict[str, Any]] = None,
                acquisition_request_id: Optional[str] = None,
                release_candidate_id: Optional[str] = None,
                decision_run_id: Optional[str] = None,
                status: str = STATUS_SUBMITTING) -> None:
    """Insert the correlation row for a new grab. Does not commit.

    Idempotent per ``download_id`` (re-recording an existing grab is a no-op
    — the transition path owns updates).
    """
    conn.execute(
        """INSERT OR IGNORE INTO acquisition_grabs(
               download_id, acquisition_request_id, release_candidate_id,
               decision_run_id, source, client, title, category, status,
               context_json)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            download_id, acquisition_request_id, release_candidate_id,
            decision_run_id, source, client, title, category, status,
            json.dumps(context or {}),
        ))


def update_grab(conn: Any, download_id: str, *,
                status: Optional[str] = None,
                external_job_id: Optional[str] = None,
                client: Optional[str] = None,
                last_client_state: Optional[str] = None,
                output_path: Optional[str] = None,
                error: Optional[str] = None,
                adopted: Optional[bool] = None) -> bool:
    """Apply a business transition / enrich correlation fields.

    Only provided fields change. A terminal status never changes to a
    DIFFERENT status (see module docstring); non-status fields may still be
    enriched. Returns whether a row was updated. Does not commit.
    """
    sets: List[str] = ["updated_at=CURRENT_TIMESTAMP"]
    args: List[Any] = []
    if status is not None:
        sets.append("status=CASE WHEN status IN ('completed','failed','cancelled') "
                    "AND status<>? THEN status ELSE ? END")
        args.extend([status, status])
    for column, value in (("external_job_id", external_job_id), ("client", client),
                          ("last_client_state", last_client_state),
                          ("output_path", output_path), ("error", error)):
        if value is not None:
            sets.append(f"{column}=?")
            args.append(value)
    if adopted is not None:
        sets.append("adopted=?")
        args.append(1 if adopted else 0)
    args.append(download_id)
    cur = conn.execute(
        f"UPDATE acquisition_grabs SET {', '.join(sets)} WHERE download_id=?",
        args)
    return cur.rowcount > 0


_COLUMNS = ("id", "download_id", "acquisition_request_id",
            "release_candidate_id", "decision_run_id",
            "source", "client", "external_job_id",
            "category", "title", "status", "last_client_state", "output_path",
            "error", "context_json", "adopted", "created_at", "updated_at")


def get_grab(conn: Any, download_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_grabs WHERE download_id=?",
        (download_id,)).fetchone()
    if row is None:
        return None
    grab = dict(zip(_COLUMNS, row, strict=True))
    try:
        grab["context"] = json.loads(grab.get("context_json") or "{}")
    except (TypeError, ValueError):
        grab["context"] = {}
    return grab


def open_grabs(conn: Any, source: str) -> List[Dict[str, Any]]:
    """Non-terminal grabs of one source — what a restart has to reconcile."""
    marks = ",".join("?" for _ in OPEN_STATUSES)
    rows = conn.execute(
        f"""SELECT download_id FROM acquisition_grabs
             WHERE source=? AND status IN ({marks})
             ORDER BY id""",
        (source, *OPEN_STATUSES)).fetchall()
    return [g for g in (get_grab(conn, r[0]) for r in rows) if g]


__all__ = [
    "ACQUISITION_GRABS_DDL",
    "OPEN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_CANCEL_PENDING",
    "STATUS_COMPLETED",
    "STATUS_DOWNLOADING",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_SUBMITTING",
    "TERMINAL_STATUSES",
    "ensure_acquisition_grabs_schema",
    "get_grab",
    "open_grabs",
    "record_grab",
    "update_grab",
]
