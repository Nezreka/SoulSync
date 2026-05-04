"""Phase A pinning tests for DeezerDownloadClient's download lifecycle.

Deezer auths via ARL token, fetches Blowfish-encrypted FLAC chunks
from the Deezer GW API, decrypts client-side. Different from
Tidal/Qobuz/HiFi:

- track_id is STRING (not int).
- username is the legacy ``'deezer_dl'`` (not ``'deezer'``).
- Auth gate at the top of `download()` short-circuits when not
  authenticated (returns None without spawning a thread).
- Thread is named ``deezer-dl-<track_id>`` for diagnostics.

Engine refactor must preserve all of these.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.deezer_download_client import DeezerDownloadClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def deezer_client():
    client = DeezerDownloadClient.__new__(DeezerDownloadClient)
    client.download_path = Path('./test_deezer_downloads')
    client.shutdown_check = None
    client.active_downloads = {}
    client._download_lock = threading.Lock()
    client._authenticated = True
    return client


def test_download_returns_none_when_not_authenticated(deezer_client):
    """Pinning: unauthenticated client refuses BEFORE any thread is
    spawned. The orchestrator's hybrid fallback depends on this
    early return — if the auth gate moves into the thread, fallback
    behavior changes."""
    deezer_client._authenticated = False
    result = _run_async(deezer_client.download('deezer_dl', '12345||Some Song', 0))
    assert result is None


def test_download_accepts_string_track_id(deezer_client):
    """Pinning: Deezer track_id stays as string — the GW API uses
    string IDs. Engine refactor cannot int-coerce on the way through."""
    with patch('core.deezer_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(
            deezer_client.download('deezer_dl', '999||My Deezer Song', 5000)
        )

    record = deezer_client.active_downloads[download_id]
    assert record['track_id'] == '999'  # STRING, not int
    assert isinstance(record['track_id'], str)


def test_download_username_field_is_legacy_deezer_dl(deezer_client):
    """Pinning: the `username` slot in the state dict is ``'deezer_dl'``,
    not ``'deezer'``. Frontend status indicators + per-source
    dispatch strings depend on the legacy form."""
    with patch('core.deezer_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(
            deezer_client.download('deezer_dl', '999||x', 0)
        )

    assert deezer_client.active_downloads[download_id]['username'] == 'deezer_dl'


def test_download_handles_missing_display_name_with_fallback(deezer_client):
    """Pinning: filename without `||` produces a synthetic display
    name `Track <track_id>`. Other clients return None for missing
    `||` — Deezer is more lenient. Engine refactor must NOT change
    this defensive fallback."""
    with patch('core.deezer_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(deezer_client.download('deezer_dl', '12345', 0))

    assert download_id is not None
    record = deezer_client.active_downloads[download_id]
    assert record['display_name'] == 'Track 12345'


def test_download_populates_active_downloads_with_initial_state(deezer_client):
    """Pinning: per-download record schema. NOTE the extra `error`
    slot — Deezer-specific, used for ARL re-auth failure messages."""
    with patch('core.deezer_download_client.threading.Thread') as fake:
        fake.return_value.start = lambda: None
        download_id = _run_async(
            deezer_client.download('deezer_dl', '999||My Deezer Song', 1024)
        )

    record = deezer_client.active_downloads[download_id]
    assert record['id'] == download_id
    assert record['filename'] == '999||My Deezer Song'
    assert record['username'] == 'deezer_dl'
    assert record['state'] == 'Initializing'
    assert record['size'] == 1024  # Deezer respects the file_size hint
    assert record['file_path'] is None
    assert record['error'] is None  # Deezer-specific slot


def test_download_thread_is_named_for_diagnostics(deezer_client):
    """Pinning: thread is named `deezer-dl-<track_id>` so multi-thread
    debugging shows which download a stuck thread belongs to. Engine
    refactor's BackgroundDownloadWorker must preserve diagnostic naming."""
    captured_kwargs = {}

    def capture_thread(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return type('FakeThread', (), {'start': lambda self: None})()

    with patch('core.deezer_download_client.threading.Thread', side_effect=capture_thread):
        _run_async(deezer_client.download('deezer_dl', '777||Title', 0))

    assert captured_kwargs.get('daemon') is True
    assert captured_kwargs.get('name') == 'deezer-dl-777'
