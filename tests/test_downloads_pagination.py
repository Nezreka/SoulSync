"""Unit tests for downloads endpoint pagination.

Fix 4.1: `GET /api/v1/downloads` previously returned every task in the
in-memory `download_tasks` dict on every call. With many downloads this
produces a huge payload. The endpoint now supports `limit`, `offset`,
and `status` query params and includes a `total` count.
"""

import sys
import types
from unittest.mock import patch

import pytest


# api/__init__.py eagerly imports flask_limiter. Stub before import.
def _install_flask_limiter_stub():
    if "flask_limiter" in sys.modules:
        return
    stub = types.ModuleType("flask_limiter")

    class _Limiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, *args, **kwargs):
            def decorator(target):
                return target
            return decorator

        def init_app(self, app):
            pass

    stub.Limiter = _Limiter
    sys.modules["flask_limiter"] = stub

    util_stub = types.ModuleType("flask_limiter.util")
    util_stub.get_remote_address = lambda: "127.0.0.1"
    sys.modules["flask_limiter.util"] = util_stub


_install_flask_limiter_stub()

from flask import Flask, Blueprint  # noqa: E402

from api import downloads as downloads_mod  # noqa: E402
import core.import_runtime_state as runtime_state  # noqa: E402


def _make_task(status="downloading", when=None):
    return {
        "status": status,
        "track_name": f"Track {when}",
        "artist_name": "Artist",
        "album_name": "Album",
        "username": "user",
        "filename": "file.mp3",
        "progress": 0,
        "size": 1000,
        "status_change_time": when or "2026-01-01T00:00:00",
    }


def _make_app_with_tasks(tasks_dict):
    """Create a minimal Flask app with the downloads blueprint mounted and
    the shared import runtime state populated with the given download_tasks dict."""
    original_tasks = dict(runtime_state.download_tasks)
    runtime_state.download_tasks.clear()
    runtime_state.download_tasks.update(tasks_dict)

    # Bypass API key auth for tests.
    def _passthrough(f):
        return f

    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")

    with patch.object(downloads_mod, "require_api_key", _passthrough):
        # downloads.register_routes was already imported with the real
        # decorator bound, but register_routes runs fresh decorators at
        # call time against the passed blueprint.
        downloads_mod.register_routes(bp)

    app.register_blueprint(bp)
    app._original_download_tasks = original_tasks
    return app


@pytest.fixture
def client():
    tasks = {
        f"task-{i:03d}": _make_task(
            status="downloading" if i % 2 == 0 else "queued",
            when=f"2026-01-{i+1:02d}T00:00:00",
        )
        for i in range(25)
    }
    app = _make_app_with_tasks(tasks)
    try:
        with app.test_client() as c:
            yield c
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(app._original_download_tasks)


def test_default_limit_applied(client):
    # 25 tasks, default limit is 100 -> all fit in one page.
    resp = client.get("/api/v1/downloads")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["total"] == 25
    assert data["limit"] == 100
    assert data["offset"] == 0
    assert len(data["downloads"]) == 25


def test_limit_and_offset_slice_correctly(client):
    resp = client.get("/api/v1/downloads?limit=5&offset=0")
    data = resp.get_json()["data"]
    assert len(data["downloads"]) == 5
    assert data["total"] == 25

    resp2 = client.get("/api/v1/downloads?limit=5&offset=20")
    data2 = resp2.get_json()["data"]
    assert len(data2["downloads"]) == 5
    # Pages should not overlap.
    page1_ids = {t["id"] for t in data["downloads"]}
    page5_ids = {t["id"] for t in data2["downloads"]}
    assert page1_ids.isdisjoint(page5_ids)


def test_status_filter_single(client):
    resp = client.get("/api/v1/downloads?status=downloading&limit=100")
    data = resp.get_json()["data"]
    # 13 even-indexed tasks (0,2,...,24)
    assert data["total"] == 13
    for t in data["downloads"]:
        assert t["status"] == "downloading"


def test_status_filter_multiple_comma_separated(client):
    resp = client.get("/api/v1/downloads?status=downloading,queued&limit=100")
    data = resp.get_json()["data"]
    assert data["total"] == 25


def test_status_filter_no_match_returns_empty(client):
    resp = client.get("/api/v1/downloads?status=nonexistent_status")
    data = resp.get_json()["data"]
    assert data["total"] == 0
    assert data["downloads"] == []


def test_limit_is_clamped_to_max(client):
    resp = client.get("/api/v1/downloads?limit=99999")
    data = resp.get_json()["data"]
    assert data["limit"] == 500


def test_negative_offset_is_normalized(client):
    resp = client.get("/api/v1/downloads?offset=-5")
    data = resp.get_json()["data"]
    assert data["offset"] == 0


def test_invalid_limit_falls_back_to_default(client):
    resp = client.get("/api/v1/downloads?limit=not_a_number")
    data = resp.get_json()["data"]
    assert data["limit"] == 100


def test_tasks_sorted_newest_first(client):
    resp = client.get("/api/v1/downloads?limit=3&offset=0")
    data = resp.get_json()["data"]
    times = [t["status_change_time"] for t in data["downloads"]]
    # Most recent (2026-01-25) should come first.
    assert times == sorted(times, reverse=True)
