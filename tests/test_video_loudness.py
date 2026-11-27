"""video loudness: measured in the background by the Loudness job, kept by
file name + size (a server scan rewriting media_files keeps it), newest
first, a time budget per run, failures given up on after three."""

from __future__ import annotations

import pytest

from core.video import loudness as VL


@pytest.fixture
def vdb(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(str(tmp_path / "video.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (id, tmdb_id, title) VALUES (1, 11, 'Old Film'), (2, 12, 'New Film')")
    conn.execute("INSERT INTO media_files (id, movie_id, relative_path, size_bytes) VALUES "
                 "(1, 1, 'Movies/Old Film (1990)/Old Film.mkv', 1000), "
                 "(2, 2, 'Movies/New Film (2026)/New Film.mkv', 2000)")
    conn.commit()
    conn.close()
    return db


class Ctx:
    def __init__(self, db, settings=None):
        self.db = db
        self.settings = settings or {}

    def report(self, **kw):
        pass

    def check_stop(self):
        pass


def test_the_schema_has_the_table_and_the_version_moved():
    from database.video_database import SCHEMA_VERSION
    assert SCHEMA_VERSION >= 79


def test_the_job_measures_newest_first_and_keeps_it_by_name_and_size(vdb):
    from core.video.repair.loudness_job import LoudnessJob
    order = []

    def measure(path):
        order.append(path)
        return {"integrated_lufs": -27.0 if "Old" in path else -16.0, "true_peak_db": -4.0, "lra": 11.0}

    res = LoudnessJob().scan(Ctx(vdb), measurer=measure, resolver=lambda r: "/mnt/" + r["relative_path"])
    assert res.auto_fixed == 2 and order[0].endswith("New Film.mkv")
    conn = vdb._get_connection()
    try:
        assert VL.lookup(conn, "Old Film.mkv", 1000) == {"integrated_lufs": -27.0, "true_peak_db": -4.0,
                                                         "lra": 11.0, "audio_stream": 0}
        # a server scan that rewrote the file rows still finds it
        conn.execute("DELETE FROM media_files")
        conn.execute("INSERT INTO media_files (id, movie_id, relative_path, size_bytes) VALUES "
                     "(9, 1, '/plex/Movies/Old Film.mkv', 1000)")
        conn.commit()
        assert VL.unmeasured(conn) == []
    finally:
        conn.close()
    assert LoudnessJob().estimate_scope(Ctx(vdb)) == 0


def test_failures_are_tried_three_times_then_left(vdb):
    from core.video.repair.loudness_job import LoudnessJob

    def broken(path):
        raise RuntimeError("no audio")

    for _ in range(3):
        res = LoudnessJob().scan(Ctx(vdb), measurer=broken, resolver=lambda r: r["relative_path"])
        assert res.errors == 2
    res = LoudnessJob().scan(Ctx(vdb), measurer=broken, resolver=lambda r: r["relative_path"])
    assert res.scanned == 0
    conn = vdb._get_connection()
    try:
        assert VL.lookup(conn, "Old Film.mkv", 1000) is None
    finally:
        conn.close()


def test_a_run_stops_when_its_time_is_up(vdb):
    from core.video.repair.loudness_job import LoudnessJob
    ticks = iter([0, 0, 2000, 2000, 2000])
    res = LoudnessJob().scan(Ctx(vdb, {"minutes_per_run": 10}),
                             measurer=lambda p: {"integrated_lufs": -20.0, "true_peak_db": -1.0},
                             resolver=lambda r: r["relative_path"], clock=lambda: next(ticks))
    assert res.scanned == 1


def test_an_unreachable_file_waits_for_the_next_run(vdb):
    from core.video.repair.loudness_job import LoudnessJob
    res = LoudnessJob().scan(Ctx(vdb), measurer=lambda p: {"integrated_lufs": -20.0},
                             resolver=lambda r: None)
    assert res.skipped == 2
    conn = vdb._get_connection()
    try:
        assert len(VL.unmeasured(conn)) == 2
    finally:
        conn.close()


def test_the_job_is_registered_as_read_only():
    from core.video.repair import get_all_jobs
    from core.video.repair.catalog import VIDEO_JOBS_WRITING_FILES, job_category
    jobs = get_all_jobs()
    assert "loudness" in jobs and job_category("loudness") == "Metadata"
    assert "loudness" not in VIDEO_JOBS_WRITING_FILES
