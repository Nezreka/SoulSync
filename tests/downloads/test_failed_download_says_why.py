"""a failed streaming download says why, not "state: Errored" (#1349).

    "Since I updated, I cant download from Deezer anymore."
    -> the task just read "Download failed (state: Errored)"

the deezer client already wrote the reason onto its download record
(_set_error), but DownloadStatus had no slot for it, so every poll dropped it
and the user only saw the state. now the reason rides DownloadStatus into the
live transfer rows, and the monitor's give-up message uses it.

real engine, real worker thread, real deezer _download_sync; only deezer's
api answer is faked.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from core.deezer_download_client import DeezerDownloadClient
from core.download_engine.engine import DownloadEngine
from core.download_engine.worker import BackgroundDownloadWorker
from core.downloads import monitor as dm


def _deezer(engine, *, track_data=None, license_token='lt'):
    client = DeezerDownloadClient.__new__(DeezerDownloadClient)
    client._engine = engine
    client.shutdown_check = None
    client._license_token = license_token
    client._get_track_data = lambda track_id: track_data
    return client


def _run(engine, client):
    worker = BackgroundDownloadWorker(engine)
    download_id = worker.dispatch(
        'deezer', '3135556', 'Linkin Park - Numb', '3135556||Linkin Park - Numb',
        client._download_sync, username_override='deezer_dl',
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        record = engine.get_record('deezer', download_id)
        if record and record['state'] in ('Errored', 'Completed, Succeeded'):
            return download_id, record
        time.sleep(0.02)
    raise AssertionError('download never finished')


def test_the_reason_survives_the_worker_marking_it_errored():
    engine = DownloadEngine()
    client = _deezer(engine, track_data=None)
    _, record = _run(engine, client)
    assert record['state'] == 'Errored'
    status = client._record_to_status(record)
    assert 'expired ARL' in status.error


def test_no_license_token_points_at_the_arl_not_the_plan(monkeypatch):
    engine = DownloadEngine()
    client = _deezer(engine, track_data={'TRACK_TOKEN': 'tok'}, license_token=None)
    client._config = SimpleNamespace(get=lambda key, default=None: default)
    client._quality = 'flac'
    client._get_media_url = lambda token, quality: None
    _, record = _run(engine, client)
    assert 'refresh your ARL' in client._record_to_status(record).error


@pytest.fixture
def mon(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    m = dm.WebUIDownloadMonitor()
    m.monitoring = True
    return m


def test_the_reason_reaches_the_users_error_message(mon, monkeypatch):
    engine = DownloadEngine()
    client = _deezer(engine, track_data=None)
    download_id, record = _run(engine, client)

    class _Engine:
        async def get_all_downloads(self, exclude=()):
            return [client._record_to_status(r) for r in engine.iter_records_for_source('deezer')]

    monkeypatch.setattr(dm, 'download_orchestrator', SimpleNamespace(engine=_Engine()))
    monkeypatch.setattr(dm, 'config_manager',
                        SimpleNamespace(get=lambda key, default=None: 'deezer' if key == 'download_source.mode' else default))
    monkeypatch.setattr(dm, 'run_async', lambda coro: asyncio.run(coro))

    live = mon._get_live_transfers()
    row = live[f'download_id::{download_id}']
    assert 'expired ARL' in row['error']

    task = {
        'track_info': {'name': 'Numb'}, 'username': 'deezer_dl',
        'filename': '3135556||Linkin Park - Numb', 'download_id': download_id,
        'status': 'downloading', 'batch_id': 'b1', 'status_change_time': time.time(),
        'error_retry_count': 3,   # retries already spent, this is the give-up
    }
    ops = []
    mon._should_retry_task('t1', task, live, time.time(), ops)
    assert task['status'] == 'failed'
    assert 'expired ARL' in task['error_message']
    assert 'Soulseek' not in task['error_message']


def test_a_reasonless_failure_keeps_the_old_message(mon):
    task = {
        'track_info': {'name': 'Numb'}, 'username': 'somepeer', 'filename': 'x.flac',
        'download_id': 'd1', 'status': 'downloading', 'batch_id': 'b1',
        'status_change_time': time.time(), 'error_retry_count': 3,
    }
    live = {'somepeer::x.flac': {'state': 'Completed, Errored'}}
    mon._should_retry_task('t1', task, live, time.time(), [])
    assert task['error_message'].startswith('Soulseek transfer errored 3 times')
