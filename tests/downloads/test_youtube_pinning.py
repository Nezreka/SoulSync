"""Phase A pinning tests for YouTubeClient — UPDATED for Phase C2.

Post-C2 the client no longer owns its own ``active_downloads`` dict
or thread spawn — both moved into the engine's BackgroundDownloadWorker.
These tests still pin the same OBSERVABLE CONTRACT (filename
encoding, UUID download_id, initial-record schema, source-specific
extras like video_id/url/title) but read state from
``engine.get_record(...)`` instead of ``client.active_downloads[...]``.

What pre-C2 pinning tests caught and what these still catch:
- Filename format: `video_id||title` ✓
- Invalid filename → None ✓
- UUID download_id format ✓
- Per-download record schema (id, filename, username, state,
  progress, video_id, url, title, file_path) ✓
- Source name in record's username slot is `'youtube'` ✓

What dropped (covered by other tests):
- Direct thread-spawn assertion (engine.worker has its own tests).
- `_download_thread_worker` target (gone from client; engine owns
  it).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.youtube_client import YouTubeClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def yt_client_with_engine():
    """A bare YouTubeClient wired into a real engine. The engine
    callback is invoked manually since we bypass orchestrator init."""
    client = YouTubeClient.__new__(YouTubeClient)
    client.download_path = Path('./test_yt_downloads')
    client.shutdown_check = None
    client.matching_engine = None
    client._download_delay = 3
    client.current_download_id = None
    client.current_download_progress = {
        'status': 'idle', 'percent': 0.0, 'downloaded_bytes': 0,
        'total_bytes': 0, 'speed': 0, 'eta': 0, 'filename': '',
    }
    client.progress_callback = None
    client.download_opts = {}
    client._engine = None

    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


# ---------------------------------------------------------------------------
# download() — filename parsing + id contract
# ---------------------------------------------------------------------------


def test_download_returns_none_for_invalid_filename_format(yt_client_with_engine):
    """Pinning: missing `||` → None (not exception)."""
    client, _ = yt_client_with_engine
    result = _run_async(client.download('youtube', 'no-separator', 0))
    assert result is None


def test_download_returns_none_when_engine_not_wired():
    """Defensive: client without engine reference can't dispatch.
    In production this never happens (orchestrator wires engine
    immediately) but the soft-fail keeps tests + dev paths safe."""
    client = YouTubeClient.__new__(YouTubeClient)
    client._engine = None
    result = _run_async(client.download('youtube', 'v||t', 0))
    assert result is None


def test_download_returns_uuid_download_id_for_valid_filename(yt_client_with_engine):
    """Pinning: valid `video_id||title` → UUID download_id."""
    client, engine = yt_client_with_engine

    # Patch _download_sync so the worker thread's impl returns
    # without doing real yt-dlp work.
    with patch.object(client, '_download_sync', return_value='/tmp/x.mp3'):
        result = _run_async(client.download('youtube', 'abc123||Some Song', 0))

    assert result is not None
    assert len(result) == 36
    assert result.count('-') == 4


def test_download_populates_engine_record_with_initial_state(yt_client_with_engine):
    """Pinning: per-download record schema. STATE LOCATION CHANGED
    in C2 (now in engine), but the SHAPE of the record is the same
    — frontend / status APIs / context-key matching depend on these
    keys."""
    client, engine = yt_client_with_engine

    # Hold the impl so we can read 'Initializing' / 'InProgress' state
    # before the worker completes.
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.mp3'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(
            client.download('youtube', 'video123||My Title', 5000)
        )
        started.wait(timeout=1.0)
        record = engine.get_record('youtube', download_id)

        assert record is not None
        assert record['id'] == download_id
        assert record['filename'] == 'video123||My Title'  # ORIGINAL form
        assert record['username'] == 'youtube'
        assert record['state'] in ('Initializing', 'InProgress, Downloading')
        assert record['progress'] == 0.0
        assert record['file_path'] is None
        # Source-specific extras must merge into the record.
        assert record['video_id'] == 'video123'
        assert record['url'] == 'https://www.youtube.com/watch?v=video123'
        assert record['title'] == 'My Title'

        release.set()


def test_set_engine_configures_worker_delay(yt_client_with_engine):
    """Pinning: when engine is wired, the YouTube download_delay
    config (3s default) propagates to the worker so successive
    downloads serialize with the same gap they did pre-C2."""
    client, engine = yt_client_with_engine
    # Default delay is 3s.
    assert engine.worker._get_delay('youtube') == 3.0


def test_rate_limit_policy_reflects_configured_delay(yt_client_with_engine):
    """Pinning (Phase E): YouTube's rate_limit_policy() returns a
    RateLimitPolicy with the configured download_delay (3s default
    from `youtube.download_delay` config). Engine reads this at
    register_plugin time."""
    client, _ = yt_client_with_engine
    policy = client.rate_limit_policy()
    assert policy.download_delay_seconds == 3.0
    assert policy.download_concurrency == 1


# ---------------------------------------------------------------------------
# Query / cancel — engine-backed reads
# ---------------------------------------------------------------------------


def test_get_all_downloads_reads_engine_records(yt_client_with_engine):
    client, engine = yt_client_with_engine

    # Seed engine with a fake record to mirror what dispatch would do.
    engine.add_record('youtube', 'dl-1', {
        'id': 'dl-1', 'filename': 'v||t', 'username': 'youtube',
        'state': 'InProgress, Downloading', 'progress': 50.0,
        'size': 1000, 'transferred': 500, 'speed': 100,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 1
    assert result[0].id == 'dl-1'
    assert result[0].state == 'InProgress, Downloading'


def test_cancel_download_marks_cancelled_and_optionally_removes(yt_client_with_engine):
    client, engine = yt_client_with_engine

    engine.add_record('youtube', 'dl-1', {
        'id': 'dl-1', 'filename': 'v||t', 'username': 'youtube',
        'state': 'InProgress, Downloading', 'progress': 50.0,
    })

    ok = _run_async(client.cancel_download('dl-1', None, remove=False))
    assert ok is True
    assert engine.get_record('youtube', 'dl-1')['state'] == 'Cancelled'

    ok = _run_async(client.cancel_download('dl-1', None, remove=True))
    assert ok is True
    assert engine.get_record('youtube', 'dl-1') is None


def test_clear_all_completed_drops_only_terminal_records(yt_client_with_engine):
    client, engine = yt_client_with_engine
    engine.add_record('youtube', 'done', {'id': 'done', 'state': 'Completed, Succeeded'})
    engine.add_record('youtube', 'erred', {'id': 'erred', 'state': 'Errored'})
    engine.add_record('youtube', 'live', {'id': 'live', 'state': 'InProgress, Downloading'})

    _run_async(client.clear_all_completed_downloads())

    assert engine.get_record('youtube', 'done') is None
    assert engine.get_record('youtube', 'erred') is None
    assert engine.get_record('youtube', 'live') is not None
