"""Restart-safe Quarantine -> Staging lifecycle correlation."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.grabs import record_grab
from core.acquisition.imports import (
    get_import,
    record_download_completed,
    record_inventory_result,
    record_matching_result,
)
from core.acquisition.recovery import (
    attach_recovered_staging_context,
    get_quarantine_recovery,
    prepare_quarantine_recovery,
    record_recovered_staging_result,
    recover_quarantine_entry_to_staging,
)
from core.acquisition.requests import create_request, transition_request
from core.imports.quarantine import plan_recover_to_staging


def _factory(path):
    def _open():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return _open


def _native_import(conn):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="recording",
        entity_id=77,
        quality_profile_id=1,
        trigger="manual",
        idempotency_key="recovery-native",
    )
    transition_request(conn, request.id, "searching")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="soulseek",
        protocol="p2p",
        content_scope="recording",
        server_ref="recovery-ref",
        title="Recovered Track",
    )
    transition_request(conn, request.id, "candidates_ready")
    transition_request(conn, request.id, "grabbing")
    record_grab(
        conn,
        "download-recovery",
        "soulseek",
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
        status="downloading",
    )
    record = record_download_completed(
        conn, "download-recovery", output_path="/download/release",
    )
    record_inventory_result(
        conn,
        record.id,
        [{"relative_path": "track.flac", "size": 10}],
        resolved_path="/download/release",
    )
    record_matching_result(
        conn,
        record.id,
        [{"relative_path": "track.flac", "track_id": 55}],
        [],
        decision="import_ready",
    )
    conn.commit()
    return record.id


def _quarantine_entry(quarantine, import_id):
    entry_id = "20260718_120000_track"
    source = quarantine / f"{entry_id}.flac.quarantined"
    source.write_bytes(b"audio")
    sidecar = quarantine / f"{entry_id}.json"
    sidecar.write_text(json.dumps({
        "original_filename": "track.flac",
        "trigger": "acoustid",
        "context": {
            "_acquisition_import_id": import_id,
            "_acquisition_relative_path": "track.flac",
            "_acquisition_track_id": 55,
        },
    }))
    return entry_id, source, sidecar


def test_recovery_journals_move_and_parks_acquisition_import(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    factory = _factory(db_path)
    conn = factory()
    ensure_acquisition_schema(conn)
    import_id = _native_import(conn)
    conn.close()
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "staging"
    entry_id, source, sidecar = _quarantine_entry(quarantine, import_id)

    recovery = recover_quarantine_entry_to_staging(
        factory,
        quarantine_dir=str(quarantine),
        staging_dir=str(staging),
        entry_id=entry_id,
    )

    assert recovery is not None
    assert recovery.status == "recovered"
    assert os.path.isfile(recovery.staged_path)
    assert not source.exists()
    assert not sidecar.exists()
    conn = factory()
    try:
        assert get_import(conn, import_id).status == "recovered_to_staging"
        event = conn.execute(
            "SELECT event_type, reason_code FROM acquisition_history "
            "WHERE download_id='download-recovery' "
            "AND event_type='recovered_to_staging'"
        ).fetchone()
        assert dict(event) == {
            "event_type": "recovered_to_staging",
            "reason_code": "manual_quarantine_recovery",
        }
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM acquisition_history "
            "WHERE download_id='download-recovery' "
            "AND event_type='recovered_to_staging'"
        ).fetchone()[0])
        assert payload["entry_id"] == entry_id
        assert payload["previous_path"]
        assert payload["staged_path"] == recovery.staged_path
    finally:
        conn.close()


def test_recovered_manual_import_restores_markers_and_closes_move_journal(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    factory = _factory(db_path)
    conn = factory()
    ensure_acquisition_schema(conn)
    import_id = _native_import(conn)
    conn.close()
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    entry_id, _, _ = _quarantine_entry(quarantine, import_id)
    recovery = recover_quarantine_entry_to_staging(
        factory,
        quarantine_dir=str(quarantine),
        staging_dir=str(tmp_path / "staging"),
        entry_id=entry_id,
    )

    context = attach_recovered_staging_context(
        factory, recovery.staged_path, {"track_info": {"name": "Recovered Track"}},
    )

    assert context["_acquisition_import_id"] == import_id
    assert context["_acquisition_relative_path"] == "track.flac"
    assert context["_acquisition_track_id"] == 55
    assert context["track_info"]["_acquisition_import_id"] == import_id
    assert context["_quarantine_recovery_entry_id"] == entry_id
    conn = factory()
    try:
        assert get_import(conn, import_id).status == "importing"
        assert get_quarantine_recovery(conn, entry_id).status == "reimporting"
    finally:
        conn.close()

    assert record_recovered_staging_result(factory, context, success=True)
    conn = factory()
    try:
        assert get_quarantine_recovery(conn, entry_id).status == "completed"
    finally:
        conn.close()


def test_prepared_move_recovers_after_crash_between_disk_and_db_commit(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    factory = _factory(db_path)
    conn = factory()
    ensure_acquisition_schema(conn)
    import_id = _native_import(conn)
    conn.close()
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "staging"
    entry_id, source, sidecar = _quarantine_entry(quarantine, import_id)
    plan = plan_recover_to_staging(str(quarantine), str(staging), entry_id)
    assert plan is not None
    conn = factory()
    prepare_quarantine_recovery(
        conn,
        entry_id=plan.entry_id,
        source_path=plan.source_path,
        sidecar_path=plan.sidecar_path,
        staged_path=plan.target_path,
        context=plan.context,
    )
    conn.commit()
    conn.close()
    shutil.move(str(source), plan.target_path)

    recovered = recover_quarantine_entry_to_staging(
        factory,
        quarantine_dir=str(quarantine),
        staging_dir=str(staging),
        entry_id=entry_id,
    )

    assert recovered is not None
    assert recovered.status == "recovered"
    assert os.path.isfile(plan.target_path)
    assert not sidecar.exists()


def test_retry_after_database_commit_removes_leftover_sidecar(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    factory = _factory(db_path)
    conn = factory()
    ensure_acquisition_schema(conn)
    import_id = _native_import(conn)
    conn.close()
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "staging"
    entry_id, _, sidecar = _quarantine_entry(quarantine, import_id)
    plan = plan_recover_to_staging(str(quarantine), str(staging), entry_id)
    conn = factory()
    prepare_quarantine_recovery(
        conn,
        entry_id=plan.entry_id,
        source_path=plan.source_path,
        sidecar_path=plan.sidecar_path,
        staged_path=plan.target_path,
        context=plan.context,
    )
    conn.commit()
    conn.close()
    from core.imports.quarantine import execute_staging_recovery
    from core.acquisition.recovery import finalize_quarantine_recovery

    assert execute_staging_recovery(plan)
    conn = factory()
    finalize_quarantine_recovery(conn, entry_id)
    conn.commit()
    conn.close()
    assert sidecar.exists()  # simulate the crash before unlink

    recovered = recover_quarantine_entry_to_staging(
        factory,
        quarantine_dir=str(quarantine),
        staging_dir=str(staging),
        entry_id=entry_id,
    )

    assert recovered.status == "recovered"
    assert not sidecar.exists()


def test_failed_reimport_with_staging_file_remains_retryable(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    factory = _factory(db_path)
    conn = factory()
    ensure_acquisition_schema(conn)
    import_id = _native_import(conn)
    conn.close()
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    entry_id, _, _ = _quarantine_entry(quarantine, import_id)
    recovery = recover_quarantine_entry_to_staging(
        factory,
        quarantine_dir=str(quarantine),
        staging_dir=str(tmp_path / "staging"),
        entry_id=entry_id,
    )
    context = attach_recovered_staging_context(factory, recovery.staged_path, {})

    assert record_recovered_staging_result(
        factory, context, success=False, error="temporary pipeline error",
    )

    conn = factory()
    try:
        current = get_quarantine_recovery(conn, entry_id)
        assert current.status == "recovered"
        assert current.error == "temporary pipeline error"
    finally:
        conn.close()
    retry_context = attach_recovered_staging_context(
        factory, recovery.staged_path, {},
    )
    assert retry_context["_quarantine_recovery_entry_id"] == entry_id


def test_old_import_status_constraint_is_widened_without_data_loss():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE acquisition_imports (
            id TEXT PRIMARY KEY, download_id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL, candidate_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending', output_path TEXT NOT NULL,
            resolved_path TEXT, expected_scope TEXT NOT NULL,
            expected_entity_id INTEGER NOT NULL, inventory_json TEXT DEFAULT '[]',
            matches_json TEXT DEFAULT '[]', rejections_json TEXT DEFAULT '[]',
            result_json TEXT DEFAULT '{}', attempts INTEGER DEFAULT 0, error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP,
            CHECK(status IN ('pending','matching','needs_review','importing','completed','failed'))
        );
        INSERT INTO acquisition_imports(
            id,download_id,request_id,status,output_path,expected_scope,expected_entity_id
        ) VALUES('i1','d1','r1','importing','/x','recording',1);
        """
    )
    from core.acquisition.imports import ensure_acquisition_imports_schema

    ensure_acquisition_imports_schema(conn)

    assert conn.execute(
        "SELECT status FROM acquisition_imports WHERE id='i1'"
    ).fetchone()[0] == "importing"
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='acquisition_imports'"
    ).fetchone()[0]
    assert "recovered_to_staging" in schema
    conn.close()
