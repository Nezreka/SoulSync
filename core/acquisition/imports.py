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

from core.acquisition.candidates import redact_sensitive_text
from core.acquisition.grabs import (
    STATUS_COMPLETED,
    TERMINAL_STATUSES as TERMINAL_GRAB_STATUSES,
    get_grab,
    update_grab,
)
from core.acquisition.history import record_history_event
from core.acquisition.requests import get_request, transition_request


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

# needs_review may re-enter matching after the user fixed mappings or asked
# for a re-scan; both terminal states are final — a failed import is retried
# through a fresh acquisition request, never by reviving its row.
_ALLOWED_TRANSITIONS = {
    "pending": {"matching", "failed"},
    "matching": {"needs_review", "importing", "failed"},
    "needs_review": {"matching", "importing", "failed"},
    "importing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}

IMPORT_FAILURE_KINDS = frozenset({"candidate", "runtime"})

ACQUISITION_IMPORTS_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_imports (
    id TEXT PRIMARY KEY,
    download_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    candidate_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    output_path TEXT NOT NULL,
    resolved_path TEXT,
    expected_scope TEXT NOT NULL,
    expected_entity_id INTEGER NOT NULL,
    inventory_json TEXT NOT NULL DEFAULT '[]',
    matches_json TEXT NOT NULL DEFAULT '[]',
    rejections_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
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
    "output_path", "resolved_path", "expected_scope", "expected_entity_id",
    "inventory_json", "matches_json", "rejections_json", "result_json",
    "attempts", "error", "created_at", "updated_at", "completed_at",
)


@dataclass(frozen=True)
class AcquisitionImport:
    id: str
    download_id: str
    request_id: str
    candidate_id: Optional[str]
    status: str
    output_path: str
    resolved_path: Optional[str]
    expected_scope: str
    expected_entity_id: int
    inventory: Tuple[Dict[str, Any], ...]
    matches: Tuple[Dict[str, Any], ...]
    rejections: Tuple[Dict[str, Any], ...]
    result: Dict[str, Any]
    attempts: int
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
            "has_resolved_path": bool(self.resolved_path),
            "attempts": self.attempts,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


def ensure_acquisition_imports_schema(conn: Any) -> None:
    conn.execute(ACQUISITION_IMPORTS_DDL)
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(acquisition_imports)").fetchall()
    }
    if "resolved_path" not in existing:
        conn.execute(
            "ALTER TABLE acquisition_imports ADD COLUMN resolved_path TEXT")
    if "attempts" not in existing:
        conn.execute(
            "ALTER TABLE acquisition_imports "
            "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
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
        resolved_path=data["resolved_path"],
        expected_scope=str(data["expected_scope"]),
        expected_entity_id=int(data["expected_entity_id"]),
        inventory=_decode_list(data["inventory_json"]),
        matches=_decode_list(data["matches_json"]),
        rejections=_decode_list(data["rejections_json"]),
        result=_decode_object(data["result_json"]),
        attempts=int(data["attempts"] or 0),
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


def _encode_items(items: Any, label: str) -> str:
    encoded = []
    for item in items or ():
        if not isinstance(item, Mapping):
            raise ValueError(f"acquisition import {label} entries must be objects")
        encoded.append(dict(item))
    try:
        return json.dumps(
            encoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"acquisition import {label} must be JSON serializable") from exc


def _open_import(conn: Any, import_id: str) -> AcquisitionImport:
    record = get_import(conn, import_id)
    if record is None:
        raise KeyError(f"acquisition import not found: {import_id}")
    if record.status not in OPEN_IMPORT_STATUSES:
        raise ValueError(
            f"acquisition import is already terminal: {record.status}")
    return record


def _reload_import(conn: Any, import_id: str) -> AcquisitionImport:
    record = get_import(conn, import_id)
    if record is None:  # pragma: no cover - row updated in this transaction
        raise RuntimeError("acquisition import disappeared mid-transaction")
    return record


def _require_transition(current: str, status: str) -> None:
    if status not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(
            f"cannot transition acquisition import {current} -> {status}")


def record_inventory_result(
    conn: Any,
    import_id: str,
    files: Any,
    *,
    resolved_path: str,
) -> AcquisitionImport:
    """Persist one successful bundle inventory and enter ``matching``.

    Repeatable: a pending import enters matching once (history event), while
    matching/needs_review imports may refresh their inventory in place after
    a mapping fix or manual re-scan.
    """
    record = _open_import(conn, import_id)
    resolved = str(resolved_path or "").strip()
    if not resolved:
        raise ValueError("bundle inventory requires a resolved local path")
    inventory_json = _encode_items(files, "inventory")
    if inventory_json == "[]":
        raise ValueError("bundle inventory requires at least one audio file")
    if record.status != "matching":
        _require_transition(record.status, "matching")
    first_inventory = record.status == "pending"
    conn.execute(
        """UPDATE acquisition_imports
              SET status='matching', inventory_json=?, resolved_path=?,
                  error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (inventory_json, resolved, record.id),
    )
    if first_inventory:
        record_history_event(
            conn,
            "import_started",
            request_id=record.request_id,
            candidate_id=record.candidate_id,
            download_id=record.download_id,
            payload={
                "file_count": len(json.loads(inventory_json)),
                "path_was_remapped": resolved != record.output_path,
            },
        )
    return _reload_import(conn, import_id)


def record_matching_result(
    conn: Any,
    import_id: str,
    matches: Any,
    rejections: Any,
    *,
    decision: str,
) -> AcquisitionImport:
    """Persist one matching outcome and route the import accordingly.

    ``import_ready`` advances to ``importing``; anything ambiguous parks in
    ``needs_review`` with its structured rejections and a visible history
    event — an ambiguous bundle is a user decision, never a partial import.
    """
    decision = str(decision or "").strip().lower()
    if decision not in {"import_ready", "needs_review"}:
        raise ValueError(
            "matching decisions must be import_ready|needs_review")
    record = _open_import(conn, import_id)
    if record.status != "matching":
        raise ValueError(
            f"matching results require a matching import, not {record.status}")
    matches_json = _encode_items(matches, "matches")
    rejections_json = _encode_items(rejections, "rejections")
    if decision == "import_ready":
        if matches_json == "[]":
            raise ValueError("import_ready requires at least one track match")
        if rejections_json != "[]":
            raise ValueError("import_ready cannot carry rejections")
        status = "importing"
    else:
        status = "needs_review"
    _require_transition(record.status, status)
    conn.execute(
        """UPDATE acquisition_imports
              SET status=?, matches_json=?, rejections_json=?,
                  error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (status, matches_json, rejections_json, record.id),
    )
    if status == "needs_review":
        rejection_codes: Dict[str, int] = {}
        for item in json.loads(rejections_json):
            code = str(item.get("code") or "unknown")
            rejection_codes[code] = rejection_codes.get(code, 0) + 1
        record_history_event(
            conn,
            "import_needs_review",
            request_id=record.request_id,
            candidate_id=record.candidate_id,
            download_id=record.download_id,
            reason_code=next(iter(sorted(rejection_codes)), None),
            payload={
                "match_count": len(json.loads(matches_json)),
                "rejection_codes": rejection_codes,
            },
        )
    return _reload_import(conn, import_id)


def record_pipeline_file_completed(
    conn: Any,
    import_id: str,
    *,
    relative_path: str,
    final_path: str,
    track_id: int,
) -> AcquisitionImport:
    """Persist one success reported by the shared main import pipeline.

    The main pipeline owns validation, quarantine, tagging and file placement.
    This function only journals its successful result and completes the owning
    acquisition once every matched file has passed that pipeline.
    """
    record = get_import(conn, import_id)
    if record is None:
        raise KeyError(f"acquisition import not found: {import_id}")
    relative = str(relative_path or "").strip().replace("\\", "/")
    final = str(final_path or "").strip()
    track_id = int(track_id)
    existing_processed = [
        dict(item) for item in record.result.get("processed", [])
        if isinstance(item, Mapping)
    ]
    if record.status == "completed" and any(
        str(item.get("relative_path") or "") == relative
        and int(item.get("track_id") or 0) == track_id
        and str(item.get("final_path") or "") == final
        for item in existing_processed
    ):
        return record
    if record.status != "importing":
        raise ValueError(
            f"pipeline completion requires importing, not {record.status}")
    if not relative or not final or track_id <= 0:
        raise ValueError(
            "pipeline completion requires relative_path, final_path and track_id")

    expected = {
        (str(item.get("relative_path") or "").replace("\\", "/"),
         int(item.get("track_id") or 0))
        for item in record.matches
    }
    key = (relative, track_id)
    if key not in expected:
        raise ValueError("pipeline completion does not match the persisted import plan")

    result = dict(record.result)
    processed = existing_processed
    by_key = {
        (str(item.get("relative_path") or ""), int(item.get("track_id") or 0)): item
        for item in processed
    }
    by_key[key] = {
        "relative_path": relative,
        "final_path": final,
        "track_id": track_id,
    }
    processed = [by_key[item] for item in sorted(by_key)]
    result["processed"] = processed
    result_json = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    completed = expected and expected <= set(by_key)
    conn.execute(
        """UPDATE acquisition_imports
              SET status=?, result_json=?, error=NULL,
                  updated_at=CURRENT_TIMESTAMP,
                  completed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id=?""",
        ("completed" if completed else "importing", result_json,
         1 if completed else 0, record.id),
    )
    if completed:
        transition_request(
            conn,
            record.request_id,
            "completed",
            expected_status="grabbing",
        )
        record_history_event(
            conn,
            "import_completed",
            request_id=record.request_id,
            candidate_id=record.candidate_id,
            download_id=record.download_id,
            payload={"file_count": len(processed), "pipeline": "main"},
        )
    return _reload_import(conn, import_id)


def record_manual_resolution(
    conn: Any,
    import_id: str,
    matches: Any,
) -> AcquisitionImport:
    """Persist a reviewed assignment and hand it to the shared pipeline."""
    record = _open_import(conn, import_id)
    if record.status != "needs_review":
        raise ValueError(
            f"manual resolution requires needs_review, not {record.status}")
    matches_json = _encode_items(matches, "matches")
    if matches_json == "[]":
        raise ValueError("manual resolution requires at least one match")
    conn.execute(
        """UPDATE acquisition_imports
              SET status='importing', matches_json=?, rejections_json='[]',
                  error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (matches_json, record.id),
    )
    record_history_event(
        conn,
        "import_resolved_manually",
        request_id=record.request_id,
        candidate_id=record.candidate_id,
        download_id=record.download_id,
        payload={"match_count": len(json.loads(matches_json))},
    )
    return _reload_import(conn, import_id)


def record_import_deferred(
    conn: Any,
    import_id: str,
    *,
    error: str,
) -> AcquisitionImport:
    """Count one transient import attempt without changing business state.

    Used for unreadable paths (broken mount, missing remote path mapping):
    the import stays open and visible with its error until a later cycle
    succeeds or an operator fails it explicitly.
    """
    record = _open_import(conn, import_id)
    safe_error = redact_sensitive_text(error) or "acquisition import deferred"
    conn.execute(
        """UPDATE acquisition_imports
              SET attempts=attempts+1, error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (str(safe_error)[:2000], record.id),
    )
    return _reload_import(conn, import_id)


def record_import_failure(
    conn: Any,
    import_id: str,
    *,
    error: str,
    failure_kind: str,
    reason_code: Optional[str] = None,
) -> AcquisitionImport:
    """Terminally fail an import and its owning request in one transaction.

    ``candidate`` failures (broken bundle, wrong content) additionally
    blocklist the exact release so a re-search cannot pick it again
    (audit §13.5). ``runtime`` failures keep the candidate grabbable.
    """
    kind = str(failure_kind or "").strip().lower()
    if kind not in IMPORT_FAILURE_KINDS:
        raise ValueError("import failures require failure_kind candidate|runtime")
    record = _open_import(conn, import_id)
    _require_transition(record.status, "failed")
    safe_error = redact_sensitive_text(error) or "acquisition import failed"
    conn.execute(
        """UPDATE acquisition_imports
              SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP,
                  completed_at=CURRENT_TIMESTAMP
            WHERE id=?""",
        (str(safe_error)[:2000], record.id),
    )
    transition_request(
        conn,
        record.request_id,
        "failed",
        expected_status="grabbing",
        error=safe_error,
    )
    record_history_event(
        conn,
        "import_failed",
        request_id=record.request_id,
        candidate_id=record.candidate_id,
        download_id=record.download_id,
        reason_code=str(reason_code or f"{kind}_failure"),
        message=safe_error,
        payload={"failure_kind": kind, "attempts": record.attempts},
    )
    if kind == "candidate":
        if not record.candidate_id:
            raise ValueError(
                "candidate import failures require a release candidate")
        from core.acquisition.blocklist import block_candidate
        block_candidate(
            conn,
            record.candidate_id,
            reason_code=str(reason_code or "import_failure"),
            message=safe_error,
            download_id=record.download_id,
        )
    return _reload_import(conn, import_id)


__all__ = [
    "ACQUISITION_IMPORTS_DDL",
    "IMPORT_FAILURE_KINDS",
    "IMPORT_ID_PREFIX",
    "IMPORT_STATUSES",
    "OPEN_IMPORT_STATUSES",
    "AcquisitionImport",
    "ensure_acquisition_imports_schema",
    "get_import",
    "get_import_by_download",
    "list_open_imports",
    "record_download_completed",
    "record_import_deferred",
    "record_import_failure",
    "record_inventory_result",
    "record_matching_result",
    "record_manual_resolution",
    "record_pipeline_file_completed",
]
