"""External-client submission boundary for prepared acquisition grabs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from core.settings import config_manager
from core.acquisition.candidates import redact_sensitive_text
from core.acquisition.grabs import (
    STATUS_QUEUED,
    STATUS_SUBMITTING,
    get_grab,
    update_grab,
)
from core.acquisition.history import record_history_event
from core.acquisition.workflow import FAILURE_KINDS, PreparedGrab
from core.download_plugins.candidate_store import (
    CandidateStore,
    candidate_binding,
    get_candidate_store,
)


class SubmissionError(RuntimeError):
    """Known external submission failure with retry/uncertainty semantics."""

    def __init__(
        self, message: str, *, failure_kind: str = "runtime",
        uncertain: bool = False,
    ) -> None:
        super().__init__(redact_sensitive_text(message))
        if failure_kind not in FAILURE_KINDS:
            raise ValueError(f"invalid submission failure_kind: {failure_kind}")
        self.failure_kind = failure_kind
        self.uncertain = bool(uncertain)


@dataclass(frozen=True)
class ExternalSubmission:
    source: str
    external_job_id: str
    client: str
    category: str

    def __post_init__(self) -> None:
        for name in ("source", "external_job_id", "client", "category"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"external submission {name} is required")
            object.__setattr__(
                self, name, value.lower() if name == "source" else value)


class AcquisitionSubmissionAdapter(Protocol):
    source: str

    async def submit(self, prepared: PreparedGrab) -> ExternalSubmission: ...

    def start_monitor(
        self, prepared: PreparedGrab, submission: ExternalSubmission,
    ) -> None: ...


class UsenetSubmissionAdapter:
    """Submit an opaque Prowlarr candidate to the configured Usenet client."""

    source = "usenet"

    def __init__(
        self,
        *,
        client_getter: Optional[Callable[[], Any]] = None,
        candidate_store: Optional[CandidateStore] = None,
        category_getter: Optional[Callable[[], Any]] = None,
        monitor_callback: Optional[Callable[[PreparedGrab, ExternalSubmission], None]] = None,
    ) -> None:
        self._client_getter = client_getter or self._default_client
        self._candidate_store = candidate_store
        self._category_getter = category_getter or (
            lambda: config_manager.get("usenet_client.category", "soulsync"))
        self._monitor_callback = monitor_callback

    @staticmethod
    def _default_client():
        from core.usenet_clients import get_active_adapter
        return get_active_adapter()

    async def submit(self, prepared: PreparedGrab) -> ExternalSubmission:
        if prepared.candidate.source != self.source:
            raise SubmissionError("Usenet submitter received a non-Usenet candidate")
        client = self._client_getter()
        if client is None or not client.is_configured():
            raise SubmissionError("No Usenet download client is configured")
        store = self._candidate_store or get_candidate_store()
        with candidate_binding(prepared.request.profile_id):
            nzb_url = store.resolve(prepared.server_ref)
        if not nzb_url:
            raise SubmissionError(
                "Candidate download reference is unavailable; search again")
        category = str(self._category_getter() or "soulsync").strip() or "soulsync"
        try:
            external_job_id = await client.add_nzb(nzb_url, category=category)
        except Exception as exc:  # noqa: BLE001 - timeout may be accepted remotely
            raise SubmissionError(
                f"Usenet client submission outcome is unknown: {exc}",
                uncertain=True,
            ) from exc
        if not external_job_id:
            raise SubmissionError("Usenet client refused the acquisition submission")
        return ExternalSubmission(
            source=self.source,
            external_job_id=str(external_job_id),
            client=client.__class__.__name__,
            category=category,
        )

    def start_monitor(
        self, prepared: PreparedGrab, submission: ExternalSubmission,
    ) -> None:
        if self._monitor_callback:
            self._monitor_callback(prepared, submission)


def record_external_submission(
    conn: Any,
    prepared: PreparedGrab,
    submission: ExternalSubmission,
) -> dict:
    """Persist client correlation after successful external submission."""
    if submission.source != prepared.candidate.source:
        raise ValueError("submission source does not match prepared candidate")
    grab = get_grab(conn, prepared.download_id)
    if grab is None:
        raise ValueError("prepared acquisition grab no longer exists")
    if (
        grab.get("acquisition_request_id") != prepared.request.id
        or grab.get("release_candidate_id") != prepared.candidate.id
    ):
        raise ValueError("prepared grab correlation does not match persisted grab")
    if grab["status"] not in {STATUS_SUBMITTING, STATUS_QUEUED}:
        raise ValueError(f"grab cannot record submission while {grab['status']}")
    if grab.get("external_job_id"):
        if grab["external_job_id"] != submission.external_job_id:
            raise ValueError("grab already belongs to a different external job")
        return grab
    update_grab(
        conn,
        prepared.download_id,
        status=STATUS_QUEUED,
        external_job_id=submission.external_job_id,
        client=submission.client,
        category=submission.category,
        last_client_state="queued",
    )
    record_history_event(
        conn,
        "grab_submitted",
        request_id=prepared.request.id,
        candidate_id=prepared.candidate.id,
        download_id=prepared.download_id,
        payload={
            "source": submission.source,
            "client": submission.client,
            "category": submission.category,
        },
    )
    updated = get_grab(conn, prepared.download_id)
    if updated is None:  # pragma: no cover - row cannot disappear here
        raise RuntimeError("submitted acquisition grab disappeared")
    return updated


#: Client states that mean "the external client may already hold this grab".
#: A restart may not declare these locally aborted: only a row with no such
#: marker is provably still ours (ACQ-03).
SUBMISSION_IN_FLIGHT_STATES = frozenset({"submission_started", "submission_unknown"})


def record_submission_started(conn: Any, prepared: PreparedGrab) -> dict:
    """Mark a grab as handed to the external client, BEFORE the network call.

    ACQ-03: ``submission_unknown`` was only ever written from an exception
    handler or a caught persistence failure. A process that dies inside
    ``add_nzb`` — or after it returned but before ``record_external_submission``
    commits — runs neither. The row was left ``submitting`` with no marker and
    no ``external_job_id``, which on the next start is exactly the shape
    ``fail_stale_local_submissions`` declares *certainly* aborted: the grab and
    its request went terminally failed, the grab left the open set, and the
    adoption that would have re-attached the running transfer never considered
    it. The download completed with nobody waiting for it, and a fresh request
    could start the same transfer a second time.

    Committing this marker first inverts the default: absence of a marker is
    what proves nothing was sent. Caller commits.
    """
    grab = get_grab(conn, prepared.download_id)
    if grab is None or grab["status"] != STATUS_SUBMITTING:
        raise ValueError("submission start requires a submitting grab")
    if (
        grab.get("acquisition_request_id") != prepared.request.id
        or grab.get("release_candidate_id") != prepared.candidate.id
    ):
        raise ValueError("prepared grab correlation does not match persisted grab")
    update_grab(
        conn,
        prepared.download_id,
        last_client_state="submission_started",
    )
    record_history_event(
        conn,
        "grab_submission_started",
        request_id=prepared.request.id,
        candidate_id=prepared.candidate.id,
        download_id=prepared.download_id,
        reason_code="client_submission_started",
    )
    updated = get_grab(conn, prepared.download_id)
    if updated is None:  # pragma: no cover
        raise RuntimeError("acquisition grab disappeared while starting submission")
    return updated


def record_uncertain_submission(
    conn: Any,
    prepared: PreparedGrab,
    error: str,
) -> dict:
    """Keep `submitting` when the client may have accepted a timed-out call."""
    safe_error = redact_sensitive_text(error)
    grab = get_grab(conn, prepared.download_id)
    if grab is None or grab["status"] != STATUS_SUBMITTING:
        raise ValueError("uncertain submission requires a submitting grab")
    if (
        grab.get("acquisition_request_id") != prepared.request.id
        or grab.get("release_candidate_id") != prepared.candidate.id
    ):
        raise ValueError("prepared grab correlation does not match persisted grab")
    update_grab(
        conn,
        prepared.download_id,
        error=safe_error,
        last_client_state="submission_unknown",
    )
    record_history_event(
        conn,
        "grab_submission_uncertain",
        request_id=prepared.request.id,
        candidate_id=prepared.candidate.id,
        download_id=prepared.download_id,
        reason_code="client_submission_unknown",
        message=safe_error,
    )
    updated = get_grab(conn, prepared.download_id)
    if updated is None:  # pragma: no cover
        raise RuntimeError("uncertain acquisition grab disappeared")
    return updated


__all__ = [
    "AcquisitionSubmissionAdapter",
    "ExternalSubmission",
    "SubmissionError",
    "UsenetSubmissionAdapter",
    "SUBMISSION_IN_FLIGHT_STATES",
    "record_external_submission",
    "record_submission_started",
    "record_uncertain_submission",
]
