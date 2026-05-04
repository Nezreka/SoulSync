"""Phase A pinning tests for QobuzClient — UPDATED for Phase C4.

Post-C4 the client uses engine.worker for thread + state management.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.qobuz_client import QobuzClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def qobuz_client_with_engine():
    client = QobuzClient.__new__(QobuzClient)
    client.download_path = Path('./test_qobuz_downloads')
    client.shutdown_check = None
    client._engine = None
    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


def test_download_returns_none_for_invalid_filename_format(qobuz_client_with_engine):
    client, _ = qobuz_client_with_engine
    result = _run_async(client.download('qobuz', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(qobuz_client_with_engine):
    client, _ = qobuz_client_with_engine
    result = _run_async(client.download('qobuz', 'not-int||title', 0))
    assert result is None


def test_download_returns_none_when_engine_not_wired():
    client = QobuzClient.__new__(QobuzClient)
    client._engine = None
    result = _run_async(client.download('qobuz', '12345||x', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(qobuz_client_with_engine):
    client, _ = qobuz_client_with_engine
    with patch.object(client, '_download_sync', return_value='/tmp/x.flac'):
        result = _run_async(client.download('qobuz', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_engine_record_with_initial_state(qobuz_client_with_engine):
    client, engine = qobuz_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('qobuz', '999||My Qobuz Song', 0))
        started.wait(timeout=1.0)
        record = engine.get_record('qobuz', download_id)

        assert record is not None
        assert record['filename'] == '999||My Qobuz Song'
        assert record['username'] == 'qobuz'
        assert record['track_id'] == 999
        assert record['display_name'] == 'My Qobuz Song'
        release.set()


def test_get_all_downloads_reads_engine_records(qobuz_client_with_engine):
    client, engine = qobuz_client_with_engine
    engine.add_record('qobuz', 'dl-1', {
        'id': 'dl-1', 'filename': '111||A', 'username': 'qobuz',
        'state': 'InProgress, Downloading', 'progress': 50.0,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 1
    assert result[0].id == 'dl-1'


def test_cancel_download_marks_cancelled(qobuz_client_with_engine):
    client, engine = qobuz_client_with_engine
    engine.add_record('qobuz', 'dl-1', {'id': 'dl-1', 'state': 'InProgress, Downloading'})

    ok = _run_async(client.cancel_download('dl-1', None, remove=False))
    assert ok is True
    assert engine.get_record('qobuz', 'dl-1')['state'] == 'Cancelled'


def test_clear_all_completed_drops_only_terminal_records(qobuz_client_with_engine):
    client, engine = qobuz_client_with_engine
    engine.add_record('qobuz', 'done', {'id': 'done', 'state': 'Completed, Succeeded'})
    engine.add_record('qobuz', 'live', {'id': 'live', 'state': 'InProgress, Downloading'})

    _run_async(client.clear_all_completed_downloads())

    assert engine.get_record('qobuz', 'done') is None
    assert engine.get_record('qobuz', 'live') is not None
