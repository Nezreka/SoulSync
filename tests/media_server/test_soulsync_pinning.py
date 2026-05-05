"""Phase A pinning tests for SoulSyncClient (standalone library mode).

Pin the OBSERVABLE BEHAVIOR the engine will dispatch through.
SoulSync standalone is the structurally-different one — no auth /
no API / no library scan. is_connected just checks `transfer_path`
is a directory. Filesystem walk happens via _get_cached_scan().
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.soulsync_client import SoulSyncClient


@pytest.fixture
def ss_client(tmp_path):
    """A bare SoulSyncClient pointed at a temp directory."""
    client = SoulSyncClient.__new__(SoulSyncClient)
    client._transfer_path = str(tmp_path)
    client._progress_callback = None
    client._cache = None
    client._cache_time = 0
    client._cache_ttl = 300
    client._last_scan_time = None
    return client


# ---------------------------------------------------------------------------
# is_connected / ensure_connection
# ---------------------------------------------------------------------------


def test_is_connected_true_when_transfer_path_exists(ss_client, tmp_path):
    """Pinning: filesystem-based connectivity. is_connected just
    checks os.path.isdir(transfer_path)."""
    assert ss_client.is_connected() is True  # tmp_path exists


def test_is_connected_false_when_transfer_path_missing(ss_client):
    """Pinning: missing transfer_path → False. The standalone case
    where the user hasn't configured a folder yet."""
    ss_client._transfer_path = '/nonexistent/path/that/does/not/exist'
    assert ss_client.is_connected() is False


def test_ensure_connection_reloads_config_then_checks_path(ss_client, tmp_path):
    """Pinning: ensure_connection re-reads config (so the user
    changing the transfer_path takes effect without a process
    restart) and then checks the path. Returns True iff the path
    is a directory."""
    with patch.object(ss_client, '_reload_config') as fake_reload:
        result = ss_client.ensure_connection()

    fake_reload.assert_called_once()
    assert result is True


# ---------------------------------------------------------------------------
# get_all_artists / get_all_album_ids
# ---------------------------------------------------------------------------


def test_get_all_artists_uses_cached_scan(ss_client):
    """Pinning: get_all_artists delegates to _get_cached_scan which
    enforces the 5-min TTL. Engine extraction must preserve."""
    fake_artists = [object(), object(), object()]
    with patch.object(ss_client, '_get_cached_scan', return_value=fake_artists):
        result = ss_client.get_all_artists()
    assert result == fake_artists


def test_get_all_album_ids_returns_set(ss_client):
    """Pinning: returns a set of MD5-hashed string ids. Same uniform
    set semantics as Plex/Jellyfin/Navidrome."""
    fake_album = type('FakeAlbum', (), {'ratingKey': 'hash-1'})()
    fake_album2 = type('FakeAlbum', (), {'ratingKey': 'hash-2'})()
    # Real SoulSyncArtist exposes albums() method (not _albums attr).
    fake_artist = type('FakeArtist', (), {
        'albums': lambda self: [fake_album, fake_album2],
    })()

    with patch.object(ss_client, '_get_cached_scan', return_value=[fake_artist]):
        result = ss_client.get_all_album_ids()

    assert isinstance(result, set)
    assert result == {'hash-1', 'hash-2'}
