"""Plex deep-scan resilience: a transient read timeout on the bulk artist/
album/track enumeration must not zero the whole scan.

A user's deep scan died with "0 artists" because get_all_artists → a single
library enumeration hit the 15s read timeout, and one failure returned [] with
last_fetch_failed=True — the scan then stopped. plexapi already pages the
library internally (100/request) but has no per-page retry, so one slow page
loses everything. These pin the retry wrapper (recover from a transient
failure) and the honest-failure contract (persistent failure still surfaces as
last_fetch_failed, NEVER a silent partial that removal-detection would misread).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.plex_client import PlexClient


def _make_client(*, music_library=None, server=None, all_libraries_mode=False,
                 scan_retries=2):
    client = PlexClient.__new__(PlexClient)
    # a truthy server satisfies ensure_connection()/_can_query() so the test
    # reaches the enumeration; single-library tests still assert on the section
    client.server = server if server is not None else MagicMock()
    client.music_library = music_library
    client._all_libraries_mode = all_libraries_mode
    client._connection_attempted = server is not None or music_library is not None
    client._is_connecting = False
    client._last_connection_check = 0
    client._connection_check_interval = 30
    client._scan_retries = scan_retries
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # retry backoff must not slow the suite
    monkeypatch.setattr('core.plex_client.time.sleep', lambda *_a, **_k: None)


# ── recovery ────────────────────────────────────────────────────────────────

def test_transient_timeout_recovers_and_scan_is_not_zeroed():
    """One slow page (raises once), then success — get_all_artists returns the
    artists and does NOT report a failed fetch."""
    section = MagicMock()
    good = [MagicMock(ratingKey=1), MagicMock(ratingKey=2)]
    section.searchArtists.side_effect = [
        TimeoutError("Read timed out. (read timeout=15)"),
        good,
    ]
    client = _make_client(music_library=section)

    result = client.get_all_artists()

    assert [a.ratingKey for a in result] == [1, 2]
    assert client.last_fetch_failed is False
    assert section.searchArtists.call_count == 2


def test_retry_count_is_bounded_then_fails_honestly():
    """Persistent timeout: after exhausting retries, get_all_artists returns []
    AND flags last_fetch_failed — the scan can tell 'fetch broke' from 'library
    is empty' and must NOT treat the artists as removed."""
    section = MagicMock()
    section.searchArtists.side_effect = TimeoutError("Read timed out.")
    client = _make_client(music_library=section, scan_retries=2)

    result = client.get_all_artists()

    assert result == []
    assert client.last_fetch_failed is True
    assert section.searchArtists.call_count == 3   # 1 try + 2 retries


def test_retries_disabled_is_single_attempt():
    section = MagicMock()
    section.searchArtists.side_effect = TimeoutError("Read timed out.")
    client = _make_client(music_library=section, scan_retries=0)

    assert client.get_all_artists() == []
    assert client.last_fetch_failed is True
    assert section.searchArtists.call_count == 1


# ── empty library is not a failure ───────────────────────────────────────────

def test_empty_library_returns_empty_without_retrying():
    """A genuinely empty library returns [] on the first (successful) call —
    no exception, so no retry, and last_fetch_failed is False."""
    section = MagicMock()
    section.searchArtists.return_value = []
    client = _make_client(music_library=section)

    assert client.get_all_artists() == []
    assert client.last_fetch_failed is False
    assert section.searchArtists.call_count == 1


# ── all-libraries mode retries the server-wide path too ──────────────────────

def test_all_libraries_mode_also_retries():
    server = MagicMock()
    good = [MagicMock(ratingKey=1)]
    server.library.search.side_effect = [ConnectionError("boom"), good]
    client = _make_client(server=server, all_libraries_mode=True)

    result = client.get_all_artists()

    assert [a.ratingKey for a in result] == [1]
    assert server.library.search.call_count == 2
    # dispatch unchanged: still the libtype='artist' server-wide search
    for call in server.library.search.call_args_list:
        assert call.kwargs == {'libtype': 'artist'}


# ── albums + tracks share the wrapper (ids paths used by removal detection) ──

def test_album_ids_recover_from_transient_failure():
    section = MagicMock()
    section.albums.side_effect = [TimeoutError("Read timed out."),
                                  [MagicMock(ratingKey=7), MagicMock(ratingKey=8)]]
    client = _make_client(music_library=section)

    ids = client.get_all_album_ids()

    assert ids == {'7', '8'}
    assert section.albums.call_count == 2


def test_default_scan_retries_when_attr_missing():
    """Clients built via __new__ in other tests never set _scan_retries — the
    wrapper must default (getattr) rather than AttributeError."""
    section = MagicMock()
    section.searchTracks.return_value = [MagicMock(ratingKey=1)]
    client = _make_client(music_library=section)
    del client._scan_retries                      # simulate the old __new__ path

    tracks = client._all_tracks()
    assert len(tracks) == 1
