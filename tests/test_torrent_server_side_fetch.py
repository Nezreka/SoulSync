"""Server-side .torrent fetch + smart add (core.torrent_clients.base).

SoulSync used to hand the torrent client Prowlarr's download URL and let the
CLIENT fetch it. In split-container setups (each app in its own LXC/Docker
with different DNS) the client can't resolve Prowlarr's hostname, the fetch
dies inside the client, and the add fails silently ("accepted the request but
no new torrent appeared"). Sonarr/Radarr avoid this by downloading the
.torrent themselves and pushing the file — add_torrent_smart is that handoff:
magnet → straight to the client; http(s) → fetched HERE (following Prowlarr's
redirect-to-magnet for magnet-only indexers) and pushed via add_torrent_file;
fetch failure → legacy URL handoff so reachable setups keep working.
"""

from __future__ import annotations

import asyncio

import requests

from core.torrent_clients.base import add_torrent_smart, fetch_torrent_payload

TORRENT_BYTES = b"d8:announce30:http://tracker.example/announce4:infod4:name5:aatede"


class _Resp:
    def __init__(self, status=200, content=b"", location=None):
        self.status_code = status
        self.content = content
        self.headers = {"Location": location} if location else {}
        self.ok = 200 <= status < 300


def _serve(monkeypatch, responses):
    """Stub requests.get with a canned response sequence; records URLs."""
    seen = []

    def fake_get(url, timeout=None, allow_redirects=None):
        seen.append(url)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    monkeypatch.setattr(requests, "get", fake_get)
    return seen


# ── fetch_torrent_payload ─────────────────────────────────────────────────────

def test_fetch_returns_torrent_bytes(monkeypatch):
    _serve(monkeypatch, [_Resp(200, TORRENT_BYTES)])
    blob, magnet = fetch_torrent_payload("http://prowlarr:9696/download?apikey=k")
    assert blob == TORRENT_BYTES and magnet is None


def test_fetch_follows_redirect_to_magnet(monkeypatch):
    # Prowlarr /download redirects to magnet: for magnet-only indexers.
    _serve(monkeypatch, [_Resp(302, location="magnet:?xt=urn:btih:abc")])
    blob, magnet = fetch_torrent_payload("http://prowlarr:9696/download?apikey=k")
    assert blob is None and magnet == "magnet:?xt=urn:btih:abc"


def test_fetch_follows_relative_redirect_then_torrent(monkeypatch):
    seen = _serve(monkeypatch, [_Resp(302, location="/dl/file.torrent"),
                                _Resp(200, TORRENT_BYTES)])
    blob, magnet = fetch_torrent_payload("http://prowlarr:9696/download?apikey=k")
    assert blob == TORRENT_BYTES and magnet is None
    assert seen[1] == "http://prowlarr:9696/dl/file.torrent"


def test_fetch_rejects_non_bencode_body(monkeypatch):
    # An indexer error page (HTML) must not be pushed to the client as a .torrent.
    _serve(monkeypatch, [_Resp(200, b"<html>tracker says no</html>")])
    assert fetch_torrent_payload("http://x/download") == (None, None)


def test_fetch_gives_up_on_redirect_loop(monkeypatch):
    _serve(monkeypatch, [_Resp(302, location="http://x/download")])
    assert fetch_torrent_payload("http://x/download") == (None, None)


def test_fetch_swallows_network_errors(monkeypatch):
    def boom(url, timeout=None, allow_redirects=None):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    assert fetch_torrent_payload("http://x/download") == (None, None)


# ── add_torrent_smart routing ─────────────────────────────────────────────────

class _Adapter:
    def __init__(self):
        self.url_calls = []
        self.file_calls = []

    async def add_torrent(self, url_or_magnet, category="soulsync", save_path=None):
        self.url_calls.append((url_or_magnet, category, save_path))
        return "hash-from-url"

    async def add_torrent_file(self, file_bytes, category="soulsync", save_path=None):
        self.file_calls.append((file_bytes, category, save_path))
        return "hash-from-file"


def test_magnet_goes_straight_to_the_client(monkeypatch):
    def no_fetch(url, timeout=None, allow_redirects=None):
        raise AssertionError("magnets must not be fetched")

    monkeypatch.setattr(requests, "get", no_fetch)
    adapter = _Adapter()
    ref = asyncio.run(add_torrent_smart(adapter, "magnet:?xt=urn:btih:abc"))
    assert ref == "hash-from-url"
    assert adapter.url_calls and not adapter.file_calls


def test_http_url_is_fetched_and_pushed_as_file(monkeypatch):
    _serve(monkeypatch, [_Resp(200, TORRENT_BYTES)])
    adapter = _Adapter()
    ref = asyncio.run(add_torrent_smart(
        adapter, "http://prowlarr:9696/download?apikey=k",
        category="music", save_path="/dl"))
    assert ref == "hash-from-file"
    assert adapter.file_calls == [(TORRENT_BYTES, "music", "/dl")]
    assert not adapter.url_calls               # the client never sees the URL


def test_magnet_redirect_is_added_as_magnet(monkeypatch):
    _serve(monkeypatch, [_Resp(302, location="magnet:?xt=urn:btih:abc")])
    adapter = _Adapter()
    ref = asyncio.run(add_torrent_smart(adapter, "http://prowlarr:9696/download"))
    assert ref == "hash-from-url"
    assert adapter.url_calls[0][0] == "magnet:?xt=urn:btih:abc"


def test_failed_fetch_falls_back_to_legacy_url_handoff(monkeypatch):
    # Setups where the CLIENT can reach the indexer but soulsync can't must
    # keep working exactly as before.
    def boom(url, timeout=None, allow_redirects=None):
        raise requests.ConnectionError("blocked")

    monkeypatch.setattr(requests, "get", boom)
    adapter = _Adapter()
    ref = asyncio.run(add_torrent_smart(adapter, "http://indexer/file.torrent"))
    assert ref == "hash-from-url"
    assert adapter.url_calls[0][0] == "http://indexer/file.torrent"
