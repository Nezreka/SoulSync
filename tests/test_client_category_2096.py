"""a grab that names no category uses the one set in Settings (#2096).

the usenet adapters took ``category="soulsync"`` as their default, so their
own fallback to the configured category never ran. the music usenet path
calls them with no category: every music nzb reached SABnzbd as "soulsync",
a category it did not have, and the finished job landed in the root of the
completed folder.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from core.torrent_clients.aria2 import Aria2Adapter
from core.torrent_clients.deluge import DelugeAdapter
from core.torrent_clients.qbittorrent import QBittorrentAdapter
from core.torrent_clients.transmission import TransmissionAdapter
from core.usenet_clients.nzbget import NZBGetAdapter
from core.usenet_clients.sabnzbd import SABnzbdAdapter

_NZB = b'<?xml version="1.0"?><nzb><file subject="x"/></nzb>'


def _sab(configured):
    a = SABnzbdAdapter()
    a._category = configured
    sent = []

    def post(mode, files=None, _timeout=None, **params):
        sent.append(params)
        return {"status": True, "nzo_ids": ["SABnzbd_nzo_1"]}
    a._post_sync = post
    return a, sent


def _nzbget(configured):
    a = NZBGetAdapter()
    a._category = configured
    sent = []

    def rpc(method, params):
        sent.append(params)
        return 7
    a._rpc_sync = rpc
    return a, sent


def test_sabnzbd_files_a_grab_with_no_category_under_the_configured_one():
    a, sent = _sab("audio")
    assert asyncio.run(a.add_nzb(_NZB)) == "SABnzbd_nzo_1"
    assert sent[0]["cat"] == "audio"


def test_nzbget_files_a_grab_with_no_category_under_the_configured_one():
    a, sent = _nzbget("audio")
    assert asyncio.run(a.add_nzb(_NZB)) == "7"
    assert sent[0][2] == "audio"


def test_a_category_that_is_asked_for_still_wins():
    a, sent = _sab("audio")
    asyncio.run(a.add_nzb(_NZB, category="video-4k"))
    assert sent[0]["cat"] == "video-4k"
    b, sent_b = _nzbget("audio")
    asyncio.run(b.add_nzb(_NZB, category="video-4k"))
    assert sent_b[0][2] == "video-4k"


def test_the_music_usenet_path_names_no_category_so_the_setting_decides():
    # the two call sites the report named: neither passes a category, which is
    # right now that the adapters fall back to the configured one
    import core.download_plugins.usenet as plugin
    src = inspect.getsource(plugin)
    assert "adapter.add_nzb(nzb_url)" in src
    assert "adapter.add_nzb(source.download_url)" in src


@pytest.mark.parametrize("adapter", [SABnzbdAdapter, NZBGetAdapter])
def test_no_usenet_adapter_defaults_to_a_category_of_its_own(adapter):
    assert inspect.signature(adapter.add_nzb).parameters["category"].default is None


@pytest.mark.parametrize("adapter", [QBittorrentAdapter, TransmissionAdapter, DelugeAdapter,
                                     Aria2Adapter])
def test_no_torrent_adapter_defaults_to_a_category_of_its_own(adapter):
    # the torrent path already passes None through add_torrent_smart; the same
    # trap is closed here so a direct caller cannot fall into it
    for method in (adapter.add_torrent, adapter.add_torrent_file):
        assert inspect.signature(method).parameters["category"].default is None
