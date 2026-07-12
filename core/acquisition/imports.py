"""Durable hand-off from a completed grab to edition-aware importing.

The download client owns transfer progress. Once it reports a completed job,
SoulSync stores one immutable correlation to the output path and continues the
request through this import lifecycle. A request is not complete merely because
the client finished downloading its bundle.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from core.acquisition.grabs import (
    STATUS_COMPLETED,
    TERMINAL_STATUSES as TERMINAL_GRAB_STATUSES,
    get_grab,
    update_grab,
)
from core.acquisition.history import record_history_event
from core.acquisition.requests import get_request


IMPORT_ID_PREFIX = "aim1-"
IMPORT_STATUSES = frozenset({
    "pending",
    "matching",
    "needs_review",
    "importing",
    "completed",
    "failed",
})
OPEN_IMPORT_STATUSES = (
    "pending", "matching", "needs_review", "importing",
)

ACQUISITION_IMPORTS_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_imports (
    id TEXT PRIMARY KEY,
    download_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    candidate_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    output_path TEXT NOT NULL,
    expected_scope TEXT NOT NULL,
    expected_entity_id INTEGER NOT NULL,
    inventory_json TEXT NOT NULL DEFAULT '[]',
    matches_json TEXT NOT NULL DEFAULT '[]',
    rejections_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (download_id) REFERENCES acquisition_grabs(download_id) ON DELETE CASCADE,
    FOREIGN KEY (request_id) REFERENCES acquisition_requests(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES release_candidates(id) ON DELETE SET NULL,
    CHECK(status IN ('pending','matching','needs_review','importing','completed','failed')),
    CHECK(expected_entity_id > 0)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_acquisition_imports_status "
    "ON acquisition_imports(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_acquisition_imports_request "
    "ON acquisition_imports(request_id, created_at)",
)

_COLUMNS = (
    "id", "download_id", "request_id", "candidate_id", "status",
    "output_path", "expected_scope", "expected_entity_id",
    "inventory_json", "matches_json", "rejections_json", "result_json",
    "error", "created_at", "updated_at", "completed_at",
)


@dataclass(frozen=True)
class AcquisitionImport:
    id: str
    download_id: str
    request_id: str
    candidate_id: Optional[str]
    status: str
    output_path: str
    expected_scope: str
    expected_entity_id: int
    inventory: Tuple[Dict[str, Any], ...]
    matches: Tuple[Dict[str, Any], ...]
    rejections: Tuple[Dict[str, Any], ...]
    result: Dict[str, Any]
    error: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "download_id": self.download_id,
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "expected_scope": self.expected_scope,
            "expected_entity_id": self.expected_entity_id,
            "inventory_count": len(self.inventory),
            "match_count": len(self.matches),
            "rejection_count": len(self.rejections),
            "has_output_path": bool(self.output_path),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


def ensure_acquisition_imports_schema(conn: Any) -> None:
    conn.execute(ACQUISITION_IMPORTS_DDL)
    for sql in _INDEXES:
        conn.execute(sql)


def _decode_list(raw: Any) -> Tuple[Dict[str, Any], ...]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _decode_object(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _from_row(row: Any) -> AcquisitionImport:
    data = dict(row) if hasattr(row, "keys") else dict(zip(_COLUMNS, row, strict=True))
    return AcquisitionImport(
        id=str(data["id"]),
        download_id=str(data["download_id"]),
        request_id=str(data["request_id"]),
        candidate_id=data["candidate_id"],
        status=str(data["status"]),
        output_path=str(data["output_path"]),
        expected_scope=str(data["expected_scope"]),
        expected_entity_id=int(data["expected_entity_id"]),
        inventory=_decode_list(data["inventory_json"]),
        matches=_decode_list(data["matches_json"]),
        rejections=_decode_list(data["rejections_json"]),
        result=_decode_object(data["result_json"]),
        error=data["error"],
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        completed_at=data["completed_at"],
    )


def get_import(conn: Any, import_id: str) -> Optional[AcquisitionImport]:
    ensure_acquisition_imports_schema(conn)
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_imports WHERE id=?",
        (str(import_id),),
    ).fetchone()
    return _from_row(row) if row is not None else None


def get_import_by_download(
    conn: Any, download_id: str,
) -> Optional[AcquisitionImport]:
    ensure_acquisition_imports_schema(conn)
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM acquisition_imports WHERE download_id=?",
        (str(download_id),),
    ).fetchone()
    return _from_row(row) if row is not None else None


def list_open_imports(conn: Any) -> Tuple[AcquisitionImport, ...]:
    ensure_acquisition_imports_schema(conn)
    marks = ",".join("?" for _ in OPEN_IMPORT_STATUSES)
    rows = conn.execute(
        f"""SELECT {', '.join(_COLUMNS)} FROM acquisition_imports
             WHERE status IN ({marks}) ORDER BY created_at, id""",
        OPEN_IMPORT_STATUSES,
    ).fetchall()
    return tuple(_from_row(row) for row in rows)


def record_download_completed(
    conn: Any,
    download_id: str,
    *,
    output_path: str,
    client_state: str = "completed",
) -> AcquisitionImport:
    """Atomically persist download completion and its pending import.

    The owning request intentionally remains ``grabbing``. It becomes complete
    only after a later import transaction succeeds.
    """
    ensure_acquisition_imports_schema(conn)
    output_path = str(output_path or "").strip()
    if not output_path:
        raise ValueError("completed acquisition download requires an output path")
    grab = get_grab(conn, download_id)
    if grab is None or not grab.get("acquisition_request_id"):
        raise ValueError("download is not linked to an acquisition request")
    request = get_request(conn, grab["acquisition_request_id"])
    if request is None:
        raise ValueError("acquisition request no longer exists")

    existing = get_import_by_download(conn, download_id)
    if existing is not None:
        if existing.output_path != output_path:
            raise ValueError("download already completed with a different output path")
        return existing
    if request.status != "grabbing":
        raise ValueError(f"download cannot complete while request is {request.status}")
    if grab["status"] in TERMINAL_GRAB_STATUSES and grab["status"] != STATUS_COMPLETED:
        raise ValueError(f"download cannot complete while grab is {grab['status']}")

    update_grab(
        conn,
        download_id,
        status=STATUS_COMPLETED,
        last_client_state=str(client_state or "completed"),
        output_path=output_path,
        clear_error=True,
    )
    import_id = IMPORT_ID_PREFIX + secrets.token_urlsafe(18)
    conn.execute(
        """INSERT INTO acquisition_imports(
               id, download_id, request_id, candidate_id, output_path,
               expected_scope, expected_entity_id)
           VALUES(?,?,?,?,?,?,?)""",
        (
            import_id,
            str(download_id),
            request.id,
            grab.get("release_candidate_id"),
            output_path,
            request.scope,
            request.entity_id,
        ),
    )
    record_history_event(
        conn,
        "grab_completed",
        request_id=request.id,
        candidate_id=grab.get("release_candidate_id"),
        download_id=str(download_id),
        payload={"has_output_path": True, "awaiting_import": True},
    )
    created = get_import(conn, import_id)
    if created is None:  # pragma: no cover - guarded by successful INSERT
        raise RuntimeError("pending acquisition import disappeared")
    return created


__all__ = [
    "ACQUISITION_IMPORTS_DDL",
    "IMPORT_ID_PREFIX",
    "IMPORT_STATUSES",
    "OPEN_IMPORT_STATUSES",
    "AcquisitionImport",
    "ensure_acquisition_imports_schema",
    "get_import",
    "get_import_by_download",
    "list_open_imports",
    "record_download_completed",
]
