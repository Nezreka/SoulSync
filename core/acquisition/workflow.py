"""Transactional orchestration across requests, candidates, decisions and grabs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.acquisition.blocklist import block_candidate
from core.acquisition.candidates import (
    ReleaseCandidate,
    list_request_candidates,
    redact_sensitive_text,
    resolve_candidate,
)
from core.acquisition.eligibility_gate import (
    CandidateDecision,
    CatalogContext,
    EligibilityGate,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.decisions import PersistedDecisionRun, record_decision
from core.acquisition.grabs import (
    STATUS_CANCELLED,
    STATUS_CANCEL_PENDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SUBMITTING,
    get_grab,
    record_grab,
    update_grab,
)
from core.acquisition.history import record_history_event
from core.acquisition.requests import (
    AcquisitionRequest,
    get_request,
    transition_request,
)


FAILURE_KINDS = frozenset({"candidate", "client", "runtime"})


@dataclass(frozen=True)
class EvaluatedCandidate:
    candidate: ReleaseCandidate
    decision_run: PersistedDecisionRun

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            **self.candidate.to_public_dict(),
            "decision_run_id": self.decision_run.id,
            "decision": self.decision_run.decision.to_public_dict(),
        }


@dataclass(frozen=True)
class SearchEvaluation:
    request: AcquisitionRequest
    candidates: Tuple[EvaluatedCandidate, ...]
    selected: Optional[EvaluatedCandidate]

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "candidates": [item.to_public_dict() for item in self.candidates],
            "selected_candidate_id": (
                self.selected.candidate.id if self.selected else None),
        }


@dataclass(frozen=True)
class PreparedGrab:
    request: AcquisitionRequest
    candidate: ReleaseCandidate
    decision_run: PersistedDecisionRun
    download_id: str

    @property
    def server_ref(self) -> str:
        """Internal adapter reference. Never include this in an API response."""
        return self.candidate.server_ref

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request.id,
            "candidate_id": self.candidate.id,
            "decision_run_id": self.decision_run.id,
            "download_id": self.download_id,
            "decision": self.decision_run.decision.to_public_dict(),
        }


def _request_for_evaluation(conn: Any, request_id: str) -> AcquisitionRequest:
    request = get_request(conn, request_id)
    if request is None:
        raise KeyError(f"acquisition request not found: {request_id}")
    if request.status not in {"searching", "candidates_ready"}:
        raise ValueError(f"request cannot be evaluated while {request.status}")
    return request


def evaluate_request_candidates(
    conn: Any,
    request_id: str,
    *,
    catalog: CatalogContext,
    runtime: RuntimeContext,
    policy: EffectivePolicy,
    automatic: bool,
    now: Optional[float] = None,
) -> SearchEvaluation:
    """Evaluate every live candidate through one shared Manual/Auto path."""
    request = _request_for_evaluation(conn, request_id)
    candidates = list_request_candidates(conn, request.id, now=now)
    evaluated = []
    for candidate in candidates:
        decision = EligibilityGate.evaluate(
            request, candidate, catalog, runtime, policy, now=now)
        evaluated.append(EvaluatedCandidate(
            candidate, record_decision(conn, decision)))
    evaluated.sort(key=lambda item: item.decision_run.decision.sort_key)
    accepted = [
        item for item in evaluated if item.decision_run.decision.accepted]
    selected = accepted[0] if automatic and accepted else None

    if request.status == "searching":
        request = transition_request(
            conn,
            request.id,
            "candidates_ready" if candidates else "no_candidate",
            expected_status="searching",
            error=None if candidates else "No candidates returned by configured sources",
        )
    if automatic and not selected and request.status == "candidates_ready":
        request = transition_request(
            conn,
            request.id,
            "no_candidate",
            expected_status="candidates_ready",
            error="No candidate passed the Entity Eligibility Gate",
        )
    record_history_event(
        conn,
        "candidates_evaluated",
        request_id=request.id,
        candidate_id=selected.candidate.id if selected else None,
        payload={
            "automatic": bool(automatic),
            "candidate_count": len(evaluated),
            "accepted_count": len(accepted),
            "selected_candidate_id": selected.candidate.id if selected else None,
            "decision_run_ids": [item.decision_run.id for item in evaluated],
        },
    )
    if request.status == "no_candidate":
        record_history_event(
            conn,
            "no_candidate",
            request_id=request.id,
            reason_code=("no_results" if not candidates else "all_rejected"),
            message=request.last_error,
            payload={"candidate_count": len(candidates)},
        )
    return SearchEvaluation(request, tuple(evaluated), selected)


def prepare_candidate_grab(
    conn: Any,
    request_id: str,
    candidate_id: str,
    *,
    download_id: str,
    catalog: CatalogContext,
    runtime: RuntimeContext,
    policy: EffectivePolicy,
    profile_id: int = 1,
    now: Optional[float] = None,
    force: bool = False,
    is_admin: bool = False,
) -> PreparedGrab:
    """Re-evaluate and persist the exact candidate immediately before submit."""
    existing = get_grab(conn, download_id)
    if existing is not None:
        if (
            existing.get("acquisition_request_id") != str(request_id)
            or existing.get("release_candidate_id") != str(candidate_id)
        ):
            raise ValueError("download_id already belongs to a different acquisition")
        request = get_request(conn, request_id)
        candidate = resolve_candidate(
            conn, candidate_id, request_id=request_id,
            profile_id=profile_id, now=now)
        if request is None or candidate is None:
            raise ValueError("existing acquisition grab is no longer resolvable")
        from core.acquisition.decisions import get_decision_run
        run = get_decision_run(conn, existing["decision_run_id"])
        if run is None:
            raise ValueError("existing acquisition grab lost its decision run")
        return PreparedGrab(request, candidate, run, download_id)

    request = get_request(conn, request_id)
    if request is None or request.status != "candidates_ready":
        raise ValueError("request must be candidates_ready before a grab")
    candidate = resolve_candidate(
        conn, candidate_id, request_id=request.id,
        profile_id=profile_id, now=now)
    if candidate is None:
        raise ValueError("candidate is unknown, expired, or not owned by this request")
    decision = EligibilityGate.evaluate(
        request,
        candidate,
        catalog,
        runtime,
        policy,
        now=now,
        force=force,
        is_admin=is_admin,
    )
    run = record_decision(conn, decision)
    if not decision.accepted:
        raise ValueError("candidate was rejected by the Entity Eligibility Gate")

    request = transition_request(
        conn, request.id, "grabbing", expected_status="candidates_ready")
    record_grab(
        conn,
        download_id,
        candidate.source,
        title=candidate.title,
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
        decision_run_id=run.id,
        context={
            "acquisition_request_id": request.id,
            "release_candidate_id": candidate.id,
            "decision_run_id": run.id,
            "quality_profile_id": request.quality_profile_id,
            "scope": request.scope,
            "entity_id": request.entity_id,
        },
        status=STATUS_SUBMITTING,
    )
    if decision.forced:
        record_history_event(
            conn,
            "force_grab",
            request_id=request.id,
            candidate_id=candidate.id,
            download_id=download_id,
            reason_code="manual_policy_override",
            payload={
                "decision_run_id": run.id,
                "overridden_reasons": [
                    reason.code for reason in decision.rejections],
            },
        )
    record_history_event(
        conn,
        "grab_prepared",
        request_id=request.id,
        candidate_id=candidate.id,
        download_id=download_id,
        payload={
            "decision_run_id": run.id,
            "source": candidate.source,
            "forced": decision.forced,
        },
    )
    return PreparedGrab(request, candidate, run, download_id)


def record_grab_outcome(
    conn: Any,
    download_id: str,
    *,
    completed: bool,
    error: Optional[str] = None,
    output_path: Optional[str] = None,
    failure_kind: Optional[str] = None,
) -> AcquisitionRequest:
    """Persist a terminal business outcome on grab and owning request."""
    grab = get_grab(conn, download_id)
    if grab is None or not grab.get("acquisition_request_id"):
        raise ValueError("download is not linked to an acquisition request")
    if completed and failure_kind is not None:
        raise ValueError("completed grabs cannot declare a failure_kind")
    if not completed:
        failure_kind = str(failure_kind or "").strip().lower()
        if failure_kind not in FAILURE_KINDS:
            raise ValueError(
                "failed grabs require failure_kind candidate|client|runtime")
    safe_error = redact_sensitive_text(error) if error else None
    status = STATUS_COMPLETED if completed else STATUS_FAILED
    update_grab(
        conn, download_id, status=status, error=safe_error, output_path=output_path)
    request = transition_request(
        conn,
        grab["acquisition_request_id"],
        "completed" if completed else "failed",
        expected_status="grabbing",
        error=safe_error,
    )
    candidate_id = grab.get("release_candidate_id")
    record_history_event(
        conn,
        "grab_completed" if completed else "grab_failed",
        request_id=request.id,
        candidate_id=candidate_id,
        download_id=download_id,
        reason_code=None if completed else f"{failure_kind}_failure",
        message=safe_error,
        payload={
            "failure_kind": failure_kind,
            "has_output_path": bool(output_path),
        },
    )
    if not completed and failure_kind in {"candidate", "client"}:
        if not candidate_id:
            raise ValueError("failed acquisition grab has no release candidate")
        block_candidate(
            conn,
            candidate_id,
            reason_code=f"{failure_kind}_failure",
            message=safe_error,
            download_id=download_id,
        )
    return request


def record_grab_cancelled(
    conn: Any,
    download_id: str,
    *,
    client_state: str = "removed",
) -> AcquisitionRequest:
    """Confirm cancellation only after the external job is absent.

    Physical file deletion is intentionally separate. The caller must remove
    the client job with ``delete_files=False`` or establish that it is already
    absent before entering this transaction.
    """
    grab = get_grab(conn, download_id)
    if grab is None or not grab.get("acquisition_request_id"):
        raise ValueError("download is not linked to an acquisition request")
    request = get_request(conn, grab["acquisition_request_id"])
    if request is None:
        raise ValueError("acquisition request no longer exists")
    if grab["status"] == STATUS_CANCELLED:
        return request
    if grab["status"] != STATUS_CANCEL_PENDING:
        raise ValueError(f"grab cannot confirm cancellation while {grab['status']}")
    if request.status != "grabbing":
        raise ValueError(f"request cannot confirm cancellation while {request.status}")

    update_grab(
        conn,
        download_id,
        status=STATUS_CANCELLED,
        last_client_state=str(client_state or "removed"),
        clear_error=True,
    )
    request = transition_request(
        conn,
        request.id,
        "cancelled",
        expected_status="grabbing",
    )
    record_history_event(
        conn,
        "cancelled",
        request_id=request.id,
        candidate_id=grab.get("release_candidate_id"),
        download_id=download_id,
        reason_code="client_job_removed",
        payload={"delete_files": False},
    )
    return request


def retry_acquisition_request(conn: Any, request_id: str) -> AcquisitionRequest:
    """Begin another search attempt for a retryable terminal search outcome."""
    current = get_request(conn, request_id)
    if current is None:
        raise KeyError(f"acquisition request not found: {request_id}")
    if current.status not in {"failed", "no_candidate"}:
        raise ValueError(
            f"request cannot be retried while {current.status}")
    retried = transition_request(
        conn,
        current.id,
        "searching",
        expected_status=current.status,
        increment_attempts=True,
    )
    record_history_event(
        conn,
        "retry_started",
        request_id=retried.id,
        reason_code=f"retry_after_{current.status}",
        payload={
            "previous_status": current.status,
            "attempt": retried.attempts,
        },
    )
    return retried


__all__ = [
    "EvaluatedCandidate",
    "FAILURE_KINDS",
    "PreparedGrab",
    "SearchEvaluation",
    "evaluate_request_candidates",
    "prepare_candidate_grab",
    "record_grab_cancelled",
    "record_grab_outcome",
    "retry_acquisition_request",
]
