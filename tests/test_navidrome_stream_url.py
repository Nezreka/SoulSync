"""Navidrome stream-URL building (#809): play a library track via the server's
Subsonic /rest/stream API so playback works without mounting the music into
the SoulSync container.

Mirrors the cover-art URL tests — token/salt are random per call, so we assert
structure + required params, not an exact string.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from core.navidrome_client import NavidromeClient


def _connected_client():
    c = NavidromeClient()
    c.base_url = "https://nav.example.com"
    c.username = "boulder"
    c.password = "hunter2"
    return c


def test_builds_authenticated_stream_url():
    url = _connected_client().build_stream_url("song-42")
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "nav.example.com"
    assert parts.path == "/rest/stream"
    q = parse_qs(parts.query)
    assert q["id"] == ["song-42"]
    assert q["u"] == ["boulder"]
    # Subsonic token auth — salted md5, never the raw password.
    assert q["t"] and q["t"][0] != "hunter2"
    assert q["s"]
    assert "hunter2" not in url
    for required in ("t", "s", "v", "c"):
        assert required in q


def test_no_transcode_by_default():
    assert "maxBitRate" not in (_connected_client().build_stream_url("x") or "")


def test_max_bitrate_when_set():
    assert "maxBitRate=320" in _connected_client().build_stream_url("x", max_bitrate=320)
    # 0 / falsy → omitted (original file).
    assert "maxBitRate" not in _connected_client().build_stream_url("x", max_bitrate=0)


def test_song_id_stringified():
    assert "id=12345" in _connected_client().build_stream_url(12345)


def test_returns_none_when_not_connected():
    assert NavidromeClient().build_stream_url("song-1") is None


def test_returns_none_for_empty_song_id():
    assert _connected_client().build_stream_url("") is None
    assert _connected_client().build_stream_url(None) is None


def test_returns_none_without_credentials():
    c = NavidromeClient()
    c.base_url = "https://nav.example.com"
    assert c.build_stream_url("song-1") is None
