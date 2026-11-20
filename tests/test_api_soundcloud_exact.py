"""/api/v1/soundcloud/exact: thin routes over core/wishlist/soundcloud_exact.

The extension reads a 404 or 405 as "this server is too old", so none of
these routes may answer either for a request it understood.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Blueprint, Flask

import api.soundcloud_exact as route_mod
import core.download_orchestrator as orch_mod
import database.music_database as db_mod
from core.wishlist import soundcloud_exact as exact


def _app():
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(route_mod, "require_api_key", lambda f: f):
        route_mod.register_routes(bp)
    app.register_blueprint(bp)
    return app


class FakeDB:
    def __init__(self, profiles=(1, 2, 3)):
        self.profiles = profiles

    def quality_profile_exists(self, qp):
        return qp in self.profiles


@pytest.fixture()
def calls(monkeypatch):
    seen = {"add": [], "status": []}
    monkeypatch.setattr(orch_mod, "get_download_orchestrator", lambda: "ORCH")
    monkeypatch.setattr(db_mod, "get_database", lambda: FakeDB())
    monkeypatch.setattr(exact, "source_state", lambda orch: (True, None))

    def fake_status(db, *, sc_id=None, url=None, profile_id=1):
        seen["status"].append({"sc_id": sc_id, "url": url, "profile_id": profile_id})
        return {"state": "queued", "matched": True, "track_id": "soundcloud_exact_123",
                "failure_reason": None, "retry_count": 0, "last_attempted": None}

    def fake_add(db, orch, **kw):
        seen["add"].append(kw)
        return 201, {"created": True, "state": "queued", "track_id": "soundcloud_exact_123",
                     "upload": {}, "track": None, "playlist": None}

    monkeypatch.setattr(exact, "status_for", fake_status)
    monkeypatch.setattr(exact, "add_exact", fake_add)
    return seen


def test_probe_says_supported(calls):
    resp = _app().test_client().get("/api/v1/soundcloud/exact")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["supported"] is True and data["enabled"] is True and data["reason"] is None
    assert data["state"] == "none" and calls["status"] == []


def test_probe_reports_a_disabled_source(calls, monkeypatch):
    monkeypatch.setattr(exact, "source_state", lambda orch: (False, "source_disabled"))
    data = _app().test_client().get("/api/v1/soundcloud/exact").get_json()["data"]
    assert (data["supported"], data["enabled"], data["reason"]) == (True, False, "source_disabled")


def test_status_by_id_and_url_with_the_profile(calls):
    client = _app().test_client()
    resp = client.get("/api/v1/soundcloud/exact?sc_id=123", headers={"X-Profile-Id": "4"})
    assert resp.status_code == 200 and resp.get_json()["data"]["state"] == "queued"
    client.get("/api/v1/soundcloud/exact?url=https://soundcloud.com/u/t&profile_id=2")
    assert calls["status"] == [
        {"sc_id": "123", "url": None, "profile_id": 4},
        {"sc_id": None, "url": "https://soundcloud.com/u/t", "profile_id": 2},
    ]


def test_a_bad_sc_id_is_400(calls):
    resp = _app().test_client().get("/api/v1/soundcloud/exact?sc_id=12x")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "BAD_REQUEST"


def test_playlists(monkeypatch):
    from core.playlists import user_playlists
    seen = []
    monkeypatch.setattr(db_mod, "get_database", lambda: "DB")
    monkeypatch.setattr(user_playlists, "list_playlists", lambda db, pid: seen.append(pid) or [
        {"id": 12, "name": "Flips", "track_count": 40, "image_url": None, "cover_tiles": [], "liked": False}])
    resp = _app().test_client().get("/api/v1/soundcloud/exact/playlists", headers={"X-Profile-Id": "3"})
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"playlists": [
        {"id": 12, "name": "Flips", "track_count": 40, "liked": False}]}
    assert seen == [3]


def test_add_passes_everything_through(calls):
    body = {"url": "https://soundcloud.com/u/t", "title": "Song", "artists": ["A", "B"],
            "remixers": ["R"], "playlist_id": 12, "quality_profile_id": 2}
    resp = _app().test_client().post("/api/v1/soundcloud/exact", json=body,
                                     headers={"X-Profile-Id": "3"})
    assert resp.status_code == 201
    assert resp.get_json()["data"]["created"] is True
    assert calls["add"] == [{"url": "https://soundcloud.com/u/t", "title": "Song", "artists": ["A", "B"],
                             "remixers": ["R"], "playlist_id": 12, "quality_profile_id": 2,
                             "profile_id": 3}]


def test_the_stored_row_is_serialized(calls, monkeypatch):
    monkeypatch.setattr(exact, "add_exact", lambda db, orch, **kw: (
        201, {"created": True, "track": {"spotify_track_id": "soundcloud_exact_123"}}))
    monkeypatch.setattr(route_mod, "serialize_wishlist_track",
                        lambda row: {"serialized": row["spotify_track_id"]})
    resp = _app().test_client().post("/api/v1/soundcloud/exact", json={"url": "https://soundcloud.com/u/t"})
    assert resp.get_json()["data"]["track"] == {"serialized": "soundcloud_exact_123"}


def test_an_update_is_200(calls, monkeypatch):
    monkeypatch.setattr(exact, "add_exact", lambda db, orch, **kw: (200, {"created": False}))
    resp = _app().test_client().post("/api/v1/soundcloud/exact", json={"url": "https://soundcloud.com/u/t"})
    assert resp.status_code == 200 and resp.get_json()["data"]["created"] is False


@pytest.mark.parametrize("body", [
    None,
    {},
    {"url": ""},
    {"url": 5},
    {"url": "https://soundcloud.com/u/t", "title": 3},
    {"url": "https://soundcloud.com/u/t", "artists": [1]},
    {"url": "https://soundcloud.com/u/t", "artists": [str(i) for i in range(11)]},
    {"url": "https://soundcloud.com/u/t", "remixers": {"a": 1}},
    {"url": "https://soundcloud.com/u/t", "playlist_id": 0},
    {"url": "https://soundcloud.com/u/t", "playlist_id": "abc"},
    {"url": "https://soundcloud.com/u/t", "quality_profile_id": -1},
    {"url": "https://soundcloud.com/u/t", "quality_profile_id": True},
    {"url": "https://soundcloud.com/u/t", "quality_profile_id": 99},
])
def test_validation_is_400_never_404_or_405(calls, body):
    client = _app().test_client()
    resp = (client.post("/api/v1/soundcloud/exact", data="not json", content_type="application/json")
            if body is None else client.post("/api/v1/soundcloud/exact", json=body))
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["error"]["code"] == "BAD_REQUEST"
    assert calls["add"] == []


@pytest.mark.parametrize("status, code", [
    (400, "BAD_REQUEST"),
    (400, "NOT_SOUNDCLOUD_TRACK"),
    (400, "BAD_PLAYLIST"),
    (422, "PLAYLIST_NOT_EDITABLE"),
    (422, "UPLOAD_UNAVAILABLE"),
    (409, "SOURCE_DISABLED"),
    (503, "SOURCE_UNAVAILABLE"),
    (409, "BLOCKLISTED"),
    (400, "REJECTED"),
    (500, "WISHLIST_ERROR"),
])
def test_service_refusals_map_to_the_envelope(calls, monkeypatch, status, code):
    def refuse(db, orch, **kw):
        raise exact.ExactError(status, code, "nope")

    monkeypatch.setattr(exact, "add_exact", refuse)
    resp = _app().test_client().post("/api/v1/soundcloud/exact", json={"url": "https://soundcloud.com/u/t"})
    assert resp.status_code == status
    body = resp.get_json()
    assert body["success"] is False and body["error"] == {"code": code, "message": "nope"}


def test_an_unexpected_error_is_500(calls, monkeypatch):
    def boom(db, orch, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(exact, "add_exact", boom)
    resp = _app().test_client().post("/api/v1/soundcloud/exact", json={"url": "https://soundcloud.com/u/t"})
    assert resp.status_code == 500
    assert resp.get_json()["error"]["code"] == "WISHLIST_ERROR"


def test_the_blueprint_registers_the_routes():
    from api import create_api_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    rules = {(r.rule, m) for r in app.url_map.iter_rules() for m in (r.methods or ())}
    assert ("/api/v1/soundcloud/exact", "GET") in rules
    assert ("/api/v1/soundcloud/exact", "POST") in rules
    assert ("/api/v1/soundcloud/exact/playlists", "GET") in rules


def test_the_routes_need_a_key():
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    route_mod.register_routes(bp)
    app.register_blueprint(bp)
    for rule in app.url_map.iter_rules():
        if "soundcloud" in rule.rule:
            assert app.view_functions[rule.endpoint]._soulsync_access == ("api_key", None)
