"""Tests for the track-detail assembly (core/downloads/track_detail.py)."""

from __future__ import annotations

from core.downloads.track_detail import build_track_detail, classify_status_kind


# ── status classification ──────────────────────────────────────────────────

def test_classify_completed():
    assert classify_status_kind('completed') == 'completed'


def test_classify_quarantined_from_integrity_message():
    assert classify_status_kind('failed', 'File integrity check failed: Duration mismatch') == 'quarantined'


def test_classify_plain_failure():
    assert classify_status_kind('failed', 'No sources found') == 'failed'


def test_classify_not_found():
    assert classify_status_kind('not_found') == 'not_found'


def test_classify_in_progress():
    assert classify_status_kind('downloading') == 'in_progress'


# ── merge: task only ────────────────────────────────────────────────────────

def test_build_from_task_only():
    task = {
        'task_id': 't1',
        'status': 'completed',
        'track_info': {'name': 'HUMBLE.', 'artists': [{'name': 'Kendrick Lamar'}], 'album': {'name': 'DAMN.'}},
        'username': 'soulseek',
        'filename': '/music/HUMBLE.flac',
    }
    d = build_track_detail(task)
    assert d['status_kind'] == 'completed'
    assert d['title'] == 'HUMBLE.'
    assert d['artist'] == 'Kendrick Lamar'
    assert d['album'] == 'DAMN.'
    assert d['file_path'] == '/music/HUMBLE.flac'
    assert d['source'] == 'soulseek'


def test_quarantined_task_carries_entry_id_and_reason():
    task = {
        'task_id': 't2',
        'status': 'failed',
        'error_message': 'File integrity check failed: Duration mismatch',
        'quarantine_entry_id': '20260531_120000_song',
        'track_info': {'name': 'Clean', 'artists': ['Taylor Swift']},
    }
    d = build_track_detail(task)
    assert d['status_kind'] == 'quarantined'
    assert d['quarantine_entry_id'] == '20260531_120000_song'
    assert 'integrity' in d['reason'].lower()
    assert d['artist'] == 'Taylor Swift'  # string-form artist handled


# ── merge: history enriches ─────────────────────────────────────────────────

def test_history_enriches_provenance():
    task = {'task_id': 't3', 'status': 'completed', 'track_info': {'name': 'N95', 'artists': [{'name': 'Kendrick Lamar'}]}}
    history = {
        'title': 'N95', 'artist_name': 'Kendrick Lamar', 'album_name': 'Mr. Morale',
        'quality': 'FLAC 16bit', 'file_path': '/lib/N95.flac', 'acoustid_result': 'error',
        'download_source': 'Soulseek', 'thumb_url': 'http://x/cover.jpg',
        'source_track_title': 'N95', 'source_artist': 'Kendrick Lamar',
    }
    d = build_track_detail(task, history)
    assert d['file_path'] == '/lib/N95.flac'
    assert d['quality'] == 'FLAC 16bit'
    assert d['acoustid_result'] == 'error'
    assert d['source'] == 'Soulseek'
    assert d['thumb_url'] == 'http://x/cover.jpg'
    assert d['downloaded'] == {'title': 'N95', 'artist': 'Kendrick Lamar', 'album': 'Mr. Morale'}
    assert d['expected'] == {'title': 'N95', 'artist': 'Kendrick Lamar'}


def test_history_fills_missing_title_from_task():
    task = {'task_id': 't4', 'status': 'completed', 'track_info': {}}  # task has no track name
    history = {'title': 'Recovered Title', 'artist_name': 'Some Artist'}
    d = build_track_detail(task, history)
    assert d['title'] == 'Recovered Title'
    assert d['artist'] == 'Some Artist'
