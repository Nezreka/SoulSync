"""The public v1 API delegates to the app's own handlers, not to web_server.

the web_server decomposition moved the wishlist / watchlist / sync / hydrabase
helpers into api modules, and the v1 endpoints kept importing them from
web_server. `from web_server import name` raised ImportError, the routes
caught it, and every caller got 501 (#1259, Wavio). these pin each seam to
the module that owns the code now, and check the status mapping.
"""

import sys
import types
from unittest.mock import patch

import pytest


from flask import Blueprint, Flask, jsonify

from api import playlists as playlists_mod
from api import watchlist as watchlist_mod
from api import wishlist as wishlist_mod


def _app(module):
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(module, "require_api_key", lambda f: f):
        module.register_routes(bp)
    app.register_blueprint(bp)
    return app


# ---- nothing in api/v1 imports from web_server any more ----

def test_no_v1_module_imports_from_web_server():
    import pathlib
    v1 = ['library', 'system', 'search', 'wishlist', 'watchlist', 'downloads', 'playlists',
          'settings', 'discover', 'profiles', 'retag', 'listenbrainz', 'cache', 'metasync',
          'request', 'helpers', 'auth', 'serializers']
    offenders = []
    for name in v1:
        text = pathlib.Path('api', f'{name}.py').read_text(encoding='utf-8')
        if 'from web_server import' in text or 'import web_server' in text:
            offenders.append(name)
    assert offenders == []


# ---- wishlist/process ----

def test_wishlist_process_starts_the_shared_processor(monkeypatch):
    from api import wishlist_routes as internal

    started = []
    monkeypatch.setattr(internal, "_process_wishlist_automatically", lambda: started.append(1))
    monkeypatch.setattr(internal, "_build_wishlist_route_runtime", lambda: types.SimpleNamespace(
        is_wishlist_actually_processing=lambda: False,
        thread_factory=lambda target, daemon: types.SimpleNamespace(start=target),
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    ))
    client = _app(wishlist_mod).test_client()
    resp = client.post("/api/v1/wishlist/process")
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["message"] == "Wishlist processing started."
    assert started == [1]


def test_wishlist_process_says_conflict_when_already_running(monkeypatch):
    from api import wishlist_routes as internal

    monkeypatch.setattr(internal, "_process_wishlist_automatically", lambda: None)
    monkeypatch.setattr(internal, "_build_wishlist_route_runtime", lambda: types.SimpleNamespace(
        is_wishlist_actually_processing=lambda: True,
        thread_factory=None,
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    ))
    resp = _app(wishlist_mod).test_client().post("/api/v1/wishlist/process")
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "CONFLICT"


def test_wishlist_process_is_503_not_501_before_boot_wiring(monkeypatch):
    from api import wishlist_routes as internal

    monkeypatch.setattr(internal, "_process_wishlist_automatically", None)
    resp = _app(wishlist_mod).test_client().post("/api/v1/wishlist/process")
    assert resp.status_code == 503


# ---- watchlist/scan ----

@pytest.mark.parametrize("internal_status, expected, code", [
    (200, 200, None),
    (409, 409, "CONFLICT"),
    (400, 400, "WATCHLIST_ERROR"),
])
def test_watchlist_scan_maps_the_internal_handler(monkeypatch, internal_status, expected, code):
    from api import artist_watchlist as internal

    def fake_scan():
        body = {"success": internal_status == 200, "error": "no provider"}
        return jsonify(body), internal_status

    monkeypatch.setattr(internal, "start_watchlist_scan", fake_scan)
    resp = _app(watchlist_mod).test_client().post("/api/v1/watchlist/scan")
    assert resp.status_code == expected
    if code:
        assert resp.get_json()["error"]["code"] == code
        if code == "WATCHLIST_ERROR":
            assert resp.get_json()["error"]["message"] == "no provider"


# ---- playlists/<id>/sync ----

def test_playlist_sync_runs_in_process_with_the_url_id(monkeypatch):
    from api import source_playlists as internal

    seen = []

    def fake_start(data):
        seen.append(data)
        return jsonify({"success": True, "message": "Sync started."})

    monkeypatch.setattr(internal, "start_playlist_sync_from_payload", fake_start)
    resp = _app(playlists_mod).test_client().post(
        "/api/v1/playlists/pl-1/sync",
        json={"playlist_name": "Mix", "tracks": [{"name": "a"}], "sync_mode": "append"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert seen == [{
        "playlist_id": "pl-1", "playlist_name": "Mix", "tracks": [{"name": "a"}],
        "image_url": "", "sync_mode": "append",
    }]


def test_playlist_sync_conflict_and_failure_map(monkeypatch):
    from api import source_playlists as internal

    monkeypatch.setattr(internal, "start_playlist_sync_from_payload",
                        lambda data: (jsonify({"success": False, "error": "busy"}), 409))
    resp = _app(playlists_mod).test_client().post(
        "/api/v1/playlists/pl-1/sync", json={"playlist_name": "Mix", "tracks": [1]})
    assert resp.status_code == 409

    monkeypatch.setattr(internal, "start_playlist_sync_from_payload",
                        lambda data: (jsonify({"success": False, "error": "Missing playlist_id, name, or tracks."}), 400))
    resp = _app(playlists_mod).test_client().post(
        "/api/v1/playlists/pl-1/sync", json={"playlist_name": "Mix", "tracks": [1]})
    assert resp.status_code == 400
    assert "Missing" in resp.get_json()["error"]["message"]
