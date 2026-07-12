"""Central Usenet monitor and download-to-import lifecycle tests."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.client_monitor import (
    MonitorRunResult,
    UsenetAcquisitionMonitor,
    UsenetClientSnapshot,
    UsenetJobSnapshot,
    collect_usenet_snapshot,
    fail_stale_local_submissions,
    reconcile_usenet_snapshot,
)
from core.acquisition.grabs import (
    STATUS_CANCELLED,
    STATUS_CANCEL_PENDING,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_QUEUED,
    get_grab,
    record_grab,
    update_grab,
)
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import_by_download,
    list_open_imports,
    record_download_completed,
)
from core.acquisition.requests import create_request, get_request, transition_request
from core.usenet_clients.base import UsenetStatus


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _linked_grab(
    conn,
    download_id: str,
    *,
    title: str = "Artist - Album",
    external_job_id: str | None = None,
    last_client_state: str | None = None,
    category: str = "soulsync",
):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=10,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=f"monitor-{download_id}",
    )
    request = transition_request(conn, request.id, "searching")
    candidate, _ = register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=f"ref-{download_id}",
        title=title,
        indexer="test-indexer",
        guid=f"guid-{download_id}",
    )
    transition_request(conn, request.id, "candidates_ready")
    record_grab(
        conn,
        download_id,
        "usenet",
        client="FakeUsenetAdapter" if external_job_id else None,
        title=title,
        category=category,
        acquisition_request_id=request.id,
        release_candidate_id=candidate.id,
    )
    transition_request(conn, request.id, "grabbing")
    if external_job_id or last_client_state:
        update_grab(
            conn,
            download_id,
            status=STATUS_QUEUED if external_job_id else None,
            external_job_id=external_job_id,
            last_client_state=last_client_state,
        )
    return request, candidate


def _job(
    job_id: str,
    *,
    name: str = "Artist - Album",
    state: str = "downloading",
    category: str | None = "soulsync",
    save_path: str | None = None,
    error: str | None = None,
) -> UsenetJobSnapshot:
    return UsenetJobSnapshot(
        id=job_id,
        name=name,
        state=state,
        category=category,
        save_path=save_path,
        error=error,
    )


class FakeUsenetAdapter:
    def __init__(self, statuses, targeted=None, *, on_get_all=None, remove_result=True):
        self.statuses = list(statuses)
        self.targeted = dict(targeted or {})
        self.lookups = []
        self.removals = []
        self.on_get_all = on_get_all
        self.remove_result = remove_result

    def is_configured(self):
        return True

    async def get_all(self):
        if self.on_get_all:
            self.on_get_all()
        return list(self.statuses)

    async def get_status(self, job_id):
        self.lookups.append(job_id)
        value = self.targeted.get(job_id)
        if isinstance(value, Exception):
            raise value
        return value

    async def remove(self, job_id, delete_files=False):
        self.removals.append((job_id, delete_files))
        if isinstance(self.remove_result, Exception):
            raise self.remove_result
        return self.remove_result


def _status(job_id, *, category="soulsync", progress=0.5):
    return UsenetStatus(
        id=job_id,
        name=f"Release {job_id}",
        state="downloading",
        progress=progress,
        size=100,
        downloaded=50,
        download_speed=10,
        category=category,
    )


def test_collect_snapshot_filters_category_and_targets_known_jobs():
    known = _status("known", category=None, progress=0.73)
    adapter = FakeUsenetAdapter(
        [_status("owned"), _status("foreign", category="other")],
        targeted={"known": known},
    )

    snapshot = asyncio.run(
        collect_usenet_snapshot(adapter, "SoulSync", known_job_ids=["known"]),
    )

    assert [job.id for job in snapshot.jobs] == ["known", "owned"]
    assert adapter.lookups == ["known"]
    assert not hasattr(snapshot.jobs[0], "progress")
    assert "foreign" not in {job.id for job in snapshot.jobs}


def test_collect_snapshot_isolates_targeted_lookup_errors():
    adapter = FakeUsenetAdapter([], targeted={"lost": RuntimeError("offline")})

    snapshot = asyncio.run(
        collect_usenet_snapshot(adapter, "soulsync", known_job_ids=["lost"]),
    )

    assert snapshot.jobs == ()
    assert snapshot.lookup_errors == ("lost",)


def test_reconcile_updates_known_job_business_state_only(conn):
    _linked_grab(conn, "dl-known", external_job_id="job-known")
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(_job("job-known", state="extracting"),),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    grab = get_grab(conn, "dl-known")
    assert result.updated == ("dl-known",)
    assert grab["status"] == STATUS_DOWNLOADING
    assert grab["last_client_state"] == "extracting"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(acquisition_grabs)")}
    assert {"progress", "speed", "eta", "downloaded"}.isdisjoint(columns)


def test_reconcile_adopts_exact_normalized_title(conn):
    request, _ = _linked_grab(
        conn,
        "dl-adopt",
        title="Artist - Album",
        last_client_state="submission_unknown",
    )
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(_job("job-adopt", name="  Artist   - Album.nzb  "),),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    grab = get_grab(conn, "dl-adopt")
    assert result.adopted == ("dl-adopt",)
    assert grab["external_job_id"] == "job-adopt"
    assert grab["adopted"] == 1
    assert grab["status"] == STATUS_DOWNLOADING
    events = list_history_events(conn, request_id=request.id)
    assert events[-1].event_type == "client_job_adopted"
    assert events[-1].payload["strategy"] == "exact_title"


def test_reconcile_adopts_only_remaining_one_to_one_category_job(conn):
    _linked_grab(
        conn,
        "dl-one",
        title="Original title",
        last_client_state="submission_unknown",
    )
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(_job("job-one", name="Client renamed title"),),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    assert result.adopted == ("dl-one",)
    assert get_grab(conn, "dl-one")["external_job_id"] == "job-one"


def test_reconcile_refuses_ambiguous_category_adoption(conn):
    for download_id in ("dl-a", "dl-b"):
        _linked_grab(
            conn,
            download_id,
            title="Same title",
            last_client_state="submission_unknown",
        )
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(
            _job("job-a", name="Same title"),
            _job("job-b", name="Same title"),
        ),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    assert result.adopted == ()
    assert result.ambiguous == ("dl-a", "dl-b")
    assert get_grab(conn, "dl-a")["external_job_id"] is None
    assert get_grab(conn, "dl-b")["external_job_id"] is None


def test_completed_download_creates_pending_import_without_completing_request(conn):
    request, _ = _linked_grab(conn, "dl-done", external_job_id="job-done")
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(
            _job(
                "job-done",
                state="completed",
                save_path="/downloads/Artist - Album",
            ),
        ),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    grab = get_grab(conn, "dl-done")
    pending_import = get_import_by_download(conn, "dl-done")
    assert result.completed == ("dl-done",)
    assert grab["status"] == STATUS_COMPLETED
    assert pending_import.status == "pending"
    assert pending_import.expected_scope == "release_edition"
    assert pending_import.expected_entity_id == 10
    assert get_request(conn, request.id).status == "grabbing"
    assert list_open_imports(conn) == (pending_import,)


def test_completed_without_path_stays_open_for_later_snapshot(conn):
    _linked_grab(conn, "dl-no-path", external_job_id="job-no-path")
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(_job("job-no-path", state="completed"),),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    assert result.completed_without_path == ("dl-no-path",)
    assert get_grab(conn, "dl-no-path")["status"] == STATUS_QUEUED
    assert get_import_by_download(conn, "dl-no-path") is None


def test_failed_job_fails_request_and_blocklists_exact_candidate(conn):
    request, candidate = _linked_grab(conn, "dl-failed", external_job_id="job-failed")
    snapshot = UsenetClientSnapshot(
        client="FakeUsenetAdapter",
        category="soulsync",
        jobs=(
            _job("job-failed", state="failed", error="PAR repair failed"),
        ),
    )

    result = reconcile_usenet_snapshot(conn, snapshot)

    assert result.failed == ("dl-failed",)
    assert get_request(conn, request.id).status == "failed"
    row = conn.execute(
        "SELECT candidate_id, reason_code FROM release_blocklist WHERE active=1",
    ).fetchone()
    assert tuple(row) == (candidate.id, "candidate_failure")


def test_record_download_completed_is_idempotent_but_rejects_path_change(conn):
    _linked_grab(conn, "dl-idempotent", external_job_id="job-idempotent")

    first = record_download_completed(
        conn, "dl-idempotent", output_path="/downloads/album",
    )
    second = record_download_completed(
        conn, "dl-idempotent", output_path="/downloads/album",
    )
    request_id = get_grab(conn, "dl-idempotent")["acquisition_request_id"]
    transition_request(conn, request_id, "completed")
    late_duplicate = record_download_completed(
        conn, "dl-idempotent", output_path="/downloads/album",
    )

    assert second == first
    assert late_duplicate == first
    assert conn.execute("SELECT COUNT(*) FROM acquisition_imports").fetchone()[0] == 1
    with pytest.raises(ValueError, match="different output path"):
        record_download_completed(
            conn, "dl-idempotent", output_path="/downloads/other",
        )


def _runtime_database(tmp_path):
    path = str(tmp_path / "acquisition-monitor.db")
    seed = sqlite3.connect(path)
    seed.row_factory = sqlite3.Row
    seed.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(seed)

    def factory():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return seed, factory


def test_runtime_monitor_does_not_hold_db_connection_during_client_io(tmp_path):
    seed, raw_factory = _runtime_database(tmp_path)
    _linked_grab(seed, "dl-runtime", external_job_id="job-runtime")
    seed.commit()
    seed.close()
    tracker = {"open": 0}

    class TrackedConnection:
        def __init__(self, connection):
            self._connection = connection
            tracker["open"] += 1

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            self._connection.close()
            tracker["open"] -= 1

    def tracked_factory():
        return TrackedConnection(raw_factory())

    adapter = FakeUsenetAdapter(
        [_status("job-runtime")],
        on_get_all=lambda: tracker["open"] == 0 or pytest.fail(
            "client I/O ran while a DB connection was open"),
    )
    monitor = UsenetAcquisitionMonitor(
        tracked_factory,
        adapter_getter=lambda: adapter,
        category_getter=lambda: "soulsync",
        process_started_at="2000-01-01 00:00:00",
    )

    result = monitor.run_once()

    assert result.reconciliation.observed == ("dl-runtime",)
    assert tracker["open"] == 0
    verify = raw_factory()
    assert get_grab(verify, "dl-runtime")["status"] == STATUS_DOWNLOADING
    verify.close()


def test_runtime_fails_only_certain_pre_restart_local_submission(tmp_path):
    seed, factory = _runtime_database(tmp_path)
    certain_request, _ = _linked_grab(seed, "dl-certain")
    uncertain_request, _ = _linked_grab(
        seed,
        "dl-uncertain",
        last_client_state="submission_unknown",
    )
    seed.commit()
    seed.close()
    monitor = UsenetAcquisitionMonitor(
        factory,
        adapter_getter=lambda: None,
        process_started_at="9999-12-31 23:59:59",
    )

    result = monitor.run_once()

    assert result.stale_submissions_failed == ("dl-certain",)
    assert result.skipped_reason == "client_unconfigured"
    verify = factory()
    assert get_request(verify, certain_request.id).status == "failed"
    assert get_request(verify, uncertain_request.id).status == "grabbing"
    assert get_grab(verify, "dl-uncertain")["status"] == "submitting"
    assert verify.execute("SELECT COUNT(*) FROM release_blocklist").fetchone()[0] == 0
    verify.close()


def test_stale_cleanup_does_not_claim_same_second_live_submission(conn):
    request, _ = _linked_grab(conn, "dl-same-second")
    created_at = get_grab(conn, "dl-same-second")["created_at"]

    failed = fail_stale_local_submissions(
        conn,
        created_before=created_at,
    )

    assert failed == ()
    assert get_request(conn, request.id).status == "grabbing"


def test_runtime_confirms_cancel_only_after_client_remove(tmp_path):
    seed, factory = _runtime_database(tmp_path)
    request, _ = _linked_grab(seed, "dl-cancel", external_job_id="job-cancel")
    update_grab(seed, "dl-cancel", status=STATUS_CANCEL_PENDING)
    seed.commit()
    seed.close()
    adapter = FakeUsenetAdapter([_status("job-cancel")])
    monitor = UsenetAcquisitionMonitor(
        factory,
        adapter_getter=lambda: adapter,
        category_getter=lambda: "soulsync",
        process_started_at="2000-01-01 00:00:00",
    )

    result = monitor.run_once()

    assert result.cancelled == ("dl-cancel",)
    assert adapter.removals == [("job-cancel", False)]
    verify = factory()
    assert get_grab(verify, "dl-cancel")["status"] == STATUS_CANCELLED
    assert get_request(verify, request.id).status == "cancelled"
    event = list_history_events(verify, download_id="dl-cancel")[-1]
    assert event.event_type == "cancelled"
    assert event.payload == {"delete_files": False}
    verify.close()


def test_runtime_treats_confirmed_absence_as_completed_cancel(tmp_path):
    seed, factory = _runtime_database(tmp_path)
    _linked_grab(seed, "dl-absent", external_job_id="job-absent")
    update_grab(seed, "dl-absent", status=STATUS_CANCEL_PENDING)
    seed.commit()
    seed.close()
    adapter = FakeUsenetAdapter([])
    monitor = UsenetAcquisitionMonitor(
        factory,
        adapter_getter=lambda: adapter,
        category_getter=lambda: "soulsync",
        process_started_at="2000-01-01 00:00:00",
    )

    first = monitor.run_once()
    second = monitor.run_once()
    result = monitor.run_once()

    assert first.cancelled == ()
    assert second.cancelled == ()
    assert result.cancelled == ("dl-absent",)
    assert adapter.lookups == ["job-absent"] * 3
    assert adapter.removals == []
    verify = factory()
    assert get_grab(verify, "dl-absent")["status"] == STATUS_CANCELLED
    verify.close()


def test_runtime_keeps_cancel_pending_when_client_remove_fails(tmp_path):
    seed, factory = _runtime_database(tmp_path)
    _linked_grab(seed, "dl-pending", external_job_id="job-pending")
    update_grab(seed, "dl-pending", status=STATUS_CANCEL_PENDING)
    seed.commit()
    seed.close()
    adapter = FakeUsenetAdapter([_status("job-pending")], remove_result=False)
    monitor = UsenetAcquisitionMonitor(
        factory,
        adapter_getter=lambda: adapter,
        category_getter=lambda: "soulsync",
        process_started_at="2000-01-01 00:00:00",
    )

    result = monitor.run_once()

    assert result.cancelled == ()
    assert result.cancel_failed == ("dl-pending",)
    verify = factory()
    assert get_grab(verify, "dl-pending")["status"] == STATUS_CANCEL_PENDING
    verify.close()


def test_runtime_monitor_wakes_immediately_after_submission():
    monitor = UsenetAcquisitionMonitor(
        lambda: None,
        interval_getter=lambda: 60,
    )
    cycle_finished = threading.Event()
    calls = []

    def fake_run_once():
        calls.append(len(calls) + 1)
        cycle_finished.set()
        return MonitorRunResult(open_grabs=0, skipped_reason="test")

    monitor.run_once = fake_run_once
    monitor.start()
    assert cycle_finished.wait(1)
    first_thread = monitor._thread
    monitor.start()
    assert monitor._thread is first_thread

    cycle_finished.clear()
    monitor.notify_submission(None, None)
    assert cycle_finished.wait(1)
    monitor.stop()

    assert calls == [1, 2]
    assert monitor.status()["running"] is False


def test_runtime_advances_imports_without_open_grabs(tmp_path):
    seed, factory = _runtime_database(tmp_path)
    seed.close()
    calls = []
    monitor = UsenetAcquisitionMonitor(
        factory,
        adapter_getter=lambda: None,
        import_pipeline_runner=lambda: calls.append("imports") or {"ok": True},
    )

    result = monitor.run_once()

    assert calls == ["imports"]
    assert result.imports == {"ok": True}
    assert result.skipped_reason == "no_open_grabs"
