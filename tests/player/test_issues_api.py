"""the player's issue reports: any profile reports, the admin triages.

the part that would quietly hurt: a report on a relayed movie stored the
media server's stream url, token and all, and handed it back to whoever
reported it (a kids profile, a paired phone). nothing here reaches the
shared session db or starts a worker.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

import api.player as player_api
from core.player import issues as player_issues
from core.player import playable

TOKEN_URL = "http://jellyfin:8096/Videos/abc/stream?static=true&api_key=SECRET123"


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def relayed(monkeypatch):
    def resolve(item_type, item_id, part):
        if item_id == "on-disk":
            return playable.Source(mode="file", path="/movies/Heat (1995)/Heat.mkv")
        return playable.Source(mode="relay", url=TOKEN_URL)
    monkeypatch.setattr(playable, "resolve", resolve)


def _client(db, monkeypatch, *, admin):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    app = Flask(__name__)
    app.secret_key = "test"

    @app.before_request
    def _stamp():
        g.is_admin = admin

    app.register_blueprint(player_api.create_blueprint())
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["profile_id"] = 1 if admin else 2
    return c


def _report(c, item_id="m1"):
    return c.post("/api/player/issues", json={
        "item_type": "movie", "item_id": item_id, "title": "Heat",
        "category": "playback", "note": "stutters", "position_sec": 61,
    })


def test_a_relayed_items_url_is_never_stored_or_returned(db, monkeypatch, relayed):
    member = _client(db, monkeypatch, admin=False)
    r = _report(member)
    assert r.status_code == 201, r.data
    assert "SECRET123" not in r.get_data(as_text=True)
    assert "file_path" not in r.get_json()["issue"]
    listed = member.get("/api/player/issues")
    assert "SECRET123" not in listed.get_data(as_text=True)
    # the admin sees which kind of source it was, still no url
    admin = _client(db, monkeypatch, admin=True)
    issues = admin.get("/api/player/issues").get_json()["issues"]
    assert issues[0]["file_path"] == "(relay stream)"
    with db._get_connection() as conn:
        stored = conn.execute("SELECT file_path FROM player_issues").fetchone()[0]
    assert "SECRET123" not in stored


def test_the_admin_sees_the_file_on_disk(db, monkeypatch, relayed):
    member = _client(db, monkeypatch, admin=False)
    _report(member, "on-disk")
    admin = _client(db, monkeypatch, admin=True)
    issues = admin.get("/api/player/issues").get_json()["issues"]
    assert issues[0]["file_path"] == "/movies/Heat (1995)/Heat.mkv"
    # the member doesn't
    mine = member.get("/api/player/issues").get_json()["issues"]
    assert "file_path" not in mine[0]


def test_an_old_row_with_a_token_url_is_masked(db, monkeypatch):
    player_issues._ensure(db)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO player_issues (profile_id, profile_name, item_type, item_id, title,"
            " subtitle, file_path, category, note, position_sec, status, admin_note, created_at)"
            " VALUES (2, 'Kid', 'movie', 'm', 'T', '', ?, 'playback', '', 0, 'open', '', 1)",
            (TOKEN_URL,),
        )
        conn.commit()
    admin = _client(db, monkeypatch, admin=True)
    body = admin.get("/api/player/issues").get_data(as_text=True)
    assert "SECRET123" not in body


def test_members_see_only_their_own_and_cannot_triage(db, monkeypatch, relayed):
    member = _client(db, monkeypatch, admin=False)
    issue_id = _report(member).get_json()["issue"]["id"]
    admin = _client(db, monkeypatch, admin=True)
    _report(admin)
    assert len(member.get("/api/player/issues").get_json()["issues"]) == 1
    assert len(admin.get("/api/player/issues").get_json()["issues"]) == 2
    r = member.put(f"/api/player/issues/{issue_id}", json={"status": "resolved"})
    assert r.status_code == 403
    r = admin.put(f"/api/player/issues/{issue_id}", json={"status": "resolved", "admin_note": "fixed"})
    assert r.status_code == 200
    assert r.get_json()["issue"]["status"] == "resolved"


def test_the_reporter_or_the_admin_can_delete(db, monkeypatch, relayed):
    member = _client(db, monkeypatch, admin=False)
    admin = _client(db, monkeypatch, admin=True)
    theirs = _report(admin).get_json()["issue"]["id"]
    mine = _report(member).get_json()["issue"]["id"]
    assert member.delete(f"/api/player/issues/{theirs}").status_code == 404
    assert member.delete(f"/api/player/issues/{mine}").status_code == 200
    assert admin.delete(f"/api/player/issues/{theirs}").status_code == 200


def test_bad_reports_are_refused(db, monkeypatch, relayed):
    member = _client(db, monkeypatch, admin=False)
    assert member.post("/api/player/issues", json={"item_type": "nope", "item_id": "1"}).status_code == 400
    assert member.post("/api/player/issues", json={"item_type": "movie"}).status_code == 400
