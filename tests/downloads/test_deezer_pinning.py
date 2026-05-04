"""Phase A pinning tests for DeezerDownloadClient — UPDATED for Phase C6.

Deezer has the same engine-driven dispatch as the other streaming
sources, with three Deezer-specific quirks preserved:
- track_id stays as STRING (Deezer GW API uses string IDs).
- Engine record's `username` slot is the legacy `'deezer_dl'`
  via worker username_override.
- Worker thread is named `deezer-dl-<track_id>` for diagnostics.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.deezer_download_client import DeezerDownloadClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def deezer_client_with_engine():
    client = DeezerDownloadClient.__new__(DeezerDownloadClient)
    client.download_path = Path('./test_deezer_downloads')
    client.shutdown_check = None
    client._authenticated = True
    client._engine = None
    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


def test_download_returns_none_when_not_authenticated(deezer_client_with_engine):
    client, _ = deezer_client_with_engine
    client._authenticated = False
    result = _run_async(client.download('deezer_dl', '12345||x', 0))
    assert result is None


def test_download_returns_none_when_engine_not_wired():
    client = DeezerDownloadClient.__new__(DeezerDownloadClient)
    client._engine = None
    client._authenticated = True
    result = _run_async(client.download('deezer_dl', '12345||x', 0))
    assert result is None


def test_download_track_id_stays_as_string(deezer_client_with_engine):
    """Pinning: Deezer GW API uses string IDs — engine record must
    keep track_id as str."""
    client, engine = deezer_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('deezer_dl', '999||X', 0))
        started.wait(timeout=1.0)
        record = engine.get_record('deezer', download_id)
        assert record['track_id'] == '999'
        assert isinstance(record['track_id'], str)
        release.set()


def test_download_username_slot_is_legacy_deezer_dl(deezer_client_with_engine):
    """Pinning: frontend status indicators key off `'deezer_dl'`,
    not the canonical `'deezer'`."""
    client, engine = deezer_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('deezer_dl', '999||x', 0))
        started.wait(timeout=1.0)
        assert engine.get_record('deezer', download_id)['username'] == 'deezer_dl'
        release.set()


def test_download_handles_missing_display_name_with_fallback(deezer_client_with_engine):
    """Pinning: filename without `||` synthesizes display name `Track <id>`."""
    client, engine = deezer_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/x.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('deezer_dl', '12345', 0))
        started.wait(timeout=1.0)
        assert engine.get_record('deezer', download_id)['display_name'] == 'Track 12345'
        release.set()


def test_download_engine_record_carries_error_slot(deezer_client_with_engine):
    """Pinning: Deezer-specific `error` slot for ARL re-auth failure
    messages must be present on init."""
    client, engine = deezer_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/x.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('deezer_dl', '999||X', 1024))
        started.wait(timeout=1.0)
        record = engine.get_record('deezer', download_id)
        assert 'error' in record
        assert record['error'] is None
        assert record['size'] == 1024
        release.set()


def test_get_all_downloads_reads_engine_records(deezer_client_with_engine):
    client, engine = deezer_client_with_engine
    engine.add_record('deezer', 'dl-1', {
        'id': 'dl-1', 'filename': '111||A', 'username': 'deezer_dl',
        'state': 'InProgress, Downloading', 'progress': 50.0,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 1
    assert result[0].id == 'dl-1'
    assert result[0].username == 'deezer_dl'
