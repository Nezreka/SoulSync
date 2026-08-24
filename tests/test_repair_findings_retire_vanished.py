"""A finding whose file is gone must not sit in the list for ever.

Findings store their own snapshot of a path. Nothing ever closed one whose file
had since been moved, replaced or deleted, so a scan's stale momentary view
stayed on screen with a Fix button that could only ever fail — the reported case
was three Corrupt Audio findings naming files that no longer existed.

The one thing a sweep must never do is retire findings because THIS process
cannot see the library (a Docker install whose catalogue holds the media
server's paths would lose every finding it has). So a finding is retired only
when its containing folder is right there and the file is not: the folder is the
proof that we are looking at the real library.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.repair_worker import RepairWorker


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


class _KeepOpen(sqlite3.Connection):
    def close(self):  # noqa: A003 - the test inspects the DB afterwards
        pass


@pytest.fixture()
def worker(tmp_path):
    conn = sqlite3.connect(":memory:", factory=_KeepOpen)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE repair_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL, finding_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'pending',
            entity_type TEXT, entity_id TEXT, file_path TEXT,
            title TEXT NOT NULL, description TEXT,
            details_json TEXT DEFAULT '{}', user_action TEXT,
            resolved_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT
        )
    """)
    conn.commit()
    w = RepairWorker.__new__(RepairWorker)
    w.db = _Db(conn)
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    w._bulk_fix_lock = threading.Lock()
    w.should_stop = False
    return w


def _add(worker, path, *, job_id="audio_corruption_detector",
         finding_type="corrupt_audio", status="pending"):
    cur = worker.db._conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, status, entity_type, "
        "entity_id, file_path, title) VALUES (?,?,?,'track','7',?, 'x')",
        (job_id, finding_type, status, str(path)))
    worker.db._conn.commit()
    return cur.lastrowid


def _row(worker, fid):
    return worker.db._conn.execute(
        "SELECT status, user_action FROM repair_findings WHERE id=?", (fid,)).fetchone()


def test_a_finding_whose_file_is_gone_is_retired(worker, tmp_path):
    album = tmp_path / "AC_DC" / "Back In Black"
    album.mkdir(parents=True)                       # the folder is still there
    fid = _add(worker, album / "02 - Shoot to Thrill.flac")   # the file is not

    assert worker.retire_vanished_findings("audio_corruption_detector") == 1
    assert tuple(_row(worker, fid)) == ("resolved", "obsolete")


def test_a_finding_whose_file_is_present_is_kept(worker, tmp_path):
    album = tmp_path / "AC_DC" / "Back In Black"
    album.mkdir(parents=True)
    track = album / "02 - Shoot to Thrill.flac"
    track.write_bytes(b"audio")
    fid = _add(worker, track)

    assert worker.retire_vanished_findings("audio_corruption_detector") == 0
    assert _row(worker, fid)["status"] == "pending"


def test_an_unreachable_library_retires_nothing(worker, tmp_path):
    """A Docker/NAS install stores the media server's paths. Neither the file
    nor its folder is visible here — that is not evidence the file is gone."""
    fid = _add(worker, "/music/AC_DC/Back In Black/02 - Shoot to Thrill.flac")

    assert worker.retire_vanished_findings("audio_corruption_detector") == 0
    assert _row(worker, fid)["status"] == "pending"


def test_other_jobs_are_left_alone(worker, tmp_path):
    album = tmp_path / "X" / "Y"
    album.mkdir(parents=True)
    mine = _add(worker, album / "gone.flac")
    theirs = _add(worker, album / "gone.flac", job_id="dead_file_cleaner",
                  finding_type="dead_file")

    assert worker.retire_vanished_findings("audio_corruption_detector") == 1
    assert _row(worker, mine)["status"] == "resolved"
    assert _row(worker, theirs)["status"] == "pending"


def test_resolved_and_dismissed_history_is_untouched(worker, tmp_path):
    album = tmp_path / "X" / "Y"
    album.mkdir(parents=True)
    done = _add(worker, album / "gone.flac", status="resolved")
    hidden = _add(worker, album / "gone.flac", status="dismissed")

    worker.retire_vanished_findings("audio_corruption_detector")

    assert _row(worker, done)["status"] == "resolved"
    assert _row(worker, hidden)["status"] == "dismissed"


def test_a_finding_without_a_path_is_not_a_candidate(worker):
    fid = _add(worker, "")
    assert worker.retire_vanished_findings("audio_corruption_detector") == 0
    assert _row(worker, fid)["status"] == "pending"
