"""Cancel All closes the sync history row.

a download batch's sync_history row is opened with completed_at NULL and
only a completion check fills it in. cancel_batch marks the batch
'cancelled' and its tasks cancelled, and nothing ever runs a completion
check on it afterwards, so the row read "In progress" in the sync history
for good. the per-track cancel that finishes a batch goes through
check_batch_completion_v2, which is covered in tests/downloads.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-cancel-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'cancel.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def batch(monkeypatch):
    from core.runtime_state import download_batches, download_tasks
    download_tasks['c-t1'] = {'status': 'completed', 'track_index': 0, 'track_info': {'name': 'done'}}
    download_tasks['c-t2'] = {'status': 'downloading', 'track_index': 1, 'track_info': {'name': 'mid'}}
    download_tasks['c-t3'] = {'status': 'queued', 'track_index': 2, 'track_info': {'name': 'waiting'}}
    download_batches['cancel-me'] = {
        'phase': 'downloading', 'playlist_id': 'pl', 'playlist_name': 'PL',
        'queue': ['c-t1', 'c-t2', 'c-t3'], 'queue_index': 2, 'active_count': 1, 'max_concurrent': 1,
        'permanently_failed_tracks': [], 'cancelled_tracks': set(), 'analysis_results': [],
    }
    yield download_batches['cancel-me']
    download_batches.pop('cancel-me', None)
    for t in ('c-t1', 'c-t2', 'c-t3'):
        download_tasks.pop(t, None)


def test_cancel_all_closes_the_sync_history_row(batch, monkeypatch):
    closed = []
    monkeypatch.setattr(web_server, '_record_sync_history_completion',
                        lambda bid, b: closed.append((bid, b.get('phase'))))
    client = web_server.app.test_client()
    resp = client.post('/api/playlists/cancel-me/cancel_batch')
    assert resp.status_code == 200, resp.data
    assert resp.get_json()['cancelled_tasks'] == 2
    assert closed == [('cancel-me', 'cancelled')]


def test_a_history_write_failure_does_not_fail_the_cancel(batch, monkeypatch):
    def boom(bid, b):
        raise RuntimeError('db locked')
    monkeypatch.setattr(web_server, '_record_sync_history_completion', boom)
    client = web_server.app.test_client()
    resp = client.post('/api/playlists/cancel-me/cancel_batch')
    assert resp.status_code == 200
    assert batch['phase'] == 'cancelled'


def test_the_row_really_gets_completed_at(batch):
    """through the real writer: the row the cancel closes carries the tracks
    that finished before it."""
    import json
    from database.music_database import MusicDatabase
    db = MusicDatabase()
    db.add_sync_history_entry('cancel-me', 'pl', 'PL', 'spotify', 'playlist', json.dumps([]), total_tracks=3)
    client = web_server.app.test_client()
    assert client.post('/api/playlists/cancel-me/cancel_batch').status_code == 200
    with db._get_connection() as conn:
        row = conn.execute("SELECT completed_at, tracks_downloaded FROM sync_history WHERE batch_id = 'cancel-me'").fetchone()
    assert row['completed_at'] is not None, "the row still reads In progress"
    assert row['tracks_downloaded'] == 1
