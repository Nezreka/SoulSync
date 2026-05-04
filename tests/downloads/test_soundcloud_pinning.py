"""Phase A pinning tests for SoundcloudClient — UPDATED for Phase C7.

SoundCloud's quirk: 3-part filename `track_id||permalink_url||display_name`
because yt-dlp consumes the URL, not the track_id, to actually download.
The engine record holds both fields so the worker can call
`_download_sync(download_id, permalink_url, display_name)` correctly.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.soundcloud_client import SoundcloudClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def sc_client_with_engine():
    client = SoundcloudClient.__new__(SoundcloudClient)
    client.download_path = Path('./test_sc_downloads')
    client.shutdown_check = None
    client._engine = None
    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


def test_download_returns_none_for_filename_with_too_few_parts(sc_client_with_engine):
    client, _ = sc_client_with_engine
    result = _run_async(client.download('soundcloud', 'just-id-no-url', 0))
    assert result is None


def test_download_returns_none_for_empty_track_id_or_url(sc_client_with_engine):
    client, _ = sc_client_with_engine
    assert _run_async(client.download('soundcloud', '||https://x.com/y', 0)) is None
    assert _run_async(client.download('soundcloud', 'track123||', 0)) is None


def test_download_returns_none_when_engine_not_wired():
    client = SoundcloudClient.__new__(SoundcloudClient)
    client._engine = None
    result = _run_async(client.download(
        'soundcloud', 'sc-1||https://soundcloud.com/x/y||T', 0,
    ))
    assert result is None


def test_download_accepts_three_part_filename_with_display(sc_client_with_engine):
    """Pinning: 3-part filename `track_id||permalink_url||display`
    is the canonical form. All three fields go into the engine record."""
    client, engine = sc_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.mp3'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download(
            'soundcloud',
            'sc-12345||https://soundcloud.com/artist/song||Some Display Title',
            0,
        ))
        started.wait(timeout=1.0)
        record = engine.get_record('soundcloud', download_id)

        assert record['track_id'] == 'sc-12345'
        assert record['permalink_url'] == 'https://soundcloud.com/artist/song'
        assert record['display_name'] == 'Some Display Title'
        release.set()


def test_download_falls_back_display_name_to_track_id_when_two_part(sc_client_with_engine):
    """Pinning: 2-part filename → display name = track_id."""
    client, engine = sc_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/x.mp3'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download(
            'soundcloud', 'sc-99||https://soundcloud.com/x/y', 0,
        ))
        started.wait(timeout=1.0)
        assert engine.get_record('soundcloud', download_id)['display_name'] == 'sc-99'
        release.set()


def test_download_populates_engine_record_with_initial_state(sc_client_with_engine):
    client, engine = sc_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/x.mp3'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download(
            'soundcloud', 'sc-1||https://soundcloud.com/x||Title', 0,
        ))
        started.wait(timeout=1.0)
        record = engine.get_record('soundcloud', download_id)
        assert record['username'] == 'soundcloud'
        assert record['state'] in ('Initializing', 'InProgress, Downloading')
        assert 'permalink_url' in record
        release.set()
