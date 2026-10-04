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


def _subject(track_id, bpm=None, title='Track', **extra):
    return {'track_id': track_id, 'title': title, 'bpm': bpm, 'is_primary': 1,
            'artist_name': 'Artist', 'album_title': 'Album',
            'path': f'/music/{track_id}.flac', **extra}


def _context(monkeypatch, subjects):
    import sqlite3
    from types import SimpleNamespace
    from core.repair_jobs.base import JobContext

    monkeypatch.setattr('core.repair_jobs.bpm_backfill.active_file_subjects',
                        lambda _db, _config: subjects)
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT, "
                 "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    findings = []
    context = JobContext(
        db=SimpleNamespace(_get_connection=lambda: conn),
        transfer_folder='',
        config_manager=SimpleNamespace(get=lambda _key, default=None: default),
        create_finding=lambda **kw: findings.append(kw) or True,
    )
    return context, findings


def test_estimate_scope_counts_missing_bpm(monkeypatch):
    """estimate_scope counts Library-v2 tracks with NULL or 0 bpm, one per track."""
    context, _ = _context(monkeypatch, [
        _subject(1), _subject(2, bpm=0), _subject(3, bpm=120.5), _subject(4, title=''),
        dict(_subject(1), is_primary=0, path='/music/1-copy.flac'),
    ])
    assert BpmBackfillJob().estimate_scope(context) == 2


def test_scan_reports_deezer_bpm_on_the_lib2_track(monkeypatch):
    class _Deezer:
        def get_track_details(self, deezer_id):
            return {'bpm': 128.04} if deezer_id == 'dz-1' else None

    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source',
                        lambda source: _Deezer())
    context, findings = _context(monkeypatch, [
        _subject(7, deezer_id='dz-1', file_id=70)])
    context.config_manager = type('C', (), {
        'get': staticmethod(lambda key, default=None:
                            {'use_deezer': True, 'use_local_analysis': False}
                            if key.endswith('.settings') else default)})()
    context.sleep_or_stop = lambda _seconds: False

    result = BpmBackfillJob().scan(context)

    assert result.findings_created == 1
    finding = findings[0]
    assert finding['entity_id'] == 'lib2:7'
    assert finding['details']['found_fields'] == {'bpm': 128.0}
    assert finding['details']['library_v2']['track_id'] == 7
