"""Chat next-level P4 — polls, pins, room topic, tuned-in presence.

Zero new server surface: all four features are pure folds over the P1
protocol bus (reducePins / reducePoll / reduceTopic / reduceTuned in
chat-protocol.js — determinism covered in tests/static/test_chat_protocol.mjs).
These tests pin the frontend wiring contracts so a refactor can't quietly
detach a surface from the bus.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
_PROTO = (_ROOT / "webui" / "static" / "chat-protocol.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def test_reducers_exist_and_are_exported():
    for name in ("reducePins", "reducePoll", "reduceTopic", "reduceTuned"):
        assert name in _PROTO
        assert (name + ": " + name) in _PROTO       # exported on window.ChatProtocol


def test_pin_wiring():
    # pin action on room messages + the board renders from the bus
    assert "data-chat-pin-user" in _JS and "data-chat-pin-ts" in _JS
    assert "reducePins(_roomEvents())" in _JS
    assert "data-chat-pins-toggle" in _JS
    assert "pin.del" in _JS
    assert "data-chat-pinbar" in _HTML
    assert ".chat-pinbar" in _CSS


def test_poll_wiring():
    assert "reducePoll(_roomEvents())" in _JS
    assert "'poll.start'" in _JS and "'poll.vote'" in _JS and "'poll.end'" in _JS
    # only the starter sees End poll (reducer enforces it too — belt & braces)
    assert "poll.by === state.selfName" in _JS
    assert "data-chat-poll-pop" in _HTML and "data-chat-poll-btn" in _HTML
    assert ".chat-poll-bar" in _CSS


def test_topic_wiring():
    assert "reduceTopic(_roomEvents())" in _JS
    assert "'topic.set'" in _JS
    assert "data-chat-topic-input" in _JS
    # the open topic input must survive the 4s poll's renderHead
    assert "if (state.topicEditing) return;" in _JS


def test_tuned_presence_wiring():
    assert "reduceTuned(_roomEvents())" in _JS
    assert "'jbx.tune'" in _JS
    assert "chat-user-tuned" in _JS and ".chat-user-tuned" in _CSS


def test_bus_surfaces_paint_together():
    """One ingest → every reduced surface repaints (a fresh event may touch
    any of them; painting piecemeal is how surfaces drift stale)."""
    assert "renderBusUI()" in _JS
    ingest = _JS[_JS.index("function _ingestProtocol"):_JS.index("function sendProtocol")]
    assert "renderBusUI()" in ingest
    bus_ui = _JS[_JS.index("function renderBusUI"):_JS.index("function renderPinbar")]
    for call in ("renderJukebox()", "renderPinbar()", "renderPoll()", "renderHead()"):
        assert call in bus_ui


def test_polls_are_room_only():
    """Bus events mean nothing in a PM — the poll button must hide there."""
    composer = _JS[_JS.index("function renderComposer"):_JS.index("var _FMT")]
    assert "data-chat-poll-btn" in composer


def test_p4_review_catches_pinned():
    """P4 adversarial review — three catches must stay fixed:
    (1) a socket protocol event during history-search must not rebuild the
    head (it would clobber the search input mid-typing); (2) the room switch
    tunes out BEFORE state.room flips so the jbx.tune off event reaches the
    OLD room — otherwise you show as listening there forever; (3) the tuned
    map reduces once per user-list render, not once per user."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    bus_ui = js[js.index("function renderBusUI"):js.index("function renderPinbar")]
    assert "if (!state.searchMode) renderHead();" in bus_ui
    open_room = js[js.index("function openRoom"):js.index("function loadRooms")]
    assert open_room.index("_jbxTuneOut()") < open_room.index("state.room = nextRoom")
    users_list = js[js.index("function renderUsersList"):js.index("function renderSide")]
    # Assert the INTENT, not the exact call: the room's events are folded ONCE
    # per render (they're now hoisted and shared with the now-playing reducer),
    # never per user.
    assert users_list.count("reduceTuned(") == 1
    assert users_list.count("_roomEvents()") == 1
    user_btn = js[js.index("function _userBtn"):js.index("function renderUsers(")]
    assert "reduceTuned" not in user_btn
