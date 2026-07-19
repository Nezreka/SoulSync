"""The sync-all job: background execution with live 'collections:sync' progress
(bell + studio), the synchronous automation path sharing the same lock, and the
API start/status seam."""

from __future__ import annotations

import time

import pytest
from flask import Flask

from core.video.collections import sync_job
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def _reset_job():
    sync_job._JOB.update(running=False, phase="idle", done=0, total=0, synced=0,
                         failed=0, added=0, removed=0, wishlisted=0, name=None, error=None)
    sync_job.set_sync_progress_emitter(None)
    yield
    sync_job._JOB["running"] = False
    sync_job.set_sync_progress_emitter(None)


def _fake_run_sync(monkeypatch, result=None, collections=("A", "B", "C")):
    """Stub run_sync: walks on_progress over the collections, returns aggregates."""
    result = result or {"ok": True, "total": len(collections), "synced": len(collections),
                        "failed": 0, "added": 5, "removed": 1, "wishlisted": 2, "results": []}

    def run(db, *, force=False, on_progress=None, **kw):
        for i, name in enumerate(collections):
            if on_progress:
                on_progress(i + 1, len(collections), name)
        return result

    monkeypatch.setattr("core.video.collections.sync.run_sync", run)
    return result


def test_sync_all_with_progress_feeds_job_and_emitter(monkeypatch):
    _fake_run_sync(monkeypatch)
    events = []
    sync_job.set_sync_progress_emitter(lambda name, payload: events.append((name, payload)))
    caller_prog = []

    r = sync_job.sync_all_with_progress(db=None, on_progress=lambda d, t, n: caller_prog.append((d, t, n)))
    assert r["ok"] and r["synced"] == 3
    s = sync_job.status()
    assert s["phase"] == "done" and s["done"] == 3 and s["total"] == 3
    assert s["added"] == 5 and s["wishlisted"] == 2 and not s["running"]
    # Start + finish always emit; the caller's hook got every step.
    assert events[0][1]["phase"] == "starting" and events[-1][1]["phase"] == "done"
    assert caller_prog == [(1, 3, "A"), (2, 3, "B"), (3, 3, "C")]


def test_error_result_and_crash_both_land_in_error_phase(monkeypatch):
    _fake_run_sync(monkeypatch, result={"ok": False, "error": "No video server configured"})
    r = sync_job.sync_all_with_progress(db=None)
    assert not r["ok"] and sync_job.status()["phase"] == "error"
    assert "No video server" in sync_job.status()["error"]

    def boom(db, **kw):
        raise RuntimeError("kaput")
    monkeypatch.setattr("core.video.collections.sync.run_sync", boom)
    sync_job._JOB["running"] = False
    r = sync_job.sync_all_with_progress(db=None)
    assert not r["ok"] and sync_job.status()["phase"] == "error"
    assert not sync_job.status()["running"]          # lock always released


def test_overlap_refused_on_both_entry_points(monkeypatch):
    _fake_run_sync(monkeypatch)
    sync_job._JOB["running"] = True
    assert "already running" in sync_job.sync_all_with_progress(db=None)["error"]
    assert "already running" in sync_job.start_sync_all(db=None)["error"]


def test_start_sync_all_runs_in_background(monkeypatch):
    _fake_run_sync(monkeypatch)
    r = sync_job.start_sync_all(db=None)
    assert r == {"ok": True, "started": True}
    for _ in range(100):
        if not sync_job.status()["running"] and sync_job.status()["phase"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert sync_job.status()["phase"] == "done" and sync_job.status()["synced"] == 3


def test_automation_default_run_goes_through_the_job(monkeypatch):
    # The nightly automation must feed the same _JOB (bell shows it live).
    _fake_run_sync(monkeypatch)
    from core.automation.handlers.video_sync_collections import _default_run
    r = _default_run(None, on_progress=None)
    assert r["ok"] and sync_job.status()["phase"] == "done"


# ── API seam ─────────────────────────────────────────────────────────────────
def _make_client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_sync_api_starts_job_and_reports_status(tmp_path, monkeypatch):
    _fake_run_sync(monkeypatch)
    client = _make_client(tmp_path)

    r = client.post("/api/video/collections/sync")
    assert r.status_code == 200 and r.get_json()["started"]
    for _ in range(100):
        s = client.get("/api/video/collections/sync/status").get_json()
        if not s["running"] and s["phase"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert s["phase"] == "done" and s["synced"] == 3

    sync_job._JOB["running"] = True
    assert client.post("/api/video/collections/sync").status_code == 409
