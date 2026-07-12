"""Central Usenet client snapshot and acquisition reconciliation.

Network reads happen in :func:`collect_usenet_snapshot`, before a database
transaction is opened. Reconciliation persists business transitions only; live
progress, speed, ETA and byte counters remain owned by SABnzbd/NZBGet.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

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
from core.acquisition.workflow import record_grab_outcome
from core.usenet_clients.base import UsenetClientAdapter, UsenetStatus


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
        job = jobs.get(job_id)
        if job is None:
            if job_id not in snapshot.lookup_errors:
                missing.append(download_id)
            continue
        observed.append(download_id)

        if grab["status"] == STATUS_CANCEL_PENDING:
            cancel_pending.append(download_id)
            if grab.get("last_client_state") != job.state:
                update_grab(conn, download_id, last_client_state=job.state)
                updated.append(download_id)
            continue

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


__all__ = [
    "ReconciliationResult",
    "UsenetClientSnapshot",
    "UsenetJobSnapshot",
    "collect_usenet_snapshot",
    "reconcile_usenet_snapshot",
]
