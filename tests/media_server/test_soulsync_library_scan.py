"""Regression tests: standalone SoulSync library scan trigger.

Spectro's log (Oct 2026): for standalone users the 'Auto-Scan After
Downloads' automation and the manual 'Scan library' button both died
with ``'SoulSyncClient' object has no attribute 'trigger_library_scan'``
— WebScanManager calls that method on whichever client is active, and
SoulSyncClient was the only one of the four clients without it (the
media-server contract documented the gap). These tests pin the fix:
SoulSyncClient implements trigger_library_scan /
is_library_scanning with the same shape as Plex/Jellyfin/Navidrome —
fire-and-forget initiation, background filesystem rescan, cache swap
on completion.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from core.soulsync_client import SoulSyncClient


@pytest.fixture
def ss_client(tmp_path):
    """A bare SoulSyncClient pointed at a temp directory (same
    construction pattern as test_soulsync_pinning.py)."""
    client = SoulSyncClient.__new__(SoulSyncClient)
    client._transfer_path = str(tmp_path)
    client._progress_callback = None
    client._cache = None
    client._cache_time = 0
    client._cache_ttl = 300
    client._last_scan_time = None
    client._scan_thread = None
    client._scan_lock = threading.Lock()
    return client


def _add_track(root, artist: str, album: str, filename: str) -> None:
    folder = root / artist / album
    folder.mkdir(parents=True, exist_ok=True)
    # Junk bytes are fine: tag reading falls back to the folder
    # structure / filename when mutagen can't parse the file, which
    # is all the walk needs to place the track.
    (folder / filename).write_bytes(b"\x00" * 2048)


def _wait_for_scan_done(client, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while client.is_library_scanning() and time.time() < deadline:
        time.sleep(0.02)
    assert not client.is_library_scanning(), "background scan did not finish in time"


# ---------------------------------------------------------------------------
# The regression itself — the two methods exist on the class
# ---------------------------------------------------------------------------


def test_soulsync_client_implements_scan_trigger_methods():
    """Class-level pin for the exact gap Spectro hit: every other
    client has these; SoulSyncClient must too, or WebScanManager's
    generic trigger call raises AttributeError for standalone."""
    assert callable(getattr(SoulSyncClient, "trigger_library_scan", None))
    assert callable(getattr(SoulSyncClient, "is_library_scanning", None))


def test_is_library_scanning_false_when_idle(ss_client):
    assert ss_client.is_library_scanning() is False


# ---------------------------------------------------------------------------
# trigger_library_scan behavior
# ---------------------------------------------------------------------------


def test_trigger_returns_false_when_transfer_path_missing(ss_client):
    ss_client._transfer_path = "/nonexistent/path/that/does/not/exist"
    assert ss_client.trigger_library_scan() is False
    assert ss_client.is_library_scanning() is False


def test_trigger_rescans_and_refreshes_cache(ss_client, tmp_path):
    """End to end with real files: prime the cache with one album,
    add a second album, trigger a scan. The triggered walk bypasses
    the (still-fresh) cache TTL and swaps in the new library state,
    so get_all_artists() sees both albums immediately after."""
    _add_track(tmp_path, "Artist A", "Album One", "01 - First.mp3")
    artists = ss_client.get_all_artists()
    assert [a.title for a in artists] == ["Artist A"]
    assert len(artists[0].albums()) == 1

    _add_track(tmp_path, "Artist A", "Album Two", "01 - Second.mp3")
    # Cache is still within its TTL — without the triggered rescan
    # this would keep serving the one-album snapshot.
    assert len(ss_client.get_all_artists()[0].albums()) == 1

    assert ss_client.trigger_library_scan() is True
    _wait_for_scan_done(ss_client)

    albums = ss_client.get_all_artists()[0].albums()
    # Untagged files key the album by its lowercased folder name
    # (pre-existing walk behavior) — compare case-insensitively.
    assert {a.title.lower() for a in albums} == {"album one", "album two"}


def test_trigger_while_scanning_does_not_start_a_second_walk(ss_client):
    """A second trigger while a walk is running reports True (a scan
    IS in progress, which is what the caller asked for) but must not
    spawn a duplicate walk."""
    release = threading.Event()
    calls = []

    def blocking_scan():
        calls.append(1)
        release.wait(timeout=10)
        return []

    with patch.object(ss_client, "_scan_transfer", side_effect=blocking_scan):
        assert ss_client.trigger_library_scan() is True
        assert ss_client.is_library_scanning() is True
        assert ss_client.trigger_library_scan() is True
        release.set()
        _wait_for_scan_done(ss_client)

    assert len(calls) == 1


def test_failed_walk_keeps_previous_cache(ss_client):
    """If the background walk blows up, the previous cache stays —
    a failed scan must not empty the library view."""
    sentinel = [object()]
    ss_client._cache = sentinel
    ss_client._cache_time = time.time()

    with patch.object(ss_client, "_scan_transfer", side_effect=RuntimeError("walk blew up")):
        assert ss_client.trigger_library_scan() is True
        _wait_for_scan_done(ss_client)

    assert ss_client._cache is sentinel


def test_walk_with_vanished_transfer_path_keeps_previous_cache(ss_client, tmp_path):
    """The race the guard in _run_triggered_scan exists for: the
    trigger's is_connected() check passes, then the Transfer path
    goes away (unmounted drive) before the background walk runs.
    _scan_transfer returns [] for a missing path instead of raising,
    so without the guard the empty result would swap in and the
    library would read as empty until the next scan."""
    _add_track(tmp_path, "Artist A", "Album One", "01 - First.mp3")
    assert len(ss_client.get_all_artists()) == 1
    cached = ss_client._cache

    ss_client._transfer_path = str(tmp_path / "gone")
    with patch.object(ss_client, "is_connected", return_value=True):
        assert ss_client.trigger_library_scan() is True
        _wait_for_scan_done(ss_client)

    assert ss_client._cache is cached


def test_concurrent_triggers_start_only_one_walk(ss_client):
    """Callers are concurrent by design (scan-manager timer thread,
    audiobook monitor's per-book nudge threads). Two triggers fired
    at the same moment must produce ONE walk — the lock in
    trigger_library_scan serializes the check + assign + start, and
    the walk stays blocked until both triggers have returned, so
    the second one necessarily observes the running scan."""
    release = threading.Event()
    calls = []

    def blocking_scan():
        calls.append(1)
        release.wait(timeout=10)
        return []

    with patch.object(ss_client, "_scan_transfer", side_effect=blocking_scan):
        barrier = threading.Barrier(3)
        results = []

        def fire():
            barrier.wait(timeout=10)
            results.append(ss_client.trigger_library_scan())

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        barrier.wait(timeout=10)  # fire both triggers at once
        for t in threads:
            t.join(timeout=10)

        assert len(results) == 2 and all(results)
        assert ss_client.is_library_scanning() is True
        release.set()
        _wait_for_scan_done(ss_client)

    assert len(calls) == 1


def test_trigger_thread_start_failure_cleans_up_and_recovers(ss_client, tmp_path):
    """If Thread.start() itself raises, the trigger reports False,
    leaves no dead thread in the slot, and a later trigger works."""
    _add_track(tmp_path, "Artist A", "Album One", "01 - First.mp3")

    with patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start")):
        assert ss_client.trigger_library_scan() is False
    assert ss_client._scan_thread is None
    assert ss_client.is_library_scanning() is False

    assert ss_client.trigger_library_scan() is True
    _wait_for_scan_done(ss_client)
    assert len(ss_client.get_all_artists()) == 1


# ---------------------------------------------------------------------------
# WebScanManager integration — the path the automation and the
# 'Scan library' button actually take
# ---------------------------------------------------------------------------


def test_web_scan_manager_executes_scan_for_soulsync(ss_client):
    """_execute_scan with 'soulsync' as the active server must reach
    the 'active' state — pre-fix it landed in the error branch via
    AttributeError and the completion callbacks never fired."""
    from core.settings import config_manager
    from core.web_scan_manager import WebScanManager

    class _Engine:
        def client(self, name):
            return ss_client

    manager = WebScanManager(_Engine(), delay_seconds=60)
    try:
        with patch.object(config_manager, "get_active_media_server", return_value="soulsync"):
            manager._execute_scan()
        assert manager._scan_in_progress is True
        assert manager._current_server_type == "soulsync"
        assert manager._scan_progress.get("status") == "active"
    finally:
        manager.shutdown()
