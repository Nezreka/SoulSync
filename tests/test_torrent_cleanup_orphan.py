"""noldevin: a dead torrent (metaDL stuck / errored / timed out) was left ORPHANED
in qbit — cleared from SoulSync but still active in the client, then re-grabbed as
a duplicate. The monitor's terminal exits now call _cleanup_torrent, which removes
(abandon) or pauses it in the client."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.download_plugins.torrent import TorrentDownloadPlugin


class _FakeAdapter:
    def __init__(self):
        self.removed = []
        self.paused = []

    async def remove(self, h, delete_files=False):
        self.removed.append((h, delete_files))

    async def pause(self, h):
        self.paused.append(h)


@pytest.fixture
def plugin():
    with patch('core.download_plugins.torrent.ProwlarrClient'):
        yield TorrentDownloadPlugin()


def test_abandon_removes_torrent_and_deletes_files(plugin):
    fake = _FakeAdapter()
    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake):
        plugin._cleanup_torrent('abc123', 'abandon')
    assert fake.removed == [('abc123', True)]   # removed + partial data deleted
    assert fake.paused == []


def test_pause_action_pauses_not_removes(plugin):
    fake = _FakeAdapter()
    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake):
        plugin._cleanup_torrent('abc123', 'pause')
    assert fake.paused == ['abc123']
    assert fake.removed == []


def test_no_hash_is_a_noop(plugin):
    fake = _FakeAdapter()
    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=fake):
        plugin._cleanup_torrent('', 'abandon')
        plugin._cleanup_torrent(None, 'abandon')
    assert fake.removed == [] and fake.paused == []


def test_no_adapter_is_a_noop(plugin):
    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=None):
        plugin._cleanup_torrent('abc123', 'abandon')   # must not raise


def test_client_error_is_swallowed(plugin):
    class _Boom:
        async def remove(self, h, delete_files=False):
            raise RuntimeError("qbit down")
    with patch('core.download_plugins.torrent.get_active_torrent_adapter', return_value=_Boom()):
        plugin._cleanup_torrent('abc123', 'abandon')   # best-effort: logged, not raised
