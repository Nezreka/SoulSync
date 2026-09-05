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


class TestArchiveFileCard:
    def test_file_metadata_survives_the_archive(self, mdb):
        """A shared-file message must keep its card metadata through the
        archive — otherwise an archived file message renders as a bare link
        with no preview or save-to-library button (only 'new' ones worked)."""
        mdb.add_chat_messages("SoulSync", [
            {"username": "dj", "message": "https://cdn.filepost.dev/x/song.flac",
             "timestamp": "2026-07-19 10:00:00",
             "file": {"n": "song.flac", "s": 12345, "m": "audio/flac"}},
            {"username": "b", "message": "just chatting",
             "timestamp": "2026-07-19 10:01:00"},
        ])
        rows = mdb.get_chat_messages("SoulSync")
        assert rows[0]["file"] == {"n": "song.flac", "s": 12345, "m": "audio/flac"}
        assert "file" not in rows[1]      # a plain message carries no file key

    def test_malformed_file_is_dropped_not_stored(self, mdb):
        mdb.add_chat_messages("SoulSync", [
            {"username": "x", "message": "u", "timestamp": "2026-07-19 10:02:00",
             "file": {"s": 5}},           # no name → not a valid card
        ])
        rows = mdb.get_chat_messages("SoulSync")
        assert "file" not in rows[0]


class TestArchiveApi:
    def _app(self, mdb):
        import api.chat as chat_api
        from flask import Flask, g
        from tests.test_chat_api import _FakeChatClient
        client = _FakeChatClient()
        chat_api._INGEST_AT.clear()
        chat_api.configure(client_getter=lambda: client, run_async=lambda v, timeout=None: v,
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
    assert "/api/chat/room/history?room=" in js   # room-scoped since multi-room P1
    assert "scroller.scrollTop < 60) loadOlder()" in js
    # scroll anchor preserved when history prepends
    assert "host.scrollHeight - prevH + prevTop" in js
    # store trims once the reader returns to the bottom
    assert "state.msgs.slice(-300)" in js


class TestArchiveSearch:
    def test_search_matches_text_and_sender_newest_first(self, mdb):
        mdb.add_chat_messages("SoulSync", [
            _m(1, text="anyone have the new Meshuggah?"),
            _m(2, user="bob", text="check soulseek search"),
            _m(3, text="meshuggah rules"),
        ])
        hits = mdb.search_chat_messages("SoulSync", "meshuggah")
        assert [h["message"] for h in hits] == ["meshuggah rules",
                                               "anyone have the new Meshuggah?"]
        assert [h["message"] for h in mdb.search_chat_messages("SoulSync", "bob")] == \
            ["check soulseek search"]

    def test_search_is_room_scoped_and_escapes_like(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, text="hello 100% real")])
        mdb.add_chat_messages("indie", [_m(2, text="hello from indie")])
        assert mdb.search_chat_messages("indie", "hello")[0]["message"] == "hello from indie"
        # LIKE wildcards in the query are literals, not patterns
        assert mdb.search_chat_messages("SoulSync", "100%")[0]["message"] == "hello 100% real"
        assert mdb.search_chat_messages("SoulSync", "%") != []      # literal % exists
        assert mdb.search_chat_messages("SoulSync", "zzz") == []
        assert mdb.search_chat_messages("SoulSync", "") == []


class TestSharedOverlaysSurvive:
    """A template shared into the room used to vanish on reload.

    An overlay share carries NO text on purpose - the card IS the message, the
    way a poll or a game move is. add_chat_messages required a message, so the
    whole row was skipped and the archive never held a single share. You could
    post a template, someone could reload, and it was simply gone.
    """

    def _share(self, n=1, name="Corner badge", user="alice"):
        return {"username": user, "message": "", "rich": True,
                "timestamp": "2026-07-19 10:%02d:00" % n,
                "overlay": {"n": name, "layers": 2, "assets": ["asset://aaaa.png"],
                            "d": {"version": 1, "layers": [
                                {"type": "text", "text": "4K"},
                                {"type": "image", "src": "asset://aaaa.png"}]}}}

    def test_a_textless_share_is_archived(self, mdb):
        assert mdb.add_chat_messages("SoulSync", [self._share()]) == 1

    def test_it_comes_back_as_a_card_the_reader_can_still_adopt(self, mdb):
        mdb.add_chat_messages("SoulSync", [self._share()])
        row = mdb.get_chat_messages("SoulSync")[0]
        assert row["overlay"]["n"] == "Corner badge"
        assert row["overlay"]["d"]["layers"][0]["text"] == "4K"

    def test_the_card_counts_and_asset_refs_are_rebuilt_not_trusted(self, mdb):
        """layers/assets are DERIVED from the definition on the way out, the
        same way the live path derives them, so a doctored archive row cannot
        make a card claim something its definition does not say."""
        mdb.add_chat_messages("SoulSync", [self._share()])
        row = mdb.get_chat_messages("SoulSync")[0]
        assert row["overlay"]["layers"] == 2
        assert row["overlay"]["assets"] == ["asset://aaaa.png"]

    def test_an_ordinary_message_carries_no_overlay_key(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1)])
        assert "overlay" not in mdb.get_chat_messages("SoulSync")[0]

    def test_a_textless_row_with_NO_overlay_is_still_skipped(self, mdb):
        """The empty-message guard still does its job; it just learned about
        the one kind of message that is legitimately textless."""
        assert mdb.add_chat_messages("SoulSync", [
            {"username": "alice", "message": "", "timestamp": "2026-07-19 10:01:00"}]) == 0

    def test_junk_in_the_overlay_field_does_not_take_the_row_down(self, mdb):
        for bad in ("not a dict", {"n": "no definition"}, {"d": {"layers": []}}, {}):
            n = mdb.add_chat_messages("SoulSync", [
                {"username": "alice", "message": "hi", "rich": True,
                 "timestamp": "2026-07-19 11:%02d:00" % len(str(bad)), "overlay": bad}])
            assert n == 1                      # the MESSAGE still archives
        rows = mdb.get_chat_messages("SoulSync")
        assert all("overlay" not in r for r in rows)

    def test_a_share_and_a_message_archive_together(self, mdb):
        """The share used to be dropped silently while its neighbours went in,
        so the batch count looked healthy."""
        assert mdb.add_chat_messages("SoulSync", [self._share(1), _m(2)]) == 2

    def test_a_definition_json_cannot_serialize_does_not_take_the_row_down(self, mdb):
        """add_chat_messages is handed DECODED dicts in-process, not raw JSON,
        so a value json refuses is reachable. The message must still archive.
        (The earlier junk cases never got this far - they are rejected by the
        shape check before json.dumps is ever called, which is exactly what a
        negative-check caught.)"""
        n = mdb.add_chat_messages("SoulSync", [
            {"username": "alice", "message": "hi", "rich": True,
             "timestamp": "2026-07-19 12:00:00",
             "overlay": {"n": "Bad", "d": {"version": 1, "layers": [{"x": object()}]}}}])
        assert n == 1
        row = mdb.get_chat_messages("SoulSync")[0]
        assert row["message"] == "hi"
        assert "overlay" not in row

    def test_an_enormous_definition_is_dropped_but_the_message_survives(self, mdb):
        """Bounded on the way IN as well as on the wire - the archive is not a
        way around the codec's ceiling."""
        big = {"version": 1, "layers": [{"type": "text", "text": "x" * 400} for _ in range(200)]}
        n = mdb.add_chat_messages("SoulSync", [
            {"username": "alice", "message": "hi", "rich": True,
             "timestamp": "2026-07-19 13:00:00", "overlay": {"n": "Huge", "d": big}}])
        assert n == 1
        assert "overlay" not in mdb.get_chat_messages("SoulSync")[0]

    def test_a_textless_share_with_an_unusable_definition_is_skipped_entirely(self, mdb):
        """No text and no storable card means there is nothing to show."""
        assert mdb.add_chat_messages("SoulSync", [
            {"username": "alice", "message": "", "timestamp": "2026-07-19 14:00:00",
             "overlay": {"n": "Bad", "d": {"layers": [{"x": object()}]}}}]) == 0


class TestReactionsSurvive:
    """Reactions used to die with slskd's buffer.

    They travel as empty-text carriers, which the unwrap pulls OUT of the
    message list — so the message archive never saw one. The react endpoint's
    own docstring said it: "live as long as slskd's room buffer". Restart
    slskd and every chip in the room was gone.

    Reactions cannot be un-sent (there is no remove carrier), so an additive
    store is the whole model: nothing here can bring back something a user
    took away, because taking one away was never possible.
    """

    KEY = "alice|1a2b3c4d"

    def test_a_reaction_round_trips(self, mdb):
        assert mdb.add_chat_reactions("SoulSync", {self.KEY: {"🔥": ["bob"]}}) == 1
        assert mdb.get_chat_reactions("SoulSync") == {self.KEY: {"🔥": ["bob"]}}

    def test_re_archiving_the_same_map_is_free(self, mdb):
        """The hydrate re-sends the whole map every time it runs."""
        m = {self.KEY: {"🔥": ["bob", "carol"]}}
        assert mdb.add_chat_reactions("SoulSync", m) == 2
        assert mdb.add_chat_reactions("SoulSync", m) == 0
        assert mdb.get_chat_reactions("SoulSync")[self.KEY]["🔥"] == ["bob", "carol"]

    def test_several_emoji_on_one_message(self, mdb):
        mdb.add_chat_reactions("SoulSync", {self.KEY: {"🔥": ["bob"], "👍": ["carol", "dave"]}})
        got = mdb.get_chat_reactions("SoulSync")[self.KEY]
        assert got["🔥"] == ["bob"] and got["👍"] == ["carol", "dave"]

    def test_rooms_do_not_share_reactions(self, mdb):
        mdb.add_chat_reactions("SoulSync", {self.KEY: {"🔥": ["bob"]}})
        mdb.add_chat_reactions("other", {self.KEY: {"👍": ["carol"]}})
        assert mdb.get_chat_reactions("SoulSync") == {self.KEY: {"🔥": ["bob"]}}
        assert mdb.get_chat_reactions("other") == {self.KEY: {"👍": ["carol"]}}

    def test_junk_is_stepped_over_not_fatal(self, mdb):
        n = mdb.add_chat_reactions("SoulSync", {
            "": {"🔥": ["bob"]},                 # no target
            self.KEY: {"": ["bob"],              # no emoji
                       "👍": "not a list",       # not a user list
                       "🔥": ["", "bob"]},       # one empty username
            "other|key": "not a dict",
        })
        assert n == 1
        assert mdb.get_chat_reactions("SoulSync") == {self.KEY: {"🔥": ["bob"]}}

    def test_an_empty_map_is_not_an_error(self, mdb):
        assert mdb.add_chat_reactions("SoulSync", {}) == 0
        assert mdb.add_chat_reactions("SoulSync", None) == 0
        assert mdb.get_chat_reactions("SoulSync") == {}

    def test_a_room_with_no_reactions_reads_empty(self, mdb):
        assert mdb.get_chat_reactions("never-used") == {}
