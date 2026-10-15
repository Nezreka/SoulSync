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


# ---------------------------------------------------------------------------
# discord report (Specialmed): "500 scanned, 0 fixed, 0 findings". the scan
# stopped at 500, deezer's bpm was never read, and a media-server path
# skipped every local analysis without a word.
# ---------------------------------------------------------------------------

def _library_with_tracks(tmp_path, n, deezer=True):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Artist')")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'a1', 'Album')")
        for i in range(n):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, file_path, deezer_id) "
                "VALUES (?, 'al1', 'a1', ?, ?, ?)",
                (f't{i}', f'Song {i}', f'/server/music/song{i}.m4a', f'{1000 + i}' if deezer else None),
            )
    return db


def _context(db, findings, logs=None):
    from core.repair_jobs.base import JobContext

    def report(**kw):
        if logs is not None and kw.get('log_line'):
            logs.append((kw.get('log_type'), kw['log_line']))

    return JobContext(
        db=db, transfer_folder='/transfer', config_manager=None,
        create_finding=lambda **kw: findings.append(kw) or True,
        report_progress=report,
    )


class _Deezer:
    """a real DeezerClient shape: get_track_details builds the enhanced dict."""

    def get_track_details(self, track_id):
        from core.deezer_client import DeezerClient
        raw = {'id': int(track_id), 'title': 'x', 'bpm': 128.0, 'isrc': 'USX', 'artist': {'name': 'a'},
               'album': {'id': 1, 'title': 'b'}, 'duration': 200}
        return DeezerClient._build_enhanced_track(None, raw)


def test_scan_is_not_capped_at_500(tmp_path, monkeypatch):
    db = _library_with_tracks(tmp_path, 520)
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source', lambda s: _Deezer())
    findings = []
    job = BpmBackfillJob()
    ctx = _context(db, findings)
    ctx.sleep_or_stop = lambda s: False
    result = job.scan(ctx)
    assert result.scanned == 520
    assert len(findings) == 520
    assert job.estimate_scope(ctx) == 520


def test_deezer_bpm_and_isrc_reach_the_top_level():
    from core.deezer_client import DeezerClient
    out = DeezerClient._build_enhanced_track(None, {'id': 1, 'bpm': 97.5, 'isrc': 'GBX', 'duration': 1})
    assert out['bpm'] == 97.5
    assert out['isrc'] == 'GBX'


def test_deezer_bpm_becomes_a_finding(tmp_path, monkeypatch):
    db = _library_with_tracks(tmp_path, 1)
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source', lambda s: _Deezer())
    findings = []
    ctx = _context(db, findings)
    ctx.sleep_or_stop = lambda s: False
    BpmBackfillJob().scan(ctx)
    assert findings[0]['details']['bpm'] == 128.0
    assert findings[0]['details']['bpm_source'] == 'deezer'


def test_local_analysis_reads_the_resolved_path(tmp_path, monkeypatch):
    db = _library_with_tracks(tmp_path, 1, deezer=False)
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source', lambda s: None)
    seen = {}
    monkeypatch.setattr(
        'core.repair_jobs.bpm_backfill.resolve_library_file_path',
        lambda p, **kw: '/local/music/song0.m4a' if kw.get('transfer_folder') == '/transfer' else None,
    )

    def fake_analyze(path):
        seen['path'] = path
        return {'bpm': 101.2}

    monkeypatch.setattr('core.sample.analyze.analyze_track', fake_analyze)
    findings = []
    BpmBackfillJob().scan(_context(db, findings))
    assert seen['path'] == '/local/music/song0.m4a'
    assert findings[0]['details']['bpm_source'] == 'local'


def test_unreachable_files_and_failed_analysis_are_reported(tmp_path, monkeypatch):
    db = _library_with_tracks(tmp_path, 2, deezer=False)
    monkeypatch.setattr('core.repair_jobs.bpm_backfill.get_client_for_source', lambda s: None)
    monkeypatch.setattr(
        'core.repair_jobs.bpm_backfill.resolve_library_file_path',
        lambda p, **kw: '/local/song0.m4a' if p.endswith('song0.m4a') else None,
    )

    def boom(path):
        raise RuntimeError("ffmpeg isn't installed")

    monkeypatch.setattr('core.sample.analyze.analyze_track', boom)
    logs = []
    result = BpmBackfillJob().scan(_context(db, [], logs))
    errors = [line for kind, line in logs if kind == 'error']
    assert result.errors == 1
    assert any("ffmpeg isn't installed" in line for line in errors)
    assert any("1 track files couldn't be found" in line for line in errors)
