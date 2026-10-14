"""Missing Subtitles maintenance job: the library-wide "wanted" sweep.

Dry run off (default) queues what's missing; dry run on raises one finding per
movie/show and approve queues it. Real VideoDatabase + real temp video files;
ffprobe is stubbed so embedded tracks only come from what a test hands it.
"""

from __future__ import annotations

import os

import pytest

from core.video import organization
from core.video.repair import get_all_jobs
from core.video.repair.worker import VideoRepairWorker
from core.video.subtitles import existing as ex
from database.video_database import VideoDatabase

JOB = "missing_subtitles"
SRT = "1\n00:00:01,000 --> 00:00:02,000\nhello there\n"


@pytest.fixture(autouse=True)
def _no_real_ffprobe(monkeypatch):
    ex.clear_probe_cache()
    monkeypatch.setattr(ex, "_probe_streams", lambda path: [])


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    organization.save(d, {"download_subtitles": True, "subtitle_langs": "en,es"})
    return d


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _file(tmp_path, name):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(4096))
    return str(p)


def _movie(db, tmp_path, mid=1, title="Heat", year=1995, path=None):
    path = path or _file(tmp_path, f"m{mid}/{title} ({year}).mkv")
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, server_source, server_id, title, year) "
                     "VALUES (?, 'plex', ?, ?, ?)", (mid, f"m{mid}", title, year))
        conn.execute("INSERT INTO media_files (movie_id, relative_path) VALUES (?, ?)",
                     (mid, path))
        conn.commit()
    finally:
        conn.close()
    return path


def _show(db, tmp_path, show_id=11, title="Severance", eps=((1, 1), (1, 2))):
    conn = db._get_connection()
    paths = {}
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title) "
                     "VALUES (?, 'plex', ?, ?)", (show_id, f"s{show_id}", title))
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (?, ?, 1)",
                     (show_id * 10, show_id))
        for i, (s, e) in enumerate(eps, 1):
            ep_id = show_id * 100 + i
            path = _file(tmp_path, f"{title}/S{s:02d}E{e:02d}.mkv")
            conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, "
                         "episode_number) VALUES (?, ?, ?, ?, ?)",
                         (ep_id, show_id, show_id * 10, s, e))
            conn.execute("INSERT INTO media_files (episode_id, relative_path) VALUES (?, ?)",
                         (ep_id, path))
            paths[ep_id] = path
        conn.commit()
    finally:
        conn.close()
    return paths


def _sidecar(video_path, suffix=".en.srt"):
    with open(os.path.splitext(video_path)[0] + suffix, "w", encoding="utf-8") as f:
        f.write(SRT)


def _rows(db, kind, vid):
    return {(r["language"], bool(r["forced"])): r["status"]
            for r in db.subtitle_get_for_video(kind, vid)}


def _pending(db):
    return db.repair_get_findings(status="pending")["items"]


def _dry(worker, on=True):
    worker.set_job_config(JOB, settings={"dry_run": on})


# ── registration ─────────────────────────────────────────────────────────────

def test_job_is_registered_with_dry_run_off_by_default():
    cls = get_all_jobs()[JOB]
    assert cls.default_settings == {"dry_run": False}
    assert cls.auto_fix is True and cls.finding_types == ("missing_subtitles",)
    import core.automation.blocks as blocks
    options = [o["value"] for b in blocks.ACTIONS if b["type"] == "video_run_repair_job"
               for f in b["config_fields"] if f["key"] == "job_id" for o in f["options"]]
    assert JOB in options


# ── dry run off: queue straight away ─────────────────────────────────────────

def test_default_run_queues_whats_missing_and_records_whats_there(db, worker, tmp_path):
    path = _movie(db, tmp_path)
    _sidecar(path, ".eng.srt")
    worker._run_job(JOB, forced=True)
    assert _rows(db, "movie", 1) == {("en", False): "have", ("es", False): "wanted"}
    assert _pending(db) == []
    assert db.repair_last_run(JOB)["auto_fixed"] == 1

    worker._run_job(JOB, forced=True)          # idempotent
    assert db.repair_last_run(JOB)["auto_fixed"] == 0


def test_embedded_tracks_and_rows_the_worker_owns_are_not_missing(db, worker, tmp_path, monkeypatch):
    _movie(db, tmp_path)
    monkeypatch.setattr(ex, "_probe_streams", lambda p: [
        {"codec_name": "subrip", "tags": {"language": "spa"}, "disposition": {}}])
    db.subtitle_want("movie", 1, "en")
    db.subtitle_mark("movie", 1, "en", "failed")   # the worker is retrying it
    worker._run_job(JOB, forced=True)
    assert _rows(db, "movie", 1) == {("en", False): "failed", ("es", False): "have"}
    assert db.repair_last_run(JOB)["auto_fixed"] == 0


def test_forced_toggle_and_per_title_overrides_shape_the_wants(db, worker, tmp_path):
    _movie(db, tmp_path)
    organization.save(db, {"download_subtitles": True, "subtitle_langs": "en",
                           "subtitle_forced": True})
    db.subtitle_override_set("movie", 1, ["fr"])
    worker._run_job(JOB, forced=True)
    assert _rows(db, "movie", 1) == {("fr", False): "wanted", ("fr", True): "wanted"}


def test_does_nothing_while_subtitle_downloads_are_off(db, worker, tmp_path):
    _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    assert len(_pending(db)) == 1
    organization.save(db, {"download_subtitles": False})
    _dry(worker, False)                       # would queue + retire if it ran
    worker._run_job(JOB, forced=True)
    assert db.subtitle_get_for_video("movie", 1) == []
    assert len(_pending(db)) == 1            # nothing scanned, nothing retired


def test_unreachable_files_are_skipped(db, worker, tmp_path):
    _movie(db, tmp_path, path="/nope/gone.mkv")
    worker._run_job(JOB, forced=True)
    assert db.subtitle_get_for_video("movie", 1) == []


# ── dry run on: findings, approve queues ─────────────────────────────────────

def test_dry_run_writes_nothing_and_raises_one_finding_per_title(db, worker, tmp_path):
    path = _movie(db, tmp_path)
    _sidecar(path)                            # en is there
    _show(db, tmp_path)                       # two episodes, nothing there
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    assert db.subtitle_get_for_video("movie", 1) == []     # not even 'have' rows
    found = {f["entity_type"]: f for f in _pending(db)}
    assert set(found) == {"movie", "show"}
    assert found["movie"]["description"] == "es"
    assert found["show"]["title"] == "Severance — 2 episodes missing subtitles"
    assert found["show"]["description"] == "2 episodes · en, es"
    items = found["show"]["details"]["items"]
    assert [it["label"] for it in items] == ["S01E01", "S01E02"]


def test_approve_queues_what_is_still_missing(db, worker, tmp_path):
    paths = _show(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    f = _pending(db)[0]
    first = sorted(paths)[0]
    _sidecar(paths[first], ".English.srt")    # landed between scan and approve
    res = worker.fix_finding(f["id"])
    assert res["success"] is True and res["action"] == "queued"
    assert res["message"] == "Queued 3 subtitles for Severance"
    assert _rows(db, "episode", first) == {("en", False): "have", ("es", False): "wanted"}


def test_approve_with_nothing_left_resolves_cleanly(db, worker, tmp_path):
    path = _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    f = _pending(db)[0]
    _sidecar(path, ".en.srt")
    _sidecar(path, ".es.srt")
    res = worker.fix_finding(f["id"])
    assert res["success"] is True and res["action"] == "resolved"


def test_a_changed_set_replaces_the_pending_finding(db, worker, tmp_path):
    path = _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    old = _pending(db)[0]
    assert old["description"] == "en, es"
    _sidecar(path, ".es.srt")
    worker._run_job(JOB, forced=True)
    now = _pending(db)
    assert len(now) == 1 and now[0]["id"] != old["id"] and now[0]["description"] == "en"


def test_gaps_filled_elsewhere_retire_the_finding(db, worker, tmp_path):
    path = _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    _sidecar(path, ".en.srt")
    _sidecar(path, ".es.srt")
    worker._run_job(JOB, forced=True)
    assert _pending(db) == []


def test_turning_dry_run_off_queues_and_retires_the_findings(db, worker, tmp_path):
    _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    assert len(_pending(db)) == 1
    _dry(worker, False)
    worker._run_job(JOB, forced=True)
    assert _pending(db) == []
    assert _rows(db, "movie", 1) == {("en", False): "wanted", ("es", False): "wanted"}


def test_a_dismissed_finding_never_comes_back(db, worker, tmp_path):
    _movie(db, tmp_path)
    _dry(worker, True)
    worker._run_job(JOB, forced=True)
    f = _pending(db)[0]
    assert worker.dismiss_finding(f["id"])
    worker._run_job(JOB, forced=True)
    assert _pending(db) == []
