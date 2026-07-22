"""Soulseek chat P1 — slskd client wrappers + the /api/chat blueprint.

Hermetic: the SoulseekClient's _make_request is faked (no network, no slskd),
and the blueprint runs on a bare Flask app with injected fakes — never the
real web_server, config, or orchestrator.
"""

from __future__ import annotations

import asyncio

import pytest
from flask import Flask, g

import api.chat as chat_api
from core.soulseek_client import SoulseekClient


# ── client wrappers ──────────────────────────────────────────────────────────

class _RecordingClient(SoulseekClient):
    """Real class, faked transport: records every _make_request call."""

    def __init__(self, responses=None):
        # Skip SoulseekClient.__init__ entirely (it reads real config).
        self.base_url = "http://slskd"
        self.api_key = "k"
        self.calls = []
        self._responses = responses or {}

    async def _make_request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        return self._responses.get((method, endpoint), {})


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestClientWrappers:
    def test_room_endpoints_and_quoting(self):
        c = _RecordingClient({("GET", "rooms/joined"): ["soulsync", "other"]})
        assert _run(c.get_joined_rooms()) == ["soulsync", "other"]
        _run(c.join_room("soulsync"))
        _run(c.get_room_messages("room with spaces"))
        _run(c.send_room_message("soulsync", "hi all"))
        _run(c.leave_room("a/b"))
        methods = [(m, e) for m, e, _ in c.calls]
        assert ("POST", "rooms/joined") in methods
        # names URL-quoted — spaces and slashes can't break the path
        assert ("GET", "rooms/joined/room%20with%20spaces/messages") in methods
        assert ("DELETE", "rooms/joined/a%2Fb") in methods
        # slskd wants a JSON-encoded STRING body for join + send
        join_kwargs = [k for m, e, k in c.calls if (m, e) == ("POST", "rooms/joined")][0]
        assert join_kwargs == {"json": "soulsync"}
        send_kwargs = [k for m, e, k in c.calls
                       if e == "rooms/joined/soulsync/messages"][0]
        assert send_kwargs == {"json": "hi all"}

    def test_conversation_endpoints(self):
        c = _RecordingClient({("GET", "conversations"): [{"username": "u"}]})
        assert _run(c.get_conversations()) == [{"username": "u"}]
        _run(c.send_private_message("some user", "yo"))
        _run(c.acknowledge_conversation("some user"))
        methods = [(m, e) for m, e, _ in c.calls]
        assert ("POST", "conversations/some%20user") in methods
        assert ("PUT", "conversations/some%20user") in methods

    def test_unreachable_slskd_degrades_to_empty(self):
        c = _RecordingClient()

        async def _none(method, endpoint, **kw):
            return None
        c._make_request = _none
        assert _run(c.get_joined_rooms()) == []
        assert _run(c.get_room_messages("r")) == []
        assert _run(c.get_conversations()) == []
        assert _run(c.join_room("r")) is False


# ── blueprint ────────────────────────────────────────────────────────────────

class _FakeChatClient:
    """Sync stand-in — paired with run_async=identity in configure()."""

    base_url = "http://slskd"

    def __init__(self):
        self.joined = []
        self.sent_room = []
        self.sent_pm = []
        self.acked = []
        self.conversation_shape = {"messages": [{"username": "u", "message": "hi"}]}

    def get_joined_rooms(self):
        return list(self.joined)

    def join_room(self, room):
        self.joined.append(room)
        return True

    def get_room_messages(self, room):
        return [{"username": "u", "message": "hello", "roomName": room}]

    def get_room_users(self, room):
        return [{"username": "u"}]

    def send_room_message(self, room, message):
        self.sent_room.append((room, message))
        return True

    def get_conversations(self):
        return [{"username": "pal", "hasUnAcknowledgedMessages": True}]

    def get_conversation(self, username):
        return self.conversation_shape

    def send_private_message(self, username, message):
        self.sent_pm.append((username, message))
        return True

    def acknowledge_conversation(self, username):
        self.acked.append(username)
        return True


@pytest.fixture()
def chat_app():
    client = _FakeChatClient()
    state = {"client": client, "admin": True, "config": {}}
    chat_api.configure(
        client_getter=lambda: state["client"],
        run_async=lambda v: v,
        config_get=lambda key, default=None: state["config"].get(key, default),
    )
    app = Flask(__name__)

    @app.before_request
    def _fake_profile():
        g.is_admin = state["admin"]
    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client(), state
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


class TestBlueprint:
    def test_status(self, chat_app):
        http, state = chat_app
        res = http.get("/api/chat/status").get_json()
        assert res["configured"] is True and res["room"] == "SoulSync"
        assert res["can_send"] is True and res["is_admin"] is True
        assert "username" in res    # our slskd name for @mention highlighting

    def test_room_hydrate_auto_joins_once(self, chat_app):
        http, state = chat_app
        res = http.get("/api/chat/room").get_json()
        assert state["client"].joined == ["SoulSync"]      # was absent → joined
        assert res["room"] == "SoulSync"
        assert res["messages"][0]["message"] == "hello"
        assert res["users"] == [{"username": "u"}]
        # second hydrate: already joined → no second join call
        http.get("/api/chat/room")
        assert state["client"].joined == ["SoulSync"]

    def test_room_respects_configured_name(self, chat_app):
        http, state = chat_app
        state["config"]["soulseek.chat_room"] = "my room"
        res = http.get("/api/chat/room").get_json()
        assert res["room"] == "my room" and state["client"].joined == ["my room"]

    def test_send_requires_admin_unless_opted_in(self, chat_app):
        http, state = chat_app
        state["admin"] = False
        r = http.post("/api/chat/room/message", json={"message": "hi"})
        assert r.status_code == 403
        assert state["client"].sent_room == []
        # the admin can opt members in — room sends ride the !SS1! envelope
        # (rich-format wire; see test_chat_codec for the codec contract)
        state["config"]["soulseek.chat_member_send"] = True
        r = http.post("/api/chat/room/message", json={"message": "hi"})
        assert r.status_code == 200
        from core.chat_codec import decode
        room, wire = state["client"].sent_room[0]
        assert room == "SoulSync" and decode(wire)["t"] == "hi"

    def test_empty_and_oversize_messages(self, chat_app):
        http, state = chat_app
        assert http.post("/api/chat/room/message", json={"message": "  "}).status_code == 400
        http.post("/api/chat/room/message", json={"message": "x" * 5000})
        from core.chat_codec import decode
        assert len(decode(state["client"].sent_room[0][1])["t"]) == 1000   # capped, not rejected

    def test_conversation_read_acks_and_tolerates_both_shapes(self, chat_app):
        http, state = chat_app
        res = http.get("/api/chat/conversations/pal").get_json()
        assert res["messages"][0]["message"] == "hi"
        assert state["client"].acked == ["pal"]               # read = acknowledged
        state["client"].conversation_shape = [{"username": "pal", "message": "raw list"}]
        res = http.get("/api/chat/conversations/pal").get_json()
        assert res["messages"][0]["message"] == "raw list"

    def test_pm_send(self, chat_app):
        http, state = chat_app
        r = http.post("/api/chat/conversations/some pal", json={"message": "yo"})
        assert r.status_code == 200
        assert state["client"].sent_pm == [("some pal", "yo")]

    def test_unconfigured_slskd_is_503_not_crash(self, chat_app):
        http, state = chat_app
        state["client"] = None
        for path in ("/api/chat/room", "/api/chat/conversations"):
            assert http.get(path).status_code == 503
        assert http.get("/api/chat/status").get_json()["configured"] is False


class TestGifProxy:
    """chatux P4 — /api/chat/gifs (GIPHY; Tenor's API died June 2026)."""

    def test_no_key_is_a_helpful_503(self, chat_app):
        http, state = chat_app
        r = http.get("/api/chat/gifs?q=cat")
        assert r.status_code == 503 and "giphy" in r.get_json()["error"].lower()

    def test_search_maps_tenor_shape(self, chat_app, monkeypatch):
        http, state = chat_app
        state["config"]["soulseek.chat_giphy_key"] = "k"
        seen = {}

        def fake_fetch(url, params):
            seen.update(params)
            assert "api.giphy.com" in url
            return {"data": [
                {"images": {"original": {"url": "https://media2.giphy.com/full.gif"},
                            "fixed_width_small": {"url": "https://media2.giphy.com/tiny.gif"}}},
                {"images": {}},                              # no gif → dropped
            ]}
        monkeypatch.setattr(chat_api, "_gif_fetch", fake_fetch)
        res = http.get("/api/chat/gifs?q=excited dance").get_json()
        assert res["gifs"] == [{"url": "https://media2.giphy.com/full.gif",
                                "preview": "https://media2.giphy.com/tiny.gif"}]
        assert seen["q"] == "excited dance" and seen["api_key"] == "k"

    def test_empty_query_400_and_upstream_failure_502(self, chat_app, monkeypatch):
        http, state = chat_app
        state["config"]["soulseek.chat_giphy_key"] = "k"
        assert http.get("/api/chat/gifs?q=").status_code == 400

        def boom(url, params):
            raise RuntimeError("tenor down")
        monkeypatch.setattr(chat_api, "_gif_fetch", boom)
        assert http.get("/api/chat/gifs?q=x").status_code == 502


class TestChatSettings:
    """The cog modal backend — admin-only, key never echoed."""

    def _wire_set(self, state):
        chat_api.configure(
            client_getter=lambda: state["client"],
            run_async=lambda v: v,
            config_get=lambda key, default=None: state["config"].get(key, default),
            config_set=lambda key, value: state["config"].__setitem__(key, value),
        )

    def test_get_never_echoes_the_key(self, chat_app):
        http, state = chat_app
        state["config"]["soulseek.chat_giphy_key"] = "SECRET"
        res = http.get("/api/chat/settings").get_json()
        assert res["giphy_key_set"] is True
        assert "SECRET" not in str(res)
        assert res["room"] == "SoulSync" and res["auto_join"] is True

    def test_non_admin_locked_out(self, chat_app):
        http, state = chat_app
        state["admin"] = False
        assert http.get("/api/chat/settings").status_code == 403
        assert http.post("/api/chat/settings", json={"room": "x"}).status_code == 403

    def test_save_updates_and_blank_key_keeps_current(self, chat_app):
        http, state = chat_app
        self._wire_set(state)
        state["config"]["soulseek.chat_giphy_key"] = "KEEP"
        res = http.post("/api/chat/settings", json={
            "room": "MyRoom", "member_send": True, "auto_join": False,
            "auto_prove": False}).get_json()
        assert res["room"] == "MyRoom" and res["member_send"] is True
        assert res["auto_join"] is False and res["auto_prove"] is False
        # key untouched (field absent from the payload = admin didn't type one)
        assert state["config"]["soulseek.chat_giphy_key"] == "KEEP"
        # explicit empty clears it
        http.post("/api/chat/settings", json={"giphy_key": ""})
        assert state["config"]["soulseek.chat_giphy_key"] == ""

    def test_room_rename_leaves_the_old_room(self, chat_app):
        http, state = chat_app
        self._wire_set(state)
        left = []
        state["client"].leave_room = lambda room: left.append(room) or True
        http.post("/api/chat/settings", json={"room": "NewRoom"})
        assert left == ["SoulSync"]     # walked out of the old room, not stuck in both

    def test_status_exposes_is_admin_for_the_cog(self, chat_app):
        http, state = chat_app
        assert http.get("/api/chat/status").get_json()["is_admin"] is True
        state["admin"] = False
        assert http.get("/api/chat/status").get_json()["is_admin"] is False

    def test_auto_join_off_leaves_the_room_immediately(self, chat_app):
        http, state = chat_app
        self._wire_set(state)
        left = []
        state["client"].leave_room = lambda room: left.append(room) or True
        # true -> false: walk out now, not at slskd's next restart
        http.post("/api/chat/settings", json={"auto_join": False})
        assert left == ["SoulSync"]
        # false -> false: nothing to leave again
        http.post("/api/chat/settings", json={"auto_join": False})
        assert left == ["SoulSync"]
        # re-enabling doesn't leave anything
        http.post("/api/chat/settings", json={"auto_join": True})
        assert left == ["SoulSync"]

    def test_push_loop_rebaselines_on_room_change(self):
        from pathlib import Path
        ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(
            encoding="utf-8", errors="replace")
        loop = ws.split("def _emit_chat_push_loop")[1].split("\ndef ")[0]
        # a renamed room must never replay its history as 'new' badge spam
        assert "room != _chat_push_state['room']" in loop
        assert "_chat_push_state['room_key'] = None" in loop


class TestRepliesAndMentions:
    """chatbic P3 — reply refs through the envelope, username in status."""

    def test_send_carries_a_validated_reply(self, chat_app):
        http, state = chat_app
        from core.chat_codec import decode
        http.post("/api/chat/room/message",
                  json={"message": "agreed!", "reply": {"u": "alice", "x": "original words"}})
        payload = decode(state["client"].sent_room[0][1])
        assert payload["t"] == "agreed!"
        assert payload["r"] == {"u": "alice", "x": "original words"}
        # hostile/malformed reply shapes are dropped, message still sends
        http.post("/api/chat/room/message",
                  json={"message": "hi", "reply": {"x": "no user"}})
        assert "r" not in decode(state["client"].sent_room[1][1])
        http.post("/api/chat/room/message",
                  json={"message": "hi", "reply": ["not", "a", "dict"]})
        assert "r" not in decode(state["client"].sent_room[2][1])

    def test_room_read_surfaces_the_reply(self, chat_app):
        http, state = chat_app
        from core.chat_codec import encode
        wire = encode("yes", {"r": {"u": "bob", "x": "should we?"}})
        state["client"].get_room_messages = lambda room: [
            {"username": "a", "message": wire, "timestamp": "1"}]
        msgs = http.get("/api/chat/room").get_json()["messages"]
        assert msgs[0]["reply"] == {"u": "bob", "x": "should we?"}

    def test_reply_caps_hostile_lengths(self):
        from core.chat_codec import reply_of
        r = reply_of({"r": {"u": "u" * 500, "x": "x" * 5000}})
        assert len(r["u"]) == 64 and len(r["x"]) == 140
        assert reply_of({"r": {"u": ""}}) is None
        assert reply_of({}) is None

    def test_status_includes_our_username(self, chat_app):
        http, state = chat_app
        import api.chat as mod
        mod._SELF.update(name="", at=0.0)
        state["client"].get_session_info = lambda: {"username": "BoulderBadgeDad"}
        assert http.get("/api/chat/status").get_json()["username"] == "BoulderBadgeDad"


class TestReactions:
    """chatbic P4 — envelope reactions + the user card endpoint."""

    def test_react_sends_an_empty_text_envelope(self, chat_app):
        http, state = chat_app
        from core.chat_codec import decode, react_key
        r = http.post("/api/chat/room/react",
                      json={"target_user": "alice", "target_text": "great song", "e": "🔥"})
        assert r.status_code == 200
        payload = decode(state["client"].sent_room[0][1])
        assert payload["t"] == "" and payload["re"]["e"] == "🔥"
        assert payload["re"]["k"] == react_key("alice", "great song")

    def test_hostile_reactions_rejected(self, chat_app):
        http, state = chat_app
        for e in ("<script>", "x" * 50, "", "a&b"):
            assert http.post("/api/chat/room/react",
                             json={"target_user": "a", "target_text": "t", "e": e}).status_code == 400
        assert http.post("/api/chat/room/react",
                         json={"target_text": "t", "e": "🔥"}).status_code == 400   # no target user
        assert state["client"].sent_room == []

    def test_room_read_aggregates_and_hides_carriers(self, chat_app):
        http, state = chat_app
        from core.chat_codec import encode, react_key
        target = {"username": "alice", "timestamp": "2026-07-19 10:00:00",
                  "message": encode("great song")}
        k = react_key("alice", "great song")
        state["client"].get_room_messages = lambda room: [
            target,
            {"username": "bob", "timestamp": "2026-07-19 10:01:00",
             "message": encode("", {"re": {"k": k, "e": "🔥"}})},
            {"username": "carol", "timestamp": "2026-07-19 10:02:00",
             "message": encode("", {"re": {"k": k, "e": "🔥"}})},
        ]
        msgs = http.get("/api/chat/room").get_json()["messages"]
        assert len(msgs) == 1                                # carriers never render
        assert msgs[0]["reactions"] == [{"e": "🔥", "n": 2, "users": ["bob", "carol"]}]

    def test_user_card_endpoint_is_best_effort(self, chat_app):
        http, state = chat_app
        state["client"].get_user_status = lambda u: {"isOnline": True}
        state["client"].get_user_info = lambda u: {"uploadSlots": 3, "picture": b"blob",
                                                   "nested": {"x": 1}, "description": "hi"}
        res = http.get("/api/chat/user/some pal").get_json()
        assert res["status"] == {"isOnline": True}
        assert res["info"] == {"uploadSlots": 3, "description": "hi"}   # primitives only


# ── auto-join OFF must mean OUT (popwaffle9000) ──────────────────────────────
# The room poll used to _ensure_joined unconditionally, so the page's 4s poll
# re-joined within seconds of the settings cog walking you out — "uncheck
# auto-join to leave" didn't work while the chat page was open.


def test_room_poll_respects_auto_join_off(chat_app):
    http, state = chat_app
    state["config"]["soulseek.chat_auto_join"] = False
    res = http.get("/api/chat/room")
    body = res.get_json()
    assert res.status_code == 200
    assert body["joined"] is False
    assert body["messages"] == [] and body["users"] == []
    assert state["client"].joined == []          # the poll did NOT join the room


def test_room_poll_joins_again_after_opt_in(chat_app):
    http, state = chat_app
    state["config"]["soulseek.chat_auto_join"] = False
    http.get("/api/chat/room")
    assert state["client"].joined == []
    state["config"]["soulseek.chat_auto_join"] = True     # the Join gate flips this
    body = http.get("/api/chat/room").get_json()
    assert body.get("joined", True) is True
    assert state["client"].joined == ["SoulSync"]


def test_join_gate_wired_in_frontend():
    from pathlib import Path
    js = Path("webui/static/chat.js").read_text(encoding="utf-8")
    assert "res.body.joined === false" in js
    assert "renderJoinGate" in js
    assert "auto_join: true" in js               # the gate's Join button opts back in
    assert "form.hidden = false" in js           # composer restored after rejoin
