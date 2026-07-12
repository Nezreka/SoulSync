"""Central Usenet client snapshot and acquisition reconciliation.

Network reads happen in :func:`collect_usenet_snapshot`, before a database
transaction is opened. Reconciliation persists business transitions only; live
progress, speed, ETA and byte counters remain owned by SABnzbd/NZBGet.
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from core.acquisition.candidates import redact_sensitive_text
from core.acquisition.grabs import (
    STATUS_CANCEL_PENDING,
    STATUS_DOWNLOADING,
    STATUS_QUEUED,
    ensure_acquisition_grabs_schema,
    open_grabs,
    update_grab,
)
from core.acquisition.history import record_history_event
from core.acquisition.imports import record_download_completed
from core.acquisition.workflow import record_grab_cancelled, record_grab_outcome
from core.usenet_clients.base import UsenetClientAdapter, UsenetStatus
from utils.async_helpers import run_async
from utils.logging_config import get_logger


logger = get_logger("acquisition.client_monitor")


_ACTIVE_STATE_TO_STATUS = {
    "queued": STATUS_QUEUED,
    "paused": STATUS_QUEUED,
    "downloading": STATUS_DOWNLOADING,
    "extracting": STATUS_DOWNLOADING,
    "verifying": STATUS_DOWNLOADING,
    "repairing": STATUS_DOWNLOADING,
}


def _category_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _title_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if text.casefold().endswith(".nzb"):
        text = text[:-4]
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class UsenetJobSnapshot:
    id: str
    name: str
    state: str
    category: Optional[str]
    save_path: Optional[str]
    error: Optional[str]

    @classmethod
    def from_status(cls, status: UsenetStatus) -> "UsenetJobSnapshot":
        job_id = str(status.id or "").strip()
        if not job_id:
            raise ValueError("Usenet client returned a job without an id")
        return cls(
            id=job_id,
            name=str(status.name or "").strip(),
            state=str(status.state or "unknown").strip().lower() or "unknown",
            category=(str(status.category).strip() if status.category is not None else None),
            save_path=(str(status.save_path).strip() if status.save_path else None),
            error=(redact_sensitive_text(status.error) if status.error else None),
        )


@dataclass(frozen=True)
class UsenetClientSnapshot:
    client: str
    category: str
    jobs: Tuple[UsenetJobSnapshot, ...]
    lookup_errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationResult:
    observed: Tuple[str, ...]
    updated: Tuple[str, ...]
    adopted: Tuple[str, ...]
    completed: Tuple[str, ...]
    failed: Tuple[str, ...]
    cancel_pending: Tuple[str, ...]
    missing: Tuple[str, ...]
    completed_without_path: Tuple[str, ...]
    ambiguous: Tuple[str, ...]
    unmatched_job_ids: Tuple[str, ...]


@dataclass(frozen=True)
class MonitorRunResult:
    open_grabs: int
    stale_submissions_failed: Tuple[str, ...] = ()
    reconciliation: Optional[ReconciliationResult] = None
    cancelled: Tuple[str, ...] = ()
    cancel_failed: Tuple[str, ...] = ()
    imports: Optional[Any] = None
    skipped_reason: Optional[str] = None


async def collect_usenet_snapshot(
    adapter: UsenetClientAdapter,
    category: str,
    *,
    known_job_ids: Iterable[str] = (),
) -> UsenetClientSnapshot:
    """Read one category plus targeted known jobs from the external client.

    The returned type deliberately has no progress/bytes/speed fields. Known
    job ids receive a targeted fallback lookup because bulk history windows are
    finite in both supported clients.
    """
    category = str(category or "").strip()
    if not category:
        raise ValueError("Usenet acquisition category is required")
    if adapter is None or not adapter.is_configured():
        raise RuntimeError("No configured Usenet client is available")

    try:
        statuses = await adapter.get_all()
    except Exception as exc:  # noqa: BLE001 - adapter/network boundary
        raise RuntimeError(
            redact_sensitive_text(f"Usenet client snapshot failed: {exc}"),
        ) from exc
    if statuses is None:
        raise RuntimeError("Usenet client snapshot returned no result")

    known = {str(job_id).strip() for job_id in known_job_ids if str(job_id).strip()}
    jobs: Dict[str, UsenetJobSnapshot] = {}
    for status in statuses:
        job = UsenetJobSnapshot.from_status(status)
        if _category_key(job.category) == _category_key(category) or job.id in known:
            jobs[job.id] = job

    lookup_errors = []
    for job_id in sorted(known - jobs.keys()):
        try:
            status = await adapter.get_status(job_id)
        except Exception:  # noqa: BLE001 - one failed lookup is isolated
            lookup_errors.append(job_id)
            continue
        if status is not None:
            job = UsenetJobSnapshot.from_status(status)
            if job.id == job_id:
                jobs[job.id] = job

    return UsenetClientSnapshot(
        client=adapter.__class__.__name__,
        category=category,
        jobs=tuple(jobs[job_id] for job_id in sorted(jobs)),
        lookup_errors=tuple(lookup_errors),
    )


def _adoption_pairs(
    unresolved: list[dict], unknown_jobs: list[UsenetJobSnapshot],
) -> Tuple[list[tuple[dict, UsenetJobSnapshot, str]], list[str]]:
    pairs: list[tuple[dict, UsenetJobSnapshot, str]] = []
    ambiguous: list[str] = []
    remaining_grabs = list(unresolved)
    remaining_jobs = list(unknown_jobs)

    grab_titles: Dict[str, list[dict]] = {}
    job_titles: Dict[str, list[UsenetJobSnapshot]] = {}
    for grab in remaining_grabs:
        key = _title_key(grab.get("title"))
        if key:
            grab_titles.setdefault(key, []).append(grab)
    for job in remaining_jobs:
        key = _title_key(job.name)
        if key:
            job_titles.setdefault(key, []).append(job)

    for key in sorted(set(grab_titles) & set(job_titles)):
        grabs = grab_titles[key]
        jobs = job_titles[key]
        if len(grabs) == 1 and len(jobs) == 1:
            pair = (grabs[0], jobs[0], "exact_title")
            pairs.append(pair)
            remaining_grabs.remove(grabs[0])
            remaining_jobs.remove(jobs[0])
        else:
            ambiguous.extend(str(grab["download_id"]) for grab in grabs)

    if len(remaining_grabs) == 1 and len(remaining_jobs) == 1:
        pairs.append((remaining_grabs[0], remaining_jobs[0], "unique_category_job"))
        remaining_grabs.clear()
        remaining_jobs.clear()
    elif remaining_grabs and remaining_jobs:
        ambiguous.extend(str(grab["download_id"]) for grab in remaining_grabs)

    return pairs, sorted(set(ambiguous))


def reconcile_usenet_snapshot(
    conn: Any,
    snapshot: UsenetClientSnapshot,
) -> ReconciliationResult:
    """Apply one client snapshot in the caller's short DB transaction."""
    ensure_acquisition_grabs_schema(conn)
    all_open = [
        grab for grab in open_grabs(conn, "usenet")
        if grab.get("acquisition_request_id")
    ]
    jobs = {job.id: job for job in snapshot.jobs}
    attached: Dict[str, list[dict]] = {}
    for grab in all_open:
        job_id = grab.get("external_job_id")
        if job_id:
            attached.setdefault(str(job_id), []).append(grab)

    known_ids = set(attached)
    unknown_jobs = [
        job for job in snapshot.jobs
        if job.id not in known_ids
        and _category_key(job.category) == _category_key(snapshot.category)
    ]
    unresolved = [
        grab for grab in all_open
        if not grab.get("external_job_id")
        and grab.get("last_client_state") == "submission_unknown"
        and _category_key(grab.get("category")) in {"", _category_key(snapshot.category)}
    ]
    adoption_pairs, ambiguous = _adoption_pairs(unresolved, unknown_jobs)
    adopted = []
    for grab, job, strategy in adoption_pairs:
        update_grab(
            conn,
            grab["download_id"],
            external_job_id=job.id,
            client=snapshot.client,
            category=snapshot.category,
            adopted=True,
            clear_error=True,
        )
        record_history_event(
            conn,
            "client_job_adopted",
            request_id=grab["acquisition_request_id"],
            candidate_id=grab.get("release_candidate_id"),
            download_id=grab["download_id"],
            payload={
                "source": "usenet",
                "client": snapshot.client,
                "category": snapshot.category,
                "strategy": strategy,
            },
        )
        grab = dict(grab)
        grab.update({
            "external_job_id": job.id,
            "client": snapshot.client,
            "category": snapshot.category,
            "adopted": 1,
            "error": None,
        })
        attached[job.id] = [grab]
        known_ids.add(job.id)
        adopted.append(grab["download_id"])

    observed = []
    updated = []
    completed = []
    failed = []
    cancel_pending = []
    missing = []
    completed_without_path = []
    conflicts = []

    for job_id, grabs in sorted(attached.items()):
        if len(grabs) != 1:
            conflicts.extend(str(grab["download_id"]) for grab in grabs)
            continue
        grab = grabs[0]
        download_id = str(grab["download_id"])
        if grab.get("client") and grab["client"] != snapshot.client:
            conflicts.append(download_id)
            continue
        if grab["status"] == STATUS_CANCEL_PENDING:
            cancel_pending.append(download_id)
            job = jobs.get(job_id)
            if job is None:
                if job_id not in snapshot.lookup_errors:
                    missing.append(download_id)
                continue
            observed.append(download_id)
            if grab.get("last_client_state") != job.state:
                update_grab(conn, download_id, last_client_state=job.state)
                updated.append(download_id)
            continue

        job = jobs.get(job_id)
        if job is None:
            if job_id not in snapshot.lookup_errors:
                missing.append(download_id)
            continue
        observed.append(download_id)

        business_status = _ACTIVE_STATE_TO_STATUS.get(job.state)
        if business_status is not None:
            if (
                grab.get("status") != business_status
                or grab.get("last_client_state") != job.state
                or grab.get("client") != snapshot.client
            ):
                update_grab(
                    conn,
                    download_id,
                    status=business_status,
                    client=snapshot.client,
                    category=grab.get("category") or job.category or snapshot.category,
                    last_client_state=job.state,
                    clear_error=True,
                )
                updated.append(download_id)
            continue

        if job.state == "completed":
            if not job.save_path:
                completed_without_path.append(download_id)
                if grab.get("last_client_state") != job.state:
                    update_grab(conn, download_id, last_client_state=job.state)
                    updated.append(download_id)
                continue
            record_download_completed(
                conn,
                download_id,
                output_path=job.save_path,
                client_state=job.state,
            )
            completed.append(download_id)
            continue

        if job.state == "failed":
            record_grab_outcome(
                conn,
                download_id,
                completed=False,
                error=job.error or "Usenet client reported failure",
                failure_kind="candidate",
            )
            failed.append(download_id)
            continue

        if grab.get("last_client_state") != job.state:
            update_grab(conn, download_id, last_client_state=job.state)
            updated.append(download_id)

    matched_jobs = set(attached)
    unmatched_job_ids = tuple(sorted(job.id for job in unknown_jobs if job.id not in matched_jobs))
    return ReconciliationResult(
        observed=tuple(sorted(set(observed))),
        updated=tuple(sorted(set(updated))),
        adopted=tuple(sorted(set(adopted))),
        completed=tuple(sorted(set(completed))),
        failed=tuple(sorted(set(failed))),
        cancel_pending=tuple(sorted(set(cancel_pending))),
        missing=tuple(sorted(set(missing))),
        completed_without_path=tuple(sorted(set(completed_without_path))),
        ambiguous=tuple(sorted(set(ambiguous + conflicts))),
        unmatched_job_ids=unmatched_job_ids,
    )


def fail_stale_local_submissions(
    conn: Any,
    *,
    created_before: str,
) -> Tuple[str, ...]:
    """Fail pre-process grabs that never reached the external client.

    ``submission_unknown`` rows are excluded because the client may have
    accepted them. Rows created by the current process are excluded by the
    process-start timestamp, so a slow live ``add_nzb`` call cannot race this
    cleanup.
    """
    failed = []
    for grab in open_grabs(conn, "usenet"):
        if (
            grab.get("acquisition_request_id")
            and grab["status"] == "submitting"
            and not grab.get("external_job_id")
            and grab.get("last_client_state") != "submission_unknown"
            and str(grab.get("created_at") or "") < str(created_before)
        ):
            record_grab_outcome(
                conn,
                grab["download_id"],
                completed=False,
                error="Lost before client submission during process restart",
                failure_kind="runtime",
            )
            failed.append(str(grab["download_id"]))
    return tuple(sorted(failed))


class UsenetAcquisitionMonitor:
    """Restart-safe periodic owner of request-bound Usenet client state."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        adapter_getter: Optional[Callable[[], Optional[UsenetClientAdapter]]] = None,
        category_getter: Optional[Callable[[], Any]] = None,
        interval_getter: Optional[Callable[[], Any]] = None,
        process_started_at: Optional[str] = None,
        import_pipeline_runner: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._adapter_getter = adapter_getter or self._default_adapter
        self._category_getter = category_getter or self._default_category
        self._interval_getter = interval_getter or (lambda: 15.0)
        self._import_pipeline_runner = (
            import_pipeline_runner or self._default_import_pipeline)
        self._process_started_at = process_started_at or datetime.now(
            timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._cycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_result: Optional[MonitorRunResult] = None
        self._last_error: Optional[str] = None
        self._last_logged_error: Optional[str] = None
        self._cancel_missing_counts: Dict[str, int] = {}

    @staticmethod
    def _default_adapter() -> Optional[UsenetClientAdapter]:
        from core.usenet_clients import get_active_adapter
        return get_active_adapter()

    def _default_import_pipeline(self) -> Any:
        from core.acquisition.import_pipeline import advance_open_imports
        return advance_open_imports(self._connection_factory)

    def _run_import_pipeline(self) -> Any:
        try:
            return self._import_pipeline_runner()
        except Exception as exc:  # noqa: BLE001 - monitor must stay alive
            logger.warning(
                "Acquisition import pipeline cycle failed: %s",
                redact_sensitive_text(exc),
            )
            return None

    @staticmethod
    def _default_category() -> str:
        from config.settings import config_manager
        return str(config_manager.get(
            "usenet_client.category", "soulsync") or "soulsync")

    def _interval(self) -> float:
        try:
            return max(0.05, float(self._interval_getter()))
        except (TypeError, ValueError):
            return 15.0

    def _read_open_grabs(self) -> Tuple[Tuple[dict, ...], Tuple[str, ...]]:
        conn = self._connection_factory()
        try:
            ensure_acquisition_grabs_schema(conn)
            stale = fail_stale_local_submissions(
                conn,
                created_before=self._process_started_at,
            )
            grabs = tuple(
                grab for grab in open_grabs(conn, "usenet")
                if grab.get("acquisition_request_id")
            )
            conn.commit()
            return grabs, stale
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_snapshot(
        self, snapshot: UsenetClientSnapshot,
    ) -> ReconciliationResult:
        conn = self._connection_factory()
        try:
            result = reconcile_usenet_snapshot(conn, snapshot)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _load_cancel_targets(
        self, download_ids: Iterable[str],
    ) -> Dict[str, str]:
        conn = self._connection_factory()
        try:
            from core.acquisition.grabs import get_grab
            targets = {}
            for download_id in download_ids:
                grab = get_grab(conn, download_id)
                if grab and grab.get("external_job_id"):
                    targets[str(download_id)] = str(grab["external_job_id"])
            return targets
        finally:
            conn.close()

    def _persist_cancelled(self, download_ids: Iterable[str]) -> Tuple[str, ...]:
        ids = tuple(sorted(set(str(item) for item in download_ids)))
        if not ids:
            return ()
        conn = self._connection_factory()
        try:
            for download_id in ids:
                record_grab_cancelled(conn, download_id)
            conn.commit()
            return ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _finish_cancellations(
        self,
        adapter: UsenetClientAdapter,
        result: ReconciliationResult,
    ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        targets = self._load_cancel_targets(result.cancel_pending)
        pending_ids = set(result.cancel_pending)
        missing_ids = pending_ids & set(result.missing)
        for download_id in set(self._cancel_missing_counts) - pending_ids:
            self._cancel_missing_counts.pop(download_id, None)
        confirmed = set()
        failed = set()
        for download_id in result.cancel_pending:
            if download_id in missing_ids:
                misses = self._cancel_missing_counts.get(download_id, 0) + 1
                self._cancel_missing_counts[download_id] = misses
                if misses >= 3:
                    confirmed.add(download_id)
                continue
            self._cancel_missing_counts.pop(download_id, None)
            job_id = targets.get(download_id)
            if not job_id:
                failed.add(download_id)
                continue
            try:
                removed = bool(run_async(
                    adapter.remove(job_id, delete_files=False)))
            except Exception as exc:  # noqa: BLE001 - external client boundary
                logger.warning(
                    "Usenet acquisition cancel remains pending for %s: %s",
                    download_id,
                    redact_sensitive_text(exc),
                )
                removed = False
            if removed:
                confirmed.add(download_id)
            else:
                failed.add(download_id)
        persisted = self._persist_cancelled(confirmed)
        for download_id in persisted:
            self._cancel_missing_counts.pop(download_id, None)
        return persisted, tuple(sorted(failed))

    def run_once(self) -> MonitorRunResult:
        """Run one complete read/network/reconcile cycle synchronously."""
        if not self._cycle_lock.acquire(blocking=False):
            return MonitorRunResult(
                open_grabs=0,
                skipped_reason="cycle_in_progress",
            )
        try:
            imports_result = self._run_import_pipeline()
            grabs, stale = self._read_open_grabs()
            if not grabs:
                result = MonitorRunResult(
                    open_grabs=0,
                    stale_submissions_failed=stale,
                    imports=imports_result,
                    skipped_reason="no_open_grabs",
                )
                self._record_success(result)
                return result

            adapter = self._adapter_getter()
            if adapter is None or not adapter.is_configured():
                result = MonitorRunResult(
                    open_grabs=len(grabs),
                    stale_submissions_failed=stale,
                    imports=imports_result,
                    skipped_reason="client_unconfigured",
                )
                self._record_success(result)
                return result

            category = str(
                self._category_getter() or "soulsync",
            ).strip() or "soulsync"
            known_ids = tuple(
                str(grab["external_job_id"])
                for grab in grabs if grab.get("external_job_id")
            )
            snapshot = run_async(collect_usenet_snapshot(
                adapter,
                category,
                known_job_ids=known_ids,
            ))
            reconciliation = self._apply_snapshot(snapshot)
            cancelled, cancel_failed = self._finish_cancellations(
                adapter, reconciliation)
            result = MonitorRunResult(
                open_grabs=len(grabs),
                stale_submissions_failed=stale,
                reconciliation=reconciliation,
                cancelled=cancelled,
                cancel_failed=cancel_failed,
                imports=imports_result,
            )
            self._record_success(result)
            if reconciliation.ambiguous:
                logger.warning(
                    "Usenet acquisition adoption is ambiguous for %d grab(s)",
                    len(reconciliation.ambiguous),
                )
            return result
        except Exception as exc:
            safe_error = redact_sensitive_text(exc)
            with self._state_lock:
                self._last_error = safe_error
            if safe_error != self._last_logged_error:
                logger.warning(
                    "Usenet acquisition monitor cycle failed: %s", safe_error)
                self._last_logged_error = safe_error
            raise
        finally:
            self._cycle_lock.release()

    def _record_success(self, result: MonitorRunResult) -> None:
        with self._state_lock:
            self._last_result = result
            self._last_error = None
        self._last_logged_error = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - run_once logs sanitized detail
                logger.debug("Usenet acquisition cycle will retry after its interval")
            self._wake_event.wait(self._interval())
            self._wake_event.clear()
        with self._state_lock:
            self._running = False

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="UsenetAcquisitionMonitor",
            )
            self._thread.start()
        logger.info("Usenet acquisition monitor started")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        still_alive = bool(thread is not None and thread.is_alive())
        with self._state_lock:
            self._running = still_alive
            self._thread = thread if still_alive else None
        if still_alive:
            logger.warning(
                "Usenet acquisition monitor is still leaving client I/O")
        else:
            logger.info("Usenet acquisition monitor stopped")

    def notify_submission(self, *_args: Any) -> None:
        """Wake the monitor after a durable external submission."""
        self._wake_event.set()

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "running": self._running,
                "last_error": self._last_error,
                "last_result": self._last_result,
            }


__all__ = [
    "MonitorRunResult",
    "ReconciliationResult",
    "UsenetAcquisitionMonitor",
    "UsenetClientSnapshot",
    "UsenetJobSnapshot",
    "collect_usenet_snapshot",
    "fail_stale_local_submissions",
    "reconcile_usenet_snapshot",
]
