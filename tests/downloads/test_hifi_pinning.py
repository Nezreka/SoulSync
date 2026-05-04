"""Phase A pinning tests for HiFiClient — UPDATED for Phase C5."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.hifi_client import HiFiClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def hifi_client_with_engine():
    client = HiFiClient.__new__(HiFiClient)
    client.download_path = Path('./test_hifi_downloads')
    client.shutdown_check = None
    client._engine = None
    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


def test_download_returns_none_for_invalid_filename_format(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    result = _run_async(client.download('hifi', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    result = _run_async(client.download('hifi', 'not-int||title', 0))
    assert result is None


def test_download_returns_none_when_engine_not_wired():
    client = HiFiClient.__new__(HiFiClient)
    client._engine = None
    result = _run_async(client.download('hifi', '12345||x', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    with patch.object(client, '_download_sync', return_value='/tmp/x.flac'):
        result = _run_async(client.download('hifi', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_engine_record_with_initial_state(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('hifi', '999||My HiFi Song', 0))
        started.wait(timeout=1.0)
        record = engine.get_record('hifi', download_id)
        assert record['filename'] == '999||My HiFi Song'
        assert record['username'] == 'hifi'
        assert record['track_id'] == 999
        assert record['display_name'] == 'My HiFi Song'
        release.set()


def test_get_all_downloads_reads_engine_records(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    engine.add_record('hifi', 'dl-1', {
        'id': 'dl-1', 'filename': '111||A', 'username': 'hifi',
        'state': 'InProgress, Downloading', 'progress': 50.0,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 1
    assert result[0].id == 'dl-1'


def test_cancel_download_marks_cancelled(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    engine.add_record('hifi', 'dl-1', {'id': 'dl-1', 'state': 'InProgress, Downloading'})
    ok = _run_async(client.cancel_download('dl-1', None, remove=False))
    assert ok is True
    assert engine.get_record('hifi', 'dl-1')['state'] == 'Cancelled'
