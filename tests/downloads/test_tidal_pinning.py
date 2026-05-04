"""Phase A pinning tests for TidalDownloadClient's download lifecycle.

Tidal authenticates via tidalapi OAuth, fetches HLS manifests for a
track_id, demuxes the FLAC stream from MP4 container with ffmpeg,
and writes the result to disk. The thread worker + state-dict
pattern is identical to YouTube's — Phase C will lift both into
the engine. These tests pin the SHAPE of the per-download record
and the filename encoding so the lift can't drift the contract.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# tidalapi may not be importable; tidal_download_client guards for that.
from core.tidal_download_client import TidalDownloadClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def tidal_client():
    """A bare TidalDownloadClient — bypasses tidalapi.Session init.
    Tests that need an authenticated state set client.session.check_login
    via mock."""
    client = TidalDownloadClient.__new__(TidalDownloadClient)
    client.download_path = Path('./test_tidal_downloads')
    client.shutdown_check = None
    client.session = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    client._device_auth_future = None
    client._device_auth_link = None
    return client


# ---------------------------------------------------------------------------
# is_configured / is_authenticated
# ---------------------------------------------------------------------------


def test_is_authenticated_false_when_no_session(tidal_client):
    """Pinning: no session → not authenticated. Used by orchestrator
    fallback to skip Tidal when user hasn't logged in."""
    assert tidal_client.is_authenticated() is False


def test_is_authenticated_false_when_session_check_login_raises(tidal_client):
    """Pinning: tidalapi.Session.check_login() can raise on expired
    tokens. Client swallows + reports False — orchestrator skip
    behavior depends on this."""
    fake_session = type('FakeSession', (), {
        'check_login': lambda self: (_ for _ in ()).throw(RuntimeError("expired")),
    })()
    tidal_client.session = fake_session
    assert tidal_client.is_authenticated() is False


# ---------------------------------------------------------------------------
# download() — filename parsing + id contract
# ---------------------------------------------------------------------------


def test_download_returns_none_for_invalid_filename_format(tidal_client):
    """Pinning: Tidal encodes search results as `track_id||display`.
    Missing `||` → None (not exception)."""
    result = _run_async(tidal_client.download('tidal', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(tidal_client):
    """Pinning: track_id portion MUST parse as int. Tidal API uses
    integer track IDs. Non-int → None (not exception)."""
    result = _run_async(tidal_client.download('tidal', 'not-a-number||some title', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(tidal_client):
    """Pinning: valid `<int>||display` filename returns a UUID
    download_id immediately; download runs in background thread."""
    with patch('core.tidal_download_client.threading.Thread') as fake_thread_cls:
        fake_thread_cls.return_value.start = lambda: None
        result = _run_async(tidal_client.download('tidal', '12345||Some Song', 0))

    assert result is not None
    assert len(result) == 36  # UUID4 format


def test_download_populates_active_downloads_with_initial_state(tidal_client):
    """Pinning: per-download record schema. Engine refactor moves
    this dict into central state but the SHAPE must stay the same
    for status APIs / frontend / post-processing consumers."""
    with patch('core.tidal_download_client.threading.Thread') as fake_thread_cls:
        fake_thread_cls.return_value.start = lambda: None
        download_id = _run_async(
            tidal_client.download('tidal', '999||My Tidal Song', 0)
        )

    record = tidal_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['filename'] == '999||My Tidal Song'  # ORIGINAL encoded form
    assert record['username'] == 'tidal'
    assert record['state'] == 'Initializing'
    assert record['progress'] == 0.0
    assert record['size'] == 0  # filled in by worker once HLS manifest fetched
    assert record['track_id'] == 999  # parsed as int
    assert record['display_name'] == 'My Tidal Song'
    assert record['file_path'] is None


def test_download_spawns_daemon_thread_targeting_worker(tidal_client):
    """Pinning: daemon thread targeting `_download_thread_worker`
    with (download_id, track_id, display_name, original_filename).
    Phase C replaces this with `engine.dispatch_download(plugin, ...)`
    that calls `plugin._download_impl(track_id)`."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.tidal_download_client.threading.Thread', side_effect=capture_thread):
        _run_async(tidal_client.download('tidal', '777||Title', 0))

    assert captured_kwargs.get('daemon') is True
    assert captured_kwargs.get('target') == tidal_client._download_thread_worker
    args = captured_kwargs.get('args', ())
    assert len(args) == 4
    # Args: (download_id, track_id, display_name, original_filename)
    assert args[1] == 777  # track_id parsed as int
    assert args[2] == 'Title'
    assert args[3] == '777||Title'  # original encoded filename


# ---------------------------------------------------------------------------
# get_all_downloads()
# ---------------------------------------------------------------------------


def test_get_all_downloads_iterates_active_downloads(tidal_client):
    """Pinning: returns one DownloadStatus per entry in
    active_downloads. Engine refactor will replace this with a
    central query — the per-record-to-DownloadStatus translation
    must preserve the field mapping."""
    tidal_client.active_downloads = {
        'dl-1': {
            'id': 'dl-1', 'filename': '111||Song A', 'username': 'tidal',
            'state': 'InProgress, Downloading', 'progress': 50.0,
            'size': 1000, 'transferred': 500, 'speed': 100,
            'time_remaining': None,
        },
        'dl-2': {
            'id': 'dl-2', 'filename': '222||Song B', 'username': 'tidal',
            'state': 'Completed, Succeeded', 'progress': 100.0,
            'size': 2000, 'transferred': 2000, 'speed': 0,
            'time_remaining': None,
        },
    }
    result = _run_async(tidal_client.get_all_downloads())
    assert len(result) == 2
    assert {r.id for r in result} == {'dl-1', 'dl-2'}
    assert {r.username for r in result} == {'tidal'}
