"""torrent_client.start_torrents: start what SoulSync adds even when qBittorrent's
"add torrents stopped" preference is on. Off by default (client default wins)."""

from __future__ import annotations

from core.torrent_clients.qbittorrent import QBittorrentAdapter

_HASH = "566313b5a172cf1e0675358f288e428da3f58cf4"
_MAGNET = "magnet:?xt=urn:btih:" + _HASH


class _Resp:
    def __init__(self, text="Ok.", payload=None):
        self.text, self.ok, self.status_code = text, True, 200
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def _adapter(start):
    a = QBittorrentAdapter()
    a._start_torrents = start
    sent, seen = [], [[]]

    def _call(method, path, **kw):
        if path.endswith("/torrents/info"):
            return _Resp(payload=[{"hash": h} for h in seen[0]])
        if path.endswith("/torrents/add"):
            sent.append(kw.get("data") or {})
            seen[0] = [_HASH]
            return _Resp()
        return _Resp()

    a._call = _call
    a._sent = sent
    return a


def test_off_by_default_leaves_the_client_default():
    a = _adapter(False)
    a._add_torrent_sync(_MAGNET, "soulsync", None)
    assert "stopped" not in a._sent[0] and "paused" not in a._sent[0]


def test_on_asks_qbittorrent_to_start_a_magnet():
    a = _adapter(True)
    a._add_torrent_sync(_MAGNET, "soulsync", None)
    assert a._sent[0]["stopped"] == "false" and a._sent[0]["paused"] == "false"


def test_on_asks_qbittorrent_to_start_a_torrent_file(monkeypatch):
    import core.quality.torrent_contents as tc
    monkeypatch.setattr(tc, "torrent_info_hash", lambda b: None)
    a = _adapter(True)
    a._add_torrent_file_sync(b"d4:infod4:name1:xee", "soulsync", None)
    assert a._sent[0]["stopped"] == "false" and a._sent[0]["paused"] == "false"


def test_the_setting_is_read_from_config(monkeypatch):
    import core.torrent_clients.qbittorrent as q
    vals = {"torrent_client.start_torrents": True}
    monkeypatch.setattr(q.config_manager, "get", lambda k, d=None: vals.get(k, d))
    a = QBittorrentAdapter()
    assert a._start_torrents is True
