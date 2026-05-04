"""Phase A pinning tests for HiFiClient's download lifecycle.

HiFi uses public hifi-api instances backed by Tidal-sourced metadata.
Same int track_id + thread-worker + state-dict pattern as Tidal/Qobuz,
EXCEPT the worker method is named `_download_worker` (no `_thread_`).
Engine refactor must preserve the worker target signature.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.hifi_client import HiFiClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def hifi_client():
    client = HiFiClient.__new__(HiFiClient)
    client.download_path = Path('./test_hifi_downloads')
    client.shutdown_check = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    return client


def test_download_returns_none_for_invalid_filename_format(hifi_client):
    result = _run_async(hifi_client.download('hifi', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(hifi_client):
    result = _run_async(hifi_client.download('hifi', 'not-int||title', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(hifi_client):
    with patch('core.hifi_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        result = _run_async(hifi_client.download('hifi', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_active_downloads_with_initial_state(hifi_client):
    with patch('core.hifi_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(hifi_client.download('hifi', '999||My HiFi Song', 0))

    record = hifi_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['filename'] == '999||My HiFi Song'
    assert record['username'] == 'hifi'
    assert record['state'] == 'Initializing'
    assert record['track_id'] == 999
    assert record['display_name'] == 'My HiFi Song'


def test_download_spawns_daemon_thread_targeting_download_worker(hifi_client):
    """Pinning: target is `_download_worker` (NOT `_thread_worker` like
    Tidal/Qobuz). Engine refactor's plugin contract must accommodate
    this naming variance OR force a rename — pinned here so the
    decision is conscious."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.hifi_client.threading.Thread', side_effect=capture_thread):
        _run_async(hifi_client.download('hifi', '777||Title', 0))

    assert captured_kwargs.get('daemon') is True
    assert captured_kwargs.get('target') == hifi_client._download_worker
    args = captured_kwargs.get('args', ())
    # HiFi's worker signature: (download_id, track_id, display_name) — 3 args, not 4
    assert len(args) == 3
    assert args[1] == 777
    assert args[2] == 'Title'
