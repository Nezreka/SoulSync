"""ChatBIC P2 — the room message archive (history survives slskd restarts).

Hermetic: a tmp MusicDatabase for the table, fakes for the blueprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def mdb(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _m(n, user="alice", rich=True, text=None):
    return {"username": user, "message": text or ("msg %d" % n), "rich": rich,
            "timestamp": "2026-07-19 10:%02d:00" % n}


class TestArchiveDb:
    def test_round_trip_and_rich_flag(self, mdb):
        assert mdb.add_chat_messages("SoulSync", [_m(1), _m(2, rich=False)]) == 2
        rows = mdb.get_chat_messages("SoulSync")
        assert [r["message"] for r in rows] == ["msg 1", "msg 2"]   # oldest-first
        assert rows[0]["rich"] is True and rows[1]["rich"] is False

    def test_idempotent_replays(self, mdb):
        batch = [_m(1), _m(2)]
        assert mdb.add_chat_messages("SoulSync", batch) == 2
        # the push loop + hydrate both feed the same buffer — replays are free
        assert mdb.add_chat_messages("SoulSync", batch) == 0
        assert len(mdb.get_chat_messages("SoulSync")) == 2

    def test_paging_backwards(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(i) for i in range(1, 10)])
        newest = mdb.get_chat_messages("SoulSync", limit=3)
        assert [r["message"] for r in newest] == ["msg 7", "msg 8", "msg 9"]
        older = mdb.get_chat_messages("SoulSync", before=newest[0]["timestamp"], limit=3)
        assert [r["message"] for r in older] == ["msg 4", "msg 5", "msg 6"]

    def test_rooms_are_isolated_and_pruned(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1)])
        mdb.add_chat_messages("other", [_m(2)])
        assert len(mdb.get_chat_messages("SoulSync")) == 1
        mdb._CHAT_ARCHIVE_KEEP = 5
        mdb.add_chat_messages("SoulSync", [_m(i) for i in range(2, 12)])
        assert len(mdb.get_chat_messages("SoulSync", limit=500)) == 5
        assert len(mdb.get_chat_messages("other")) == 1     # untouched

    def test_junk_rows_skipped(self, mdb):
        assert mdb.add_chat_messages("SoulSync", [
            {"username": "", "message": "x", "timestamp": "t"},
            {"username": "u", "message": "", "timestamp": "t"},
            "not a dict", None]) == 0


class TestArchiveReply:
    def test_reply_survives_the_archive(self, mdb):
        mdb.add_chat_messages("SoulSync", [
            {"username": "a", "message": "yes", "rich": True,
             "timestamp": "2026-07-19 10:00:00",
             "reply": {"u": "bob", "x": "should we?"}},
            {"username": "b", "message": "plain", "rich": False,
             "timestamp": "2026-07-19 10:01:00"},
        ])
        rows = mdb.get_chat_messages("SoulSync")
        assert rows[0]["reply"] == {"u": "bob", "x": "should we?"}
        assert "reply" not in rows[1]


class TestArchiveApi:
    def _app(self, mdb):
        import api.chat as chat_api
        from flask import Flask, g
        from tests.test_chat_api import _FakeChatClient
        client = _FakeChatClient()
        chat_api._INGEST_AT.clear()
        chat_api.configure(client_getter=lambda: client, run_async=lambda v: v,
                           config_get=lambda k, d=None: d, db_getter=lambda: mdb)
        app = Flask(__name__)

        @app.before_request
        def _p():
            g.is_admin = True
        app.register_blueprint(chat_api.create_blueprint())
        return app.test_client(), client

    def test_hydrate_archives_and_serves_the_archive(self, mdb):
        http, client = self._app(mdb)
        from core.chat_codec import encode
        client.get_room_messages = lambda room: [
            {"username": "a", "message": encode("rich one"), "timestamp": "2026-07-19 10:00:00"},
            {"username": "b", "message": "plain", "timestamp": "2026-07-19 10:01:00"},
        ]
        msgs = http.get("/api/chat/room").get_json()["messages"]
        assert [m["message"] for m in msgs] == ["rich one", "plain"]
        assert msgs[0]["rich"] is True
        # the archive now holds the DECODED copy — an slskd restart loses nothing
        rows = mdb.get_chat_messages("SoulSync")
        assert [r["message"] for r in rows] == ["rich one", "plain"]

    def test_history_endpoint_pages_older(self, mdb):
        http, client = self._app(mdb)
        mdb.add_chat_messages("SoulSync", [_m(i) for i in range(1, 8)])
        res = http.get("/api/chat/room/history?before=2026-07-19 10:04:00&limit=2").get_json()
        assert [m["message"] for m in res["messages"]] == ["msg 2", "msg 3"]
        assert res["done"] is False
        res = http.get("/api/chat/room/history?before=2026-07-19 10:02:00&limit=5").get_json()
        assert [m["message"] for m in res["messages"]] == ["msg 1"]
        assert res["done"] is True

    def test_hydrate_ingest_is_throttled(self, mdb, monkeypatch):
        http, client = self._app(mdb)
        calls = []
        real = mdb.add_chat_messages
        monkeypatch.setattr(mdb, "add_chat_messages",
                            lambda room, msgs: calls.append(1) or real(room, msgs))
        http.get("/api/chat/room")
        http.get("/api/chat/room")      # 4s poll — must NOT re-ingest the buffer
        assert len(calls) == 1


def test_push_loop_feeds_the_archive():
    ws = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
    loop = ws.split("def _emit_chat_push_loop")[1].split("\ndef ")[0]
    assert "add_chat_messages(room, decoded)" in loop


def test_frontend_store_and_scrollback_pins():
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8", errors="replace")
    assert "function mergeMessages(" in js
    assert "function loadOlder(" in js
    assert "/api/chat/room/history?before=" in js
    assert "scroller.scrollTop < 60) loadOlder()" in js
    # scroll anchor preserved when history prepends
    assert "host.scrollHeight - prevH + prevTop" in js
    # store trims once the reader returns to the bottom
    assert "state.msgs.slice(-300)" in js
