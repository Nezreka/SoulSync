"""offline downloads: the admin's switch and cap, and who counts as admin.

a phone paired to an admin profile gets g.is_admin False (devices never
act as the browser admin) and perms_source "admin". some player routes
checked only g.is_admin, so the app's Users & Downloads screen never
showed for an admin. the cap (max_items) was stored and never checked.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

import api.player as player_api
from core.player import downloads


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


def _client(db, monkeypatch, *, profile_id, browser_admin=False, device_admin=False):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"

    @app.before_request
    def _stamp():
        g.is_admin = browser_admin
        if device_admin:
            g.device_auth = True
            g.perms_source = "admin"

    app.register_blueprint(player_api.create_blueprint())
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["profile_id"] = profile_id
    return c


def _log(c, item_id, **extra):
    return c.post("/api/player/downloads", json={
        "item_type": "movie", "item_id": item_id, "title": item_id, "device_id": "phone", **extra})


def test_a_phone_on_an_admin_profile_is_admin_in_the_player(db, monkeypatch):
    phone = _client(db, monkeypatch, profile_id=1, device_admin=True)
    assert phone.get("/api/player/downloads").get_json()["is_admin"] is True
    monkeypatch.setattr(player_api, "get_database",
                        lambda: type("D", (), {"get_all_profiles": lambda self: [{"id": 1, "name": "A"}]})())
    assert phone.get("/api/player/admin/users").status_code == 200
    member = _client(db, monkeypatch, profile_id=2)
    assert member.get("/api/player/admin/users").status_code == 403
    assert member.get("/api/player/downloads").get_json()["is_admin"] is False


def test_the_cap_is_held(db, monkeypatch):
    downloads.set_permission(db, 2, True, 2)
    member = _client(db, monkeypatch, profile_id=2)
    assert _log(member, "a").status_code == 200
    assert _log(member, "b").status_code == 200
    r = _log(member, "c")
    assert r.status_code == 403 and r.get_json()["error"] == "download_limit"
    # downloading one already there again isn't a new one
    assert _log(member, "a").status_code == 200
    # removing one makes room
    member.post("/api/player/downloads/removed", json={"item_type": "movie", "item_id": "a",
                                                       "device_id": "phone"})
    assert _log(member, "c").status_code == 200


def test_switched_off_refuses_but_an_admin_still_downloads(db, monkeypatch):
    downloads.set_permission(db, 2, False, 0)
    downloads.set_permission(db, 1, False, 1)
    member = _client(db, monkeypatch, profile_id=2)
    assert _log(member, "a").status_code == 403
    admin = _client(db, monkeypatch, profile_id=1, browser_admin=True)
    assert _log(admin, "a").status_code == 200
    assert _log(admin, "b").status_code == 200


def test_bad_numbers_are_a_400_not_a_500(db, monkeypatch):
    admin = _client(db, monkeypatch, profile_id=1, browser_admin=True)
    r = admin.put("/api/player/admin/users/2/downloads", json={"max_items": "lots"})
    assert r.status_code == 400
    assert _log(admin, "a", size_bytes="big").status_code == 200
    r = admin.post("/api/player/downloads/removed", json={"id": "abc"})
    assert r.status_code == 200
