"""#1763: a page-load probe of /api/sync/status gets a 200 for an idle playlist.

three discover probes plus one per deezer arl row used to 404 on every load,
which crowdsec's http-probing scenario reads as a scanner. pollers that started
a sync still get the 404, they give up on it.
"""

from __future__ import annotations

import threading

import pytest
from flask import Flask

sp = pytest.importorskip('api.source_playlists')


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(sp, 'sync_states', {'running_1': {'status': 'syncing', 'progress': {}}})
    monkeypatch.setattr(sp, 'active_sync_workers', {})
    monkeypatch.setattr(sp, 'sync_lock', threading.Lock())
    app = Flask(__name__)
    app.add_url_rule('/api/sync/status/<playlist_id>', view_func=sp.get_sync_status)
    return app.test_client()


def test_probe_of_an_idle_playlist_is_a_200(client):
    r = client.get('/api/sync/status/discover_release_radar?probe=1')
    assert r.status_code == 200
    assert r.get_json() == {'status': 'idle'}


def test_poll_of_an_idle_playlist_still_404s(client):
    r = client.get('/api/sync/status/discover_release_radar')
    assert r.status_code == 404
    assert r.get_json() == {'status': 'not_found'}


def test_probe_of_a_running_sync_returns_its_state(client):
    r = client.get('/api/sync/status/running_1?probe=1')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'syncing'
