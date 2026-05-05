"""Phase A pinning tests for TidalDownloadClient — UPDATED for Phase C3.

Post-C3 the client no longer owns its own ``active_downloads`` dict
or thread spawn — both moved into the engine's BackgroundDownloadWorker.
Pinning tests now read state from ``engine.get_record('tidal', ...)``
instead of ``client.active_downloads[...]``.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.tidal_download_client import TidalDownloadClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def tidal_client_with_engine():
    client = TidalDownloadClient.__new__(TidalDownloadClient)
    client.download_path = Path('./test_tidal_downloads')
    client.shutdown_check = None
    client.session = None
    client._device_auth_future = None
    client._device_auth_link = None
    client._engine = None

    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


# ---------------------------------------------------------------------------
# is_configured / is_authenticated
# ---------------------------------------------------------------------------


def test_is_authenticated_false_when_no_session(tidal_client_with_engine):
    client, _ = tidal_client_with_engine
    assert client.is_authenticated() is False


def test_is_authenticated_false_when_session_check_login_raises(tidal_client_with_engine):
    client, _ = tidal_client_with_engine
    fake_session = type('FakeSession', (), {
        'check_login': lambda self: (_ for _ in ()).throw(RuntimeError("expired")),
    })()
    client.session = fake_session
    assert client.is_authenticated() is False


# ---------------------------------------------------------------------------
# download() — filename parsing + id contract
# ---------------------------------------------------------------------------


def test_download_returns_none_for_invalid_filename_format(tidal_client_with_engine):
    client, _ = tidal_client_with_engine
    result = _run_async(client.download('tidal', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(tidal_client_with_engine):
    client, _ = tidal_client_with_engine
    result = _run_async(client.download('tidal', 'not-int||title', 0))
    assert result is None


def test_download_raises_when_engine_not_wired():
    """Defensive: client without engine reference must raise so the
    orchestrator's download_with_fallback surfaces the error and
    moves on to the next source. Returning None silently would drop
    the download with no user feedback (per JohnBaumb)."""
    import pytest
    client = TidalDownloadClient.__new__(TidalDownloadClient)
    client._engine = None
    with pytest.raises(RuntimeError, match="engine reference"):
        _run_async(client.download('tidal', 'v||t', 0))


def test_download_returns_uuid_for_valid_filename(tidal_client_with_engine):
    client, _ = tidal_client_with_engine
    with patch.object(client, '_download_sync', return_value='/tmp/x.flac'):
        result = _run_async(client.download('tidal', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_engine_record_with_initial_state(tidal_client_with_engine):
    client, engine = tidal_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('tidal', '999||My Tidal Song', 0))
        started.wait(timeout=1.0)
        record = engine.get_record('tidal', download_id)

        assert record is not None
        assert record['id'] == download_id
        assert record['filename'] == '999||My Tidal Song'
        assert record['username'] == 'tidal'
        assert record['state'] in ('Initializing', 'InProgress, Downloading')
        assert record['progress'] == 0.0
        assert record['track_id'] == 999  # parsed as int
        assert record['display_name'] == 'My Tidal Song'
        assert record['file_path'] is None
        release.set()


# ---------------------------------------------------------------------------
# Query / cancel — engine-backed reads
# ---------------------------------------------------------------------------


def test_get_all_downloads_reads_engine_records(tidal_client_with_engine):
    client, engine = tidal_client_with_engine
    engine.add_record('tidal', 'dl-1', {
        'id': 'dl-1', 'filename': '111||Song A', 'username': 'tidal',
        'state': 'InProgress, Downloading', 'progress': 50.0,
        'size': 1000, 'transferred': 500, 'speed': 100,
    })
    engine.add_record('tidal', 'dl-2', {
        'id': 'dl-2', 'filename': '222||Song B', 'username': 'tidal',
        'state': 'Completed, Succeeded', 'progress': 100.0,
        'size': 2000, 'transferred': 2000, 'speed': 0,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 2
    assert {r.id for r in result} == {'dl-1', 'dl-2'}
    assert {r.username for r in result} == {'tidal'}


def test_cancel_download_marks_cancelled(tidal_client_with_engine):
    client, engine = tidal_client_with_engine
    engine.add_record('tidal', 'dl-1', {'id': 'dl-1', 'state': 'InProgress, Downloading'})

    ok = _run_async(client.cancel_download('dl-1', None, remove=False))
    assert ok is True
    assert engine.get_record('tidal', 'dl-1')['state'] == 'Cancelled'

    ok = _run_async(client.cancel_download('dl-1', None, remove=True))
    assert ok is True
    assert engine.get_record('tidal', 'dl-1') is None


def test_clear_all_completed_drops_only_terminal_records(tidal_client_with_engine):
    client, engine = tidal_client_with_engine
    engine.add_record('tidal', 'done', {'id': 'done', 'state': 'Completed, Succeeded'})
    engine.add_record('tidal', 'live', {'id': 'live', 'state': 'InProgress, Downloading'})

    _run_async(client.clear_all_completed_downloads())

    assert engine.get_record('tidal', 'done') is None
    assert engine.get_record('tidal', 'live') is not None
