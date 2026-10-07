"""Tests for the BPM backfill repair job (#1476)."""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.repair_jobs.bpm_backfill import BpmBackfillJob
from core.repair_jobs import get_all_jobs
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


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


@pytest.mark.parametrize('track_id', ['navidrome-track-abc', '42'])
def test_fix_finding_writes_bpm_for_text_track_id(tmp_path, track_id):
    """Both opaque and digit-only track IDs must survive the fix path."""
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES ('artist-1', 'Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('album-1', 'artist-1', 'Album')")
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, server_source) "
            "VALUES (?, 'album-1', 'artist-1', 'Song', 'navidrome')", (track_id,)
        )
        finding_id = conn.execute(
            "INSERT INTO repair_findings "
            "(job_id, finding_type, entity_type, entity_id, title, details_json) "
            "VALUES ('bpm_backfill', 'bpm_backfill', 'track', ?, 'Missing BPM', ?)",
            (track_id, json.dumps({'track_id': track_id, 'found_fields': {'bpm': 112.3}})),
        ).lastrowid

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._config_manager = None
    result = worker.fix_finding(finding_id)

    with db._get_connection() as conn:
        track = conn.execute('SELECT bpm FROM tracks WHERE id = ?', (track_id,)).fetchone()
        finding = conn.execute('SELECT status FROM repair_findings WHERE id = ?', (finding_id,)).fetchone()
    assert result['success'] is True
    assert track['bpm'] == 112.3
    assert finding['status'] == 'resolved'


def test_metadata_fix_does_not_claim_success_when_track_is_gone(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db

    result = worker._fix_metadata_gap(
        'track', 'missing-track-id', None, {'found_fields': {'bpm': 112.3}}
    )

    assert result['success'] is False
    assert 'not found' in result['error']
