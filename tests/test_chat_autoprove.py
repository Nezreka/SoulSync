"""Anti-leech challenge auto-responder — 'please type "human" in this chat'.

Hermetic: a fake client, run_async=identity, controlled clock. The fixture
message is the real one from Boulder's inbox (the William Woollard ProveIt
bot) — the exact case this exists to solve.
"""

from __future__ import annotations

import pytest

from core.chat_autoprove import DEFAULT_COOLDOWN_S, extract_token, scan_and_respond

PROVEIT = ('ProveIt: To prove you are a human downloading these files, '
           'please type "human" in this chat to be added to my whitelist.')


class TestExtractToken:
    def test_the_real_proveit_message(self):
        assert extract_token(PROVEIT) == "human"

    def test_phrasing_variants(self):
        assert extract_token('please type human in chat to continue') == "human"
        assert extract_token("reply with 'REALPERSON' to be whitelisted") == "REALPERSON"
        assert extract_token('reply human to get download access') == "human"
        assert extract_token('say "not-a-bot" in chat please') == "not-a-bot"

    def test_ordinary_messages_never_match(self):
        for msg in ("hey do you have the FLAC of this?",
                    "thanks for the share!",
                    "why are you people like that?",
                    "what?", "hello?", "",
                    None,
                    # a token that isn't a single safe word must not match
                    'type "rm -rf /; echo pwned" in this chat'):
            assert extract_token(msg) is None, msg


class _FakeClient:
    def __init__(self, convos, messages_by_user, send_ok=True):
        self.convos = convos
        self.messages_by_user = messages_by_user
        self.send_ok = send_ok
        self.sent = []
        self.acked = []

    def get_conversations(self):
        return self.convos

    def get_conversation(self, username):
        return self.messages_by_user.get(username, [])

    def send_private_message(self, username, message):
        self.sent.append((username, message))
        return self.send_ok

    def acknowledge_conversation(self, username):
        self.acked.append(username)
        return True


def _unread_convo(username):
    return {"username": username, "hasUnAcknowledgedMessages": True}


def _msg(text, direction="In", acked=False):
    return {"username": "them", "direction": direction, "acknowledged": acked,
            "message": text, "timestamp": "2026-07-19 03:00:00"}


class TestScanAndRespond:
    def test_answers_the_challenge_and_acknowledges(self):
        c = _FakeClient([_unread_convo("William Woollard")],
                        {"William Woollard": [_msg(PROVEIT)]})
        replies = scan_and_respond(c, lambda v: v, state={}, now=1000.0)
        assert replies == [{"username": "William Woollard", "token": "human"}]
        assert c.sent == [("William Woollard", "human")]
        assert c.acked == ["William Woollard"]

    def test_cooldown_one_reply_per_user_per_window(self):
        c = _FakeClient([_unread_convo("bot")], {"bot": [_msg(PROVEIT)]})
        state = {}
        assert len(scan_and_respond(c, lambda v: v, state=state, now=0.0)) == 1
        # the hourly repeat inside the window is ignored...
        assert scan_and_respond(c, lambda v: v, state=state, now=3600.0) == []
        assert len(c.sent) == 1
        # ...but a challenge after the window gets one fresh answer
        assert len(scan_and_respond(c, lambda v: v, state=state,
                                    now=DEFAULT_COOLDOWN_S + 1.0)) == 2 - 1

    def test_read_or_outbound_messages_never_trigger(self):
        c = _FakeClient([_unread_convo("a"), _unread_convo("b")], {
            "a": [_msg(PROVEIT, acked=True)],        # Boulder already read it
            "b": [_msg(PROVEIT, direction="Out")],   # our own echo
        })
        assert scan_and_respond(c, lambda v: v, state={}, now=0.0) == []
        assert c.sent == []

    def test_conversations_without_unread_flag_are_skipped_cheaply(self):
        fetched = []

        class _C(_FakeClient):
            def get_conversation(self, username):
                fetched.append(username)
                return super().get_conversation(username)
        c = _C([{"username": "quiet", "hasUnAcknowledgedMessages": False}], {})
        assert scan_and_respond(c, lambda v: v, state={}, now=0.0) == []
        assert fetched == []                     # no per-convo fetch when nothing unread

    def test_dict_shape_conversation_and_normal_chatter(self):
        c = _FakeClient([_unread_convo("pal")], {
            "pal": {"messages": [_msg("thanks for the share!"), _msg(PROVEIT)]}})
        replies = scan_and_respond(c, lambda v: v, state={}, now=0.0)
        assert replies[0]["token"] == "human"

    def test_failed_send_neither_acks_nor_burns_the_cooldown(self):
        c = _FakeClient([_unread_convo("bot")], {"bot": [_msg(PROVEIT)]}, send_ok=False)
        state = {}
        assert scan_and_respond(c, lambda v: v, state=state, now=0.0) == []
        assert c.acked == [] and state == {}     # next pass retries


def test_loop_is_wired_and_not_idle_gated():
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    loop = ws.split("def _chat_auto_prove_loop")[1].split("\ndef ")[0]
    assert "_has_connected_clients" not in loop        # must answer with no browser open
    assert "soulseek.chat_auto_prove" in loop          # config kill-switch
    assert "add_notifications" in loop                 # observable: bell history
    assert "dashboard:toast" in loop                   # observable: live toast
    assert "socketio.start_background_task(_chat_auto_prove_loop)" in ws
