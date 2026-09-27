"""Chat badges + history retention — envelope tag, archive, settings, prune.

Badges: users set a flair badge in the chat settings modal; it rides the
!SS1! envelope ('bg') like the avatar does, and every client renders it next
to the sender's name. Staff-impersonation words are refused at every layer.

Retention: the room archive keeps a configurable rolling window (default 30
days, 0 = count-cap only), pruned daily by the push loop and immediately on
a setting change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest
from flask import Flask, g

import api.chat as chat_api
from core import chat_codec
from core.chat_codec import badge_of, decode, encode


# ── badge codec ──────────────────────────────────────────────────────────────


class TestBadgeCodec:
    def test_plain_badge_passes(self):
        assert badge_of({"bg": "vinyl nerd"}) == "vinyl nerd"

    def test_whitespace_collapses_and_trims(self):
        assert badge_of({"bg": "  spaced   out  "}) == "spaced out"

    def test_length_capped(self):
        assert badge_of({"bg": "x" * 40}) == "x" * 24

    @pytest.mark.parametrize(
        "word",
        [
            "admin",
            "Administrator",
            "MOD",
            "moderator",
            "dev",
            "Developer",
            "lead dev",
            "LEADDEV",
            "soulsync",
            "system",
            "owner",
            "staff",
            "support",
            "official",
        ],
    )
    def test_staff_words_refused(self, word):
        assert badge_of({"bg": word}) is None

    def test_staff_word_with_padding_still_refused(self):
        assert badge_of({"bg": "  admin  "}) is None

    @pytest.mark.parametrize(
        "sneaky",
        [
            "LEAD DEV!",  # trailing punctuation
            "d.e.v",  # punctuation-split
            "(admin)",  # wrapped
            "SoulSync Admin",  # reserved word inside a longer badge
            "official staff",  # two reserved words
            "the lead dev",  # reserved phrase as words
            "MODERATOR!",  # case + punctuation
        ],
    )
    def test_impersonation_variants_refused(self, sneaky):
        assert badge_of({"bg": sneaky}) is None

    @pytest.mark.parametrize(
        "innocent",
        [
            "device",  # 'dev' only as a substring
            "devon",  # 'dev' only as a prefix
            "vinyl collector",
            "moderately chill",  # 'mod' only as a substring
        ],
    )
    def test_innocent_badges_pass(self, innocent):
        assert badge_of({"bg": innocent}) == innocent

    @pytest.mark.parametrize(
        "bad",
        [
            "<img src=x>",
            "a&b",
            'q"uote',
            "it's",
            "<script>",
        ],
    )
    def test_markup_characters_refused(self, bad):
        assert badge_of({"bg": bad}) is None

    @pytest.mark.parametrize("bad", [None, "", "   ", 42, ["x"], {"bg": None}])
    def test_empty_and_wrong_shapes_refused(self, bad):
        payload = bad if isinstance(bad, dict) else {"bg": bad}
        assert badge_of(payload) is None

    def test_emoji_badge_allowed(self):
        assert badge_of({"bg": "🎧 night owl"}) == "🎧 night owl"

    def test_round_trips_through_the_envelope(self):
        packed = encode("hello room", {"bg": "crate digger"})
        assert badge_of(decode(packed)) == "crate digger"

    def test_hostile_envelope_badge_dropped_on_receive(self):
        # a hostile client crafting its own envelope can't wear staff words —
        # the receive path folds the same verdict as the send guard
        packed = encode("hi", {"bg": "admin"})
        assert badge_of(decode(packed)) is None


# ── badge archive ────────────────────────────────────────────────────────────


@pytest.fixture()
def mdb(tmp_path):
    from database.music_database import MusicDatabase

    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _m(n, user="alice", badge=None, ts=None):
    m = {"username": user, "message": "msg %d" % n, "rich": True, "timestamp": ts or ("2026-07-19 10:%02d:00" % n)}
    if badge:
        m["badge"] = badge
    return m


class TestBadgeArchive:
    def test_badge_stored_and_returned(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, badge="crate digger")])
        rows = mdb.get_chat_messages("SoulSync")
        assert rows[0]["badge"] == "crate digger"

    def test_missing_badge_has_no_key(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1)])
        rows = mdb.get_chat_messages("SoulSync")
        assert "badge" not in rows[0]

    def test_badge_capped_on_store(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, badge="y" * 60)])
        assert mdb.get_chat_messages("SoulSync")[0]["badge"] == "y" * 24


# ── retention prune ──────────────────────────────────────────────────────────


def _old_ts(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


class TestRetentionPrune:
    def test_prunes_only_older_than_window(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, ts=_old_ts(60)), _m(2, ts=_old_ts(10)), _m(3, ts=_old_ts(1))])
        assert mdb.prune_chat_messages(30) == 1
        assert [r["message"] for r in mdb.get_chat_messages("SoulSync")] == ["msg 2", "msg 3"]

    def test_boundary_is_exclusive(self, mdb):
        # exactly at the cutoff is KEPT (strictly-older-than)
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        mdb.add_chat_messages("SoulSync", [_m(1, ts=cutoff), _m(2, ts=_old_ts(31))])
        assert mdb.prune_chat_messages(30) == 1
        assert [r["message"] for r in mdb.get_chat_messages("SoulSync")] == ["msg 1"]

    def test_zero_disables(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, ts=_old_ts(400))])
        assert mdb.prune_chat_messages(0) == 0
        assert len(mdb.get_chat_messages("SoulSync")) == 1

    @pytest.mark.parametrize("bad", [None, "", "soon", -5, float("nan")])
    def test_invalid_disables(self, mdb, bad):
        mdb.add_chat_messages("SoulSync", [_m(1, ts=_old_ts(400))])
        assert mdb.prune_chat_messages(bad) == 0
        assert len(mdb.get_chat_messages("SoulSync")) == 1

    def test_prune_is_per_room(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, ts=_old_ts(60))])
        mdb.add_chat_messages("other", [_m(2, ts=_old_ts(60))])
        # prune targets every room's old rows — it's a global age bound
        assert mdb.prune_chat_messages(30) == 2

    def test_malformed_timestamps_dont_crash(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, ts="not a timestamp"), _m(2, ts=_old_ts(60))])
        deleted = mdb.prune_chat_messages(30)
        assert deleted == 1  # the 60-day row; the junk row sorts wherever it sorts

    def test_nothing_old_deletes_nothing(self, mdb):
        mdb.add_chat_messages("SoulSync", [_m(1, ts=_old_ts(1))])
        assert mdb.prune_chat_messages(30) == 0


# ── retention setting plumbing ───────────────────────────────────────────────


class TestRetentionDays:
    def test_default(self):
        assert chat_api._retention_days(lambda k, d=None: d) == 30

    def test_valid_passthrough(self):
        assert chat_api._retention_days(lambda k, d=None: 7) == 7

    def test_zero_kept(self):
        assert chat_api._retention_days(lambda k, d=None: 0) == 0

    def test_clamped_to_max(self):
        assert chat_api._retention_days(lambda k, d=None: 99999) == chat_api.RETENTION_DAYS_MAX

    def test_negative_clamped_to_zero(self):
        assert chat_api._retention_days(lambda k, d=None: -3) == 0

    def test_garbage_falls_back_to_default(self):
        assert chat_api._retention_days(lambda k, d=None: "soon") == 30

    def test_config_exception_falls_back_to_default(self):
        def _boom(k, d=None):
            raise RuntimeError("nope")

        assert chat_api._retention_days(_boom) == 30


# ── settings + send API ──────────────────────────────────────────────────────


class _FakeChatClient:
    """Sync stand-in — paired with run_async=identity in configure()."""

    base_url = "http://slskd"

    def __init__(self):
        self.joined = []
        self.sent_room = []

    def get_joined_rooms(self):
        return list(self.joined)

    def join_room(self, room):
        self.joined.append(room)
        return True

    def send_room_message(self, room, message):
        self.sent_room.append((room, message))
        return True


@pytest.fixture()
def chat_app():
    client = _FakeChatClient()
    state = {"client": client, "admin": True, "config": {}}
    chat_api.configure(
        client_getter=lambda: state["client"],
        run_async=lambda v, timeout=None: v,
        config_get=lambda key, default=None: state["config"].get(key, default),
        config_set=lambda key, value: state["config"].__setitem__(key, value),
    )
    app = Flask(__name__)

    @app.before_request
    def _fake_profile():
        g.is_admin = state["admin"]

    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client(), state
    chat_api.configure(client_getter=lambda: None, run_async=lambda v, timeout=None: v, config_get=lambda k, d=None: d)


class TestChatSettingsApi:
    def test_get_reports_badge_and_retention_defaults(self, chat_app):
        http, _ = chat_app
        body = http.get("/api/chat/settings").get_json()
        assert body["badge"] == ""
        assert body["history_retention_days"] == 30

    def test_get_echoes_configured_values(self, chat_app):
        http, state = chat_app
        state["config"]["soulseek.chat_badge"] = "crate digger"
        state["config"]["soulseek.chat_history_retention_days"] = 7
        body = http.get("/api/chat/settings").get_json()
        assert body["badge"] == "crate digger"
        assert body["history_retention_days"] == 7

    def test_set_badge_round_trips(self, chat_app):
        http, state = chat_app
        res = http.post("/api/chat/settings", json={"badge": "night owl"})
        assert res.status_code == 200
        assert state["config"]["soulseek.chat_badge"] == "night owl"
        assert res.get_json()["badge"] == "night owl"

    def test_set_badge_empty_clears(self, chat_app):
        http, state = chat_app
        state["config"]["soulseek.chat_badge"] = "old"
        assert http.post("/api/chat/settings", json={"badge": ""}).status_code == 200
        assert state["config"]["soulseek.chat_badge"] == ""

    def test_set_badge_staff_word_refused_loudly(self, chat_app):
        http, state = chat_app
        res = http.post("/api/chat/settings", json={"badge": "admin"})
        assert res.status_code == 400
        assert "badge" not in state["config"]

    def test_set_retention_round_trips(self, chat_app):
        http, state = chat_app
        res = http.post("/api/chat/settings", json={"history_retention_days": 7})
        assert res.status_code == 200
        assert state["config"]["soulseek.chat_history_retention_days"] == 7
        assert res.get_json()["history_retention_days"] == 7

    def test_set_retention_zero_and_clamp(self, chat_app):
        http, state = chat_app
        assert http.post("/api/chat/settings", json={"history_retention_days": 0}).status_code == 200
        assert state["config"]["soulseek.chat_history_retention_days"] == 0
        http.post("/api/chat/settings", json={"history_retention_days": 99999})
        assert state["config"]["soulseek.chat_history_retention_days"] == chat_api.RETENTION_DAYS_MAX

    def test_set_retention_garbage_falls_back(self, chat_app):
        http, state = chat_app
        assert http.post("/api/chat/settings", json={"history_retention_days": "soon"}).status_code == 200
        assert state["config"]["soulseek.chat_history_retention_days"] == 30

    def test_settings_require_admin(self, chat_app):
        http, state = chat_app
        state["admin"] = False
        assert http.get("/api/chat/settings").status_code == 403
        assert http.post("/api/chat/settings", json={"badge": "x"}).status_code == 403
        state["admin"] = True


class TestBadgeSend:
    def test_badge_rides_the_envelope(self, chat_app):
        http, state = chat_app
        res = http.post("/api/chat/room/message", json={"room": "SoulSync", "message": "hi", "badge": "crate digger"})
        assert res.status_code == 200
        room, packed = state["client"].sent_room[-1]
        assert badge_of(decode(packed)) == "crate digger"

    def test_no_badge_no_tag(self, chat_app):
        http, state = chat_app
        assert http.post("/api/chat/room/message", json={"room": "SoulSync", "message": "hi"}).status_code == 200
        _, packed = state["client"].sent_room[-1]
        assert "bg" not in decode(packed)

    def test_staff_badge_refused_loudly(self, chat_app):
        http, state = chat_app
        res = http.post("/api/chat/room/message", json={"room": "SoulSync", "message": "hi", "badge": "moderator"})
        assert res.status_code == 400
        assert not state["client"].sent_room  # nothing went out

    def test_unwrap_attaches_badge(self):
        packed = encode("hello", {"bg": "night owl"})
        live, _, _ = chat_api._unwrap_room_messages([{"username": "u", "message": packed, "timestamp": "2026-07-19 10:00:00"}])
        assert live[0]["badge"] == "night owl"

    def test_unwrap_drops_hostile_badge(self):
        packed = encode("hello", {"bg": "admin"})
        live, _, _ = chat_api._unwrap_room_messages([{"username": "u", "message": packed, "timestamp": "2026-07-19 10:00:00"}])
        assert "badge" not in live[0]
