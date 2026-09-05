"""Talking to people who are NOT running SoulSync.

Every room message was enveloped, unconditionally. So the SoulSync room looked
like a shared room and was not: a vanilla Soulseek client saw `!SS1!` followed
by base64, and the person typing had no way to know. Boulder's words - "users
think they are doing that but its always enveloped".

Plain mode sends the raw text. The judgements this pins:

* Nothing rich can ride along. There is no envelope to carry a reply ref, a
  template, a file card, a channel tag, an avatar or a thread id. Those are
  REFUSED with a reason, never quietly dropped - silently sending a bare
  sentence when someone attached a template is the same class of lie this mode
  exists to fix.
* The wire bytes are the test. The whole bug was that the wire said one thing
  and the UI said another, so these read what the fake client was actually
  handed rather than trusting a 200.
* Default is unchanged. Rich is the product; plain is the exception you opt
  into, and an ordinary send must not pay for it existing.
"""

from __future__ import annotations

from core import chat_codec

from tests.test_chat_api import chat_app  # noqa: F401


def _wire(state):
    """Exactly what a vanilla Soulseek client would receive."""
    return state["client"].sent_room[-1][1]


# ── the point of the feature ─────────────────────────────────────────────────
def test_a_plain_message_goes_out_as_readable_text(chat_app):
    http, state = chat_app
    r = http.post("/api/chat/room/message", json={"message": "anyone got the FLAC?", "plain": True})
    assert r.status_code == 200 and r.get_json()["plain"] is True
    assert _wire(state) == "anyone got the FLAC?"


def test_it_carries_no_marker_at_all(chat_app):
    """The marker IS the thing other clients cannot read."""
    http, state = chat_app
    http.post("/api/chat/room/message", json={"message": "hello", "plain": True})
    wire = _wire(state)
    assert chat_codec.MARKER not in wire
    assert chat_codec.decode(wire) is None


def test_the_default_is_still_the_envelope(chat_app):
    """Rich is the product. Plain is the exception, and omitting the flag must
    not quietly change what a normal send does."""
    http, state = chat_app
    http.post("/api/chat/room/message", json={"message": "hello"})
    wire = _wire(state)
    assert wire.startswith(chat_codec.MARKER)
    assert chat_codec.decode(wire)["t"] == "hello"


def test_plain_false_is_the_envelope_too(chat_app):
    http, state = chat_app
    http.post("/api/chat/room/message", json={"message": "hello", "plain": False})
    assert _wire(state).startswith(chat_codec.MARKER)


def test_only_a_real_boolean_switches_it(chat_app):
    """A truthy string arriving from somewhere unexpected must not silently
    strip the envelope off a room's traffic."""
    http, state = chat_app
    for truthy in ("true", 1, "yes", [1]):
        http.post("/api/chat/room/message", json={"message": "hi", "plain": truthy})
        assert _wire(state).startswith(chat_codec.MARKER), truthy


# ── what plain text cannot carry ─────────────────────────────────────────────
def _refused(http, extra):
    body = {"message": "hi", "plain": True}
    body.update(extra)
    return http.post("/api/chat/room/message", json=body)


def test_an_attached_template_is_refused_not_dropped(chat_app):
    """Sending the sentence without the template would be exactly the silent
    lie this mode exists to remove."""
    http, state = chat_app
    before = len(state["client"].sent_room)
    r = _refused(http, {"overlay": {"n": "Badge", "d": {"version": 1, "layers": [{"type": "text"}]}}})
    assert r.status_code == 400
    assert "overlay template" in r.get_json()["error"]
    assert len(state["client"].sent_room) == before      # nothing reached the room


def test_a_file_card_is_refused(chat_app):
    http, state = chat_app
    before = len(state["client"].sent_room)
    r = _refused(http, {"file": {"n": "x.flac", "s": 10, "m": "audio/flac"}})
    assert r.status_code == 400 and "a file" in r.get_json()["error"]
    assert len(state["client"].sent_room) == before


def test_a_reply_is_refused(chat_app):
    http, _ = chat_app
    r = _refused(http, {"reply": {"u": "someone", "x": "earlier"}})
    assert r.status_code == 400 and "a reply" in r.get_json()["error"]


def test_an_edit_is_refused(chat_app):
    http, _ = chat_app
    r = _refused(http, {"edit": "someone|2026-09-03T10:00:00"})
    assert r.status_code == 400 and "an edit" in r.get_json()["error"]


def test_a_channel_tag_is_refused(chat_app):
    """A channel is a virtual thing that only exists inside the envelope, so a
    plain message cannot be in one - it lands in the room everybody sees."""
    http, _ = chat_app
    r = _refused(http, {"chan": "music"})
    assert r.status_code == 400 and "channel" in r.get_json()["error"]


def test_a_thread_is_refused(chat_app):
    http, _ = chat_app
    r = _refused(http, {"thread": "u|2026-09-03T10:00:00"})
    assert r.status_code == 400 and "thread" in r.get_json()["error"]


def test_the_default_channel_is_fine_because_it_is_untagged(chat_app):
    """#general is the absence of a tag, so it costs nothing."""
    http, state = chat_app
    r = http.post("/api/chat/room/message",
                  json={"message": "hi", "plain": True, "chan": "general"})
    assert r.status_code == 200
    assert _wire(state) == "hi"


def test_an_avatar_never_makes_it_onto_the_wire(chat_app):
    """The avatar rides the envelope. In plain mode there is nowhere to put it,
    and it must not resurrect the envelope behind the user's back."""
    http, state = chat_app
    r = http.post("/api/chat/room/message",
                  json={"message": "hi", "plain": True, "avatar": 3})
    assert r.status_code == 200
    assert _wire(state) == "hi"


# ── the ordinary guards still apply ──────────────────────────────────────────
def test_an_empty_plain_message_is_still_refused(chat_app):
    http, _ = chat_app
    assert http.post("/api/chat/room/message",
                     json={"message": "   ", "plain": True}).status_code == 400


def test_it_still_joins_the_room_first(chat_app):
    http, state = chat_app
    state["client"].joined = []
    http.post("/api/chat/room/message", json={"message": "hi", "plain": True})
    assert state["client"].joined == ["SoulSync"]


def test_an_unknown_room_is_still_a_404(chat_app):
    http, _ = chat_app
    r = http.post("/api/chat/room/message",
                  json={"message": "hi", "plain": True, "room": "not-a-room"})
    assert r.status_code == 404


def test_read_only_servers_still_refuse_it(chat_app):
    """Plain mode is a FORMAT choice, never a way around the admin-only gate."""
    http, state = chat_app
    state["admin"] = False
    state["config"]["soulseek.chat_admin_only"] = True
    before = len(state["client"].sent_room)
    r = http.post("/api/chat/room/message", json={"message": "hi", "plain": True})
    assert r.status_code == 403
    assert len(state["client"].sent_room) == before


# ── a SoulSync reader still sees it ──────────────────────────────────────────
def test_a_soulsync_client_renders_it_as_an_ordinary_message(chat_app):
    """It arrives with no envelope, so it is not 'rich' - which is exactly how
    every other Soulseek client's message already arrives. It must not vanish."""
    from api import chat as chat_api
    msgs, _reacts, _proto = chat_api._unwrap_room_messages(
        [{"username": "someone", "message": "anyone got the FLAC?", "timestamp": "t"}])
    assert len(msgs) == 1
    assert msgs[0]["message"] == "anyone got the FLAC?"
    assert msgs[0].get("rich") is not True
