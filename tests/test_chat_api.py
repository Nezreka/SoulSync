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
        assert res == {"configured": True, "room": "SoulSync", "can_send": True}

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
    """chatux P4 — /api/chat/gifs (Tenor, key server-side only)."""

    def test_no_key_is_a_helpful_503(self, chat_app):
        http, state = chat_app
        r = http.get("/api/chat/gifs?q=cat")
        assert r.status_code == 503 and "tenor" in r.get_json()["error"].lower()

    def test_search_maps_tenor_shape(self, chat_app, monkeypatch):
        http, state = chat_app
        state["config"]["soulseek.chat_tenor_key"] = "k"
        seen = {}

        def fake_fetch(url, params):
            seen.update(params)
            assert "tenor.googleapis.com" in url
            return {"results": [
                {"media_formats": {"gif": {"url": "https://media.tenor.com/full.gif"},
                                   "tinygif": {"url": "https://media.tenor.com/tiny.gif"}}},
                {"media_formats": {}},                       # no gif → dropped
            ]}
        monkeypatch.setattr(chat_api, "_gif_fetch", fake_fetch)
        res = http.get("/api/chat/gifs?q=excited dance").get_json()
        assert res["gifs"] == [{"url": "https://media.tenor.com/full.gif",
                                "preview": "https://media.tenor.com/tiny.gif"}]
        assert seen["q"] == "excited dance" and seen["key"] == "k"

    def test_empty_query_400_and_upstream_failure_502(self, chat_app, monkeypatch):
        http, state = chat_app
        state["config"]["soulseek.chat_tenor_key"] = "k"
        assert http.get("/api/chat/gifs?q=").status_code == 400

        def boom(url, params):
            raise RuntimeError("tenor down")
        monkeypatch.setattr(chat_api, "_gif_fetch", boom)
        assert http.get("/api/chat/gifs?q=x").status_code == 502
