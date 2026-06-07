"""#809: when a library file isn't on SoulSync's disk, play it by proxying the
media server's stream API instead of 404-ing.

Tests the routing helper _build_library_stream_url: Navidrome-only, uses the
passed song id or falls back to a DB lookup, returns None otherwise.
"""

from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")


class _Client:
    def build_stream_url(self, song_id, max_bitrate=0):
        return f"http://nav.example/rest/stream?id={song_id}"


@pytest.fixture()
def navidrome(monkeypatch):
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "navidrome")
    monkeypatch.setattr(web_server.media_server_engine, "client", lambda name: _Client())


def test_non_navidrome_server_returns_none(monkeypatch):
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "plex")
    assert web_server._build_library_stream_url("song1", "/music/x.flac") is None


def test_uses_passed_track_id(navidrome):
    url = web_server._build_library_stream_url("song-42", "/music/x.flac")
    assert url == "http://nav.example/rest/stream?id=song-42"


def test_falls_back_to_db_lookup_when_no_id(navidrome, monkeypatch):
    class _Cur:
        def execute(self, *a):
            return self
        def fetchone(self):
            return ("song-from-db",)

    class _Conn:
        def cursor(self):
            return _Cur()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(web_server, "get_database",
                        lambda: type("DB", (), {"_get_connection": lambda self: _Conn()})())
    url = web_server._build_library_stream_url(None, "/music/x.flac")
    assert url == "http://nav.example/rest/stream?id=song-from-db"


def test_no_id_no_db_match_returns_none(navidrome, monkeypatch):
    class _Cur:
        def execute(self, *a):
            return self
        def fetchone(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(web_server, "get_database",
                        lambda: type("DB", (), {"_get_connection": lambda self: _Conn()})())
    assert web_server._build_library_stream_url(None, "/music/x.flac") is None
