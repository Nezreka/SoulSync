"""The v1 video surface runs the app's own /api/video handlers in-process
and wraps them in the v1 envelope. these go through a real video database
in a tmp path so the wishlist round-trips for real.
"""

import sys
import types
from unittest.mock import patch

import pytest


from flask import Blueprint, Flask

from api import video_v1


@pytest.fixture
def client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase

    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    with patch.object(video_v1, "require_api_key", lambda f: f):
        video_v1.register_routes(v1)
    app.register_blueprint(v1)
    try:
        with app.test_client() as c:
            yield c
    finally:
        # the tmp db must not leak past this test: the isolation guard
        # checks get_video_db() still points at the session's own path
        videoapi._video_db = None


def test_wishlist_round_trip(client):
    r = client.post("/api/v1/video/wishlist", json={"movie": {"tmdb_id": 603, "title": "The Matrix", "year": 1999}})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True and body["error"] is None
    assert body["data"]["added"] == 1
    assert body["data"]["counts"]["movie"] == 1

    r = client.get("/api/v1/video/wishlist?kind=movie")
    assert r.status_code == 200
    titles = [m["title"] for m in r.get_json()["data"]["items"]]
    assert titles == ["The Matrix"]

    r = client.delete("/api/v1/video/wishlist", json={"scope": "movie", "tmdb_id": 603})
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/v1/video/wishlist/counts").get_json()["data"]["movie"] == 0


def test_bad_body_is_a_v1_error(client):
    r = client.post("/api/v1/video/wishlist", json={"nope": 1})
    assert r.status_code == 400
    assert r.get_json()["error"] == {"code": "WISHLIST_ERROR", "message": "movie or show+episodes required"}

    r = client.get("/api/v1/video/search")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "BAD_REQUEST"


def test_requests_round_trip(client):
    r = client.post("/api/v1/video/requests", json={"kind": "movie", "tmdb_id": 27205, "title": "Inception", "year": 2010})
    assert r.status_code == 200, r.get_json()
    r = client.get("/api/v1/video/requests")
    assert r.status_code == 200
    reqs = r.get_json()["data"]["requests"]
    assert [x["title"] for x in reqs] == ["Inception"]


def test_status_reads_are_enveloped(client):
    for path in ("/api/v1/video/scan/status", "/api/v1/video/downloads", "/api/v1/video/downloads/status",
                 "/api/v1/video/library", "/api/v1/video/watchlist"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.get_json())
        assert set(r.get_json()) == {"success", "data", "error", "pagination"}


def test_without_the_video_blueprint_it_says_so():
    app = Flask(__name__)
    v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")
    with patch.object(video_v1, "require_api_key", lambda f: f):
        video_v1.register_routes(v1)
    app.register_blueprint(v1)
    r = app.test_client().get("/api/v1/video/wishlist")
    assert r.status_code == 503
    assert r.get_json()["error"]["code"] == "NOT_AVAILABLE"


def test_every_relayed_view_exists_on_the_video_blueprint():
    """a renamed internal view would 503 every caller; catch it here."""
    import inspect
    import re

    import api.video as videoapi

    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    source = inspect.getsource(video_v1)
    wanted = set(re.findall(r'_relay\("([a-z_]+)"', source))
    assert wanted
    missing = [name for name in wanted if f"video_api.{name}" not in app.view_functions]
    assert missing == []
