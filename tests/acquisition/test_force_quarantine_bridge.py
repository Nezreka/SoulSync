import sqlite3

from core.acquisition import ensure_acquisition_schema
from core.acquisition.eligibility_gate import CandidateDecision, DecisionReason
from core.acquisition.decisions import record_decision
from core.acquisition.history import list_history_events
from core.acquisition.pipeline_callback import (
    notify_force_quarantine_auto_approved,
)
from tests.acquisition.test_pipeline_callback import _importing_record


def _factory(path):
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return connect


def _seed_forced_import(path, *, forced=True):
    factory = _factory(path)
    conn = factory()
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)
    decision = CandidateDecision(
        request_id=request.id,
        candidate_id=importing.candidate_id,
        accepted=True,
        forced=forced,
        reasons=(
            DecisionReason(
                "quality",
                "quality_not_allowed",
                "rejection",
                "Candidate matches no allowed quality target",
                True,
            ),
            DecisionReason(
                "cutoff",
                "below_cutoff",
                "warning",
                "Candidate remains below cutoff",
                True,
            ),
        ),
        quality_rank=1,
        cutoff_delta=1,
        custom_format_score=0,
        edition_match_confidence=1.0,
        sort_key=(1.0,),
        engine_version="test",
    )
    run = record_decision(conn, decision)
    conn.execute(
        "UPDATE acquisition_grabs SET decision_run_id=? WHERE download_id=?",
        (run.id, importing.download_id),
    )
    conn.commit()
    conn.close()
    context = {
        "_acquisition_import_id": importing.id,
        "_acquisition_relative_path": "01.flac",
        "_acquisition_track_id": 101,
    }
    return factory, importing, request, context


def test_exact_forced_rejection_auto_approves_and_audits(tmp_path):
    factory, _importing, request, context = _seed_forced_import(
        tmp_path / "exact.sqlite")

    assert notify_force_quarantine_auto_approved(
        context,
        reason_code="quality_not_allowed",
        trigger="quality",
        reason="Downloaded file misses every configured target",
        connection_factory=factory,
    ) is True

    conn = factory()
    event = list_history_events(conn, request_id=request.id)[-1]
    assert event.event_type == "force_quarantine_auto_approved"
    assert event.reason_code == "quality_not_allowed"
    assert event.payload == {
        "import_id": context["_acquisition_import_id"],
        "relative_path": "01.flac",
        "track_id": 101,
        "trigger": "quality",
    }
    conn.close()


def test_different_reason_is_not_approved(tmp_path):
    factory, _importing, request, context = _seed_forced_import(
        tmp_path / "different.sqlite")

    assert notify_force_quarantine_auto_approved(
        context,
        reason_code="acoustid_mismatch",
        trigger="acoustid",
        reason="Fingerprint mismatch",
        connection_factory=factory,
    ) is False

    conn = factory()
    assert all(
        event.event_type != "force_quarantine_auto_approved"
        for event in list_history_events(conn, request_id=request.id)
    )
    conn.close()


def test_non_forced_decision_cannot_auto_approve(tmp_path):
    factory, _importing, _request, context = _seed_forced_import(
        tmp_path / "normal.sqlite", forced=False)

    assert notify_force_quarantine_auto_approved(
        context,
        reason_code="quality_not_allowed",
        trigger="quality",
        reason="Downloaded file misses every configured target",
        connection_factory=factory,
    ) is False


def test_context_must_match_the_persisted_import_plan(tmp_path):
    factory, _importing, _request, context = _seed_forced_import(
        tmp_path / "plan.sqlite")
    context["_acquisition_track_id"] = 999

    assert notify_force_quarantine_auto_approved(
        context,
        reason_code="quality_not_allowed",
        trigger="quality",
        reason="Downloaded file misses every configured target",
        connection_factory=factory,
    ) is False
