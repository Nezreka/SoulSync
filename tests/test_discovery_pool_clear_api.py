"""Endpoint-level tests for DELETE /api/discovery-pool/cache (#1452 follow-up).

DB-level clear semantics live in
tests/database/test_discovery_pool_playlist_filter.py; these pin the route
layer: playlist_id validation (a malformed value must 400, never silently
escalate to a global wipe) and profile/dep wiring.
"""
from __future__ import annotations

import pytest

web_server = pytest.importorskip("web_server")

import api.mirrored_playlists as mpl


class _FakeDB:
    def __init__(self):
        self.calls = []

    def clear_discovery_cache(self, playlist_id=None, profile_id=None):
        self.calls.append({"playlist_id": playlist_id, "profile_id": profile_id})
        return 7


@pytest.fixture()
def client(monkeypatch):
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(mpl, "get_current_profile_id", lambda: 1)
    fake = _FakeDB()
    monkeypatch.setattr(mpl, "get_database", lambda: fake)
    return web_server.app.test_client(), fake


def test_clear_all_calls_db_unscoped(client):
    c, fake = client
    r = c.delete("/api/discovery-pool/cache")
    assert r.status_code == 200
    assert r.get_json() == {"success": True, "cleared": 7}
    assert fake.calls == [{"playlist_id": None, "profile_id": 1}]


def test_clear_scoped_to_playlist(client):
    c, fake = client
    r = c.delete("/api/discovery-pool/cache?playlist_id=42")
    assert r.status_code == 200
    assert fake.calls == [{"playlist_id": 42, "profile_id": 1}]


@pytest.mark.parametrize("bad", ["abc", "0", "-3", "4.5"])
def test_clear_rejects_bad_playlist_id(client, bad):
    c, fake = client
    r = c.delete(f"/api/discovery-pool/cache?playlist_id={bad}")
    assert r.status_code == 400
    assert r.get_json()["error"] == "Invalid playlist_id"
    assert fake.calls == []  # never reaches the DB: no silent global wipe
