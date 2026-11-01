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
        'use_file_tags': True,
        'use_beatport': True,
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


# --- #1939: the file's own bpm, and beatport -----------------------------------


def _file_context(monkeypatch, tmp_path, subjects, settings):
    """a context whose db hands out fresh connections to one file"""
    import sqlite3
    from types import SimpleNamespace
    from core.repair_jobs.base import JobContext

    db_path = str(tmp_path / 'm.db')
    setup = sqlite3.connect(db_path)
    setup.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT, "
                  "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    setup.execute("CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, bpm REAL, updated_at TIMESTAMP)")
    for subject in subjects:
        setup.execute("INSERT INTO lib2_tracks(id, bpm) VALUES (?, ?)",
                      (subject['track_id'], subject.get('bpm')))
    setup.commit()
    setup.close()
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.active_file_subjects',
                        lambda _db, _config: subjects)
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source', lambda source: None)
    findings, changes = [], []
    context = JobContext(
        db=SimpleNamespace(_get_connection=lambda: sqlite3.connect(db_path)),
        transfer_folder='',
        config_manager=SimpleNamespace(get=lambda key, default=None:
                                       settings if key.endswith('.settings') else default),
        create_finding=lambda **kw: findings.append(kw) or True,
        report_change=lambda **kw: changes.append(kw),
    )
    context.sleep_or_stop = lambda _seconds: False
    return context, findings, changes, db_path


def _bpm_of(db_path, track_id):
    import sqlite3
    return sqlite3.connect(db_path).execute(
        "SELECT bpm FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]


def test_a_bpm_the_file_carries_is_taken_straight_in(monkeypatch, tmp_path):
    # the reporter's 184 tracks: bpm in the file, none in soulsync
    audio = tmp_path / 'a.flac'
    audio.write_bytes(b'x')
    monkeypatch.setattr('core.tag_writer.read_file_tags', lambda path: {'bpm': 174.0})
    context, findings, changes, db_path = _file_context(
        monkeypatch, tmp_path, [_subject(5, path=str(audio))],
        {'use_beatport': False, 'use_deezer': False, 'use_local_analysis': False})

    result = BpmBackfillJob().scan(context)

    assert _bpm_of(db_path, 5) == 174.0
    assert result.auto_fixed == 1 and findings == []
    assert changes[0]['details'] == {'bpm': 174.0, 'bpm_source': 'file'}


def test_junk_in_the_file_is_not_taken(monkeypatch, tmp_path):
    audio = tmp_path / 'a.flac'
    audio.write_bytes(b'x')
    monkeypatch.setattr('core.tag_writer.read_file_tags', lambda path: {'bpm': 0})
    context, _, _, db_path = _file_context(
        monkeypatch, tmp_path, [_subject(5, path=str(audio))],
        {'use_beatport': False, 'use_deezer': False, 'use_local_analysis': False})
    BpmBackfillJob().scan(context)
    assert _bpm_of(db_path, 5) is None


def test_beatport_answers_by_isrc_before_deezer(monkeypatch, tmp_path):
    calls = []

    def lookup(isrc, artist='', title='', allow_search=True, scraper=None):
        calls.append((isrc, allow_search))
        return {'bpm': 125} if isrc == 'GBUM71234567' else None

    monkeypatch.setattr('core.beatport.enrich.lookup', lookup)
    context, findings, _, _ = _file_context(
        monkeypatch, tmp_path,
        [_subject(9, isrc='GBUM71234567', album_genres='["Tech House"]', path='/gone/9.flac')],
        {'use_file_tags': True, 'use_deezer': True, 'use_local_analysis': False})

    BpmBackfillJob().scan(context)

    # an electronic genre lets beatport search; the label's bpm is offered
    assert calls == [('GBUM71234567', True)]
    assert findings[0]['details']['bpm_source'] == 'beatport'
    assert findings[0]['details']['found_fields'] == {'bpm': 125.0}


def test_beatport_does_not_search_for_a_track_that_isnt_electronic(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr('core.beatport.enrich.lookup',
                        lambda isrc, artist='', title='', allow_search=True, scraper=None:
                        calls.append(allow_search))
    context, _, _, _ = _file_context(
        monkeypatch, tmp_path,
        [_subject(9, isrc='USUM71234567', album_genres='["Folk"]', path='/gone/9.flac')],
        {'use_deezer': False, 'use_local_analysis': False})
    BpmBackfillJob().scan(context)
    # the metadata cache is still asked; beatport's search is not
    assert calls == [False]

