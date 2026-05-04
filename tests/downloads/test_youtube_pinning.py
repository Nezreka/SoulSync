"""Phase A pinning tests for YouTubeClient's download lifecycle.

YouTube uses a yt-dlp subprocess wrapped in a threading.Thread. The
upcoming engine refactor will lift the thread management + state
tracking + rate-limit semaphore OUT of this client and into the
central engine — leaving YouTubeClient as just `_download_impl`
(the yt-dlp subprocess invocation) + `search_videos` (the search
request) + auth/config.

These tests pin the OBSERVABLE BEHAVIOR that the engine will
preserve: filename encoding format, download_id shape, state-dict
schema, and the failure modes (invalid filename, etc.). They do
NOT exercise the yt-dlp subprocess itself — that's the
source-specific atomic operation that stays per-client through the
refactor.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.youtube_client import YouTubeClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def yt_client():
    """A YouTubeClient with a temp download path. Threading is NOT
    patched here — individual tests that don't want the background
    thread to actually run patch it themselves so the download_id
    can be returned + state-dict pinned without yt-dlp ever firing."""
    client = YouTubeClient.__new__(YouTubeClient)
    client.download_path = Path('./test_yt_downloads')
    client.shutdown_check = None
    client.matching_engine = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    client._download_semaphore = threading.Semaphore(1)
    client._download_delay = 3
    client._last_download_time = 0
    client.current_download_id = None
    client.current_download_progress = {
        'status': 'idle', 'percent': 0.0, 'downloaded_bytes': 0,
        'total_bytes': 0, 'speed': 0, 'eta': 0, 'filename': '',
    }
    client.progress_callback = None
    client.download_opts = {}
    return client


# ---------------------------------------------------------------------------
# download() — filename parsing + id contract
# ---------------------------------------------------------------------------


def test_download_returns_none_for_invalid_filename_format(yt_client):
    """Pinning: YouTube encodes the search result as `video_id||title`.
    A filename without `||` is invalid → None (not exception). This is
    the soft-fail signal the orchestrator's hybrid fallback relies on."""
    result = _run_async(yt_client.download('youtube', 'no-separator-here', 0))
    assert result is None


def test_download_returns_uuid_download_id_for_valid_filename(yt_client):
    """Pinning: a valid `video_id||title` filename produces a UUID
    download_id immediately. The actual download runs in a background
    thread; the orchestrator polls via get_download_status."""
    # Patch threading.Thread so the worker never actually runs (no
    # yt-dlp invocation, no real network).
    with patch('core.youtube_client.threading.Thread') as fake_thread_cls:
        fake_thread = fake_thread_cls.return_value
        fake_thread.start = lambda: None

        result = _run_async(yt_client.download('youtube', 'abc123||Some Song', 0))

    assert result is not None
    # UUID format — 36 chars with dashes at standard positions.
    assert len(result) == 36
    assert result.count('-') == 4


def test_download_populates_active_downloads_with_initial_state(yt_client):
    """Pinning: after `download()` returns, the engine refactor will
    move this dict into central state, but the SHAPE of the
    per-download record must stay the same. Frontend, status APIs,
    and post-processing all read these keys directly."""
    with patch('core.youtube_client.threading.Thread') as fake_thread_cls:
        fake_thread_cls.return_value.start = lambda: None
        download_id = _run_async(
            yt_client.download('youtube', 'video123||My Title', 5000)
        )

    record = yt_client.active_downloads[download_id]
    # Pin the state-dict schema. These keys are consumed by the
    # status API + frontend + matched_downloads_context lookups.
    assert record['id'] == download_id
    assert record['filename'] == 'video123||My Title'  # ORIGINAL encoded form, not parsed
    assert record['username'] == 'youtube'
    assert record['state'] == 'Initializing'  # Soulseek-style state name
    assert record['progress'] == 0.0
    assert record['size'] == 5000
    assert record['transferred'] == 0
    assert record['video_id'] == 'video123'
    assert record['url'] == 'https://www.youtube.com/watch?v=video123'
    assert record['title'] == 'My Title'
    assert record['file_path'] is None  # set by worker on completion


def test_download_spawns_daemon_thread_for_background_work(yt_client):
    """Pinning: the worker MUST be a daemon thread so it doesn't
    block process shutdown. Engine refactor's BackgroundDownloadWorker
    must preserve this."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        result = type('FakeThread', (), {'start': lambda self: None})()
        return result

    with patch('core.youtube_client.threading.Thread', side_effect=capture_thread):
        _run_async(yt_client.download('youtube', 'v||t', 0))

    assert captured_kwargs.get('daemon') is True


def test_download_thread_target_is_download_thread_worker(yt_client):
    """Pinning: the spawned thread runs `_download_thread_worker`.
    Phase C will replace this with `engine.dispatch_download(...)`
    that calls `plugin._download_impl(...)`. The contract that's
    preserved: a single thread per download, semaphore-serialized,
    state updates flow through `active_downloads`."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.youtube_client.threading.Thread', side_effect=capture_thread):
        _run_async(yt_client.download('youtube', 'v||t', 0))

    assert captured_kwargs.get('target') == yt_client._download_thread_worker
    # First positional arg of the target is the download_id, then url, title, original_filename.
    target_args = captured_kwargs.get('args', ())
    assert len(target_args) == 4
