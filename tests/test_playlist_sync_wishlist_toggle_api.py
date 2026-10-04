"""#1455: pipeline/run endpoint honors the wishlist toggle + per-click override.

Needs the full app stack (web_server import); skipped in minimal envs,
runs on CI. The pure unit tests live in test_playlist_sync_wishlist_toggle.py.
"""
from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")

import api.mirrored_playlists as mpl  # noqa: E402  (needs the full app stack)


class _FakeConfigManager:
    def __init__(self, settings):
        self._settings = settings

    def get(self, key, default=None):
        return self._settings.get(key, default)


class _FakeThread:
    instances = []

    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        _FakeThread.instances.append(self)

    def start(self):
        pass  # do not run the pipeline in tests


@pytest.fixture()
def client(monkeypatch):
    _FakeThread.instances.clear()
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(mpl, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(mpl, "get_database", lambda: object())
    monkeypatch.setattr(
        mpl, "_owned_mirrored_playlist",
        lambda db, pid: {"id": pid, "name": "Test", "source": "spotify"})
    monkeypatch.setattr(
        mpl, "_get_automation_deps",
        lambda: type("D", (), {"state": type(
            "S", (), {"is_pipeline_running": staticmethod(lambda: False)})()})())
    monkeypatch.setattr("threading.Thread", _FakeThread)
    return web_server.app.test_client()


def _thread_skip_wishlist():
    assert len(_FakeThread.instances) == 1
    # args = (playlist_id, skip_wishlist, profile_id, refresh_only)
    return _FakeThread.instances[0].args[1]


def test_endpoint_empty_body_uses_toggle_on(monkeypatch, client):
    monkeypatch.setattr(
        mpl, "config_manager",
        _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": True}))
    r = client.post("/api/mirrored-playlists/7/pipeline/run", json={})
    assert r.status_code == 200
    assert _thread_skip_wishlist() is False


def test_endpoint_empty_body_toggle_off_skips(monkeypatch, client):
    monkeypatch.setattr(
        mpl, "config_manager",
        _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": False}))
    r = client.post("/api/mirrored-playlists/7/pipeline/run", json={})
    assert r.status_code == 200
    assert _thread_skip_wishlist() is True


def test_endpoint_explicit_true_overrides_toggle_on(monkeypatch, client):
    monkeypatch.setattr(
        mpl, "config_manager",
        _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": True}))
    r = client.post("/api/mirrored-playlists/7/pipeline/run",
                    json={"skip_wishlist": True})
    assert r.status_code == 200
    assert _thread_skip_wishlist() is True


def test_endpoint_explicit_false_overrides_toggle_off(monkeypatch, client):
    monkeypatch.setattr(
        mpl, "config_manager",
        _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": False}))
    r = client.post("/api/mirrored-playlists/7/pipeline/run",
                    json={"skip_wishlist": False})
    assert r.status_code == 200
    assert _thread_skip_wishlist() is False
