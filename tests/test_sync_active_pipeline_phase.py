"""The dashboard's live status covers a Sync & download run from its start.

The run refreshes and identifies for minutes before its sync step registers,
and the dashboard showed nothing live for all of that."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-syncactive-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'm.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


@pytest.fixture()
def clean_states():
    with web_server.sync_lock:
        saved_sync = dict(web_server.sync_states)
        web_server.sync_states.clear()
    with web_server.playlist_pipeline_progress_lock:
        saved_pipe = dict(web_server.playlist_pipeline_progress_states)
        web_server.playlist_pipeline_progress_states.clear()
    yield
    with web_server.sync_lock:
        web_server.sync_states.clear()
        web_server.sync_states.update(saved_sync)
    with web_server.playlist_pipeline_progress_lock:
        web_server.playlist_pipeline_progress_states.clear()
        web_server.playlist_pipeline_progress_states.update(saved_pipe)


def _pipeline(pid, status='running', phase='Identifying tracks...'):
    with web_server.playlist_pipeline_progress_lock:
        web_server.playlist_pipeline_progress_states[f'1:{pid}'] = {
            'playlist_id': pid, 'playlist_name': 'Radar Weekly',
            'status': status, 'progress': 30, 'phase': phase}


def test_an_identifying_run_shows_live(clean_states):
    _pipeline(3942)
    rows = web_server._active_playlist_sync_summaries()
    assert rows == [{'playlist_id': 'auto_mirror_3942', 'playlist_name': 'Radar Weekly',
                     'status': 'running',
                     'progress': {'progress': 30, 'current_step': 'Identifying tracks...',
                                  'playlist_name': 'Radar Weekly'}}]


def test_the_sync_step_takes_over_once_it_registers(clean_states):
    _pipeline(3942, phase='Syncing...')
    with web_server.sync_lock:
        web_server.sync_states['auto_mirror_3942'] = {
            'status': 'syncing', 'playlist_name': 'Radar Weekly', 'progress': {'progress': 50}}
    rows = web_server._active_playlist_sync_summaries()
    assert [r['playlist_id'] for r in rows] == ['auto_mirror_3942']
    assert rows[0]['status'] == 'syncing'


def test_a_finished_run_is_not_live(clean_states):
    _pipeline(3942, status='finished')
    assert web_server._active_playlist_sync_summaries() == []
