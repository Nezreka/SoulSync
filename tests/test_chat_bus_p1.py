"""Chat next-level P1 — the protocol bus foundation + user card overhaul.

The room is plain text; SoulSync envelopes can carry a protocol object under
'p' (machine coordination: beacons, votes, pins). P1 pins the whole path:
codec validation (hostile input), unwrap interception (protocol carriers are
never visible and never archived), the send endpoint (same gate as talking),
the enriched user card (download history + private notes), and the frontend
flip (assume-SoulSync presence).

Hermetic throughout — fake slskd client, temp DB, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

import api.chat as chat_api
from core import chat_codec

_ROOT = Path(__file__).resolve().parent.parent


# ── codec: protocol_of ──────────────────────────────────────────────────────

def test_protocol_roundtrip():
    env = chat_codec.encode("", {"p": {"k": "jbx.vote", "o": "abc123"}})
    dec = chat_codec.decode(env)
    assert chat_codec.protocol_of(dec) == {"k": "jbx.vote", "o": "abc123"}


@pytest.mark.parametrize("bad", [
    None, {}, {"p": None}, {"p": []}, {"p": {"k": 42}},
    {"p": {"k": "UPPER"}}, {"p": {"k": "a" * 30}},
    {"p": {"k": "x", "s": "y" * 600}},
    {"p": {"k": "x.y", "blob": {"deep": {"deeper": 1}}}},
    {"p": {"k": "x", "huge": 1e20}},
])
def test_protocol_rejects_hostile_shapes(bad):
    assert chat_codec.protocol_of(bad) is None


def test_protocol_field_count_bomb():
    p = {"k": "x"}
    p.update({f"f{i}": i for i in range(20)})
    assert chat_codec.protocol_of({"p": p}) is None


# ── unwrap: carriers intercepted, never visible ─────────────────────────────

def _msg(text, user="peer", ts="2026-07-24T10:00:00Z"):
    return {"username": user, "message": text, "timestamp": ts}


def test_unwrap_pulls_protocol_out_of_the_visible_list():
    proto_env = chat_codec.encode("", {"p": {"k": "hello"}})
    rich_env = chat_codec.encode("hey there")
    msgs = [_msg("plain text", "vanilla_user"),
            _msg(proto_env, "app_user"),
            _msg(rich_env, "app_user")]
    out, reactions, protocol = chat_api._unwrap_room_messages(msgs)
    assert len(out) == 2                              # protocol carrier gone
    assert all(chat_codec.MARKER not in m["message"] for m in out)
    assert protocol == [{"username": "app_user",
                         "timestamp": "2026-07-24T10:00:00Z",
                         "p": {"k": "hello"}}]
    assert reactions == {}


def test_reaction_carriers_still_work_alongside():
    react = chat_codec.encode("", {"re": {"k": "peer|deadbeef", "e": "🔥"}})
    out, reactions, protocol = chat_api._unwrap_room_messages([_msg(react, "fan")])
    assert out == [] and protocol == []
    assert "peer|deadbeef" in reactions


# ── endpoints (fake client harness, mirrors test_chat_api.py) ───────────────

class _FakeClient:
    base_url = "http://slskd"

    def __init__(self):
        self.joined = ["SoulSync"]
        self.sent_room = []

    def get_joined_rooms(self):
        return list(self.joined)

    def join_room(self, room):
        self.joined.append(room)
        return True

    def get_room_messages(self, room):
        return [
            {"username": "vanilla_user", "message": "yo", "timestamp": "t1"},
            {"username": "app_user",
             "message": chat_codec.encode("", {"p": {"k": "hello"}}), "timestamp": "t2"},
        ]

    def get_room_users(self, room):
        return [{"username": "vanilla_user"}, {"username": "app_user"}]

    def send_room_message(self, room, message):
        self.sent_room.append((room, message))
        return True

    def get_conversations(self):
        return []


@pytest.fixture()
def bus_app():
    client = _FakeClient()
    state = {"client": client, "admin": True, "config": {}}
    chat_api.configure(
        client_getter=lambda: state["client"],
        run_async=lambda v: v,
        config_get=lambda key, default=None: state["config"].get(key, default),
        config_set=lambda key, value: state["config"].__setitem__(key, value),
    )
    app = Flask(__name__)

    @app.before_request
    def _fake_profile():
        g.is_admin = state["admin"]

    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client(), state, client
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


def test_room_response_carries_protocol_feed(bus_app):
    http, state, client = bus_app
    body = http.get("/api/chat/room").get_json()
    assert body["joined"] is True
    assert body["protocol"] == [{"username": "app_user", "timestamp": "t2",
                                 "p": {"k": "hello"}}]
    # the carrier never appears as a visible message
    assert all("!SS1!" not in m["message"] for m in body["messages"])


def test_protocol_send_encodes_a_carrier(bus_app):
    http, state, client = bus_app
    r = http.post("/api/chat/room/protocol",
                  json={"room": "SoulSync", "p": {"k": "jbx.vote", "o": "x1"}})
    assert r.get_json()["ok"] is True
    room, wire = client.sent_room[-1]
    dec = chat_codec.decode(wire)
    assert chat_codec.protocol_of(dec) == {"k": "jbx.vote", "o": "x1"}
    assert dec["t"] == ""                             # empty-text carrier


def test_protocol_send_respects_the_send_gate(bus_app):
    http, state, client = bus_app
    state["admin"] = False                            # default: only admin talks
    r = http.post("/api/chat/room/protocol",
                  json={"room": "SoulSync", "p": {"k": "hello"}})
    assert r.status_code == 403
    assert not any("hello" in w for _, w in client.sent_room)


def test_protocol_send_rejects_garbage(bus_app):
    http, state, client = bus_app
    r = http.post("/api/chat/room/protocol",
                  json={"room": "SoulSync", "p": {"k": "NOPE!"}})
    assert r.status_code == 400


# ── user card: history + notes (temp DB) ────────────────────────────────────

def test_user_download_stats_and_notes(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        for i, status in enumerate(["completed", "completed", "failed"]):
            conn.execute(
                "INSERT INTO track_downloads (source_service, source_username, "
                "source_filename, source_size, status) VALUES "
                "('soulseek', 'goodpeer', ?, 1000, ?)", (f"f{i}.flac", status))
        conn.commit()

    stats = db.get_user_download_stats("goodpeer")
    assert stats["downloads"] == 3 and stats["completed"] == 2
    assert stats["success_rate"] == 66.7
    assert stats["total_bytes"] == 2000               # completed only
    assert db.get_user_download_stats("stranger")["downloads"] == 0

    assert db.set_chat_user_note("goodpeer", "  great jazz rips  ")
    assert db.get_chat_user_note("goodpeer") == "great jazz rips"
    assert db.set_chat_user_note("goodpeer", "")      # empty clears
    assert db.get_chat_user_note("goodpeer") == ""


# ── frontend pins ───────────────────────────────────────────────────────────

def test_frontend_flip_and_bus_wiring():
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    assert "_userClassification" in js
    assert "cls[n] !== 'vanilla'" in js               # assumed users bucket as SoulSync
    assert "Other clients" in js                      # the renamed vanilla bucket
    assert "_ingestProtocol(res.body.protocol)" in js
    assert "_sendJoinBeacon()" in js
    assert "onRoomProtocol: onRoomProtocol" in js
    assert "data-chat-card-note" in js                # note editor on the card

    core = (_ROOT / "webui" / "static" / "core.js").read_text(encoding="utf-8")
    assert "chat:room_protocol" in core

    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "chat-protocol.js" in html
    assert html.index("chat-protocol.js") < html.index("filename='chat.js'") \
        or html.index("chat-protocol.js") < html.index('chat.js')


def test_piggybacked_text_still_renders():
    """REVIEW CATCH: a message with BOTH text and 'p' must render its text
    (only pure empty-text carriers vanish) — otherwise any client can vanish
    its own text from SoulSync views, and piggybacked state (now-playing on a
    real message) would eat the message."""
    env = chat_codec.encode("check this song out", {"p": {"k": "np", "t2": "Song"}})
    out, _reactions, protocol = chat_api._unwrap_room_messages([_msg(env, "dj")])
    assert len(out) == 1 and out[0]["message"] == "check this song out"
    assert protocol and protocol[0]["p"]["k"] == "np"
