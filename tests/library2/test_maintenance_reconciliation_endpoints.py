"""Admin API boundaries for LV2-006 and LV2-013."""

from __future__ import annotations

import threading
import time

import pytest


flask = pytest.importorskip("flask")


def _api(*, profile=1, integrity=None):
    state = {"profile": profile}
    calls = {"acquisition": [], "integrity": []}
    app = flask.Flask(__name__)
    from api.library_v2 import register_library_v2_routes

    def _acquisition(*, dry_run):
        calls["acquisition"].append(dry_run)
        return {"dry_run": dry_run, "observed": 2, "counts": {"awaiting_evidence": 2}}

    # the runner now also takes a progress callback (it runs as a job)
    def _integrity(*, max_findings, progress=None):
        calls["integrity"].append(max_findings)
        if integrity is not None:
            return integrity(max_findings=max_findings, progress=progress)
        return {"read_only": True, "findings": [], "findings_total": 0}

    register_library_v2_routes(
        app,
        get_database=lambda: object(),
        config_get=lambda key, default=None: (
            True if key == "features.library_v2" else default
        ),
        profile_id_getter=lambda: state["profile"],
        acquisition_reconciliation_runner=_acquisition,
        integrity_report_runner=_integrity,
    )
    return app.test_client(), state, calls


def test_acquisition_get_is_admin_dry_run():
    client, _, calls = _api()

    response = client.get("/api/library/v2/maintenance/reconcile-acquisition")

    assert response.status_code == 200
    assert response.get_json()["report"]["dry_run"] is True
    assert calls["acquisition"] == [True]


def test_acquisition_post_only_applies_with_explicit_true():
    client, _, calls = _api()

    preview = client.post(
        "/api/library/v2/maintenance/reconcile-acquisition", json={"apply": False},
    )
    applied = client.post(
        "/api/library/v2/maintenance/reconcile-acquisition", json={"apply": True},
    )

    assert preview.get_json()["report"]["dry_run"] is True
    assert applied.get_json()["report"]["dry_run"] is False
    assert calls["acquisition"] == [True, False]


def test_read_only_maintenance_endpoints_are_admin_only():
    client, _, calls = _api(profile=2)

    acquisition = client.get(
        "/api/library/v2/maintenance/reconcile-acquisition",
    )
    integrity = client.get("/api/library/v2/maintenance/integrity-report")

    assert acquisition.status_code == 403
    assert integrity.status_code == 403
    assert calls == {"acquisition": [], "integrity": []}


def test_integrity_report_is_bounded_and_read_only():
    client, _, calls = _api()

    response = client.get(
        "/api/library/v2/maintenance/integrity-report?max_findings=120",
    )

    assert response.status_code == 200
    assert response.get_json()["report"]["read_only"] is True
    assert calls["integrity"] == [120]


def test_integrity_report_rejects_invalid_bound():
    client, _, calls = _api()

    response = client.get(
        "/api/library/v2/maintenance/integrity-report?max_findings=lots",
    )

    assert response.status_code == 400
    assert calls["integrity"] == []


# -- review 5 perf: the report checks every file on disk (most of an hour on a
# 300k-track library) and used to hold the request for all of it

def _wait_for_job(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/library/v2/jobs/status?job_id={job_id}").get_json()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError("integrity job never finished")


def test_slow_integrity_report_answers_202_then_serves_the_finished_report(monkeypatch):
    import api.library_v2 as lib2_api

    monkeypatch.setattr(lib2_api, "_INTEGRITY_REPORT_WAIT_S", 0.2)
    gate = threading.Event()

    def _slow(*, max_findings, progress):
        progress(3, 10)
        gate.wait(5)
        return {"read_only": True, "findings": [], "findings_total": 0, "truncated": False}

    client, _, calls = _api(integrity=_slow)
    try:
        started = time.time()
        first = client.get("/api/library/v2/maintenance/integrity-report")
        assert time.time() - started < 3
        assert first.status_code == 202
        body = first.get_json()
        assert body["success"] is True and body["running"] is True
        job_id = body["job_id"]

        second = client.get("/api/library/v2/maintenance/integrity-report")
        assert second.status_code == 202
        assert second.get_json()["job_id"] == job_id
        assert second.get_json()["current"] == 3 and second.get_json()["total"] == 10
        assert calls["integrity"] == [1000]
    finally:
        gate.set()
    _wait_for_job(client, job_id)

    done = client.get("/api/library/v2/maintenance/integrity-report")
    assert done.status_code == 200
    assert done.get_json()["report"]["read_only"] is True
    assert calls["integrity"] == [1000]


def test_finished_report_is_served_again_within_a_smaller_bound():
    findings = [{"code": f"c{i}"} for i in range(3)]

    def _three(*, max_findings, progress):
        return {"read_only": True, "findings": findings[:max_findings],
                "findings_total": 3, "truncated": max_findings < 3}

    client, _, calls = _api(integrity=_three)
    full = client.get("/api/library/v2/maintenance/integrity-report")
    assert full.status_code == 200
    assert full.get_json()["report"]["truncated"] is False

    small = client.get("/api/library/v2/maintenance/integrity-report?max_findings=2")
    report = small.get_json()["report"]
    assert [f["code"] for f in report["findings"]] == ["c0", "c1"]
    assert report["truncated"] is True and report["findings_total"] == 3
    assert calls["integrity"] == [1000]

    client.get("/api/library/v2/maintenance/integrity-report?refresh=1")
    assert calls["integrity"] == [1000, 1000]


def test_integrity_report_failure_still_answers_500():
    def _broken(*, max_findings, progress):
        raise RuntimeError("disk gone")

    client, _, _ = _api(integrity=_broken)
    response = client.get("/api/library/v2/maintenance/integrity-report")
    assert response.status_code == 500
    assert response.get_json()["error"] == "disk gone"
