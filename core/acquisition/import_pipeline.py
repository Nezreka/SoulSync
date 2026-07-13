"""Restart-safe coordinator around inventory, matching and the main pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from core.acquisition.bundle_inventory import (
    INVENTORY_NO_AUDIO_FILES,
    collect_bundle_inventory,
)
from core.acquisition.bundle_matching import load_expected_tracks, match_bundle
from core.acquisition.imports import (
    get_import,
    list_open_imports,
    record_import_deferred,
    record_import_failure,
    record_inventory_result,
    record_matching_result,
)
from core.acquisition.main_pipeline_bridge import dispatch_import_to_main_pipeline
from utils.logging_config import get_logger


logger = get_logger("acquisition.import_pipeline")

BACKOFF_BASE_SECONDS = 60.0
BACKOFF_CAP_SECONDS = 3600.0


@dataclass(frozen=True)
class ImportPipelineResult:
    processed: Tuple[str, ...] = ()
    outcomes: Dict[str, str] = field(default_factory=dict)

    def to_public_dict(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for outcome in self.outcomes.values():
            counts[outcome] = counts.get(outcome, 0) + 1
        return {"processed": len(self.processed), "outcomes": counts}


def retry_backoff_seconds(attempts: int) -> float:
    exponent = min(max(int(attempts) - 1, 0), 6)
    return min(BACKOFF_BASE_SECONDS * (2 ** exponent), BACKOFF_CAP_SECONDS)


def _timestamp(raw: Any) -> Optional[float]:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def is_due(record: Any, *, now: float) -> bool:
    if any(
        isinstance(item, dict)
        for item in record.result.get("quarantined", [])
    ):
        return False
    if record.attempts <= 0 or not record.error:
        return True
    updated = _timestamp(record.updated_at)
    return updated is None or now >= updated + retry_backoff_seconds(record.attempts)


def _defer(connection_factory: Callable[[], Any], import_id: str, error: str) -> None:
    conn = connection_factory()
    try:
        record_import_deferred(conn, import_id, error=error)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def advance_import(
    connection_factory: Callable[[], Any],
    import_id: str,
    *,
    config_get: Optional[Callable[..., Any]] = None,
    collector: Optional[Callable[..., Any]] = None,
    dispatcher: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> str:
    """Advance one row until it reaches a waiting or terminal boundary."""
    collector = collector or collect_bundle_inventory
    dispatcher = dispatcher or dispatch_import_to_main_pipeline
    timestamp = float(now) if now is not None else datetime.now(timezone.utc).timestamp()

    for _ in range(4):
        conn = connection_factory()
        try:
            record = get_import(conn, import_id)
        finally:
            conn.close()
        if record is None:
            return "missing"
        if record.status == "needs_review":
            return "needs_review"
        if record.status in {"completed", "failed"}:
            return record.status
        if record.result.get("quarantined"):
            return "quarantined"
        if not is_due(record, now=timestamp):
            return "backoff"

        if record.status == "pending":
            inventory = collector(record.output_path, config_get=config_get)
            conn = connection_factory()
            try:
                if inventory.ok:
                    record_inventory_result(
                        conn,
                        record.id,
                        [item.to_dict() for item in inventory.files],
                        resolved_path=inventory.resolved_path,
                    )
                    conn.commit()
                    continue
                if inventory.status == INVENTORY_NO_AUDIO_FILES:
                    record_import_failure(
                        conn,
                        record.id,
                        error=inventory.error or "Completed download contains no audio files",
                        failure_kind="candidate",
                        reason_code="no_audio_files",
                    )
                    conn.commit()
                    return "failed"
                record_import_deferred(
                    conn,
                    record.id,
                    error=inventory.error or "Completed download path is not readable",
                )
                conn.commit()
                return "deferred"
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if record.status == "matching":
            conn = connection_factory()
            try:
                from core.acquisition.requests import get_request
                request = get_request(conn, record.request_id)
                search_options = request.search_options if request else {}
                expected = load_expected_tracks(
                    conn,
                    record.expected_scope,
                    record.expected_entity_id,
                    search_options=search_options,
                )
                report = match_bundle(expected, record.inventory)
                record_matching_result(
                    conn,
                    record.id,
                    report.matches_payload(),
                    report.rejections_payload(),
                    decision=report.decision,
                )
                conn.commit()
                if not report.import_ready:
                    return "needs_review"
                continue
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if record.status == "importing":
            result = dispatcher(
                connection_factory,
                record.id,
                config_get=config_get,
            )
            conn = connection_factory()
            try:
                current = get_import(conn, record.id)
            finally:
                conn.close()
            if current and current.status == "completed":
                return "completed"
            if result.errors:
                _defer(connection_factory, record.id, "; ".join(result.errors))
                return "deferred"
            if result.waiting:
                _defer(
                    connection_factory,
                    record.id,
                    "Waiting for shared pipeline retry or quarantine review",
                )
                return "waiting_pipeline"
            return "importing"
    return "deferred"


def advance_open_imports(
    connection_factory: Callable[[], Any],
    *,
    limit: int = 10,
    config_get: Optional[Callable[..., Any]] = None,
    collector: Optional[Callable[..., Any]] = None,
    dispatcher: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> ImportPipelineResult:
    timestamp = float(now) if now is not None else datetime.now(timezone.utc).timestamp()

    # First revive retry walks a restart interrupted — their imports carry
    # quarantined entries and are deliberately NOT due below; the journaled
    # legacy-worker state is the only thing that can continue them.
    try:
        from core.acquisition.retry_resume import resume_interrupted_retry_walks
        resume_interrupted_retry_walks(connection_factory, now=timestamp)
    except Exception as exc:  # noqa: BLE001 - resume must not stop imports
        logger.warning("Acquisition retry resume failed: %s", exc)

    conn = connection_factory()
    try:
        records = [
            record for record in list_open_imports(conn)
            if record.status != "needs_review" and is_due(record, now=timestamp)
        ][:max(int(limit), 0)]
    finally:
        conn.close()

    outcomes = {}
    for record in records:
        try:
            outcomes[record.id] = advance_import(
                connection_factory,
                record.id,
                config_get=config_get,
                collector=collector,
                dispatcher=dispatcher,
                now=timestamp,
            )
        except Exception as exc:  # noqa: BLE001 - isolate imports
            logger.warning("Acquisition import %s failed to advance: %s", record.id, exc)
            try:
                _defer(connection_factory, record.id, f"pipeline step failed: {exc}")
            except Exception:
                logger.exception("Could not defer acquisition import %s", record.id)
            outcomes[record.id] = "deferred"
    return ImportPipelineResult(tuple(outcomes), outcomes)


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "ImportPipelineResult",
    "advance_import",
    "advance_open_imports",
    "is_due",
    "retry_backoff_seconds",
]
