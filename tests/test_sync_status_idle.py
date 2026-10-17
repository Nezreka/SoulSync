"""GET /api/sync/status/<id> for a playlist with no sync running (#1763).

Idle is the normal state: every dashboard load polls the discover_* ids
before any sync has run. The endpoint used to answer 404 for that, so a
healthy page load produced a burst of 4xx that CrowdSec / fail2ban read as
probing. It now answers 200 with the same {"status": "not_found"} body;
consumers that counted 404s to detect a never-started sync read the body's
status field instead.
"""

import threading

import pytest
from flask import Flask

from api import source_playlists as sp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sp, "sync_lock", threading.Lock())
    monkeypatch.setattr(sp, "sync_states", {})
    monkeypatch.setattr(sp, "active_sync_workers", {})
    app = Flask(__name__)
    app.register_blueprint(sp.bp)
    return app.test_client()


def test_unknown_playlist_is_idle_not_404(client):
    resp = client.get("/api/sync/status/discover_release_radar")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "not_found"}


def test_known_playlist_returns_its_state(client, monkeypatch):
    state = {"status": "syncing", "progress": {"progress": 42}}
    monkeypatch.setattr(sp, "sync_states", {"pl-1": state})
    resp = client.get("/api/sync/status/pl-1")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "syncing"
    assert resp.get_json()["progress"] == {"progress": 42}
