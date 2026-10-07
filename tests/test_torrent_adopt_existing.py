"""a torrent the client already holds is adopted, on every client.

re-grabbing a finished audiobook answered "the torrent client didn't accept the
release": the client refuses the duplicate, so nothing new appeared. with
``adopt_existing`` the grab finds the torrent by info-hash and tracks it where
it is. off by default, so the music and video callers keep adding as before.
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import patch

from core.torrent_clients.aria2 import Aria2Adapter
from core.torrent_clients.base import AdoptedRef, TorrentStatus, add_torrent_smart

_HASH = "d880c485f7655d4122619a9a8fe6a82f0044fe1e"
_MAGNET = "magnet:?xt=urn:btih:" + _HASH


def _bencode(value):
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, str):
        return _bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(v) for v in value) + b"e"
    return b"d" + b"".join(_bencode(k) + _bencode(value[k]) for k in sorted(value)) + b"e"


_INFO = {"name": "The Reckoning", "piece length": 16384, "pieces": b"x" * 20, "length": 5}
_TORRENT = _bencode({"announce": "https://tracker.invalid/a", "info": _INFO})
_FILE_HASH = hashlib.sha1(_bencode(_INFO)).hexdigest()


def _status(id_, info_hash=None):
    return TorrentStatus(id=id_, name="x", state="completed", progress=1.0, size=1,
                         downloaded=1, download_speed=0, upload_speed=0, info_hash=info_hash)


class _Client:
    """a client that already holds `held` and records any add."""

    def __init__(self, held):
        self.held = held
        self.added = []

    async def get_all(self):
        return self.held

    async def add_torrent(self, magnet, category=None, save_path=None):
        self.added.append(magnet)
        return None  # refuses the duplicate, like the real clients

    async def add_torrent_file(self, payload, category=None, save_path=None):
        self.added.append(payload)
        return None


def _smart(client, url, **kw):
    return asyncio.run(add_torrent_smart(client, url, category="audiobooks", **kw))


def test_a_held_magnet_is_adopted_without_adding():
    client = _Client([_status(_HASH)])
    ref = _smart(client, _MAGNET, adopt_existing=True)
    assert ref == _HASH and isinstance(ref, AdoptedRef)
    assert client.added == []


def test_a_held_torrent_file_is_adopted_on_a_client_that_keys_on_gid():
    # aria2: the id is a gid, the hash rides alongside
    client = _Client([_status("gid-7", info_hash=_FILE_HASH)])
    with patch("core.torrent_clients.base._fetch_torrent_payload_async",
               new=_returns((_TORRENT, None))):
        ref = _smart(client, "https://mam.invalid/t.torrent", adopt_existing=True)
    assert ref == "gid-7" and isinstance(ref, AdoptedRef)
    assert client.added == []


def test_without_the_opt_in_nothing_changes():
    client = _Client([_status(_HASH)])
    assert _smart(client, _MAGNET) is None
    assert client.added == [_MAGNET]


def test_a_torrent_the_client_lacks_is_still_added():
    client = _Client([_status("0" * 40)])
    _smart(client, _MAGNET, adopt_existing=True)
    assert client.added == [_MAGNET]


def test_an_unlistable_client_falls_through_to_a_normal_add():
    client = _Client([])

    async def broken():
        raise RuntimeError("down")

    client.get_all = broken
    _smart(client, _MAGNET, adopt_existing=True)
    assert client.added == [_MAGNET]


def test_aria2_reports_the_info_hash_beside_its_gid():
    status = Aria2Adapter.__new__(Aria2Adapter)._parse_status(
        {"gid": "g1", "status": "complete", "totalLength": "5", "completedLength": "5",
         "infoHash": _HASH.upper()})
    assert status.id == "g1" and status.info_hash == _HASH


def test_the_audiobook_grab_says_when_it_adopted():
    from core.audiobook_grab import grab_torrent

    class _Adapter:
        def is_configured(self):
            return True

    with patch("core.torrent_clients.get_active_adapter", return_value=_Adapter()), \
         patch("core.torrent_clients.base.add_torrent_smart",
               new=_returns(AdoptedRef(_HASH))):
        assert grab_torrent(_MAGNET) == {"ok": True, "ref": _HASH, "adopted": True}
    with patch("core.torrent_clients.get_active_adapter", return_value=_Adapter()), \
         patch("core.torrent_clients.base.add_torrent_smart",
               new=_returns(_HASH)):
        assert grab_torrent(_MAGNET)["adopted"] is False


def _returns(value):
    async def _fn(*a, **kw):
        return value
    return _fn
