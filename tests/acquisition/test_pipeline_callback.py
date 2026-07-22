import sqlite3

from core.acquisition import ensure_acquisition_schema
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import,
    record_inventory_result,
    record_matching_result,
    record_pipeline_file_completed,
    record_pipeline_file_quarantined,
)
from core.acquisition.pipeline_callback import (
    notify_pipeline_check_result,
    notify_pipeline_import_quarantined,
    notify_pipeline_import_started,
    notify_pipeline_retry_exhausted,
)
from core.acquisition.requests import get_request
from core.imports.quarantine import serialize_quarantine_context
from tests.acquisition.test_bundle_inventory import _pending_import


def _importing_record(conn):
    pending, request, _candidate = _pending_import(conn)
    inventory = [
        {"relative_path": "01.flac", "size_bytes": 10},
        {"relative_path": "02.flac", "size_bytes": 20},
    ]
    record_inventory_result(
        conn, pending.id, inventory, resolved_path="/resolved")
    importing = record_matching_result(
        conn,
        pending.id,
        [
            {"relative_path": "01.flac", "track_id": 101},
            {"relative_path": "02.flac", "track_id": 102},
        ],
        [],
        decision="import_ready",
    )
    return importing, request


def test_main_pipeline_completes_import_only_after_every_match():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)

    partial = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="01.flac",
        final_path="/library/01.flac",
        track_id=101,
    )
    assert partial.status == "importing"
    assert get_request(conn, request.id).status == "grabbing"

    completed = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="02.flac",
        final_path="/library/02.flac",
        track_id=102,
    )
    assert completed.status == "completed"
    assert get_request(conn, request.id).status == "completed"
    events = list_history_events(conn, request_id=request.id)
    assert [event.event_type for event in events].count("import_completed") == 1

    duplicate = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="02.flac",
        final_path="/library/02.flac",
        track_id=102,
    )
    assert duplicate.status == "completed"
    conn.close()


def test_pipeline_completion_rejects_a_file_outside_persisted_matches():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, _request = _importing_record(conn)

    try:
        record_pipeline_file_completed(
            conn,
            importing.id,
            relative_path="other.flac",
            final_path="/library/other.flac",
            track_id=999,
        )
    except ValueError as exc:
        assert "persisted import plan" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unexpected completion was accepted")
    assert get_import(conn, importing.id).status == "importing"
    conn.close()


def test_quarantine_is_persisted_and_cleared_by_later_success():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)

    quarantined = record_pipeline_file_quarantined(
        conn,
        importing.id,
        relative_path="01.flac",
        track_id=101,
        trigger="acoustid",
        reason="Fingerprint mismatch",
    )

    assert quarantined.status == "importing"
    assert quarantined.result["quarantined"] == [{
        "reason": "Fingerprint mismatch",
        "relative_path": "01.flac",
        "track_id": 101,
        "trigger": "acoustid",
    }]
    assert get_request(conn, request.id).status == "grabbing"
    events = list_history_events(conn, request_id=request.id)
    assert events[-1].event_type == "import_file_quarantined"

    partial = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="01.flac",
        final_path="/library/01.flac",
        track_id=101,
    )
    assert partial.status == "importing"
    assert partial.result["quarantined"] == []
    conn.close()


def test_quarantine_callback_ignores_legacy_imports_and_uses_markers(tmp_path):
    database_path = tmp_path / "callback.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    importing, _request = _importing_record(conn)
    conn.commit()
    conn.close()

    assert notify_pipeline_import_quarantined(
        {"track_info": {"name": "Legacy"}},
        trigger="quality",
        reason="ignored",
        connection_factory=factory,
    ) is False
    assert notify_pipeline_import_quarantined(
        {
            "_acquisition_import_id": importing.id,
            "_acquisition_relative_path": "02.flac",
            "_acquisition_track_id": 102,
        },
        trigger="quality",
        reason="Below profile target",
        connection_factory=factory,
    ) is True

    conn = factory()
    record = get_import(conn, importing.id)
    assert record.result["quarantined"][0]["track_id"] == 102
    conn.close()


def test_pipeline_checks_keep_native_import_correlation_and_structured_status(tmp_path):
    database_path = tmp_path / "checks.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)
    conn.commit()
    conn.close()
    context = {
        "_acquisition_import_id": importing.id,
        "_acquisition_relative_path": "01.flac",
        "_acquisition_track_id": 101,
    }

    # Native bundle setup already recorded import_started; the pipeline bridge
    # recognizes it and does not create a duplicate.
    assert notify_pipeline_import_started(
        context, connection_factory=factory
    ) is True
    assert notify_pipeline_check_result(
        context,
        check="quality",
        status="passed",
        reason_code="quality_allowed",
        payload={
            "quality_profile_id": 1,
            "before_quality": "MP3",
            "after_quality": "FLAC 24-bit/96kHz",
            "decision": "allowed",
        },
        connection_factory=factory,
    ) is True
    assert notify_pipeline_check_result(
        context,
        check="acoustid",
        status="not_run",
        reason_code="verification_unavailable",
        message="API key unavailable",
        connection_factory=factory,
    ) is True

    conn = factory()
    events = list_history_events(conn, request_id=request.id)
    assert [event.event_type for event in events].count("import_started") == 1
    quality = next(event for event in events if event.event_type == "quality_checked")
    assert quality.download_id == importing.download_id
    assert quality.candidate_id == importing.candidate_id
    assert quality.payload == {
        "actor": "system",
        "after_quality": "FLAC 24-bit/96kHz",
        "before_quality": "MP3",
        "check": "quality",
        "decision": "allowed",
        "import_id": importing.id,
        "pipeline": "main",
        "quality_profile_id": 1,
        "status": "passed",
        "track_id": 101,
    }
    acoustic = next(
        event for event in events if event.event_type == "acoustic_id_checked"
    )
    assert acoustic.reason_code == "verification_unavailable"
    assert acoustic.payload["status"] == "not_run"
    conn.close()


def test_pipeline_check_callback_rejects_invalid_or_uncorrelated_input():
    assert notify_pipeline_check_result({}, check="quality", status="passed") is False
    assert notify_pipeline_check_result(
        {"_acquisition_import_id": "x"}, check="integrity", status="passed"
    ) is False
    assert notify_pipeline_check_result(
        {"_acquisition_import_id": "x"}, check="quality", status="unknown"
    ) is False


def test_retry_exhaustion_fails_import_and_blocklists_release(tmp_path):
    database_path = tmp_path / "retry.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    importing, request = _importing_record(conn)
    conn.commit()
    conn.close()

    assert notify_pipeline_retry_exhausted(
        {"_acquisition_import_id": importing.id},
        error="No candidates remain",
        connection_factory=factory,
    ) is True

    conn = factory()
    assert get_import(conn, importing.id).status == "failed"
    assert get_request(conn, request.id).status == "failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM release_blocklist WHERE candidate_id=? AND active=1",
        (importing.candidate_id,),
    ).fetchone()[0] == 1
    events = list_history_events(conn, request_id=request.id)
    assert events[-2].event_type == "import_failed"
    assert events[-1].event_type == "candidate_blocklisted"
    conn.close()


def test_quarantine_sidecar_preserves_acquisition_markers():
    context = {
        "_acquisition_import_id": "aim1-test",
        "_acquisition_relative_path": "Disc 1/01.flac",
        "_acquisition_track_id": 42,
        "track_info": {
            "quality_profile_id": 7,
            "_acquisition_import_id": "aim1-test",
        },
    }

    restored = serialize_quarantine_context(context)

    assert restored["_acquisition_import_id"] == "aim1-test"
    assert restored["_acquisition_relative_path"] == "Disc 1/01.flac"
    assert restored["_acquisition_track_id"] == 42
    assert restored["track_info"]["quality_profile_id"] == 7
