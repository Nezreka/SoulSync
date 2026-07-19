"""The video admin gate honors the profile's REAL is_admin flag — a secondary
admin (is_admin, not profile 1) gets the same access the frontend already
shows them. Non-admins stay locked out of the control surfaces."""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase

GATED = ("/api/video/collections", "/api/video/repair/jobs")
OPEN = ("/api/video/issues/counts", "/api/video/wishlist/counts")


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    import core.video.repair.worker as worker_mod
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    worker_mod._worker = None
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.is_admin = g.profile_id == 1 or request.headers.get("X-Test-Admin") == "1"
        g.can_download = True
        g.profile_name = "T"

    try:
        yield app.test_client()
    finally:
        w = worker_mod._worker
        if w:
            w.stop()
        worker_mod._worker = None
        videoapi._video_db = None


def test_gate_honors_the_real_admin_flag(client):
    for path in GATED:
        assert client.get(path).status_code == 200, path                       # profile 1
        assert client.get(path, headers={"X-Test-Profile": "2"}).status_code == 403, path
        assert client.get(path, headers={"X-Test-Profile": "7", "X-Test-Admin": "1"}
                          ).status_code == 200, path                            # secondary admin
    for path in OPEN:
        assert client.get(path, headers={"X-Test-Profile": "2"}).status_code == 200, path
