"""Tests for the BPM backfill repair job (#1476)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.repair_jobs.bpm_backfill import BpmBackfillJob
from core.repair_jobs import get_all_jobs


def test_job_registered():
    jobs = get_all_jobs()
    assert 'bpm_backfill' in jobs
    assert jobs['bpm_backfill'] is BpmBackfillJob


def test_job_metadata():
    job = BpmBackfillJob
    assert job.job_id == 'bpm_backfill'
    assert job.display_name == 'BPM Backfill'
    assert job.default_enabled is False
    assert job.default_interval_hours == 168
    assert job.default_settings == {
        'use_deezer': True,
        'use_local_analysis': True,
    }
    assert job.auto_fix is False


def test_estimate_scope_counts_missing_bpm():
    """estimate_scope should count tracks with NULL or 0 bpm."""
    import sqlite3

    class FakeDB:
        def _get_connection(self):
            conn = sqlite3.connect(':memory:')
            conn.execute("CREATE TABLE tracks (id INTEGER, title TEXT, bpm REAL)")
            conn.execute("INSERT INTO tracks VALUES (1, 'Track 1', NULL)")
            conn.execute("INSERT INTO tracks VALUES (2, 'Track 2', 0)")
            conn.execute("INSERT INTO tracks VALUES (3, 'Track 3', 120.5)")
            conn.execute("INSERT INTO tracks VALUES (4, '', NULL)")
            return conn

    class FakeContext:
        db = FakeDB()
        config_manager = None

    job = BpmBackfillJob()
    # 2 tracks with missing BPM (NULL and 0); empty title excluded
    assert job.estimate_scope(FakeContext()) == 2
