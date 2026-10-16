"""The v1 video surface runs the app's own /api/video handlers in-process
and wraps them in the v1 envelope. these go through a real video database
in a tmp path so the wishlist round-trips for real.
"""

import sys
import types
from unittest.mock import patch

import pytest


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

from flask import Blueprint, Flask  # noqa: E402

from api import video_v1  # noqa: E402


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


def _fake_channel():
    return {"youtube_id": "UCtest123", "title": "Test Channel",
            "avatar_url": "https://example.com/a.jpg", "videos": []}


def test_youtube_channel_relay_validation(client):
    # resolve without a url -> 400 v1 error envelope
    r = client.get("/api/v1/video/youtube/resolve")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "YOUTUBE_ERROR"

    # follow without url/channel -> 400
    r = client.post("/api/v1/video/youtube/follow", json={})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "YOUTUBE_ERROR"

    # unfollow without youtube_id -> 400
    r = client.post("/api/v1/video/youtube/unfollow", json={})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "YOUTUBE_ERROR"


def test_youtube_follow_unfollow_round_trip(client):
    import core.video.youtube as ytmod

    channel = _fake_channel()
    with patch.object(ytmod, "resolve_channel", return_value=dict(channel)):
        r = client.get("/api/v1/video/youtube/resolve?url=https://www.youtube.com/@test")
        assert r.status_code == 200, r.get_json()
        data = r.get_json()["data"]
        assert data["following"] is False
        assert data["channel"]["youtube_id"] == "UCtest123"

        r = client.post("/api/v1/video/youtube/follow",
                        json={"url": "https://www.youtube.com/@test"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["following"] is True

    # resolve again: now followed
    with patch.object(ytmod, "resolve_channel", return_value=dict(channel)):
        r = client.get("/api/v1/video/youtube/resolve?url=https://www.youtube.com/@test")
        assert r.get_json()["data"]["following"] is True

    r = client.post("/api/v1/video/youtube/unfollow", json={"youtube_id": "UCtest123"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["following"] is False


def _seed_stats(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies (id, server_source, title, updated_at) VALUES (1, 'plex', 'Heat', '2026-10-01 10:00:00')")
        conn.execute("INSERT INTO movies (id, server_source, title) VALUES (2, 'jellyfin', 'Other Server')")
        conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes) VALUES (1, 'Heat.mkv', 1000)")
        conn.execute("INSERT INTO shows (id, server_source, title, updated_at) VALUES (1, 'plex', 'Lost', '2026-10-02 08:30:00')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) VALUES (1, 1, 1)")
        # (episode, has_file, monitored, air_date)
        rows = [(1, 1, 1, "2004-09-22"), (2, 0, 1, "2004-09-29"), (3, 0, 0, "2004-10-06"), (4, 0, 1, "2999-01-01")]
        for row in rows:
            conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file, monitored, air_date) "
                         "VALUES (1, 1, 1, ?, ?, ?, ?)", row)
        conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (1, 'Lost.S01E01.mkv', 500)")
        for title, status, speed in [("a", "downloading", 300), ("b", "queued", 0), ("c", "importing", 0),
                                     ("d", "failed", 0), ("e", "import_failed", 0), ("f", "completed", 0),
                                     ("g", "completed", 0), ("h", "cancelled", 0)]:
            conn.execute("INSERT INTO video_downloads (kind, title, status, speed_bps) VALUES ('movie', ?, ?, ?)",
                         (title, status, speed))
    db.set_setting("video_last_scan_at", "1792172934.5")


def test_stats_counts_the_video_server_only(client, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources

    _seed_stats(videoapi.get_video_db())
    monkeypatch.setattr(sources, "resolve_video_server", lambda db=None: "plex")

    r = client.get("/api/v1/video/stats")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["server"] == "plex"
    # the jellyfin movie is not counted, an episode with no file is not a library episode,
    # and only a monitored aired one is missing (the unmonitored and the unaired are not)
    lib = data["library"]
    assert (lib["movies"], lib["shows"], lib["episodes"], lib["episodes_missing"], lib["size_bytes"]) == (1, 1, 1, 1, 1500)
    assert data["database"]["last_update"] == "2026-10-02T08:30:00+00:00"
    assert data["database"]["last_scan"].startswith("2026-")
    assert data["database"]["size_mb"] > 0
    dl = data["downloads"]
    assert (dl["active"], dl["failed"], dl["completed"], dl["cancelled"], dl["speed_bps"]) == (3, 2, 2, 1, 300)
    assert dl["by_status"]["import_failed"] == 1 and dl["by_status"]["searching"] == 0
    assert data["wishlist"]["total"] == 0


def test_stats_on_an_empty_db_is_all_zeros(client, monkeypatch):
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda db=None: None)

    data = client.get("/api/v1/video/stats").get_json()["data"]
    assert data["library"] == {"movies": 0, "shows": 0, "episodes": 0, "episodes_missing": 0, "size_bytes": 0}
    assert data["downloads"]["active"] == 0 and data["downloads"]["completed_all_time"] == 0
    assert data["database"]["last_update"] is None and data["database"]["last_scan"] is None


def test_downloads_status_filter(client):
    import api.video as videoapi
    import core.video.download_monitor as monitor

    _seed_stats(videoapi.get_video_db())
    with patch.object(monitor, "ensure_started", lambda *a, **k: None):
        everything = client.get("/api/v1/video/downloads").get_json()["data"]["downloads"]
        assert len(everything) == 8

        r = client.get("/api/v1/video/downloads?status=downloading")
        assert r.status_code == 200, r.get_json()
        assert [d["title"] for d in r.get_json()["data"]["downloads"]] == ["a"]
        assert r.get_json()["data"]["count"] == 1

        active = client.get("/api/v1/video/downloads?status=active").get_json()["data"]
        assert sorted(d["title"] for d in active["downloads"]) == ["a", "b", "c"]

        mixed = client.get("/api/v1/video/downloads?status=failed,completed").get_json()["data"]
        assert sorted(d["title"] for d in mixed["downloads"]) == ["d", "e", "f", "g"]

        bad = client.get("/api/v1/video/downloads?status=downlaoding")
        assert bad.status_code == 400
        assert bad.get_json()["error"]["code"] == "BAD_REQUEST"
