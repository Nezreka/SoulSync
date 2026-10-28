"""Video Library Maintenance: the jobs & findings framework (music standard) —
the finding recurrence contract (#1882), run recording, worker config +
dispatch, and the missing-episodes job end to end (scan → finding → approve
→ wishlist rows with FULL context, the write-parity standard)."""

from __future__ import annotations

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)   # never .start()ed — tests drive it directly


def _seed_show(db, *, tmdb=500, missing=3, owned=1, specials=0, server_id="s1",
               title="Breaking Sad"):
    eps, n = [], 0
    for i in range(1, missing + owned + 1):
        n += 1
        eps.append({"episode_number": i, "title": f"Ep {i}", "server_id": f"{server_id}e{i}",
                    "air_date": "2020-01-%02d" % i, "still_url": f"https://img/e{i}.jpg",
                    "overview": f"about ep {i}",
                    "file": ({"path": f"/e{i}.mkv"} if i <= owned else None)})
    seasons = [{"season_number": 1, "episodes": eps}]
    if specials:
        seasons.append({"season_number": 0, "episodes": [
            {"episode_number": j, "title": f"Sp {j}", "server_id": f"{server_id}sp{j}",
             "air_date": "2019-01-0%d" % j} for j in range(1, specials + 1)]})
    return db.upsert_show_tree("plex", {"server_id": server_id, "title": title,
                                        "tmdb_id": tmdb, "seasons": seasons})


# ── DB layer: findings ───────────────────────────────────────────────────────
def _age_handled(db, fid, days=30):
    """Backdate a handled finding's resolved_at past the grace window."""
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_repair_findings "
                     "SET resolved_at=datetime('now', ?) WHERE id=?",
                     (f"-{days} days", fid))
        conn.commit()
    finally:
        conn.close()


def _rows(db, status=None):
    return db.repair_get_findings(status=status, limit=200)["items"]


def test_finding_dedup_basics(db):
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc")
    assert db.repair_create_finding("job", **kw) is True
    assert db.repair_create_finding("job", **kw) is False           # pending dupe
    # Different entity → new finding. NULL entities never dedup each other.
    assert db.repair_create_finding("job", finding_type="t", title="Y",
                                    entity_type="show", entity_id="1:def")
    assert db.repair_create_finding("job", finding_type="t", title="A", file_path="/a")
    assert db.repair_create_finding("job", finding_type="t", title="B", file_path="/b")
    assert db.repair_create_finding("job", finding_type="t", title="B2", file_path="/b") is False


def test_pending_finding_refreshes_in_place(db):
    kw = dict(finding_type="t", title="old", severity="info", description="d1",
              entity_type="show", entity_id="1:abc", file_path="/a",
              details={"expected_path": "/old"})
    assert db.repair_create_finding("job", **kw) is True
    assert db.repair_create_finding(
        "job", finding_type="t", title="new", severity="warning", description="d2",
        entity_type="show", entity_id="1:abc", file_path="/b",
        details={"expected_path": "/new"}) is False
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["title"] == "new" and rows[0]["severity"] == "warning"
    assert rows[0]["description"] == "d2" and rows[0]["file_path"] == "/b"
    assert rows[0]["details"]["expected_path"] == "/new"
    # A later claim without a path keeps the stored one.
    assert db.repair_create_finding(
        "job", finding_type="t", title="newer", entity_type="show",
        entity_id="1:abc") is False
    assert _rows(db)[0]["file_path"] == "/b"


def test_dismissed_finding_never_recurs(db):
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc",
              details={"expected_path": "/a"})
    assert db.repair_create_finding("job", **kw) is True
    fid = _rows(db)[0]["id"]
    db.repair_set_finding_status(fid, "dismissed")
    assert db.repair_create_finding("job", **kw) is False
    _age_handled(db, fid)
    assert db.repair_create_finding("job", **kw) is False           # age changes nothing
    # Not even when the claim itself changed — a dismissal is final.
    assert db.repair_create_finding(
        "job", finding_type="t", title="X", entity_type="show", entity_id="1:abc",
        details={"expected_path": "/different"}) is False
    assert len(_rows(db)) == 1


def test_resolved_finding_recurs_only_after_grace(db):
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc")
    assert db.repair_create_finding("job", **kw) is True
    fid = _rows(db)[0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="fixed")
    assert db.repair_create_finding("job", **kw) is False           # inside grace
    _age_handled(db, fid)
    assert db.repair_create_finding("job", **kw) is True            # problem is back
    rows = _rows(db)
    assert len(rows) == 2
    assert db.repair_get_finding(fid)["status"] == "resolved"      # history kept
    assert [r["status"] for r in rows].count("pending") == 1


def test_resolved_finding_recurs_immediately_when_target_changed(db):
    # #1882: a fix approved under the old template/rule must not block a
    # re-scan once the expected path itself moved (template edit, #1881).
    kw = dict(finding_type="naming_mismatch", title="X", entity_type="episode",
              entity_id="episode:1:2",
              details={"current_path": "/lib/old.mkv", "expected_path": "/lib/old.mkv"})
    assert db.repair_create_finding("naming_conformance", **kw) is True
    fid = _rows(db)[0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="renamed")
    assert db.repair_create_finding("naming_conformance", **kw) is False   # same claim, in grace
    moved = dict(kw, details={"current_path": "/lib/old.mkv",
                              "expected_path": "/lib/new.mkv"})
    assert db.repair_create_finding("naming_conformance", **moved) is True


def test_scan_superseded_finding_recurs_immediately(db):
    # repair_dismiss_absent retires pendings the library outgrew; that is
    # the system verifying the problem was gone, not a user dismissal and
    # not a fix in flight — so a scan that produces the finding again
    # raises it at once, with no grace window.
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc")
    assert db.repair_create_finding("job", **kw) is True
    fid = _rows(db)[0]["id"]
    assert db.repair_dismiss_absent("job", "t", keep_entity_ids=[]) == 1
    assert db.repair_get_finding(fid)["status"] == "dismissed"
    assert db.repair_create_finding("job", **kw) is True


def test_superseded_row_does_not_restart_a_resolved_rows_clock(db):
    # Resolved long ago → recurs after grace → the new pending gets
    # scan-retired while the file is briefly unlocatable → the problem's
    # reappearance must not be silenced by the retirement's fresh stamp.
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc")
    assert db.repair_create_finding("job", **kw) is True
    fid = _rows(db)[0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="fixed")
    _age_handled(db, fid)
    assert db.repair_create_finding("job", **kw) is True            # recurred
    assert db.repair_dismiss_absent("job", "t", keep_entity_ids=[]) == 1
    assert db.repair_create_finding("job", **kw) is True            # still news


def test_resolved_with_no_timestamp_stays_silent(db):
    # Conservative direction (music parity): a handled row whose
    # resolved_at can't be read counts as inside the grace window.
    kw = dict(finding_type="t", title="X", entity_type="show", entity_id="1:abc")
    assert db.repair_create_finding("job", **kw) is True
    fid = _rows(db)[0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="fixed")
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_repair_findings SET resolved_at=NULL WHERE id=?", (fid,))
        conn.commit()
    finally:
        conn.close()
    assert db.repair_create_finding("job", **kw) is False


def test_finding_lifecycle_and_counts(db):
    for i in range(3):
        db.repair_create_finding("job", finding_type="t", title=f"F{i}",
                                 entity_type="e", entity_id=str(i), details={"n": i})
    got = db.repair_get_findings(status="pending", limit=2)
    assert got["total"] == 3 and len(got["items"]) == 2
    assert got["items"][0]["details"]["n"] in (0, 1, 2)
    ids = [f["id"] for f in db.repair_get_findings()["items"]]
    assert db.repair_bulk_update_findings(ids[:2], "dismissed") == 2
    c = db.repair_counts()
    assert c["pending"] == 1 and c["dismissed"] == 2
    assert c["by_job"]["job"]["total"] == 1 and c["by_job"]["job"]["types"] == {"t": 1}
    db.repair_set_finding_status(ids[2], "resolved", action="wishlisted")
    f = db.repair_get_finding(ids[2])
    assert f["status"] == "resolved" and f["user_action"] == "wishlisted" and f["resolved_at"]
    assert db.repair_clear_findings(status="dismissed") == 2


def test_run_recording_and_stale_sweep(db):
    rid = db.repair_record_job_start("job")
    assert db.repair_last_run("job")["status"] == "running"
    db.repair_record_job_finish(rid, items_scanned=5, findings_created=2)
    last = db.repair_last_run("job")
    assert last["status"] == "completed" and last["items_scanned"] == 5
    assert last["duration_seconds"] is not None
    db.repair_record_job_start("job")                       # simulate a crash mid-run
    assert db.repair_sweep_stale_runs() == 1
    assert len(db.repair_history("job")) == 2


# ── DB layer: missing-episode enumeration ────────────────────────────────────
def test_missing_episode_rows(db):
    _seed_show(db, missing=3, owned=1, specials=2)
    rows = db.missing_episode_rows()
    assert len(rows) == 3                                    # aired, unowned, no specials
    assert all(r["season_number"] == 1 for r in rows)
    assert rows[0]["show_title"] == "Breaking Sad" and rows[0]["show_tmdb_id"] == 500
    assert len(db.missing_episode_rows(include_specials=True)) == 5
    # Future episodes never count as missing.
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET air_date='2099-01-01' WHERE episode_number=2 "
                 "AND season_number=1")
    conn.commit(); conn.close()
    assert len(db.missing_episode_rows()) == 2


# ── worker: config + scheduling surface ──────────────────────────────────────
def test_worker_config_roundtrip(worker):
    assert worker.master_enabled() is False
    worker.set_master(True)
    assert worker.master_enabled() is True
    cfg = worker.job_config("missing_episodes")
    assert cfg == {"enabled": False, "interval_hours": 24,
                   "settings": {"include_specials": False}}
    worker.set_job_config("missing_episodes", enabled=True, interval_hours=6,
                          settings={"include_specials": True})
    cfg = worker.job_config("missing_episodes")
    assert cfg["enabled"] and cfg["interval_hours"] == 6 and cfg["settings"]["include_specials"]
    info = {j["job_id"]: j for j in worker.get_all_job_info()}
    assert info["missing_episodes"]["enabled"]
    # All eight jobs registered and reporting.
    assert set(info) >= {"missing_episodes", "movie_collections", "quality_upgrade",
                         "broken_files", "metadata_gaps", "duplicate_movies",
                         "wishlist_audit", "youtube_ghosts"}


# ── the job: scan → findings (with supersede) ────────────────────────────────
def test_scan_creates_one_finding_per_show_and_dedups(db, worker):
    _seed_show(db, missing=3)
    _seed_show(db, tmdb=600, missing=12, server_id="s2", title="Gaps Galore")
    worker._run_job("missing_episodes", forced=True)
    got = db.repair_get_findings(status="pending")
    assert got["total"] == 2
    by_title = {f["title"]: f for f in got["items"]}
    small = by_title["Breaking Sad — 3 missing episodes"]
    assert small["severity"] == "info" and small["description"] == "S01: 3"
    assert len(small["details"]["episodes"]) == 3
    assert by_title["Gaps Galore — 12 missing episodes"]["severity"] == "warning"
    # Re-scan with nothing changed: same entity ids → pure dedup, still 2.
    worker._run_job("missing_episodes", forced=True)
    assert db.repair_get_findings(status="pending")["total"] == 2
    assert len(db.repair_history("missing_episodes")) == 2


def test_scan_supersedes_stale_pending_when_the_set_changes(db, worker):
    sid = _seed_show(db, missing=3)
    worker._run_job("missing_episodes", forced=True)
    old = db.repair_get_findings(status="pending")["items"][0]
    # One of the gaps gets a file → the missing set changed.
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET has_file=1 WHERE show_id=? AND episode_number=2", (sid,))
    conn.commit(); conn.close()
    worker._run_job("missing_episodes", forced=True)
    pend = db.repair_get_findings(status="pending")["items"]
    assert len(pend) == 1 and pend[0]["details"]["count"] == 2
    stale = db.repair_get_finding(old["id"])
    assert stale["status"] == "dismissed" and "superseded" in stale["user_action"]


# ── the job: approve == fix == wishlist (write-parity standard) ──────────────
def test_fix_wishlists_with_full_context(db, worker, monkeypatch):
    sid = _seed_show(db, missing=2)

    class FakeEngine:
        def tmdb_season(self, tmdb_id, sn):
            assert tmdb_id == 500 and sn == 1
            return {"poster_url": "https://img/season1.jpg",
                    "episodes": [{"episode_number": 2, "title": "TMDB Ep2",
                                  "still_url": "https://img/tmdb2.jpg",
                                  "overview": "tmdb about 2", "air_date": "2020-01-02"}]}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    worker._run_job("missing_episodes", forced=True)
    f = db.repair_get_findings(status="pending")["items"][0]
    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "wishlisted" and "2 episodes" in res["message"]
    assert db.repair_get_finding(f["id"])["status"] == "resolved"

    conn = db._get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM video_wishlist WHERE kind='episode' ORDER BY episode_number").fetchall()]
    conn.close()
    assert len(rows) == 2
    for r in rows:   # the art-less-orb standard: every context field present
        assert r["tmdb_id"] == 500 and r["title"] == "Breaking Sad"
        assert r["library_id"] == sid
        assert r["poster_url"] == f"/api/video/poster/show/{sid}"
        assert r["season_poster_url"] == "https://img/season1.jpg"
        assert r["status"] == "wanted"
    assert rows[0]["episode_title"] == "TMDB Ep2"            # ep2: TMDB preferred
    assert rows[0]["still_url"] == "https://img/tmdb2.jpg"
    assert rows[1]["episode_title"] == "Ep 3"                # ep3: DB fallback
    assert rows[1]["still_url"] == "https://img/e3.jpg"


def test_reapprove_of_reraised_missing_episodes_is_a_noop_success(db, worker, monkeypatch):
    """#1882 end to end: approve wishlists the episodes; a week later they
    still haven't landed, the scan re-raises the same set, and approving
    THAT succeeds as a no-op instead of erroring 'nothing could be
    wishlisted' (the wishlist add is idempotent)."""
    _seed_show(db, missing=2)

    class FakeEngine:
        def tmdb_season(self, tmdb_id, sn):
            return {"poster_url": None, "episodes": []}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    worker._run_job("missing_episodes", forced=True)
    f = db.repair_get_findings(status="pending")["items"][0]
    assert worker.fix_finding(f["id"])["success"]
    _age_handled(db, f["id"])
    worker._run_job("missing_episodes", forced=True)
    again = db.repair_get_findings(status="pending")["items"]
    assert len(again) == 1 and again[0]["id"] != f["id"]          # re-raised
    res = worker.fix_finding(again[0]["id"])
    assert res["success"] and "already on the wishlist" in res["message"]
    assert db.repair_get_finding(again[0]["id"])["status"] == "resolved"


def test_fix_requires_tmdb_match_and_dismiss_works(db, worker):
    db.repair_create_finding("missing_episodes", finding_type="missing_episodes",
                             title="Unmatched — 1 missing episode", entity_type="show",
                             entity_id="9:zzz",
                             details={"show_id": 9, "show_title": "Unmatched",
                                      "tmdb_id": None, "episodes": [
                                          {"season_number": 1, "episode_number": 1}]})
    f = db.repair_get_findings()["items"][0]
    res = worker.fix_finding(f["id"])
    assert not res["success"] and "TMDB" in res["error"]
    assert db.repair_get_finding(f["id"])["status"] == "pending"   # failed fix stays pending
    assert worker.dismiss_finding(f["id"])
    assert worker.fix_finding(f["id"]) == {"success": False, "error": "already handled"}


def test_bulk_fix_reports_mixed_results(db, worker, monkeypatch):
    _seed_show(db, missing=1)

    class FakeEngine:
        def tmdb_season(self, tmdb_id, sn):
            return {}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    worker._run_job("missing_episodes", forced=True)
    db.repair_create_finding("missing_episodes", finding_type="missing_episodes",
                             title="Broken", entity_type="show", entity_id="8:qqq",
                             details={"episodes": []})
    out = worker.bulk_fix_findings(job_id="missing_episodes")
    assert out["total"] == 2 and out["fixed"] == 1 and out["failed"] == 1
    assert not out["success"] and out["errors"]
