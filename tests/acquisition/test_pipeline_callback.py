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
    notify_previous_file_replaced,
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


def test_previous_file_replaced_keeps_native_import_correlation(tmp_path):
    """F-10 history step: an upgrade/replace inside the shared pipeline is
    journaled against the same request/candidate/download as the rest of
    that import's correlated events."""
    database_path = tmp_path / "replaced.sqlite"

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

    assert notify_previous_file_replaced(
        context, reason="quality_upgrade", connection_factory=factory,
    ) is True

    conn = factory()
    events = list_history_events(conn, request_id=request.id)
    replaced = next(
        event for event in events if event.event_type == "previous_file_replaced"
    )
    assert replaced.download_id == importing.download_id
    assert replaced.candidate_id == importing.candidate_id
    assert replaced.reason_code == "quality_upgrade"
    assert replaced.payload["reason"] == "quality_upgrade"
    assert replaced.payload["track_id"] == 101
    conn.close()


def test_previous_file_replaced_is_a_noop_for_ordinary_imports():
    """Ordinary (non-acquisition) imports carry no marker — zero-write no-op,
    matching every other pipeline callback in this module."""
    assert notify_previous_file_replaced({}, reason="quality_upgrade") is False


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


def _library_history_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS library_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               event_type TEXT NOT NULL,
               title TEXT NOT NULL,
               file_path TEXT,
               verification_status TEXT)"""
    )


def test_history_correlation_is_persisted_onto_the_library_history_row(tmp_path):
    """F-10 decision (features.md): ``library_history`` gains a persistent
    acquisition correlation, so a verification decision made days later can
    still be journaled against the same request/candidate/download. Until now
    the only link was the in-memory ``context['_history_id']`` of that one
    pipeline run."""
    from core.acquisition.pipeline_callback import persist_history_correlation

    database_path = tmp_path / "correlation.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    _library_history_table(conn)
    importing, request = _importing_record(conn)
    conn.execute("INSERT INTO library_history(id, event_type, title) "
                 "VALUES(7, 'download', 'Nonstop')")
    conn.commit()
    conn.close()

    assert persist_history_correlation(
        {"_acquisition_import_id": importing.id}, 7, connection_factory=factory,
    ) is True

    conn = factory()
    row = conn.execute(
        "SELECT acquisition_request_id, acquisition_candidate_id, "
        "       acquisition_download_id FROM library_history WHERE id=7"
    ).fetchone()
    conn.close()
    assert row["acquisition_request_id"] == request.id
    assert row["acquisition_download_id"] == importing.download_id
    assert row["acquisition_candidate_id"] == importing.candidate_id


def test_history_correlation_is_a_noop_for_ordinary_imports():
    from core.acquisition.pipeline_callback import persist_history_correlation

    assert persist_history_correlation({}, 7) is False


def test_verification_decision_journals_human_verified_and_rejected(tmp_path):
    """The two F-10 steps that were unreachable before: an approve/reject that
    happens long after the pipeline run now finds its correlation on the
    history row itself."""
    from core.acquisition.pipeline_callback import (
        notify_verification_decision,
        persist_history_correlation,
    )

    database_path = tmp_path / "verification.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    _library_history_table(conn)
    importing, request = _importing_record(conn)
    conn.execute("INSERT INTO library_history(id, event_type, title, file_path) "
                 "VALUES(7, 'download', 'Nonstop', '/library/01.flac')")
    conn.commit()
    conn.close()
    persist_history_correlation(
        {"_acquisition_import_id": importing.id}, 7, connection_factory=factory)

    assert notify_verification_decision(
        7, decision="human_verified", actor="profile:1",
        connection_factory=factory,
    ) is True
    assert notify_verification_decision(
        7, decision="rejected", reason_code="wrong_track",
        connection_factory=factory,
    ) is True

    conn = factory()
    events = {event.event_type: event
              for event in list_history_events(conn, request_id=request.id)}
    conn.close()
    assert events["human_verified"].download_id == importing.download_id
    assert events["human_verified"].candidate_id == importing.candidate_id
    assert events["rejected"].reason_code == "wrong_track"
    assert events["rejected"].payload["library_history_id"] == 7


def test_verification_decision_without_correlation_writes_nothing(tmp_path):
    """A plain library import has no acquisition side — the decision must stay
    a zero-write no-op rather than inventing a correlation."""
    from core.acquisition.pipeline_callback import notify_verification_decision

    database_path = tmp_path / "uncorrelated.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = factory()
    ensure_acquisition_schema(conn)
    _library_history_table(conn)
    conn.execute("INSERT INTO library_history(id, event_type, title) "
                 "VALUES(9, 'download', 'Untracked')")
    conn.commit()
    conn.close()

    assert notify_verification_decision(9, decision="human_verified",
                                        connection_factory=factory) is False


def test_verification_decision_rejects_an_unknown_decision(tmp_path):
    from core.acquisition.pipeline_callback import notify_verification_decision

    assert notify_verification_decision(1, decision="maybe") is False
