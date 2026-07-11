"""Transactional orchestration across requests, candidates, decisions and grabs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.acquisition.candidates import (
    ReleaseCandidate,
    list_request_candidates,
    resolve_candidate,
)
from core.acquisition.decision_engine import (
    CandidateDecision,
    CatalogContext,
    DecisionEngine,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.decisions import PersistedDecisionRun, record_decision
from core.acquisition.grabs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SUBMITTING,
    get_grab,
    record_grab,
    update_grab,
)
from core.acquisition.requests import (
    AcquisitionRequest,
    get_request,
    transition_request,
)


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
        decision = DecisionEngine.evaluate(
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
            error="No candidate passed the Decision Engine",
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
    decision = DecisionEngine.evaluate(
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
        raise ValueError("candidate was rejected by the Decision Engine")

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
    return PreparedGrab(request, candidate, run, download_id)


def record_grab_outcome(
    conn: Any,
    download_id: str,
    *,
    completed: bool,
    error: Optional[str] = None,
    output_path: Optional[str] = None,
) -> AcquisitionRequest:
    """Persist a terminal business outcome on grab and owning request."""
    grab = get_grab(conn, download_id)
    if grab is None or not grab.get("acquisition_request_id"):
        raise ValueError("download is not linked to an acquisition request")
    status = STATUS_COMPLETED if completed else STATUS_FAILED
    update_grab(
        conn, download_id, status=status, error=error, output_path=output_path)
    return transition_request(
        conn,
        grab["acquisition_request_id"],
        "completed" if completed else "failed",
        expected_status="grabbing",
        error=error,
    )


__all__ = [
    "EvaluatedCandidate",
    "PreparedGrab",
    "SearchEvaluation",
    "evaluate_request_candidates",
    "prepare_candidate_grab",
    "record_grab_outcome",
]
