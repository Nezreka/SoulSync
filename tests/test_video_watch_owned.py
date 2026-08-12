"""The watch-together ownership probe (/api/video/watch/owned).

The chat page's movie-night ballot is shared state (a fold over the room's
protocol bus), but ownership is personal — each client asks its OWN video
library "can this box play these". The endpoint answers per nomination key
("m:603", "t:1399:1x1"), keyed exactly like chat-protocol.js reduceWatch, and
"owned" means a playable file (video_stored_file_path), not library presence.
"""

from __future__ import annotations

from flask import Flask


class _StubDb:
    """Only what the route touches — answers a fixed ownership table."""

    OWNED = {("movie", 603, None, None), ("episode", 1399, 1, 1)}

    def __init__(self):
        self.calls = []

    def video_stored_file_path(self, kind, *, tmdb_id=None, season=None, episode=None):
        self.calls.append((kind, tmdb_id, season, episode))
        if (kind, tmdb_id, season, episode) in self.OWNED:
            return {"path": "Movies/x.mkv", "size_bytes": 1}
        return None


def _client(stub):
    import api.video as videoapi
    videoapi._video_db = stub
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_owned_map_uses_reducer_keys():
    c = _client(_StubDb())
    r = c.post("/api/video/watch/owned", json={"items": [
        {"kd": "m", "id": 603},
        {"kd": "m", "id": "604"},                       # string ids fine (protocol strings)
        {"kd": "t", "id": 1399, "s": 1, "e": 1},
        {"kd": "t", "id": 1399, "s": 1, "e": 2},
    ]})
    assert r.status_code == 200
    assert r.get_json()["owned"] == {
        "m:603": True,
        "m:604": False,
        "t:1399:1x1": True,
        "t:1399:1x2": False,
    }


def test_garbage_items_are_skipped_not_fatal():
    c = _client(_StubDb())
    r = c.post("/api/video/watch/owned", json={"items": [
        "not-a-dict",
        {"kd": "m", "id": "abc"},                       # non-numeric id
        {"kd": "m", "id": -5},                          # negative id
        {"kd": "z", "id": 603},                         # unknown kind
        {"kd": "t", "id": 1399, "s": 1},                # episode missing e
        {"kd": "t", "id": 1399, "s": -1, "e": 2},       # negative season
        {"kd": "m", "id": 603},                         # the one sane item
    ]})
    assert r.status_code == 200
    assert r.get_json()["owned"] == {"m:603": True}


def test_items_must_be_a_list():
    c = _client(_StubDb())
    assert c.post("/api/video/watch/owned", json={"items": "m:603"}).status_code == 400
    assert c.post("/api/video/watch/owned", json={}).status_code == 400


def test_request_is_capped_at_32_probes():
    stub = _StubDb()
    c = _client(stub)
    items = [{"kd": "m", "id": i + 1} for i in range(50)]
    r = c.post("/api/video/watch/owned", json={"items": items})
    assert r.status_code == 200
    assert len(stub.calls) == 32
    assert len(r.get_json()["owned"]) == 32
