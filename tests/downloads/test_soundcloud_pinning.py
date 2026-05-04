"""Phase A pinning tests for SoundcloudClient's download lifecycle.

SoundCloud is anonymous-only (no auth required). Uses yt-dlp's
``scsearch:`` for search, downloads via yt-dlp subprocess. Different
filename format from every other source: 3-part
``track_id||permalink_url||display_name`` because yt-dlp needs the
permalink URL, not the track_id, to actually download.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.soundcloud_client import SoundcloudClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def sc_client():
    client = SoundcloudClient.__new__(SoundcloudClient)
    client.download_path = Path('./test_sc_downloads')
    client.shutdown_check = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    return client


def test_download_returns_none_for_filename_with_too_few_parts(sc_client):
    """Pinning: SoundCloud needs at LEAST `track_id||permalink_url`.
    A 1-part filename → None. Engine refactor's filename parsing
    must keep the 2-part minimum."""
    result = _run_async(sc_client.download('soundcloud', 'just-id-no-url', 0))
    assert result is None


def test_download_returns_none_for_empty_track_id_or_url(sc_client):
    """Pinning: defensive — if EITHER side of `||` is empty, refuse."""
    result = _run_async(sc_client.download('soundcloud', '||https://soundcloud.com/x', 0))
    assert result is None
    result = _run_async(sc_client.download('soundcloud', 'track123||', 0))
    assert result is None


def test_download_accepts_three_part_filename_with_display(sc_client):
    """Pinning: 3-part filename `track_id||permalink_url||display`
    is the canonical form. Display name is extracted as the third
    field."""
    with patch('core.soundcloud_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(sc_client.download(
            'soundcloud',
            'sc-12345||https://soundcloud.com/artist/song||Some Display Title',
            0,
        ))

    record = sc_client.active_downloads[download_id]
    assert record['track_id'] == 'sc-12345'
    assert record['permalink_url'] == 'https://soundcloud.com/artist/song'
    assert record['display_name'] == 'Some Display Title'


def test_download_falls_back_display_name_to_track_id_when_two_part(sc_client):
    """Pinning: when display name is missing (2-part filename), the
    track_id IS the display name. Used for some search result
    encodings that don't carry a separate display."""
    with patch('core.soundcloud_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(sc_client.download(
            'soundcloud', 'sc-99||https://soundcloud.com/x/y', 0,
        ))

    record = sc_client.active_downloads[download_id]
    assert record['display_name'] == 'sc-99'


def test_download_populates_active_downloads_with_initial_state(sc_client):
    with patch('core.soundcloud_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(sc_client.download(
            'soundcloud', 'sc-1||https://soundcloud.com/x||Title', 0,
        ))

    record = sc_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['username'] == 'soundcloud'
    assert record['state'] == 'Initializing'
    assert record['progress'] == 0.0
    assert record['file_path'] is None
    # Permalink URL stays as a slot — yt-dlp downloads from URL not track_id
    assert 'permalink_url' in record


def test_download_spawns_daemon_thread_with_permalink_url_arg(sc_client):
    """Pinning: thread target signature is
    `(download_id, permalink_url, display_name, original_filename)`.
    Critical: the URL (not the track_id) is what yt-dlp consumes."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.soundcloud_client.threading.Thread', side_effect=capture_thread):
        _run_async(sc_client.download(
            'soundcloud', 'sc-1||https://soundcloud.com/x||Title', 0,
        ))

    assert captured_kwargs.get('daemon') is True
    args = captured_kwargs.get('args', ())
    assert len(args) == 4
    assert args[1] == 'https://soundcloud.com/x'  # permalink_url, NOT track_id
    assert args[2] == 'Title'
