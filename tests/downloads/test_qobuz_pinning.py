"""Phase A pinning tests for QobuzClient's download lifecycle.

Qobuz hits the Qobuz REST API + downloads HLS-segmented FLAC.
Same thread-worker + state-dict pattern as Tidal/HiFi — Phase C
will lift the threading. These tests pin the contract.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.qobuz_client import QobuzClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def qobuz_client():
    client = QobuzClient.__new__(QobuzClient)
    client.download_path = Path('./test_qobuz_downloads')
    client.shutdown_check = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    return client


def test_download_returns_none_for_invalid_filename_format(qobuz_client):
    """Pinning: filename without `||` → None, not exception."""
    result = _run_async(qobuz_client.download('qobuz', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(qobuz_client):
    """Pinning: Qobuz REST API uses int track IDs. Non-int → None."""
    result = _run_async(qobuz_client.download('qobuz', 'not-int||title', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(qobuz_client):
    """Pinning: valid `<int>||display` returns UUID download_id."""
    with patch('core.qobuz_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        result = _run_async(qobuz_client.download('qobuz', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_active_downloads_with_initial_state(qobuz_client):
    """Pinning: per-download record schema for engine extraction."""
    with patch('core.qobuz_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(qobuz_client.download('qobuz', '999||My Qobuz Song', 0))

    record = qobuz_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['filename'] == '999||My Qobuz Song'
    assert record['username'] == 'qobuz'
    assert record['state'] == 'Initializing'
    assert record['progress'] == 0.0
    assert record['track_id'] == 999
    assert record['display_name'] == 'My Qobuz Song'
    assert record['file_path'] is None


def test_download_spawns_daemon_thread_targeting_worker(qobuz_client):
    """Pinning: daemon thread → `_download_thread_worker(download_id, track_id, display_name, original_filename)`."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.qobuz_client.threading.Thread', side_effect=capture_thread):
        _run_async(qobuz_client.download('qobuz', '777||Title', 0))

    assert captured_kwargs.get('daemon') is True
    assert captured_kwargs.get('target') == qobuz_client._download_thread_worker
    args = captured_kwargs.get('args', ())
    assert len(args) == 4
    assert args[1] == 777
    assert args[2] == 'Title'
    assert args[3] == '777||Title'
