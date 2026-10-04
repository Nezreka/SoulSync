"""#1289: a job that quits on its own before the end is recorded as 'stopped'
with its reason, not a clean 'completed', through the real _run_job seam."""

from __future__ import annotations

from database.music_database import MusicDatabase
from core.repair_jobs.base import JobResult
from core.repair_worker import RepairWorker


def _worker(tmp_path, result):
    w = RepairWorker(database=MusicDatabase(str(tmp_path / 'music.db')))
    w._config_manager = None
    w.transfer_folder = str(tmp_path)

    class _J:
        job_id = 'quality_upgrade'
        display_name = 'Quality Upgrade Finder'
        auto_fix = False
        writes_library_files = False

        def scan(self, context):
            return result

    w._jobs = {'quality_upgrade': _J()}
    w.get_job_config = lambda jid: {'settings': {}}
    retired = []
    w.retire_vanished_findings = lambda jid: retired.append(jid) or 0
    return w, retired


def _last_run(w):
    with w.db._get_connection() as conn:
        row = conn.execute("SELECT status, error_text, items_scanned FROM repair_job_runs "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    return tuple(row)


def test_a_run_that_quit_early_is_recorded_as_stopped(tmp_path):
    w, retired = _worker(tmp_path, JobResult(
        scanned=3008, stopped_early='Spotify rate limit hit. Stopped at track 3008 of 8050.'))
    w._run_job('quality_upgrade', forced=True)
    assert _last_run(w) == ('stopped', 'Spotify rate limit hit. Stopped at track 3008 of 8050.', 3008)
    # a partial view is not evidence: findings are not retired off it
    assert retired == []


def test_a_full_run_is_still_completed(tmp_path):
    w, retired = _worker(tmp_path, JobResult(scanned=8050))
    w._run_job('quality_upgrade', forced=True)
    assert _last_run(w) == ('completed', None, 8050)
    assert retired == ['quality_upgrade']
