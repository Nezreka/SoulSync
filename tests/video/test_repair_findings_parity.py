"""video findings speak the music findings api (oct 27 2026).

the video Tools page moved onto the React music page, which drives both sides
with one client. every route and shape that client reads on /api/repair has to
exist on /api/video/repair too, or a video button does nothing. these pin the
ones video didn't have: types, search, sort, 0-based paging, groups, the type
catalog, reopen, dismiss-by-type, a filter-honest clear, last_error, the
background fix-all, and runs that say when they failed.
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from flask import Flask

import core.video.repair.worker as worker_mod
from core.video.repair import JobContext, JobResult, VideoRepairJob
from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    worker_mod._worker = None
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db
    finally:
        w = worker_mod._worker
        if w:
            w.stop()
            t = w._bulk_thread
            if t is not None:
                t.join(timeout=10)
        worker_mod._worker = None
        videoapi._video_db = None


def _seed(db, job_id, finding_type, n, *, severity="info", title="Item", details=None):
    for i in range(n):
        db.repair_create_finding(job_id, finding_type=finding_type, severity=severity,
                                 title=f"{title} {i}", entity_type="movie",
                                 entity_id=f"{finding_type}:{title}:{i}",
                                 details=dict(details or {}, n=i))


# ── fake jobs so fix paths stay inside this process ──────────────────────────

class _OkJob(VideoRepairJob):
    job_id = "fake_ok"
    display_name = "Fake OK"
    finding_types = ("metadata_gap",)

    def scan(self, context: JobContext) -> JobResult:
        return JobResult()

    def fix(self, context, finding, fix_action=None):
        return {"success": True, "action": "refreshed", "message": "ok"}


class _GrabJob(VideoRepairJob):
    job_id = "fake_grab"
    display_name = "Fake Grab"
    finding_types = ("broken_file",)
    calls: list = []

    def scan(self, context):
        return JobResult()

    def fix(self, context, finding, fix_action=None):
        _GrabJob.calls.append((finding["id"], fix_action))
        return {"success": True, "action": "grabbed"}


class _FailJob(VideoRepairJob):
    job_id = "fake_fail"
    display_name = "Fake Fail"
    finding_types = ("stale_wishlist",)

    def scan(self, context):
        return JobResult()

    def fix(self, context, finding, fix_action=None):
        return {"success": False, "error": "nothing acceptable found"}


class _ReportOnly(VideoRepairJob):
    job_id = "fake_report"
    display_name = "Fake Report"
    finding_types = ("duplicate_movie",)

    def scan(self, context):
        return JobResult()


class _Boom(VideoRepairJob):
    job_id = "fake_boom"
    display_name = "Fake Boom"
    finding_types = ()

    def scan(self, context):
        raise RuntimeError("drive went away")


@pytest.fixture()
def fake_jobs(monkeypatch):
    _GrabJob.calls = []
    jobs = {c.job_id: c for c in (_OkJob, _GrabJob, _FailJob, _ReportOnly, _Boom)}
    monkeypatch.setattr(worker_mod, "get_all_jobs", lambda: dict(jobs))
    return jobs


def _wait_bulk(c, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/api/video/repair/bulk-fix/status").get_json()
        if not st.get("running"):
            return st
        time.sleep(0.05)
    raise AssertionError("bulk fix never finished")


# ── list: type, search, sort, 0-based paging ─────────────────────────────────

def test_findings_list_filters_by_type_search_and_pages_from_zero(client):
    c, db = client
    _seed(db, "broken_files", "broken_file", 3, title="Alien")
    _seed(db, "metadata_gaps", "metadata_gap", 2, title="Heat",
          details={"gaps": ["overview"]})
    got = c.get("/api/video/repair/findings?finding_type=broken_file").get_json()
    assert got["total"] == 3 and {f["finding_type"] for f in got["items"]} == {"broken_file"}

    # search reaches details too, not just the title
    got = c.get("/api/video/repair/findings?q=overview").get_json()
    assert got["total"] == 2

    # page 0 is the FIRST page (the music client sends 0-based pages; the old
    # 1-based video endpoint showed page two when asked for page one)
    p0 = c.get("/api/video/repair/findings?limit=2&page=0&sort=oldest").get_json()
    p1 = c.get("/api/video/repair/findings?limit=2&page=1&sort=oldest").get_json()
    assert p0["page"] == 0 and len(p0["items"]) == 2 and len(p1["items"]) == 2
    assert not {f["id"] for f in p0["items"]} & {f["id"] for f in p1["items"]}
    assert p0["items"][0]["id"] < p1["items"][0]["id"]
    # junk paging never 500s
    assert c.get("/api/video/repair/findings?page=undefined&limit=null").status_code == 200


def test_severity_sort_and_filter_treat_critical_as_the_top_band(client):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 2, severity="info", title="Info")
    _seed(db, "youtube_ghosts", "youtube_mass_missing", 1, severity="critical", title="Mass")
    _seed(db, "broken_files", "broken_file", 1, severity="warning", title="Warn")
    items = c.get("/api/video/repair/findings?sort=severity").get_json()["items"]
    assert [f["severity"] for f in items] == ["critical", "warning", "info", "info"]
    # the client's "Critical" filter sends 'error' (the music word)
    got = c.get("/api/video/repair/findings?severity=error").get_json()
    assert got["total"] == 1 and got["items"][0]["severity"] == "critical"


# ── groups ───────────────────────────────────────────────────────────────────

def test_groups_count_every_status_and_rank_worst_first(client):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 4)
    _seed(db, "youtube_ghosts", "youtube_mass_missing", 1, severity="critical")
    ids = [f["id"] for f in db.repair_get_findings(finding_type="stale_wishlist")["items"]]
    db.repair_set_finding_status(ids[0], "dismissed")
    db.repair_set_finding_status(ids[1], "resolved", action="removed")
    groups = c.get("/api/video/repair/findings/groups").get_json()["groups"]
    assert [g["finding_type"] for g in groups] == ["youtube_mass_missing", "stale_wishlist"]
    stale = groups[1]
    assert (stale["pending"], stale["dismissed"], stale["resolved"], stale["total"]) == (2, 1, 1, 4)
    assert stale["job_ids"] == ["wishlist_audit"]
    # critical is reported in the music word so the client colours it
    assert groups[0]["severity_max"] == "error"


def test_a_handled_critical_no_longer_colours_its_group(client):
    c, db = client
    _seed(db, "youtube_ghosts", "youtube_mass_missing", 1, severity="critical")
    fid = db.repair_get_findings()["items"][0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="flagged")
    g = c.get("/api/video/repair/findings/groups").get_json()["groups"][0]
    assert g["pending"] == 0 and g["severity_max"] == "info"


# ── the type catalog ─────────────────────────────────────────────────────────

def test_every_type_a_real_job_emits_is_described():
    """drift guard: a new job type with no catalog row would show its slug and
    a guessed verb. the music side had nine of those."""
    from core.video.repair import get_all_jobs
    from core.video.repair.catalog import VIDEO_FINDING_TYPE_META, VIDEO_JOB_CATEGORIES
    emitted = {t for cls in get_all_jobs().values() for t in (cls.finding_types or ())}
    assert emitted - set(VIDEO_FINDING_TYPE_META) == set()
    for slug, meta in VIDEO_FINDING_TYPE_META.items():
        assert meta.get("label") and meta.get("blurb"), slug
    assert set(get_all_jobs()) - set(VIDEO_JOB_CATEGORIES) == set()


def test_catalog_derives_fixability_from_the_jobs(client):
    c, _ = client
    types = {t["type"]: t for t in c.get("/api/video/repair/finding-types").get_json()["types"]}
    # report-only: the duplicate job has no fix, so no button
    assert types["duplicate_movie"]["fixable"] is False and types["duplicate_movie"]["verb"] is None
    assert types["duplicate_episode"]["fixable"] is False
    assert types["naming_mismatch"]["verb"] == "Rename" and types["naming_mismatch"]["destructive"]
    assert types["missing_episodes"]["acquires"] and not types["missing_episodes"]["destructive"]
    assert types["stale_wishlist"]["fixable"] and not types["stale_wishlist"]["destructive"]
    assert types["watched_cleanup"]["confirm"]
    assert all(t["blurb"] for t in types.values())


def test_safe_types_never_touch_files_or_start_downloads():
    from core.video.repair.catalog import VIDEO_FINDING_TYPE_META, is_safe_type
    safe = {t for t in VIDEO_FINDING_TYPE_META if is_safe_type(t)}
    for t in ("watched_cleanup", "naming_mismatch", "quality_upgrade", "broken_file",
              "missing_episodes", "incomplete_collection"):
        assert t not in safe
    assert {"metadata_gap", "stale_wishlist", "missing_subtitles", "youtube_ghost"} <= safe


def test_jobs_carry_their_family_and_file_writing_flag(client):
    c, _ = client
    jobs = {j["job_id"]: j for j in c.get("/api/video/repair/jobs").get_json()["jobs"]}
    assert jobs["broken_files"]["category"] == "Files & storage"
    assert jobs["missing_episodes"]["category"] == "Collection gaps"
    assert jobs["naming_conformance"]["writes_library_files"] is True
    assert jobs["metadata_gaps"]["writes_library_files"] is False


def test_jobs_take_their_schedule_from_the_system_automation(client, monkeypatch):
    """the cadence editor writes to the job's automation row, so the list must
    hand over its id and its interval, like music does."""
    import database.music_database as mdb

    class _DB:
        def get_automations(self, profile_id):
            return [{"id": 77, "owned_by": "system:repair_job",
                     "action_type": "video_run_repair_job",
                     "action_config": '{"job_id": "broken_files"}',
                     "trigger_type": "schedule", "trigger_config": '{"interval": 12}',
                     "next_run": "2026-10-28T03:00:00"},
                    # a music row with the same job id must not leak in
                    {"id": 5, "owned_by": "system:repair_job", "action_type": "run_repair_job",
                     "action_config": '{"job_id": "metadata_gaps"}',
                     "trigger_type": "schedule", "trigger_config": '{"interval": 3}'}]

    monkeypatch.setattr(mdb, "get_database", lambda *a, **k: _DB())
    c, _ = client
    jobs = {j["job_id"]: j for j in c.get("/api/video/repair/jobs").get_json()["jobs"]}
    assert jobs["broken_files"]["automation_id"] == 77
    assert jobs["broken_files"]["interval_hours"] == 12
    assert jobs["broken_files"]["next_run"] == "2026-10-28T03:00:00"
    assert jobs["metadata_gaps"]["automation_id"] is None


# ── reopen, dismiss by type, clear ───────────────────────────────────────────

def test_reopen_one_and_reopen_all_dismissed_only(client):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 3)
    ids = [f["id"] for f in db.repair_get_findings(sort="oldest")["items"]]
    db.repair_set_finding_status(ids[0], "dismissed")
    db.repair_set_finding_status(ids[1], "dismissed")
    db.repair_set_finding_status(ids[2], "resolved", action="removed")

    assert c.post(f"/api/video/repair/findings/{ids[0]}/reopen").get_json()["success"]
    row = db.repair_get_finding(ids[0])
    assert row["status"] == "pending" and row["resolved_at"] is None
    assert c.post(f"/api/video/repair/findings/{ids[0]}/reopen").status_code == 404

    r = c.post("/api/video/repair/findings/reopen-all",
               json={"finding_type": "stale_wishlist"}).get_json()
    # the resolved one is work that happened; bulk reopen leaves it alone
    assert r["reopened"] == 1 and db.repair_get_finding(ids[2])["status"] == "resolved"
    # an empty filter must not quietly mean "everything"
    db.repair_set_finding_status(ids[1], "dismissed")
    assert c.post("/api/video/repair/findings/reopen-all",
                  json={"finding_type": ""}).get_json()["reopened"] == 0


def test_dismiss_all_of_one_type_leaves_other_types_alone(client):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 3)
    _seed(db, "broken_files", "broken_file", 2)
    r = c.post("/api/video/repair/findings/bulk",
               json={"finding_type": "stale_wishlist", "action": "dismiss"})
    assert r.get_json() == {"success": True, "updated": 3}
    assert db.repair_get_findings(status="pending")["total"] == 2
    bad = c.post("/api/video/repair/findings/bulk",
                 json={"finding_type": "broken_file", "action": "resolve"})
    assert bad.status_code == 400


def test_clear_only_deletes_what_the_filters_show(client):
    """the #1142 trap: clear honoured two of five filters and deleted rows
    that were never on screen."""
    c, db = client
    _seed(db, "broken_files", "broken_file", 2, title="Alien")
    _seed(db, "broken_files", "broken_file", 2, title="Heat")
    _seed(db, "wishlist_audit", "stale_wishlist", 2, title="Alien")
    r = c.post("/api/video/repair/findings/clear",
               json={"status": "pending", "finding_type": "broken_file", "q": "Alien"})
    assert r.get_json()["deleted"] == 2
    left = db.repair_get_findings(limit=50)["items"]
    assert sorted((f["finding_type"], f["title"].split()[0]) for f in left) == [
        ("broken_file", "Heat"), ("broken_file", "Heat"),
        ("stale_wishlist", "Alien"), ("stale_wishlist", "Alien")]


# ── fixing ───────────────────────────────────────────────────────────────────

def test_a_failed_fix_keeps_its_reason_on_the_row(client, fake_jobs):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 1)
    fid = db.repair_get_findings()["items"][0]["id"]
    r = c.post(f"/api/video/repair/findings/{fid}/fix", json={})
    assert r.status_code == 400
    row = c.get("/api/video/repair/findings").get_json()["items"][0]
    assert row["status"] == "pending" and row["last_error"] == "nothing acceptable found"


def test_a_successful_fix_clears_the_old_reason(client, fake_jobs):
    c, db = client
    _seed(db, "metadata_gaps", "metadata_gap", 1)
    fid = db.repair_get_findings()["items"][0]["id"]
    db.repair_set_finding_error(fid, "tmdb timed out")
    assert c.post(f"/api/video/repair/findings/{fid}/fix", json={}).get_json()["success"]
    row = db.repair_get_finding(fid)
    assert row["status"] == "resolved" and row["user_action"] == "refreshed"
    assert row["last_error"] is None


def test_fix_all_runs_in_the_background_and_reports(client, fake_jobs):
    c, db = client
    _seed(db, "broken_files", "broken_file", 3)
    r = c.post("/api/video/repair/findings/bulk-fix-start", json={"finding_type": "broken_file"})
    assert r.get_json() == {"started": True, "total": 3}
    st = _wait_bulk(c)
    assert (st["fixed"], st["failed"], st["total"]) == (3, 0, 3)
    assert st["per_type"] == {"broken_file": {"fixed": 3, "failed": 0}}
    assert db.repair_get_findings(status="pending")["total"] == 0


def test_fix_all_safe_skips_downloads_and_file_changes(client, fake_jobs):
    c, db = client
    _seed(db, "metadata_gaps", "metadata_gap", 2)        # safe
    _seed(db, "broken_files", "broken_file", 2)          # grabs a replacement
    r = c.post("/api/video/repair/findings/bulk-fix-start", json={"safe_only": True})
    assert r.get_json()["total"] == 2
    _wait_bulk(c)
    assert _GrabJob.calls == []
    pend = db.repair_get_findings(status="pending")["items"]
    assert {f["finding_type"] for f in pend} == {"broken_file"}


def test_fix_all_reports_failures_with_reasons(client, fake_jobs):
    c, db = client
    _seed(db, "wishlist_audit", "stale_wishlist", 2)
    c.post("/api/video/repair/findings/bulk-fix-start", json={"finding_type": "stale_wishlist"})
    st = _wait_bulk(c)
    assert st["failed"] == 2 and st["errors"][0]["error"] == "nothing acceptable found"


def test_one_action_never_spans_two_types(client, fake_jobs):
    c, db = client
    _seed(db, "metadata_gaps", "metadata_gap", 1)
    _seed(db, "broken_files", "broken_file", 1)
    r = c.post("/api/video/repair/findings/bulk-fix-start", json={"fix_action": "forget"})
    assert r.status_code == 400 and r.get_json()["invalid"] is True
    assert db.repair_get_findings(status="pending")["total"] == 2


def test_report_only_types_are_never_in_a_fix_all(client, fake_jobs):
    c, db = client
    _seed(db, "duplicate_movies", "duplicate_movie", 2)
    r = c.post("/api/video/repair/findings/bulk-fix-start", json={})
    assert r.get_json()["started"] is False


def test_a_second_fix_all_is_refused_while_one_runs(client, fake_jobs, monkeypatch):
    import threading
    gate = threading.Event()
    orig = _GrabJob.fix

    def slow(self, context, finding, fix_action=None):
        gate.wait(5)
        return orig(self, context, finding, fix_action)

    monkeypatch.setattr(_GrabJob, "fix", slow)
    c, db = client
    _seed(db, "broken_files", "broken_file", 2)
    assert c.post("/api/video/repair/findings/bulk-fix-start", json={}).get_json()["started"]
    again = c.post("/api/video/repair/findings/bulk-fix-start", json={}).get_json()
    assert again["started"] is False and again["already_running"] is True
    c.post("/api/video/repair/bulk-fix/stop")
    gate.set()
    st = _wait_bulk(c)
    # stop lands after the fix in flight, so at most one finished
    assert st["stopped"] is True and st["done"] <= 2


# ── runs ─────────────────────────────────────────────────────────────────────

def test_a_run_that_raises_is_recorded_as_failed_with_why(client, fake_jobs):
    c, db = client
    w = worker_mod.get_video_repair_worker(db)
    w._run_job("fake_boom", forced=True)
    run = c.get("/api/video/repair/history?job_id=fake_boom").get_json()["runs"][0]
    assert run["status"] == "failed" and run["error_text"] == "drive went away"
    assert run["finished_at"]


def test_an_old_db_gains_the_new_columns(tmp_path):
    """the live server runs from this tree: a db made before v57 has neither
    column, and the fix path writes last_error on every attempt."""
    path = tmp_path / "old.db"
    VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE video_repair_findings DROP COLUMN last_error")
    conn.execute("ALTER TABLE video_repair_job_runs DROP COLUMN error_text")
    conn.commit()
    conn.close()
    # a restart: init runs once per path per process
    import database.video_database as vdbm
    vdbm._initialized_paths.discard(str(path.resolve()))
    db = VideoDatabase(database_path=str(path))
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_repair_findings)")}
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(video_repair_job_runs)")}
    conn.close()
    assert "last_error" in cols and "error_text" in rcols
    db.repair_create_finding("x", finding_type="metadata_gap", title="t", entity_id="1")
    fid = db.repair_get_findings()["items"][0]["id"]
    db.repair_set_finding_error(fid, "boom")
    assert db.repair_get_finding(fid)["last_error"] == "boom"
