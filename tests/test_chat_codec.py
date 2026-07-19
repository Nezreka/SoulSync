"""RichChat P1 — the !SS1! envelope codec + room encode/decode integration.

The codec's decode() faces raw REMOTE input: anything a stranger types into
the room arrives here first. Every rejection path returns None (renders as
plaintext), never raises.
"""

from __future__ import annotations

import base64
import json
import zlib

from core.chat_codec import (MARKER, MAX_ENCODED_LEN, MAX_TEXT_LEN,
                             decode, encode)


class TestRoundTrip:
    def test_basic(self):
        packed = encode("hello **room** :fire:")
        assert packed.startswith(MARKER)
        assert decode(packed) == {"v": 1, "t": "hello **room** :fire:"}

    def test_unicode_and_emoji(self):
        text = "héllo 🎵 — «rich» ¯\\_(ツ)_/¯"
        assert decode(encode(text))["t"] == text

    def test_looks_like_noise_to_other_clients(self):
        packed = encode("a perfectly ordinary message")
        # nothing human-readable survives the compression
        assert "ordinary" not in packed and "message" not in packed

    def test_wire_cap_send_side(self):
        assert encode("x" * 50) is not None
        # the cap bites on ENCODED size — repetitive text compresses to almost
        # nothing, so overflow needs incompressible input (chained hashes)
        import hashlib
        chunks, seed = [], b"ss"
        while sum(len(c) for c in chunks) < 6000:
            seed = hashlib.sha256(seed).digest()
            chunks.append(seed.hex())
        assert encode("".join(chunks)) is None     # caller surfaces 'too long'
        ok = encode("y" * 500)
        assert ok is not None and len(ok) <= MAX_ENCODED_LEN


class TestHostileInput:
    def test_plaintext_and_junk_pass_through_as_none(self):
        for x in ("hello room", "", None, 42, ["!SS1!"],
                  "!SS1!", "!SS1!not-base64!!!", "!SS1!aGVsbG8=",   # valid b64, not zlib
                  "!SS2!" + encode("x")[len(MARKER):]):             # wrong version marker
            assert decode(x) is None, repr(x)

    def test_zlib_bomb_is_bounded(self):
        bomb = MARKER + base64.b64encode(zlib.compress(b"\x00" * 50_000_000, 9)).decode()
        assert decode(bomb) is None                # bounded decompress, no balloon

    def test_wrong_shapes_rejected(self):
        def _pack(obj):
            raw = json.dumps(obj).encode()
            return MARKER + base64.b64encode(zlib.compress(raw)).decode()
        assert decode(_pack([1, 2, 3])) is None            # not a dict
        assert decode(_pack({"v": 2, "t": "x"})) is None   # future version → plaintext
        assert decode(_pack({"v": 1})) is None             # no text
        assert decode(_pack({"v": 1, "t": 5})) is None     # text not a string
        assert decode(_pack({"v": 1, "t": "x" * (MAX_TEXT_LEN + 1)})) is None

    def test_extra_keys_preserved_for_future_versions(self):
        raw = json.dumps({"v": 1, "t": "hi", "future": {"k": 1}}).encode()
        packed = MARKER + base64.b64encode(zlib.compress(raw)).decode()
        assert decode(packed)["future"] == {"k": 1}

    def test_oversized_wire_input_rejected_before_work(self):
        assert decode(MARKER + "A" * 100_000) is None


class TestApiIntegration:
    """The blueprint: room messages ride the envelope, PMs never do."""

    def _app(self):
        import api.chat as chat_api
        from flask import Flask, g
        from tests.test_chat_api import _FakeChatClient
        client = _FakeChatClient()
        state = {"client": client}
        chat_api.configure(client_getter=lambda: state["client"],
                           run_async=lambda v: v,
                           config_get=lambda k, d=None: d)
        app = Flask(__name__)

        @app.before_request
        def _p():
            g.is_admin = True
        app.register_blueprint(chat_api.create_blueprint())
        return app.test_client(), client

    def test_room_send_is_enveloped(self):
        http, client = self._app()
        assert http.post("/api/chat/room/message",
                         json={"message": "**bold** hi"}).status_code == 200
        room, wire = client.sent_room[0]
        assert wire.startswith(MARKER)
        assert decode(wire)["t"] == "**bold** hi"

    def test_room_read_unwraps_and_flags_rich(self):
        http, client = self._app()
        wire = encode("*rich* message")
        client.get_room_messages = lambda room: [
            {"username": "a", "message": wire, "timestamp": "1"},
            {"username": "b", "message": "plain from nicotine+", "timestamp": "2"},
        ]
        msgs = http.get("/api/chat/room").get_json()["messages"]
        assert msgs[0]["message"] == "*rich* message" and msgs[0]["rich"] is True
        assert msgs[1]["message"] == "plain from nicotine+" and "rich" not in msgs[1]

    def test_pms_are_never_enveloped(self):
        http, client = self._app()
        assert http.post("/api/chat/conversations/pal",
                         json={"message": "human"}).status_code == 200
        assert client.sent_pm == [("pal", "human")]        # literal plaintext


def test_autoprove_replies_stay_plaintext():
    """The ProveIt bots must receive a literal token — the responder sends
    through send_private_message directly, which the envelope never touches."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "chat_autoprove.py").read_text(
        encoding="utf-8", errors="replace")
    assert "chat_codec" not in src and "encode(" not in src
