"""A dead_file finding is a snapshot, and nothing used to close it.

`retire_vanished_findings` skips `dead_file` on purpose (the absence is the
finding), so a finding whose track row was later deleted, or whose file came
back, stayed pending for ever. Its Fix either deleted a row or re-downloaded a
track that was fine.

The sweep here retires on positive evidence only: the row is gone from
`tracks`, or the file now resolves. It never retires because this process
cannot see the library.

Library v2 port: the subject is `lib2:<track id>`, the dead file a
`lib2_track_files` row in `missing_confirmed`. A file row a rescan found again,
a delete retired or a repoint moved is the same positive evidence.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.library2.schema import ensure_library_v2_schema
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
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Artist')")
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Album')")
    conn.commit()
    w = RepairWorker.__new__(RepairWorker)
    w.db = _Db(conn)
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    w._bulk_fix_lock = threading.Lock()
    w.should_stop = False
    return w


def _track(worker, track_id, path, state="missing_confirmed"):
    conn = worker.db._conn
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, 1, 'x')", (track_id,))
    conn.execute("INSERT INTO lib2_track_files (track_id, path, file_state, is_primary)"
                 " VALUES (?, ?, ?, 1)", (track_id, str(path), state))
    conn.commit()


def _live_files(worker, track_id):
    return worker.db._conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files WHERE track_id = ?"
        " AND file_state NOT IN ('deleted')", (track_id,)).fetchone()[0]


def _finding(worker, track_id, path, *, job_id="dead_file_cleaner",
             finding_type="dead_file"):
    cur = worker.db._conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, entity_type, entity_id, "
        "file_path, title) VALUES (?,?,'track',?,?,'x')",
        (job_id, finding_type, f"lib2:{track_id}", str(path)))
    worker.db._conn.commit()
    return cur.lastrowid


def _row(worker, fid):
    return tuple(worker.db._conn.execute(
        "SELECT status, user_action FROM repair_findings WHERE id=?", (fid,)).fetchone())


def test_a_finding_whose_track_row_is_gone_is_retired(worker, tmp_path):
    fid = _finding(worker, 1, tmp_path / "Artist" / "gone.flac")   # no tracks row

    assert worker.retire_resolved_dead_file_findings() == 1
    assert _row(worker, fid) == ("resolved", "obsolete")


def test_a_finding_whose_file_is_back_is_retired(worker, tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    path = album / "01 - back.flac"
    path.write_bytes(b"audio")
    _track(worker, 1, path)
    fid = _finding(worker, 1, path)

    assert worker.retire_resolved_dead_file_findings() == 1
    assert _row(worker, fid) == ("resolved", "obsolete")


@pytest.mark.parametrize("state", ["active", "deleted"])
def test_a_file_row_a_rescan_found_or_a_delete_retired_is_retired(worker, tmp_path, state):
    path = tmp_path / "Artist" / "Album" / "03.flac"      # not reachable from here
    _track(worker, 1, path, state=state)
    fid = _finding(worker, 1, path)

    assert worker.retire_resolved_dead_file_findings() == 1
    assert _row(worker, fid) == ("resolved", "obsolete")


def test_a_repointed_file_retires_the_finding_for_the_old_path(worker, tmp_path):
    _track(worker, 1, tmp_path / "Artist" / "Album" / "new.flac")
    fid = _finding(worker, 1, tmp_path / "Artist" / "Album" / "old.flac")

    assert worker.retire_resolved_dead_file_findings() == 1
    assert _row(worker, fid) == ("resolved", "obsolete")


def test_a_genuinely_dead_file_stays_pending(worker, tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    path = album / "02 - gone.flac"                  # row is there, file is not
    _track(worker, 1, path)
    fid = _finding(worker, 1, path)

    assert worker.retire_resolved_dead_file_findings() == 0
    assert _row(worker, fid) == ("pending", None)


def test_an_unreachable_library_retires_nothing(worker, tmp_path):
    # the folder is not there either: this process may simply not see the library
    path = tmp_path / "not-mounted" / "Artist" / "Album" / "01.flac"
    _track(worker, 1, path)
    fid = _finding(worker, 1, path)

    assert worker.retire_resolved_dead_file_findings() == 0
    assert _row(worker, fid) == ("pending", None)


def test_other_jobs_and_types_are_left_alone(worker, tmp_path):
    gone = tmp_path / "Artist" / "gone.flac"
    other_job = _finding(worker, 1, gone, job_id="audio_corruption_detector",
                         finding_type="corrupt_audio")
    other_type = _finding(worker, 2, gone, finding_type="orphan_file")

    assert worker.retire_resolved_dead_file_findings() == 0
    assert _row(worker, other_job) == ("pending", None)
    assert _row(worker, other_type) == ("pending", None)


def test_fix_refuses_a_finding_whose_file_exists(worker, tmp_path):
    path = tmp_path / "Artist" / "Album" / "01 - here.flac"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"audio")
    _track(worker, 1, path)

    for action in ("remove", "redownload"):
        result = worker._fix_dead_file(
            "track", "lib2:1", str(path), {"_fix_action": action, "title": "x"})
        assert result["success"] is False
        assert "out of date" in result["error"]
    assert _live_files(worker, 1) == 1


def test_fix_remove_still_deletes_the_row_of_a_dead_file(worker, tmp_path):
    path = tmp_path / "Artist" / "Album" / "02 - gone.flac"
    path.parent.mkdir(parents=True)
    _track(worker, 1, path)

    result = worker._fix_dead_file(
        "track", "lib2:1", str(path), {"_fix_action": "remove", "title": "x"})

    # the native fix hands the file delete and the track decision to the
    # shared maintenance sync, which retires the file row
    assert result["success"] is True
    assert result["action"] == "removed"
    assert result["library_v2_file_deleted"] is True
    assert result["repair_intent"] == "remove"


# --- through the real _run_job seam ------------------------------------------


def _run_cleaner(tmp_path, result):
    """a dead_file finding for a track row that no longer exists, then one
    cleaner run through _run_job. returns the finding's status after."""
    from database.music_database import MusicDatabase

    w = RepairWorker(database=MusicDatabase(str(tmp_path / 'music.db')))
    w._config_manager = None
    w.transfer_folder = str(tmp_path)

    class _Cleaner:
        job_id = 'dead_file_cleaner'
        display_name = 'Dead File Cleaner'
        auto_fix = False
        writes_library_files = False

        def scan(self, context):
            return result

    w._jobs = {'dead_file_cleaner': _Cleaner()}
    w.get_job_config = lambda jid: {'settings': {}}
    with w.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO repair_findings (job_id, finding_type, severity, status, entity_type, "
            "entity_id, file_path, title) VALUES ('dead_file_cleaner', 'dead_file', 'warning', "
            "'pending', 'track', 'lib2:999', '/music/gone.mp3', 'Missing file: Gone')")
        conn.commit()
    w._run_job('dead_file_cleaner', forced=True)
    with w.db._get_connection() as conn:
        return tuple(conn.execute(
            "SELECT status, user_action FROM repair_findings WHERE entity_id = 'lib2:999'").fetchone())


def test_a_completed_cleaner_run_retires_the_stale_finding(tmp_path):
    from core.repair_jobs.base import JobResult
    assert _run_cleaner(tmp_path, JobResult(scanned=10)) == ('resolved', 'obsolete')


def test_a_cleaner_run_that_stopped_early_retires_nothing(tmp_path):
    # a partial run is not evidence, same rule as the other sweeps
    from core.repair_jobs.base import JobResult
    assert _run_cleaner(tmp_path, JobResult(scanned=3, stopped_early='quit')) == ('pending', None)
