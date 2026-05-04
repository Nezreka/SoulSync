"""Phase A pinning tests for LidarrDownloadClient's download lifecycle.

Lidarr is the special case in the dispatcher — it's an
ALBUM-grabber, not a track-grabber. When the user asks for a
track, Lidarr grabs the whole album, then we pick the wanted
track out (logic at the end of `_download_thread_worker`).

Engine refactor's plugin contract must accommodate album-only
sources OR Lidarr stays special. Pinning the current contract
forces the design decision to be conscious during Phase G.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.lidarr_download_client import LidarrDownloadClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def lidarr_client():
    client = LidarrDownloadClient.__new__(LidarrDownloadClient)
    client.download_path = Path('./test_lidarr_downloads')
    client.shutdown_check = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    client._url = 'http://lidarr.test'
    client._api_key = 'test-key'
    return client


def test_download_returns_none_when_not_configured():
    """Pinning: no Lidarr URL/key → None. Orchestrator hybrid skip
    behavior depends on this."""
    client = LidarrDownloadClient.__new__(LidarrDownloadClient)
    client._url = ''
    client._api_key = ''
    result = _run_async(client.download('lidarr', '12345||Album Name', 0))
    assert result is None


def test_download_returns_uuid_for_valid_filename(lidarr_client):
    """Pinning: valid filename → UUID download_id."""
    with patch('core.lidarr_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        result = _run_async(lidarr_client.download(
            'lidarr', '12345||Some Album', 0,
        ))
    assert result is not None
    assert len(result) == 36


def test_download_parses_album_foreign_id_from_filename(lidarr_client):
    """Pinning: filename format is ``album_foreign_id||display`` where
    `album_foreign_id` is the MusicBrainz album MBID Lidarr lookups
    use. Engine refactor's plugin contract must respect that this
    is an ALBUM identifier, not a track."""
    with patch('core.lidarr_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(lidarr_client.download(
            'lidarr', 'mbid-album-123||Some Album by Artist', 0,
        ))

    record = lidarr_client.active_downloads[download_id]
    assert record['album_foreign_id'] == 'mbid-album-123'
    assert record['display_name'] == 'Some Album by Artist'


def test_download_handles_filename_without_separator(lidarr_client):
    """Pinning: defensive — filename without `||` still produces a
    download record (album_foreign_id stays empty, display_name is
    the whole filename). Lidarr's worker tries lookup-by-display."""
    with patch('core.lidarr_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(lidarr_client.download(
            'lidarr', 'just-some-display-name', 0,
        ))

    assert download_id is not None
    record = lidarr_client.active_downloads[download_id]
    assert record['album_foreign_id'] == ''
    assert record['display_name'] == 'just-some-display-name'


def test_download_populates_active_downloads_with_album_oriented_state(lidarr_client):
    """Pinning: Lidarr's state-dict is SMALLER than streaming sources
    (no track_id, no transferred/speed/time_remaining — Lidarr
    polls Lidarr's queue API for those, doesn't track byte-level
    progress locally). Engine extraction must accommodate the
    smaller schema."""
    with patch('core.lidarr_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(lidarr_client.download(
            'lidarr', 'mbid-1||Album', 0,
        ))

    record = lidarr_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['username'] == 'lidarr'
    assert record['state'] == 'Initializing'
    assert record['progress'] == 0.0
    assert record['album_foreign_id'] == 'mbid-1'
    assert record['file_path'] is None


def test_download_spawns_daemon_thread_targeting_worker(lidarr_client):
    """Pinning: thread target is `_download_thread_worker(download_id,
    album_foreign_id, display_name)` — 3 args, not 4 like streaming
    sources. Lidarr doesn't need original_filename because the album
    foreign id IS the unique key."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.lidarr_download_client.threading.Thread', side_effect=capture_thread):
        _run_async(lidarr_client.download('lidarr', 'mbid-x||Album', 0))

    assert captured_kwargs.get('daemon') is True
    assert captured_kwargs.get('target') == lidarr_client._download_thread_worker
    args = captured_kwargs.get('args', ())
    assert len(args) == 3  # 3-arg signature
    assert args[1] == 'mbid-x'
    assert args[2] == 'Album'
