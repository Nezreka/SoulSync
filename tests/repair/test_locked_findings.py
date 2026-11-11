"""#2046: what a reviewed and locked album means to the repair jobs.

A job that would change the locked part files nothing. A job that reports
damage still reports, and only a person can run its fix.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from core.library2 import locks
from core.repair_worker import LOCKED_FIX_MESSAGE, RepairWorker
from database.music_database import MusicDatabase


@pytest.fixture(autouse=True)
def _fresh_locks():
    locks.reset_for_tests()
    yield
    locks.reset_for_tests()


@pytest.fixture
def setup(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with closing(db._get_connection()) as conn, conn:
        conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'A')")
        for album_id in (1, 2):
            conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(?,1,?)",
                         (album_id, f"Album {album_id}"))
            conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) VALUES(?,?,?,1)",
                         (album_id, album_id, f"T{album_id}"))
            conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                         (album_id, f"/m/{album_id}/1.flac"))
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker.transfer_folder, worker._config_manager = db, str(tmp_path), None
    worker._jobs = {}
    return db, worker


def _lock(db, album_id, scopes=None, entity="album"):
    with closing(db._get_connection()) as conn, conn:
        locks.set_lock(conn, entity, album_id, scopes)


def _file(worker, job_id, track_id, finding_type="x", **extra):
    return worker._create_finding(
        job_id=job_id, finding_type=finding_type, severity="info", entity_type="track",
        entity_id=f"lib2:{track_id}", file_path=f"/m/{track_id}/1.flac",
        title=f"finding {track_id}", description="d", details=extra or None)


def _rows(db):
    with closing(db._get_connection()) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT job_id, entity_id, status FROM repair_findings ORDER BY id")]


def test_a_job_that_changes_a_locked_part_files_nothing(setup):
    db, worker = setup
    _lock(db, 1, ["tags"])
    assert _file(worker, "library_retag", 1, "library_retag") is False
    assert _file(worker, "library_retag", 2, "library_retag") is True
    assert _rows(db) == [("library_retag", "lib2:2", "pending")]
    with closing(db._get_connection()) as conn:
        assert [s["what"] for s in locks.skips(conn, "album", 1)] == ["Library Retag"]


def test_only_the_locked_scope_keeps_a_job_away(setup):
    db, worker = setup
    _lock(db, 1, ["files"])
    assert _file(worker, "library_retag", 1, "library_retag") is True
    assert _file(worker, "library_reorganize", 1, "path_mismatch") is False


def test_a_finding_is_found_by_its_file_or_its_album_ids(setup):
    db, worker = setup
    _lock(db, 1, ["artwork"])
    by_file = worker._create_finding(
        job_id="missing_cover_art", finding_type="missing_cover_art", severity="info",
        entity_type="file", entity_id="", file_path="/m/1/1.flac", title="t", description="d")
    by_ids = worker._create_finding(
        job_id="missing_cover_art", finding_type="missing_cover_art", severity="info",
        entity_type="album", entity_id="legacy-9", file_path=None, title="t", description="d",
        details={"library_v2": {"album_ids": [1]}})
    assert (by_file, by_ids) == (False, False)
    assert _rows(db) == []


def test_damage_on_a_locked_album_is_still_reported_and_marked(setup):
    db, worker = setup
    _lock(db, 1)
    assert _file(worker, "dead_file_cleaner", 1, "dead_file") is True
    assert _file(worker, "dead_file_cleaner", 2, "dead_file") is True
    items = worker.get_findings(job_id="dead_file_cleaner")["items"]
    by_entity = {item["entity_id"]: item.get("locked") for item in items}
    assert by_entity == {"lib2:1": {"entity_type": "album", "entity_id": 1}, "lib2:2": None}


def test_a_bulk_fix_leaves_a_locked_finding_open_and_one_by_hand_goes_through(setup):
    db, worker = setup
    _lock(db, 1)
    _file(worker, "dead_file_cleaner", 1, "dead_file")
    with closing(db._get_connection()) as conn:
        finding_id = conn.execute("SELECT id FROM repair_findings").fetchone()[0]
    ran = []

    def execute(*_args):
        ran.append(locks.acting_as_user())
        return {"success": False, "error": "stub"}
    worker._execute_fix = execute

    refused = worker.fix_finding(finding_id)
    assert refused == {"success": False, "locked": True,
                       "error": LOCKED_FIX_MESSAGE.format(what="album")}
    assert ran == []
    with closing(db._get_connection()) as conn:
        row = conn.execute("SELECT status, fix_claimed_at FROM repair_findings").fetchone()
    assert tuple(row) == ("pending", None)

    by_hand = worker.fix_finding(finding_id, explicit=True)
    assert by_hand["error"] == "stub" and ran == [True]


def test_a_bulk_fix_on_an_open_album_runs_as_automation(setup):
    db, worker = setup
    _lock(db, 1)
    _file(worker, "dead_file_cleaner", 2, "dead_file")
    with closing(db._get_connection()) as conn:
        finding_id = conn.execute("SELECT id FROM repair_findings").fetchone()[0]
    ran = []
    worker._execute_fix = lambda *_a: ran.append(locks.acting_as_user()) or {
        "success": False, "error": "stub"}
    worker.fix_finding(finding_id)
    assert ran == [False]


def test_an_artist_lock_covers_findings_about_the_artist(setup):
    db, worker = setup
    _lock(db, 1, ["metadata"], entity="artist")
    filed = worker._create_finding(
        job_id="genre_cleanup", finding_type="genre_cleanup", severity="info",
        entity_type="artist", entity_id="lib2:1", file_path=None, title="t", description="d")
    assert filed is False
    # its albums are not covered by the artist's lock
    filed = worker._create_finding(
        job_id="genre_cleanup", finding_type="genre_cleanup", severity="info",
        entity_type="album", entity_id="lib2:1", file_path=None, title="t", description="d")
    assert filed is True


# ── every job says what a lock means to it ───────────────────────────────────

def test_every_job_declares_its_lock_scope():
    from core.repair_jobs import (
        JOB_LOCK_SCOPE, JOB_LOCK_SCOPES, RETIRED_JOB_IDS, get_all_jobs,
    )
    jobs = get_all_jobs()
    assert set(jobs) == set(JOB_LOCK_SCOPE), (
        "a job without a JOB_LOCK_SCOPE entry, or an entry without a job: "
        f"{sorted(set(jobs) ^ set(JOB_LOCK_SCOPE))}")
    assert set(JOB_LOCK_SCOPE.values()) <= set(JOB_LOCK_SCOPES)
    assert not set(JOB_LOCK_SCOPE) & RETIRED_JOB_IDS
    for job_id, cls in jobs.items():
        assert cls.lock_scope == JOB_LOCK_SCOPE[job_id]
    # the scopes a job may name are the scopes a lock has
    assert set(JOB_LOCK_SCOPES) - {"report", "none"} == set(locks.SCOPES)


def test_a_job_that_changes_tags_or_files_cannot_claim_to_touch_nothing():
    """the effects manifest already says what each job changes. a job that
    writes tags, artwork or paths has to name a scope, or report."""
    from core.repair_jobs import JOB_LIBRARY_V2_EFFECTS, JOB_LOCK_SCOPE
    writes = {"tags", "artwork", "path"}
    allowed = {
        "orphan_file_detector",   # files the library does not hold
        # wants an album's missing tracks, which a lock does not stop. the
        # tracklist and art it fetches go through the metadata writers, and
        # those turn a locked album away themselves
        "album_completeness",
    }
    wrong = sorted(job for job, scope in JOB_LOCK_SCOPE.items()
                   if scope == "none" and JOB_LIBRARY_V2_EFFECTS[job] & writes
                   and job not in allowed)
    assert wrong == []


def test_an_undeclared_job_cannot_register():
    from core.repair_jobs import JOB_DATA_BASIS, JOB_LIBRARY_V2_EFFECTS, register_job
    from core.repair_jobs.base import RepairJob

    class Undeclared(RepairJob):
        job_id = "undeclared_lock_job"

        def scan(self, context):
            return None

    JOB_DATA_BASIS["undeclared_lock_job"] = next(iter(JOB_DATA_BASIS.values()))
    JOB_LIBRARY_V2_EFFECTS["undeclared_lock_job"] = frozenset({"none"})
    try:
        with pytest.raises(ValueError, match="locked album"):
            register_job(Undeclared)
    finally:
        JOB_DATA_BASIS.pop("undeclared_lock_job")
        JOB_LIBRARY_V2_EFFECTS.pop("undeclared_lock_job")
