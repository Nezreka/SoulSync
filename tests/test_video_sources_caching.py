"""Tests for video sources connection timeout and offline caching resilience."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

import core.video.sources as sources_mod
from core.video.sources import (
    PLEX_CONNECT_TIMEOUT,
    PLEX_SCAN_TIMEOUT,
    _build_source,
    get_active_video_source,
    invalidate_video_source_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_video_source_cache()
    yield
    invalidate_video_source_cache()


def test_plex_connect_timeout_configured_properly():
    assert PLEX_CONNECT_TIMEOUT == 8
    assert PLEX_SCAN_TIMEOUT == 120


def test_build_source_passes_timeout_tuple(monkeypatch):
    monkeypatch.setattr(sources_mod, "resolve_video_server", lambda db=None: "plex")
    monkeypatch.setattr(
        sources_mod,
        "video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "tok12345"},
    )

    captured_kwargs = {}

    def fake_plex_init(base_url, token, timeout=None):
        captured_kwargs["base_url"] = base_url
        captured_kwargs["token"] = token
        captured_kwargs["timeout"] = timeout
        return MagicMock()

    import plexapi.server
    monkeypatch.setattr(plexapi.server, "PlexServer", fake_plex_init)

    src = _build_source()
    assert src is not None
    assert captured_kwargs["timeout"] == (PLEX_CONNECT_TIMEOUT, PLEX_CONNECT_TIMEOUT)
    assert src._server._timeout == (PLEX_CONNECT_TIMEOUT, PLEX_SCAN_TIMEOUT)


def test_build_source_negative_cache_prevents_repeated_connect_attempts(monkeypatch):
    monkeypatch.setattr(sources_mod, "resolve_video_server", lambda db=None: "plex")
    monkeypatch.setattr(
        sources_mod,
        "video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "tok12345"},
    )

    attempts = 0

    def fake_failing_init(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("Server offline")

    import plexapi.server
    monkeypatch.setattr(plexapi.server, "PlexServer", fake_failing_init)

    # First attempt: connection fails, recorded in cache
    src1 = _build_source()
    assert src1 is None
    assert attempts == 1

    # Second attempt within 60s: hits negative cache immediately without calling PlexServer
    src2 = _build_source()
    assert src2 is None
    assert attempts == 1

    # Call through get_active_video_source: also hits negative cache immediately
    monkeypatch.setattr(sources_mod, "_load_selection", lambda: {})
    src_active = get_active_video_source()
    assert src_active is None
    assert attempts == 1

    # Fast forward time beyond 60s TTL: reconnects
    original_at = sources_mod._plex_srv_cache["at"]
    sources_mod._plex_srv_cache["at"] = original_at - 61.0

    src3 = _build_source()
    assert src3 is None
    assert attempts == 2


def test_build_source_credential_change_invalidates_cache(monkeypatch):
    monkeypatch.setattr(sources_mod, "resolve_video_server", lambda db=None: "plex")
    monkeypatch.setattr(
        sources_mod,
        "video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "tok12345"},
    )

    attempts = 0

    def fake_failing_init(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("Server offline")

    import plexapi.server
    monkeypatch.setattr(plexapi.server, "PlexServer", fake_failing_init)

    # First call fails
    assert _build_source() is None
    assert attempts == 1

    # Change credentials
    monkeypatch.setattr(
        sources_mod,
        "video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.2:32400", "token": "tok99999"},
    )

    # Reconnects immediately
    assert _build_source() is None
    assert attempts == 2


def test_build_source_positive_caching_reuses_instance(monkeypatch):
    monkeypatch.setattr(sources_mod, "resolve_video_server", lambda db=None: "plex")
    monkeypatch.setattr(
        sources_mod,
        "video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "tok12345"},
    )

    fake_server = MagicMock()
    init_count = 0

    def fake_plex_init(*args, **kwargs):
        nonlocal init_count
        init_count += 1
        return fake_server

    import plexapi.server
    monkeypatch.setattr(plexapi.server, "PlexServer", fake_plex_init)

    src1 = _build_source(movies_lib="Movies")
    assert src1 is not None
    assert src1._server is fake_server
    assert src1._movies_lib == "Movies"
    assert init_count == 1

    # Second call with different library mapping reuses the underlying connection
    src2 = _build_source(tv_lib="TV Shows")
    assert src2 is not None
    assert src2._server is fake_server
    assert src2._tv_lib == "TV Shows"
    assert init_count == 1

    # Explicit invalidation forces reconnect
    invalidate_video_source_cache()
    src3 = _build_source()
    assert src3 is not None
    assert init_count == 2


def test_video_server_config_test_uses_connect_timeout(tmp_path, monkeypatch):
    from flask import Flask, g
    import api.video as videoapi
    from database.video_database import VideoDatabase

    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()

    monkeypatch.setattr(
        "core.video.sources.video_plex_config",
        lambda db=None: {"base_url": "http://192.0.2.1:32400", "token": "tok12345"},
    )

    captured_timeout = None

    class _FakePlex:
        friendlyName = "Test Plex"

    def fake_plex_init(base_url, token, timeout=None):
        nonlocal captured_timeout
        captured_timeout = timeout
        return _FakePlex()

    import plexapi.server
    monkeypatch.setattr(plexapi.server, "PlexServer", fake_plex_init)

    res = client.post("/api/video/server-config/test", json={"server": "plex"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    assert captured_timeout == PLEX_CONNECT_TIMEOUT

