"""Read-only integrity report across Library v2 and its boundary indices.

This is intentionally an auditor, not a second repair engine.  It resolves
stored paths through the shared mapping layer, observes every optional index
that exists, and emits small reason-coded findings.  It never updates a row,
deletes a file, or treats an unavailable storage root as proof of absence.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from core.library2.paths import missing_path_root_is_healthy, resolve_lib2_path


_TERMINAL_RUNTIME = {
    "completed", "failed", "cancelled", "canceled", "not_found", "skipped",
    "already_owned",
}
_OPEN_GRAB = {"pending", "submitting", "queued", "downloading", "cancel_pending"}


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def _columns(conn: Any, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _dict_rows(conn: Any, sql: str, parameters: Sequence[Any] = ()) -> list[dict]:
    rows = conn.execute(sql, tuple(parameters)).fetchall()
    return [dict(row) for row in rows]


def _normalized(path: Any) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        return os.path.normcase(os.path.abspath(value)).replace("\\", "/")
    except (OSError, ValueError):
        return value.replace("\\", "/").casefold()


def _resolved(path: Any, config_manager: Any = None) -> Optional[str]:
    value = str(path or "").strip()
    if not value:
        return None
    resolved = resolve_lib2_path(value, config_manager=config_manager)
    if resolved and os.path.isfile(resolved):
        return resolved
    return value if os.path.isfile(value) else None


def _context_marker(context: Mapping[str, Any]) -> Optional[str]:
    from core.acquisition.manual_grab import GRAB_MARKER

    marker = context.get(GRAB_MARKER)
    if marker:
        return str(marker)
    track = context.get("track_info")
    if isinstance(track, Mapping) and track.get(GRAB_MARKER):
        return str(track[GRAB_MARKER])
    return None


@dataclass(frozen=True)
class IntegrityFinding:
    code: str
    severity: str
    component: str
    entity: str
    reason: str
    details: Dict[str, Any]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "component": self.component,
            "entity": self.entity,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class IntegrityReport:
    read_only: bool
    observed: Dict[str, int]
    counts: Dict[str, int]
    findings: Tuple[IntegrityFinding, ...]
    findings_total: int
    truncated: bool
    coverage: Dict[str, Any]
    acquisition: Optional[Dict[str, Any]] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "read_only": self.read_only,
            "observed": dict(self.observed),
            "counts": dict(self.counts),
            "findings": [item.to_public_dict() for item in self.findings],
            "findings_total": self.findings_total,
            "truncated": self.truncated,
            "coverage": dict(self.coverage),
            "acquisition": dict(self.acquisition) if self.acquisition else None,
        }


class _Collector:
    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self.items: list[IntegrityFinding] = []
        self.counts: Counter[str] = Counter()
        self.total = 0

    def add(
        self,
        code: str,
        severity: str,
        component: str,
        entity: Any,
        reason: str,
        **details: Any,
    ) -> None:
        self.total += 1
        self.counts[code] += 1
        self.counts[f"severity:{severity}"] += 1
        if len(self.items) < self.limit:
            self.items.append(IntegrityFinding(
                code=code,
                severity=severity,
                component=component,
                entity=str(entity),
                reason=reason,
                details=details,
            ))


def _quarantine_disk_findings(
    collector: _Collector, quarantine_dir: Optional[str],
) -> Dict[str, int]:
    observed = {"quarantine_files": 0, "quarantine_sidecars": 0}
    if not quarantine_dir or not os.path.isdir(quarantine_dir):
        return observed
    try:
        names = os.listdir(quarantine_dir)
    except OSError as exc:
        collector.add(
            "quarantine_unreadable", "warning", "quarantine", quarantine_dir,
            "Quarantine directory could not be read", error=str(exc),
        )
        return observed
    files = set()
    sidecars = set()
    from core.imports.quarantine import entry_id_from_quarantined_filename

    for name in names:
        if name.endswith(".quarantined"):
            files.add(entry_id_from_quarantined_filename(name))
        elif name.endswith(".json"):
            sidecars.add(name[:-5])
    observed.update(
        quarantine_files=len(files), quarantine_sidecars=len(sidecars),
    )
    for entry_id in sorted(files - sidecars):
        collector.add(
            "quarantine_file_without_sidecar", "warning", "quarantine",
            entry_id, "Quarantined file has no lifecycle context",
        )
    for entry_id in sorted(sidecars - files):
        collector.add(
            "quarantine_sidecar_without_file", "warning", "quarantine",
            entry_id, "Quarantine sidecar has no quarantined file",
        )
    return observed


def build_integrity_report(
    conn: Any,
    *,
    runtime_tasks: Optional[Mapping[str, Mapping[str, Any]]] = None,
    matched_contexts: Iterable[Mapping[str, Any]] = (),
    client_observations: Optional[Mapping[str, Mapping[str, Any]]] = None,
    quarantine_entries: Iterable[Mapping[str, Any]] = (),
    quarantine_dir: Optional[str] = None,
    acquisition_report: Optional[Mapping[str, Any]] = None,
    media_server: Optional[Mapping[str, Any]] = None,
    config_manager: Any = None,
    max_findings: int = 1000,
) -> IntegrityReport:
    """Build one database- and filesystem-read-only cross-index snapshot."""
    collector = _Collector(max_findings)
    observed: Counter[str] = Counter()
    tables = {
        name: _table_exists(conn, name)
        for name in (
            "lib2_track_files", "tracks", "track_downloads", "library_history",
            "acquisition_requests", "acquisition_grabs", "acquisition_imports",
            "acquisition_quarantine_recoveries",
        )
    }
    coverage: Dict[str, Any] = {
        "tables": tables,
        "disk": True,
        "runtime": True,
        "external_client": client_observations is not None,
        "quarantine": bool(quarantine_dir),
        "media_server": dict(media_server or {"available": False}),
        "storage_health_gate": True,
        "destructive_actions": False,
    }

    lib2_real: set[str] = set()
    lib2_paths_by_key: Dict[str, set[int]] = {}
    if tables["lib2_track_files"]:
        rows = _dict_rows(
            conn,
            "SELECT id, track_id, path, COALESCE(file_state,'active') AS file_state "
            "FROM lib2_track_files WHERE path IS NOT NULL AND path<>''",
        )
        observed["lib2_files"] = len(rows)
        for row in rows:
            raw_path = row["path"]
            resolved = _resolved(raw_path, config_manager=config_manager)
            state = str(row.get("file_state") or "active")
            if resolved:
                resolved_key = _normalized(resolved)
                if state not in {"missing_confirmed", "deleted"}:
                    lib2_real.add(resolved_key)
                    lib2_paths_by_key.setdefault(resolved_key, set()).add(
                        int(row["track_id"]),
                    )
                if state in {"missing_suspected", "missing_confirmed", "deleted"}:
                    collector.add(
                        "lib2_state_file_recovered", "warning", "library_v2",
                        row["id"], "File exists while its persisted state is missing",
                        track_id=row["track_id"], file_state=state, path=raw_path,
                        resolved_path=resolved,
                    )
            elif state not in {"missing_confirmed", "deleted"}:
                healthy = missing_path_root_is_healthy(
                    raw_path, config_manager=config_manager,
                )
                collector.add(
                    "lib2_active_file_missing" if healthy else "lib2_file_unresolved",
                    "error" if healthy else "warning",
                    "library_v2",
                    row["id"],
                    (
                        "Active Library-v2 file is absent on a healthy storage root"
                        if healthy else
                        "File cannot be resolved; unhealthy storage prevents confirmation"
                    ),
                    track_id=row["track_id"], file_state=state, path=raw_path,
                    storage_root_healthy=healthy,
                )
        for key, track_ids in lib2_paths_by_key.items():
            if len(track_ids) > 1:
                collector.add(
                    "lib2_path_multiple_tracks", "error", "library_v2", key,
                    "One physical file is attached to multiple Library-v2 tracks",
                    track_ids=sorted(track_ids),
                )

    legacy_real: set[str] = set()
    legacy_rows: list[dict] = []
    if tables["tracks"] and "file_path" in _columns(conn, "tracks"):
        select = "id, file_path"
        if "server_source" in _columns(conn, "tracks"):
            select += ", server_source"
        legacy_rows = _dict_rows(
            conn, f"SELECT {select} FROM tracks WHERE file_path IS NOT NULL AND file_path<>''",
        )
        observed["legacy_files"] = len(legacy_rows)
        for row in legacy_rows:
            resolved = _resolved(row["file_path"], config_manager=config_manager)
            if not resolved:
                healthy = missing_path_root_is_healthy(
                    row["file_path"], config_manager=config_manager,
                )
                if healthy:
                    collector.add(
                        "legacy_index_file_missing", "warning", "legacy_index",
                        row["id"], "Legacy/media-server projection points to a missing file",
                        path=row["file_path"], storage_root_healthy=True,
                    )
                continue
            key = _normalized(resolved)
            legacy_real.add(key)
            if key not in lib2_real:
                collector.add(
                    "legacy_only_indexed_file", "warning", "index_divergence",
                    row["id"], "Real file is indexed by Legacy but not active Library v2",
                    path=row["file_path"], resolved_path=resolved,
                    server_source=row.get("server_source"),
                )
    for key in sorted(lib2_real - legacy_real):
        collector.add(
            "lib2_only_indexed_file", "info", "index_divergence", key,
            "Real file is indexed by Library v2 but not the Legacy/media-server projection",
            resolved_path=key,
        )
    indexed_real = lib2_real | legacy_real

    for table, component in (
        ("track_downloads", "download_provenance"),
        ("library_history", "library_history"),
    ):
        if not tables[table] or "file_path" not in _columns(conn, table):
            continue
        rows = _dict_rows(
            conn, f"SELECT id, file_path FROM {table} WHERE file_path IS NOT NULL AND file_path<>''",
        )
        observed[table] = len(rows)
        for row in rows:
            resolved = _resolved(row["file_path"], config_manager=config_manager)
            if resolved and _normalized(resolved) not in indexed_real:
                collector.add(
                    "provenance_unindexed_file", "warning", component, row["id"],
                    "Provenance/history points to a real file absent from both file indices",
                    table=table, path=row["file_path"], resolved_path=resolved,
                )

    runtime_tasks = {
        str(key): dict(value)
        for key, value in (runtime_tasks or {}).items()
        if isinstance(value, Mapping)
    }
    observed["runtime_tasks"] = len(runtime_tasks)
    for task_id, task in runtime_tasks.items():
        status = str(task.get("status") or "").casefold()
        path = task.get("final_file_path") or task.get("file_path")
        resolved = _resolved(path, config_manager=config_manager)
        if status in _TERMINAL_RUNTIME and resolved and _normalized(resolved) not in indexed_real:
            collector.add(
                "runtime_terminal_unindexed_file", "error", "runtime", task_id,
                "Terminal runtime task has a real final file absent from both indices",
                status=status, path=path, resolved_path=resolved,
            )

    grabs: Dict[str, Dict[str, Any]] = {}
    if tables["acquisition_grabs"]:
        grab_cols = _columns(conn, "acquisition_grabs")
        wanted = [
            name for name in ("download_id", "status", "acquisition_request_id")
            if name in grab_cols
        ]
        if "download_id" in wanted:
            rows = _dict_rows(conn, f"SELECT {', '.join(wanted)} FROM acquisition_grabs")
            grabs = {str(row["download_id"]): row for row in rows}
            observed["acquisition_grabs"] = len(rows)

    contexts = [dict(item) for item in matched_contexts if isinstance(item, Mapping)]
    observed["matched_contexts"] = len(contexts)
    for index, context in enumerate(contexts):
        marker = _context_marker(context)
        if marker and marker in grabs and str(grabs[marker].get("status")) not in _OPEN_GRAB:
            collector.add(
                "stale_matched_context", "warning", "runtime", marker,
                "Matched runtime context survived a terminal persistent grab",
                grab_status=grabs[marker].get("status"), context_index=index,
            )
        path = context.get("_final_processed_path") or context.get("_final_path")
        resolved = _resolved(path, config_manager=config_manager)
        if resolved and _normalized(resolved) not in indexed_real:
            collector.add(
                "matched_context_unindexed_file", "error", "runtime", marker or index,
                "Post-processing context has a real final file absent from both indices",
                path=path, resolved_path=resolved,
            )

    if tables["acquisition_requests"] and tables["acquisition_grabs"]:
        request_columns = _columns(conn, "acquisition_requests")
        if {"id", "status"}.issubset(request_columns):
            rows = _dict_rows(
                conn,
                """SELECT r.id AS request_id, r.status AS request_status,
                          g.download_id, g.status AS grab_status
                     FROM acquisition_requests r
                     JOIN acquisition_grabs g ON g.acquisition_request_id=r.id""",
            )
            observed["acquisition_links"] = len(rows)
            for row in rows:
                request_open = row["request_status"] == "grabbing"
                grab_open = row["grab_status"] in _OPEN_GRAB
                if request_open != grab_open:
                    collector.add(
                        "acquisition_lifecycle_divergence", "error", "acquisition",
                        row["download_id"],
                        "Request and grab disagree about terminality",
                        request_id=row["request_id"],
                        request_status=row["request_status"],
                        grab_status=row["grab_status"],
                    )

    if tables["acquisition_imports"]:
        import_cols = _columns(conn, "acquisition_imports")
        if {"id", "status", "output_path"}.issubset(import_cols):
            resolved_expr = "resolved_path" if "resolved_path" in import_cols else "NULL"
            rows = _dict_rows(
                conn,
                f"SELECT id, status, output_path, {resolved_expr} AS resolved_path "
                "FROM acquisition_imports",
            )
            observed["acquisition_imports"] = len(rows)
            for row in rows:
                if row["status"] != "completed":
                    continue
                path = row.get("resolved_path") or row.get("output_path")
                resolved = _resolved(path, config_manager=config_manager)
                if not resolved or _normalized(resolved) not in indexed_real:
                    collector.add(
                        "completed_import_without_indexed_file", "error", "acquisition",
                        row["id"],
                        "Completed acquisition import lacks a real indexed file",
                        path=path, file_exists=bool(resolved),
                    )

    if tables["acquisition_quarantine_recoveries"]:
        rows = _dict_rows(
            conn,
            "SELECT entry_id, staged_path, status FROM acquisition_quarantine_recoveries",
        )
        observed["quarantine_recoveries"] = len(rows)
        for row in rows:
            if row["status"] in {"recovered", "reimporting"} and not os.path.isfile(
                str(row["staged_path"] or "")
            ):
                collector.add(
                    "recovery_staging_file_missing", "error", "quarantine",
                    row["entry_id"],
                    "Recovery journal waits for a staging file that is absent",
                    status=row["status"], staged_path=row["staged_path"],
                )

    quarantine = [
        dict(item) for item in quarantine_entries if isinstance(item, Mapping)
    ]
    observed["quarantine_entries"] = len(quarantine)
    for entry in quarantine:
        context = entry.get("context")
        if not isinstance(context, Mapping) or not context:
            collector.add(
                "quarantine_correlation_missing", "warning", "quarantine",
                entry.get("id") or "unknown",
                "Quarantine entry has no persisted pipeline correlation",
            )
            continue
        import_id = context.get("_acquisition_import_id")
        marker = _context_marker(context)
        if import_id and tables["acquisition_imports"]:
            found = conn.execute(
                "SELECT 1 FROM acquisition_imports WHERE id=?", (str(import_id),),
            ).fetchone()
            if found is None:
                collector.add(
                    "quarantine_import_reference_missing", "error", "quarantine",
                    entry.get("id") or "unknown",
                    "Sidecar references an acquisition import that does not exist",
                    import_id=import_id,
                )
        elif marker and marker not in grabs:
            collector.add(
                "quarantine_grab_reference_missing", "warning", "quarantine",
                entry.get("id") or "unknown",
                "Sidecar references a persistent grab that does not exist",
                download_id=marker,
            )

    observed.update(_quarantine_disk_findings(collector, quarantine_dir))

    clients = {
        str(key): dict(value)
        for key, value in (client_observations or {}).items()
        if isinstance(value, Mapping)
    }
    observed["external_client_jobs"] = len(clients)
    for client_id, state in clients.items():
        raw_state = str(state.get("state") or "").casefold()
        path = state.get("file_path") or state.get("save_path")
        resolved = _resolved(path, config_manager=config_manager)
        if (
            any(token in raw_state for token in ("complete", "succeed", "finished"))
            and resolved
            and _normalized(resolved) not in indexed_real
        ):
            collector.add(
                "client_terminal_unindexed_file", "error", "external_client",
                client_id, "External client completed a real file absent from both indices",
                state=raw_state, path=path, resolved_path=resolved,
            )

    acquisition = dict(acquisition_report or {}) or None
    if acquisition:
        for decision in acquisition.get("decisions") or []:
            if not isinstance(decision, Mapping) or decision.get("action") == "none":
                continue
            collector.add(
                "acquisition_transition_pending", "warning", "acquisition",
                decision.get("download_id") or "unknown",
                "Dry-run found an evidence-backed persistent transition",
                action=decision.get("action"),
                decision_reason=decision.get("reason"),
                evidence=decision.get("evidence"),
            )

    return IntegrityReport(
        read_only=True,
        observed=dict(sorted(observed.items())),
        counts=dict(sorted(collector.counts.items())),
        findings=tuple(collector.items),
        findings_total=collector.total,
        truncated=collector.total > len(collector.items),
        coverage=coverage,
        acquisition=acquisition,
    )


__all__ = [
    "IntegrityFinding",
    "IntegrityReport",
    "build_integrity_report",
]
