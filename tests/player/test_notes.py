"""comments on titles and marks on moments"""

from __future__ import annotations

import pytest
from flask import Flask

import api.player as player_api
from core.player import notes


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    monkeypatch.setattr(player_api, "_may_see_title", lambda t, i: i != "secret")
    player_api._who.update({"at": 1e18, "map": {2: {"name": "Ada"}, 3: {"name": "Bo"}}})
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    yield app.test_client()
    player_api._who.update({"at": 0.0, "map": {}})


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


def test_comments_are_shared_marks_are_yours_unless_shared(db):
    notes.add(db, 2, "movie", "7", "loved the ending", now=1)
    notes.add(db, 2, "movie", "7", "the heist", at=3725.5, now=2)
    notes.add(db, 2, "movie", "7", None, at=60, shared=True, now=3)
    mine = notes.list_for(db, 2, "movie", "7")
    assert [(n["at"], n["shared"]) for n in mine] == [(None, True), (60.0, True), (3725.5, False)]
    theirs = notes.list_for(db, 3, "movie", "7")
    assert [n["at"] for n in theirs] == [None, 60.0]
    assert not any(n["mine"] for n in theirs)


@pytest.mark.parametrize("kw", [{"body": ""}, {"body": "x" * 1001}, {"body": "hi", "at": -1},
                                {"body": "hi", "at": True}])
def test_bad_notes_are_refused(db, kw):
    with pytest.raises(notes.NoteError):
        notes.add(db, 2, "movie", "7", **kw)
    with pytest.raises(notes.NoteError):
        notes.add(db, 2, "game", "7", "hi")


def test_only_yours_to_edit_a_moderator_may_take_down(db):
    c = notes.add(db, 2, "movie", "7", "first")
    mark = notes.add(db, 2, "movie", "7", None, at=5)
    assert notes.edit(db, 3, c["id"], "hacked") is None
    assert notes.edit(db, 2, mark["id"], "the drop", shared=True)["shared"] is True
    assert not notes.remove(db, 3, c["id"])
    assert notes.remove(db, 3, c["id"], moderator=True)
    private = notes.add(db, 2, "movie", "7", None, at=9)
    assert not notes.remove(db, 3, private["id"], moderator=True)  # not shared: not theirs to see


def test_a_comment_cannot_be_emptied(db):
    c = notes.add(db, 2, "movie", "7", "first")
    with pytest.raises(notes.NoteError):
        notes.edit(db, 2, c["id"], "  ")


def test_note_routes(client):
    _as(client, 2)
    made = client.post("/api/player/notes/episode/9", json={"at": 61, "body": "this line", "shared": True})
    assert made.status_code == 200 and made.get_json()["note"]["profile"]["name"] == "Ada"
    client.post("/api/player/notes/episode/9", json={"body": "best episode"})
    _as(client, 3)
    got = client.get("/api/player/notes/episode/9").get_json()["notes"]
    assert [n["body"] for n in got] == ["best episode", "this line"]
    nid = got[0]["id"]
    assert client.delete(f"/api/player/note/{nid}").status_code == 404
    assert client.get("/api/player/notes/movie/secret").status_code == 404
    assert client.post("/api/player/notes/movie/secret", json={"body": "x"}).status_code == 404
